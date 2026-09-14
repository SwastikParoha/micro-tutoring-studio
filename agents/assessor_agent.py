"""The Assessor.

Turns a tutor's notes AND/OR a photo of the pupil's completed work into a
structured JSON profile: ``weak_points``, ``strengths``, a 0-100 score, a
``difficulty_recommendation`` for the next workbook, plus any student metadata.

Two modes:
* diagnostic  - the first demo lesson (notes, maybe a photo)
* weekly      - a photo/score of this week's practice, used to decide whether to
                make next week easier, the same, or harder

Uses Gemini (multimodal) when available; a deterministic keyword heuristic
otherwise, so the CLI still works with no API key (text only).
"""
from __future__ import annotations

import re
from typing import Any, Optional

from agents import llm

SYSTEM = (
    "You are an experienced UK primary-school tutor and diagnostician. "
    "You turn messy notes and photos of a child's work into a precise, structured "
    "assessment aligned to the National Curriculum for England (Key Stages 1-2). "
    "Name the exact skill or misconception, never a vague area. Be honest and "
    "encouraging."
)

_SHAPE = """\
{
  "student": {
    "full_name": string | null,
    "year_group": integer | null,
    "key_stage": 1 | 2 | null,
    "age": integer | null,
    "interests": [string],
    "guardian_name": string | null
  },
  "subject": "maths" | "english" | "science" | "geography" | "history" | string,
  "overall_score": number | null,          // 0-100. From a stated score, or estimate from the work.
  "strengths": [string],                    // concrete things the pupil can already do
  "weak_points": [
    {
      "topic": string,                      // e.g. "comparing unit fractions"
      "detail": string,                     // what exactly goes wrong
      "misconception": string | null,
      "severity": "low" | "medium" | "high"
    }
  ],
  "recommended_focus": [string],            // 1-3 topics to prioritise next
  "difficulty_recommendation": "support" | "core" | "stretch",
  "difficulty_reason": string,              // one sentence: why that level
  "tutor_summary": string                   // 2-3 plain sentences for the tutor
}"""

_NOTES_RULES = (
    "Rules:\n"
    "- Be faithful to the notes; do not invent scores or facts.\n"
    "- Phrase weak_points as teachable skills, not personality traits.\n"
    '- difficulty_recommendation: "support" if they struggled a lot, "stretch" if it '
    'was easy, otherwise "core".'
)

_IMAGE_RULES = (
    "Rules:\n"
    "- overall_score: count the marks if you can; otherwise estimate the percentage correct.\n"
    "- weak_points: be specific about the mistake pattern you can see in the work.\n"
    '- difficulty_recommendation: "support" if <55% or many method errors, "stretch" if '
    '>=85% and clean method, otherwise "core".\n'
    '- Only fill "student" fields you can actually tell from the page; use null otherwise.'
)


def _notes_prompt(notes: str) -> str:
    return (
        "Here are a tutor's notes from a diagnostic lesson with a primary pupil. "
        "Parse them into JSON.\n\nRAW NOTES:\n\"\"\"\n" + notes + "\n\"\"\"\n\n"
        "Return ONLY JSON with this exact shape:\n" + _SHAPE + "\n\n" + _NOTES_RULES
    )


def _image_prompt(ctx: str, extra: str) -> str:
    return (
        "The image(s) show a primary-school pupil's completed worksheet or test" + ctx + ".\n\n"
        "Look carefully at what they got right and wrong, and HOW they went wrong "
        "(method errors, misconceptions, slips). Then fill in this JSON.\n\n"
        "Return ONLY JSON with this exact shape:\n" + _SHAPE + "\n\n" + _IMAGE_RULES + extra
    )


def analyse(raw_notes: Optional[str] = None, *, image: Optional[tuple[bytes, str]] = None,
            kind: str = "diagnostic", subject: Optional[str] = None,
            context: str = "", verbose: bool = False) -> dict[str, Any]:
    """Analyse notes and/or a photo of the pupil's work.

    ``image`` is ``(bytes, mime_type)``. At least one of ``raw_notes`` / ``image``
    is required.
    """
    raw_notes = (raw_notes or "").strip()
    if not raw_notes and not image:
        raise ValueError("Provide assessment notes, a photo of the work, or both.")

    try:
        if image is not None:
            ctx = f" for {subject}" if subject else ""
            extra = f"\n- Tutor's note: {raw_notes}" if raw_notes else ""
            result = llm.generate_json(_image_prompt(ctx, extra), system=SYSTEM, images=[image])
            engine = "gemini-vision"
        else:
            result = llm.generate_json(_notes_prompt(raw_notes), system=SYSTEM)
            engine = "gemini"
        if verbose:
            print(f"[assessor] {engine} analysis complete.")
        return _coerce(result, raw_notes, kind, subject)
    except llm.LLMUnavailable:
        if verbose:
            print("[assessor] No GEMINI_API_KEY - using heuristic parser (text only).")
    except Exception as exc:  # noqa: BLE001
        if verbose:
            print(f"[assessor] Gemini failed ({exc!r}) - using heuristic parser.")

    out = _heuristic(raw_notes or "Pupil's worksheet submitted as a photo.")
    out["kind"] = kind
    if subject:
        out["subject"] = subject
    return out


