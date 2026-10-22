"""C1 settings, read from environment variables (BLUEPRINT 19.4). Defaults suit a laptop.

MG_* names are shared by every service (contracts section 5). C1_* names are C1's own.
Keys come from the environment or a .env file that is never committed.
"""

import os
from dataclasses import dataclass
from pathlib import Path

C1_ROOT = Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class Settings:
    """Everything C1 needs to start. Build it with load_settings()."""

    service_name: str
    accepted_keys: str
    model_path: Path
    manifest_path: Path
    name_threshold: float
    config_folder: Path


# Read one setting from the environment, or use the default
def env(name: str, default: str) -> str:
    return os.environ.get(name, default)


# Build the settings from environment variables
def load_settings() -> Settings:
    return Settings(
        service_name=env("MG_SERVICE_NAME", "c1"),
        accepted_keys=env("MG_ACCEPTED_KEYS", ""),
        model_path=Path(env("C1_MODEL_PATH", str(C1_ROOT / "models" / "c1_model.joblib"))),
        manifest_path=Path(env("C1_MODEL_MANIFEST", str(C1_ROOT / "models" / "manifest.json"))),
        name_threshold=float(env("C1_NAME_THRESHOLD", "0.65")),
        config_folder=Path(env("C1_CONFIG_FOLDER", str(C1_ROOT / "configs"))),
    )
