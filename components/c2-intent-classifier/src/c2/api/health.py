from __future__ import annotations

from fastapi import APIRouter

from c2.config import get_settings

router = APIRouter()


@router.get("/health")
def health() -> dict:
    from c2.runtime import get_runtime

    runtime = get_runtime()
    settings = get_settings()
    classifier_ok = runtime.classifier.status == "ok"
    chroma_status = runtime.similarity.status
    status = "ok" if classifier_ok else "degraded"
    if not classifier_ok and chroma_status not in {"ok"}:
        status = "not_ready"
    return {
        "status": status,
        "service": settings.service_name,
        "model_version": settings.model_version,
        "chroma": chroma_status,
        "classifier": runtime.classifier.status,
        "vectors": runtime.similarity.vector_count,
        "outbox_pending": runtime.events.pending_count(),
    }


@router.get("/info")
def info() -> dict:
    from c2.runtime import get_runtime

    runtime = get_runtime()
    settings = get_settings()
    return {
        "model_dir": str(settings.classifier_checkpoint_path),
        "device": runtime.classifier._device,
        "classifier": runtime.classifier.status,
        "chroma": runtime.similarity.status,
        "model_version": settings.model_version,
    }
