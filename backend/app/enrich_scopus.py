"""
Scopus enrichment for nodes that appear in the precomputed risk graph.

For each direct collaborator and flagged-second-hop author in
collaboration_graph.json, this script:

  1. Resolves the OpenAlex author to a Scopus author ID
       - prefers ORCID lookup (pulled from OpenAlex /authors)
       - falls back to author name + last-known affiliation
  2. Pulls the Scopus author detail (current affiliation, h-index,
     document count, full affiliation history)
  3. Writes a flat row per author to backend/data/scopus_enrichment.csv
     and caches the raw JSON in backend/data/cache/scopus/<id>.json

Run AFTER precompute_graph.py:

    python -m app.enrich_scopus [--limit N] [--only-flagged]

Idempotent — already-cached authors are skipped unless --refresh is passed.
"""

from __future__ import annotations

import argparse
import csv
import json
import time
from datetime import datetime, timezone
from typing import Dict, Iterable, List, Optional

import requests

from . import config
from ._http import RateLimiter, polite_request
from .data_loader import load_graph

SCOPUS_SEARCH = "https://api.elsevier.com/content/search/author"
SCOPUS_AUTHOR = "https://api.elsevier.com/content/author/author_id/{aid}"
OPENALEX_AUTHOR = "https://api.openalex.org/authors/{aid}"

# Scopus' burst guideline is ~9 req/s on search and lower on retrieval. We
# stay at ~3 req/s to leave headroom for the inevitable retry. The weekly
# quota (~20k requests on most contracts) is a separate concern — use
# --only-flagged to keep batches small.
_SCOPUS_LIMITER = RateLimiter(min_interval_seconds=0.35)
# OpenAlex polite pool: 10 req/s. Half that to share with other tooling.
_OPENALEX_LIMITER = RateLimiter(min_interval_seconds=0.2)


def _short_id(author_id: str) -> str:
    if not author_id:
        return ""
    return author_id.rsplit("/", 1)[-1]


def _scopus_headers() -> Dict[str, str]:
    headers = {
        "X-ELS-APIKey": config.SCOPUS_API_KEY,
        "Accept": "application/json",
    }
    if config.SCOPUS_INST_TOKEN:
        headers["X-ELS-Insttoken"] = config.SCOPUS_INST_TOKEN
    return headers


def _cache_path(openalex_id: str):
    config.SCOPUS_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    return config.SCOPUS_CACHE_DIR / f"{_short_id(openalex_id)}.json"


def _fetch_orcid(openalex_id: str) -> Optional[str]:
    """Pull the ORCID (if any) for an OpenAlex author."""
    short = _short_id(openalex_id)
    if not short:
        return None
    params = {}
    if config.OPENALEX_MAILTO:
        params["mailto"] = config.OPENALEX_MAILTO
    try:
        r = polite_request(
            "GET",
            OPENALEX_AUTHOR.format(aid=short),
            params=params,
            limiter=_OPENALEX_LIMITER,
            timeout=30,
        )
    except requests.RequestException:
        return None
    if r.status_code != 200:
        return None
    orcid = (r.json().get("orcid") or "")
    return orcid.rsplit("/", 1)[-1] if orcid else None


def _scopus_search(query: str) -> Optional[str]:
    """Return the first matching Scopus author ID for a query, or None."""
    try:
        r = polite_request(
            "GET",
            SCOPUS_SEARCH,
            params={"query": query, "count": 1},
            headers=_scopus_headers(),
            limiter=_SCOPUS_LIMITER,
            timeout=30,
        )
    except requests.RequestException:
        return None
    if r.status_code != 200:
        return None
    results = r.json().get("search-results", {}).get("entry", []) or []
    if not results:
        return None
    ident = results[0].get("dc:identifier", "")
    if ident.startswith("AUTHOR_ID:"):
        return ident.split(":", 1)[1]
    return None


def resolve_scopus_id(name: str, affiliation: str, openalex_id: str) -> Optional[str]:
    """Best-effort OpenAlex → Scopus author ID mapping."""
    orcid = _fetch_orcid(openalex_id)
    if orcid:
        sid = _scopus_search(f"ORCID({orcid})")
        if sid:
            return sid

    parts = (name or "").split()
    if len(parts) < 2:
        return None
    first, last = parts[0], parts[-1]
    q = f'AUTHFIRST("{first}") AND AUTHLASTNAME("{last}")'
    if affiliation:
        q += f' AND AFFIL("{affiliation}")'
    return _scopus_search(q)


def fetch_scopus_detail(scopus_id: str) -> Optional[Dict]:
    try:
        r = polite_request(
            "GET",
            SCOPUS_AUTHOR.format(aid=scopus_id),
            params={"view": "ENHANCED"},
            headers=_scopus_headers(),
            limiter=_SCOPUS_LIMITER,
            timeout=30,
        )
    except requests.RequestException:
        return None
    if r.status_code != 200:
        return None
    return r.json()


def _flatten_affil_history(detail: Dict) -> List[Dict[str, str]]:
    """Pull the affiliation-history list out of a Scopus author response."""
    try:
        author = detail["author-retrieval-response"][0]
    except (KeyError, IndexError, TypeError):
        return []
    hist_raw = author.get("affiliation-history") or {}
    items = hist_raw.get("affiliation") if isinstance(hist_raw, dict) else hist_raw
    if not items:
        return []
    if isinstance(items, dict):
        items = [items]
    out: List[Dict[str, str]] = []
    for a in items:
        out.append(
            {
                "name": a.get("affiliation-name") or a.get("ip-doc", {}).get("preferred-name", {}).get("$") or "",
                "country": a.get("affiliation-country") or "",
                "city": a.get("affiliation-city") or "",
                "id": a.get("@id") or a.get("@affiliation-id") or "",
            }
        )
    return out


