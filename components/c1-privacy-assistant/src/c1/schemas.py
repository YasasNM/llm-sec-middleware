"""Request and response shapes for C1's API (Pydantic checks them automatically).

/v1/analyze and /v1/reid are internal to C1 and the gateway, so they have no shared contract.
/v1/check (Step 6) mirrors contracts/schemas/c1_check_*.json exactly.
"""

from pydantic import BaseModel, Field

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
    """What /health returns. The block-list cache age arrives with Step 6."""

    status: str
    service: str
    name_detector: str
    blocklist_cache_age_s: float | None = None
