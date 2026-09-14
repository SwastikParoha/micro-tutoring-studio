"""The Explainer.

Writes this week's **revision notes** for the pupil - the pages they read on
their own to remember what they learned and to get ready for a test. Not a
lesson plan, not for the tutor. Very simple language.

Uses Gemini when available; a plain, honest template otherwise.
"""
from __future__ import annotations

from typing import Any

from agents import llm
from agents.curriculum_ingestor import CurriculumPacket

SYSTEM = (
    "You write revision notes for a child aged 7-9 who reads them ALONE, to get "
    "ready for a test at school. Rules:\n"
    "- Reading age 6-7. Only words a Year 2-3 child knows.\n"
    "- Sentences of 6-10 words. One idea per sentence.\n"
    "- Talk to the child as 'you'. Warm, clear, calm.\n"
    "- NO teacher words. Never: objective, method, represent, sequence, expansion, "
    "consequence, evaluate, chronological, significant, context, misconception.\n"
    "- Every hard word (like 'denominator' or 'monastery') is explained right there, "
    "with an example.\n"
    "- Show, don't just tell. Use one tiny picture-in-words or a worked example.\n"
    "- British spelling (maths, colour, favourite, practise).\n"
    "- Write fractions as plain text like 1/4 or 'one quarter'. NEVER use LaTeX, "
    "$ signs, or \\frac.\n"
    "Output GitHub-flavoured Markdown only. No title, no preamble, no closing pep talk."
)

_DIFFICULTY = {
    "support": "Keep it very gentle. Tiny numbers or simple facts. Extra worked examples. "
               "One step at a time.",
    "core": "Aim at the normal standard for this year group.",
    "stretch": "Push a little. Bigger numbers or deeper 'why' questions. Fewer hand-holds.",
}

_PROMPT = """\
Write this week's REVISION NOTES for the pupil. This is the page they read on
their own to revise for a school test. THE TEST: after reading only these notes,
the pupil must be able to answer any normal Year {year} question on this topic.
So the notes must teach the WHOLE method - every case, not just one.

TOPIC (write about THIS and only this): {topic}
YEAR: {year}
WHAT THEY LIKE (use in an example if it fits naturally): {interests}
DIFFICULTY: {difficulty_note}

WHAT THEY COVERED (from the official curriculum - stay inside this, focused on the topic):
{objectives}

THINGS PUPILS OFTEN GET WRONG:
{misconceptions}

KEY WORDS:
{vocab}

{teaching_block}

=== WRITE THE NOTES AS MARKDOWN, EXACTLY THIS SHAPE ===

## What this is about
(2 sentences: what the topic is and where you use it.)

## The idea you must understand
(A clear explanation of the core idea. 5-8 short sentences. Use a picture-in-words
they can imagine. If there is a rule, state it plainly. This is the heart of the notes.)

## The method — step by step
(A numbered list of the exact steps to answer a question. 3-6 steps. Each step is
one short clear line. Cover every case they might meet (e.g. same number of digits
AND different numbers of digits; whole numbers AND with a remainder; etc).)

## Worked examples
(TWO or THREE examples, each showing a DIFFERENT case. For each: write the question,
then every step of working on its own line, then **Answer: ...**. Number them.)

## Words to know
(Bullet list, 5-8 words. **word** - short meaning, then "like ..." with an example.)

## Common mistakes
(3-4 bullets. Each: "Some pupils think X." on the next line "But really: Y.")

## Test yourself
(5 questions from easy to harder, covering the different cases. Put the answer right
after each one in *italics*.)

Rules:
- 320-410 words. Complete but tight - every line earns its place, no padding or repeating.
  It should fill roughly one page.
- Simple words only. Sentences 6-13 words. Talk to the child as "you".
- No teacher jargon. British spelling. Fractions/decimals as plain text (1/4, 0.7), never LaTeX.
- Do not add a "Revision notes" heading or any section not listed above.
"""


