"""Settings: C3_* and MG_* environment variables plus YAML config (blueprint 19.2, 19.3, NFR5)."""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import yaml
from pydantic import BaseModel
from pydantic_settings import BaseSettings, SettingsConfigDict

COMPONENT_ROOT = Path(__file__).resolve().parents[2]


class RetrievalCfg(BaseModel):
    top_k: int = 5
    max_k: int = 8
    embedder: str = "hashing"
    embedder_dir: str = "models/retriever"
    embedder_fallback_name: str = "sentence-transformers/all-MiniLM-L6-v2"
    s_min: float = 0.20


class IndexerCfg(BaseModel):
    debounce_ms: int = 400
    use_polling_observer: bool = False
    max_pdf_mb: float = 20
    max_md_kb: float = 1024
    max_pages: int = 200
    max_clauses: int = 500
    min_text_chars: int = 80
    vector_backend: str = "memory"


class ApiCfg(BaseModel):
    max_response_chars: int = 50000
    timeout_s: float = 5
    fail_mode: str = "closed"


class GenerationCfg(BaseModel):
    enabled: bool = False             # production sets true; then missing models stop start-up
    model_dir: str = "generator"      # under C3_MODEL_DIR
    device: str = "cpu"
    two_stage: bool = True
    with_quote: bool = True
    max_new_tokens: int = 40
    unseen_high_tau: float = 0.30
    verify_hashes: bool = True        # check models/manifest.json at start (T-X07)


class YamlConfig(BaseModel):
    retrieval: RetrievalCfg = RetrievalCfg()
    indexer: IndexerCfg = IndexerCfg()
    api: ApiCfg = ApiCfg()
    generation: GenerationCfg = GenerationCfg()
    category_severity: dict[str, str] = {}
    default_category: str = "general"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="C3_", env_file=".env", extra="ignore")

    config_file: str = str(COMPONENT_ROOT / "configs" / "default.yaml")
    severity_file: str = str(COMPONENT_ROOT / "configs" / "severity.yaml")
    db_path: str = str(COMPONENT_ROOT / "var" / "c3.db")
    chroma_dir: str = str(COMPONENT_ROOT / "var" / "chroma")
    model_dir: str = str(COMPONENT_ROOT / "models")
    policy_dir: str = str(COMPONENT_ROOT / "var" / "policies")
    watch_dir: str = str(COMPONENT_ROOT / "var" / "policies_watch")
    enable_watcher: bool = True
    log_level: str = "INFO"
    def load_yaml(self) -> YamlConfig:
        data: dict = {}
        for f in (self.config_file, self.severity_file):
            p = Path(f)
            if p.exists():
                data |= yaml.safe_load(p.read_text(encoding="utf-8")) or {}
        return YamlConfig(**data)


class MgSettings(BaseSettings):
    """Shared MG_* variables, read from the same environment as the other services."""

    model_config = SettingsConfigDict(env_prefix="MG_", env_file=".env", extra="ignore")

    service_name: str = "c3"
    service_key: str = ""            # the key C3 presents when it calls C4
    accepted_keys: str = ""
    c4_url: str = "http://c4:8004"
    outbox_path: str = ""


def parse_keys(raw: str) -> dict[str, str]:
    """'gateway:abc,admin:def' -> {'abc': 'gateway', 'def': 'admin'} (key -> caller)."""
    out: dict[str, str] = {}
    for part in filter(None, (p.strip() for p in raw.split(","))):
        caller, _, key = part.partition(":")
        if caller and key:
            out[key] = caller
    return out


@lru_cache
def get_settings() -> Settings:
    return Settings()
