"""Central configuration loaded from the environment (.env)."""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

ROOT = Path(__file__).resolve().parent


def _bool(name: str, default: bool = False) -> bool:
    return os.getenv(name, str(default)).strip().lower() in {"1", "true", "yes", "on"}


def _key(name: str) -> str:
    """Read a secret, treating .env.example placeholders as 'unset'."""
    val = os.getenv(name, "").strip()
    if not val or "your_" in val.lower() or val.lower().endswith("_here"):
        return ""
    return val


# --- LLM -------------------------------------------------------------------
GEMINI_API_KEY = _key("GEMINI_API_KEY")
# gemini-1.5-pro was retired by Google; "flash-latest" tracks the current fast model.
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-flash-latest").strip()

# --- Oak National Academy API -------------------------------------------------
OAK_API_KEY = _key("OAK_API_KEY")
OAK_API_BASE_URL = os.getenv("OAK_API_BASE_URL", "https://open-api.thenational.academy").rstrip("/")
OAK_API_AUTH_SCHEME = os.getenv("OAK_API_AUTH_SCHEME", "bearer").strip().lower()

# --- Business ---------------------------------------------------------------
BUSINESS_NAME = os.getenv("BUSINESS_NAME", "Bright Steps Tutoring").strip()
TUTOR_NAME = os.getenv("TUTOR_NAME", "").strip()

# --- Behaviour --------------------------------------------------------------
OFFLINE_MODE = _bool("OFFLINE_MODE", False)

# --- Paths ----------------------------------------------------------------
DB_PATH = (ROOT / os.getenv("DB_PATH", "database/tutoring.db")).resolve()
SCHEMA_PATH = ROOT / "database" / "schema.sql"
OUTPUT_DIR = (ROOT / os.getenv("OUTPUT_DIR", "outputs")).resolve()
UPLOAD_DIR = (ROOT / os.getenv("UPLOAD_DIR", "uploads")).resolve()
TEMPLATE_DIR = ROOT / "templates"
MOCK_CURRICULUM_PATH = ROOT / "data" / "oak_mock_curriculum.json"
# Oak National Academy bulk dataset (SQLite) - downloaded by `main.py setup`
OAK_LOCAL_DB_PATH = (ROOT / os.getenv("OAK_LOCAL_DB_PATH", "data/oak-curriculum.sqlite")).resolve()


def llm_available() -> bool:
    return bool(GEMINI_API_KEY)


def oak_api_available() -> bool:
    return bool(OAK_API_KEY) and not OFFLINE_MODE


def oak_local_available() -> bool:
    return OAK_LOCAL_DB_PATH.is_file() and OAK_LOCAL_DB_PATH.stat().st_size > 1_000_000


def curriculum_source() -> str:
    if oak_local_available():
        base = "Oak National Academy curriculum (full dataset)"
        return base + " + live API depth (transcripts, real quiz questions)" if oak_api_available() else base
    if oak_api_available():
        return "Oak National Academy API (live)"
    return "bundled sample data (limited coverage)"


def summary() -> str:
    return (
        f"Gemini model:   {GEMINI_MODEL}  (key {'set' if GEMINI_API_KEY else 'MISSING -> deterministic fallback'})\n"
        f"Oak API:        {'key set' if OAK_API_KEY else 'no key'}  ({OAK_API_BASE_URL})\n"
        f"Oak local DB:   {'found' if oak_local_available() else 'MISSING -> run: python main.py setup'}  ({OAK_LOCAL_DB_PATH})\n"
        f"Curriculum src: {curriculum_source()}\n"
        f"Offline mode:   {OFFLINE_MODE}\n"
        f"Database:       {DB_PATH}\n"
        f"Output dir:     {OUTPUT_DIR}"
    )
