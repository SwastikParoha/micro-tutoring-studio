"""Micro-Tutoring Studio - command-line interface.

New here?  Run these three commands:

    python main.py setup          # one-time: downloads curriculum data + creates the database
    python main.py demo           # see the whole thing work on a sample pupil
    python main.py --help         # full command list

Everyday use:

    python main.py onboard --notes-file notes.txt          # add a pupil from demo-lesson notes
    python main.py topics --subject science --keystage 2   # browse what you can teach
    python main.py generate-week --student "Ava Patel" --topic "Rivers" --keystage 2
"""
from __future__ import annotations

import argparse
import sys
import textwrap
from pathlib import Path

import config

OK, BAD, ARROW = "[ OK ]", "[ !! ]", "  ->"


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def hr(text: str = "") -> None:
    print("\n" + "=" * 70 + (f"\n {text}\n" + "=" * 70 if text else ""))


def _load_notes(args) -> str | None:
    if getattr(args, "notes_file", None):
        return Path(args.notes_file).read_text(encoding="utf-8")
    return getattr(args, "notes", None)


_MIME = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png",
         ".webp": "image/webp", ".heic": "image/heic", ".gif": "image/gif"}


def _load_image(path: str | None):
    if not path:
        return None
    p = Path(path)
    if not p.exists():
        print(f"{BAD} Image not found: {path}", file=sys.stderr)
        return None
    return p.read_bytes(), _MIME.get(p.suffix.lower(), "image/jpeg")


def _resolve_student(ident: str):
    from database import db_manager as db
    row = db.get_student(int(ident)) if str(ident).isdigit() else db.find_student_by_name(ident)
    return row


# --------------------------------------------------------------------------- #
# setup / doctor / demo
# --------------------------------------------------------------------------- #
def cmd_setup(args) -> int:
    hr("Setup")

    # 1. .env
    env = config.ROOT / ".env"
    if not env.exists():
        env.write_text((config.ROOT / ".env.example").read_text(encoding="utf-8"), encoding="utf-8")
        print(f"{OK} created .env  (edit it to add API keys - optional)")
    else:
        print(f"{OK} .env already exists")

    # 2. database
    from database import db_manager as db
    db.init_db()
    print(f"{OK} CRM database ready  ({config.DB_PATH.name})")

    # 3. Oak curriculum dataset
    if config.oak_local_available():
        mb = config.OAK_LOCAL_DB_PATH.stat().st_size / 1e6
        print(f"{OK} Oak curriculum dataset present  ({mb:.0f} MB)")
    elif args.skip_download:
        print(f"{BAD} Oak curriculum dataset not downloaded (--skip-download).")
        print(f"{ARROW} the app will use limited bundled sample data until you run setup without --skip-download")
    else:
        _download_oak_dataset()

    # 4. official study material (real National Curriculum docs + real past SATs papers)
    from agents import study_materials as sm
    n = sm.counts()
    if n["missing"] == 0:
        print(f"{OK} Study material library complete  ({n['total']} official documents)")
    elif args.skip_download:
        print(f"{BAD} Study material not downloaded (--skip-download).")
        print(f"{ARROW} run: python main.py fetch-study-materials")
    else:
        print(f"{ARROW} downloading official study material "
              f"({n['missing']} National Curriculum docs + past SATs papers) ...")
        r = sm.fetch_all(verbose=True)
        if r["failed"]:
            print(f"{BAD} {r['failed']} file(s) failed to download - re-run "
                  f"python main.py fetch-study-materials later")
        else:
            print(f"{OK} Study material library ready  ({r['downloaded']} downloaded)")

    hr()
    print("Setup complete. Next:\n")
    print("    python main.py demo         # watch it build a workbook for a sample pupil")
    print("    python main.py doctor       # check everything is wired up")
    print("    python main.py --help       # all commands\n")
    return 0


