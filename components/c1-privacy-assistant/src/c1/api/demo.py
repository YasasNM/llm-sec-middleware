"""Temporary demo page for C1 (only when C1_DEMO=1). The real UI comes later in apps/web.

Why a separate route: a browser must never hold the gateway's service key (K23), so the page
calls POST /demo/analyze, which needs no key and exists only in demo mode.
Privacy: the reply holds positions, types, tokens and scores only. It never sends back a
detected value; the page reads the highlighted words from its own text box.

Send in the demo differs from /v1/check ON PURPOSE. The gateway API /v1/check always masks every
direct identifier, and that is not changed. In the demo (POST /demo/send):
- "Send" (mode as_typed) sends the text exactly as the user left it: only the words the user
  masked or generalised by hovering are changed, nothing is masked automatically;
- "Mask all and send" (mode mask_all) masks every direct identifier and applies every suggestion.
Each send writes one PHI-free decision-log row: how many direct identifiers went out unmasked
and their types, never the text or the values. Unlike /demo/analyze, the /demo/send reply holds
the sent text, because that text is exactly what the user chose to send.
The C1 event contract (c1_event 1.0.0) has no field for the unmasked count, so no event carries it.
"""

import csv
import json
import logging
import re
import time
import uuid
from collections import Counter
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse

from c1.common import utc_now_iso
from c1.config import C1_ROOT
from c1.detection.entity import Entity
from c1.log.decision_log import Decision, write_decision
from c1.masking.masker import MaskResult, assign_tokens, mask, non_overlapping, replace_spans
from c1.pipeline import analyze_text, elapsed_ms
from c1.reid.finders import QuasiIdentifier
from c1.reid.scorer import score_reid
from c1.reid.suggestions import Edit, suggestion_edits
from c1.schemas import TextRequest

logger = logging.getLogger("c1.api.demo")
router = APIRouter()

PAGE_FILE = C1_ROOT / "demo" / "ui" / "index.html"
# A placeholder token such as [PATIENT_NAME_1], split into its type and number
TOKEN_NUMBER = re.compile(r"\[([A-Z_]+)_(\d+)\]")
SAMPLES_FOLDER = C1_ROOT / "data" / "samples"


# Serve the demo page itself
@router.get("/demo")
def demo_page() -> FileResponse:
    return FileResponse(PAGE_FILE, media_type="text/html")


