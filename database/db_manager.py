"""SQLite CRM access layer.

Thin, dependency-free CRUD helpers around the schema in ``schema.sql``.
JSON-shaped columns are transparently encoded/decoded.
"""
from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterable, Iterator, Optional

import config

_JSON_COLUMNS = {
    "strengths_json", "weak_points_json", "analysis_json",
    "curriculum_json", "strategy_json", "problems_json",
    "subjects_json", "line_items_json",
}


# --------------------------------------------------------------------------- #
# Connection / schema
# --------------------------------------------------------------------------- #
@contextmanager
def connect(db_path: Optional[Path] = None) -> Iterator[sqlite3.Connection]:
    path = Path(db_path or config.DB_PATH)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


# columns added after the first release - applied to existing databases on init
_MIGRATIONS = [
    ("Assessments", "kind", "TEXT DEFAULT 'diagnostic'"),
    ("Assessments", "image_path", "TEXT"),
    ("Assessments", "difficulty_reco", "TEXT"),
    ("WeeklyBooks", "difficulty", "TEXT DEFAULT 'core'"),
    ("Students", "date_of_birth", "TEXT"),
    ("Students", "guardian_email", "TEXT"),
    ("Students", "guardian_relationship", "TEXT"),
    ("Students", "address", "TEXT"),
    ("Students", "goals", "TEXT"),
    ("Students", "status", "TEXT DEFAULT 'enquiry'"),
    ("Sessions", "billable", "INTEGER DEFAULT 0"),
    ("Sessions", "charge_amount", "REAL DEFAULT 0"),
    ("Sessions", "invoice_id", "INTEGER"),
]


def init_db(db_path: Optional[Path] = None, schema_path: Optional[Path] = None) -> Path:
    """Create tables from schema.sql and apply column migrations (idempotent)."""
    schema = Path(schema_path or config.SCHEMA_PATH).read_text(encoding="utf-8")
    with connect(db_path) as conn:
        conn.executescript(schema)
        for table, col, decl in _MIGRATIONS:
            try:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {decl}")
            except sqlite3.OperationalError:
                pass  # column already exists
    return Path(db_path or config.DB_PATH)


def _row_to_dict(row: sqlite3.Row | None) -> Optional[dict[str, Any]]:
    if row is None:
        return None
    out = dict(row)
    for col in _JSON_COLUMNS:
        if col in out and isinstance(out[col], str) and out[col]:
            try:
                out[col] = json.loads(out[col])
            except json.JSONDecodeError:
                pass
    return out


def _dump(value: Any) -> Any:
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False)
    return value


# --------------------------------------------------------------------------- #
# Students
# --------------------------------------------------------------------------- #
_STUDENT_FIELDS = {
    "full_name", "year_group", "key_stage", "age", "date_of_birth", "school",
    "guardian_name", "guardian_contact", "guardian_email", "guardian_relationship",
    "address", "interests", "goals", "status", "notes",
}


def create_student(**fields: Any) -> int:
    data = {k: v for k, v in fields.items() if k in _STUDENT_FIELDS}
    if not data.get("full_name"):
        raise ValueError("full_name is required")
    if data.get("key_stage") is None and data.get("year_group") is not None:
        data["key_stage"] = 1 if int(data["year_group"]) <= 2 else 2
    cols = ", ".join(data)
    ph = ", ".join("?" for _ in data)
    with connect() as conn:
        cur = conn.execute(f"INSERT INTO Students ({cols}) VALUES ({ph})", tuple(data.values()))
        return int(cur.lastrowid)


def get_student(student_id: int) -> Optional[dict[str, Any]]:
    with connect() as conn:
        return _row_to_dict(conn.execute("SELECT * FROM Students WHERE id = ?", (student_id,)).fetchone())


def find_student_by_name(name: str) -> Optional[dict[str, Any]]:
    with connect() as conn:
        row = conn.execute(
            "SELECT * FROM Students WHERE lower(full_name) = lower(?) ORDER BY id LIMIT 1", (name,)
        ).fetchone()
        return _row_to_dict(row)


def search_students(query: str) -> list[dict[str, Any]]:
    """Name / guardian / school substring search."""
    q = f"%{query.strip().lower()}%"
    with connect() as conn:
        return [_row_to_dict(r) for r in conn.execute(
            """SELECT * FROM Students
               WHERE lower(full_name) LIKE ? OR lower(coalesce(guardian_name,'')) LIKE ?
                  OR lower(coalesce(school,'')) LIKE ?
               ORDER BY full_name""", (q, q, q))]


