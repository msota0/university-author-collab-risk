"""
Enrich authors.csv with the canonical display name from OpenAlex's /authors
endpoint. Names that come off `/works` byline data are often initials
("A. Dye"); the per-author record usually has the full "First Last" form.

Run AFTER ingest_openalex.py and BEFORE precompute_graph.py:

    python app/enrich_authors.py [--mailto you@example.com] [--batch 50]

Idempotent — re-runs only update rows whose name actually changed.
"""

import argparse
import csv
import os
import time
from typing import Dict, List

import requests

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data")
AUTHORS_CSV = os.path.join(DATA_DIR, "authors.csv")
OPENALEX_AUTHORS = "https://api.openalex.org/authors"


def _short_id(author_id: str) -> str:
    """Extract the trailing OpenAlex short id (e.g. A5012345) from a URL."""
    if not author_id:
        return ""
    return author_id.rsplit("/", 1)[-1]


def _best_name(author: dict) -> str:
    """Pick the most usable display name available."""
    primary = (author.get("display_name") or "").strip()
    alternatives = [str(n).strip() for n in (author.get("display_name_alternatives") or []) if n]

    # Prefer names with a space (likely "First Last") and the longest length.
    candidates = [n for n in [primary] + alternatives if n]
    if not candidates:
        return ""
    candidates.sort(key=lambda n: (1 if " " in n else 0, len(n)), reverse=True)
    return candidates[0]


def fetch_batch(short_ids: List[str], mailto: str | None) -> List[dict]:
    if not short_ids:
        return []
    params = {
        "filter": f"openalex_id:{'|'.join(short_ids)}",
        "per-page": len(short_ids),
    }
    if mailto:
        params["mailto"] = mailto
    resp = requests.get(OPENALEX_AUTHORS, params=params, timeout=60)
    resp.raise_for_status()
    return resp.json().get("results", []) or []


def run(batch_size: int, mailto: str | None) -> None:
    if not os.path.exists(AUTHORS_CSV):
        raise FileNotFoundError(
            f"{AUTHORS_CSV} not found. Run ingest_openalex.py first."
        )

    with open(AUTHORS_CSV, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        fieldnames = reader.fieldnames or []
        rows = list(reader)

    by_id: Dict[str, Dict] = {r["author_id"]: r for r in rows if r.get("author_id")}
    all_ids = list(by_id.keys())
    print(f"Enriching {len(all_ids)} authors in batches of {batch_size}...")

    updated = 0
    failed = 0
    for i in range(0, len(all_ids), batch_size):
        chunk = all_ids[i : i + batch_size]
        short_ids = [_short_id(x) for x in chunk if _short_id(x)]
        try:
            results = fetch_batch(short_ids, mailto)
        except requests.RequestException as e:
            failed += len(chunk)
            print(f"  batch {i // batch_size + 1}: request failed ({e}); skipping")
            continue

        for author in results:
            aid = author.get("id")
            if not aid or aid not in by_id:
                continue
            name = _best_name(author)
            if name and name != by_id[aid].get("display_name"):
                by_id[aid]["display_name"] = name
                updated += 1

        time.sleep(0.2)
        if (i // batch_size + 1) % 10 == 0:
            print(f"  ...{i + len(chunk)} / {len(all_ids)} processed")

    with open(AUTHORS_CSV, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for r in rows:
            writer.writerow(r)

    print("Enrichment complete.")
    print(f"  updated names:   {updated}")
    if failed:
        print(f"  failed lookups:  {failed}")
    print("Now re-run precompute_graph.py so the new names flow into the graph JSON.")


def main():
    parser = argparse.ArgumentParser(description="Enrich authors.csv with full OpenAlex names.")
    parser.add_argument("--batch", type=int, default=50, help="Authors per /authors request.")
    parser.add_argument(
        "--mailto",
        type=str,
        default=os.environ.get("OPENALEX_MAILTO"),
        help="Email for OpenAlex polite pool.",
    )
    args = parser.parse_args()
    run(args.batch, args.mailto)


if __name__ == "__main__":
    main()
