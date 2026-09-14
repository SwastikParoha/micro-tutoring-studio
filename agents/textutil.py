"""Small text helpers shared across the fallback (no-LLM) generators."""
from __future__ import annotations

import re

_LEAD = re.compile(r"^\s*(?:i\s+can|i\s+know|i\s+am\s+able\s+to|students?\s+can|pupils?\s+can|"
                   r"to\s+be\s+able\s+to|be\s+able\s+to|learn\s+to|understand\s+that)\s+",
                   re.I)


def strip_lead(objective: str) -> str:
    """Remove a leading 'I can' / 'I know' / 'be able to' etc."""
    return _LEAD.sub("", (objective or "").strip()).strip()


def as_action(objective: str) -> str:
    """A clean imperative-ish phrase: 'Describe the early Viking raids ...'."""
    s = strip_lead(objective).rstrip(".")
    return s[:1].upper() + s[1:] if s else s


def to_i_can(objective: str) -> str:
    """Exactly one 'I can ...' prefix, lower-cased verb."""
    s = strip_lead(objective).rstrip(".")
    return f"I can {s[:1].lower() + s[1:]}" if s else "I can do this"


_NARRATION = re.compile(
    r"^(?:some\s+)?(?:pupils?|students?|children|the pupil|the child|they)\s+"
    r"(?:may\s+|might\s+|often\s+|sometimes\s+|will\s+|need(?:s)?\s+to\s+|have\s+to\s+|"
    r"should\s+|must\s+)*"
    r"(?:not\s+)?(?:think|believe|assume|say|state|understand|realise|realize|see|know|"
    r"consider|expect|feel|forget|struggle to|find it hard to|be able to)\s+"
    r"(?:that\s+|the\s+)?",
    re.I,
)


def depersonalise(text: str) -> str:
    """'Pupils may think that X is Y'  ->  'X is Y'."""
    t = re.sub(r"\s+", " ", str(text or "")).strip()
    new = _NARRATION.sub("", t).strip()
    if new and new.lower() != t.lower():
        return new[:1].upper() + new[1:]
    return t


def short(text: str, max_words: int = 12) -> str:
    t = depersonalise(text).strip(" .-")
    t = re.split(r"[:;.\n]| - ", t, maxsplit=1)[0].strip()
    words = t.split()
    return " ".join(words[:max_words]) + ("..." if len(words) > max_words else "")


# Conservative word swaps only - safe substitutions that never break grammar.
# (Rule-based simplification is limited; the LLM path does this properly.)
_PLAIN = [
    (r"\bchronological order\b", "the right order"),
    (r"\bchronologically\b", "in time order"),
    (r"\bidentify\b", "find"),
    (r"\bsignificant(ly)?\b", "important"),
    (r"\butilise\b", "use"),
    (r"\bcommence\b", "start"),
    (r"\bdemonstrate\b", "show"),
    (r"\bproof-?read\b", "check your writing"),
]


def plain(text: str, max_words: int = 20) -> str:
    """Light tidy-up for fallback (no-LLM) text: strip 'I can' lead-ins and swap a
    few hard words. Deliberately conservative - it never truncates a sentence into
    a fragment. Real simplification happens on the LLM path."""
    t = strip_lead(text).rstrip(".")
    for pat, rep in _PLAIN:
        t = re.sub(pat, rep, t, flags=re.I)
    t = re.sub(r"\s{2,}", " ", t).strip(" ,;")
    return t[:1].upper() + t[1:] if t else t
