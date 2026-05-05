"""
Dimensions (Digital Science) enrichment for graph authors.

For each direct collaborator and flagged-second-hop author in the precomputed
graph, this script queries Dimensions for:

  - the matching researcher record (preferring ORCID lookup, falling back to
    name + last-known affiliation)
  - grants where they are an investigator (with funder country)
  - patents where they are an inventor

Output: backend/data/dimensions_enrichment.csv plus per-author JSON cache in
backend/data/cache/dimensions/<id>.json.

Run AFTER precompute_graph.py (and after enrich_scopus.py, optionally):

    python -m app.enrich_dimensions [--limit N] [--only-flagged]

Auth: the script first POSTs to /api/auth using either DIMENSIONS_API_KEY or
DIMENSIONS_USERNAME + DIMENSIONS_PASSWORD, then uses the returned JWT for the
DSL queries.
"""

from __future__ import annotations

import argparse
import csv
import json
import time
from datetime import datetime, timezone
from typing import Dict, List, Optional

import requests

from . import config
from ._http import RateLimiter, polite_request
from .data_loader import load_graph, load_review_countries

DIMENSIONS_AUTH = "https://app.dimensions.ai/api/auth"
DIMENSIONS_DSL = "https://app.dimensions.ai/api/dsl/v2"
OPENALEX_AUTHOR = "https://api.openalex.org/authors/{aid}"

# Dimensions DSL is typically capped around 30 req/min on standard plans, with
# a daily quota on top. 2.5s = 24 req/min keeps us safely under both. The
# rate limiter dwarfs script wall-time, so the per-author cache matters a lot:
# repeat runs after the first only re-fetch missing authors.
_DIMENSIONS_LIMITER = RateLimiter(min_interval_seconds=2.5)
_OPENALEX_LIMITER = RateLimiter(min_interval_seconds=0.2)


def _short_id(author_id: str) -> str:
    if not author_id:
        return ""
    return author_id.rsplit("/", 1)[-1]


def _cache_path(openalex_id: str):
    config.DIMENSIONS_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    return config.DIMENSIONS_CACHE_DIR / f"{_short_id(openalex_id)}.json"


class _Auth:
    """Holds the current Dimensions JWT and refreshes on demand."""

    def __init__(self):
        self.token: Optional[str] = None

    def fetch(self) -> str:
        if config.DIMENSIONS_API_KEY:
            body = {"key": config.DIMENSIONS_API_KEY}
        elif config.DIMENSIONS_USERNAME and config.DIMENSIONS_PASSWORD:
            body = {
                "username": config.DIMENSIONS_USERNAME,
                "password": config.DIMENSIONS_PASSWORD,
            }
        else:
            raise SystemExit(
                "Dimensions credentials not set. Add DIMENSIONS_API_KEY (or "
                "DIMENSIONS_USERNAME + DIMENSIONS_PASSWORD) to backend/.env."
            )
        # Auth itself can be rate limited too — share the DSL limiter so we
        # never burst above the ceiling on token renewals.
        r = polite_request(
            "POST",
            DIMENSIONS_AUTH,
            json=body,
            limiter=_DIMENSIONS_LIMITER,
            timeout=30,
        )
        if r.status_code != 200:
            raise SystemExit(
                f"Dimensions auth failed ({r.status_code}): {r.text[:200]}"
            )
        token = r.json().get("token")
        if not token:
            raise SystemExit("Dimensions auth returned no token.")
        self.token = token
        return token

    def header(self) -> Dict[str, str]:
        if not self.token:
            self.fetch()
        return {
            "Authorization": f"JWT {self.token}",
            "Content-Type": "application/json",
        }


def _dsl(auth: _Auth, query: str, _retry: bool = True) -> Dict:
    """POST a DSL query, refreshing the JWT on 403 and retrying once."""
    r = polite_request(
        "POST",
        DIMENSIONS_DSL,
        data=query.encode("utf-8"),
        headers=auth.header(),
        limiter=_DIMENSIONS_LIMITER,
        timeout=60,
    )
    if r.status_code == 403 and _retry:
        # Token likely expired — refresh and retry exactly once.
        auth.fetch()
        return _dsl(auth, query, _retry=False)
    if r.status_code != 200:
        return {}
    try:
        return r.json()
    except ValueError:
        return {}


def _fetch_orcid(openalex_id: str) -> Optional[str]:
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
    orcid = (r.json().get("orcid") or "").rsplit("/", 1)[-1]
    return orcid or None