def list_students() -> list[dict[str, Any]]:
    with connect() as conn:
        return [_row_to_dict(r) for r in conn.execute("SELECT * FROM Students ORDER BY full_name")]


def update_student(student_id: int, **fields: Any) -> None:
    fields = {k: v for k, v in fields.items() if k in _STUDENT_FIELDS}
    if not fields:
        return
    sets = ", ".join(f"{k} = ?" for k in fields)
    values = list(fields.values())
    with connect() as conn:
        conn.execute(
            f"UPDATE Students SET {sets}, updated_at = datetime('now') WHERE id = ?",
            (*values, student_id),
        )


# --------------------------------------------------------------------------- #
# Assessments
# --------------------------------------------------------------------------- #
def add_assessment(student_id: int, *, subject: str = "maths", raw_notes: str = "",
                   kind: str = "diagnostic", image_path: str | None = None,
                   overall_score: float | None = None, difficulty_reco: str | None = None,
                   strengths: Iterable[str] | None = None,
                   weak_points: Iterable[Any] | None = None, analysis: dict | None = None) -> int:
    with connect() as conn:
        cur = conn.execute(
            """INSERT INTO Assessments
               (student_id, subject, kind, raw_notes, image_path, overall_score,
                difficulty_reco, strengths_json, weak_points_json, analysis_json)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                student_id, subject, kind, raw_notes, image_path, overall_score,
                difficulty_reco,
                _dump(list(strengths or [])),
                _dump(list(weak_points or [])),
                _dump(analysis or {}),
            ),
        )
        return int(cur.lastrowid)


def recommend_difficulty(student_id: int) -> tuple[str, str]:
    """Suggest the next workbook's difficulty from recent results.

    Returns (level, reason). Levels: 'support' | 'core' | 'stretch'.
    """
    rows = list_assessments(student_id)
    # An explicit recommendation from the most recent review/result wins.
    for a in rows:
        if a.get("difficulty_reco"):
            if a.get("image_path"):
                why = "based on the last photo of their work"
            elif a.get("overall_score") is not None:
                why = f"based on their last score ({a['overall_score']:.0f}%)"
            else:
                why = "based on the last assessment"
            return a["difficulty_reco"], why
    scores = [a["overall_score"] for a in rows if a.get("overall_score") is not None][:2]
    if not scores:
        return "core", "no results logged yet - starting at the year-group standard"
    avg = sum(scores) / len(scores)
    last = scores[0]
    if avg >= 85 and last >= 80:
        return "stretch", f"recent scores are strong ({_fmt_scores(scores)})"
    if avg < 55 or last < 45:
        return "support", f"recent scores suggest more practice is needed ({_fmt_scores(scores)})"
    return "core", f"on track at the year-group standard ({_fmt_scores(scores)})"


def _fmt_scores(scores: list[float]) -> str:
    return ", ".join(f"{s:.0f}%" for s in scores)


def latest_assessment(student_id: int, subject: str | None = None) -> Optional[dict[str, Any]]:
    q = "SELECT * FROM Assessments WHERE student_id = ?"
    params: list[Any] = [student_id]
    if subject:
        q += " AND subject = ?"
        params.append(subject)
    q += " ORDER BY assessed_on DESC, id DESC LIMIT 1"
    with connect() as conn:
        return _row_to_dict(conn.execute(q, tuple(params)).fetchone())


def list_assessments(student_id: int) -> list[dict[str, Any]]:
    with connect() as conn:
        return [
            _row_to_dict(r)
            for r in conn.execute(
                "SELECT * FROM Assessments WHERE student_id = ? ORDER BY id DESC", (student_id,)
            )
        ]


# --------------------------------------------------------------------------- #
# WeeklyBooks
# --------------------------------------------------------------------------- #
def add_weekly_book(student_id: int, *, subject: str, topic: str, key_stage: int | None,
                    year_group: int | None, curriculum: dict, strategy: dict,
                    explanation_md: str, problems: dict, difficulty: str = "core",
                    pdf_path: str | None = None) -> int:
    with connect() as conn:
        cur = conn.execute(
            """INSERT INTO WeeklyBooks
               (student_id, subject, topic, key_stage, year_group, difficulty,
                curriculum_json, strategy_json, explanation_md, problems_json, pdf_path)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                student_id, subject, topic, key_stage, year_group, difficulty,
                _dump(curriculum), _dump(strategy), explanation_md, _dump(problems), pdf_path,
            ),
        )
        return int(cur.lastrowid)


