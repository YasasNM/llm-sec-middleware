from __future__ import annotations

from c2.config import Settings


class EmbeddingEncoder:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._model = None
        self.status = "not_loaded"
        self.error: str | None = None

    def load(self) -> None:
        try:
            from sentence_transformers import SentenceTransformer

            self._model = SentenceTransformer(self.settings.embedding_model)
            self.status = "ok"
            self.error = None
        except Exception as exc:  # noqa: BLE001
            self.status = "error"
            self.error = str(exc)
            self._model = None

    def encode(self, text: str) -> list[float]:
        if self._model is None:
            raise RuntimeError(self.error or "embedder not loaded")
        vector = self._model.encode(text, normalize_embeddings=True)
        return vector.tolist()
