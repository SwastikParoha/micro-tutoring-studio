"""Live Oak National Academy Open Curriculum API - depth layer.

The bulk dataset (agents/oak_local.py) gives the curriculum *structure* - units,
lessons, objectives, misconceptions, vocabulary.  The live API adds *depth* that
the file does not carry:

* ``lesson_transcript(slug)`` - the actual words Oak's teacher uses to explain
  the concept in the lesson video.  Fed to the Explainer so the revision notes
  follow how the topic is really taught.
* ``lesson_summary(slug)``   - key learning points, keywords, common mistakes.
* ``subject_questions(ks, subject)`` - Oak's real starter / exit quiz questions
  for the (~6-19) lessons Oak has published questions for.

Everything is cached to ``data/oak_cache/`` because none of it changes; the file
dataset stays the source of truth and this is best-effort enrichment.

Base URL: https://open-api.thenational.academy/api/v0   (Authorization: Bearer <key>)
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Optional

import config

_API = "/api/v0"
_CACHE = config.ROOT / "data" / "oak_cache"
_TIMEOUT = 20


def available() -> bool:
    return bool(config.OAK_API_KEY) and not config.OFFLINE_MODE


# --------------------------------------------------------------------------- #
def _session():
    import requests
    s = requests.Session()
    s.headers.update({
        "Authorization": f"Bearer {config.OAK_API_KEY}",
        "Accept": "application/json",
        "User-Agent": "micro-tutoring/1.0",
    })
    return s


def _get(path: str) -> Optional[Any]:
    """GET {base}{_API}{path} as JSON, or None on any failure."""
    try:
        r = _session().get(config.OAK_API_BASE_URL.rstrip("/") + _API + path, timeout=_TIMEOUT)
        if r.status_code != 200:
            return None
        return r.json()
    except Exception:  # noqa: BLE001
        return None


def _cache_path(name: str) -> Path:
    _CACHE.mkdir(parents=True, exist_ok=True)
    safe = re.sub(r"[^A-Za-z0-9_.-]", "_", name)[:150]
    return _CACHE / safe


def _cached_json(name: str, fetch) -> Optional[Any]:
    p = _cache_path(name + ".json")
    if p.exists():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            pass
    data = fetch()
    if data is not None:
        try:
            p.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        except Exception:  # noqa: BLE001
            pass
    return data


# --------------------------------------------------------------------------- #
def lesson_transcript(slug: str) -> str:
    """The teacher's spoken explanation for a lesson (cached). '' if unavailable."""
    if not slug or not available():
        return ""
    p = _cache_path(f"transcript_{slug}.txt")
    if p.exists():
        return p.read_text(encoding="utf-8")
    data = _get(f"/lessons/{slug}/transcript")
    text = (data or {}).get("transcript", "") if isinstance(data, dict) else ""
    text = _tidy_transcript(text)
    if text:
        try:
            p.write_text(text, encoding="utf-8")
        except Exception:  # noqa: BLE001
            pass
    return text


def _tidy_transcript(text: str) -> str:
    """Drop the greeting/sign-off filler and quiz-time chatter; keep the teaching."""
    if not text:
        return ""
    t = re.sub(r"\s+", " ", text).strip()

    # cut the intro: everything up to the last of these markers in the first 40%
    intro = list(re.finditer(
        r"(?:let'?s (?:get started|make a start|begin)|we can get started|"
        r"ready to (?:start|begin)|let'?s go|here'?s (?:the|our) outcome|"
        r"first (?:learning cycle|part of (?:our|the) lesson))", t, re.I))
    cut = [m.end() for m in intro if m.end() < len(t) * 0.4]
    if cut:
        t = t[max(cut):].strip()

    # stop before the wrap-up / final quiz
    end = re.search(
        r"(?:that'?s the end of (?:the |our )?lesson|well done(?:,| everyone)?[.!]|"
        r"see you (?:again|next time)|thank you for (?:your hard work|learning)|"
        r"you'?ve (?:completed|finished) (?:the|this|today'?s) lesson|"
        r"it'?s time for (?:the |your )?(?:final|exit) (?:quiz|task))", t, re.I)
    if end:
        t = t[:end.start()].strip()

    # strip "pause the video" / "have a go" scaffolding sentences
    t = re.sub(r"[^.!?]*\bpause the video\b[^.!?]*[.!?]", "", t, flags=re.I)
    return re.sub(r"\s{2,}", " ", t).strip()