def latest_book(student_id: int) -> Optional[dict[str, Any]]:
    with connect() as conn:
        return _row_to_dict(conn.execute(
            "SELECT * FROM WeeklyBooks WHERE student_id = ? ORDER BY id DESC LIMIT 1",
            (student_id,)).fetchone())


def set_book_pdf(book_id: int, pdf_path: str) -> None:
    with connect() as conn:
        conn.execute("UPDATE WeeklyBooks SET pdf_path = ? WHERE id = ?", (pdf_path, book_id))


def list_books(student_id: int) -> list[dict[str, Any]]:
    with connect() as conn:
        return [
            _row_to_dict(r)
            for r in conn.execute(
                "SELECT * FROM WeeklyBooks WHERE student_id = ? ORDER BY id DESC", (student_id,)
            )
        ]


def get_book(book_id: int) -> Optional[dict[str, Any]]:
    with connect() as conn:
        return _row_to_dict(conn.execute("SELECT * FROM WeeklyBooks WHERE id = ?", (book_id,)).fetchone())


# --------------------------------------------------------------------------- #
# Enrolments  (a pupil's plan + pricing)
# --------------------------------------------------------------------------- #
_ENROL_FIELDS = {"status", "start_date", "end_date", "hourly_rate", "classes_per_month",
                 "class_length_min", "subjects_json", "payment_method", "payment_schedule", "notes"}


def create_enrolment(student_id: int, *, hourly_rate: float, classes_per_month: int,
                     class_length_min: int = 60, subjects: list | None = None,
                     payment_method: str = "bank transfer", payment_schedule: str = "monthly",
                     start_date: str | None = None, notes: str = "") -> int:
    with connect() as conn:
        # a pupil has one live plan - end any existing active one
        conn.execute("UPDATE Enrolments SET status = 'ended', end_date = date('now') "
                     "WHERE student_id = ? AND status = 'active'", (student_id,))
        cur = conn.execute(
            """INSERT INTO Enrolments
               (student_id, hourly_rate, classes_per_month, class_length_min, subjects_json,
                payment_method, payment_schedule, start_date, notes)
               VALUES (?, ?, ?, ?, ?, ?, ?, coalesce(?, date('now')), ?)""",
            (student_id, hourly_rate, classes_per_month, class_length_min,
             _dump(subjects or []), payment_method, payment_schedule, start_date, notes),
        )
        conn.execute("UPDATE Students SET status = 'active', updated_at = datetime('now') WHERE id = ?",
                     (student_id,))
        return int(cur.lastrowid)


def active_enrolment(student_id: int) -> Optional[dict[str, Any]]:
    with connect() as conn:
        return _row_to_dict(conn.execute(
            "SELECT * FROM Enrolments WHERE student_id = ? AND status = 'active' "
            "ORDER BY id DESC LIMIT 1", (student_id,)).fetchone())


def list_enrolments(student_id: int) -> list[dict[str, Any]]:
    with connect() as conn:
        return [_row_to_dict(r) for r in conn.execute(
            "SELECT * FROM Enrolments WHERE student_id = ? ORDER BY id DESC", (student_id,))]


def update_enrolment(enrolment_id: int, **fields: Any) -> None:
    fields = {k: (_dump(v) if k == "subjects_json" else v)
              for k, v in fields.items() if k in _ENROL_FIELDS}
    if not fields:
        return
    sets = ", ".join(f"{k} = ?" for k in fields)
    with connect() as conn:
        conn.execute(f"UPDATE Enrolments SET {sets} WHERE id = ?", (*fields.values(), enrolment_id))


def monthly_value(enrolment: dict[str, Any]) -> float:
    """£/month for an enrolment."""
    if not enrolment:
        return 0.0
    hrs = (enrolment["classes_per_month"] * enrolment["class_length_min"]) / 60.0
    return round(hrs * enrolment["hourly_rate"], 2)


def per_class_price(enrolment: dict[str, Any] | None) -> float:
    """£ for one class on this plan - the price of an extra class beyond the monthly allowance."""
    if not enrolment:
        return 0.0
    return round(enrolment["hourly_rate"] * enrolment["class_length_min"] / 60.0, 2)


