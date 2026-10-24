from __future__ import annotations

from pathlib import Path

from c2.config import Settings
from c2.ensemble.decide import BranchResult


class ClassifierService:
    """ELECTRA-small binary classifier trained on HC-AttackCorpus (Local_dev checkpoint)."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._model = None
        self._tokenizer = None
        self._device = "cpu"
        self._attack_index = 1
        self.status = "not_loaded"
        self.error: str | None = None
        self.load()

    def load(self) -> None:
        path = Path(self.settings.classifier_checkpoint_path)
        weights = path / "model.safetensors"
        if not path.is_dir() or not weights.is_file():
            self.status = "missing"
            self.error = f"checkpoint not found at {path}"
            return
        try:
            import torch
            from transformers import AutoModelForSequenceClassification, AutoTokenizer

            self._device = "cuda" if torch.cuda.is_available() else "cpu"
            self._tokenizer = AutoTokenizer.from_pretrained(path)
            self._model = AutoModelForSequenceClassification.from_pretrained(path)
            self._model.to(self._device)
            self._model.eval()
            self._attack_index = self._resolve_attack_index(self._model.config)
            self.status = "ok"
            self.error = None
        except Exception as exc:  # noqa: BLE001 — fail closed, never crash the API
            self.status = "error"
            self.error = str(exc)
            self._model = None
            self._tokenizer = None

    @staticmethod
    def _resolve_attack_index(config) -> int:
        id2label = getattr(config, "id2label", None) or {}
        mapping = {int(k): str(v).lower() for k, v in id2label.items()}
        for idx, name in mapping.items():
            if name in {"attack", "malicious", "jailbreak", "label_1"}:
                return idx
        return 1 if len(mapping) != 1 else 0

    def predict(self, prompt: str) -> BranchResult:
        if self._model is None or self._tokenizer is None:
            return BranchResult(ok=False, error=self.error or "classifier unavailable")
        try:
            import torch

            encoded = self._tokenizer(
                prompt,
                return_tensors="pt",
                truncation=True,
                padding=True,
                max_length=self.settings.max_length,
            )
            encoded = {k: v.to(self._device) for k, v in encoded.items()}
            with torch.no_grad():
                logits = self._model(**encoded).logits
                probs = torch.softmax(logits, dim=-1)[0]
            score = float(probs[self._attack_index].item())
            return BranchResult(ok=True, score=score)
        except Exception as exc:  # noqa: BLE001
            return BranchResult(ok=False, error=str(exc))
