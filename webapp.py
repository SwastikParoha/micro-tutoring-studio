"""Micro-Tutoring Studio - local web interface.

A small Flask app so a tutor never has to touch the command line.

    python main.py serve          # then open http://127.0.0.1:5000

Everything runs on your own machine: the SQLite CRM, the agent pipeline and the
PDF builder are exactly the same code the CLI uses.
"""
from __future__ import annotations

import socket
import threading
import time
import traceback
import webbrowser
from pathlib import Path

import functools
import os
import secrets

from flask import (Flask, abort, flash, g, redirect, render_template, request,
                   send_file, session, url_for)

from typing import Optional

import config
from database import db_manager as db

# Heavy imports (crewai, google-genai, weasyprint) are done lazily inside the
# routes so the web server binds its port in well under a second.
BUSINESS_NAME = config.BUSINESS_NAME

app = Flask(__name__, template_folder="templates/web", static_folder="templates/web/static")
# A stable secret so sessions survive restarts. Override with SECRET_KEY in .env for deploys.
_SECRET_FILE = config.ROOT / ".flask_secret"
if os.getenv("SECRET_KEY"):
    app.secret_key = os.environ["SECRET_KEY"]
elif _SECRET_FILE.exists():
    app.secret_key = _SECRET_FILE.read_text().strip()
else:
    app.secret_key = secrets.token_hex(32)
    try:
        _SECRET_FILE.write_text(app.secret_key)
    except Exception:  # noqa: BLE001
        pass
app.config["MAX_CONTENT_LENGTH"] = 25 * 1024 * 1024  # 25 MB photo cap
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"


# --------------------------------------------------------------------------- #
# Auth
# --------------------------------------------------------------------------- #
def _bootstrap_admin() -> Optional[dict]:
    """Ensure an admin login exists. Returns {username, password} only the first time."""
    db.init_db()
    uname = (os.getenv("ADMIN_USERNAME") or "admin").strip().lower()
    pw = os.getenv("ADMIN_PASSWORD") or "admin"
    _id, created = db.ensure_admin(uname, pw)
    return {"username": uname, "password": pw} if created else None


@app.before_request
def _load_user():
    g.user = db.get_user(session["uid"]) if session.get("uid") else None
    if g.user and g.user.get("must_change_pw") and request.endpoint not in (
            "account", "logout", "login", "static"):
        return redirect(url_for("account"))


def login_required(view):
    @functools.wraps(view)
    def wrapped(*a, **kw):
        if not g.user:
            return redirect(url_for("login", next=request.path))
        return view(*a, **kw)
    return wrapped


def admin_required(view):
    @functools.wraps(view)
    def wrapped(*a, **kw):
        if not g.user:
            return redirect(url_for("login", next=request.path))
        if g.user["role"] != "admin":
            return redirect(url_for("parent_home"))
        return view(*a, **kw)
    return wrapped


def _parent_pupil_id() -> int:
    """The pupil a logged-in parent is allowed to see; aborts otherwise."""
    if not g.user or g.user["role"] != "parent" or not g.user["student_id"]:
        abort(403)
    return g.user["student_id"]


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "GET":
        return render_template("login.html", next=request.args.get("next", ""))
    u = db.get_user_by_name(request.form.get("username", ""))
    if not u or not db.check_password(u, request.form.get("password", "")):
        flash("Wrong username or password.", "error")
        return render_template("login.html", next=request.form.get("next", ""))
    session["uid"] = u["id"]
    db.touch_login(u["id"])
    nxt = request.form.get("next") or ""
    if nxt.startswith("/"):
        return redirect(nxt)
    return redirect(url_for("home"))


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.route("/account", methods=["GET", "POST"])
@login_required
def account():
    if request.method == "POST":
        new = request.form.get("password", "")
        if len(new) < 6:
            flash("Password must be at least 6 characters.", "error")
        else:
            db.set_password(g.user["id"], new)
            flash("Password updated.", "ok")
            return redirect(url_for("home"))
    return render_template("account.html")

SUBJECT_CHOICES = ["maths", "english", "science", "geography", "history",
                   "art", "computing", "music", "pe", "design and technology", "languages"]


# --------------------------------------------------------------------------- #
_STATUS_CACHE: dict = {}


def _status(force: bool = False) -> dict:
    """Cached app status. The Gemini health check is a live API call, so we only
    run it once at startup (and on demand), never on every page render."""
    if not force and _STATUS_CACHE and time.time() - _STATUS_CACHE["_t"] < 300:
        return _STATUS_CACHE
    if config.GEMINI_API_KEY:
        from agents import llm
        gem_ok, gem_msg = llm.health_check()
    else:
        gem_ok, gem_msg = False, "no GEMINI_API_KEY (built-in generator in use)"
    _STATUS_CACHE.update({
        "_t": time.time(),
        "business": BUSINESS_NAME,
        "curriculum": config.curriculum_source(),
        "gemini_ok": gem_ok,
        "gemini_msg": gem_msg,
        "oak_local": config.oak_local_available(),
    })
    return _STATUS_CACHE


@app.context_processor
def _inject():
    return {"status": _status(), "subjects": SUBJECT_CHOICES}


