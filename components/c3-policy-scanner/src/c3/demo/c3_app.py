"""The C3 service as the demo runs it, with the trained models.

    uvicorn c3.demo.c3_app:app --port 8003
"""
from __future__ import annotations

import os
from pathlib import Path

from c3.config import COMPONENT_ROOT, Settings
from c3.container import build_container
from c3.main import create_app

MISSING = (
    "The trained models were not found in {path}.\n"
    "Train them (see docs/runbooks/c3-training-colab.md) or copy the models folder from Drive "
    "into components/c3-policy-scanner/models/ so that it contains generator/, retriever/ and "
    "manifest.json."
)


def models_available(model_dir: Path) -> bool:
    return ((model_dir / "generator" / "model.safetensors").exists()
            and (model_dir / "retriever" / "model.safetensors").exists()
            and (model_dir / "manifest.json").exists())


def create():
    model_dir = Path(os.getenv("C3_MODEL_DIR", COMPONENT_ROOT / "models"))
    if not models_available(model_dir):
        raise RuntimeError(MISSING.format(path=model_dir))
    os.environ["C3_CONFIG_FILE"] = str(COMPONENT_ROOT / "configs" / "demo.yaml")
    return create_app(build_container(Settings()))


app = create()
