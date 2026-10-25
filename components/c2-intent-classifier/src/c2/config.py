from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import yaml
from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

COMPONENT_ROOT = Path(__file__).resolve().parents[2]
REPO_ROOT = COMPONENT_ROOT.parents[1]
WORKSPACE_ROOT = REPO_ROOT.parent
LOCAL_DEV_CHECKPOINT = (
    WORKSPACE_ROOT / "Local_dev" / "llm-c2-tester" / "c2_classifier_final"
)
PACKAGED_CHECKPOINT = COMPONENT_ROOT / "models" / "classifier"


def _default_checkpoint() -> Path:
    packaged = PACKAGED_CHECKPOINT / "model.safetensors"
    if packaged.is_file():
        return PACKAGED_CHECKPOINT
    if (LOCAL_DEV_CHECKPOINT / "model.safetensors").is_file():
        return LOCAL_DEV_CHECKPOINT
    return PACKAGED_CHECKPOINT


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(COMPONENT_ROOT / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
        populate_by_name=True,
    )

    service_name: str = Field(default="c2", alias="MG_SERVICE_NAME")
    service_key: str = Field(default="change-me-c2", alias="MG_SERVICE_KEY")
    accepted_keys: str = Field(default="gateway:change-me-gateway", alias="MG_ACCEPTED_KEYS")
    c4_url: str = Field(default="http://127.0.0.1:8004", alias="MG_C4_URL")
    outbox_path: Path = Field(default=COMPONENT_ROOT / "data" / "c2.db", alias="MG_OUTBOX_PATH")

    model_version: str = Field(default="c2-electra-base-v1", alias="C2_MODEL_VERSION")
    classifier_checkpoint_path: Path = Field(
        default_factory=_default_checkpoint, alias="C2_CLASSIFIER_CHECKPOINT_PATH"
    )
    chroma_dir: Path = Field(default=COMPONENT_ROOT / "data" / "chroma", alias="C2_CHROMA_DIR")
    chroma_collection_name: str = Field(default="hc_attack_vectors", alias="C2_CHROMA_COLLECTION")
    embedding_model: str = Field(default="sentence-transformers/all-MiniLM-L6-v2", alias="C2_EMBEDDING_MODEL")
    thresholds_file: Path = Field(
        default=COMPONENT_ROOT / "configs" / "thresholds.yaml", alias="C2_THRESHOLDS_FILE"
    )
    allow_unauthenticated: bool = Field(default=False, alias="C2_ALLOW_UNAUTHENTICATED")
    dev_ui: bool = Field(default=False, alias="C2_DEV_UI")
    max_length: int = Field(default=256, alias="C2_MAX_LENGTH")
    host: str = Field(default="0.0.0.0", alias="C2_HOST")
    port: int = Field(default=8002, alias="C2_PORT")

    @model_validator(mode="after")
    def resolve_checkpoint(self) -> Settings:
        path = Path(self.classifier_checkpoint_path)
        if not path.name or str(path) in {".", ""}:
            self.classifier_checkpoint_path = _default_checkpoint()
        return self


class Thresholds:
    def __init__(self, path: Path) -> None:
        raw = yaml.safe_load(path.read_text()) if path.is_file() else {}
        self.near_exact_threshold = float(raw.get("near_exact_threshold", 0.97))
        self.ensemble_threshold = float(raw.get("ensemble_threshold", 0.75))
        self.similarity_weight = float(raw.get("similarity_weight", 0.5))
        self.classifier_weight = float(raw.get("classifier_weight", 0.5))
        self.top_k = int(raw.get("top_k", 5))


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


@lru_cache(maxsize=1)
def get_thresholds() -> Thresholds:
    return Thresholds(get_settings().thresholds_file)
