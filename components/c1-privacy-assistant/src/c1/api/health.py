"""GET /health: is C1 up, and which name detector is active. No key needed (Compose calls it)."""

from fastapi import APIRouter, Request

from c1.schemas import HealthResponse

router = APIRouter()


# Report that C1 is running and whether it uses the trained model or the heuristic
@router.get("/health", response_model=HealthResponse)
def health(request: Request) -> HealthResponse:
    return HealthResponse(
        status="ok",
        service=request.app.state.settings.service_name,
        name_detector=request.app.state.resources.name_detector,
    )