_IMG_MIME = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png",
             ".webp": "image/webp", ".heic": "image/heic", ".gif": "image/gif"}


def _save_upload(file_storage) -> Optional[str]:
    """Save an uploaded worksheet photo; return its stored path (or None)."""
    if not file_storage or not file_storage.filename:
        return None
    import time
    ext = Path(file_storage.filename).suffix.lower() or ".jpg"
    if ext not in _IMG_MIME:
        return None
    config.UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    dest = config.UPLOAD_DIR / f"work_{int(time.time())}{ext}"
    file_storage.save(dest)
    return str(dest)


def _image_tuple(path: Optional[str]):
    if not path or not Path(path).exists():
        return None
    return Path(path).read_bytes(), _IMG_MIME.get(Path(path).suffix.lower(), "image/jpeg")


STATUS_CHOICES = ["enquiry", "trial", "active", "paused", "ended"]
PAYMENT_METHODS = ["bank transfer", "card", "cash", "standing order"]
PAYMENT_SCHEDULES = ["monthly", "per class", "termly", "upfront"]


WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]


@app.context_processor
def _inject_choices():
    return {"STATUS_CHOICES": STATUS_CHOICES, "PAYMENT_METHODS": PAYMENT_METHODS,
            "PAYMENT_SCHEDULES": PAYMENT_SCHEDULES, "WEEKDAYS": WEEKDAYS,
            "user": getattr(g, "user", None)}


# --------------------------------------------------------------------------- #
@app.route("/")
def home():
    if not g.user:
        return redirect(url_for("login"))
    return redirect(url_for("admin_home") if g.user["role"] == "admin" else url_for("parent_home"))


@app.route("/admin")
@admin_required
def admin_home():
    db.init_db()
    stats = db.dashboard_stats()
    pupils = db.list_students()
    for p in pupils:
        p["enrolment"] = db.active_enrolment(p["id"])
    active = [p for p in pupils if p.get("status") == "active"]
    enquiries = [p for p in pupils if p.get("status") in ("enquiry", "trial")]
    recent_books = []
    for p in pupils:
        for b in db.list_books(p["id"])[:2]:
            recent_books.append({**b, "pupil": p["full_name"], "pupil_id": p["id"]})
    recent_books.sort(key=lambda b: b["id"], reverse=True)
    unpaid = [i for i in db.all_invoices(limit=200) if i["status"] in ("draft", "sent")]
    import datetime as _dt
    today = _dt.date.today()
    week_end = today + _dt.timedelta(days=8)
    upcoming = db.sessions_between(today.strftime("%Y-%m-%d 00:00"),
                                  week_end.strftime("%Y-%m-%d 00:00"))
    return render_template("home.html", stats=stats, active=active, enquiries=enquiries,
                           recent_books=recent_books[:6], unpaid=unpaid[:6],
                           upcoming=upcoming, monthly_value=db.monthly_value)


@app.route("/admin/pupils")
@admin_required
def pupils_list():
    q = (request.args.get("q") or "").strip()
    rows = db.search_students(q) if q else db.list_students()
    for p in rows:
        p["enrolment"] = db.active_enrolment(p["id"])
        p["books"] = len(db.list_books(p["id"]))
    return render_template("pupils.html", pupils=rows, q=q, monthly_value=db.monthly_value)


# ---- registration ---------------------------------------------------- #
@app.route("/admin/pupil/new", methods=["GET", "POST"])
@admin_required
def pupil_new():
    if request.method == "GET":
        return render_template("pupil_form.html", pupil={}, mode="new")
    f = request.form
    if not (f.get("full_name") or "").strip():
        flash("The pupil's name is required.", "error")
        return render_template("pupil_form.html", pupil=f, mode="new")
    year = f.get("year_group", type=int)
    sid = db.create_student(
        full_name=f["full_name"].strip(), year_group=year,
        key_stage=(1 if year and year <= 2 else 2 if year else None),
        age=f.get("age", type=int), date_of_birth=f.get("date_of_birth") or None,
        school=f.get("school") or None, guardian_name=f.get("guardian_name") or None,
        guardian_contact=f.get("guardian_contact") or None,
        guardian_email=f.get("guardian_email") or None,
        guardian_relationship=f.get("guardian_relationship") or None,
        address=f.get("address") or None, interests=f.get("interests") or None,
        goals=f.get("goals") or None, status=f.get("status") or "enquiry",
        notes=f.get("notes") or None,
    )
    flash("Pupil registered.", "ok")
    return redirect(url_for("pupil", pupil_id=sid))


@app.route("/admin/pupil/<int:pupil_id>/edit", methods=["GET", "POST"])
@admin_required
def pupil_edit(pupil_id: int):
    p = db.get_student(pupil_id)
    if not p:
        abort(404)
    if request.method == "GET":
        return render_template("pupil_form.html", pupil=p, mode="edit")
    f = request.form
    year = f.get("year_group", type=int)
    db.update_student(pupil_id, **{
        "full_name": f.get("full_name", p["full_name"]).strip(),
        "year_group": year, "key_stage": (1 if year and year <= 2 else 2 if year else None),
        "age": f.get("age", type=int), "date_of_birth": f.get("date_of_birth") or None,
        "school": f.get("school") or None, "guardian_name": f.get("guardian_name") or None,
        "guardian_contact": f.get("guardian_contact") or None,
        "guardian_email": f.get("guardian_email") or None,
        "guardian_relationship": f.get("guardian_relationship") or None,
        "address": f.get("address") or None, "interests": f.get("interests") or None,
        "goals": f.get("goals") or None, "status": f.get("status") or p.get("status"),
        "notes": f.get("notes") or None,
    })
    flash("Details updated.", "ok")
    return redirect(url_for("pupil", pupil_id=pupil_id))


