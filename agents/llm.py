"""Gemini access, shared by every agent.

Uses the current ``google-genai`` SDK (the older ``google-generativeai`` package
is deprecated).  Two entry points:

* ``generate(prompt, system=..., as_json=True)`` - a direct, framework-free call
  used by the fallback pipeline and by agents that just need one completion.
* ``crew_llm()`` - a CrewAI-compatible LLM object, used when the full CrewAI
  pipeline is assembled.

If ``GEMINI_API_KEY`` is missing, ``generate`` raises ``LLMUnavailable`` so the
caller can fall back to deterministic output.  This keeps the whole system
runnable end-to-end with no keys for demos and testing.
"""
from __future__ import annotations

import json as _json
import re
from typing import Any, Optional

import config


class LLMUnavailable(RuntimeError):
    """Raised when no Gemini API key is configured."""


_client = None


def _get_client():
    global _client
    if _client is not None:
        return _client
    if not config.GEMINI_API_KEY:
        raise LLMUnavailable("GEMINI_API_KEY is not set")
    from google import genai

    _client = genai.Client(api_key=config.GEMINI_API_KEY)
    return _client


_RETRY_STATUSES = ("503", "429", "UNAVAILABLE", "RESOURCE_EXHAUSTED", "overloaded", "high demand")

# If the configured model is out of daily free-tier quota, try these (each has its
# own quota bucket) before giving up and using the deterministic fallback.
_FALLBACK_MODELS = ["gemini-3.5-flash", "gemini-flash-lite-latest", "gemini-2.5-flash"]


def generate(
    prompt: str,
    *,
    system: Optional[str] = None,
    temperature: float = 0.4,
    as_json: bool = False,
    retries: int = 3,
    images: Optional[list[tuple[bytes, str]]] = None,
) -> str:
    """Single-shot Gemini completion, with backoff on transient overload.

    ``images`` is an optional list of ``(bytes, mime_type)`` - Gemini is
    multimodal, so this is how a photo of a pupil's worksheet is passed in.
    Returns raw text.
    """
    import time

    from google.genai import types

    client = _get_client()
    cfg = types.GenerateContentConfig(
        system_instruction=system,
        temperature=temperature,
        response_mime_type="application/json" if as_json else "text/plain",
    )

    contents: Any = prompt
    if images:
        contents = [prompt] + [
            types.Part.from_bytes(data=data, mime_type=mime) for data, mime in images
        ]

    models = [config.GEMINI_MODEL] + [m for m in _FALLBACK_MODELS if m != config.GEMINI_MODEL]
    last_exc: Exception | None = None
    for model in models:
        for attempt in range(retries + 1):
            try:
                resp = client.models.generate_content(model=model, contents=contents, config=cfg)
                return (resp.text or "").strip()
            except Exception as exc:  # noqa: BLE001
                last_exc = exc
                msg = str(exc)
                daily = "PerDay" in msg or "per_day" in msg or "free_tier" in msg
                if daily:
                    break  # this model is spent for the day - try the next model
                if attempt < retries and any(s in msg for s in _RETRY_STATUSES):
                    time.sleep(1.5 * (2 ** attempt))  # 1.5s, 3s, 6s
                    continue
                raise
    raise last_exc  # pragma: no cover


def generate_json(prompt: str, *, system: Optional[str] = None, temperature: float = 0.3,
                  images: Optional[list[tuple[bytes, str]]] = None) -> Any:
    """Gemini completion parsed as JSON, tolerant of code fences / prose wrappers."""
    raw = generate(prompt, system=system, temperature=temperature, as_json=True, images=images)
    return _loads_loose(raw)


def _loads_loose(raw: str) -> Any:
    raw = (raw or "").strip()
    try:
        return _json.loads(raw)
    except _json.JSONDecodeError:
        pass
    fenced = re.search(r"```(?:json)?\s*(.*?)```", raw, re.DOTALL)
    if fenced:
        try:
            return _json.loads(fenced.group(1).strip())
        except _json.JSONDecodeError:
            pass
    match = re.search(r"(\{.*\}|\[.*\])", raw, re.DOTALL)
    if match:
        return _json.loads(match.group(1))
    raise ValueError(f"Could not parse JSON from model output:\n{raw[:500]}")


def crew_llm():
    """Return a CrewAI-compatible LLM. Raises LLMUnavailable when no key is set."""
    if not config.GEMINI_API_KEY:
        raise LLMUnavailable("GEMINI_API_KEY is not set")
    from crewai import LLM

    model = config.GEMINI_MODEL
    return LLM(model=f"gemini/{model}", api_key=config.GEMINI_API_KEY, temperature=0.4)


def health_check(live: bool = False) -> tuple[bool, str]:
    """Used by `main.py doctor` and the web app.

    ``live=False`` (default) just reports whether a key is configured - it makes
    no API call, so it doesn't spend request quota.  ``live=True`` sends one tiny
    request to confirm the model actually answers.
    """
    if not config.GEMINI_API_KEY:
        return False, "no GEMINI_API_KEY (deterministic fallback in use)"
    if not live:
        return True, f"key set, model {config.GEMINI_MODEL}"
    try:
        generate("Reply with the single word: ok", temperature=0, retries=0)
        return True, f"{config.GEMINI_MODEL} responding"
    except Exception as exc:  # noqa: BLE001
        return False, f"{config.GEMINI_MODEL}: {str(exc)[:140]}"