def monthly_recurring_revenue() -> float:
    with connect() as conn:
        rows = [_row_to_dict(r) for r in conn.execute(
            "SELECT * FROM Enrolments WHERE status = 'active'")]
    return round(sum(monthly_value(e) for e in rows), 2)


# --------------------------------------------------------------------------- #
# Invoices  (quotes)
# --------------------------------------------------------------------------- #
def next_invoice_number() -> str:
    import datetime as _dt
    yr = _dt.date.today().year
    with connect() as conn:
        n = conn.execute("SELECT count(*) FROM Invoices WHERE number LIKE ?",
                         (f"INV-{yr}-%",)).fetchone()[0]
    return f"INV-{yr}-{n + 1:03d}"


def create_invoice(student_id: int, *, enrolment_id: int | None, period: str,
                   line_items: list[dict], due_on: str | None = None,
                   notes: str = "", status: str = "draft") -> int:
    subtotal = round(sum(float(li.get("amount", 0)) for li in line_items), 2)
    with connect() as conn:
        cur = conn.execute(
            """INSERT INTO Invoices
               (student_id, enrolment_id, number, period, due_on, line_items_json,
                subtotal, total, status, notes)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (student_id, enrolment_id, next_invoice_number(), period, due_on,
             _dump(line_items), subtotal, subtotal, status, notes),
        )
        return int(cur.lastrowid)


def get_invoice(invoice_id: int) -> Optional[dict[str, Any]]:
    with connect() as conn:
        return _row_to_dict(conn.execute(
            "SELECT * FROM Invoices WHERE id = ?", (invoice_id,)).fetchone())


def list_invoices(student_id: int) -> list[dict[str, Any]]:
    with connect() as conn:
        return [_row_to_dict(r) for r in conn.execute(
            "SELECT * FROM Invoices WHERE student_id = ? ORDER BY id DESC", (student_id,))]


def all_invoices(status: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
    q = "SELECT i.*, s.full_name AS student_name FROM Invoices i JOIN Students s ON s.id = i.student_id"
    params: list[Any] = []
    if status:
        q += " WHERE i.status = ?"
        params.append(status)
    q += " ORDER BY i.id DESC LIMIT ?"
    params.append(limit)
    with connect() as conn:
        return [_row_to_dict(r) for r in conn.execute(q, tuple(params))]


def set_invoice(invoice_id: int, **fields: Any) -> None:
    allowed = {"status", "due_on", "pdf_path", "period", "notes", "line_items_json",
               "subtotal", "total"}
    fields = {k: v for k, v in fields.items() if k in allowed}
    if not fields:
        return
    sets = ", ".join(f"{k} = ?" for k in fields)
    with connect() as conn:
        conn.execute(f"UPDATE Invoices SET {sets} WHERE id = ?", (*fields.values(), invoice_id))


# --------------------------------------------------------------------------- #
# Reports  (progress reports for parents)
# --------------------------------------------------------------------------- #
def create_report(student_id: int, *, period: str, content_md: str,
                  summary: str = "", status: str = "draft") -> int:
    with connect() as conn:
        cur = conn.execute(
            """INSERT INTO Reports (student_id, period, content_md, summary, status)
               VALUES (?, ?, ?, ?, ?)""",
            (student_id, period, content_md, summary, status),
        )
        return int(cur.lastrowid)


def get_report(report_id: int) -> Optional[dict[str, Any]]:
    with connect() as conn:
        return _row_to_dict(conn.execute(
            "SELECT * FROM Reports WHERE id = ?", (report_id,)).fetchone())


def list_reports(student_id: int) -> list[dict[str, Any]]:
    with connect() as conn:
        return [_row_to_dict(r) for r in conn.execute(
            "SELECT * FROM Reports WHERE student_id = ? ORDER BY id DESC", (student_id,))]


def set_report(report_id: int, **fields: Any) -> None:
    allowed = {"content_md", "summary", "period", "status", "pdf_path"}
    fields = {k: v for k, v in fields.items() if k in allowed}
    if not fields:
        return
    sets = ", ".join(f"{k} = ?" for k in fields)
    with connect() as conn:
        conn.execute(f"UPDATE Reports SET {sets} WHERE id = ?", (*fields.values(), report_id))


# --------------------------------------------------------------------------- #
# Users / auth
# --------------------------------------------------------------------------- #
def _hash(pw: str) -> str:
    from werkzeug.security import generate_password_hash
    return generate_password_hash(pw)


def create_user(username: str, password: str, *, role: str = "parent",
                student_id: int | None = None, display_name: str | None = None,
                must_change_pw: bool = False) -> int:
    with connect() as conn:
        cur = conn.execute(
            """INSERT INTO Users (username, password_hash, role, student_id, display_name, must_change_pw)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (username.strip().lower(), _hash(password), role, student_id, display_name,
             1 if must_change_pw else 0),
        )
        return int(cur.lastrowid)