# ---- onboarding --------------------------------------------------------- #
@app.route("/admin/onboard", methods=["GET", "POST"])
@admin_required
def onboard():
    if request.method == "GET":
        return render_template("onboard.html")

    notes = (request.form.get("notes") or "").strip()
    image_path = _save_upload(request.files.get("worksheet"))
    if not notes and not image_path:
        flash("Add the demo-lesson notes, or a photo of the pupil's work (or both).", "error")
        return render_template("onboard.html", form=request.form)

    try:
        from agents import crew
        analysis = crew.run_assessment(notes or None, image=_image_tuple(image_path), verbose=False)
    except Exception as exc:  # noqa: BLE001
        app.logger.error(traceback.format_exc())
        flash(f"Could not read that: {exc}", "error")
        return render_template("onboard.html", form=request.form)

    st = analysis.get("student", {})
    name = (request.form.get("name") or st.get("full_name") or "").strip()
    if not name:
        flash("Please type the pupil's name - I couldn't find it.", "error")
        return render_template("onboard.html", form=request.form, analysis=analysis)

    year = request.form.get("year", type=int) or st.get("year_group")
    interests = (request.form.get("interests") or ", ".join(st.get("interests", []) or [])).strip()
    key_stage = st.get("key_stage") or (1 if year and year <= 2 else 2 if year else None)

    fields = {k: v for k, v in dict(
        full_name=name, year_group=year, key_stage=key_stage, age=st.get("age"),
        interests=interests or None, guardian_name=(request.form.get("guardian")
                                                    or st.get("guardian_name")),
        guardian_contact=request.form.get("contact"),
    ).items() if v is not None}

    existing = db.find_student_by_name(name)
    if existing:
        sid = existing["id"]
        db.update_student(sid, **{k: v for k, v in fields.items() if k != "full_name"})
    else:
        sid = db.create_student(**fields)

    db.add_assessment(sid, subject=analysis.get("subject", "maths"), raw_notes=notes,
                      kind="diagnostic", image_path=image_path,
                      overall_score=analysis.get("overall_score"),
                      difficulty_reco=analysis.get("difficulty_recommendation"),
                      strengths=analysis.get("strengths", []),
                      weak_points=analysis.get("weak_points", []), analysis=analysis)
    flash(f"Saved {name}.", "ok")
    return redirect(url_for("pupil", pupil_id=sid))


# ---- pupil hub ------------------------------------------------------- #
@app.route("/admin/pupil/<int:pupil_id>")
@admin_required
def pupil(pupil_id: int):
    p = db.get_student(pupil_id)
    if not p:
        abort(404)
    level, reason = db.recommend_difficulty(pupil_id)
    last = db.latest_book(pupil_id)
    assessments = db.list_assessments(pupil_id)
    scores = [(a["assessed_on"], a["overall_score"]) for a in reversed(assessments)
              if a.get("overall_score") is not None]
    enrol = db.active_enrolment(pupil_id)
    return render_template("pupil.html", pupil=p, assessments=assessments,
                           books=db.list_books(pupil_id), reco_level=level, reco_reason=reason,
                           last_book=last, scores=scores, enrol=enrol,
                           monthly=db.monthly_value(enrol) if enrol else 0,
                           invoices=db.list_invoices(pupil_id),
                           reports=db.list_reports(pupil_id),
                           upcoming_sessions=db.list_sessions(pupil_id, upcoming=True),
                           past_sessions=db.list_sessions(pupil_id, upcoming=False)[:6],
                           parent_user=db.user_for_student(pupil_id),
                           extras_pending=db.extras_pending_total(pupil_id))


# ---- add to programme (enrolment) --------------------------------- #
@app.route("/admin/pupil/<int:pupil_id>/enrol", methods=["GET", "POST"])
@admin_required
def enrol(pupil_id: int):
    p = db.get_student(pupil_id)
    if not p:
        abort(404)
    if request.method == "GET":
        return render_template("enrol.html", pupil=p, enrol=db.active_enrolment(pupil_id))

    f = request.form
    subjects = []
    for name, cls in zip(f.getlist("subject_name"), f.getlist("subject_classes")):
        name = (name or "").strip()
        if name:
            subjects.append({"subject": name, "classes": int(cls or 0)})
    rate = f.get("hourly_rate", type=float) or 15.0
    cpm = f.get("classes_per_month", type=int) or 4
    length = f.get("class_length_min", type=int) or 60

    eid = db.create_enrolment(
        pupil_id, hourly_rate=rate, classes_per_month=cpm, class_length_min=length,
        subjects=subjects, payment_method=f.get("payment_method") or "bank transfer",
        payment_schedule=f.get("payment_schedule") or "monthly",
        start_date=f.get("start_date") or None, notes=f.get("notes") or "",
    )
    flash("Pupil added to the programme.", "ok")
    if f.get("make_quote"):
        return redirect(url_for("invoice_new", pupil_id=pupil_id))
    return redirect(url_for("pupil", pupil_id=pupil_id))