# --------------------------------------------------------------------------- #
def _coerce(data: dict[str, Any], raw_notes: str, kind: str = "diagnostic",
            subject: str | None = None) -> dict[str, Any]:
    data.setdefault("student", {})
    data.setdefault("subject", subject or "maths")
    data.setdefault("strengths", [])
    data.setdefault("weak_points", [])
    data.setdefault("recommended_focus", [])
    data.setdefault("tutor_summary", "")
    data["kind"] = kind
    reco = str(data.get("difficulty_recommendation", "core")).lower()
    data["difficulty_recommendation"] = reco if reco in ("support", "core", "stretch") else "core"
    data.setdefault("difficulty_reason", "")
    data["raw_notes"] = raw_notes
    norm = []
    for wp in data["weak_points"]:
        if isinstance(wp, str):
            wp = {"topic": wp, "detail": wp, "misconception": None, "severity": "medium"}
        wp.setdefault("severity", "medium")
        wp.setdefault("misconception", None)
        wp.setdefault("detail", wp.get("topic", ""))
        norm.append(wp)
    data["weak_points"] = norm
    st = data["student"]
    if st.get("year_group") and not st.get("key_stage"):
        st["key_stage"] = 1 if int(st["year_group"]) <= 2 else 2
    st.setdefault("interests", [])
    return data


# --------------------------------------------------------------------------- #
_YEAR_RE = re.compile(r"\b(?:year|yr|y)\s*([1-6])\b", re.I)
_AGE_RE = re.compile(r"\b(?:aged?\s*)?(\d{1,2})\s*(?:years?\s*old|yo|y/o)\b|\baged?\s*(\d{1,2})\b", re.I)
_SCORE_RE = re.compile(r"\b(\d{1,3})\s*(?:%|/\s*100|out of 100)\b", re.I)
_FRAC_SCORE_RE = re.compile(r"\b(\d{1,2})\s*/\s*(\d{1,2})\b")

_WEAK_CUES = ["struggle", "struggled", "couldn't", "could not", "can't", "cannot", "unable",
              "confus", "muddl", "guess", "no idea", "didn't know", "did not know", "weak",
              "difficulty", "mistake", "wrong", "incorrect", "misconcept", "gap"]
_STRONG_CUES = ["confident", "fluent", "quick", "easily", "no problem", "strong", "good at",
                "mastered", "secure", "accurate", "knew", "correct", "excellent"]