def write_explanation(packet: CurriculumPacket, strategy: dict[str, Any],
                      student: dict[str, Any], *, difficulty: str = "core",
                      verbose: bool = False) -> str:
    interests = student.get("interests") or ""
    year = student.get("year_group") or packet.year_group or 3
    objectives = strategy.get("objectives_selected") or [
        _plain(o) for o in packet.learning_objectives[:4]
    ]
    if not objectives:
        objectives = [f"(No exact curriculum unit matched. Cover '{packet.topic}' at the "
                      f"normal Year {year} standard, using your own knowledge. Keep it simple.)"]
    misc = strategy.get("misconceptions_targeted") or [
        {"misconception": m["misconception"]} for m in packet.misconceptions[:3]
    ]
    vocab_defs = (packet.key_vocabulary_defs or [
        {"term": w, "definition": ""} for w in packet.key_vocabulary
    ])[:8]

    teaching = ""
    for t in (packet.teaching_notes or [])[:2]:
        teaching += f"\n--- Oak lesson: {t.get('title', '')} ---\n{t.get('transcript', '')}\n"
    teaching_block = (
        "HOW OAK'S OWN TEACHER EXPLAINS THIS (use the SAME approach, order and examples, "
        "but rewrite it as short notes for a child reading alone - do NOT copy the "
        "spoken style):\n" + teaching
    ) if teaching.strip() else ""

    if getattr(packet, "national_curriculum_extract", ""):
        teaching_block += (
            "\n\nTHE REAL, OFFICIAL NATIONAL CURRICULUM WORDING FOR THIS SUBJECT (gov.uk - this "
            "is the actual statutory requirement schools are marked against; make sure your notes "
            "genuinely cover what it asks for, in your own simple words, not copied verbatim):\n"
            + packet.national_curriculum_extract
        )

    prompt = _PROMPT.format(
        topic=packet.topic,
        year=year,
        interests=interests or "(nothing recorded - keep examples simple)",
        difficulty_note=_DIFFICULTY.get(difficulty, _DIFFICULTY["core"]),
        objectives="\n".join(f"  - {o}" for o in objectives),
        misconceptions="\n".join(
            f"  - {_dep(m.get('misconception', m))}"
            + (f"  (the fix: {m['teaching_move']})" if isinstance(m, dict) and m.get("teaching_move") else "")
            for m in misc
        ) or "  - (none listed - just remind them to check their work)",
        vocab="\n".join(
            f"  - {d.get('term', '')}" + (f": {d['definition']}" if d.get("definition") else "")
            for d in vocab_defs
        ) or "  - (none listed)",
        teaching_block=teaching_block,
    )

    try:
        md = llm.generate(prompt, system=SYSTEM, temperature=0.6)
        if verbose:
            print("[explainer] Gemini revision notes complete.")
        return md.strip()
    except llm.LLMUnavailable:
        if verbose:
            print("[explainer] No GEMINI_API_KEY - using template notes.")
        return _fallback(packet, strategy, student)
    except Exception as exc:  # noqa: BLE001
        if verbose:
            print(f"[explainer] Gemini failed ({exc!r}) - using template notes.")
        return _fallback(packet, strategy, student)


def _plain(text: str) -> str:
    from agents.textutil import plain
    return plain(text)


def _dep(text: Any) -> str:
    from agents.textutil import depersonalise
    return depersonalise(str(text))


# --------------------------------------------------------------------------- #
def _fallback(packet: CurriculumPacket, strategy: dict[str, Any],
              student: dict[str, Any]) -> str:
    """Plain revision notes from the real Oak content, no LLM."""
    from agents.textutil import depersonalise

    titles = [l.get("title", "") for l in packet.lessons if l.get("title")][:6]
    steps = "\n".join(f"{i}. {t}" for i, t in enumerate(titles, 1)) or \
        f"1. Learn what {packet.topic} means.\n2. Practise it with your tutor."

    defs = {d.get("term", "").lower(): d.get("definition", "")
            for d in (packet.key_vocabulary_defs or [])}
    vocab = packet.key_vocabulary[:8] or list(defs)
    vocab_lines = "\n".join(
        f"- **{w}**" + (f" - {defs[w.lower()]}" if defs.get(w.lower()) else "")
        for w in vocab) or "- Ask your tutor for this week's words."

    watch = strategy.get("misconceptions_targeted") or [
        {"misconception": m.get("misconception", ""), "teaching_move": m.get("response", "")}
        for m in packet.misconceptions[:3]
    ]
    watch_lines = "\n".join(
        f"- Some pupils think: {depersonalise(w.get('misconception', '')).rstrip('.')}.  \n"
        f"  But really: {(w.get('teaching_move') or w.get('response') or 'check it carefully with your tutor').rstrip('.')}."
        for w in watch if w.get("misconception")
    ) or "- Read every question twice before you answer."

    checks = ""
    for q in packet.baseline_questions[:4]:
        checks += f"- {q}  *(check with your tutor)*\n"

    prereqs = [p.rstrip(".") for p in packet.prerequisites if 3 <= len(p.split()) <= 10][:2]
    already = ("\n\n**You should already know:** " + "; ".join(prereqs) + "."
               if prereqs else "")

    return f"""## What this is about
This week is about **{packet.topic}**. These notes remind you of the important
parts so you can revise on your own.{already}

## The idea you must understand
Your tutor explains this step by step in your lesson. Read these notes again
before your practice and before any test. Take your time and picture each idea.

## The method — step by step
{steps}

## Worked examples
Ask your tutor to work through two of the practice questions with you and write
the steps here, so you can copy the method later.

## Words to know
{vocab_lines}

## Common mistakes
{watch_lines}

## Test yourself
{checks or "- Ask your tutor for four quick questions to try."}
"""