# ---- invoices / quotes ------------------------------------------- #
@app.route("/admin/pupil/<int:pupil_id>/invoice/new", methods=["GET", "POST"])
@admin_required
def invoice_new(pupil_id: int):
    p = db.get_student(pupil_id)
    if not p:
        abort(404)
    en = db.active_enrolment(pupil_id)
    import datetime as _dt
    default_period = _dt.date.today().strftime("%B %Y")

    if request.method == "GET":
        # pre-fill line items from the enrolment
        items = []
        if en:
            hrs = round(en["class_length_min"] / 60, 2)
            items.append({
                "description": f"Tutoring - {en['classes_per_month']} classes "
                               f"({en['class_length_min']} min each)",
                "qty": en["classes_per_month"],
                "unit_price": round(en["hourly_rate"] * hrs, 2),
                "amount": round(en["hourly_rate"] * hrs * en["classes_per_month"], 2),
            })
            for s in (en.get("subjects_json") or []):
                if s.get("classes"):
                    items.append({"description": f"  incl. {s['subject'].title()} "
                                  f"({s['classes']} of the {en['classes_per_month']} classes)",
                                  "qty": "", "unit_price": "", "amount": 0})
        extras = db.unbilled_extras(pupil_id)
        for s in extras:
            label = f"Extra class - {s['starts_at'][:16]}"
            if s.get("subject"):
                label += f" ({s['subject']})"
            items.append({"description": label, "qty": 1,
                          "unit_price": s["charge_amount"], "amount": s["charge_amount"]})
        return render_template("invoice_form.html", pupil=p, enrol=en, items=items,
                               period=default_period,
                               extra_ids=[s["id"] for s in extras])

    f = request.form
    items = []
    for desc, qty, price in zip(f.getlist("desc"), f.getlist("qty"), f.getlist("price")):
        desc = (desc or "").strip()
        if not desc:
            continue
        q = float(qty) if qty else 0
        pr = float(price) if price else 0
        items.append({"description": desc, "qty": qty or "", "unit_price": pr,
                      "amount": round(q * pr, 2) if (qty and price) else 0})
    if not items:
        flash("Add at least one line.", "error")
        return redirect(url_for("invoice_new", pupil_id=pupil_id))

    iid = db.create_invoice(pupil_id, enrolment_id=en["id"] if en else None,
                            period=f.get("period") or default_period,
                            due_on=f.get("due_on") or None, line_items=items,
                            notes=f.get("notes") or "",
                            status="sent" if f.get("send") else "draft")
    db.attach_sessions_to_invoice(f.getlist("extra_id"), iid)
    _build_invoice_pdf(iid)
    flash("Quote created.", "ok")
    return redirect(url_for("invoice_view", invoice_id=iid))


def _build_invoice_pdf(invoice_id: int) -> Optional[str]:
    inv = db.get_invoice(invoice_id)
    if not inv:
        return None
    student = db.get_student(inv["student_id"])
    en = db.active_enrolment(inv["student_id"])
    try:
        from pdf_builder import build_invoice_pdf
        out = build_invoice_pdf(inv, student, en)
        db.set_invoice(invoice_id, pdf_path=str(out))
        return str(out)
    except Exception:  # noqa: BLE001
        app.logger.error(traceback.format_exc())
        return None


@app.route("/admin/invoice/<int:invoice_id>")
@admin_required
def invoice_view(invoice_id: int):
    inv = db.get_invoice(invoice_id)
    if not inv:
        abort(404)
    return render_template("invoice.html", inv=inv, pupil=db.get_student(inv["student_id"]))


@app.route("/admin/invoice/<int:invoice_id>/status", methods=["POST"])
@admin_required
def invoice_status(invoice_id: int):
    new = request.form.get("status")
    if new in ("draft", "sent", "paid"):
        db.set_invoice(invoice_id, status=new)
        _build_invoice_pdf(invoice_id)
        flash(f"Marked {new}.", "ok")
    inv = db.get_invoice(invoice_id)
    return redirect(url_for("invoice_view", invoice_id=invoice_id))


@app.route("/invoice/<int:invoice_id>/pdf")
@login_required
def invoice_pdf(invoice_id: int):
    inv = db.get_invoice(invoice_id)
    if not inv:
        abort(404)
    if g.user["role"] == "parent" and inv["student_id"] != g.user["student_id"]:
        abort(403)
    path = inv.get("pdf_path") if inv else None
    if not path or not Path(path).exists():
        path = _build_invoice_pdf(invoice_id)
    if not path or not Path(path).exists():
        abort(404)
    return send_file(path, as_attachment=True, download_name=Path(path).name)


@app.route("/admin/invoices")
@admin_required
def invoices_all():
    status_filter = request.args.get("status") or None
    return render_template("invoices.html", invoices=db.all_invoices(status_filter),
                           status_filter=status_filter)


