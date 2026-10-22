"""POST /v1/analyze: live explicit-PHI findings while the clinician types. Sends no event."""

import time
from typing import Annotated

from fastapi import APIRouter, Depends, Request

from c1.api.security import require_gateway
from c1.detection.entity import Entity
from c1.pipeline import analyze_text, elapsed_ms
from c1.schemas import AnalyzeResponse, EntityOut, RiskOut, TextRequest

router = APIRouter()


# Convert one internal Entity into the API shape
def entity_out(entity: Entity) -> EntityOut:
    return EntityOut(
        type=entity.type,
        start=entity.start,
        end=entity.end,
        value=entity.value,
        confidence=entity.confidence,
        source=entity.source,
    )


# Analyse the text and return the explicit identifiers with the live risk level
@router.post("/v1/analyze", response_model=AnalyzeResponse)
def analyze(
    body: TextRequest, request: Request, caller: Annotated[str, Depends(require_gateway)]
) -> AnalyzeResponse:
    started = time.perf_counter()
    result = analyze_text(body.text, request.app.state.resources)
    return AnalyzeResponse(
        entities=[entity_out(entity) for entity in result.entities],
        explicit_risk=RiskOut(**result.risk["explicit"]),
        overall_level=result.risk["overall_level"],
        latency_ms=elapsed_ms(started),
    )
