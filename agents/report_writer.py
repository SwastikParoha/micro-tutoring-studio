"""Progress report writer.

Drafts a warm, specific progress report for a pupil's parents from what the CRM
knows: the diagnostic, weekly results (scores over time), weak points that have
improved or still need work, the workbooks covered, and the current level.

Gemini writes it when available; a plain template otherwise.  The tutor edits the
draft before sending.
"""
from __future__ import annotations

from typing import Any

from agents import llm

SYSTEM = (
    "You are an experienced UK primary tutor writing a progress report for a pupil's "
    "parents. Warm, honest, specific and encouraging. Name concrete things the child "
    "can now do and concrete next steps - never vague praise. Plain English a parent "
    "understands, British spelling. No jargon like 'objective' or 'misconception'. "
    "Do not invent facts - use only what you are given."
)

_PROMPT = """\
Write a progress report for {name}'s parents, covering {period}.

PUPIL
Name: {name}   Year group: {year}   School: {school}
What the family wants: {goals}
Interests: {interests}
Current working level: {level}

STARTING POINT (first assessment)
{diagnostic}

RESULTS SINCE THEN (most recent first)
{results}

WHAT WE HAVE WORKED ON (weekly workbooks)
{topics}

STILL TO WORK ON
{weak_points}

=== WRITE THE REPORT AS MARKDOWN, THIS SHAPE ===

## Summary
(2-3 sentences: how {name} is getting on overall, and the direction of travel.)

## What {name} can do well now
(3-5 bullet points, concrete.)

## What we are working on next
(2-4 bullet points, concrete, with how the parent can help at home.)

## How the sessions are going
(1 short paragraph: attitude, effort, engagement.)

## Recommendation
(1-2 sentences: keep the current plan / adjust / add a subject - your honest view.)

Rules:
- 250-380 words. British spelling. Warm but truthful.
- Refer to the child by first name.
- If there is little data yet, say so plainly rather than padding.
- End with a friendly sign-off line inviting the parent to get in touch.
"""


def _fmt_results(assessments: list[dict]) -> str:
    lines = []
    for a in assessments:
        if a.get("kind") == "weekly" or a.get("overall_score") is not None:
            sc = a.get("overall_score")
            lines.append(f"- {a.get('assessed_on', '')}: {a.get('subject', '')}"
                         + (f" - {sc:.0f}%" if sc is not None else "")
                         + (f" ({a.get('kind')})" if a.get("kind") else ""))
    return "\n".join(lines) or "- (no marked results logged yet)"


def _fmt_weak(assessments: list[dict]) -> str:
    seen, out = set(), []
    for a in assessments:
        for w in (a.get("analysis_json") or {}).get("weak_points", []) or a.get("weak_points_json") or []:
            t = w.get("topic") if isinstance(w, dict) else str(w)
            if t and t.lower() not in seen:
                seen.add(t.lower())
                out.append(f"- {t}")
    return "\n".join(out[:6]) or "- (nothing specific flagged)"


def draft_report(student: dict[str, Any], assessments: list[dict], books: list[dict],
                 *, period: str, level: str = "core", verbose: bool = False) -> tuple[str, str]:
    """Return (markdown_body, one_line_summary)."""
    name = (student.get("full_name") or "the pupil").split(" ")[0]
    diag = next((a for a in reversed(assessments) if a.get("kind") == "diagnostic"), None)
    diag_txt = "- (no diagnostic on file)"
    if diag:
        an = diag.get("analysis_json") or {}
        diag_txt = (f"- {an.get('tutor_summary', '')}\n"
                    f"- score: {diag.get('overall_score', 'n/a')}\n"
                    f"- strengths: {', '.join(an.get('strengths', [])[:3])}")
    topics = "\n".join(f"- {b['subject']}: {b['topic']}" for b in books[:12]) or "- (no workbooks yet)"

    prompt = _PROMPT.format(
        name=name, period=period,
        year=student.get("year_group") or "?", school=student.get("school") or "-",
        goals=student.get("goals") or "not recorded",
        interests=student.get("interests") or "not recorded",
        level={"support": "extra support", "core": "on track for their year", "stretch": "working above"}
              .get(level, level),
        diagnostic=diag_txt,
        results=_fmt_results(assessments),
        topics=topics,
        weak_points=_fmt_weak(assessments),
    )

    try:
        md = llm.generate(prompt, system=SYSTEM, temperature=0.5).strip()
        if verbose:
            print("[report] Gemini draft complete.")
    except Exception as exc:  # noqa: BLE001
        if verbose:
            print(f"[report] Gemini unavailable ({exc!r}) - using template.")
        md = _fallback(name, period, student, assessments, books, level)

    # one-line summary = first sentence of the Summary section
    summary = ""
    for line in md.splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            summary = line.split(". ")[0].rstrip(".") + "."
            break
    return md, summary[:200]


def _fallback(name, period, student, assessments, books, level) -> str:
    scores = [a["overall_score"] for a in reversed(assessments)
              if a.get("overall_score") is not None]
    trend = ""
    if len(scores) >= 2:
        trend = (" and scores are improving" if scores[-1] > scores[0]
                 else " and scores are holding steady" if scores[-1] == scores[0]
                 else "")
    return f"""## Summary
This report covers {period}. {name} has completed {len(books)} weekly workbook(s) with us{trend}.
{name} is currently working at a level best described as {level}.

## What {name} can do well now
{_fmt_weak_inverse(assessments)}

## What we are working on next
{_fmt_weak(assessments)}

At home, going over the weekly revision notes together before each session helps a lot.

## How the sessions are going
{name} engages well and completes the set work. Full session notes are on file.

## Recommendation
Continue with the current plan for now. We will review again next term.

Thank you for your support - please get in touch any time with questions.
"""


def _fmt_weak_inverse(assessments: list[dict]) -> str:
    for a in reversed(assessments):
        s = (a.get("analysis_json") or {}).get("strengths") or a.get("strengths_json") or []
        if s:
            return "\n".join(f"- {x}" for x in s[:5])
    return "- Settles quickly and attempts every question."
