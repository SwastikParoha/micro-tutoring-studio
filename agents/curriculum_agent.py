"""The Curriculum Mapper.

Takes the UK curriculum packet from The Ingestor + the student's DB profile
(and latest assessment) and produces a tailored weekly teaching strategy.

Example output: "Address the misconception that 1/4 is larger than 1/2 using
visual pie charts, matching their interest in pizza."
"""
from __future__ import annotations

from typing import Any

from agents import llm
from agents.curriculum_ingestor import CurriculumPacket

SYSTEM = (
    "You are a UK primary curriculum lead planning one week of 1:1 tuition. "
    "Stay strictly within the official Key Stage objectives you are given - never add "
    "content from later year groups. Turn the pupil's weak points into concrete teaching "
    "moves, personalised to their interests.\n"
    "IMPORTANT: 'week_focus', 'objectives_selected' and 'success_criteria' are printed on "
    "the CHILD'S workbook. Write those three in plain words a 7-9 year old understands - "
    "short, no teacher jargon (no 'distinguish', 'debunk', 'evaluate', 'chronological', "
    "'consequence', 'expansion'). British spelling (armour, colour, maths). "
    "The other fields are for the tutor and can be more technical."
)

_PROMPT = """\
Plan ONE week of tuition for this pupil.

=== OFFICIAL UK CURRICULUM (from Oak National Academy - do not go beyond this) ===
{curriculum}

=== PUPIL PROFILE ===
Name: {name}
Year group: {year} (Key Stage {key_stage})
Difficulty for this week: {difficulty}
Interests: {interests}
Tutor summary: {tutor_summary}

Strengths:
{strengths}

Weak points (priority order):
{weak_points}

=== TASK ===
Return ONLY JSON:
{{
  "week_focus": string,                       // ONE short sentence, kid-friendly: the main goal this week
  "objectives_selected": [string],            // 2-4 goals from the official list, REWORDED simply for a child
  "misconceptions_targeted": [
    {{
      "misconception": string,                // taken from the official list where possible
      "teaching_move": string,                // concrete activity, personalised to the interests
      "representation": string                // e.g. "pie charts", "bar model", "number line", "counters"
    }}
  ],
  "personalisation_notes": string,            // how the pupil's interests thread through the week
  "explainer_brief": string,                  // 2-3 sentences telling The Explainer what to write
  "problem_setter_brief": {{
    "baseline_focus": [string],               // UK-standard skills for the 70% baseline questions
    "targeted_focus": [string]                // this pupil's weak points for the 30% targeted questions
  }},
  "success_criteria": [string]                // 2-4 short "I can ..." statements in kid words
}}

Rules:
- EVERYTHING this week is about the topic named at the top. If a pupil weak point is
  about a DIFFERENT topic, leave it out - do not mix other topics into this workbook.
- misconceptions_targeted and targeted_focus: only the pupil's mistakes that relate to
  THIS topic. If none of their weak points fit the topic, use the common mistakes for
  the topic instead.
- Name a specific picture/model for each misconception.
- Weave the pupil's interests into concrete contexts (football scores, pizza slices, Minecraft blocks).
- week_focus / objectives_selected / success_criteria: short, plain, child-friendly. British spelling.
"""


_DIFFICULTY = {
    "support": "EASIER than the year-group norm - smaller numbers, more scaffolding, one step.",
    "core": "at the year-group standard.",
    "stretch": "a little HARDER - bigger numbers, multi-step, more 'why' questions.",
}


def map_strategy(packet: CurriculumPacket, student: dict[str, Any],
                 assessment: dict[str, Any] | None, *, difficulty: str = "core",
                 verbose: bool = False) -> dict[str, Any]:
    assessment = assessment or {}
    weak_points = assessment.get("weak_points") or assessment.get("weak_points_json") or []
    strengths = assessment.get("strengths") or assessment.get("strengths_json") or []
    interests = student.get("interests") or ", ".join(assessment.get("student", {}).get("interests", []))

    prompt = _PROMPT.format(
        curriculum=packet.summary_for_prompt(),
        name=student.get("full_name") or assessment.get("student", {}).get("full_name") or "the pupil",
        year=student.get("year_group") or packet.year_group or "?",
        key_stage=student.get("key_stage") or packet.key_stage,
        difficulty=_DIFFICULTY.get(difficulty, _DIFFICULTY["core"]),
        interests=interests or "not recorded",
        tutor_summary=assessment.get("tutor_summary", ""),
        strengths="\n".join(f"  - {s}" for s in _as_str_list(strengths)) or "  - (none recorded)",
        weak_points="\n".join(
            f"  - {wp.get('topic', wp) if isinstance(wp, dict) else wp}"
            + (f" | {wp['detail']}" if isinstance(wp, dict) and wp.get("detail") else "")
            + (f" | misconception: {wp['misconception']}" if isinstance(wp, dict) and wp.get("misconception") else "")
            for wp in weak_points
        ) or "  - (none recorded - use baseline objectives)",
    )

    try:
        strategy = llm.generate_json(prompt, system=SYSTEM, temperature=0.5)
        if verbose:
            print("[mapper] Gemini strategy complete.")
        return _coerce(strategy, packet, weak_points)
    except llm.LLMUnavailable:
        if verbose:
            print("[mapper] No GEMINI_API_KEY - using rule-based strategy.")
        return _fallback(packet, student, weak_points, strengths, interests)
    except Exception as exc:  # noqa: BLE001
        if verbose:
            print(f"[mapper] Gemini failed ({exc!r}) - using rule-based strategy.")
        return _fallback(packet, student, weak_points, strengths, interests)