def _download_oak_dataset() -> None:
    from agents import oak_local
    import requests

    dest = config.OAK_LOCAL_DB_PATH
    dest.parent.mkdir(parents=True, exist_ok=True)
    print(f"{ARROW} downloading Oak National Academy curriculum dataset ({oak_local.RELEASE_TAG}) ...")
    print(f"     {oak_local.DOWNLOAD_URL}")
    try:
        with requests.get(oak_local.DOWNLOAD_URL, stream=True, timeout=60) as r:
            r.raise_for_status()
            total = int(r.headers.get("content-length", 0))
            done = 0
            tmp = dest.with_suffix(".part")
            with open(tmp, "wb") as f:
                for chunk in r.iter_content(chunk_size=1 << 20):
                    f.write(chunk)
                    done += len(chunk)
                    if total:
                        pct = 100 * done / total
                        print(f"\r     {done/1e6:5.1f} / {total/1e6:.0f} MB  ({pct:3.0f}%)", end="")
            print()
            tmp.replace(dest)
        print(f"{OK} Oak curriculum dataset saved to {dest}")
    except Exception as exc:  # noqa: BLE001
        print(f"{BAD} download failed: {exc}")
        print(f"{ARROW} you can download it manually and place it at {dest}:")
        print(f"     {oak_local.DOWNLOAD_URL}")
        print(f"{ARROW} the app still works meanwhile using bundled sample data.")


def cmd_fetch_study_materials(_args) -> int:
    hr("Study material")
    from agents import study_materials as sm
    n = sm.counts()
    print(f"{n['downloaded']}/{n['total']} official documents already downloaded.")
    r = sm.fetch_all(verbose=True)
    hr()
    print(f"Downloaded {r['downloaded']} new file(s), {r['already_had']} already present, "
          f"{r['failed']} failed.")
    print("Open them in the web interface: Admin -> Study material.")
    return 1 if r["failed"] else 0


def cmd_doctor(_args) -> int:
    hr("Health check")
    rows: list[tuple[bool, str, str]] = []

    rows.append((sys.version_info >= (3, 10), f"Python {sys.version.split()[0]}",
                 "need Python 3.10+"))

    for mod, pip in [("requests", "requests"), ("jinja2", "jinja2"), ("markdown", "markdown"),
                     ("dotenv", "python-dotenv")]:
        try:
            __import__(mod)
            rows.append((True, f"package: {pip}", ""))
        except ImportError:
            rows.append((False, f"package: {pip}", f"pip install {pip}"))

    rows.append((config.DB_PATH.exists(), f"CRM database ({config.DB_PATH.name})",
                 "run: python main.py setup"))
    rows.append((config.oak_local_available(), "Oak curriculum dataset",
                 "run: python main.py setup  (uses bundled sample data meanwhile)"))
    from agents import study_materials as sm
    _smn = sm.counts()
    rows.append((_smn["missing"] == 0,
                 f"Study material: {_smn['downloaded']}/{_smn['total']} official documents",
                 "run: python main.py fetch-study-materials"))
    from agents import llm
    gem_ok, gem_msg = llm.health_check(live=True)
    rows.append((gem_ok, f"Gemini: {gem_msg}",
                 "optional - add GEMINI_API_KEY to .env; without it a deterministic fallback is used"))
    if config.OAK_API_KEY:
        from agents import oak_api
        ok = bool(oak_api._get("/subjects"))
        rows.append((ok, "Oak API depth (lesson transcripts + real quiz questions)"
                     + ("" if ok else " - key set but not responding"),
                     "check OAK_API_KEY / OAK_API_BASE_URL in .env"))
    else:
        rows.append((True, "Oak API depth: off (add OAK_API_KEY for lesson transcripts)",
                     "optional - the full dataset already covers the curriculum"))

    # PDF engine
    pdf_engine = _detect_pdf_engine()
    rows.append((pdf_engine != "none", f"PDF engine: {pdf_engine}",
                 "pip install playwright && playwright install chromium"))

    for ok, label, fix in rows:
        print(f"{OK if ok else BAD} {label}")
        if not ok and fix:
            print(f"{ARROW} {fix}")

    hard_fail = any(not ok for ok, label, fix in rows
                    if label.startswith(("Python", "package")))
    hr()
    print("Curriculum source in use:", config.curriculum_source())
    if not config.GEMINI_API_KEY:
        gen = "built-in generator (no GEMINI_API_KEY)"
    elif gem_ok:
        gen = f"Google Gemini ({config.GEMINI_MODEL})"
    else:
        gen = "built-in generator (Gemini key set but not responding - see above)"
    print("Lesson generator:", gen)
    print("\nAll essentials present." if not hard_fail else "\nInstall the missing packages above, then re-run.")
    return 1 if hard_fail else 0