# ---- progress reports ------------------------------------------- #
@app.route("/admin/pupil/<int:pupil_id>/report/new", methods=["GET", "POST"])
@admin_required
def report_new(pupil_id: int):
    p = db.get_student(pupil_id)
    if not p:
        abort(404)
    import datetime as _dt
    default_period = f"{_dt.date.today().strftime('%B %Y')}"

    if request.method == "GET":
        return render_template("report_form.html", pupil=p, period=default_period)

    period = request.form.get("period") or default_period
    assessments = db.list_assessments(pupil_id)
    books = db.list_books(pupil_id)
    level = db.recommend_difficulty(pupil_id)[0]
    try:
        from agents import report_writer
        md, summary = report_writer.draft_report(p, assessments, books, period=period,
                                                 level=level, verbose=False)
    except Exception as exc:  # noqa: BLE001
        app.logger.error(traceback.format_exc())
        flash(f"Could not draft the report: {exc}", "error")
        return redirect(url_for("pupil", pupil_id=pupil_id))
    rid = db.create_report(pupil_id, period=period, content_md=md, summary=summary)
    flash("Report drafted - review and edit it, then download.", "ok")
    return redirect(url_for("report_view", report_id=rid))


@app.route("/admin/report/<int:report_id>", methods=["GET", "POST"])
@admin_required
def report_view(report_id: int):
    rep = db.get_report(report_id)
    if not rep:
        abort(404)
    p = db.get_student(rep["student_id"])
    if request.method == "POST":
        db.set_report(report_id, content_md=request.form.get("content_md", rep["content_md"]),
                      period=request.form.get("period", rep["period"]),
                      status=request.form.get("status", rep["status"]))
        _build_report_pdf(report_id)
        flash("Report saved.", "ok")
        return redirect(url_for("report_view", report_id=report_id))
    return render_template("report.html", rep=rep, pupil=p)


def _build_report_pdf(report_id: int) -> Optional[str]:
    rep = db.get_report(report_id)
    if not rep:
        return None
    p = db.get_student(rep["student_id"])
    scores = [(a["assessed_on"], a["overall_score"])
              for a in reversed(db.list_assessments(rep["student_id"]))
              if a.get("overall_score") is not None]
    try:
        from pdf_builder import build_report_pdf
        out = build_report_pdf(rep, p, scores, tutor_name=config.TUTOR_NAME)
        db.set_report(report_id, pdf_path=str(out))
        return str(out)
    except Exception:  # noqa: BLE001
        app.logger.error(traceback.format_exc())
        return None


@app.route("/report/<int:report_id>/pdf")
@login_required
def report_pdf(report_id: int):
    rep = db.get_report(report_id)
    if not rep:
        abort(404)
    if g.user["role"] == "parent" and (rep["student_id"] != g.user["student_id"]
                                       or rep["status"] != "sent"):
        abort(403)
    path = rep.get("pdf_path") if rep else None
    if not path or not Path(path).exists():
        path = _build_report_pdf(report_id)
    if not path or not Path(path).exists():
        abort(404)
    return send_file(path, as_attachment=True, download_name=Path(path).name)


@app.route("/admin/pupil/<int:pupil_id>/log", methods=["POST"])
@admin_required
def log_result(pupil_id: int):
    p = db.get_student(pupil_id)
    if not p:
        abort(404)
    score = request.form.get("score", type=float)
    notes = (request.form.get("notes") or "").strip()
    subject = (request.form.get("subject") or "").strip() or None
    image_path = _save_upload(request.files.get("worksheet"))
    if score is None and not notes and not image_path:
        flash("Add a score, a photo of their work, or a note.", "error")
        return redirect(url_for("pupil", pupil_id=pupil_id))

    analysis: dict = {}
    if notes or image_path:
        try:
            from agents import crew
            analysis = crew.run_assessment(notes or None, image=_image_tuple(image_path),
                                           kind="weekly", subject=subject, verbose=False)
        except Exception as exc:  # noqa: BLE001
            app.logger.error(traceback.format_exc())
            flash(f"Could not read that: {exc}", "error")
            return redirect(url_for("pupil", pupil_id=pupil_id))

    final_score = score if score is not None else analysis.get("overall_score")
    reco = analysis.get("difficulty_recommendation")
    if not reco and final_score is not None:
        reco = "stretch" if final_score >= 85 else "support" if final_score < 55 else "core"
    db.add_assessment(pupil_id, subject=subject or analysis.get("subject", "maths"),
                      kind="weekly", raw_notes=notes, image_path=image_path,
                      overall_score=final_score, difficulty_reco=reco,
                      strengths=analysis.get("strengths", []),
                      weak_points=analysis.get("weak_points", []), analysis=analysis)
    level, reason = db.recommend_difficulty(pupil_id)
    flash(f"Result logged. Suggested level for next week: {level} ({reason}).", "ok")
    return redirect(url_for("pupil", pupil_id=pupil_id))