def lesson_summary(slug: str) -> dict[str, Any]:
    if not slug or not available():
        return {}
    data = _cached_json(f"summary_{slug}", lambda: _get(f"/lessons/{slug}/summary"))
    return data if isinstance(data, dict) else {}


# --------------------------------------------------------------------------- #
_LATEX = re.compile(r"\$\$?(.*?)\$\$?", re.DOTALL)


def _plainify(text: str) -> str:
    """Turn Oak's LaTeX-ish quiz text into something printable for a child."""
    def repl(m: "re.Match[str]") -> str:
        inner = m.group(1)
        inner = re.sub(r"\{?\s*([0-9a-z]+)\s*\}?\s*\\over\s*\{?\s*([0-9a-z]+)\s*\}?",
                       r"\1/\2", inner)
        inner = re.sub(r"\\times", " x ", inner)
        inner = re.sub(r"[{}\\]", "", inner)
        return inner.strip()
    text = _LATEX.sub(repl, text or "")
    text = text.replace("{{ }}", "___").replace("{{}}", "___")
    return re.sub(r"\s+", " ", text).strip()


def subject_questions(key_stage: int, subject: str) -> list[dict[str, Any]]:
    """Oak's real starter/exit quiz questions for the lessons Oak has published.

    Returns a flat list of ``{lesson, question, answer, choices, image}`` with the
    LaTeX cleaned up.  Image-only questions are dropped (can't print them).
    """
    if not available():
        return []
    ks = f"ks{key_stage}"
    subj = _api_subject(subject)
    raw = _cached_json(f"questions_{ks}_{subj}",
                       lambda: _get(f"/key-stages/{ks}/subject/{subj}/questions"))
    if not isinstance(raw, list):
        return []

    out: list[dict[str, Any]] = []
    for lesson in raw:
        title = lesson.get("lessonTitle", "")
        for quiz in ("starterQuiz", "exitQuiz"):
            for q in lesson.get(quiz, []) or []:
                prompt = _plainify(q.get("question", ""))
                if not prompt or "___" in prompt and not q.get("answers"):
                    continue
                answers = q.get("answers", []) or []
                correct = [_plainify(a.get("content", "")) for a in answers
                           if not a.get("distractor") and a.get("type") == "text"]
                choices = [_plainify(a.get("content", "")) for a in answers
                           if a.get("type") == "text"]
                out.append({
                    "lesson": title,
                    "question": prompt,
                    "answer": "; ".join(c for c in correct if c),
                    "choices": [c for c in choices if c],
                    "needs_image": bool(q.get("questionImage")),
                })
    return out


_API_SUBJECT = {
    "maths": "maths", "mathematics": "maths", "english": "english",
    "science": "science", "biology": "science", "chemistry": "science", "physics": "science",
    "geography": "geography", "history": "history", "art": "art", "computing": "computing",
    "music": "music", "pe": "physical-education", "physical education": "physical-education",
    "dt": "design-technology", "design and technology": "design-technology",
    "languages": "french", "french": "french", "german": "german",
}


def _api_subject(subject: str) -> str:
    return _API_SUBJECT.get(subject.strip().lower(), subject.strip().lower())


# --------------------------------------------------------------------------- #
def enrich_packet(packet, *, max_lessons: int = 2, verbose: bool = False):
    """Attach Oak teaching transcripts + real quiz questions to a CurriculumPacket."""
    if not available():
        return packet

    from dataclasses import replace

    teaching = []
    for l in packet.lessons[:max_lessons]:
        tx = lesson_transcript(l.get("slug", ""))
        if tx:
            teaching.append({"title": l.get("title", ""), "transcript": tx[:3500]})
    oak_questions = subject_questions(packet.key_stage, packet.subject)
    # keep only questions whose lesson looks related to this topic, no image needed
    topic_words = set(re.findall(r"[a-z]{4,}", packet.topic.lower()))
    rel = [q for q in oak_questions
           if not q["needs_image"]
           and (topic_words & set(re.findall(r"[a-z]{4,}", q["lesson"].lower()))
                or topic_words & set(re.findall(r"[a-z]{4,}", q["question"].lower())))]

    if verbose:
        print(f"[oak-api] +{len(teaching)} transcript(s), {len(rel)} matching Oak quiz question(s)")
    if not teaching and not rel:
        return packet
    return replace(packet, teaching_notes=teaching, oak_questions=rel[:12])
