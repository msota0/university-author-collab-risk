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


class SharedWork(BaseModel):
    work_id: str
    title: Optional[str] = ""
    publication_year: Optional[int] = None


class SharedWorksResponse(BaseModel):
    source: str
    target: str
    weight: Optional[int] = 0
    works: List[SharedWork] = []
