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
    demo_enabled: bool = False  # C1_DEMO=1 turns on the temporary /demo page (never in production)
    name_lexicon: bool = True  # C1_NAME_LEXICON=0 turns off the lexicon fallback for names
    lexicon_path: Path = C1_ROOT / "data" / "lexicons" / "names.json"
    # Step 6: block list and events from and to C4 (BLUEPRINT 19.4)
    c4_url: str = "http://localhost:8004"
    service_key: str = ""  # the key C1 presents to C4 (MG_SERVICE_KEY)
    outbox_path: Path = C1_ROOT / "c1.db"  # outbox + decision log; *.db is gitignored
    blocklist_cache_seconds: float = 30.0
    blocklist_max_stale_seconds: float = 300.0
    event_retry_delays: tuple[float, ...] = (0.5, 2.0, 5.0)


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
        demo_enabled=env("C1_DEMO", "0") == "1",
        name_lexicon=env("C1_NAME_LEXICON", "1") == "1",
        c4_url=env("MG_C4_URL", "http://localhost:8004"),
        service_key=env("MG_SERVICE_KEY", ""),
        outbox_path=Path(env("MG_OUTBOX_PATH", str(C1_ROOT / "c1.db"))),
        blocklist_cache_seconds=float(env("C1_BLOCKLIST_CACHE_SECONDS", "30")),
        blocklist_max_stale_seconds=float(env("C1_BLOCKLIST_MAX_STALE_SECONDS", "300")),
    )
