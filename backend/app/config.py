"""
Environment + configuration. Reads backend/.env if present and exposes
typed accessors so the rest of the app doesn't sprinkle os.environ calls.
"""

import os
from pathlib import Path

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover - dotenv is in requirements but be defensive
    load_dotenv = None  # type: ignore

BACKEND_ROOT = Path(__file__).resolve().parent.parent
ENV_FILE = BACKEND_ROOT / ".env"

if load_dotenv and ENV_FILE.exists():
    load_dotenv(ENV_FILE)


def _env(key: str, default: str = "") -> str:
    val = os.environ.get(key, default)
    return (val or "").strip()


# ─── Credentials ────────────────────────────────────────────────────────────
SCOPUS_API_KEY = _env("SCOPUS_API_KEY")
SCOPUS_INST_TOKEN = _env("SCOPUS_INST_TOKEN")

DIMENSIONS_API_KEY = _env("DIMENSIONS_API_KEY")
DIMENSIONS_USERNAME = _env("DIMENSIONS_USERNAME")
DIMENSIONS_PASSWORD = _env("DIMENSIONS_PASSWORD")

OPENALEX_MAILTO = _env("OPENALEX_MAILTO")

# ─── Paths ──────────────────────────────────────────────────────────────────
DATA_DIR = BACKEND_ROOT / "data"
CACHE_DIR = Path(_env("ENRICHMENT_CACHE_DIR") or str(DATA_DIR / "cache"))
SCOPUS_CACHE_DIR = CACHE_DIR / "scopus"
DIMENSIONS_CACHE_DIR = CACHE_DIR / "dimensions"

SCOPUS_ENRICHMENT_CSV = DATA_DIR / "scopus_enrichment.csv"
DIMENSIONS_ENRICHMENT_CSV = DATA_DIR / "dimensions_enrichment.csv"


def has_scopus() -> bool:
    return bool(SCOPUS_API_KEY)


def has_dimensions() -> bool:
    return bool(DIMENSIONS_API_KEY) or bool(
        DIMENSIONS_USERNAME and DIMENSIONS_PASSWORD
    )