def _detect_pdf_engine() -> str:
    try:
        from pdf_builder import _quiet
        with _quiet():
            from weasyprint import HTML  # noqa: F401
        return "weasyprint"
    except Exception:
        pass
    try:
        import playwright  # noqa: F401
        return "chromium (playwright)"
    except Exception:
        return "none (will write HTML - open it and 'Print to PDF')"


def cmd_demo(args) -> int:
    from database import db_manager as db
    db.init_db()
    sample = config.ROOT / "sample_assessment.txt"

    hr("Demo - onboarding a sample pupil")
    class _NS:  # minimal namespace for cmd_onboard
        notes_file = str(sample); notes = None
        name = year = interests = guardian = contact = image = None
    cmd_onboard(_NS())

    hr("Demo - generating this week's workbook")
    class _G:
        topic = args.topic or "Fractions"
        keystage = 2
        student = "Ava Patel"
        subject = "maths"
        year = None; interests = None
        questions = 8
        difficulty = None
        space = "few"
        crewai = False
        output = None
        dump_json = None
    rc = cmd_generate_week(_G())

    hr()
    print("That's the whole pipeline. To do it for a real pupil:\n")
    print("    python main.py onboard --notes-file <your notes>.txt")
    print('    python main.py generate-week --student "<name>" --topic "<topic>" --keystage <1|2>\n')
    return rc


# --------------------------------------------------------------------------- #
# browse
# --------------------------------------------------------------------------- #
def cmd_subjects(_args) -> int:
    from agents import oak_local
    hr("Subjects you can teach")
    if config.oak_local_available():
        print("Type any of these as --subject (plus friendly aliases like 'maths', 'science'):\n")
        for s in oak_local.list_subjects():
            print(f"  - {s}")
        print("\nAliases: maths -> Mathematics, science -> Biology/Chemistry/Physics, "
              "english, geography, history, art, computing, music, pe, dt, languages")
    else:
        print("Full subject list needs the Oak dataset. Run:  python main.py setup\n")
        print("Bundled sample data covers: maths, english, science, geography, history (KS1 & KS2).")
    return 0


def cmd_topics(args) -> int:
    if not config.oak_local_available():
        print("Browsing topics needs the Oak dataset. Run:  python main.py setup")
        print("(You can still pass any --topic to generate-week; it will match as best it can.)")
        return 1
    from agents import oak_local
    units = oak_local.list_units(args.subject, args.keystage, search=args.search)
    hr(f"{args.subject.title()} - Key Stage {args.keystage}"
       + (f"  (matching '{args.search}')" if args.search else ""))
    if not units:
        print("No matching units. Try a broader --search, or `python main.py subjects`.")
        return 0
    current = None
    for u in units:
        if u["subject"] != current:
            current = u["subject"]
            print(f"\n{current}")
        print(f"  * {u['title']}")
        if u["description"]:
            print(textwrap.fill(u["description"], width=66,
                                initial_indent="      ", subsequent_indent="      "))
    print(f"\n{len(units)} unit(s). Use one as --topic, e.g.:")
    print(f'   python main.py generate-week --topic "{units[0]["title"]}" --keystage {args.keystage} '
          f'--subject {args.subject}')
    return 0


