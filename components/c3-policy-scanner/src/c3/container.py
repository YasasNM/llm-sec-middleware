"""Wires the parts together. One container per process (one worker, blueprint 19.4)."""
from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass
from pathlib import Path

from c3.config import MgSettings, Settings, YamlConfig, parse_keys
from c3.events import EventEmitter
from c3.indexing.chroma_mirror import ChromaMirror
from c3.indexing.embedder import Embedder, build_embedder
from c3.indexing.indexer import PolicyIndexer
from c3.indexing.registry import OrgIndexRegistry
from c3.indexing.watcher import MarkdownWatcher
from c3.policy.parser import ParserLimits, PolicyParser
from c3.policy_service import PolicyService
from c3.scanner import RagScanner, ScanSettings
from c3.storage.db import Database
from c3.storage.files import PolicyFileStore

log = logging.getLogger("c3")


@dataclass
class Container:
    settings: Settings
    cfg: YamlConfig
    keys: dict[str, str]
    db: Database
    files: PolicyFileStore
    embedder: Embedder
    registry: OrgIndexRegistry
    indexer: PolicyIndexer
    parser: PolicyParser
    policies: PolicyService
    scanner: RagScanner
    emitter: EventEmitter
    generator: object | None = None
    watcher: MarkdownWatcher | None = None

    def start(self) -> None:
        for org in self.policies.known_orgs():
            idx = self.registry.get(org)
            if not idx.snapshot().entries:
                n = self.policies.rebuild_org(org)
                log.info("rebuilt index for org=%s from %d documents", org, n)
        self.emitter.start()
        if self.watcher is not None:
            self.watcher.start()
            self.watcher.scan_existing()

    def stop(self) -> None:
        if self.watcher is not None:
            self.watcher.stop()
        self.policies.wait_idle()
        self.emitter.stop()
        self.db.close()


def build_container(
    settings: Settings | None = None,
    *,
    embedder: Embedder | None = None,
    keys: dict[str, str] | None = None,
    generator=None,
    c4_transport=None,
) -> Container:
    settings = settings or Settings()
    cfg = settings.load_yaml()
    mg = MgSettings()
    db = Database(settings.db_path)
    files = PolicyFileStore(settings.policy_dir)
    embedder = embedder or build_embedder(
        cfg.retrieval.embedder,
        settings.model_dir + "/retriever",
        cfg.retrieval.embedder_fallback_name,
    )
    mirror = ChromaMirror(settings.chroma_dir) if cfg.indexer.vector_backend == "chroma" else None
    registry = OrgIndexRegistry(embedder, mirror)
    indexer = PolicyIndexer(registry, embedder)
    parser = PolicyParser(
        ParserLimits(
            max_pdf_mb=cfg.indexer.max_pdf_mb,
            max_md_kb=cfg.indexer.max_md_kb,
            max_pages=cfg.indexer.max_pages,
            max_clauses=cfg.indexer.max_clauses,
            min_text_chars=cfg.indexer.min_text_chars,
        ),
        cfg.category_severity,
        cfg.default_category,
    )
    policies = PolicyService(db, files, parser, indexer, registry)

    seen_hashes = _load_seen_hashes(Path(settings.model_dir))
    calib = _load_calibration(Path(settings.model_dir) / cfg.generation.model_dir)
    if generator is None and cfg.generation.enabled:
        generator = load_generator(settings, cfg, calib)
    scan_cfg = ScanSettings(
        top_k=cfg.retrieval.top_k, max_k=cfg.retrieval.max_k, s_min=cfg.retrieval.s_min,
        unseen_high_tau=cfg.generation.unseen_high_tau,
        max_new_tokens=cfg.generation.max_new_tokens, two_stage=cfg.generation.two_stage,
        with_quote=cfg.generation.with_quote,
        model_version=getattr(generator, "version", "none"),
        max_response_chars=cfg.api.max_response_chars,
    )
    if calib:
        scan_cfg.sev_tau = calib["sev_tau"]
    scanner = RagScanner(registry, embedder, generator, db, scan_cfg,
                         seen_clause_hashes=seen_hashes)
    emitter = EventEmitter(db, mg.c4_url, mg.service_key, transport=c4_transport)
    watcher = None
    if settings.enable_watcher:
        watcher = MarkdownWatcher(
            policies, settings.watch_dir,
            debounce_ms=cfg.indexer.debounce_ms, use_polling=cfg.indexer.use_polling_observer,
        )
    return Container(
        settings, cfg, keys if keys is not None else parse_keys(mg.accepted_keys), db, files,
        embedder, registry, indexer, parser, policies, scanner, emitter, generator, watcher,
    )


def _load_seen_hashes(model_dir: Path) -> set[str] | None:
    f = model_dir / "trained_clause_hashes.json"
    return set(json.loads(f.read_text())) if f.exists() else None


def _load_calibration(gen_dir: Path) -> dict | None:
    f = gen_dir / "calibration.json"
    return json.loads(f.read_text()) if f.exists() else None


def verify_manifest(model_dir: Path) -> None:
    """Refuse to start if a model file does not match its recorded SHA-256 (T-X07)."""
    f = model_dir / "manifest.json"
    if not f.exists():
        raise RuntimeError(f"no model manifest at {f}")
    manifest = json.loads(f.read_text())
    for part in ("retriever", "generator"):
        for rel, want in manifest.get(part, {}).items():
            path = model_dir / part / rel
            if not path.exists():
                raise RuntimeError(f"model file missing: {path}")
            h = hashlib.sha256()
            with path.open("rb") as fh:
                for chunk in iter(lambda: fh.read(1 << 20), b""):
                    h.update(chunk)
            if h.hexdigest() != want:
                raise RuntimeError(f"model file changed since training: {path}")


def load_generator(settings: Settings, cfg: YamlConfig, calib: dict | None):
    """Load the fine-tuned generator. Missing files or a hash mismatch stop the service."""
    from c3.generation.t5 import T5Generator

    model_dir = Path(settings.model_dir)
    gen_dir = model_dir / cfg.generation.model_dir
    if not gen_dir.exists():
        raise RuntimeError(
            f"generator not found at {gen_dir}; train it or set generation.enabled=false"
        )
    if cfg.generation.verify_hashes:
        verify_manifest(model_dir)
    temp = calib["temperature"] if calib else 1.0
    return T5Generator(gen_dir, device=cfg.generation.device, temperature=temp)
