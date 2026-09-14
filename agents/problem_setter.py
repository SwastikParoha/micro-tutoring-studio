"""The Problem Setter.

Generates 5-10 practice questions:
  * ~70% aligned to the UK baseline fetched from Oak,
  * ~30% targeting this pupil's specific weak points.
Each question carries an answer and a one-line mark-scheme note for the tutor.
"""
from __future__ import annotations

import math
import random
from typing import Any

from agents import llm
from agents.curriculum_ingestor import CurriculumPacket

SYSTEM = (
    "You write practice questions for a 7-9 year old to do at home ALONE. "
    "Each question must be:\n"
    "- Short. One thing to do. Reading age 7 - words a Year 3 child knows.\n"
    "- No teacher words ('objective', 'misconception', 'represent', 'evaluate', 'context', "
    "'chronological'). No question longer than about 20 words.\n"
    "- Clear enough that the child knows exactly what to write or draw.\n"
    "- Fractions as plain text (1/4), never LaTeX or $ signs.\n"
    "Baseline questions stay inside the given learning goals. Targeted questions gently test "
    "the thing the pupil gets muddled with. Output JSON only."
)

_DIFFICULTY = {
    "support": "EASIER than the year-group norm. Tiny numbers or one clear fact. "
               "One step only. Give a worked-style hint.",
    "core": "At the normal standard for this year group.",
    "stretch": "A bit HARDER. Bigger numbers, two steps, or 'explain why'. Short hints only.",
}

_PROMPT = """\
Write {total} practice questions for this week's booklet.

TOPIC - every question must be about THIS: {topic}   (Year {year})
WHAT THEY LIKE (use if it fits, don't force it): {interests}
DIFFICULTY: {difficulty_note}

MOST QUESTIONS ({n_baseline}) - normal revision of this week's work:
{baseline_focus}
Example style from the curriculum (match the style, do NOT copy word for word):
{baseline_examples}
{oak_examples}

A FEW QUESTIONS ({n_targeted}) - gently practise the bit they get muddled with:
{targeted_focus}

Return ONLY JSON:
{{
  "questions": [
    {{
      "n": integer,
      "type": "baseline" | "targeted",
      "prompt": string,            // what the CHILD reads - short, plain, ONE instruction
      "answer": string,            // the correct answer (for the answers page at the back)
      "hint": string,              // one short, kind nudge - never the answer
      "space": "small" | "medium" | "large"  // how much working room to leave
    }}
  ]
}}

Rules:
- STAY ON THE TOPIC "{topic}". If the curriculum list mentions other things, ignore them.
- Simple words. Each prompt under 18 words. British spelling. Fractions as text (1/4).
- Friendly numbers that fit the topic and the difficulty.
- 'space': "small" = one number/word answer, "medium" = short working, "large" = a drawing.
- Order: mostly the normal questions first, then the muddle ones. Exactly {total} questions.
"""


def set_problems(packet: CurriculumPacket, strategy: dict[str, Any], student: dict[str, Any],
                 *, total: int = 8, difficulty: str = "core",
                 verbose: bool = False) -> dict[str, Any]:
    total = max(5, min(10, total))
    n_targeted = max(1, round(total * 0.3))
    n_baseline = total - n_targeted

    brief = strategy.get("problem_setter_brief", {})
    baseline_focus = brief.get("baseline_focus") or packet.learning_objectives[:3] or [packet.topic]
    targeted_focus = brief.get("targeted_focus") or _weak_topics(strategy) or [packet.topic]

    oak_examples = ""
    if packet.oak_questions:
        lines = []
        for q in packet.oak_questions[:8]:
            a = f"  (answer: {q['answer']})" if q.get("answer") else ""
            lines.append(f"  - {q['question']}{a}")
        oak_examples = ("Oak's REAL quiz questions on this topic (Year-group standard - "
                        "write NEW questions at this exact level and style):\n" + "\n".join(lines))

    prompt = _PROMPT.format(
        total=total, n_baseline=n_baseline, n_targeted=n_targeted,
        topic=packet.topic,
        year=packet.year_group or student.get("year_group") or "3",
        interests=student.get("interests") or "n/a",
        difficulty_note=_DIFFICULTY.get(difficulty, _DIFFICULTY["core"]),
        baseline_focus="\n".join(f"  - {b}" for b in baseline_focus),
        baseline_examples="\n".join(f"  - {q}" for q in (packet.baseline_questions[:6] or ["(none)"])),
        oak_examples=oak_examples,
        targeted_focus="\n".join(f"  - {t}" for t in targeted_focus),
    )

    try:
        result = llm.generate_json(prompt, system=SYSTEM, temperature=0.6)
        qs = result.get("questions", result if isinstance(result, list) else [])
        qs = _coerce(qs, total)
        if verbose:
            print(f"[problem-setter] Gemini produced {len(qs)} questions.")
        return {"questions": qs, "mix": {"baseline": n_baseline, "targeted": n_targeted},
                "_engine": "gemini"}
    except llm.LLMUnavailable:
        if verbose:
            print("[problem-setter] No GEMINI_API_KEY - using generated question bank.")
    except Exception as exc:  # noqa: BLE001
        if verbose:
            print(f"[problem-setter] Gemini failed ({exc!r}) - using generated question bank.")

    qs = _fallback(packet, targeted_focus, n_baseline, n_targeted)
    return {"questions": qs, "mix": {"baseline": n_baseline, "targeted": n_targeted},
            "_engine": "fallback"}