# ---- generate a workbook --------------------------------------------- #
@app.route("/admin/generate", methods=["GET", "POST"])
@admin_required
def generate():
    pupils = db.list_students()
    if request.method == "GET":
        pre = request.args.get("pupil", type=int)
        pre_level, pre_topic, pre_subject = "core", "", "maths"
        if pre:
            pre_level = db.recommend_difficulty(pre)[0]
            lb = db.latest_book(pre)
            if lb:
                pre_subject = lb["subject"]
        return render_template("generate.html", pupils=pupils, pre_pupil=pre,
                               pre_level=pre_level, pre_topic=pre_topic, pre_subject=pre_subject)

    topic = (request.form.get("topic") or "").strip()
    subject = (request.form.get("subject") or "maths").strip()
    key_stage = request.form.get("keystage", type=int) or 2
    questions = min(10, max(4, request.form.get("questions", type=int) or 6))
    pupil_id = request.form.get("pupil", type=int)
    difficulty = (request.form.get("difficulty") or "").strip()
    space = (request.form.get("space") or "few").strip()

    if not topic:
        flash("Please enter a topic (or pick one with 'Browse topics').", "error")
        return render_template("generate.html", pupils=pupils, form=request.form)

    student, assessment = {}, None
    if pupil_id:
        student = db.get_student(pupil_id) or {}
        a = db.latest_assessment(pupil_id, subject=subject) or db.latest_assessment(pupil_id)
        assessment = a.get("analysis_json") if a else None
        if not difficulty:
            difficulty = db.recommend_difficulty(pupil_id)[0]
    student.setdefault("key_stage", key_stage)
    difficulty = difficulty if difficulty in ("support", "core", "stretch") else "core"

    try:
        from agents import crew
        from pdf_builder import build_pdf
        result = crew.run_weekly_pipeline(
            topic=topic, key_stage=key_stage, subject=subject, student=student,
            assessment=assessment, num_questions=questions, difficulty=difficulty,
            space=space if space in ("few", "some", "lots") else "few", verbose=False,
        )
        out = build_pdf(result.as_dict(), student)
    except Exception as exc:  # noqa: BLE001
        app.logger.error(traceback.format_exc())
        flash(f"Something went wrong building the workbook: {exc}", "error")
        return render_template("generate.html", pupils=pupils, form=request.form)

    book_id = None
    if student.get("id"):
        book_id = db.add_weekly_book(
            student["id"], subject=result.subject, topic=result.topic,
            key_stage=result.key_stage, year_group=result.year_group, difficulty=result.difficulty,
            curriculum=result.curriculum, strategy=result.strategy,
            explanation_md=result.explanation_md, problems=result.problems, pdf_path=str(out),
        )
    flash("Workbook ready.", "ok")
    if book_id:
        return redirect(url_for("workbook", book_id=book_id))
    return redirect(url_for("workbook_file", filename=Path(out).name))


@app.route("/admin/study")
@admin_required
def study_material():
    from agents import study_materials as sm
    return render_template("study.html", groups=sm.grouped_catalog(), counts=sm.counts(),
                           license=sm.LICENSE, source_note=sm.SOURCE_NOTE)


@app.route("/admin/study/fetch", methods=["POST"])
@admin_required
def study_material_fetch():
    from agents import study_materials as sm
    r = sm.fetch_all(verbose=False)
    if r["failed"]:
        flash(f"Downloaded {r['downloaded']}, but {r['failed']} file(s) failed - "
              f"check your internet connection and try again.", "error")
    elif r["downloaded"]:
        flash(f"Downloaded {r['downloaded']} new document(s).", "ok")
    else:
        flash("Everything is already downloaded.", "ok")
    return redirect(url_for("study_material"))


@app.route("/admin/study/<material_id>")
@admin_required
def study_material_file(material_id: str):
    from agents import study_materials as sm
    item = sm.get(material_id)
    if not item or not item["downloaded"]:
        abort(404)
    path = sm.local_path(item)
    return send_file(path, as_attachment=request.args.get("dl") == "1",
                     download_name=path.name)


@app.route("/admin/topics")
@admin_required
def topics():
    """HTMX-free topic browser: returns an HTML fragment."""
    subject = request.args.get("subject", "maths")
    key_stage = request.args.get("keystage", type=int) or 2
    q = request.args.get("q") or None
    units = []
    if config.oak_local_available():
        from agents import oak_local
        units = oak_local.list_units(subject, key_stage, search=q, limit=40)
    return render_template("_topics.html", units=units, subject=subject,
                           key_stage=key_stage, has_data=config.oak_local_available())


# ---- workbooks ------------------------------------------------------- #
@app.route("/admin/workbook/<int:book_id>")
@admin_required
def workbook(book_id: int):
    b = db.get_book(book_id)
    if not b:
        abort(404)
    p = db.get_student(b["student_id"])
    from agents import study_materials as sm
    curr = b.get("curriculum_json") or {}
    return render_template("workbook.html", book=b, pupil=p, curr=curr,
                           nc_doc=sm.curriculum_doc_for(b["subject"]))


@app.route("/download/<int:book_id>")
@admin_required
def download(book_id: int):
    return _send_book(book_id, attach=True)


@app.route("/view/<int:book_id>")
@admin_required
def view(book_id: int):
    return _send_book(book_id, attach=False)


def _send_book(book_id: int, *, attach: bool):
    b = db.get_book(book_id)
    if not b or not b.get("pdf_path") or not Path(b["pdf_path"]).exists():
        abort(404)
    return send_file(b["pdf_path"], as_attachment=attach,
                     download_name=Path(b["pdf_path"]).name)


