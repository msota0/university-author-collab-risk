"""
Risk-path computation.

Given a UM seed author, find the slice of the co-authorship graph where:

    seed (UM) --> direct collaborator --> flagged second-hop collaborator
                                          (country in review_countries.csv)

Direct collaborators are only included if they have at least one flagged
second-hop. The result is intentionally narrow so the UI can show meaningful
risk paths instead of a hairball.
"""

from collections import defaultdict
from typing import Dict, List, Tuple

from .data_loader import load_graph, load_review_countries, load_works_df


def _index_graph(graph: Dict) -> Tuple[Dict[str, Dict], Dict[str, Dict[str, Dict]]]:
    nodes_by_id = {str(n["id"]): n for n in graph.get("nodes", [])}
    adjacency: Dict[str, Dict[str, Dict]] = defaultdict(dict)
    for e in graph.get("edges", []):
        src, tgt = str(e["source"]), str(e["target"])
        adjacency[src][tgt] = e
        adjacency[tgt][src] = e
    return nodes_by_id, adjacency


def _make_node(node: Dict, node_type: str) -> Dict:
    return {
        "id": str(node["id"]),
        "label": node.get("label", ""),
        "country": node.get("country", "Unknown"),
        "institution": node.get("institution", ""),
        "is_um_author": bool(node.get("is_um_author", False)),
        "node_type": node_type,
    }


def compute_risk_paths(seed_author_id: str) -> Dict:
    graph = load_graph()
    review_countries = load_review_countries()
    nodes_by_id, adjacency = _index_graph(graph)

    seed = nodes_by_id.get(seed_author_id)
    if seed is None:
        return {
            "seed": None,
            "nodes": [],
            "edges": [],
            "review_countries": list(review_countries.keys()),
        }

    direct_ids = list(adjacency.get(seed_author_id, {}).keys())

    out_nodes: Dict[str, Dict] = {}
    indirect_edges: List[Dict] = []
    direct_to_flagged: Dict[str, set] = defaultdict(set)

    for direct_id in direct_ids:
        direct = nodes_by_id.get(direct_id)
        if direct is None:
            continue

        for second_id, second_edge in adjacency.get(direct_id, {}).items():
            if second_id == seed_author_id:
                continue
            second = nodes_by_id.get(second_id)
            if second is None:
                continue
            country = (second.get("country") or "").upper()
            if country not in review_countries:
                continue

            # Materialize the slice
            if seed_author_id not in out_nodes:
                out_nodes[seed_author_id] = _make_node(seed, "seed")

            if direct_id not in out_nodes:
                out_nodes[direct_id] = _make_node(direct, "direct")

            if second_id not in out_nodes:
                flagged_node = _make_node(second, "flagged_second_hop")
                flagged_node["flag_reason"] = review_countries[country].get("flag_reason", "")
                flagged_node["risk_level"] = review_countries[country].get("risk_level", "")
                out_nodes[second_id] = flagged_node

            direct_to_flagged[direct_id].add(second_id)

            indirect_edges.append({
                "source": direct_id,
                "target": second_id,
                "weight": int(second_edge.get("weight", 1)),
                "edge_type": "indirect_risk_path",
            })

    # Add seed→direct edges only for directs that produced flagged hops.
    direct_edges: List[Dict] = []
    for direct_id, flagged_set in direct_to_flagged.items():
        seed_edge = adjacency.get(seed_author_id, {}).get(direct_id, {})
        direct_edges.append({
            "source": seed_author_id,
            "target": direct_id,
            "weight": int(seed_edge.get("weight", 1)),
            "edge_type": "direct",
        })
        out_nodes[direct_id]["indirect_risk_count"] = len(flagged_set)

    # De-dup edges (same direct/flagged pair could be hit twice via different traversals)
    seen = set()
    deduped: List[Dict] = []
    for e in direct_edges + indirect_edges:
        key = (e["source"], e["target"], e["edge_type"])
        if key in seen:
            continue
        seen.add(key)
        deduped.append(e)

    return {
        "seed": out_nodes.get(seed_author_id),
        "nodes": list(out_nodes.values()),
        "edges": deduped,
        "review_countries": list(review_countries.keys()),
    }


def compute_summary(seed_author_id: str) -> Dict:
    risk = compute_risk_paths(seed_author_id)
    nodes = risk["nodes"]
    edges = risk["edges"]

    direct_ids = {n["id"] for n in nodes if n.get("node_type") == "direct"}
    flagged_nodes = [n for n in nodes if n.get("node_type") == "flagged_second_hop"]
    flagged_ids = {n["id"] for n in flagged_nodes}

    risk_paths = sum(1 for e in edges if e.get("edge_type") == "indirect_risk_path")

    by_country: Dict[str, int] = defaultdict(int)
    for n in flagged_nodes:
        by_country[n.get("country", "Unknown")] += 1

    return {
        "seed_author_id": seed_author_id,
        "total_risk_paths": risk_paths,
        "direct_collaborators_with_indirect_risk": len(direct_ids),
        "flagged_second_hop_authors": len(flagged_ids),
        "country_breakdown": dict(by_country),
    }


def shared_works_between(source_id: str, target_id: str) -> Dict:
    graph = load_graph()
    works_df = load_works_df()
    _, adjacency = _index_graph(graph)

    edge = adjacency.get(source_id, {}).get(target_id)
    if not edge:
        return {"source": source_id, "target": target_id, "weight": 0, "works": []}

    work_ids = edge.get("shared_works", []) or []

    by_id = {}
    if not works_df.empty:
        for _, w in works_df.iterrows():
            by_id[str(w["work_id"])] = w

    works_out = []
    for wid in work_ids:
        wid = str(wid)
        w = by_id.get(wid)
        if w is not None:
            year_raw = str(w.get("publication_year", ""))
            year = int(year_raw) if year_raw.isdigit() else None
            works_out.append({
                "work_id": wid,
                "title": str(w.get("title", "")),
                "publication_year": year,
            })
        else:
            works_out.append({"work_id": wid, "title": "", "publication_year": None})

    return {
        "source": source_id,
        "target": target_id,
        "weight": int(edge.get("weight", len(work_ids))),
        "works": works_out,
    }
