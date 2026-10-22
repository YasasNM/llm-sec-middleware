from __future__ import annotations

from fastapi import FastAPI
from fastapi.responses import FileResponse

from c2.api.classify import router as classify_router
from c2.api.health import router as health_router
from c2.classifier.predict import ClassifierService
from c2.config import COMPONENT_ROOT, get_settings
from c2.embedding.encoder import EmbeddingEncoder
from c2.events import EventSender
from c2.log.verdicts import VerdictLog
from c2.runtime import Runtime, set_runtime
from c2.similarity.chroma_store import SimilarityStore


def create_app() -> FastAPI:
    settings = get_settings()
    classifier = ClassifierService(settings)
    encoder = EmbeddingEncoder(settings)
    similarity = SimilarityStore(settings, encoder)
    set_runtime(
        Runtime(
            classifier=classifier,
            similarity=similarity,
            events=EventSender(),
            verdicts=VerdictLog(settings),
        )
    )

    app = FastAPI(title="MediGuard C2 Intent Classifier", version=settings.model_version)
    app.include_router(health_router)
    app.include_router(classify_router)

    demo_page = COMPONENT_ROOT / "demo" / "tester" / "index.html"
    if settings.dev_ui and demo_page.is_file():

        @app.get("/")
        def demo() -> FileResponse:
            return FileResponse(demo_page)

    return app


app = create_app()


def run() -> None:
    import uvicorn

    settings = get_settings()
    uvicorn.run("c2.main:app", host=settings.host, port=settings.port, reload=False)


if __name__ == "__main__":
    run()
