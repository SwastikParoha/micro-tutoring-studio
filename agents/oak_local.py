"""Offline curriculum retrieval from the Oak National Academy bulk dataset.

Queries the pre-built SQLite distribution of the Oak Curriculum Ontology
(https://github.com/oaknational/oak-curriculum-ontology/releases) - the same
content the Oak API serves, licensed Open Government Licence v3.0, no API key.

Covers every 2014 National Curriculum subject.  ``main.py setup`` downloads the
DB to ``data/oak-curriculum.sqlite``.

The public surface is ``search_units()`` and ``build_packet()``; the ingestor
uses these as its middle retrieval tier (live API -> this -> bundled mock).
"""
from __future__ import annotations

import re
import sqlite3
from pathlib import Path
from typing import Any, Optional

import config

# Download details (kept here so `main.py setup` and the docs agree)
RELEASE_TAG = "v0.1.3"
DOWNLOAD_URL = (
    f"https://github.com/oaknational/oak-curriculum-ontology/releases/download/"
    f"{RELEASE_TAG}/oak-curriculum.sqlite"
)

# Friendly subject word -> Oak subject name(s)
SUBJECT_ALIASES: dict[str, list[str]] = {
    "maths": ["Mathematics"], "math": ["Mathematics"], "mathematics": ["Mathematics"],
    "numeracy": ["Mathematics"], "arithmetic": ["Mathematics"],
    "english": ["English"], "literacy": ["English"], "reading": ["English"],
    "writing": ["English"], "grammar": ["English"], "phonics": ["English"], "spelling": ["English"],
    "science": ["Biology", "Chemistry", "Physics"],
    "biology": ["Biology"], "chemistry": ["Chemistry"], "physics": ["Physics"],
    "geography": ["Geography"],
    "history": ["History"],
    "art": ["Art and design"], "art and design": ["Art and design"],
    "computing": ["Computing"], "ict": ["Computing"], "computer science": ["Computing"],
    "dt": ["Design and technology"], "design and technology": ["Design and technology"],
    "music": ["Music"], "pe": ["Physical education"], "physical education": ["Physical education"],
    "languages": ["Languages"], "french": ["Languages"], "spanish": ["Languages"], "german": ["Languages"],
    "food": ["Food and nutrition"], "cooking": ["Food and nutrition"],
    "citizenship": ["Citizenship"], "pshe": ["Citizenship"],
}

# What the tutor sees when browsing (`main.py topics`)
PRIMARY_SUBJECTS = ["maths", "english", "science", "geography", "history",
                    "art", "computing", "music", "pe", "design and technology", "languages"]


# --------------------------------------------------------------------------- #
def db_path() -> Path:
    return Path(config.OAK_LOCAL_DB_PATH)


