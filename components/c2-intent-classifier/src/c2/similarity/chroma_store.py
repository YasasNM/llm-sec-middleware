from __future__ import annotations

from pathlib import Path

from c2.config import Settings, get_thresholds
from c2.embedding.encoder import EmbeddingEncoder
from c2.ensemble.decide import BranchResult


class SimilarityStore:
    def __init__(self, settings: Settings, encoder: EmbeddingEncoder) -> None:
        self.settings = settings
        self.encoder = encoder
        self._client = None
        self._collection = None
        self.status = "not_loaded"
        self.vector_count = 0
        self.error: str | None = None
        self.load()

    def load(self) -> None:
        try:
            import chromadb

            Path(self.settings.chroma_dir).mkdir(parents=True, exist_ok=True)
            self._client = chromadb.PersistentClient(path=str(self.settings.chroma_dir))
            self._collection = self._client.get_or_create_collection(
                name=self.settings.chroma_collection_name,
                metadata={"hnsw:space": "cosine"},
            )
            self.vector_count = int(self._collection.count())
            self.status = "ok" if self.vector_count > 0 else "empty"
            self.error = None
        except Exception as exc:  # noqa: BLE001
            self.status = "error"
            self.error = str(exc)
            self._collection = None

    def search(self, prompt: str) -> BranchResult:
        if self._collection is None:
            return BranchResult(ok=False, error=self.error or "chroma unavailable")
        if self.vector_count == 0:
            # Empty store is not a crash; classifier-only path is used instead.
            return BranchResult(ok=False, error="chroma collection empty")
        try:
            if self.encoder._model is None:
                self.encoder.load()
            embedding = self.encoder.encode(prompt)
            top_k = get_thresholds().top_k
            result = self._collection.query(
                query_embeddings=[embedding],
                n_results=min(top_k, self.vector_count),
                include=["distances"],
            )
            ids = (result.get("ids") or [[]])[0]
            distances = (result.get("distances") or [[]])[0]
            scores = [max(0.0, min(1.0, 1.0 - float(d))) for d in distances]
            top = scores[0] if scores else 0.0
            return BranchResult(
                ok=True,
                score=top,
                nearest_ids=list(ids),
                nearest_scores=scores,
            )
        except Exception as exc:  # noqa: BLE001
            return BranchResult(ok=False, error=str(exc))
