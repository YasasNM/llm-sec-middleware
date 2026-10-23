from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))

from c3_helpers import KEY, CountingEmbedder  # noqa: E402

from c3.config import Settings  # noqa: E402
from c3.container import build_container  # noqa: E402
from c3.indexing.indexer import PolicyIndexer  # noqa: E402
from c3.indexing.registry import OrgIndexRegistry  # noqa: E402
from c3.policy.parser import ParserLimits, PolicyParser  # noqa: E402


@pytest.fixture
def embedder():
    return CountingEmbedder()


@pytest.fixture
def parser():
    return PolicyParser(
        ParserLimits(), {"data_handling": "high", "prescribing": "high"}, "general"
    )


@pytest.fixture
def registry(embedder):
    return OrgIndexRegistry(embedder)


@pytest.fixture
def indexer(registry, embedder):
    return PolicyIndexer(registry, embedder)


@pytest.fixture
def make_container(tmp_path, monkeypatch):
    """Build a container on a temp folder. Call stop() through the yielded list."""
    made = []
    monkeypatch.setenv("MG_SERVICE_KEY", "c3-to-c4-key")

    def _make(*, embedder=None, backend="memory", watcher=False, subdir="run", generator=None,
              c4_transport=None, **cfg_over):
        base = tmp_path / subdir
        settings = Settings(
            db_path=str(base / "c3.db"),
            chroma_dir=str(base / "chroma"),
            policy_dir=str(base / "policies"),
            watch_dir=str(base / "watch"),
            model_dir=str(base / "models"),
            enable_watcher=watcher,
        )
        c = build_container(settings, embedder=embedder or CountingEmbedder(),
                            keys={KEY: "gateway"}, generator=generator, c4_transport=c4_transport)
        if backend != "memory":
            # swap in the Chroma mirror for this container
            from c3.indexing.chroma_mirror import ChromaMirror

            c.registry.mirror = ChromaMirror(settings.chroma_dir)
        made.append(c)
        return c

    yield _make
    for c in made:
        try:
            c.stop()
        except Exception:
            pass

