"""POST /v1/reid: live re-identification risk and suggestions while typing. Sends no event."""

import time
from typing import Annotated

from fastapi import APIRouter, Depends, Request

from c1.api.security import require_gateway
from c1.pipeline import elapsed_ms
from c1.reid.finders import QuasiIdentifier
from c1.reid.scorer import score_reid
from c1.schemas import QuasiOut, ReidResponse, TextRequest

router = APIRouter()


# Convert one internal QuasiIdentifier into the API shape
def quasi_out(item: QuasiIdentifier) -> QuasiOut:
    return QuasiOut(
        type=item.type,
        start=item.start,
        end=item.end,
        match=item.match,
        rarity=item.rarity,
        suggestion=item.suggestion,
    )


# Score the re-identification risk and return the findings, level and safer prompt
@router.post("/v1/reid", response_model=ReidResponse)
def reid(
    body: TextRequest, request: Request, caller: Annotated[str, Depends(require_gateway)]
) -> ReidResponse:
    started = time.perf_counter()
    result = score_reid(body.text, request.app.state.resources.reid_lists)
    return ReidResponse(
        quasi_identifiers=[quasi_out(item) for item in result.quasi_identifiers],
        count=result.count,
        score=result.score,
        level=result.level,
        explanation=result.explanation,
        safer_prompt=result.safer_prompt,
        latency_ms=elapsed_ms(started),
    )
