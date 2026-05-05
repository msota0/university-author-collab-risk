"""
On-demand one-hop neighbourhood lookup against the OpenAlex API.

Used as a fallback in /review/expand when the requested author is *not* in
the precomputed collaboration_graph.json. This makes the tool useful for
ad-hoc reviewer workflows where someone pastes an arbitrary OpenAlex ID
rather than choosing from the UM dataset.

The response shape matches graph_builder.expand_neighbors so the frontend
doesn't need to branch on data source.

Performance:
  - Each lookup makes 1 author call + N works pages (200 results/page).
  - Most authors fit in 1–3 pages → ~1–3 OpenAlex calls.
  - We cache full results per-process so repeated expansions of the same
    author don't re-hit OpenAlex within a single backend lifetime.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Dict, List, Optional

import requests

from . import config
from ._http import RateLimiter, polite_request
from .data_loader import load_review_countries

OPENALEX_AUTHOR_URL = "https://api.openalex.org/authors/{aid}"
OPENALEX_WORKS_URL = "https://api.openalex.org/works"
_OPENALEX_LIMITER = RateLimiter(min_interval_seconds=0.15)

# Per-process cache; persistent caching would need invalidation rules we
# don't want to invent right now.
_CACHE: Dict[str, Dict] = {}


def _short_id(author_id: str) -> str:
    if not author_id:
        return ""
    return author_id.rsplit("/", 1)[-1]


def _polite_params() -> Dict[str, str]:
    p: Dict[str, str] = {}
    if config.OPENALEX_MAILTO:
        p["mailto"] = config.OPENALEX_MAILTO
    return p


def _fetch_author(short: str) -> Optional[Dict]:
    try:
        r = polite_request(
            "GET",
            OPENALEX_AUTHOR_URL.format(aid=short),
            params=_polite_params(),
            limiter=_OPENALEX_LIMITER,
            timeout=30,
        )
    except requests.RequestException:
        return None
    if r.status_code != 200:
        return None
    return r.json()


def _fetch_all_works(short: str, max_works: int = 400) -> List[Dict]:
    """Fetch up to `max_works` works for this author, paginated."""
    out: List[Dict] = []
    cursor: Optional[str] = "*"
    while cursor and len(out) < max_works:
        params = {
            **_polite_params(),
            "filter": f"author.id:{short}",
            "per-page": 200,
            "cursor": cursor,
            "select": "id,authorships,publication_year,title",
        }
        try:
            r = polite_request(
                "GET",
                OPENALEX_WORKS_URL,
                params=params,
                limiter=_OPENALEX_LIMITER,
                timeout=60,
            )
        except requests.RequestException:
            break
        if r.status_code != 200:
            break
        data = r.json()
        results = data.get("results") or []
        if not results:
            break
        out.extend(results)
        cursor = (data.get("meta") or {}).get("next_cursor")
    return out[:max_works]


def _author_to_seed(author: Dict) -> Dict:
    inst = (author.get("last_known_institution") or {}) or {}
    return {
        "id": author.get("id"),
        "label": author.get("display_name") or "",
        "country": (inst.get("country_code") or "Unknown").upper(),
        "institution": inst.get("display_name") or "",
        "is_um_author": False,
        "node_type": "seed",
    }


def _empty(author_id: str) -> Dict:
    return {
        "author_id": author_id,
        "seed": None,
        "neighbor_count": 0,
        "shown_count": 0,
        "nodes": [],
        "edges": [],
        "live": True,
    }


def _trim(full: Dict, limit: int) -> Dict:
    """Return a view of the cached response trimmed to the first `limit` nodes."""
    nodes = (full.get("nodes") or [])[:limit]
    keep = {n["id"] for n in nodes}
    edges = [e for e in (full.get("edges") or []) if e["target"] in keep]
    return {**full, "nodes": nodes, "edges": edges, "shown_count": len(nodes)}


def fetch_live_neighborhood(author_id: str, limit: int = 75) -> Dict:
    """Build a one-hop graph slice for `author_id` from OpenAlex on demand."""
    short = _short_id(author_id)
    if not short:
        return _empty(author_id)

    cached = _CACHE.get(short)
    if cached:
        return _trim(cached, limit)

    author = _fetch_author(short)
    if not author:
        return _empty(author_id)

    seed_info = _author_to_seed(author)
    works = _fetch_all_works(short)

    coauthor_weight: Dict[str, int] = defaultdict(int)
    coauthor_info: Dict[str, Dict] = {}
    for w in works:
        for ship in w.get("authorships") or []:
            a = ship.get("author") or {}
            aid = a.get("id")
            if not aid or _short_id(aid) == short:
                continue
            coauthor_weight[aid] += 1
            if aid not in coauthor_info:
                # First-seen institution wins; OpenAlex listings vary by paper.
                first_inst = (ship.get("institutions") or [{}])[0] or {}
                coauthor_info[aid] = {
                    "id": aid,
                    "label": a.get("display_name") or "",
                    "country": (first_inst.get("country_code") or "Unknown").upper(),
                    "institution": first_inst.get("display_name") or "",
                    "is_um_author": False,
                }

    review_countries = load_review_countries()
    sorted_pairs = sorted(
        coauthor_weight.items(), key=lambda kv: kv[1], reverse=True
    )

    out_nodes: List[Dict] = []
    out_edges: List[Dict] = []
    for aid, weight in sorted_pairs:
        info = coauthor_info[aid]
        country = (info["country"] or "").upper()
        is_flagged = country in review_countries
        node = {**info, "node_type": "flagged_second_hop" if is_flagged else "neighbor"}
        if is_flagged:
            node["flag_reason"] = review_countries[country].get("flag_reason", "")
            node["risk_level"] = review_countries[country].get("risk_level", "")
        out_nodes.append(node)
        out_edges.append(
            {
                "source": author_id,
                "target": aid,
                "weight": weight,
                "edge_type": "indirect_risk_path" if is_flagged else "neighbor",
            }
        )

    full = {
        "author_id": author_id,
        "seed": seed_info,
        "neighbor_count": len(coauthor_weight),
        "shown_count": len(out_nodes),
        "nodes": out_nodes,
        "edges": out_edges,
        "live": True,
    }
    _CACHE[short] = full
    return _trim(full, limit)
