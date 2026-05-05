"""
Pydantic response models. Used for OpenAPI docs and response shape validation.
"""

from typing import Dict, List, Optional

from pydantic import BaseModel


class UMAuthor(BaseModel):
    author_id: str
    display_name: str
    institution_name: Optional[str] = ""
    country: Optional[str] = ""


class ScopusEnrichment(BaseModel):
    scopus_id: Optional[str] = ""
    h_index: Optional[str] = ""
    document_count: Optional[str] = ""
    current_affiliation: Optional[str] = ""
    current_affiliation_country: Optional[str] = ""
    affiliation_history: Optional[str] = ""


class DimensionsEnrichment(BaseModel):
    dimensions_researcher_id: Optional[str] = ""
    grant_count: Optional[int] = 0
    patent_count: Optional[int] = 0
    funder_countries: Optional[str] = ""
    has_review_country_funding: Optional[bool] = False
    grants: Optional[str] = ""
    patents: Optional[str] = ""


class AffiliationMismatch(BaseModel):
    graph_country: str
    scopus_country: str
    scopus_affiliation: str


class RiskNode(BaseModel):
    id: str
    label: str
    country: str
    institution: str
    is_um_author: bool
    node_type: str  # "seed" | "direct" | "flagged_second_hop"
    indirect_risk_count: Optional[int] = None
    flag_reason: Optional[str] = None
    risk_level: Optional[str] = None
    scopus: Optional[ScopusEnrichment] = None
    dimensions: Optional[DimensionsEnrichment] = None
    affiliation_mismatch: Optional[AffiliationMismatch] = None
    funding_risk: Optional[bool] = None


class RiskEdge(BaseModel):
    source: str
    target: str
    weight: int
    edge_type: str  # "direct" | "indirect_risk_path"


class RiskPathsResponse(BaseModel):
    seed: Optional[RiskNode] = None
    nodes: List[RiskNode] = []
    edges: List[RiskEdge] = []
    review_countries: List[str] = []


class RiskSummaryResponse(BaseModel):
    seed_author_id: str
    total_risk_paths: int
    direct_collaborators_with_indirect_risk: int
    flagged_second_hop_authors: int
    country_breakdown: Dict[str, int]


class ExpandResponse(BaseModel):
    author_id: str
    seed: Optional[RiskNode] = None
    neighbor_count: int
    shown_count: int
    nodes: List[RiskNode] = []
    edges: List[RiskEdge] = []
    live: Optional[bool] = False


class SharedWork(BaseModel):
    work_id: str
    title: Optional[str] = ""
    publication_year: Optional[int] = None


class SharedWorksResponse(BaseModel):
    source: str
    target: str
    weight: Optional[int] = 0
    works: List[SharedWork] = []
