"""Temporary demo page for C1 (only when C1_DEMO=1). The real UI comes later in apps/web.

Why a separate route: a browser must never hold the gateway's service key (K23), so the page
calls POST /demo/analyze, which needs no key and exists only in demo mode.
Privacy: the reply holds positions, types, tokens and scores only. It never sends back a
detected value; the page reads the highlighted words from its own text box.
"""

import csv
import json
import time
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse

from c1.config import C1_ROOT
from c1.detection.entity import Entity
from c1.masking.masker import MaskResult, mask
from c1.pipeline import analyze_text, elapsed_ms
from c1.reid.finders import QuasiIdentifier
from c1.reid.scorer import score_reid
from c1.schemas import TextRequest

router = APIRouter()

PAGE_FILE = C1_ROOT / "demo" / "ui" / "index.html"
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


# Entities as positions and types only, never their values
def entity_rows(entities: list[Entity]) -> list[dict]:
    return [
        {
            "type": e.type,
            "start": e.start,
            "end": e.end,
            "source": e.source,
            "confidence": e.confidence,
        }
        for e in entities
    ]


# Re-identification findings as positions, rarity and suggestion only
def reid_rows(found: list[QuasiIdentifier]) -> list[dict]:
    return [
        {
            "type": q.type,
            "start": q.start,
            "end": q.end,
            "rarity": q.rarity,
            "suggestion": q.suggestion,
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
        "entities": entity_rows(analysis.entities),
        "masked_text": masked.masked_text,
        "masking_percentage": masked.masking_percentage,
        "tokens": token_rows(masked),
        "risk": analysis.risk,
        "reid": {
            "level": analysis.reid.level,
            "score": analysis.reid.score,
            "explanation": analysis.reid.explanation,
            "items": reid_rows(analysis.reid.quasi_identifiers),
            "masked_with_suggestions": safer.safer_prompt,
        },
        "name_detector": request.app.state.resources.name_detector,
        "latency_ms": elapsed_ms(started),
    }