# --------------------------------------------------------------------------- #
# core workflow
# --------------------------------------------------------------------------- #
def cmd_onboard(args) -> int:
    from agents import crew
    from database import db_manager as db
    db.init_db()

    raw_notes = _load_notes(args)
    image = _load_image(getattr(args, "image", None))
    if not raw_notes and not image:
        print("Provide notes (--notes-file / --notes) and/or a photo of the pupil's work (--image).",
              file=sys.stderr)
        print('Example:  python main.py onboard --notes-file sample_assessment.txt', file=sys.stderr)
        return 2

    hr("The Assessor - reading the assessment")
    analysis = crew.run_assessment(raw_notes, image=image, verbose=True)
    st = analysis.get("student", {})

    name = args.name or st.get("full_name")
    if not name:
        print(f"\n{BAD} Could not detect the pupil's name in the notes.")
        print(f"{ARROW} re-run adding:  --name \"First Last\"")
        return 2

    year = args.year or st.get("year_group")
    key_stage = st.get("key_stage") or (1 if year and year <= 2 else 2 if year else None)
    interests = args.interests or ", ".join(st.get("interests", []) or [])

    fields = {k: v for k, v in dict(
        full_name=name, year_group=year, key_stage=key_stage, age=st.get("age"),
        interests=interests or None, guardian_name=args.guardian or st.get("guardian_name"),
        guardian_contact=args.contact,
    ).items() if v is not None}

    existing = db.find_student_by_name(name)
    if existing:
        sid = existing["id"]
        db.update_student(sid, **{k: v for k, v in fields.items() if k != "full_name"})
        print(f"\n{OK} updated pupil #{sid}: {name}")
    else:
        sid = db.create_student(**fields)
        print(f"\n{OK} added pupil #{sid}: {name}")

    aid = db.add_assessment(sid, subject=analysis.get("subject", "maths"),
                            raw_notes=raw_notes or "", kind="diagnostic",
                            image_path=(args.image if getattr(args, "image", None) else None),
                            overall_score=analysis.get("overall_score"),
                            difficulty_reco=analysis.get("difficulty_recommendation"),
                            strengths=analysis.get("strengths", []),
                            weak_points=analysis.get("weak_points", []), analysis=analysis)
    print(f"{OK} saved assessment #{aid}  (subject: {analysis.get('subject', 'maths')}, "
          f"score: {analysis.get('overall_score', 'n/a')}, "
          f"suggested level: {analysis.get('difficulty_recommendation', 'core')})")

    print("\nStrengths:")
    for s in analysis.get("strengths", []):
        print(f"  + {s}")
    print("\nWeak points to target:")
    for wp in analysis.get("weak_points", []):
        if isinstance(wp, dict):
            print(f"  - [{wp.get('severity', '?')}] {wp.get('topic', '')}")
        else:
            print(f"  - {wp}")

    hr()
    print("Next - build this pupil's first workbook:\n")
    print(f'    python main.py generate-week --student "{name}" --topic "<topic>" '
          f'--keystage {key_stage or 2}\n')
    print("Not sure what topic? Browse:  python main.py topics --subject "
          f"{analysis.get('subject', 'maths')} --keystage {key_stage or 2}")
    return 0


