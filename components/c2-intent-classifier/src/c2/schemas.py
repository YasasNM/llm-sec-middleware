from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional, Union

from pydantic import BaseModel, Field

Verdict = Literal["PASS", "BLOCK"]
Method = Literal["similarity_override", "ensemble", "fail_safe"]


class C1Metadata(BaseModel):
    masking_percentage: Optional[float] = None
    entity_counts: Optional[Dict[str, Any]] = None
    reid_level: Optional[Literal["Low", "Medium", "High"]] = None
    placeholder_manifest: Optional[List[str]] = None


class C2Input(BaseModel):
    request_id: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_.:-]+$")
    org_id: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_.:-]+$")
    user_id: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_.:-]+$")
    session_id: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_.:-]+$")
    prompt: str = Field(min_length=1, max_length=8000)
    c1_metadata: Union[C1Metadata, Dict[str, Any]] = Field(default_factory=dict)


class C2Output(BaseModel):
    request_id: str
    c2_verdict: Verdict
    c2_score: float = Field(ge=0, le=1)
    method: Method
    nearest_attack_ids: list[str] = Field(default_factory=list)
    nearest_scores: list[float] = Field(default_factory=list)
    degraded: bool
    latency_ms: int = Field(ge=0)
    model_version: str


class C2Event(BaseModel):
    event_id: str
    org_id: str
    user_id: str
    request_id: str
    session_id: str
    event_timestamp: str
    c2_verdict: Verdict
    c2_score: float = Field(ge=0, le=1)
