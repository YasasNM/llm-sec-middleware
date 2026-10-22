"""Settings. Values come from environment variables (see .env.example)."""

from __future__ import annotations

from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

_COMPONENT_ROOT = Path(__file__).resolve().parents[2]
_REPO_ROOT = Path(__file__).resolve().parents[4]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="C4_", env_file=".env", extra="ignore", populate_by_name=True
    )

    mongo_url: str = "mongodb://localhost:27017/c4"
    rules_file: Path = _COMPONENT_ROOT / "configs" / "rules.yaml"
    contracts_dir: Path = _REPO_ROOT / "contracts"
    future_skew_minutes: int = 5
    # "c1:key1,c2:key2,c3:key3,gateway:key4" (same name as the shared library, MG_ prefix)
    accepted_keys: str = Field(default="", validation_alias="MG_ACCEPTED_KEYS")

    def key_map(self) -> dict[str, str]:
        """Return {key: caller_name}."""
        out: dict[str, str] = {}
        for pair in self.accepted_keys.split(","):
            if ":" in pair:
                name, key = pair.split(":", 1)
                if name.strip() and key.strip():
                    out[key.strip()] = name.strip()
        return out
