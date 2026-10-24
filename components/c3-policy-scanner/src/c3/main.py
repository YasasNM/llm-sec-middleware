"""FastAPI app for C3. Run with one worker: `uvicorn c3.main:app --port 8003`."""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from c3 import __version__
from c3.api import policies, scan
from c3.config import Settings
from c3.container import Container, build_container


def create_app(container: Container | None = None) -> FastAPI:
    container = container or build_container(Settings())
    logging.basicConfig(
        level=container.settings.log_level,
        format='{"ts":"%(asctime)s","lvl":"%(levelname)s","logger":"%(name)s","msg":"%(message)s"}',
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        container.start()
        yield
        container.stop()

    app = FastAPI(title="C3 Policy Scanner", version=__version__, lifespan=lifespan)
    app.state.container = container
    app.include_router(policies.router)
    app.include_router(scan.router)

    @app.get("/health", tags=["health"])
    def health():
        c = app.state.container
        orgs = c.registry.orgs()
        return {
            "status": "ok",
            "version": __version__,
            "embedder": c.embedder.version,
            "orgs_loaded": len(orgs),
            "index_non_empty": any(c.registry.get(o).snapshot().entries for o in orgs),
            "watcher_alive": c.watcher.alive if c.watcher is not None else None,
            "model_loaded": c.generator is not None,
            "model_version": getattr(c.generator, "version", None),
            "events": c.db.event_counts(),
        }

    return app


def __getattr__(name: str):
    # `uvicorn c3.main:app` builds the app lazily, so importing the module has no side effects
    if name == "app":
        return create_app()
    raise AttributeError(name)