def _weak_topics(strategy: dict[str, Any]) -> list[str]:
    return [m.get("misconception", "") for m in strategy.get("misconceptions_targeted", []) if m.get("misconception")]


def _coerce(questions: list[Any], total: int) -> list[dict[str, Any]]:
    out = []
    for i, q in enumerate(questions[:total], start=1):
        if not isinstance(q, dict):
            q = {"prompt": str(q)}
        space = str(q.get("space", "medium")).lower()
        out.append({
            "n": i,
            "type": q.get("type", "baseline" if i <= math.ceil(total * 0.7) else "targeted"),
            "prompt": q.get("prompt", ""),
            "answer": str(q.get("answer", "")),
            "hint": q.get("hint", ""),
            "space": space if space in ("small", "medium", "large") else "medium",
            "marking_note": q.get("marking_note", ""),
            "objective": q.get("objective", ""),
        })
    return out


# --------------------------------------------------------------------------- #
def _fallback(packet: CurriculumPacket, targeted_focus: list[str],
              n_baseline: int, n_targeted: int) -> list[dict[str, Any]]:
    from agents.textutil import short, strip_lead
    rng = random.Random(42)
    questions: list[dict[str, Any]] = []
    hint = _SUBJECT_HINT.get(packet.subject, "Show your working and take your time.")

    pool = list(packet.baseline_questions)
    rng.shuffle(pool)
    for i in range(n_baseline):
        p = pool.pop() if pool else f"Practice question on {packet.topic} (part {i + 1})."
        obj = (packet.learning_objectives[i % len(packet.learning_objectives)]
               if packet.learning_objectives else packet.topic)
        questions.append({
            "n": len(questions) + 1, "type": "baseline", "prompt": p,
            "answer": "Check this one with your tutor.",
            "hint": hint, "space": "medium",
            "marking_note": "Aligned to the Oak / UK baseline - check method and working.",
            "objective": strip_lead(obj).rstrip("."),
        })

    for j in range(n_targeted):
        misc = packet.misconceptions[j % len(packet.misconceptions)] if packet.misconceptions else {}
        focus = (targeted_focus[j % len(targeted_focus)] if targeted_focus else None) \
            or short(misc.get("misconception", "")) or packet.topic
        if misc:
            from agents.textutil import depersonalise
            stmt = depersonalise(misc["misconception"]).rstrip(".")
            prompt = f'True or false? "{stmt}." Draw a picture to show why.'
            answer = misc.get("response", "Ask your tutor to check this.")
            note = f"Targets: {short(misc['misconception'], 16)}. Correct: {misc.get('response', '')}".strip()
        else:
            prompt = f"Show what you know about {focus}. Explain your thinking."
            answer = "Check this one with your tutor."
            note = f"Targets this pupil's weak point: {focus}."
        questions.append({
            "n": len(questions) + 1, "type": "targeted", "prompt": prompt,
            "answer": answer, "hint": hint, "space": "large",
            "marking_note": note, "objective": focus,
        })

    return questions


_SUBJECT_HINT = {
    "maths": "Draw a picture first.",
    "english": "Plan it, then check your full stops.",
    "science": "Use the science words and give a reason.",
    "geography": "Add a small map or picture if you can.",
    "history": "Say how we know it is true.",
}
