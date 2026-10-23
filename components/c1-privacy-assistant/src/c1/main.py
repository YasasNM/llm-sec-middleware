"""C1 service entry point (port 8001).

Start it with:  uvicorn c1.main:create_app --factory --port 8001
No CORS is added on purpose: only the gateway calls C1, never a browser.
The one exception is the temporary /demo page, which exists only when C1_DEMO=1.
"""

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError

from c1.api import analyze, demo, health, reid
from c1.api.security import validation_error_without_input
from c1.config import Settings, load_settings
from c1.pipeline import load_resources


# Build the C1 web app: load settings and models once, then attach the routes
def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or load_settings()
    app = FastAPI(title="C1 Privacy Assistant", version="0.1.0")
    app.state.settings = settings
    app.state.resources = load_resources(settings)
    app.add_exception_handler(RequestValidationError, validation_error_without_input)
    app.include_router(health.router)
    app.include_router(analyze.router)
    app.include_router(reid.router)
    if settings.demo_enabled:
        app.include_router(demo.router)
    return app
