"""Shared helpers for C1 tests: settings and a test client without needing a real model file."""

from pathlib import Path

from fastapi.testclient import TestClient

from c1.config import C1_ROOT, Settings
from c1.main import create_app

GATEWAY_KEY = "test-gateway-key"
OTHER_KEY = "test-c2-key"
GATEWAY_HEADERS = {"X-Service-Key": GATEWAY_KEY}


# Settings for tests: known keys, the real configs, and a model path that may not exist.
# The outbox and decision log go into the test's own temp folder, and retries do not wait.
def settings_for_tests(model_folder: Path, demo: bool = False) -> Settings:
    return Settings(
        service_name="c1",
        accepted_keys=f"gateway:{GATEWAY_KEY},c2:{OTHER_KEY}",
        model_path=model_folder / "c1_model.joblib",
        manifest_path=model_folder / "manifest.json",
        name_threshold=0.65,
        config_folder=C1_ROOT / "configs",
        demo_enabled=demo,
        c4_url="http://c4.test",
        service_key="test-c1-key",
        outbox_path=model_folder / "c1.db",
        event_retry_delays=(0, 0, 0),
    )


# A test client for the C1 app; with an empty folder the heuristic name detector is used
def make_client(model_folder: Path, demo: bool = False) -> TestClient:
    return TestClient(create_app(settings_for_tests(model_folder, demo)))