def cmd_generate_week(args) -> int:
    from agents import crew
    from database import db_manager as db
    db.init_db()

    student: dict = {}
    assessment = None
    if args.student:
        row = _resolve_student(args.student)
        if not row:
            print(f"{BAD} No pupil matching '{args.student}'.", file=sys.stderr)
            print(f"{ARROW} list pupils:  python main.py students", file=sys.stderr)
            print(f"{ARROW} or add one:   python main.py onboard --notes-file <notes>.txt", file=sys.stderr)
            return 2
        student = row
        a = db.latest_assessment(student["id"], subject=args.subject)
        assessment = a.get("analysis_json") if a else None

    if args.year:
        student.setdefault("year_group", args.year)
    if args.interests:
        student["interests"] = args.interests
    key_stage = args.keystage or student.get("key_stage") or 2
    student.setdefault("key_stage", key_stage)

    difficulty = args.difficulty
    reco_reason = ""
    if not difficulty and student.get("id"):
        difficulty, reco_reason = db.recommend_difficulty(student["id"])
    difficulty = difficulty or "core"

    hr(f"Weekly workbook - {args.topic}  (Key Stage {key_stage}, {args.subject})")
    if student.get("full_name"):
        print(f"Pupil:      {student['full_name']}  (Year {student.get('year_group', '?')})")
        print(f"Interests:  {student.get('interests') or 'not recorded'}")
        print(f"Assessment: {'on file - questions will be personalised' if assessment else 'none - using year-group baseline'}")
    else:
        print("No pupil selected - generating a generic year-group workbook.")
    print(f"Difficulty: {difficulty}" + (f"  ({reco_reason})" if reco_reason else ""))
    print(f"Curriculum: {config.curriculum_source()}")

    result = crew.run_weekly_pipeline(
        topic=args.topic, key_stage=key_stage, subject=args.subject, student=student,
        assessment=assessment, num_questions=args.questions, difficulty=difficulty,
        space=getattr(args, "space", "few"), use_crewai=args.crewai, verbose=True,
    )

    hr("Building the PDF")
    from pdf_builder import build_pdf
    out = build_pdf(result.as_dict(), student, out_path=args.output)

    if student.get("id"):
        bid = db.add_weekly_book(
            student["id"], subject=result.subject, topic=result.topic, key_stage=result.key_stage,
            year_group=result.year_group, difficulty=result.difficulty,
            curriculum=result.curriculum, strategy=result.strategy,
            explanation_md=result.explanation_md, problems=result.problems, pdf_path=str(out),
        )
        print(f"{OK} saved as WeeklyBook #{bid} for {student['full_name']}")

    if args.dump_json:
        import json
        Path(args.dump_json).write_text(
            json.dumps({"result": result.as_dict(), "student": student}, indent=2, default=str),
            encoding="utf-8")
        print(f"{OK} pipeline detail written to {args.dump_json}")

    hr()
    print(f"Workbook ready:  {out}\n")
    print(f"Curriculum grounding: {config.curriculum_source()}")
    print(f"Lesson generator:     {'Google Gemini' if result.engine in ('gemini', 'crewai') else 'built-in fallback'}")
    return 0


# --------------------------------------------------------------------------- #
# CRM
# --------------------------------------------------------------------------- #
def cmd_students(_args) -> int:
    from database import db_manager as db
    db.init_db()
    rows = db.list_students()
    hr(f"Pupils ({len(rows)})")
    if not rows:
        print("None yet. Add one:  python main.py onboard --notes-file sample_assessment.txt")
        return 0
    for r in rows:
        print(f"  #{r['id']:<3} {r['full_name']:<24} Year {r.get('year_group', '?'):<3} "
              f"KS{r.get('key_stage', '?')}   {r.get('interests') or ''}")
    print("\nDetail:  python main.py student <id>")
    return 0


def cmd_student(args) -> int:
    from database import db_manager as db
    db.init_db()
    r = db.get_student(int(args.id)) if str(args.id).isdigit() else db.find_student_by_name(args.id)
    if not r:
        print(f"{BAD} No pupil '{args.id}'.", file=sys.stderr)
        return 2
    hr(f"#{r['id']}  {r['full_name']}")
    for k in ("year_group", "key_stage", "age", "school", "guardian_name", "guardian_contact", "interests"):
        if r.get(k):
            print(f"  {k.replace('_', ' '):17} {r[k]}")
    level, reason = db.recommend_difficulty(r["id"])
    print(f"\n  Suggested difficulty next: {level}  ({reason})")
    print("\n  Assessments / weekly results:")
    for a in db.list_assessments(r["id"]):
        print(f"    #{a['id']}  {a['assessed_on']}  {a.get('kind', 'diagnostic'):10}  {a['subject']:8}  "
              f"score {a.get('overall_score', '-')}  "
              f"({len(a.get('weak_points_json') or [])} weak points)")
    print("\n  Weekly workbooks:")
    for b in db.list_books(r["id"]):
        print(f"    #{b['id']}  {b['week_of']}  {b['subject']:8}  {(b.get('difficulty') or 'core'):8}  "
              f"{b['topic']:24}  {b.get('pdf_path') or '-'}")
    return 0


