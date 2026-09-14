"""Smoke tests — run with `python -m pytest` or `python tests/test_smoke.py`.

Covers both engines:
  * the deterministic no-API fallback path (default here, no keys),
  * the Gemini path, with agents.llm stubbed so no network is needed.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# isolated DB per run
_tmp = tempfile.mkdtemp()
os.environ["DB_PATH"] = str(Path(_tmp) / "test.db")
os.environ["OFFLINE_MODE"] = "true"

import config  # noqa: E402
config.DB_PATH = Path(os.environ["DB_PATH"])
config.OFFLINE_MODE = True
# Force the bundled-sample tier so tests don't depend on the downloaded Oak DB.
config.OAK_LOCAL_DB_PATH = Path(_tmp) / "no-oak-db.sqlite"
# Tests must not hit the network: null the real keys except where a test stubs them.
_REAL_GEMINI_KEY = config.GEMINI_API_KEY
config.GEMINI_API_KEY = ""
config.OAK_API_KEY = ""

from agents import assessor_agent, crew, llm  # noqa: E402
from agents.curriculum_ingestor import fetch_curriculum  # noqa: E402
from database import db_manager as db  # noqa: E402
from pdf_builder import render_html  # noqa: E402

NOTES = """Pupil: Ava Patel, Year 3 (age 8). Loves pizza and football.
Thinks 1/4 is bigger than 1/2 "because 4 is more than 2". Confident on 2s/5s/10s times tables.
Struggled to find 1/4 of 12. Got 6/15 on the fractions check."""


def _check(name: str, cond: bool) -> None:
    print(f"  {'PASS' if cond else 'FAIL'}  {name}")
    assert cond, name


def test_ingestor_offline():
    p = fetch_curriculum("fractions", 2, subject="maths", year_group=3)
    _check("ingestor: KS2 fractions unit", "fraction" in p.unit_title.lower())
    _check("ingestor: objectives present", len(p.learning_objectives) >= 3)
    _check("ingestor: misconceptions present", len(p.misconceptions) >= 1)
    _check("ingestor: source is bundled sample", p.source == "mock")


def test_ingestor_all_subjects():
    for subject, ks, topic in [("maths", 2, "fractions"), ("english", 2, "recount"),
                               ("science", 2, "states of matter"), ("geography", 2, "rivers"),
                               ("history", 2, "vikings")]:
        p = fetch_curriculum(topic, ks, subject=subject)
        _check(f"ingestor: {subject}/{topic} -> objectives + misconceptions",
               len(p.learning_objectives) >= 2 and len(p.misconceptions) >= 1)


def test_oak_local_dataset():
    from agents import oak_local
    real_db = ROOT / "data" / "oak-curriculum.sqlite"
    saved = config.OAK_LOCAL_DB_PATH
    config.OAK_LOCAL_DB_PATH = real_db
    try:
        if not oak_local.available():
            print("  SKIP  Oak dataset not downloaded (run: python main.py setup)")
            return
        _run_oak_local_checks(oak_local)
    finally:
        config.OAK_LOCAL_DB_PATH = saved


def _run_oak_local_checks(oak_local):
    from agents.textutil import to_i_can
    p = oak_local.build_packet("history", 2, "The Vikings")
    _check("oak-local: real Viking unit", "viking" in p.unit_title.lower())
    _check("oak-local: source tag", p.source == "oak-local")
    _check("oak-local: has NC refs", len(p.national_curriculum_refs) >= 1)
    _check("oak-local: vocab has definitions", any(d.get("definition") for d in p.key_vocabulary_defs))
    _check("oak-local: no double 'I can'",
           not any(to_i_can(o).lower().startswith("i can i can") for o in p.learning_objectives))


def test_assessor_heuristic():
    a = assessor_agent.analyse(NOTES)
    _check("assessor: name parsed", a["student"]["full_name"] == "Ava Patel")
    _check("assessor: year 3", a["student"]["year_group"] == 3)
    _check("assessor: subject maths", a["subject"] == "maths")
    _check("assessor: score ~40", abs((a["overall_score"] or 0) - 40.0) < 0.1)
    _check("assessor: weak points found", len(a["weak_points"]) >= 1)
    _check("assessor: interests", "pizza" in " ".join(a["student"]["interests"]).lower())
    _check("assessor: difficulty recommended",
           a["difficulty_recommendation"] in ("support", "core", "stretch"))


def test_crm_enrolment_invoice_report():
    db.init_db()
    sid = db.create_student(full_name="Billing Kid", year_group=4, guardian_name="A Parent",
                            guardian_email="p@example.com", status="enquiry")
    eid = db.create_enrolment(sid, hourly_rate=15, classes_per_month=5, class_length_min=60,
                              subjects=[{"subject": "maths", "classes": 3},
                                        {"subject": "science", "classes": 2}])
    en = db.active_enrolment(sid)
    _check("enrol: created + active", en is not None and en["id"] == eid)
    _check("enrol: monthly value 5x15 = 75", db.monthly_value(en) == 75.0)
    _check("enrol: subjects json roundtrip",
           en["subjects_json"][0]["subject"] == "maths" and en["subjects_json"][1]["classes"] == 2)
    _check("enrol: pupil auto-activated", db.get_student(sid)["status"] == "active")
    _check("mrr includes this pupil", db.monthly_recurring_revenue() >= 75.0)

    iid = db.create_invoice(sid, enrolment_id=eid, period="October 2026",
                            line_items=[{"description": "Tutoring", "qty": 5, "unit_price": 15,
                                         "amount": 75}])
    inv = db.get_invoice(iid)
    _check("invoice: total = 75", inv["total"] == 75.0)
    _check("invoice: number format", inv["number"].startswith("INV-"))
    db.set_invoice(iid, status="paid")
    _check("invoice: status update", db.get_invoice(iid)["status"] == "paid")

    from agents import report_writer
    md, summary = report_writer.draft_report(db.get_student(sid), db.list_assessments(sid),
                                             db.list_books(sid), period="Autumn 2026")
    _check("report: markdown draft", "##" in md and len(md) > 100)
    rid = db.create_report(sid, period="Autumn 2026", content_md=md, summary=summary)
    _check("report: stored", db.get_report(rid)["period"] == "Autumn 2026")

    stats = db.dashboard_stats()
    _check("dashboard: has stats", stats["active"] >= 1 and stats["mrr"] >= 75.0)


def test_auth_and_booking():
    db.init_db()
    _id, created = db.ensure_admin("admin", "admin")
    _check("auth: admin bootstrapped", created and db.get_user(_id)["role"] == "admin")
    _check("auth: second ensure_admin is a no-op", db.ensure_admin("admin", "x")[1] is False)

    sid = db.create_student(full_name="Portal Kid", year_group=4)
    creds = db.make_parent_login(sid)
    pu = db.get_user_by_name(creds["username"])
    _check("auth: parent login made", pu is not None and pu["student_id"] == sid)
    _check("auth: parent password checks out", db.check_password(pu, creds["password"]))
    _check("auth: must change pw on first login", pu["must_change_pw"] == 1)
    _check("auth: reset keeps same username",
           db.make_parent_login(sid)["username"] == creds["username"])

    db.set_availability([{"weekday": 0, "start_time": "16:00", "end_time": "19:00", "slot_min": 60},
                         {"weekday": 2, "start_time": "16:00", "end_time": "18:00", "slot_min": 60}])
    _check("calendar: availability stored", len(db.list_availability()) == 2)
    slots = db.available_slots(duration_min=60, lead_hours=1)
    _check("calendar: slots offered inside windows", len(slots) > 0
           and all(s["time"] >= "16:00" and s["time"] < "19:00" for s in slots))

    slot = slots[0]["iso"]
    sess_id = db.create_session(sid, starts_at=slot, duration_min=60, booked_by="parent")
    _check("calendar: booking counts this month", db.bookings_this_month(sid) >= 1)
    _check("calendar: slot no longer offered once booked",
           slot not in [s["iso"] for s in db.available_slots(duration_min=60, lead_hours=1)])

    db.mark_arrived(sess_id)
    _check("attendance: arrived -> in_session", db.current_status(sid)["state"] == "in_session")
    db.mark_departed(sess_id)
    st = db.current_status(sid)
    _check("attendance: departed -> finished/none",
           st["state"] in ("finished_today", "none", "upcoming"))

    iid = db.create_invoice(sid, enrolment_id=None, period="Now",
                            line_items=[{"description": "x", "qty": 1, "unit_price": 20, "amount": 20}],
                            status="sent")
    pay = db.payment_summary(sid)
    _check("payments: due reflects unpaid invoice", pay["due"] == 20.0 and pay["paid"] == 0.0)

    # ---- extra classes beyond the plan get priced + invoiced --------------- #
    ek = db.create_student(full_name="Extra Kid", year_group=5)
    db.create_enrolment(ek, hourly_rate=20, classes_per_month=2, class_length_min=60,
                        subjects=[{"subject": "maths", "classes": 2}])
    en = db.active_enrolment(ek)
    _check("extra: per-class price 20", db.per_class_price(en) == 20.0)
    db.set_availability([{"weekday": d, "start_time": "09:00", "end_time": "17:00", "slot_min": 60}
                         for d in range(7)])
    free = db.available_slots(duration_min=60, lead_hours=1)
    _check("extra: not extra while inside plan", db.would_be_extra(ek, en) is False)
    db.create_session(ek, starts_at=free[0]["iso"], booked_by="parent")
    db.create_session(ek, starts_at=free[1]["iso"], booked_by="parent")
    _check("extra: now beyond plan", db.would_be_extra(ek, en) is True)
    db.create_session(ek, starts_at=free[2]["iso"], booked_by="parent",
                      billable=True, charge_amount=db.per_class_price(en))
    _check("extra: one unbilled extra at £20",
           len(db.unbilled_extras(ek)) == 1 and db.extras_pending_total(ek) == 20.0)
    _check("extra: included count ignores the extra", db.bookings_this_month(ek, included_only=True) == 2)

    ex_id = db.unbilled_extras(ek)[0]["id"]
    eiid = db.create_invoice(ek, enrolment_id=en["id"], period="Now",
                             line_items=[{"description": "Extra class", "qty": 1,
                                          "unit_price": 20, "amount": 20}], status="sent")
    db.attach_sessions_to_invoice([ex_id], eiid)
    _check("extra: invoiced extra clears the pending list", db.extras_pending_total(ek) == 0.0)
    _check("extra: session now links to the invoice", db.get_session(ex_id)["invoice_id"] == eiid)


def test_study_materials_catalog():
    from agents import study_materials as sm
    _check("study: catalog non-empty", len(sm.CATALOG) > 10)
    ids = [i["id"] for i in sm.CATALOG]
    _check("study: unique ids", len(ids) == len(set(ids)))
    _check("study: every item has a gov.uk PDF url",
           all(i["url"].startswith("https://assets.publishing.service.gov.uk/") for i in sm.CATALOG))
    _check("study: covers core subjects",
           {"maths", "english", "science", "geography", "history"} <=
           {i["subject"] for i in sm.CATALOG if i["category"] == "curriculum"})
    g = sm.grouped_catalog()
    _check("study: grouped by category", {grp["category"] for grp in g} <= {"curriculum", "past_paper", "mark_scheme"})
    c = sm.counts()
    _check("study: counts add up", c["downloaded"] + c["missing"] == c["total"] == len(sm.CATALOG))
    _check("study: unknown id returns None", sm.get("does-not-exist") is None)
    # excerpt helper degrades gracefully with nothing downloaded / no match
    excerpt = sm.relevant_curriculum_excerpt("maths", "fractions")
    _check("study: excerpt is a string (possibly empty if not yet downloaded)", isinstance(excerpt, str))


def test_webapp_routes_smoke():
    import webapp
    webapp.app.config["TESTING"] = True
    db.init_db()
    db.ensure_admin("admin", "admin")
    c = webapp.app.test_client()

    r = c.get("/", follow_redirects=False)
    _check("web: anonymous redirected to login", r.status_code == 302 and "/login" in r.headers["Location"])
    r = c.get("/parent", follow_redirects=False)
    _check("web: /parent needs login", r.status_code == 302)

    r = c.post("/login", data={"username": "admin", "password": "admin"}, follow_redirects=False)
    _check("web: admin login ok", r.status_code == 302)
    _check("web: admin lands on /admin", c.get("/").headers["Location"].endswith("/admin"))
    _check("web: admin dashboard renders", c.get("/admin").status_code == 200)
    _check("web: calendar renders", c.get("/admin/calendar").status_code == 200)
    _check("web: availability renders", c.get("/admin/availability").status_code == 200)
    _check("web: study material page renders", c.get("/admin/study").status_code == 200)
    _check("web: business name shows on admin dashboard (no template var collision)",
           config.BUSINESS_NAME.encode() in c.get("/admin").data)
    _check("web: business name shows on invoices page (regression: status= var collision)",
           config.BUSINESS_NAME.encode() in c.get("/admin/invoices").data)

    sid = db.create_student(full_name="Web Parent Kid", year_group=3)
    r = c.post(f"/admin/pupil/{sid}/parent-login", follow_redirects=False)
    _check("web: parent-login route ok", r.status_code == 302)
    pu = db.user_for_student(sid)
    db.set_password(pu["id"], "familypass", must_change=False)

    c.get("/logout")
    c.post("/login", data={"username": pu["username"], "password": "familypass"})
    _check("web: parent lands on /parent", c.get("/").headers["Location"].endswith("/parent"))
    _check("web: parent dashboard renders", c.get("/parent").status_code == 200)
    _check("web: business name shows on parent dashboard (regression: status= var collision)",
           config.BUSINESS_NAME.encode() in c.get("/parent").data)
    _check("web: parent booking page renders", c.get("/parent/book").status_code == 200)
    _check("web: parent blocked from /admin", c.get("/admin", follow_redirects=False).status_code == 302)
    _check("web: parent blocked from another pupil's data (403/redirect)",
           c.get("/admin/pupils", follow_redirects=False).status_code == 302)


def test_difficulty_recommendation():
    db.init_db()
    sid = db.create_student(full_name="Test Kid", year_group=3)
    _check("reco: default core", db.recommend_difficulty(sid)[0] == "core")
    db.add_assessment(sid, kind="weekly", overall_score=92)
    db.add_assessment(sid, kind="weekly", overall_score=88)
    _check("reco: high scores -> stretch", db.recommend_difficulty(sid)[0] == "stretch")
    sid2 = db.create_student(full_name="Test Kid Two", year_group=3)
    db.add_assessment(sid2, kind="weekly", overall_score=40)
    _check("reco: low score -> support", db.recommend_difficulty(sid2)[0] == "support")


def test_full_pipeline_fallback():
    db.init_db()
    sid = db.create_student(full_name="Ava Patel", year_group=3, interests="pizza, football")
    a = assessor_agent.analyse(NOTES)
    db.add_assessment(sid, subject="maths", raw_notes=NOTES,
                      strengths=a["strengths"], weak_points=a["weak_points"], analysis=a)

    student = db.get_student(sid)
    result = crew.run_weekly_pipeline(topic="Fractions", key_stage=2, subject="maths",
                                      student=student, assessment=a, num_questions=8,
                                      difficulty="support", verbose=False)
    qs = result.problems["questions"]
    _check("pipeline: 8 questions", len(qs) == 8)
    _check("pipeline: has targeted questions", any(q["type"] == "targeted" for q in qs))
    _check("pipeline: 70/30 split", result.problems["mix"] == {"baseline": 6, "targeted": 2})
    _check("pipeline: questions carry a space size", all(q.get("space") in ("small", "medium", "large") for q in qs))
    _check("pipeline: difficulty threaded", result.difficulty == "support")
    _check("pipeline: notes are markdown", result.explanation_md.lstrip().startswith("#"))
    _check("pipeline: grounded in curriculum", result.curriculum["source"] == "mock")

    html = render_html(result.as_dict(), student)
    _check("pdf: html renders", "Ava Patel" in html and "Fractions" in html)
    _check("pdf: student-only (no tutor sections)",
           "Answers" in html and "marking note" not in html.lower() and "grown-up note" not in html.lower())
    _check("pdf: working boxes present", 'class="work' in html)

    bid = db.add_weekly_book(sid, subject="maths", topic="Fractions", key_stage=2, year_group=3,
                             difficulty="support", curriculum=result.curriculum, strategy=result.strategy,
                             explanation_md=result.explanation_md, problems=result.problems)
    b = db.get_book(bid)
    _check("db: weekly book stored + difficulty", b["difficulty"] == "support"
           and b["problems_json"]["mix"]["baseline"] == 6)


def test_gemini_path_stubbed(monkeypatch=None):
    """Stub agents.llm so the Gemini code paths run without network."""
    strategy_json = {
        "week_focus": "Compare unit fractions with pizza models.",
        "objectives_selected": ["Compare and order unit fractions"],
        "misconceptions_targeted": [
            {"misconception": "1/4 > 1/2", "teaching_move": "cut two identical pizzas",
             "representation": "pie charts"}
        ],
        "personalisation_notes": "pizza throughout",
        "explainer_brief": "Explain with pizza.",
        "problem_setter_brief": {"baseline_focus": ["name unit fractions"],
                                 "targeted_focus": ["compare 1/2 vs 1/4"]},
        "success_criteria": ["I can compare unit fractions"],
    }
    questions_json = {"questions": [
        {"n": i, "type": "baseline" if i <= 6 else "targeted", "prompt": f"Q{i}",
         "answer": "a", "hint": "h", "space": "medium"} for i in range(1, 9)
    ]}

    def fake_generate(prompt, *, system=None, temperature=0.4, as_json=False, retries=3, images=None):
        return "## The big idea\nPizza is nice.\n\n## Words to know\n- half\n"

    def fake_generate_json(prompt, *, system=None, temperature=0.3, images=None):
        return questions_json if "practice questions" in prompt.lower() else strategy_json

    import agents.curriculum_agent as ca
    import agents.explainer_agent as ea
    import agents.problem_setter as ps

    for mod in (ca, ea, ps):
        mod.llm.generate = fake_generate  # type: ignore
        mod.llm.generate_json = fake_generate_json  # type: ignore

    monkeypatch_setattr = getattr(config, "GEMINI_API_KEY", "")
    config.GEMINI_API_KEY = "test-key"
    try:
        packet = fetch_curriculum("fractions", 2, subject="maths", year_group=3)
        student = {"full_name": "Ava Patel", "year_group": 3, "key_stage": 2, "interests": "pizza"}
        strat = ca.map_strategy(packet, student, {"weak_points": [{"topic": "compare 1/2 vs 1/4"}]},
                                difficulty="stretch", verbose=False)
        _check("gemini: strategy parsed", strat["week_focus"].startswith("Compare"))
        md = ea.write_explanation(packet, strat, student, difficulty="stretch", verbose=False)
        _check("gemini: notes md", md.lstrip().startswith("#"))
        probs = ps.set_problems(packet, strat, student, total=8, difficulty="stretch", verbose=False)
        _check("gemini: 8 questions", len(probs["questions"]) == 8)
    finally:
        config.GEMINI_API_KEY = monkeypatch_setattr


if __name__ == "__main__":
    for fn in (test_ingestor_offline, test_ingestor_all_subjects, test_oak_local_dataset,
               test_assessor_heuristic, test_difficulty_recommendation,
               test_crm_enrolment_invoice_report, test_auth_and_booking, test_study_materials_catalog,
               test_webapp_routes_smoke,
               test_full_pipeline_fallback, test_gemini_path_stubbed):
        print(f"\n{fn.__name__}")
        fn()
    print("\nAll smoke tests passed.")
