"""
OpenAlex ingestion for University of Mississippi works.

Pulls works where at least one authorship is affiliated with UM
(ROR 02teq1165) and writes flat CSVs that the precompute step consumes.

Storage is intentionally a flat-file write so it can be swapped later for
Postgres without changing the rest of the pipeline.
"""

import argparse
import csv
import os
import time
from typing import Dict, Iterable, List, Set

import requests

UM_ROR = "02teq1165"
UM_ROR_URL = f"https://ror.org/{UM_ROR}"
OPENALEX_WORKS = "https://api.openalex.org/works"

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data")
AUTHORS_CSV = os.path.join(DATA_DIR, "authors.csv")
WORKS_CSV = os.path.join(DATA_DIR, "works.csv")
AUTHORSHIPS_CSV = os.path.join(DATA_DIR, "authorships.csv")


def fetch_works(per_page: int, max_pages: int, from_year: int, mailto: str | None) -> Iterable[dict]:
    """Yield OpenAlex work objects filtered to UM, using cursor pagination."""
    cursor = "*"
    page = 0
    base_filter = f"authorships.institutions.ror:{UM_ROR}"
    if from_year:
        base_filter += f",from_publication_date:{from_year}-01-01"

    while page < max_pages and cursor:
        params = {
            "filter": base_filter,
            "per-page": per_page,
            "cursor": cursor,
        }
        if mailto:
            params["mailto"] = mailto

        resp = requests.get(OPENALEX_WORKS, params=params, timeout=60)
        resp.raise_for_status()
        payload = resp.json()
        results = payload.get("results", []) or []
        if not results:
            break

        for work in results:
            yield work

        cursor = (payload.get("meta") or {}).get("next_cursor")
        page += 1
        # Be polite to the API
        time.sleep(0.2)


def normalize_country(institution: dict | None) -> str:
    code = (institution or {}).get("country_code")
    if not code:
        return "Unknown"
    return str(code).upper()


def is_um_institution(institution: dict | None) -> bool:
    ror = (institution or {}).get("ror") or ""
    return UM_ROR in ror


def run(per_page: int, max_pages: int, from_year: int, mailto: str | None) -> None:
    os.makedirs(DATA_DIR, exist_ok=True)

    authors: Dict[str, Dict] = {}
    works: Dict[str, Dict] = {}
    authorships: List[Dict] = []
    seen_links: Set[tuple] = set()

    for work in fetch_works(per_page, max_pages, from_year, mailto):
        work_id = work.get("id")
        if not work_id:
            continue

        works[work_id] = {
            "work_id": work_id,
            "title": (work.get("title") or "").replace("\n", " ").strip(),
            "publication_year": work.get("publication_year") or "",
        }

        for authorship in work.get("authorships") or []:
            author = authorship.get("author") or {}
            author_id = author.get("id")
            if not author_id:
                continue
            display_name = author.get("display_name") or ""
            position = authorship.get("author_position") or ""

            # First listed institution becomes the author's snapshot affiliation.
            insts = authorship.get("institutions") or []
            primary = insts[0] if insts else {}
            inst_name = (primary.get("display_name") or "").strip() or "Unknown"
            country = normalize_country(primary)
            is_um = any(is_um_institution(i) for i in insts)

            existing = authors.get(author_id)
            # Prefer the UM-flagged record if we see this author on a UM-tagged authorship.
            if existing is None or (is_um and not existing.get("is_um_author")):
                authors[author_id] = {
                    "author_id": author_id,
                    "display_name": display_name,
                    "institution_name": inst_name,
                    "country": country,
                    "is_um_author": "true" if is_um else "false",
                }

            link = (work_id, author_id)
            if link not in seen_links:
                seen_links.add(link)
                authorships.append({
                    "work_id": work_id,
                    "author_id": author_id,
                    "author_position": position,
                })

    save_csv(
        AUTHORS_CSV,
        ["author_id", "display_name", "institution_name", "country", "is_um_author"],
        list(authors.values()),
    )
    save_csv(
        WORKS_CSV,
        ["work_id", "title", "publication_year"],
        list(works.values()),
    )
    save_csv(
        AUTHORSHIPS_CSV,
        ["work_id", "author_id", "author_position"],
        authorships,
    )

    um_count = sum(1 for a in authors.values() if a["is_um_author"] == "true")
    print("Ingest complete.")
    print(f"  works:        {len(works)}")
    print(f"  authors:      {len(authors)}")
    print(f"  UM authors:   {um_count}")
    print(f"  authorships:  {len(authorships)}")
    print(f"  saved to:     {DATA_DIR}")


def save_csv(path: str, fields: List[str], rows: List[Dict]) -> None:
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({k: row.get(k, "") for k in fields})


def main() -> None:
    parser = argparse.ArgumentParser(description="Ingest UM works from OpenAlex into local CSVs.")
    parser.add_argument("--per-page", type=int, default=100, help="Results per page (max 200).")
    parser.add_argument("--max-pages", type=int, default=20, help="Max number of pages to fetch.")
    parser.add_argument("--from-year", type=int, default=2018, help="Earliest publication year.")
    parser.add_argument(
        "--mailto",
        type=str,
        default=os.environ.get("OPENALEX_MAILTO"),
        help="Email for OpenAlex polite pool (recommended).",
    )
    args = parser.parse_args()
    run(args.per_page, args.max_pages, args.from_year, args.mailto)


if __name__ == "__main__":
    main()
