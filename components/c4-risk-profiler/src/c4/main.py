"""C4 application. Run:  uvicorn c4.main:app --port 8004"""

from __future__ import annotations

import logging
import threading
from contextlib import asynccontextmanager

from fastapi import FastAPI

from c4 import __version__
from c4.api import actions, blocklist, events, health, users
from c4.config import Settings
from c4.contracts import load_validators
from c4.core.ruleset import load_ruleset
from c4.db.mongo import get_db

log = logging.getLogger("c4")


class _UserLocks:
    """One lock per (org, user), so two events of the same user never rebuild at the same time."""

    def __init__(self) -> None:
        self._guard = threading.Lock()
        self._locks: dict[tuple[str, str], threading.Lock] = {}

    def __call__(self, org_id: str, user_id: str) -> threading.Lock:
        with self._guard:
            return self._locks.setdefault((org_id, user_id), threading.Lock())


def create_app(settings: Settings | None = None, db=None) -> FastAPI:
    settings = settings or Settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if getattr(app.state, "db", None) is None:
            app.state.db = get_db(settings.mongo_url)
        yield

    app = FastAPI(title="MediGuard C4 Risk Profiler", version=__version__, lifespan=lifespan)
    app.state.settings = settings
    app.state.key_map = settings.key_map()
    if not app.state.key_map:
        log.warning("MG_ACCEPTED_KEYS is empty: every call will be refused with 401")
    app.state.rules = load_ruleset(settings.rules_file)
    app.state.validators = load_validators(settings.contracts_dir)
    app.state.user_lock = _UserLocks()
    app.state.db = db

    for router in (health.router, events.router, users.router, actions.router, blocklist.router):
        app.include_router(router)
    return app


app = create_app()
