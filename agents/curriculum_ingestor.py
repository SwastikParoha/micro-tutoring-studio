"""The Ingestor.

Retrieval module that grounds the whole pipeline in the official
UK National Curriculum via the Oak National Academy Open Curriculum API
(https://open-api.thenational.academy).

Given a target week's topic (e.g. "fractions", Key Stage 2, Year 3) it returns a
normalised ``CurriculumPacket`` containing:

* the official learning objectives / key learning points,
* known pupil misconceptions and suggested teacher responses,
* key vocabulary,
* a set of baseline questions aligned to the UK standard,
* the National Curriculum statutory references.

If no ``OAK_API_KEY`` is configured (or ``OFFLINE_MODE=true``, or the API call
fails) it transparently falls back to the bundled
``data/oak_mock_curriculum.json`` so the pipeline stays runnable.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from difflib import SequenceMatcher
from typing import Any, Optional

import requests

import config


_SOURCE_LABELS = {
    "oak-api": "Oak National Academy API (live)",
    "oak-local": "Oak National Academy bulk dataset (official, offline)",
    "mock": "bundled sample data",
}


# --------------------------------------------------------------------------- #
# Normalised output
# --------------------------------------------------------------------------- #
@dataclass
class CurriculumPacket:
    topic: str
    subject: str
    key_stage: int
    year_group: Optional[int]
    unit_title: str
    learning_objectives: list[str] = field(default_factory=list)
    misconceptions: list[dict[str, str]] = field(default_factory=list)
    key_vocabulary: list[str] = field(default_factory=list)
    key_vocabulary_defs: list[dict[str, str]] = field(default_factory=list)  # [{term, definition}]
    national_curriculum_refs: list[str] = field(default_factory=list)
    lessons: list[dict[str, str]] = field(default_factory=list)
    baseline_questions: list[str] = field(default_factory=list)
    prerequisites: list[str] = field(default_factory=list)
    teaching_notes: list[dict[str, str]] = field(default_factory=list)   # Oak lesson transcripts
    oak_questions: list[dict[str, Any]] = field(default_factory=list)    # Oak real quiz questions
    national_curriculum_extract: str = ""   # real DfE programme-of-study wording, gov.uk
    source: str = "mock"          # "oak-api" | "oak-local" | "mock"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def focused_on(self, topic: str) -> "CurriculumPacket":
        """Return a copy narrowed to the tutor's topic when the matched Oak unit
        is broader (e.g. unit 'fractions and decimals', tutor typed 'decimals')."""
        import re
        from dataclasses import replace

        stop = {"the", "and", "of", "a", "an", "to", "in", "with", "for", "comparing",
                "compare", "using", "understanding", "understand"}
        words = {w for w in re.findall(r"[a-z]+", topic.lower())
                 if w not in stop and len(w) > 3}
        if not words:
            return self

        def hit(text: str) -> bool:
            t = (text or "").lower()
            return any(w in t or w[:-1] in t for w in words)

        objs = [o for o in self.learning_objectives if hit(o)]
        misc = [m for m in self.misconceptions if hit(m.get("misconception", ""))]
        base = [q for q in self.baseline_questions if hit(q)]
        vocab = [v for v in self.key_vocabulary if hit(v)]
        vdefs = [d for d in self.key_vocabulary_defs if hit(d.get("term", ""))]

        if len(objs) >= 2:
            # the unit genuinely covers the topic - narrow to it, keep the lessons
            lessons = [l for l in self.lessons if hit(l.get("title", ""))] or self.lessons
            return replace(
                self,
                learning_objectives=objs,
                misconceptions=misc or self.misconceptions[:3],
                baseline_questions=base or self.baseline_questions,
                key_vocabulary=vocab or self.key_vocabulary,
                key_vocabulary_defs=vdefs or self.key_vocabulary_defs,
                lessons=lessons,
            )

        # the matched unit's objectives don't cover the topic the tutor asked for.
        # Drop the unit's content (and its lessons - so we don't pull the wrong
        # transcripts) and let the topic itself drive the notes/questions.
        return replace(
            self,
            unit_title=topic.strip()[:1].upper() + topic.strip()[1:],
            learning_objectives=[],
            misconceptions=misc,
            baseline_questions=base,
            key_vocabulary=vocab,
            key_vocabulary_defs=vdefs,
            lessons=[l for l in self.lessons if hit(l.get("title", ""))],
        )

    def summary_for_prompt(self) -> str:
        lines = [
            f"UNIT: {self.unit_title}  ({self.subject}, Key Stage {self.key_stage}"
            + (f", Year {self.year_group}" if self.year_group else "") + ")",
            f"SOURCE: {_SOURCE_LABELS.get(self.source, self.source)}",
            "",
            "OFFICIAL LEARNING OBJECTIVES:",
            *[f"  - {o}" for o in self.learning_objectives],
            "",
            "NATIONAL CURRICULUM REFERENCES:",
            *[f"  - {r}" for r in self.national_curriculum_refs],
            "",
            "KNOWN PUPIL MISCONCEPTIONS (address these explicitly):",
        ]
        for m in self.misconceptions:
            lines.append(f"  - {m.get('misconception', '')}")
            if m.get("why"):
                lines.append(f"      why: {m['why']}")
            if m.get("response"):
                lines.append(f"      suggested response: {m['response']}")
        lines += [
            "",
            f"KEY VOCABULARY: {', '.join(self.key_vocabulary)}",
            f"PREREQUISITES: {', '.join(self.prerequisites)}",
            "",
            "UK BASELINE QUESTIONS (the standard all pupils should meet):",
            *[f"  - {q}" for q in self.baseline_questions],
        ]
        return "\n".join(lines)


# --------------------------------------------------------------------------- #
# Oak API client
# --------------------------------------------------------------------------- #
class OakClient:
    """Minimal client for the Oak National Academy Open Curriculum API."""

    def __init__(self, api_key: str | None = None, base_url: str | None = None,
                 auth_scheme: str | None = None, timeout: int = 20):
        self.api_key = api_key or config.OAK_API_KEY
        self.base_url = (base_url or config.OAK_API_BASE_URL).rstrip("/")
        self.auth_scheme = (auth_scheme or config.OAK_API_AUTH_SCHEME).lower()
        self.timeout = timeout
        self.session = requests.Session()

    @property
    def enabled(self) -> bool:
        return bool(self.api_key) and not config.OFFLINE_MODE

    def _headers(self) -> dict[str, str]:
        h = {"Accept": "application/json", "User-Agent": "micro-tutoring/1.0"}
        if self.auth_scheme in ("x-api-key", "apikey", "header"):
            h["x-api-key"] = self.api_key
        else:  # default: bearer
            h["Authorization"] = f"Bearer {self.api_key}"
        return h

    def get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        url = path if path.startswith("http") else f"{self.base_url}/{path.lstrip('/')}"
        resp = self.session.get(url, headers=self._headers(), params=params, timeout=self.timeout)
        resp.raise_for_status()
        return resp.json()

    # -- higher level ------------------------------------------------------- #
    def find_unit(self, *, subject: str, key_stage: int, year_group: int | None,
                  topic: str) -> Optional[dict[str, Any]]:
        """Search the curriculum for a unit matching the topic.

        The Oak API exposes several routes to units; we try the documented
        subject/key-stage listing first and match the topic client-side.
        """
        candidate_paths = [
            (f"key-stages/ks{key_stage}/subjects/{subject}/units", {}),
            (f"key-stages/key-stage-{key_stage}/subjects/{subject}/units", {}),
            ("units", {"subject": subject, "keyStage": f"ks{key_stage}"}),
            ("search", {"q": topic, "subject": subject, "keyStage": f"ks{key_stage}"}),
        ]
        units: list[dict[str, Any]] = []
        for path, params in candidate_paths:
            try:
                data = self.get(path, params or None)
            except requests.RequestException:
                continue
            units = _coerce_unit_list(data)
            if units:
                break
        if not units:
            return None

        best, best_score = None, 0.0
        for u in units:
            title = (u.get("title") or u.get("unitTitle") or u.get("name") or "").lower()
            slug = (u.get("slug") or "").lower()
            score = max(_similar(topic, title), _similar(topic, slug))
            if year_group and str(year_group) in (str(u.get("yearGroup", "")) + str(u.get("year", ""))):
                score += 0.15
            if score > best_score:
                best, best_score = u, score
        return best if best_score >= 0.35 else units[0]

    def lesson_summary(self, lesson_slug: str) -> Optional[dict[str, Any]]:
        for path in (f"lessons/{lesson_slug}/summary", f"lessons/{lesson_slug}"):
            try:
                return self.get(path)
            except requests.RequestException:
                continue
        return None


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def _similar(a: str, b: str) -> float:
    a, b = a.lower().strip(), b.lower().strip()
    if not a or not b:
        return 0.0
    if a in b or b in a:
        return 0.9
    return SequenceMatcher(None, a, b).ratio()


def _coerce_unit_list(data: Any) -> list[dict[str, Any]]:
    if isinstance(data, list):
        return [d for d in data if isinstance(d, dict)]
    if isinstance(data, dict):
        for key in ("units", "data", "results", "items"):
            if isinstance(data.get(key), list):
                return [d for d in data[key] if isinstance(d, dict)]
    return []


def _normalise_oak_unit(unit: dict[str, Any], client: OakClient, *, topic: str,
                        subject: str, key_stage: int, year_group: int | None) -> CurriculumPacket:
    objectives: list[str] = []
    for k in ("learningObjectives", "keyLearningPoints", "pupilLessonOutcomes", "learningOutcomes"):
        v = unit.get(k)
        if isinstance(v, list):
            objectives += [str(x.get("text") if isinstance(x, dict) else x) for x in v]

    misconceptions: list[dict[str, str]] = []
    for m in unit.get("misconceptions", []) or []:
        if isinstance(m, dict):
            misconceptions.append({
                "misconception": m.get("misconception") or m.get("text") or "",
                "why": m.get("why", ""),
                "response": m.get("response") or m.get("mcResponse") or "",
            })

    lessons = []
    for l in unit.get("lessons", []) or []:
        if isinstance(l, dict):
            lessons.append({"slug": l.get("slug", ""), "title": l.get("title") or l.get("lessonTitle", "")})

    # Enrich from lesson summaries when the unit payload is thin on misconceptions.
    if not misconceptions:
        for l in lessons[:4]:
            if not l["slug"]:
                continue
            summ = client.lesson_summary(l["slug"])
            for m in (summ or {}).get("misconceptionsAndCommonMistakes", []) or (summ or {}).get("misconceptions", []) or []:
                if isinstance(m, dict):
                    misconceptions.append({
                        "misconception": m.get("misconception") or m.get("misconceptionOrMistake") or "",
                        "why": "",
                        "response": m.get("response") or m.get("mcResponse") or "",
                    })
            for lp in (summ or {}).get("keyLearningPoints", []) or []:
                objectives.append(lp.get("keyLearningPoint") if isinstance(lp, dict) else str(lp))

    vocab = []
    for kw in unit.get("keyVocabulary", []) or unit.get("keywords", []) or []:
        vocab.append(kw.get("keyword") if isinstance(kw, dict) else str(kw))

    refs = []
    for r in unit.get("nationalCurriculumContent", []) or unit.get("nationalCurriculumRefs", []) or []:
        refs.append(r.get("contentGuidance") if isinstance(r, dict) else str(r))

    return CurriculumPacket(
        topic=topic,
        subject=subject,
        key_stage=key_stage,
        year_group=year_group,
        unit_title=unit.get("title") or unit.get("unitTitle") or topic.title(),
        learning_objectives=_dedupe(objectives),
        misconceptions=[m for m in misconceptions if m["misconception"]],
        key_vocabulary=_dedupe(vocab),
        national_curriculum_refs=_dedupe(refs),
        lessons=lessons,
        baseline_questions=_dedupe([str(q) for q in unit.get("baseline_questions", []) or []]),
        prerequisites=_dedupe([str(p) for p in unit.get("prerequisites", []) or []]),
        source="oak-api",
    )


def _dedupe(items: list[str]) -> list[str]:
    seen, out = set(), []
    for it in items:
        it = (it or "").strip()
        if it and it.lower() not in seen:
            seen.add(it.lower())
            out.append(it)
    return out


# --------------------------------------------------------------------------- #
# Mock retrieval
# --------------------------------------------------------------------------- #
def _load_mock() -> list[dict[str, Any]]:
    return json.loads(config.MOCK_CURRICULUM_PATH.read_text(encoding="utf-8")).get("units", [])


def _retrieve_from_mock(*, topic: str, subject: str, key_stage: int,
                        year_group: int | None) -> CurriculumPacket:
    units = _load_mock()
    scored: list[tuple[float, dict[str, Any]]] = []
    for u in units:
        score = 0.0
        if u.get("subject") == subject:
            score += 0.3
        if u.get("key_stage") == key_stage:
            score += 0.3
        if year_group and u.get("year_group") == year_group:
            score += 0.2
        score += _similar(topic, u.get("title", "")) + _similar(topic, u.get("slug", ""))
        scored.append((score, u))
    scored.sort(key=lambda t: t[0], reverse=True)
    best = scored[0][1] if scored else {}

    return CurriculumPacket(
        topic=topic,
        subject=subject,
        key_stage=key_stage,
        year_group=year_group or best.get("year_group"),
        unit_title=best.get("title", topic.title()),
        learning_objectives=best.get("learning_objectives", []),
        misconceptions=best.get("misconceptions", []),
        key_vocabulary=best.get("key_vocabulary", []),
        national_curriculum_refs=best.get("national_curriculum_refs", []),
        lessons=best.get("lessons", []),
        baseline_questions=best.get("baseline_questions", []),
        prerequisites=best.get("prerequisites", []),
        source="mock",
    )


# --------------------------------------------------------------------------- #
# Public entry point
# --------------------------------------------------------------------------- #
def fetch_curriculum(topic: str, key_stage: int, *, subject: str = "maths",
                     year_group: int | None = None,
                     verbose: bool = False) -> CurriculumPacket:
    """Fetch curriculum data for the target week's topic, grounded in the UK
    National Curriculum.

    Retrieval tiers, in order of preference:
      1. Oak National Academy bulk dataset (local) - `python main.py setup`
         (enriched with live-API lesson transcripts + real quiz questions when
         OAK_API_KEY is set - see agents/oak_api.py, applied in crew.py)
      2. Bundled sample data (data/oak_mock_curriculum.json)
      3. Legacy live "find unit" fetch (kept for environments with no bulk file)
    """
    subject = subject.lower().strip()

    # --- Tier 1: local Oak bulk dataset (the structural backbone) ----------
    if config.oak_local_available():
        try:
            from agents import oak_local  # lazy: breaks the import cycle

            packet = oak_local.build_packet(subject, key_stage, topic,
                                            year_group=year_group, verbose=verbose)
            if packet and (packet.learning_objectives or packet.misconceptions):
                return packet
            if verbose:
                print("[ingestor] Local dataset had no match; using bundled sample data.")
        except Exception as exc:  # noqa: BLE001
            if verbose:
                print(f"[ingestor] Local dataset error ({exc!r}); using bundled sample data.")

    # --- Tier 3: legacy live "find unit" (only if there is no bulk file) ----
    client = OakClient()
    if client.enabled and not config.oak_local_available():
        try:
            unit = client.find_unit(subject=subject, key_stage=key_stage,
                                    year_group=year_group, topic=topic)
            if unit:
                packet = _normalise_oak_unit(unit, client, topic=topic, subject=subject,
                                             key_stage=key_stage, year_group=year_group)
                if packet.learning_objectives or packet.misconceptions:
                    if not packet.baseline_questions:
                        packet.baseline_questions = _fallback_baseline(
                            topic, subject, key_stage, year_group)
                    return packet
        except Exception:  # noqa: BLE001
            pass

    # --- Tier 4: bundled sample data ------------------------------------
    packet = _retrieve_from_mock(topic=topic, subject=subject, key_stage=key_stage,
                                 year_group=year_group)
    if verbose:
        note = "" if config.oak_local_available() else "  (run `python main.py setup` for full coverage)"
        print(f"[ingestor] Bundled sample data: {packet.unit_title}{note}")
    return packet


def _fallback_baseline(topic: str, subject: str, key_stage: int,
                       year_group: int | None) -> list[str]:
    return _retrieve_from_mock(topic=topic, subject=subject, key_stage=key_stage,
                               year_group=year_group).baseline_questions


if __name__ == "__main__":  # quick manual check
    p = fetch_curriculum("fractions", 2, subject="maths", year_group=3, verbose=True)
    print(p.summary_for_prompt())
