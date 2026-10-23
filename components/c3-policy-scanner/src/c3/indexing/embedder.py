"""Embedders. The index records `version` so a new retriever forces a full re-embed."""
from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Protocol

import numpy as np


class Embedder(Protocol):
    version: str
    dim: int

    def embed(self, texts: list[str]) -> np.ndarray:
        """Return an (n, dim) float32 array of L2-normalised vectors."""
        ...


def _normalise(mat: np.ndarray) -> np.ndarray:
    norms = np.linalg.norm(mat, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    return (mat / norms).astype(np.float32)


class HashingEmbedder:
    """Deterministic bag-of-words embedder. A stand-in for tests and offline development.

    Its numbers say nothing about real retrieval quality (blueprint Appendix A).
    """

    def __init__(self, dim: int = 512):
        self.dim = dim
        self.version = f"hashing-{dim}"

    def _bucket(self, token: str) -> tuple[int, float]:
        h = int.from_bytes(hashlib.md5(token.encode()).digest()[:8], "big")
        return h % self.dim, 1.0 if (h >> 63) & 1 else -1.0

    def embed(self, texts: list[str]) -> np.ndarray:
        out = np.zeros((len(texts), self.dim), dtype=np.float32)
        for i, text in enumerate(texts):
            tokens = re.findall(r"[a-z0-9]+", text.lower())
            grams = tokens + [f"{a}_{b}" for a, b in zip(tokens, tokens[1:], strict=False)]
            for g in grams:
                idx, sign = self._bucket(g)
                out[i, idx] += sign
        return _normalise(out)


class TransformerEmbedder:
    """Bi-encoder built on plain `transformers`: mean pooling plus L2 normalisation.

    This is the same computation sentence-transformers uses for all-MiniLM-L6-v2, without
    importing that package (it pulls in `datasets` and `pyarrow`, which some locked-down
    machines block). Fine-tuned checkpoints saved with `save_pretrained` load from a folder.
    """

    def __init__(self, model_dir: str | Path | None, fallback_name: str, device: str = "cpu",
                 max_length: int = 256, batch_size: int = 32):
        import torch
        from transformers import AutoModel, AutoTokenizer

        self.torch = torch
        path = Path(model_dir) if model_dir else None
        if path and path.exists() and any(path.iterdir()):
            source, self.version = str(path), f"hf-{path.name}-{_dir_fingerprint(path)}"
        else:
            source, self.version = fallback_name, f"hf-base-{fallback_name.split('/')[-1]}"
        self.tok = AutoTokenizer.from_pretrained(source)
        self.model = AutoModel.from_pretrained(source).to(device).eval()
        self.device, self.max_length, self.batch_size = device, max_length, batch_size
        self.dim = int(self.model.config.hidden_size)

    def embed(self, texts: list[str]) -> np.ndarray:
        torch = self.torch
        out = []
        with torch.no_grad():
            for i in range(0, len(texts), self.batch_size):
                enc = self.tok(texts[i : i + self.batch_size], padding=True, truncation=True,
                               max_length=self.max_length, return_tensors="pt").to(self.device)
                hidden = self.model(**enc).last_hidden_state
                mask = enc["attention_mask"].unsqueeze(-1).to(hidden.dtype)
                pooled = (hidden * mask).sum(1) / mask.sum(1).clamp(min=1e-9)
                out.append(torch.nn.functional.normalize(pooled, dim=-1).float().cpu().numpy())
        if not out:
            return np.zeros((0, self.dim), np.float32)
        return np.concatenate(out).astype(np.float32)


# Kept so existing configs that say `sentence_transformers` keep working.
SentenceTransformerEmbedder = TransformerEmbedder


def _dir_fingerprint(path: Path) -> str:
    """Short hash of file names and sizes. The full SHA-256 manifest check lives in models/."""
    h = hashlib.sha256()
    for p in sorted(path.rglob("*")):
        if p.is_file():
            h.update(f"{p.relative_to(path)}:{p.stat().st_size}".encode())
    return h.hexdigest()[:8]


def build_embedder(kind: str, model_dir: str | None = None, fallback_name: str = "") -> Embedder:
    if kind == "hashing":
        return HashingEmbedder()
    if kind in ("transformers", "sentence_transformers"):
        return TransformerEmbedder(model_dir, fallback_name)
    raise ValueError(f"Unknown embedder kind: {kind}")
