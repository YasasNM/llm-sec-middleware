"""Choose the name detector: the ML model when it loaded, otherwise the titled-name heuristic."""

from c1.detection.entity import Entity
from c1.detection.ml_names import DEFAULT_THRESHOLD, detect_ml_names
from c1.detection.titled_names import detect_titled_names


# Which name detector is active: "ml" or "heuristic" (shown later by /health)
def name_detector_label(model: dict | None) -> str:
    return "ml" if model is not None else "heuristic"


# Find patient names with the model if we have one, otherwise with the heuristic
def find_names(text: str, model: dict | None, threshold: float = DEFAULT_THRESHOLD) -> list[Entity]:
    if model is None:
        return detect_titled_names(text)
    return detect_ml_names(text, model, threshold)