def get_user(user_id: int) -> Optional[dict[str, Any]]:
    with connect() as conn:
        return _row_to_dict(conn.execute("SELECT * FROM Users WHERE id = ?", (user_id,)).fetchone())


def get_user_by_name(username: str) -> Optional[dict[str, Any]]:
    with connect() as conn:
        return _row_to_dict(conn.execute(
            "SELECT * FROM Users WHERE username = ?", (username.strip().lower(),)).fetchone())


def user_for_student(student_id: int) -> Optional[dict[str, Any]]:
    with connect() as conn:
        return _row_to_dict(conn.execute(
            "SELECT * FROM Users WHERE student_id = ? AND role = 'parent' ORDER BY id LIMIT 1",
            (student_id,)).fetchone())


def check_password(user: dict[str, Any], password: str) -> bool:
    from werkzeug.security import check_password_hash
    return bool(user) and check_password_hash(user["password_hash"], password)


def set_password(user_id: int, password: str, *, must_change: bool = False) -> None:
    with connect() as conn:
        conn.execute("UPDATE Users SET password_hash = ?, must_change_pw = ? WHERE id = ?",
                     (_hash(password), 1 if must_change else 0, user_id))


def touch_login(user_id: int) -> None:
    with connect() as conn:
        conn.execute("UPDATE Users SET last_login = datetime('now') WHERE id = ?", (user_id,))


def ensure_admin(username: str, password: str) -> tuple[int, bool]:
    """Create the admin account if there is no admin yet. Returns (id, created)."""
    with connect() as conn:
        row = conn.execute("SELECT * FROM Users WHERE role = 'admin' LIMIT 1").fetchone()
    if row:
        return row["id"], False
    return create_user(username, password, role="admin", display_name="Tutor"), True


def make_parent_login(student_id: int, *, username: str | None = None) -> dict[str, str]:
    """Create (or reset) the parent login for a pupil. Returns {username, password}."""
    import secrets
    student = get_student(student_id)
    base = (username or (student["full_name"].split(" ")[0]
            + (student["full_name"].split(" ")[-1][0] if " " in student["full_name"] else ""))).lower()
    base = "".join(c for c in base if c.isalnum()) or f"pupil{student_id}"
    pw = secrets.token_urlsafe(6)
    existing = user_for_student(student_id)
    if existing:
        set_password(existing["id"], pw, must_change=True)
        return {"username": existing["username"], "password": pw}
    uname = base
    i = 1
    while get_user_by_name(uname):
        i += 1
        uname = f"{base}{i}"
    create_user(uname, pw, role="parent", student_id=student_id,
                display_name=student["guardian_name"] or "Parent", must_change_pw=True)
    return {"username": uname, "password": pw}


# --------------------------------------------------------------------------- #
# Availability + Sessions (calendar / booking)
# --------------------------------------------------------------------------- #
def list_availability() -> list[dict[str, Any]]:
    with connect() as conn:
        return [_row_to_dict(r) for r in conn.execute(
            "SELECT * FROM Availability WHERE active = 1 ORDER BY weekday, start_time")]


def set_availability(rules: list[dict]) -> None:
    """Replace the weekly availability with `rules` = [{weekday,start_time,end_time,slot_min}]."""
    with connect() as conn:
        conn.execute("DELETE FROM Availability")
        for r in rules:
            conn.execute(
                "INSERT INTO Availability (weekday, start_time, end_time, slot_min) VALUES (?, ?, ?, ?)",
                (int(r["weekday"]), r["start_time"], r["end_time"], int(r.get("slot_min", 60))))


