"""POST /v1/check: the decision on submit (contract c1_check_request -> c1_check_response).

Order (BLUEPRINT 6.4):
1. M1 gate. If the block list cannot be confirmed: 503 BLOCKLIST_UNAVAILABLE and NO event.
2. Detection always runs, also for a blocked user, so masking_percentage is real (IR14).
3. Blocked user: ACCESS_BLOCKED, masked_text null.
4. Allowed user: explicit identifiers are always masked (K2); re-identification suggestions are
   applied only when apply_reid_suggestions is true (the default).
5. After the reply is sent: one C1 event to C4 (M9) and one row in the decision log (M10).
   Both run in the background, so a slow or down C4 never delays the answer.

Nothing here logs or stores text: only ids, the decision, counts and percentages.
"""

import logging
import time
from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends, Request
from fastapi.responses import JSONResponse

from c1.api.security import require_gateway
from c1.common import build_c1_event, send_event, utc_now_iso
from c1.gate.blocklist import BlocklistUnavailable
from c1.log.decision_log import Decision, write_decision
from c1.masking.masker import MaskResult, mask
from c1.pipeline import Analysis, analyze_text, elapsed_ms
from c1.reid.scorer import score_reid
from c1.schemas import CheckRequest, CheckResponse, ReidSummary

logger = logging.getLogger("c1.check")
router = APIRouter()


# Check one submitted prompt: gate, detect, mask, answer; event and log follow in the background
@router.post("/v1/check", response_model=CheckResponse)
def check(
    body: CheckRequest,
    request: Request,
    background: BackgroundTasks,
    caller: Annotated[str, Depends(require_gateway)],
):
    started = time.perf_counter()
    app_state = request.app.state
    try:
        blocked = app_state.blocklist.is_blocked(body.org_id, body.user_id)
    except BlocklistUnavailable:
        return blocklist_unavailable(body, app_state, background, elapsed_ms(started))
    analysis = analyze_text(body.text, app_state.resources)
    masked = mask(body.text, analysis.entities)
    if blocked:
        answer = blocked_answer(body, analysis, masked, elapsed_ms(started))
    else:
        answer = allowed_answer(body, analysis, masked, app_state, started)
    background.add_task(record_check, app_state, body, answer)
    logger.info(
        "check request_id=%s decision=%s masking_percentage=%s latency_ms=%s",
        body.request_id, answer.decision, answer.masking_percentage, answer.latency_ms,
    )  # fmt: skip
    return answer


# Re-identification summary for the answer: level and score of the prompt as typed
def reid_summary(analysis: Analysis, applied: bool) -> ReidSummary:
    return ReidSummary(level=analysis.reid.level, score=analysis.reid.score, applied=applied)


# Blocked user: no text goes back, but the masking percentage is still real (IR14)
def blocked_answer(
    body: CheckRequest, analysis: Analysis, masked: MaskResult, latency_ms: int
) -> CheckResponse:
    return CheckResponse(
        request_id=body.request_id,
        decision="ACCESS_BLOCKED",
        masked_text=None,
        placeholder_manifest=[],
        masking_percentage=masked.masking_percentage,
        entity_counts=masked.entity_counts,
        reid=reid_summary(analysis, applied=False),
        user_message_code="ACCESS_BLOCKED",
        latency_ms=latency_ms,
    )


# Allowed user: masked text (plus the re-id suggestions if the clinician kept them on)
def allowed_answer(
    body: CheckRequest, analysis: Analysis, masked: MaskResult, app_state, started: float
) -> CheckResponse:
    text = masked.masked_text
    if body.apply_reid_suggestions:
        # Suggestions are applied to the already-masked text; placeholders are never touched
        text = score_reid(text, app_state.resources.reid_lists).safer_prompt
    return CheckResponse(
        request_id=body.request_id,
        decision="ALLOW",
        masked_text=text,
        placeholder_manifest=masked.placeholder_manifest,
        masking_percentage=masked.masking_percentage,
        entity_counts=masked.entity_counts,
        reid=reid_summary(analysis, applied=body.apply_reid_suggestions),
        user_message_code=None,
        latency_ms=elapsed_ms(started),
    )


# Fail closed: 503 with a code only, no event (CONTRACTS 2.5); the decision log still records it
def blocklist_unavailable(body: CheckRequest, app_state, background, latency_ms: int):
    row = decision_row(body, app_state, "BLOCKLIST_UNAVAILABLE", None, latency_ms)
    background.add_task(safe_write_decision, app_state.settings.outbox_path, row)
    logger.warning("check request_id=%s decision=BLOCKLIST_UNAVAILABLE", body.request_id)
    content = {"code": "BLOCKLIST_UNAVAILABLE", "request_id": body.request_id}
    return JSONResponse(status_code=503, content=content)


# Build the decision-log row: ids, decision and numbers only
def decision_row(body, app_state, decision: str, answer: CheckResponse | None, latency_ms: int):
    return Decision(
        request_id=body.request_id,
        org_id=body.org_id,
        user_id=body.user_id,
        session_id=body.session_id,
        decision=decision,
        masking_percentage=answer.masking_percentage if answer else None,
        entity_counts=answer.entity_counts if answer else {},
        reid_level=answer.reid.level if answer else None,
        reid_score=answer.reid.score if answer else None,
        reid_applied=answer.reid.applied if answer else None,
        name_detector=app_state.resources.name_detector,
        latency_ms=latency_ms,
        timestamp=utc_now_iso(),
    )


# Background work after an ALLOW or ACCESS_BLOCKED answer: decision log, then exactly one event
def record_check(app_state, body: CheckRequest, answer: CheckResponse) -> None:
    settings = app_state.settings
    row = decision_row(body, app_state, answer.decision, answer, answer.latency_ms)
    safe_write_decision(settings.outbox_path, row)
    event = build_c1_event(
        body.org_id, body.user_id, body.request_id, body.session_id, answer.masking_percentage
    )
    try:
        send_event(settings.c4_url, settings.service_key, event, str(settings.outbox_path),
                   settings.event_retry_delays)  # fmt: skip
    except Exception:  # an outbox problem must never crash C1; the reply was already sent
        logger.exception("c1 event could not be sent or stored event_id=%s", event["event_id"])


# Write a decision-log row; a disk problem is logged (ids only) and never breaks a request
def safe_write_decision(path, row: Decision) -> None:
    try:
        write_decision(str(path), row)
    except Exception:
        logger.exception("decision log write failed request_id=%s", row.request_id)
