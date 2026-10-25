"""Request and response shapes for C1's API (Pydantic checks them automatically).

/v1/analyze and /v1/reid are internal to C1 and the gateway, so they have no shared contract.
/v1/check mirrors contracts/schemas/c1_check_*.json exactly; tests/contract proves it by checking
the same payloads with these models and with the real schema files.
"""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

MAX_TEXT_CHARS = 8000


class TextRequest(BaseModel):
    """Body of /v1/analyze and /v1/reid: just the prompt text, at most 8000 characters."""

    text: str = Field(max_length=MAX_TEXT_CHARS)


class EntityOut(BaseModel):
    """One explicit identifier. `value` goes to the gateway only, for underlining; never logged."""

    type: str
    start: int
    end: int
    value: str
    confidence: float
    source: str


class RiskOut(BaseModel):
    """A score from 0 to 100 and its level."""

    score: int
    level: str


class AnalyzeResponse(BaseModel):
    """What /v1/analyze returns (BLUEPRINT 9.1), plus the overall live risk level."""

    entities: list[EntityOut]
    explicit_risk: RiskOut
    overall_level: str
    latency_ms: int


class QuasiOut(BaseModel):
    """One re-identification detail and its suggested general wording."""

    type: str
    start: int
    end: int
    match: str
    rarity: str
    suggestion: str


class ReidResponse(BaseModel):
    """What /v1/reid returns (BLUEPRINT 9.2)."""

    quasi_identifiers: list[QuasiOut]
    count: int
    score: int
    level: str
    explanation: str
    safer_prompt: str
    latency_ms: int


class HealthResponse(BaseModel):
    """What /health returns, including the age of the oldest cached block list (None if none)."""

    status: str
    service: str
    name_detector: str
    blocklist_cache_age_s: float | None = None


# One id as the contracts define it: 1 to 64 letters, digits or _ . : -
Id = Annotated[str, Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_.:-]+$")]


class CheckRequest(BaseModel):
    """Body of POST /v1/check (contract c1_check_request): unknown fields are rejected."""

    model_config = ConfigDict(extra="forbid", strict=True)

    request_id: Id
    org_id: Id
    user_id: Id
    session_id: Id
    text: str = Field(min_length=1, max_length=MAX_TEXT_CHARS)
    apply_reid_suggestions: bool = True  # explicit identifiers are masked either way (K2)


class ReidSummary(BaseModel):
    """Re-identification part of the /v1/check answer."""

    level: Literal["Low", "Medium", "High"]
    score: int = Field(ge=0, le=100)
    applied: bool


class CheckResponse(BaseModel):
    """Answer of POST /v1/check (contract c1_check_response). Tokens and counts only, no values."""

    request_id: Id
    decision: Literal["ALLOW", "ACCESS_BLOCKED"]
    masked_text: str | None  # None when ACCESS_BLOCKED
    placeholder_manifest: list[Annotated[str, Field(pattern=r"^\[[A-Z_]+_[0-9]+\]$")]]
    masking_percentage: float = Field(ge=0, le=100)
    entity_counts: dict[str, Annotated[int, Field(ge=0)]]
    reid: ReidSummary
    user_message_code: Literal["ACCESS_BLOCKED"] | None = None
    latency_ms: int = Field(ge=0)