def create_session(student_id: int, *, starts_at: str, duration_min: int = 60,
                   subject: str | None = None, booked_by: str = "admin",
                   billable: bool = False, charge_amount: float = 0.0,
                   notes: str = "") -> int:
    with connect() as conn:
        cur = conn.execute(
            """INSERT INTO Sessions
               (student_id, starts_at, duration_min, subject, booked_by, billable, charge_amount, notes)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (student_id, starts_at, duration_min, subject, booked_by,
             1 if billable else 0, round(float(charge_amount or 0), 2), notes))
        return int(cur.lastrowid)


def get_session(session_id: int) -> Optional[dict[str, Any]]:
    with connect() as conn:
        return _row_to_dict(conn.execute(
            "SELECT * FROM Sessions WHERE id = ?", (session_id,)).fetchone())


def list_sessions(student_id: int, *, upcoming: bool | None = None) -> list[dict[str, Any]]:
    q = "SELECT * FROM Sessions WHERE student_id = ? AND status != 'cancelled'"
    if upcoming is True:
        q += " AND starts_at >= datetime('now')"
    elif upcoming is False:
        q += " AND starts_at < datetime('now')"
    q += " ORDER BY starts_at" + (" DESC" if upcoming is False else "")
    with connect() as conn:
        return [_row_to_dict(r) for r in conn.execute(q, (student_id,))]


def sessions_between(start_iso: str, end_iso: str) -> list[dict[str, Any]]:
    with connect() as conn:
        return [_row_to_dict(r) for r in conn.execute(
            """SELECT s.*, st.full_name AS student_name FROM Sessions s
               JOIN Students st ON st.id = s.student_id
               WHERE s.starts_at >= ? AND s.starts_at < ? AND s.status != 'cancelled'
               ORDER BY s.starts_at""", (start_iso, end_iso))]


def update_session(session_id: int, **fields: Any) -> None:
    allowed = {"status", "starts_at", "duration_min", "subject", "notes",
               "arrived_at", "departed_at", "billable", "charge_amount", "invoice_id"}
    fields = {k: v for k, v in fields.items() if k in allowed}
    if not fields:
        return
    sets = ", ".join(f"{k} = ?" for k in fields)
    with connect() as conn:
        conn.execute(f"UPDATE Sessions SET {sets} WHERE id = ?", (*fields.values(), session_id))


def mark_arrived(session_id: int) -> None:
    update_session(session_id, status="arrived", arrived_at=_now())


def mark_departed(session_id: int) -> None:
    update_session(session_id, status="departed", departed_at=_now())


def _now() -> str:
    import datetime as _dt
    return _dt.datetime.now().strftime("%Y-%m-%d %H:%M")


def available_slots(*, weeks_ahead: int = 4, duration_min: int = 60,
                    lead_hours: int = 24) -> list[dict[str, Any]]:
    """Bookable start times over the next few weeks: inside the tutor's weekly
    availability, not clashing with an existing session, at least `lead_hours` away.
    Returns [{iso, label, day}] grouped-friendly."""
    import datetime as _dt

    rules = list_availability()
    if not rules:
        return []
    now = _dt.datetime.now()
    earliest = now + _dt.timedelta(hours=lead_hours)

    # existing bookings as (start, end) datetimes
    horizon_end = now + _dt.timedelta(weeks=weeks_ahead)
    booked = []
    for s in sessions_between(now.strftime("%Y-%m-%d %H:%M"), horizon_end.strftime("%Y-%m-%d %H:%M")):
        st = _dt.datetime.strptime(s["starts_at"][:16], "%Y-%m-%d %H:%M")
        booked.append((st, st + _dt.timedelta(minutes=s["duration_min"])))

    out: list[dict[str, Any]] = []
    for d in range(weeks_ahead * 7):
        day = (now + _dt.timedelta(days=d)).date()
        for r in rules:
            if r["weekday"] != day.weekday():
                continue
            step = r["slot_min"] or 60
            sh, sm = map(int, r["start_time"].split(":"))
            eh, em = map(int, r["end_time"].split(":"))
            cursor = _dt.datetime.combine(day, _dt.time(sh, sm))
            window_end = _dt.datetime.combine(day, _dt.time(eh, em))
            while cursor + _dt.timedelta(minutes=duration_min) <= window_end:
                slot_end = cursor + _dt.timedelta(minutes=duration_min)
                clash = any(cursor < b_end and slot_end > b_start for b_start, b_end in booked)
                if cursor >= earliest and not clash:
                    out.append({
                        "iso": cursor.strftime("%Y-%m-%d %H:%M"),
                        "label": cursor.strftime("%a %d %b · %H:%M"),
                        "day": cursor.strftime("%A %d %B"),
                        "time": cursor.strftime("%H:%M"),
                    })
                cursor += _dt.timedelta(minutes=step)
    return out


def bookings_this_month(student_id: int, *, included_only: bool = False) -> int:
    import datetime as _dt
    first = _dt.date.today().replace(day=1).isoformat()
    q = ("SELECT count(*) FROM Sessions WHERE student_id = ? AND status != 'cancelled' "
         "AND date(starts_at) >= ?")
    if included_only:
        q += " AND billable = 0"
    with connect() as conn:
        return conn.execute(q, (student_id, first)).fetchone()[0]


def would_be_extra(student_id: int, enrolment: dict[str, Any] | None) -> bool:
    """True if the next class booked this month is beyond the plan's monthly allowance."""
    if not enrolment or not enrolment.get("classes_per_month"):
        return False
    return bookings_this_month(student_id, included_only=True) >= enrolment["classes_per_month"]


def unbilled_extras(student_id: int) -> list[dict[str, Any]]:
    """Extra (paid) classes that have been booked but not yet put on an invoice."""
    with connect() as conn:
        return [_row_to_dict(r) for r in conn.execute(
            "SELECT * FROM Sessions WHERE student_id = ? AND billable = 1 AND invoice_id IS NULL "
            "AND status != 'cancelled' ORDER BY starts_at", (student_id,))]


def extras_pending_total(student_id: int) -> float:
    return round(sum(s["charge_amount"] or 0 for s in unbilled_extras(student_id)), 2)


def attach_sessions_to_invoice(session_ids: Iterable[int], invoice_id: int) -> None:
    ids = [int(s) for s in session_ids if s]
    if not ids:
        return
    with connect() as conn:
        conn.executemany("UPDATE Sessions SET invoice_id = ? WHERE id = ? AND billable = 1",
                         [(invoice_id, sid) for sid in ids])


def current_status(student_id: int) -> dict[str, Any]:
    """What's happening for this pupil right now (for the parent dashboard)."""
    import datetime as _dt
    now = _dt.datetime.now()
    with connect() as conn:
        rows = [_row_to_dict(r) for r in conn.execute(
            "SELECT * FROM Sessions WHERE student_id = ? AND status != 'cancelled' "
            "ORDER BY starts_at", (student_id,))]
    for s in rows:
        if s["status"] == "arrived":
            return {"state": "in_session", "session": s}
    today = [s for s in rows if s["starts_at"][:10] == now.strftime("%Y-%m-%d")]
    for s in today:
        if s["status"] == "departed":
            return {"state": "finished_today", "session": s}
    upcoming = [s for s in rows if s["starts_at"] >= now.strftime("%Y-%m-%d %H:%M")
                and s["status"] == "booked"]
    if upcoming:
        return {"state": "upcoming", "session": upcoming[0]}
    return {"state": "none", "session": None}


# --------------------------------------------------------------------------- #
def payment_summary(student_id: int) -> dict[str, Any]:
    inv = list_invoices(student_id)
    paid = round(sum(i["total"] for i in inv if i["status"] == "paid"), 2)
    due = round(sum(i["total"] for i in inv if i["status"] in ("draft", "sent")), 2)
    return {"invoices": inv, "paid": paid, "due": due,
            "extras_pending": extras_pending_total(student_id)}


def dashboard_stats() -> dict[str, Any]:
    with connect() as conn:
        pupils = conn.execute("SELECT count(*) FROM Students").fetchone()[0]
        active = conn.execute("SELECT count(*) FROM Students WHERE status = 'active'").fetchone()[0]
        enquiries = conn.execute("SELECT count(*) FROM Students WHERE status IN ('enquiry','trial')").fetchone()[0]
        books = conn.execute("SELECT count(*) FROM WeeklyBooks").fetchone()[0]
        unpaid = conn.execute("SELECT coalesce(sum(total),0) FROM Invoices WHERE status IN ('draft','sent')").fetchone()[0]
    return {
        "pupils": pupils, "active": active, "enquiries": enquiries,
        "workbooks": books, "mrr": monthly_recurring_revenue(),
        "outstanding": round(unpaid, 2),
    }
