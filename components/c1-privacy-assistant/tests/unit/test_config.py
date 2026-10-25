"""Unit tests for C1 settings: the service always loads the own-corpus name model."""

from c1.config import load_settings


def test_default_model_is_the_own_corpus_model(monkeypatch):
    monkeypatch.delenv("C1_MODEL_PATH", raising=False)
    assert load_settings().model_path.name == "c1_model.joblib"


def test_explicit_model_path_is_respected(monkeypatch, tmp_path):
    monkeypatch.setenv("C1_MODEL_PATH", str(tmp_path / "other.joblib"))
    assert load_settings().model_path.name == "other.joblib"


def test_demo_is_off_by_default(monkeypatch):
    monkeypatch.delenv("C1_DEMO", raising=False)
    assert load_settings().demo_enabled is False
