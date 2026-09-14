"""Sequential multi-agent orchestration.

The pipeline is a strict sequential handoff:

    Ingestor  ->  Curriculum Mapper  ->  Explainer  ->  Problem Setter

The Ingestor grounds everything in the official UK National Curriculum (Oak
National Academy); its packet is passed unchanged to every downstream agent so
their outputs cannot drift beyond the Key Stage.

Two execution engines:

* ``run_weekly_pipeline(...)``            - direct sequential calls to the agent
  modules (each of which calls Gemini, with deterministic fallbacks). This is the
  default and always works.
* ``build_crewai_crew(...)``              - the same four roles expressed as a
  CrewAI ``Crew`` with sequential ``Process``, for when you want the framework's
  own handoff machinery. Used when ``use_crewai=True`` and the library + API key
  are present.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

import config
from agents import assessor_agent, curriculum_agent, explainer_agent, problem_setter
from agents import llm
from agents.curriculum_ingestor import CurriculumPacket, fetch_curriculum


@dataclass
class WeeklyResult:
    topic: str
    subject: str
    key_stage: int
    year_group: Optional[int]
    difficulty: str
    space: str
    curriculum: dict[str, Any]
    strategy: dict[str, Any]
    explanation_md: str
    problems: dict[str, Any]
    engine: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "topic": self.topic, "subject": self.subject, "key_stage": self.key_stage,
            "year_group": self.year_group, "difficulty": self.difficulty, "space": self.space,
            "curriculum": self.curriculum, "strategy": self.strategy,
            "explanation_md": self.explanation_md, "problems": self.problems,
            "engine": self.engine,
        }


# --------------------------------------------------------------------------- #
# Onboarding / progress (The Assessor)
# --------------------------------------------------------------------------- #
def run_assessment(raw_notes: str | None = None, *, image: tuple[bytes, str] | None = None,
                   kind: str = "diagnostic", subject: str | None = None,
                   verbose: bool = True) -> dict[str, Any]:
    return assessor_agent.analyse(raw_notes, image=image, kind=kind, subject=subject,
                                  verbose=verbose)


# --------------------------------------------------------------------------- #
# Weekly book pipeline
# --------------------------------------------------------------------------- #
def run_weekly_pipeline(*, topic: str, key_stage: int, subject: str = "maths",
                        student: dict[str, Any] | None = None,
                        assessment: dict[str, Any] | None = None,
                        num_questions: int = 8, difficulty: str = "core",
                        space: str = "few", use_crewai: bool = False,
                        verbose: bool = True) -> WeeklyResult:
    student = student or {}
    year_group = student.get("year_group")
    difficulty = difficulty if difficulty in ("support", "core", "stretch") else "core"
    space = space if space in ("few", "some", "lots") else "few"

    # 1. INGESTOR — ground in the official UK curriculum
    if verbose:
        print(f"\n[1/4] Ingestor: fetching UK curriculum for '{topic}' (KS{key_stage})...")
    packet: CurriculumPacket = fetch_curriculum(topic, key_stage, subject=subject,
                                                year_group=year_group, verbose=verbose)
    # If the matched Oak unit is broader than the tutor's topic, narrow it so the
    # notes and questions stay on the topic that was actually asked for.
    packet = packet.focused_on(topic)
    # Depth layer: pull Oak lesson transcripts + real quiz questions (needs OAK_API_KEY)
    try:
        from agents import oak_api
        if oak_api.available():
            packet = oak_api.enrich_packet(packet, verbose=verbose)
    except Exception as exc:  # noqa: BLE001
        if verbose:
            print(f"      (Oak API enrichment skipped: {exc!r})")

    # Ground the notes in the real, statutory National Curriculum wording (gov.uk,
    # Open Government Licence) - independent of Oak, downloaded via `python main.py setup`.
    try:
        from agents import study_materials
        extract = study_materials.relevant_curriculum_excerpt(
            subject, topic, year_group=year_group or packet.year_group)
        if extract:
            packet.national_curriculum_extract = extract
    except Exception as exc:  # noqa: BLE001
        if verbose:
            print(f"      (National Curriculum grounding skipped: {exc!r})")

    if verbose:
        print(f"      -> {packet.unit_title}  [{packet.source}]  "
              f"{len(packet.learning_objectives)} objectives, "
              f"{len(packet.misconceptions)} misconceptions"
              + (f", {len(packet.teaching_notes)} Oak transcript(s)" if packet.teaching_notes else ""))

    if verbose:
        print(f"      difficulty: {difficulty}")

    if use_crewai and config.llm_available():
        try:
            return _run_with_crewai(packet, student, assessment, num_questions,
                                    difficulty, space, verbose)
        except Exception as exc:  # noqa: BLE001
            if verbose:
                print(f"      CrewAI path failed ({exc!r}); using direct sequential path.")

    # 2. CURRICULUM MAPPER
    if verbose:
        print("[2/4] Curriculum Mapper: building the weekly strategy...")
    strategy = curriculum_agent.map_strategy(packet, student, assessment,
                                             difficulty=difficulty, verbose=verbose)

    # 3. EXPLAINER
    if verbose:
        print("[3/4] Explainer: writing the pupil's revision notes...")
    explanation_md = explainer_agent.write_explanation(packet, strategy, student,
                                                       difficulty=difficulty, verbose=verbose)

    # 4. PROBLEM SETTER
    if verbose:
        print("[4/4] Problem Setter: writing the practice questions...")
    problems = problem_setter.set_problems(packet, strategy, student, total=num_questions,
                                           difficulty=difficulty, verbose=verbose)

    engine = "gemini" if config.llm_available() else "fallback"
    return WeeklyResult(
        topic=topic, subject=subject, key_stage=key_stage, year_group=year_group,
        difficulty=difficulty, space=space, curriculum=packet.to_dict(), strategy=strategy,
        explanation_md=explanation_md, problems=problems, engine=engine,
    )


# --------------------------------------------------------------------------- #
# CrewAI expression of the same four roles
# --------------------------------------------------------------------------- #
def build_crewai_crew(packet: CurriculumPacket, student: dict[str, Any],
                      assessment: dict[str, Any] | None, num_questions: int,
                      difficulty: str = "core"):
    """Build (crew, ) for the sequential Mapper -> Explainer -> Problem Setter handoff.

    The Ingestor has already run (its output is injected as grounding context).
    """
    from crewai import Agent, Crew, Process, Task

    gemini = llm.crew_llm()
    curriculum_context = packet.summary_for_prompt()
    profile = _student_block(student, assessment)
    diff_note = {"support": "make it EASIER than the year-group norm",
                 "core": "aim at the year-group standard",
                 "stretch": "make it a bit HARDER"}.get(difficulty, "aim at the year-group standard")
    kid_rules = ("Reading age 6-7. Sentences 6-10 words. Talk to the child as 'you'. "
                 "No teacher words. British spelling.")

    mapper = Agent(
        role="UK Primary Curriculum Mapper",
        goal="Turn official Key Stage objectives + a pupil profile into a concrete, personalised weekly strategy.",
        backstory="A primary curriculum lead who never teaches beyond the Key Stage and always "
                  "personalises to the child's interests.",
        llm=gemini, verbose=False, allow_delegation=False,
    )
    explainer = Agent(
        role="Children's Concept Explainer",
        goal="Explain the week's concept to a 7-9 year old in warm, simple Markdown.",
        backstory="A Year 3 teacher famous for making hard ideas feel easy with a good story.",
        llm=gemini, verbose=False, allow_delegation=False,
    )
    setter = Agent(
        role="Primary Practice Question Writer",
        goal="Write 5-10 questions: 70% at the UK baseline, 30% targeting the pupil's weak points.",
        backstory="An assessment writer who calibrates every item to the National Curriculum.",
        llm=gemini, verbose=False, allow_delegation=False,
    )

    t_map = Task(
        description=f"OFFICIAL UK CURRICULUM (do not exceed):\n{curriculum_context}\n\n"
                    f"PUPIL PROFILE:\n{profile}\n\n"
                    "Produce a JSON weekly strategy with keys: week_focus, objectives_selected, "
                    "misconceptions_targeted (each with misconception, teaching_move, representation), "
                    "personalisation_notes, explainer_brief, problem_setter_brief "
                    "(baseline_focus, targeted_focus), success_criteria.",
        expected_output="A single JSON object, no prose.",
        agent=mapper,
    )
    t_explain = Task(
        description="Using the strategy from the previous task and staying within the official "
                    f"objectives:\n{curriculum_context}\n\n"
                    "Write the pupil's REVISION NOTES as Markdown with exactly these sections: "
                    "'## The big idea', '## Remember these steps', '## Words to know', "
                    "'## Easy to get wrong', '## Check yourself' (3 Q with the answer under each). "
                    f"{kid_rules} Difficulty: {diff_note}. Under 320 words. No title, no pep talk.",
        expected_output="GitHub-flavoured Markdown, no preamble.",
        agent=explainer, context=[t_map],
    )
    t_set = Task(
        description=f"Using the strategy's problem_setter_brief and these example items:\n"
                    f"{chr(10).join('- ' + q for q in packet.baseline_questions[:6])}\n\n"
                    f"Write exactly {num_questions} questions as JSON {{\"questions\": [...]}}, each with "
                    "n, type ('baseline'|'targeted'), prompt, answer, hint, space ('small'|'medium'|'large'). "
                    f"~70% normal revision, ~30% on the pupil's weak points. {kid_rules} "
                    f"Difficulty: {diff_note}. Each prompt under 18 words.",
        expected_output="A single JSON object with a 'questions' array.",
        agent=setter, context=[t_map],
    )

    crew = Crew(agents=[mapper, explainer, setter], tasks=[t_map, t_explain, t_set],
                process=Process.sequential, verbose=False)
    return crew, (t_map, t_explain, t_set)


def _run_with_crewai(packet, student, assessment, num_questions, difficulty, space, verbose) -> WeeklyResult:
    if verbose:
        print("[2-4/4] CrewAI: sequential Mapper -> Explainer -> Problem Setter...")
    crew, (t_map, t_explain, t_set) = build_crewai_crew(packet, student, assessment,
                                                        num_questions, difficulty)
    crew.kickoff()

    strategy = llm._loads_loose(str(t_map.output))
    explanation_md = str(t_explain.output).strip()
    problems_raw = llm._loads_loose(str(t_set.output))
    problems = {"questions": problem_setter._coerce(problems_raw.get("questions", []), num_questions),
                "mix": {"baseline": num_questions - max(1, round(num_questions * 0.3)),
                        "targeted": max(1, round(num_questions * 0.3))},
                "_engine": "crewai"}
    strategy["_curriculum_source"] = packet.source

    return WeeklyResult(
        topic=packet.topic, subject=packet.subject, key_stage=packet.key_stage,
        year_group=packet.year_group, difficulty=difficulty, space=space,
        curriculum=packet.to_dict(), strategy=strategy, explanation_md=explanation_md,
        problems=problems, engine="crewai",
    )


def _student_block(student: dict[str, Any], assessment: dict[str, Any] | None) -> str:
    a = assessment or {}
    wp = a.get("weak_points") or a.get("weak_points_json") or []
    st = a.get("strengths") or a.get("strengths_json") or []
    lines = [
        f"Name: {student.get('full_name') or a.get('student', {}).get('full_name') or 'the pupil'}",
        f"Year group: {student.get('year_group') or '?'}  Key Stage: {student.get('key_stage') or '?'}",
        f"Interests: {student.get('interests') or ', '.join(a.get('student', {}).get('interests', [])) or 'n/a'}",
        f"Tutor summary: {a.get('tutor_summary', 'n/a')}",
        "Strengths: " + ("; ".join(map(str, st)) or "n/a"),
        "Weak points:",
    ]
    for w in wp:
        if isinstance(w, dict):
            lines.append(f"  - {w.get('topic', '')}: {w.get('detail', '')} "
                         f"(misconception: {w.get('misconception') or 'n/a'}, severity: {w.get('severity', 'medium')})")
        else:
            lines.append(f"  - {w}")
    return "\n".join(lines)
