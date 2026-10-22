from __future__ import annotations

from dataclasses import dataclass

from c2.classifier.predict import ClassifierService
from c2.events import EventSender
from c2.log.verdicts import VerdictLog
from c2.similarity.chroma_store import SimilarityStore


@dataclass
class Runtime:
    classifier: ClassifierService
    similarity: SimilarityStore
    events: EventSender
    verdicts: VerdictLog


_RUNTIME: Runtime | None = None


def set_runtime(runtime: Runtime) -> None:
    global _RUNTIME
    _RUNTIME = runtime


def get_runtime() -> Runtime:
    if _RUNTIME is None:
        raise RuntimeError("C2 runtime is not initialised")
    return _RUNTIME