def cmd_log_result(args) -> int:
    """Record how a pupil did this week (score and/or photo of their work)."""
    from agents import crew
    from database import db_manager as db
    db.init_db()

    row = _resolve_student(args.student)
    if not row:
        print(f"{BAD} No pupil matching '{args.student}'.", file=sys.stderr)
        return 2
    image = _load_image(args.image)
    if not image and args.score is None and not args.notes:
        print("Give at least one of: --score, --image, --notes.", file=sys.stderr)
        return 2

    hr(f"Logging this week's result for {row['full_name']}")
    analysis = {}
    if args.notes or image:
        analysis = crew.run_assessment(args.notes, image=image, kind="weekly",
                                       subject=args.subject, verbose=True)
    score = args.score if args.score is not None else analysis.get("overall_score")
    reco = analysis.get("difficulty_recommendation")
    if not reco and score is not None:
        reco = "stretch" if score >= 85 else "support" if score < 55 else "core"
    reco = reco or "core"

    aid = db.add_assessment(row["id"], subject=args.subject or analysis.get("subject", "maths"),
                            kind="weekly", raw_notes=args.notes or "",
                            image_path=args.image, overall_score=score,
                            difficulty_reco=reco,
                            strengths=analysis.get("strengths", []),
                            weak_points=analysis.get("weak_points", []), analysis=analysis)
    print(f"\n{OK} logged result #{aid}  (score: {score if score is not None else 'n/a'})")
    for wp in analysis.get("weak_points", [])[:4]:
        print(f"  - {wp.get('topic', wp) if isinstance(wp, dict) else wp}")
    level, reason = db.recommend_difficulty(row["id"])
    print(f"\n{ARROW} Next workbook difficulty: {level}  ({reason})")
    print(f'\n    python main.py generate-week --student "{row["full_name"]}" --topic "<topic>"')
    return 0


def cmd_config(_args) -> int:
    hr("Configuration")
    print(config.summary())
    print("\nEdit .env to change keys/paths. `python main.py doctor` checks everything.")
    return 0


def cmd_serve(args) -> int:
    try:
        import webapp
    except ImportError:
        print(f"{BAD} Flask is not installed.  Run:  pip install flask", file=sys.stderr)
        return 2
    webapp.run(host=args.host, port=args.port, open_browser=not args.no_browser)
    return 0