def _flatten_summary(detail: Dict) -> Dict[str, str]:
    try:
        author = detail["author-retrieval-response"][0]
    except (KeyError, IndexError, TypeError):
        return {}
    coredata = author.get("coredata", {}) or {}
    current = author.get("affiliation-current", {}) or {}
    if isinstance(current, dict):
        cur_name = current.get("affiliation-name") or ""
        cur_country = current.get("affiliation-country") or ""
    else:
        cur_name = cur_country = ""
    return {
        "h_index": str(author.get("h-index", "") or ""),
        "document_count": str(coredata.get("document-count", "") or ""),
        "current_affiliation": cur_name,
        "current_affiliation_country": cur_country,
    }


def collect_target_nodes(only_flagged: bool) -> List[Dict]:
    """Pull the (author_id, label, country, institution) tuples we want to enrich."""
    graph = load_graph()
    nodes = graph.get("nodes", []) or []
    targets: List[Dict] = []
    seen = set()
    for n in nodes:
        nid = str(n.get("id", ""))
        if not nid or nid in seen:
            continue
        # We enrich anyone who could surface as a flagged hop or its bridge.
        # Skipping the seed (UM author) saves quota.
        if n.get("is_um_author"):
            continue
        country = (n.get("country") or "").upper()
        if only_flagged and not n.get("flagged_second_hop"):
            # The full graph nodes don't carry node_type yet, so fall back to
            # whether the country is on the review list — caller can pass
            # --only-flagged once review_countries.csv exists.
            from .data_loader import load_review_countries

            review = load_review_countries()
            if country not in review:
                continue
        seen.add(nid)
        targets.append(
            {
                "author_id": nid,
                "label": n.get("label", ""),
                "country": n.get("country", ""),
                "institution": n.get("institution", ""),
            }
        )
    return targets


def run(limit: Optional[int], only_flagged: bool, refresh: bool) -> None:
    if not config.has_scopus():
        raise SystemExit(
            "SCOPUS_API_KEY not set. Add it to backend/.env (see .env.example)."
        )

    targets = collect_target_nodes(only_flagged=only_flagged)
    if limit:
        targets = targets[:limit]
    print(f"Enriching {len(targets)} authors via Scopus...")

    config.SCOPUS_ENRICHMENT_CSV.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "author_id",
        "scopus_id",
        "h_index",
        "document_count",
        "current_affiliation",
        "current_affiliation_country",
        "affiliation_history",
        "updated_at",
    ]

    rows: List[Dict[str, str]] = []
    failed = 0
    for i, t in enumerate(targets, 1):
        cache_file = _cache_path(t["author_id"])
        if cache_file.exists() and not refresh:
            try:
                cached = json.loads(cache_file.read_text(encoding="utf-8"))
                rows.append(cached["row"])
                continue
            except Exception:
                pass

        sid = resolve_scopus_id(t["label"], t["institution"], t["author_id"])
        if not sid:
            failed += 1
            if i % 25 == 0:
                print(f"  ...{i}/{len(targets)} (no Scopus match for {t['label']})")
            continue

        detail = fetch_scopus_detail(sid)
        if detail is None:
            failed += 1
            continue

        summary = _flatten_summary(detail)
        history = _flatten_affil_history(detail)
        history_str = "; ".join(
            f"{h['name']} ({h['country']})" if h.get("country") else h.get("name", "")
            for h in history
            if h.get("name")
        )
        row = {
            "author_id": t["author_id"],
            "scopus_id": sid,
            "h_index": summary.get("h_index", ""),
            "document_count": summary.get("document_count", ""),
            "current_affiliation": summary.get("current_affiliation", ""),
            "current_affiliation_country": summary.get(
                "current_affiliation_country", ""
            ),
            "affiliation_history": history_str,
            "updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }
        rows.append(row)
        cache_file.write_text(
            json.dumps({"row": row, "raw": detail}, ensure_ascii=False),
            encoding="utf-8",
        )

        if i % 10 == 0:
            print(f"  ...{i}/{len(targets)} processed")

    with open(config.SCOPUS_ENRICHMENT_CSV, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for r in rows:
            writer.writerow(r)

    print("Scopus enrichment complete.")
    print(f"  rows written: {len(rows)}")
    print(f"  unmatched:    {failed}")
    print(
        "Now restart the backend (or POST /admin/reload) so the new enrichment is picked up."
    )


def main():
    p = argparse.ArgumentParser(description="Enrich graph authors with Scopus metadata.")
    p.add_argument("--limit", type=int, default=None, help="Cap how many authors to enrich.")
    p.add_argument(
        "--only-flagged",
        action="store_true",
        help="Restrict to authors whose country is on review_countries.csv.",
    )
    p.add_argument(
        "--refresh",
        action="store_true",
        help="Ignore cached lookups and re-fetch every author.",
    )
    args = p.parse_args()
    run(args.limit, args.only_flagged, args.refresh)


if __name__ == "__main__":
    main()