# Read the synthetic sample notes and prompts for the dropdown
def read_samples(folder: Path) -> list[dict]:
    samples = []
    with open(folder / "clinical_notes_sample.jsonl", encoding="utf-8") as handle:
        for line in handle:
            note = json.loads(line)
            samples.append({"label": f"Note: {note['scenario']}", "text": note["text"]})
    with open(folder / "sensitivity_sample.csv", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            samples.append({"label": f"Prompt: {row['text'][:60]}", "text": row["text"]})
    return samples


# List the sample texts (all synthetic) so the page can fill its dropdown
@router.get("/demo/samples")
def samples() -> list[dict]:
    return read_samples(SAMPLES_FOLDER)


# Entities as positions, types and risk classes only, never their values. The risk class
# ("high", "medium" or "low") comes from configs/default.yaml risk.entity_classes, the same
# table the live risk score uses (risk.py); a type that is not listed there counts as "low".
def entity_rows(entities: list[Entity], risk_rules: dict) -> list[dict]:
    classes = risk_rules["entity_classes"]
    return [
        {
            "type": e.type,
            "start": e.start,
            "end": e.end,
            "source": e.source,
            "confidence": e.confidence,
            "risk_class": classes.get(e.type, "low"),
        }
        for e in entities
    ]


# One tidied edit as positions and the new wording (the wording comes from the config lists)
def edit_row(edit: Edit) -> dict:
    return {"start": edit.start, "end": edit.end, "replacement": edit.replacement}


# Re-identification findings as positions, rarity and suggestion only, plus the edit that
# "Generalise this" makes for that one finding (tidied: no doubled words, right article)
def reid_rows(text: str, found: list[QuasiIdentifier]) -> list[dict]:
    return [
        {
            "type": q.type,
            "start": q.start,
            "end": q.end,
            "rarity": q.rarity,
            "suggestion": q.suggestion,
            "edit": edit_row(suggestion_edits(text, [q])[0]),
        }
        for q in found
    ]


# One row per placeholder token: the token, its type and how often it appears
def token_rows(result: MaskResult) -> list[dict]:
    rows = []
    for token in result.placeholder_manifest:
        token_type = token.strip("[]").rsplit("_", 1)[0]
        rows.append({"token": token, "type": token_type, "count": result.masked_text.count(token)})
    return rows


# Analyse the text with the same pipeline as /v1/analyze, then mask it as /v1/check will
@router.post("/demo/analyze")
def demo_analyze(body: TextRequest, request: Request) -> dict:
    started = time.perf_counter()
    analysis = analyze_text(body.text, request.app.state.resources)
    masked = mask(body.text, analysis.entities)
    safer = score_reid(masked.masked_text, request.app.state.resources.reid_lists)
    return {
        "entities": entity_rows(analysis.entities, request.app.state.resources.risk_rules),
        "masked_text": masked.masked_text,
        "masking_percentage": masked.masking_percentage,
        "tokens": token_rows(masked),
        "risk": analysis.risk,
        "reid": {
            "level": analysis.reid.level,
            "score": analysis.reid.score,
            "explanation": analysis.reid.explanation,
            "items": reid_rows(body.text, analysis.reid.quasi_identifiers),
            # All suggestions at once, merged so they read naturally ("Apply all suggestions")
            "edits": [
                edit_row(e) for e in suggestion_edits(body.text, analysis.reid.quasi_identifiers)
            ],
            "masked_with_suggestions": safer.safer_prompt,
        },
        "name_detector": request.app.state.resources.name_detector,
        "latency_ms": elapsed_ms(started),
    }


class DemoSendRequest(TextRequest):
    """Body of /demo/send: the composer text and which button was pressed."""

    mode: Literal["as_typed", "mask_all"]


# Highest number already used for each token type in the text, e.g. {"PATIENT_NAME": 1}
def used_token_numbers(text: str) -> dict[str, int]:
    used = {}
    for token_type, number in TOKEN_NUMBER.findall(text):
        used[token_type] = max(used.get(token_type, 0), int(number))
    return used


# Mask every direct identifier, numbering new tokens after the ones the user already put in,
# so a second name becomes [PATIENT_NAME_2] next to the user's own [PATIENT_NAME_1]
def mask_after_user_tokens(text: str, entities: list[Entity]) -> str:
    kept = non_overlapping(entities)
    used = used_token_numbers(text)
    tokens = {}
    for key, token in assign_tokens(kept).items():
        token_type, number = TOKEN_NUMBER.fullmatch(token).groups()
        tokens[key] = f"[{token_type}_{int(number) + used.get(token_type, 0)}]"
    return replace_spans(text, kept, tokens)


# Number of re-identification clues whose suggestion really changes the text
def clues_to_generalise(text: str, found: list[QuasiIdentifier]) -> int:
    changed = 0
    for clue in found:
        edit = suggestion_edits(text, [clue])[0]
        if text[edit.start : edit.end].lower() != edit.replacement.lower():
            changed += 1
    return changed


# How many direct identifiers of each type, e.g. {"PATIENT_NAME": 1, "PHONE": 1}
def counts_by_type(entities: list[Entity]) -> dict[str, int]:
    return dict(Counter(entity.type for entity in entities))


# The PHI-free decision-log row for one demo send: ids, numbers and types only
def send_row(request_id: str, mode: str, analysis, unmasked: list[Entity], app_state) -> Decision:
    return Decision(
        request_id=request_id,
        org_id="demo",
        user_id="demo",
        session_id="demo",
        decision="DEMO_SEND_" + mode.upper(),  # DEMO_SEND_AS_TYPED or DEMO_SEND_MASK_ALL
        masking_percentage=None,  # only the composer text is known, not what was typed first
        entity_counts=counts_by_type(analysis.entities),
        reid_level=analysis.reid.level,
        reid_score=analysis.reid.score,
        reid_applied=mode == "mask_all",
        name_detector=app_state.resources.name_detector,
        latency_ms=0,
        timestamp=utc_now_iso(),
        unmasked_count=len(unmasked),
        unmasked_types=sorted({entity.type for entity in unmasked}),
    )


# The text to send and what changed, for one button. as_typed changes nothing; mask_all masks
# every red word and then applies every suggestion (same order as /v1/check with suggestions).
def prepare_send(text: str, mode: str, analysis, reid_lists: dict) -> dict:
    if mode == "as_typed":
        return {"sent_text": text, "unmasked": analysis.entities, "masked": 0, "generalised": 0}
    masked = mask_after_user_tokens(text, analysis.entities)
    safer = score_reid(masked, reid_lists)
    generalised = clues_to_generalise(masked, safer.quasi_identifiers)
    return {
        "sent_text": safer.safer_prompt,
        "unmasked": [],
        "masked": len(analysis.entities),
        "generalised": generalised,
    }


# A demo send (DEMO ONLY: /v1/check always masks). Logs a count and types, never text or values.
@router.post("/demo/send")
def demo_send(body: DemoSendRequest, request: Request) -> dict:
    started = time.perf_counter()
    app_state = request.app.state
    analysis = analyze_text(body.text, app_state.resources)
    result = prepare_send(body.text, body.mode, analysis, app_state.resources.reid_lists)
    request_id = "demo_" + uuid.uuid4().hex[:12]
    row = send_row(request_id, body.mode, analysis, result["unmasked"], app_state)
    row.latency_ms = elapsed_ms(started)
    write_decision(str(app_state.settings.outbox_path), row)
    logger.info(
        "demo send request_id=%s mode=%s unmasked_sent=%d types=%s",
        request_id, body.mode, row.unmasked_count, row.unmasked_types,
    )  # fmt: skip
    return {
        "request_id": request_id,
        "sent_text": result["sent_text"],
        "masked_by_user": len(TOKEN_NUMBER.findall(body.text)),
        "unmasked_sent": row.unmasked_count,
        "types": row.unmasked_types,
        "masked_automatically": result["masked"],
        "generalised": result["generalised"],
    }
