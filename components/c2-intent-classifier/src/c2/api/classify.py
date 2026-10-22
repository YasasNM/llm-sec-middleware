from __future__ import annotations

import asyncio
import time

from fastapi import APIRouter, Depends

from c2.auth import require_gateway
from c2.config import get_settings, get_thresholds
from c2.ensemble.decide import decide
from c2.events import build_event
from c2.schemas import C2Input, C2Output

router = APIRouter()


@router.post("/v1/classify", response_model=C2Output)
async def classify(req: C2Input, _caller: str = Depends(require_gateway)) -> C2Output:
    from c2.runtime import get_runtime

    runtime = get_runtime()
    settings = get_settings()
    thresholds = get_thresholds()
    start = time.perf_counter()

    sim_task = asyncio.to_thread(runtime.similarity.search, req.prompt)
    clf_task = asyncio.to_thread(runtime.classifier.predict, req.prompt)
    similarity, classifier = await asyncio.gather(sim_task, clf_task)

    verdict, score, method, degraded = decide(similarity, classifier, thresholds)
    latency_ms = int((time.perf_counter() - start) * 1000)
    out = C2Output(
        request_id=req.request_id,
        c2_verdict=verdict,  # type: ignore[arg-type]
        c2_score=score,
        method=method,  # type: ignore[arg-type]
        nearest_attack_ids=similarity.nearest_ids,
        nearest_scores=similarity.nearest_scores,
        degraded=degraded,
        latency_ms=latency_ms,
        model_version=settings.model_version,
    )
    runtime.verdicts.write(req, out, similarity.score, classifier.score)
    if method != "fail_safe":
        runtime.events.send(build_event(req, out))
    return out
