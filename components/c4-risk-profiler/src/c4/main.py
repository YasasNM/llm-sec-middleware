"""C4 application. Run:  python -m uvicorn c4.main:app --port 8000   (dashboard: /dashboard)"""

from __future__ import annotations

import logging
import threading
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles

from c4 import __version__
from c4.api import actions, blocklist, dashboard, events, health, users
from c4.config import Settings
from c4.contracts import load_validators
from c4.core.ruleset import load_ruleset
from c4.db.mongo import get_db
from c4.demo.data import new_sim_state

log = logging.getLogger("c4")


# Each user gets a lock so two events of the same user are never processed at the same moment
class _UserLocks:
    """One lock per (org, user), so two events of the same user never rebuild at the same time."""

    def __init__(self) -> None:
        self._guard = threading.Lock()
        self._locks: dict[tuple[str, str], threading.Lock] = {}

    def __call__(self, org_id: str, user_id: str) -> threading.Lock:
        with self._guard:
            return self._locks.setdefault((org_id, user_id), threading.Lock())


def create_app(settings: Settings | None = None, db=None) -> FastAPI:
    """Build the C4 app. Tests pass their own settings and a fake database."""
    settings = settings or Settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        # Connect to MongoDB when the server starts (unless a test already supplied a database)
        if getattr(app.state, "db", None) is None:
            app.state.db = get_db(settings.mongo_url)
        yield

    app = FastAPI(title="MediGuard C4 Risk Profiler", version=__version__, lifespan=lifespan)
    app.state.settings = settings
    app.state.key_map = settings.key_map()
    if not app.state.key_map:
        log.warning("MG_ACCEPTED_KEYS is empty: every call will be refused with 401")
    # Load the scoring rules and the event schemas once, when C4 starts
    app.state.rules = load_ruleset(settings.rules_file)
    app.state.validators = load_validators(settings.contracts_dir)
    app.state.user_lock = _UserLocks()
    app.state.db = db
    # Demo-only state for the dashboard: the live attack progress and the latest gate attempts
    app.state.sim = new_sim_state()
    app.state.gate_log = dashboard.new_gate_log()

    # Every group of endpoints in one list, so it is easy to see what C4 offers
    for router in (health.router, events.router, users.router, actions.router, blocklist.router):
        app.include_router(router)

    if settings.dashboard_enabled:
        app.include_router(dashboard.router)
        # The page itself is plain HTML, CSS and JavaScript files (no build step needed)
        app.mount(
            "/dashboard",
            StaticFiles(directory=Path(__file__).parent / "dashboard", html=True),
            name="dashboard",
        )

        @app.get("/", include_in_schema=False)
        def home():
            return RedirectResponse("/dashboard/")

    return app


# uvicorn looks for this name: "c4.main:app"
app = create_app()