def _tidy(text: str, max_words: int = 12) -> str:
    """Trim a weak-point sentence to a short, readable focus phrase."""
    import re
    t = re.sub(r"\s+", " ", str(text)).strip(" .-")
    t = re.split(r"[:;.\n]| - ", t, maxsplit=1)[0].strip()
    t = re.sub(r"^(?:the pupil|pupil|she|he|they|when i showed[^,]*,?\s*)\s*", "", t, flags=re.I)
    words = t.split()
    return " ".join(words[:max_words]) + ("..." if len(words) > max_words else "")


def _as_str_list(items: Any) -> list[str]:
    out = []
    for it in items or []:
        if isinstance(it, dict):
            out.append(it.get("topic") or it.get("detail") or str(it))
        else:
            out.append(str(it))
    return out


def _coerce(strategy: dict[str, Any], packet: CurriculumPacket, weak_points: list) -> dict[str, Any]:
    strategy.setdefault("week_focus", f"Secure the Year {packet.year_group} objectives for {packet.topic}.")
    strategy.setdefault("objectives_selected", packet.learning_objectives[:3])
    strategy.setdefault("misconceptions_targeted", [])
    strategy.setdefault("personalisation_notes", "")
    strategy.setdefault("explainer_brief", "")
    strategy.setdefault("problem_setter_brief", {})
    strategy["problem_setter_brief"].setdefault("baseline_focus", packet.learning_objectives[:3])
    strategy["problem_setter_brief"].setdefault(
        "targeted_focus", _as_str_list(weak_points)[:3]
        or [m.get("misconception", "") for m in packet.misconceptions[:2]]
        or packet.learning_objectives[:1])
    strategy.setdefault("success_criteria", [])
    strategy["_curriculum_source"] = packet.source
    return strategy


_REPRESENTATIONS = {
    "maths": ["a bar model", "a number line", "counters or cubes", "a pie chart / fraction wall"],
    "english": ["a word bank", "a modelled sentence", "a planning grid", "colour-coded examples"],
    "science": ["a labelled diagram", "a simple experiment", "a real object to handle", "a before/after picture"],
    "geography": ["a labelled map", "an aerial photo", "a cross-section diagram", "a fieldwork sketch"],
    "history": ["a timeline", "a source to compare", "a picture from the period", "a then-and-now table"],
}
_DEFAULT_REPS = ["a labelled diagram", "a worked example", "a picture", "a sorting activity"]


def _fallback(packet: CurriculumPacket, student: dict[str, Any], weak_points: list,
              strengths: Any, interests: str) -> dict[str, Any]:
    from agents.textutil import to_i_can, short, plain

    interest = (interests.split(",")[0].strip() if interests else "")
    reps = _REPRESENTATIONS.get(packet.subject, _DEFAULT_REPS)
    like = f" Where possible, use an example about {interest}." if interest else ""

    targeted = [_tidy(t) for t in _as_str_list(weak_points)[:3]]
    if not targeted:
        targeted = [short(m.get("misconception", "")) for m in packet.misconceptions[:2]]

    misconceptions_targeted = []
    for i, m in enumerate(packet.misconceptions[:3]):
        move = (m.get("response") or "").strip() or f"Model it with {reps[i % len(reps)]}."
        misconceptions_targeted.append({
            "misconception": m.get("misconception", ""),
            "teaching_move": move + like,
            "representation": reps[i % len(reps)],
        })

    weak_note = (", with some extra practice on the tricky bits" if weak_points else "")
    return {
        "week_focus": f"Get really good at {packet.topic}{weak_note}.",
        "objectives_selected": [plain(o) for o in packet.learning_objectives[:4]],
        "misconceptions_targeted": misconceptions_targeted,
        "personalisation_notes": (f"Thread examples about {interest} through the week."
                                  if interest else "Keep every example concrete and visual."),
        "explainer_brief": (
            f"Explain {packet.topic} to a {student.get('age') or 8}-year-old."
            + (f" Use {interest} for the examples." if interest else "")
            + f" Cover: {'; '.join(as_short(packet.learning_objectives[:3]))}."
            + (f" Head off this misconception: {packet.misconceptions[0]['misconception']}"
               if packet.misconceptions else "")
        ),
        "problem_setter_brief": {
            "baseline_focus": packet.learning_objectives[:3] or [packet.topic],
            "targeted_focus": targeted or [packet.topic],
        },
        "success_criteria": [to_i_can(plain(o)) for o in packet.learning_objectives[:3]],
        "_curriculum_source": packet.source,
        "_engine": "fallback",
    }


def as_short(items: list[str]) -> list[str]:
    from agents.textutil import strip_lead
    return [strip_lead(i).rstrip(".") for i in items]
