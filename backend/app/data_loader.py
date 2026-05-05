"""
Thin abstraction over the local file storage so the rest of the app does not
depend on CSV/JSON specifics. To migrate to Postgres later, replace the bodies
of these functions with database queries — the function signatures and return
shapes can stay the same.
"""

import json
import os
from functools import lru_cache
from typing import Dict, List

import pandas as pd

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data")
AUTHORS_CSV = os.path.join(DATA_DIR, "authors.csv")
WORKS_CSV = os.path.join(DATA_DIR, "works.csv")
GRAPH_JSON = os.path.join(DATA_DIR, "collaboration_graph.json")
REVIEW_COUNTRIES_CSV = os.path.join(DATA_DIR, "review_countries.csv")
SCOPUS_ENRICHMENT_CSV = os.path.join(DATA_DIR, "scopus_enrichment.csv")
DIMENSIONS_ENRICHMENT_CSV = os.path.join(DATA_DIR, "dimensions_enrichment.csv")


def _exists(path: str) -> bool:
    return os.path.exists(path)


@lru_cache(maxsize=1)
def load_graph() -> Dict:
    if not _exists(GRAPH_JSON):
        return {
            "metadata": {"total_nodes": 0, "total_edges": 0, "total_works": 0},
            "nodes": [],
            "edges": [],
        }
    with open(GRAPH_JSON, "r", encoding="utf-8") as f:
        return json.load(f)


@lru_cache(maxsize=1)
def load_authors_df() -> pd.DataFrame:
    if not _exists(AUTHORS_CSV):
        return pd.DataFrame(
            columns=["author_id", "display_name", "institution_name", "country", "is_um_author"]
        )
    df = pd.read_csv(AUTHORS_CSV)
    df["is_um_author"] = df["is_um_author"].astype(str).str.lower() == "true"
    df["country"] = df["country"].fillna("Unknown")
    df["institution_name"] = df["institution_name"].fillna("Unknown")
    return df


@lru_cache(maxsize=1)
def load_works_df() -> pd.DataFrame:
    if not _exists(WORKS_CSV):
        return pd.DataFrame(columns=["work_id", "title", "publication_year"])
    return pd.read_csv(WORKS_CSV)


@lru_cache(maxsize=1)
def load_review_countries() -> Dict[str, Dict[str, str]]:
    if not _exists(REVIEW_COUNTRIES_CSV):
        return {}
    df = pd.read_csv(REVIEW_COUNTRIES_CSV)
    out: Dict[str, Dict[str, str]] = {}
    for _, row in df.iterrows():
        code = str(row["country"]).upper().strip()
        if not code:
            continue
        out[code] = {
            "flag_reason": str(row.get("flag_reason", "")),
            "risk_level": str(row.get("risk_level", "")),
        }
    return out


@lru_cache(maxsize=1)
def load_scopus_enrichment() -> Dict[str, Dict]:
    """Returns {author_id (OpenAlex URL): {scopus fields}} or {} if file absent."""
    if not _exists(SCOPUS_ENRICHMENT_CSV):
        return {}
    df = pd.read_csv(SCOPUS_ENRICHMENT_CSV).fillna("")
    out: Dict[str, Dict] = {}
    for _, r in df.iterrows():
        aid = str(r.get("author_id", "")).strip()
        if not aid:
            continue
        out[aid] = {
            "scopus_id": str(r.get("scopus_id", "")),
            "h_index": str(r.get("h_index", "")),
            "document_count": str(r.get("document_count", "")),
            "current_affiliation": str(r.get("current_affiliation", "")),
            "current_affiliation_country": str(
                r.get("current_affiliation_country", "")
            ),
            "affiliation_history": str(r.get("affiliation_history", "")),
        }
    return out


@lru_cache(maxsize=1)
def load_dimensions_enrichment() -> Dict[str, Dict]:
    """Returns {author_id (OpenAlex URL): {dimensions fields}} or {} if file absent."""
    if not _exists(DIMENSIONS_ENRICHMENT_CSV):
        return {}
    df = pd.read_csv(DIMENSIONS_ENRICHMENT_CSV).fillna("")
    out: Dict[str, Dict] = {}
    for _, r in df.iterrows():
        aid = str(r.get("author_id", "")).strip()
        if not aid:
            continue
        out[aid] = {
            "dimensions_researcher_id": str(r.get("dimensions_researcher_id", "")),
            "grant_count": int(float(r.get("grant_count") or 0)),
            "patent_count": int(float(r.get("patent_count") or 0)),
            "funder_countries": str(r.get("funder_countries", "")),
            "has_review_country_funding": str(
                r.get("has_review_country_funding", "")
            ).lower()
            == "true",
            "grants": str(r.get("grants", "")),
            "patents": str(r.get("patents", "")),
        }
    return out


def reset_cache() -> None:
    load_graph.cache_clear()
    load_authors_df.cache_clear()
    load_works_df.cache_clear()
    load_review_countries.cache_clear()
    load_scopus_enrichment.cache_clear()
    load_dimensions_enrichment.cache_clear()


def list_um_authors() -> List[Dict]:
    df = load_authors_df()
    if df.empty:
        return []
    um = df[df["is_um_author"]].copy()
    um = um.sort_values("display_name", na_position="last")
    return [
        {
            "author_id": str(r["author_id"]),
            "display_name": str(r["display_name"]),
            "institution_name": str(r.get("institution_name", "")),
            "country": str(r.get("country", "")),
        }
        for _, r in um.iterrows()
    ]
