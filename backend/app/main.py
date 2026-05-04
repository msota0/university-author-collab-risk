"""
FastAPI app for the University Collaboration Risk Analysis Tool.

Serves a precomputed co-authorship graph and exposes endpoints that compute
indirect risk paths (UM seed → direct collaborator → flagged second-hop).
"""

from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware

from .data_loader import list_um_authors, load_graph, reset_cache
from .graph_builder import compute_risk_paths, compute_summary, shared_works_between
from .models import (
    RiskPathsResponse,
    RiskSummaryResponse,
    SharedWorksResponse,
    UMAuthor,
)

app = FastAPI(title="UM Collaboration Risk Analysis Tool", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/")
def health():
    """Health check + lightweight graph metadata."""
    meta = load_graph().get("metadata", {})
    return {
        "status": "ok",
        "service": "um-collab-risk-analysis",
        "graph": meta,
    }


@app.get("/authors/um", response_model=list[UMAuthor])
def get_um_authors():
    """List UM-affiliated authors so the UI can offer name-based selection."""
    return list_um_authors()


@app.get("/review/risk-paths", response_model=RiskPathsResponse)
def get_risk_paths(seed_author_id: str = Query(..., description="OpenAlex author ID for the UM seed.")):
    """
    Return only the slice of the graph that forms an indirect risk path from
    the seed. Direct collaborators with no flagged second-hop are excluded.
    """
    return compute_risk_paths(seed_author_id)


@app.get("/review/summary", response_model=RiskSummaryResponse)
def get_summary(seed_author_id: str = Query(...)):
    """Aggregate counts plus a country breakdown of flagged second-hop authors."""
    return compute_summary(seed_author_id)


@app.get("/review/shared-works", response_model=SharedWorksResponse)
def get_shared_works(source: str = Query(...), target: str = Query(...)):
    """Shared publications behind a co-authorship edge."""
    return shared_works_between(source, target)


@app.post("/admin/reload")
def reload_cache():
    """Clear the in-memory caches so freshly ingested data is picked up."""
    reset_cache()
    return {"status": "reloaded"}
