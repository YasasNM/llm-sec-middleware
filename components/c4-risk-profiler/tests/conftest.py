from __future__ import annotations

import itertools
from pathlib import Path

import pytest
from c4.config import Settings
from c4.core import clock
from c4.core.ruleset import load_ruleset
from c4.db.mongo import get_db
from c4.main import create_app
from fastapi.testclient import TestClient

KEYS = {"c1": "key-c1", "c2": "key-c2", "c3": "key-c3", "gateway": "key-gw"}
RULES_FILE = Path(__file__).resolve().parents[1] / "configs" / "rules.yaml"
_counter = itertools.count()


@pytest.fixture(autouse=True)
def real_time():
    """Every test starts with the real clock (one test moves it forward)."""
    clock.reset()
    yield
    clock.reset()


@pytest.fixture
def rs():
    return load_ruleset(RULES_FILE)


@pytest.fixture
def db():
    return get_db(f"mongomock://test{next(_counter)}/c4")


@pytest.fixture
def client(db):
    settings = Settings(
        accepted_keys=",".join(f"{n}:{k}" for n, k in KEYS.items()), mongo_url="mongomock://x/c4"
    )
    return TestClient(create_app(settings, db))


@pytest.fixture
def h():
    """Headers per caller."""
    return {n: {"X-Service-Key": k} for n, k in KEYS.items()}
