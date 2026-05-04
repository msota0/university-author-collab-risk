"""
Build a co-authorship graph JSON from the ingested CSVs.

Two authors are connected if they share at least one work.
Edge weight is the count of shared works; ``shared_works`` lists the IDs.
"""

import json
import os
from collections import defaultdict
from itertools import combinations
from typing import Dict, List, Tuple

import pandas as pd

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data")
AUTHORS_CSV = os.path.join(DATA_DIR, "authors.csv")
WORKS_CSV = os.path.join(DATA_DIR, "works.csv")
AUTHORSHIPS_CSV = os.path.join(DATA_DIR, "authorships.csv")
GRAPH_JSON = os.path.join(DATA_DIR, "collaboration_graph.json")


def _load_inputs():
    missing = [p for p in (AUTHORS_CSV, WORKS_CSV, AUTHORSHIPS_CSV) if not os.path.exists(p)]
    if missing:
        raise FileNotFoundError(
            "Missing input CSVs: " + ", ".join(missing) + ". Run ingest_openalex.py first."
        )
    authors = pd.read_csv(AUTHORS_CSV)
    works = pd.read_csv(WORKS_CSV)
    auths = pd.read_csv(AUTHORSHIPS_CSV)
    return authors, works, auths


def build() -> None:
    authors_df, works_df, auths_df = _load_inputs()

    nodes: List[Dict] = []
    for _, a in authors_df.iterrows():
        nodes.append({
            "id": str(a["author_id"]),
            "label": str(a.get("display_name", "")),
            "institution": str(a.get("institution_name", "")),
            "country": str(a.get("country", "Unknown")),
            "is_um_author": str(a.get("is_um_author", "false")).lower() == "true",
        })

    # Group authors by work for pairwise edge construction.
    work_to_authors: Dict[str, List[str]] = defaultdict(list)
    for _, row in auths_df.iterrows():
        work_to_authors[str(row["work_id"])].append(str(row["author_id"]))

    edge_map: Dict[Tuple[str, str], Dict] = {}
    for work_id, author_ids in work_to_authors.items():
        unique_ids = sorted(set(author_ids))
        for a, b in combinations(unique_ids, 2):
            key = (a, b)
            edge = edge_map.get(key)
            if edge is None:
                edge = {"source": a, "target": b, "weight": 0, "shared_works": []}
                edge_map[key] = edge
            edge["weight"] += 1
            edge["shared_works"].append(work_id)

    edges = list(edge_map.values())

    graph = {
        "metadata": {
            "total_nodes": len(nodes),
            "total_edges": len(edges),
            "total_works": int(works_df.shape[0]),
        },
        "nodes": nodes,
        "edges": edges,
    }

    with open(GRAPH_JSON, "w", encoding="utf-8") as f:
        json.dump(graph, f)

    print("Graph precomputed.")
    print(f"  nodes:    {graph['metadata']['total_nodes']}")
    print(f"  edges:    {graph['metadata']['total_edges']}")
    print(f"  works:    {graph['metadata']['total_works']}")
    print(f"  saved to: {GRAPH_JSON}")


if __name__ == "__main__":
    build()