@app.route("/file/<path:filename>")
@admin_required
def workbook_file(filename: str):
    fp = config.OUTPUT_DIR / filename
    if not fp.exists():
        abort(404)
    return send_file(fp, as_attachment=filename.endswith(".pdf"))


# --------------------------------------------------------------------------- #
# Calendar / availability / attendance  (admin)
# --------------------------------------------------------------------------- #
@app.route("/admin/availability", methods=["GET", "POST"])
@admin_required
def availability():
    if request.method == "POST":
        rules = []
        for wd, start, end, slot in zip(request.form.getlist("weekday"),
                                        request.form.getlist("start_time"),
                                        request.form.getlist("end_time"),
                                        request.form.getlist("slot_min")):
            if start and end:
                rules.append({"weekday": int(wd), "start_time": start, "end_time": end,
                              "slot_min": int(slot or 60)})
        db.set_availability(rules)
        flash("Availability saved. Families can now book inside these times.", "ok")
        return redirect(url_for("availability"))
    return render_template("availability.html", rules=db.list_availability())


@app.route("/admin/calendar")
@admin_required
def calendar():
    import datetime as _dt
    start = request.args.get("start")
    try:
        anchor = _dt.date.fromisoformat(start) if start else _dt.date.today()
    except ValueError:
        anchor = _dt.date.today()
    monday = anchor - _dt.timedelta(days=anchor.weekday())
    days = [monday + _dt.timedelta(days=i) for i in range(7)]
    rows = db.sessions_between(monday.strftime("%Y-%m-%d 00:00"),
                              (monday + _dt.timedelta(days=7)).strftime("%Y-%m-%d 00:00"))
    by_day: dict = {d.isoformat(): [] for d in days}
    for s in rows:
        by_day.setdefault(s["starts_at"][:10], []).append(s)
    return render_template("calendar.html", days=days, by_day=by_day, today=_dt.date.today(),
                           prev=(monday - _dt.timedelta(days=7)).isoformat(),
                           nextw=(monday + _dt.timedelta(days=7)).isoformat())


@app.route("/admin/pupil/<int:pupil_id>/book", methods=["POST"])
@admin_required
def admin_book(pupil_id: int):
    if not db.get_student(pupil_id):
        abort(404)
    slot = (request.form.get("starts_at") or "").replace("T", " ")[:16]
    if len(slot) < 16:
        flash("Pick a date and time for the class.", "error")
        return redirect(url_for("pupil", pupil_id=pupil_id))
    en = db.active_enrolment(pupil_id)
    billable = bool(request.form.get("billable"))
    db.create_session(pupil_id, starts_at=slot,
                      duration_min=request.form.get("duration_min", type=int)
                      or (en["class_length_min"] if en else 60),
                      subject=(request.form.get("subject") or "").strip() or None,
                      booked_by="admin", billable=billable,
                      charge_amount=db.per_class_price(en) if billable else 0.0)
    flash("Extra class added — it will appear on the next invoice." if billable
          else "Class added to the calendar.", "ok")
    return redirect(request.form.get("next") or url_for("pupil", pupil_id=pupil_id))


@app.route("/admin/session/<int:session_id>/<action>", methods=["POST"])
@admin_required
def session_action(session_id: int, action: str):
    s = db.get_session(session_id)
    if not s:
        abort(404)
    msgs = {
        "arrived": "Marked arrived — the family can see this on their page now.",
        "departed": "Marked departed — the family can see this now.",
        "cancel": "Class cancelled.",
        "missed": "Marked as missed.",
    }
    if action == "arrived":
        db.mark_arrived(session_id)
    elif action == "departed":
        db.mark_departed(session_id)
    elif action == "cancel":
        db.update_session(session_id, status="cancelled")
    elif action == "missed":
        db.update_session(session_id, status="missed")
    else:
        abort(404)
    flash(msgs[action], "ok")
    return redirect(request.form.get("next") or url_for("pupil", pupil_id=s["student_id"]))


@app.route("/admin/pupil/<int:pupil_id>/parent-login", methods=["POST"])
@admin_required
def parent_login_make(pupil_id: int):
    p = db.get_student(pupil_id)
    if not p:
        abort(404)
    creds = db.make_parent_login(pupil_id)
    link = request.url_root.rstrip("/") + url_for("login")
    flash(f"Parent login for {p['full_name']} — save these now, the password is not shown again. "
          f"Site: {link}  ·  Username: {creds['username']}  ·  Password: {creds['password']}", "creds")
    return redirect(url_for("pupil", pupil_id=pupil_id))


# --------------------------------------------------------------------------- #
# Parent / family portal
# --------------------------------------------------------------------------- #
@app.route("/parent")
@login_required
def parent_home():
    if g.user["role"] == "admin":
        return redirect(url_for("admin_home"))
    sid = _parent_pupil_id()
    p = db.get_student(sid)
    en = db.active_enrolment(sid)
    reports = [r for r in db.list_reports(sid) if r["status"] == "sent"]
    return render_template("parent_home.html", pupil=p, enrol=en,
                           monthly=db.monthly_value(en) if en else 0,
                           pay=db.payment_summary(sid), live_status=db.current_status(sid),
                           upcoming=db.list_sessions(sid, upcoming=True),
                           past=db.list_sessions(sid, upcoming=False)[:10],
                           reports=reports,
                           booked_this_month=db.bookings_this_month(sid, included_only=True))