def _heuristic(raw_notes: str) -> dict[str, Any]:
    text = raw_notes
    lower = text.lower()

    year = int(_YEAR_RE.search(text).group(1)) if _YEAR_RE.search(text) else None
    age = None
    am = _AGE_RE.search(text)
    if am:
        age = int(next(g for g in am.groups() if g))
    if year is None and age is not None:
        year = max(1, min(6, age - 4))

    score = None
    if _SCORE_RE.search(text):
        score = float(_SCORE_RE.search(text).group(1))
    else:
        best = None  # (has_score_context, denominator, value)
        for m in _FRAC_SCORE_RE.finditer(text):
            a, b = map(int, m.groups())
            if not b or a > b:
                continue
            ctx = lower[max(0, m.start() - 40):m.end() + 10]
            has_ctx = any(w in ctx for w in ("score", "scored", "got", "out of", "check",
                                             "test", "assessment", "quiz", "mark"))
            cand = (has_ctx, b, round(100 * a / b, 1))
            if best is None or cand[:2] > best[:2]:
                best = cand
        if best and (best[0] or best[1] >= 8):
            score = best[2]

    maths_hits = sum(lower.count(w) for w in
                     ["math", "fraction", "times table", "number bond", "add", "subtract",
                      "multipl", "divi", "place value", "counter", "digit", "arithmetic"])
    english_hits = sum(lower.count(w) for w in
                       ["writ", "spell", "sentence", "phonic", "grammar", "comprehension",
                        "story", "recount", "punctuation", "vocabulary", "handwriting"])
    english_hits += lower.count("read") - lower.count("read a fraction") - lower.count("reading a fraction")
    subject = "english" if english_hits > maths_hits else "maths"

    sentences = re.split(r"(?<=[.!?\n])\s+", text)
    strengths, weak_points = [], []
    for s in sentences:
        s_clean = s.strip(" -*\t").strip()
        if not s_clean:
            continue
        sl = s_clean.lower()
        if any(c in sl for c in _WEAK_CUES):
            weak_points.append({
                "topic": _short_topic(s_clean),
                "detail": s_clean,
                "misconception": s_clean if "misconcept" in sl or "thinks" in sl else None,
                "severity": "high" if any(w in sl for w in ["no idea", "cannot", "can't", "unable"]) else "medium",
            })
        elif any(c in sl for c in _STRONG_CUES):
            strengths.append(_short_topic(s_clean))

    name = None
    m = re.search(r"(?:pupil|student|child|name|learner)\s*[:\-]\s*"
                  r"([A-Z][a-z]+(?:\s+[A-Z][a-z]+){0,2})", text, re.I)
    if not m:
        # "<Name>, Year 3" or "<Name> (age 8)" or "<Name> is 8"
        m = re.search(r"\b([A-Z][a-z]+(?:\s+[A-Z][a-z]+)?)\b\s*"
                      r"(?=,?\s*(?:[Yy]ear|[Yy]r|\(?[Aa]ged?|is\s+\d|\(\d))", text)
    if not m:
        m = re.search(r"(?:with|for|met|tutored|assessed)\s+([A-Z][a-z]+(?:\s+[A-Z][a-z]+)?)\b", text)
    if not m:
        # first capitalised word/pair on the first non-empty line
        first = next((ln for ln in text.splitlines() if ln.strip()), "")
        m = re.search(r"\b([A-Z][a-z]+(?:\s+[A-Z][a-z]+)?)\b", first)
    if m:
        cand = m.group(1).strip()
        if cand.lower().split()[0] not in {"pupil", "student", "child", "name", "learner", "year"}:
            name = cand

    interests = []
    m = re.search(r"(?:really\s+)?(?:likes?|loves?|into|interested in|hobby|hobbies|enjoys?|keen on)\s+([^.\n]+)", lower)
    if m:
        raw = re.sub(r"\([^)]*\)", "", m.group(1))
        parts = [re.sub(r"^and\s+", "", w.strip()) for w in re.split(r",| and ", raw) if w.strip()]
        interests = [p for p in parts
                     if len(p.split()) <= 3 and not any(c in p.lower() for c in _WEAK_CUES)][:5]

    guardian = None
    m = re.search(r"(?:mum|mother|dad|father|parent|guardian|carer)(?:'s)?\s*(?:name)?\s*(?:is|:)\s*([A-Z][a-z]+)", text, re.I)
    if m:
        guardian = m.group(1)

    return {
        "student": {
            "full_name": name,
            "year_group": year,
            "key_stage": (1 if year and year <= 2 else 2) if year else None,
            "age": age,
            "interests": interests,
            "guardian_name": guardian,
        },
        "subject": subject,
        "overall_score": score,
        "strengths": strengths or ["Engaged well with the tutor and attempted every question"],
        "weak_points": weak_points or [{
            "topic": "needs a fuller diagnostic",
            "detail": "The notes did not surface a specific weak point; run a longer assessment.",
            "misconception": None, "severity": "medium",
        }],
        "recommended_focus": [wp["topic"] for wp in weak_points[:3]] or ["core fluency"],
        "difficulty_recommendation": _difficulty_from_score(score, len(weak_points)),
        "difficulty_reason": "estimated from the notes (no AI available)",
        "tutor_summary": (sentences[0].strip() if sentences else raw_notes[:200]),
        "kind": "diagnostic",
        "raw_notes": raw_notes,
        "_engine": "heuristic",
    }


def _difficulty_from_score(score: float | None, n_weak: int) -> str:
    if score is not None:
        if score >= 85:
            return "stretch"
        if score < 55:
            return "support"
        return "core"
    return "support" if n_weak >= 3 else "core"


def _short_topic(sentence: str) -> str:
    s = re.sub(r"\s+", " ", sentence.strip(" -*\t")).strip()
    # drop leading narration and take the first clause
    s = re.sub(r"^(?:when i showed[^,]*,?\s*)", "", s, flags=re.I)
    s = re.split(r"[:;.\n]| - | -- ", s, maxsplit=1)[0].strip()
    s = re.sub(r"^(they|he|she|the (?:pupil|child|student)|pupil|child|student)\b\s*", "",
               s, flags=re.I)
    words = s.split()
    return " ".join(words[:12]) + ("..." if len(words) > 12 else "")