def available() -> bool:
    p = db_path()
    return p.is_file() and p.stat().st_size > 1_000_000


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{db_path()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _clean(text: Any) -> str:
    """Collapse whitespace, repair mojibake, and unwrap the dataset's markup tokens."""
    s = str(text or "")
    s = (s.replace("�", "'")           # lost smart quotes
           .replace("’", "'").replace("‘", "'")
           .replace("“", '"').replace("”", '"')
           .replace("–", "-").replace("—", "-"))
    # Oak stores fractions/markup as tokens like "[Fraction:3/4]" or "[Math:x^2]"
    s = re.sub(r"\[(?:Fraction|Math|Maths|Formula|Latex)\s*:\s*([^\]]+)\]", r"\1", s)
    s = re.sub(r"\[/?\w+\]", "", s)      # any other stray [tag]
    return re.sub(r"\s+", " ", s).strip()


def resolve_subjects(subject: str) -> list[str]:
    key = subject.strip().lower()
    if key in SUBJECT_ALIASES:
        return SUBJECT_ALIASES[key]
    # exact (case-insensitive) match against the real subject names
    with _connect() as c:
        for (name,) in c.execute("SELECT name FROM subject"):
            if name.lower() == key:
                return [name]
    return [subject.strip().title()]


_STOP = {"the", "a", "an", "of", "and", "or", "to", "in", "on", "for", "with", "how",
         "what", "why", "is", "are", "this", "that", "their", "its", "from", "by",
         "study", "understand", "knowledge", "including", "pupils", "children"}


def _content_words(s: str) -> set[str]:
    return {w for w in re.findall(r"[a-z]+", s.lower()) if w not in _STOP and len(w) > 2}


def _similar(a: str, b: str) -> float:
    from difflib import SequenceMatcher
    a, b = a.lower().strip(), b.lower().strip()
    if not a or not b:
        return 0.0
    if a in b or b in a:
        return 0.92
    aw, bw = _content_words(a), _content_words(b)
    # word-stem overlap so "vikings" ~ "viking"
    overlap = 0.0
    if aw:
        hit = sum(1 for x in aw if any(x[:5] == y[:5] for y in bw))
        overlap = hit / len(aw)
    return max(SequenceMatcher(None, a, b).ratio(), overlap)


# --------------------------------------------------------------------------- #
def list_subjects() -> list[str]:
    with _connect() as c:
        return [r[0] for r in c.execute("SELECT name FROM subject ORDER BY name")]


def list_units(subject: str, key_stage: int, *, search: str | None = None,
               limit: int = 60) -> list[dict[str, Any]]:
    """Browse the units available for a subject + key stage."""
    subjects = resolve_subjects(subject)
    ph = ",".join("?" * len(subjects))
    with _connect() as c:
        rows = c.execute(
            f"""SELECT u.slug, u.name, u.description, sub.name AS subject
                FROM unit u
                JOIN scheme s   ON s.id = u.scheme_id
                JOIN subject sub ON sub.id = s.subject_id
                WHERE sub.name IN ({ph}) AND s.key_stage_id = ?
                ORDER BY sub.name, u.name""",
            (*subjects, key_stage),
        ).fetchall()
    out = [{"slug": r["slug"], "title": _clean(r["name"]),
            "description": _clean(r["description"]), "subject": r["subject"]} for r in rows]
    if search:
        s = search.lower()
        out = [u for u in out if s in u["title"].lower() or s in u["description"].lower()
               or s in u["slug"].replace("-", " ")]
    return out[:limit]


def search_units(subject: str, key_stage: int, topic: str,
                 *, year_group: int | None = None) -> list[dict[str, Any]]:
    """Rank units by how well they match the requested topic."""
    subjects = resolve_subjects(subject)
    ph = ",".join("?" * len(subjects))
    with _connect() as c:
        rows = c.execute(
            f"""SELECT u.id, u.slug, u.name, u.description, u.why_this_why_now,
                       u.scheme_id, sub.name AS subject
                FROM unit u
                JOIN scheme s    ON s.id = u.scheme_id
                JOIN subject sub ON sub.id = s.subject_id
                WHERE sub.name IN ({ph}) AND s.key_stage_id = ?""",
            (*subjects, key_stage),
        ).fetchall()

        year_unit_ids: set[int] = set()
        if year_group:
            year_unit_ids = {
                r[0] for r in c.execute(
                    f"""SELECT DISTINCT uv.unit_id
                        FROM programme pr
                        JOIN scheme s   ON s.id = pr.scheme_id
                        JOIN subject sb ON sb.id = s.subject_id
                        JOIN year_group yg ON yg.id = pr.year_group_id
                        JOIN unit_variant_inclusion uvi ON uvi.programme_id = pr.id
                        JOIN unit_variant uv ON uv.id = uvi.unit_variant_id
                        WHERE sb.name IN ({ph}) AND yg.name LIKE ?""",
                    (*subjects, f"%{year_group}%"),
                )
            }

    scored = []
    for r in rows:
        score = max(
            _similar(topic, _clean(r["name"])),
            _similar(topic, r["slug"].replace("-", " ")),
            0.6 * _similar(topic, _clean(r["description"])),
        )
        if year_unit_ids and r["id"] in year_unit_ids:
            score += 0.25  # prefer units actually taught in the pupil's year
        scored.append((score, dict(r)))
    scored.sort(key=lambda t: t[0], reverse=True)
    return [u for score, u in scored if score >= 0.2][:8] or [dict(r) for r in rows[:3]]


# --------------------------------------------------------------------------- #
def _lessons_for_unit(c: sqlite3.Connection, unit_id: int) -> list[dict[str, Any]]:
    rows = c.execute(
        """SELECT l.id, l.slug, l.name, MIN(li.sequence_position) AS pos
           FROM unit_variant uv
           JOIN lesson_inclusion li ON li.unit_variant_id = uv.id
           JOIN lesson l            ON l.id = li.lesson_id
           WHERE uv.unit_id = ?
           GROUP BY l.id
           ORDER BY pos""",
        (unit_id,),
    ).fetchall()
    return [{"id": r["id"], "slug": r["slug"], "title": _clean(r["name"])} for r in rows]


def _nc_refs(c: sqlite3.Connection, scheme_id: int, topic: str,
            year_group: int | None = None, limit: int = 8) -> list[str]:
    q = ("""SELECT DISTINCT cd.name, ss.name AS substrand
            FROM progression p
            JOIN progression_content_descriptor pcd ON pcd.progression_id = p.id
            JOIN content_descriptor cd ON cd.id = pcd.content_descriptor_id
            LEFT JOIN sub_strand ss ON ss.id = cd.sub_strand_id
            LEFT JOIN year_group yg ON yg.id = p.year_group_id
            WHERE p.scheme_id = ?""")
    params: list[Any] = [scheme_id]
    rows = []
    if year_group:
        rows = c.execute(q + " AND yg.name LIKE ?", (*params, f"%{year_group}%")).fetchall()
    if not rows:
        rows = c.execute(q, params).fetchall()
    if not rows:
        return []
    scored = sorted(
        ((_similar(topic, _clean(r["name"])) + 0.5 * _similar(topic, _clean(r["substrand"])), r)
         for r in rows),
        key=lambda t: t[0], reverse=True,
    )
    strong = [_clean(r["name"]) for score, r in scored if score > 0.4]
    if strong:
        return strong[:limit]
    picked = [_clean(r["name"]) for score, r in scored if score > 0.15][:limit]
    return picked or [_clean(r["name"]) for r in rows[:limit]]


def _synth_baseline(objectives: list[str], topic: str) -> list[str]:
    from agents.textutil import plain
    out = []
    for o in objectives[:7]:
        q = plain(o)
        if q:
            out.append(f"Show me you can {q[:1].lower() + q[1:]}.")
    return out or [f"Tell me what you know about {topic}."]


def build_packet(subject: str, key_stage: int, topic: str,
                 *, year_group: int | None = None, verbose: bool = False):
    """Return a CurriculumPacket for the best-matching Oak unit, or None."""
    from agents.curriculum_ingestor import CurriculumPacket  # lazy: avoid import cycle

    matches = search_units(subject, key_stage, topic, year_group=year_group)
    if not matches:
        return None
    unit = matches[0]

    with _connect() as c:
        lessons = _lessons_for_unit(c, unit["id"])
        lesson_ids = [l["id"] for l in lessons]
        lph = ",".join("?" * len(lesson_ids)) or "NULL"

        outcomes = [_clean(r[0]) for r in c.execute(
            f"SELECT name FROM pupil_lesson_outcome WHERE lesson_id IN ({lph})", lesson_ids)]
        klps = [_clean(r[0]) for r in c.execute(
            f"SELECT name FROM key_learning_point WHERE lesson_id IN ({lph})", lesson_ids)]

        misc_rows = c.execute(
            f"""SELECT DISTINCT m.statement, m.correction
                FROM lesson_misconception lm
                JOIN misconception m ON m.id = lm.misconception_id
                WHERE lm.lesson_id IN ({lph})""", lesson_ids).fetchall()

        vocab_rows = c.execute(
            f"""SELECT DISTINCT k.name, k.description FROM lesson_keyword lk
                JOIN keyword k ON k.id = lk.keyword_id
                WHERE lk.lesson_id IN ({lph}) ORDER BY k.name""", lesson_ids).fetchall()

        from agents.textutil import depersonalise
        prereqs = [depersonalise(_clean(r[0])) for r in c.execute(
            "SELECT name FROM prior_knowledge_requirement WHERE unit_id = ?", (unit["id"],))]

        nc_refs = _nc_refs(c, unit["scheme_id"], topic, year_group)

    # Prefer the pupil-facing "I can ..." outcomes; top up with key learning
    # points only if the unit is thin on outcomes. One Oak unit ~= a half-term,
    # so we scope to a focused set - the Curriculum Mapper picks 2-4 from these.
    objectives = _dedupe(outcomes)[:10]
    if len(objectives) < 4:
        objectives = _dedupe(objectives + klps)[:8]

    seen_mc, misconceptions = set(), []
    for r in misc_rows:
        stmt = _clean(r["statement"])
        if stmt and stmt.lower() not in seen_mc:
            seen_mc.add(stmt.lower())
            misconceptions.append({"misconception": stmt, "why": "",
                                   "response": _clean(r["correction"])})
    misconceptions = misconceptions[:8]

    seen_v, vocab, vocab_defs = set(), [], []
    for r in vocab_rows:
        term = _clean(r["name"])
        if term and term.lower() not in seen_v:
            seen_v.add(term.lower())
            vocab.append(term)
            vocab_defs.append({"term": term, "definition": _clean(r["description"])})
    vocab, vocab_defs = vocab[:14], vocab_defs[:14]

    packet = CurriculumPacket(
        topic=topic,
        subject=subject.lower(),
        key_stage=key_stage,
        year_group=year_group,
        unit_title=_clean(unit["name"]),
        learning_objectives=objectives,
        misconceptions=misconceptions,
        key_vocabulary=vocab,
        key_vocabulary_defs=vocab_defs,
        national_curriculum_refs=nc_refs,
        lessons=lessons,
        baseline_questions=_synth_baseline(objectives, topic),
        prerequisites=_dedupe(prereqs),
        source="oak-local",
    )
    if verbose:
        print(f"[oak-local] {packet.unit_title}  ({unit['subject']}, KS{key_stage})  "
              f"{len(objectives)} objectives, {len(misconceptions)} misconceptions, "
              f"{len(lessons)} lessons")
    return packet


def _dedupe(items: list[str]) -> list[str]:
    seen, out = set(), []
    for it in items:
        it = (it or "").strip()
        k = it.lower()
        if it and k not in seen:
            seen.add(k)
            out.append(it)
    return out


if __name__ == "__main__":
    import sys
    args = sys.argv[1:]
    if not available():
        print(f"Oak DB not found at {db_path()}. Run:  python main.py setup")
        raise SystemExit(1)
    subj = args[0] if args else "science"
    ks = int(args[1]) if len(args) > 1 else 2
    topic = args[2] if len(args) > 2 else "rivers"
    p = build_packet(subj, ks, topic, verbose=True)
    if p:
        print("\n" + p.summary_for_prompt())