@app.route("/parent/book", methods=["GET", "POST"])
@login_required
def parent_book():
    sid = _parent_pupil_id()
    en = db.active_enrolment(sid)
    limit = en["classes_per_month"] if en else 0
    used = db.bookings_this_month(sid, included_only=True)
    dur = en["class_length_min"] if en else 60
    extra_price = db.per_class_price(en)
    is_extra = db.would_be_extra(sid, en)

    if request.method == "POST":
        slot = request.form.get("slot") or ""
        if not slot:
            flash("Please choose a time.", "error")
        elif is_extra and not request.form.get("confirm_extra"):
            flash("Please tick the box to confirm the extra-class charge.", "error")
        else:
            db.create_session(sid, starts_at=slot[:16], duration_min=dur,
                              subject=(request.form.get("subject") or "").strip() or None,
                              booked_by="parent", billable=is_extra,
                              charge_amount=extra_price if is_extra else 0.0)
            if is_extra:
                flash(f"Extra class booked. £{extra_price:.2f} will be added to your next invoice.", "ok")
            else:
                flash("Class booked. It is now in your timetable.", "ok")
        return redirect(url_for("parent_home"))

    from itertools import groupby
    slots = db.available_slots(duration_min=dur)
    grouped = [(day, list(items)) for day, items in groupby(slots, key=lambda s: s["day"])]
    return render_template("parent_book.html", grouped=grouped, limit=limit, used=used,
                           enrol=en, subjects=SUBJECT_CHOICES, is_extra=is_extra,
                           extra_price=extra_price, pending=db.unbilled_extras(sid))


@app.route("/parent/session/<int:session_id>/cancel", methods=["POST"])
@login_required
def parent_cancel(session_id: int):
    sid = _parent_pupil_id()
    s = db.get_session(session_id)
    if not s or s["student_id"] != sid:
        abort(403)
    import datetime as _dt
    starts = _dt.datetime.strptime(s["starts_at"][:16], "%Y-%m-%d %H:%M")
    if s["status"] != "booked":
        flash("That class can no longer be changed here.", "error")
    elif starts - _dt.datetime.now() < _dt.timedelta(hours=24):
        flash("Classes can only be cancelled here more than 24 hours ahead. Please call us.", "error")
    else:
        db.update_session(session_id, status="cancelled")
        flash("Class cancelled.", "ok")
    return redirect(url_for("parent_home"))


@app.route("/parent/report/<int:report_id>")
@login_required
def parent_report(report_id: int):
    sid = _parent_pupil_id()
    rep = db.get_report(report_id)
    if not rep or rep["student_id"] != sid or rep["status"] != "sent":
        abort(403)
    scores = [(a["assessed_on"], a["overall_score"])
              for a in reversed(db.list_assessments(sid))
              if a.get("overall_score") is not None]
    return render_template("parent_report.html", rep=rep, pupil=db.get_student(sid),
                           scores=scores)


# --------------------------------------------------------------------------- #
def _port_is_free(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind((host, port))
            return True
        except OSError:
            return False


def _wait_then_open(host: str, port: int, url: str) -> None:
    """Open the browser only once the server is actually accepting connections."""
    for _ in range(60):  # up to ~30s
        try:
            with socket.create_connection((host, port), timeout=0.5):
                webbrowser.open(url)
                return
        except OSError:
            time.sleep(0.5)


def run(host: str = "127.0.0.1", port: int = 5000, open_browser: bool = True) -> None:
    db.init_db()
    creds = _bootstrap_admin()

    if not _port_is_free(host, port):
        for alt in range(port + 1, port + 12):
            if _port_is_free(host, alt):
                print(f"  Port {port} is busy - using {alt} instead.")
                port = alt
                break
        else:
            print(f"  Ports {port}-{port + 11} are all busy. "
                  f"Free one up or run:  python main.py serve --port 8080")
            return

    url = f"http://{host}:{port}"
    print(f"\n  {BUSINESS_NAME} - web interface", flush=True)
    print(f"  Open this in your browser:  {url}", flush=True)
    if creds:
        print("\n  First run - your ADMIN login (change the password from the Account page):", flush=True)
        print(f"     username:  {creds['username']}", flush=True)
        print(f"     password:  {creds['password']}", flush=True)
    else:
        print("  Log in with your admin username and password.", flush=True)
    print("\n  Pages:", flush=True)
    print(f"     Tutor / admin :  {url}/admin", flush=True)
    print(f"     Families      :  {url}/parent   (each family signs in to their own child)", flush=True)
    print("  (starting up - the page may take a few seconds the first time)", flush=True)
    print("  Stop the server with Ctrl+C\n", flush=True)

    if open_browser:
        threading.Thread(target=_wait_then_open, args=(host, port, url), daemon=True).start()

    try:
        app.run(host=host, port=port, debug=False, use_reloader=False)
    except OSError as exc:
        print(f"\n  Could not start the server: {exc}")
        print("  Try a different port:  python main.py serve --port 8080")


if __name__ == "__main__":
    run()