# --------------------------------------------------------------------------- #
# parser
# --------------------------------------------------------------------------- #
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="main.py",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description=textwrap.dedent("""\
            Micro-Tutoring Studio
            ---------------------
            Turn a demo-lesson assessment into a personalised, print-ready weekly
            workbook, grounded in the UK National Curriculum (Oak National Academy).

            FIRST TIME:
              python main.py setup      one-time: curriculum data + database
              python main.py demo       see it work on a sample pupil
              python main.py doctor     check your install
        """),
        epilog=textwrap.dedent("""\
            typical week:
              python main.py onboard --notes-file notes.txt
              python main.py topics --subject science --keystage 2 --search rivers
              python main.py generate-week --student "Ava Patel" --topic "Rivers" --keystage 2
        """),
    )
    sub = p.add_subparsers(dest="command", metavar="<command>")

    s = sub.add_parser("setup", help="one-time setup: download curriculum data + create the database")
    s.add_argument("--skip-download", action="store_true", help="don't download the Oak dataset (~40 MB)")
    s.set_defaults(func=cmd_setup)

    sub.add_parser("doctor", help="check packages, keys, curriculum data and PDF engine").set_defaults(func=cmd_doctor)
    sub.add_parser("fetch-study-materials",
                   help="download the real National Curriculum docs + past SATs papers"
                   ).set_defaults(func=cmd_fetch_study_materials)

    d = sub.add_parser("demo", help="run the full pipeline on the bundled sample pupil")
    d.add_argument("--topic", help='topic to demo (default "Fractions")')
    d.set_defaults(func=cmd_demo)

    sub.add_parser("subjects", help="list subjects you can teach").set_defaults(func=cmd_subjects)

    t = sub.add_parser("topics", help="browse curriculum units for a subject + key stage")
    t.add_argument("--subject", required=True, help="maths | english | science | geography | history | ...")
    t.add_argument("--keystage", type=int, choices=[1, 2, 3, 4], required=True)
    t.add_argument("--search", help="filter by a word, e.g. 'fractions', 'rivers', 'Vikings'")
    t.set_defaults(func=cmd_topics)

    o = sub.add_parser("onboard", help="add a pupil from a tutor's demo-lesson notes (runs The Assessor)")
    o.add_argument("--notes-file", help="path to a text file of the demo-lesson notes")
    o.add_argument("--notes", help="the notes as a string (alternative to --notes-file)")
    o.add_argument("--name", help="pupil's full name (if not clear from the notes)")
    o.add_argument("--year", type=int, help="year group 1-6")
    o.add_argument("--interests", help='comma-separated, e.g. "football, pizza, Minecraft"')
    o.add_argument("--guardian", help="parent/guardian name")
    o.add_argument("--contact", help="parent/guardian contact")
    o.add_argument("--image", help="photo of the pupil's completed work (jpg/png) - the AI reads it")
    o.set_defaults(func=cmd_onboard)

    g = sub.add_parser("generate-week", aliases=["generate_week"],
                       help="build this week's personalised PDF workbook (runs the full agent pipeline)")
    g.add_argument("--topic", required=True, help='e.g. "Fractions", "Rivers", "The Vikings"')
    g.add_argument("--keystage", type=int, choices=[1, 2, 3, 4], help="1 or 2 (defaults to the pupil's)")
    g.add_argument("--student", help="pupil id or name (adds their profile + latest assessment)")
    g.add_argument("--subject", default="maths",
                   help="maths | english | science | geography | history | ... (default: maths)")
    g.add_argument("--difficulty", choices=["support", "core", "stretch"],
                   help="easier / on-track / harder (default: auto from the pupil's recent results)")
    g.add_argument("--space", choices=["few", "some", "lots"], default="few",
                   help="working room per question: few (~4 lines) / some (~half page) / lots (full half-page+)")
    g.add_argument("--year", type=int, help="year group override")
    g.add_argument("--interests", help="interests override")
    g.add_argument("--questions", type=int, default=8, help="how many practice questions, 5-10 (default 8)")
    g.add_argument("--crewai", action="store_true", help="run the agent hand-offs through CrewAI")
    g.add_argument("--output", help="explicit output path for the PDF")
    g.add_argument("--dump-json", help="also save the full pipeline result as JSON")
    g.set_defaults(func=cmd_generate_week)

    lr = sub.add_parser("log-result", help="record how a pupil did this week (score and/or photo)")
    lr.add_argument("--student", required=True, help="pupil id or name")
    lr.add_argument("--score", type=float, help="their score out of 100")
    lr.add_argument("--image", help="photo of their completed worksheet - the AI reads it")
    lr.add_argument("--notes", help="anything you noticed")
    lr.add_argument("--subject", help="subject this result is for")
    lr.set_defaults(func=cmd_log_result)

    sub.add_parser("students", help="list pupils in the CRM").set_defaults(func=cmd_students)
    st = sub.add_parser("student", help="show one pupil's profile, assessments and workbooks")
    st.add_argument("id", help="pupil id or name")
    st.set_defaults(func=cmd_student)

    sub.add_parser("config", help="show resolved configuration").set_defaults(func=cmd_config)

    sv = sub.add_parser("serve", help="start the web interface (recommended for tutors)")
    sv.add_argument("--host", default="127.0.0.1")
    sv.add_argument("--port", type=int, default=5000)
    sv.add_argument("--no-browser", action="store_true", help="don't auto-open a browser")
    sv.set_defaults(func=cmd_serve)
    return p


def _welcome() -> None:
    print(textwrap.dedent(f"""\
        Micro-Tutoring Studio
        =====================
        Personalised, curriculum-grounded weekly workbooks for primary tutoring.

        First time?
          1.  python main.py setup      download curriculum data + create the database
          2.  python main.py serve      open the web interface  (easiest - no command line)
              ...or python main.py demo to watch it work on a sample pupil in the terminal

        Prefer the terminal?
          python main.py onboard --notes-file <demo notes>.txt
          python main.py topics --subject science --keystage 2
          python main.py generate-week --student "<name>" --topic "<topic>" --keystage 2

        All commands:  python main.py --help
        Status now:    {config.curriculum_source()}
    """))


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "func", None):
        _welcome()
        return 0
    try:
        return args.func(args)
    except KeyboardInterrupt:
        print("\nCancelled.")
        return 130
    except FileNotFoundError as exc:
        print(f"\n{BAD} File not found: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