def resolve_researcher_id(
    auth: _Auth, name: str, affiliation: str, openalex_id: str
) -> Optional[str]:
    """Map an OpenAlex author to a Dimensions researcher.id (ur.######)."""
    orcid = _fetch_orcid(openalex_id)
    if orcid:
        q = (
            f'search researchers where orcid_id = "{orcid}" '
            f'return researchers[id+first_name+last_name] limit 1'
        )
        data = _dsl(auth, q)
        rs = data.get("researchers") or []
        if rs:
            return rs[0].get("id")

    parts = (name or "").split()
    if len(parts) < 2:
        return None
    first, last = parts[0], parts[-1]
    affil_clause = f' and research_orgs.name ~ "{affiliation}"' if affiliation else ""
    q = (
        f'search researchers where first_name ~ "{first}" '
        f'and last_name = "{last}"{affil_clause} '
        f'return researchers[id+first_name+last_name+research_orgs] limit 1'
    )
    data = _dsl(auth, q)
    rs = data.get("researchers") or []
    return rs[0].get("id") if rs else None


def fetch_grants(auth: _Auth, researcher_id: str) -> List[Dict]:
    q = (
        f'search grants where researchers.id = "{researcher_id}" '
        f'return grants[id+title+start_year+funder_countries+funders] limit 100'
    )
    return _dsl(auth, q).get("grants") or []


def fetch_patents(auth: _Auth, researcher_id: str) -> List[Dict]:
    q = (
        f'search patents where inventors.id = "{researcher_id}" '
        f'return patents[id+title+granted_year+jurisdiction+assignees] limit 50'
    )
    return _dsl(auth, q).get("patents") or []


def _funder_country_codes(grants: List[Dict]) -> List[str]:
    out: List[str] = []
    for g in grants:
        for c in g.get("funder_countries") or []:
            code = (c.get("name") or c.get("id") or "").strip()
            if code:
                out.append(code)
    # Deduplicate while preserving order.
    seen = set()
    dedup: List[str] = []
    for c in out:
        if c not in seen:
            seen.add(c)
            dedup.append(c)
    return dedup


def collect_target_nodes(only_flagged: bool) -> List[Dict]:
    graph = load_graph()
    review = load_review_countries() if only_flagged else {}
    nodes = graph.get("nodes", []) or []
    targets: List[Dict] = []
    seen = set()
    for n in nodes:
        nid = str(n.get("id", ""))
        if not nid or nid in seen:
            continue
        if n.get("is_um_author"):
            continue
        country = (n.get("country") or "").upper()
        if only_flagged and country not in review:
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
    if not config.has_dimensions():
        raise SystemExit(
            "Dimensions credentials not set. Add DIMENSIONS_API_KEY (or username/"
            "password) to backend/.env. See .env.example."
        )

    targets = collect_target_nodes(only_flagged=only_flagged)
    if limit:
        targets = targets[:limit]
    print(f"Enriching {len(targets)} authors via Dimensions...")

    review = load_review_countries()
    review_country_codes = {c.upper() for c in review.keys()}

    auth = _Auth()
    auth.fetch()
    config.DIMENSIONS_ENRICHMENT_CSV.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "author_id",
        "dimensions_researcher_id",
        "grant_count",
        "patent_count",
        "funder_countries",
        "has_review_country_funding",
        "grants",
        "patents",
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

        try:
            rid = resolve_researcher_id(
                auth, t["label"], t["institution"], t["author_id"]
            )
        except requests.RequestException:
            failed += 1
            continue

        if not rid:
            failed += 1
            continue

        try:
            grants = fetch_grants(auth, rid)
        except requests.RequestException:
            grants = []
        try:
            patents = fetch_patents(auth, rid)
        except requests.RequestException:
            patents = []

        funder_countries = _funder_country_codes(grants)
        has_review = any(
            c.upper() in review_country_codes for c in funder_countries
        )

        grants_str = "; ".join(
            f"{g.get('title','')[:80]} [{(g.get('start_year') or '')}]"
            for g in grants[:10]
        )
        patents_str = "; ".join(
            f"{p.get('title','')[:80]} [{(p.get('granted_year') or '')}]"
            for p in patents[:10]
        )

        row = {
            "author_id": t["author_id"],
            "dimensions_researcher_id": rid,
            "grant_count": str(len(grants)),
            "patent_count": str(len(patents)),
            "funder_countries": "; ".join(funder_countries),
            "has_review_country_funding": "true" if has_review else "false",
            "grants": grants_str,
            "patents": patents_str,
            "updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }
        rows.append(row)
        cache_file.write_text(
            json.dumps(
                {"row": row, "grants": grants, "patents": patents},
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

        if i % 10 == 0:
            print(f"  ...{i}/{len(targets)} processed")
        time.sleep(0.2)

    with open(config.DIMENSIONS_ENRICHMENT_CSV, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for r in rows:
            writer.writerow(r)

    print("Dimensions enrichment complete.")
    print(f"  rows written: {len(rows)}")
    print(f"  unmatched:    {failed}")
    print(
        "Now restart the backend (or POST /admin/reload) so the new enrichment is picked up."
    )


def main():
    p = argparse.ArgumentParser(description="Enrich graph authors with Dimensions metadata.")
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
