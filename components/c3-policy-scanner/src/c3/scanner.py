"""RagScanner: the whole two-stage scan (blueprint 8.1, 12.4, 12.5; FR3 to FR9, FR14, FR16, FR25).

Order is fixed (NFR4):
  normalise -> retrieve -> label context -> stage 1 (probability) -> stage 2 only above the
  threshold -> grounding check -> leak re-check (answer AND generated text) -> verdict.

Every exception ends in a withheld answer (`error_fail_closed`). No event is sent for those.
"""
from __future__ import annotations

import hashlib
import logging
import threading
import time
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Protocol

from c3.generation.rag_format import (
    DEFAULT_SEV_TAU,
    ContextItem,
    Final,
    build_prompt,
    effective_tau,
    finalize,
    first_sentence,
    fold_text,
    label_context,
    parse_output,
    template_reason,
)
from c3.indexing.embedder import Embedder
from c3.indexing.registry import OrgIndexRegistry
from c3.leakcheck import LeakChecker, normalise_text
from c3.storage.db import Database

log = logging.getLogger("c3.scan")

USER_MESSAGE = "OUTPUT_WITHHELD"
LOW_ASSURANCE_STATUSES = ("repaired_quote", "repaired_citation")


class Generator(Protocol):
    version: str

    def stage1(self, prompt: str): ...           # returns an object with .gap and .ms
    def stage2(self, s1, max_new_tokens: int = 40) -> tuple[str, float]: ...
    def probability(self, gap: float) -> float: ...


class ScanError(Exception):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


@dataclass
class ScanSettings:
    top_k: int = 5
    max_k: int = 8
    s_min: float = 0.20                       # best similarity below this lowers assurance
    sev_tau: dict[str, float] = field(default_factory=lambda: dict(DEFAULT_SEV_TAU))
    unseen_high_tau: float = 0.30             # lower bar for unseen high-severity clauses
    max_new_tokens: int = 40
    two_stage: bool = True
    with_quote: bool = True
    model_version: str = "c3-gen-unversioned"
    max_response_chars: int = 50000


class RagScanner:
    def __init__(
        self,
        registry: OrgIndexRegistry,
        embedder: Embedder,
        generator: Generator | None,
        db: Database,
        settings: ScanSettings | None = None,
        leak: LeakChecker | None = None,
        seen_clause_hashes: Iterable[str] | None = None,
    ):
        self.registry, self.embedder, self.generator, self.db = registry, embedder, generator, db
        self.cfg = settings or ScanSettings()
        self.leak = leak or LeakChecker()
        # None means "unknown": every clause counts as seen (no training manifest loaded)
        self.seen = None if seen_clause_hashes is None else set(seen_clause_hashes)
        self._gen_lock = threading.Lock()      # one generator pass at a time per worker

    # ---- public -----------------------------------------------------------------------
    def scan(self, req: dict) -> dict:
        t0 = time.perf_counter()
        try:
            verdict = self._scan(req, t0)
        except ScanError as exc:
            verdict = self._error(req, exc.code, t0)
        except Exception:
            log.exception("scan failed for request %s", req.get("request_id"))
            verdict = self._error(req, "SCAN_ERROR", t0)
        try:
            self._audit(req, verdict)
        except Exception:
            log.exception("audit write failed for request %s", req.get("request_id"))
        return verdict

    # ---- pipeline ---------------------------------------------------------------------
    def _scan(self, req: dict, t0: float) -> dict:
        cfg, timings = self.cfg, {}
        text = normalise_text(req["response_text"])
        if not text.strip():
            raise ScanError("EMPTY_RESPONSE")
        if self.generator is None:
            raise ScanError("MODEL_UNAVAILABLE")

        index = self.registry.get(req["org_id"], create=False)
        if index is None:
            raise ScanError("INDEX_UNAVAILABLE")
        snap = index.snapshot()                 # one complete snapshot for the whole scan
        if not snap.entries:
            raise ScanError("INDEX_UNAVAILABLE")
        if snap.retriever_version != self.embedder.version:
            raise ScanError("RETRIEVER_MISMATCH")

        # 1. retrieve (FR3)
        t = time.perf_counter()
        k = min(cfg.top_k, cfg.max_k)
        qvec = self.embedder.embed([text])[0]
        cats = req.get("categories") or None
        hits = index.search(qvec, k=k, categories=cats, snap=snap)
        if not hits and cats:
            hits = index.search(qvec, k=k, snap=snap)       # filter matched nothing: unfiltered
        timings["retrieve"] = _ms(t)
        if not hits:
            raise ScanError("NO_CONTEXT")

        # 2. labelled context in rank order
        clauses = [h.clause for h in hits]
        ctx = label_context(clauses)
        entry = {h.clause.clause_id: snap.entries[h.clause.clause_id] for h in hits}
        sims = {h.clause.clause_id: h.similarity for h in hits}
        prompt = build_prompt(text, ctx)

        # 3. stage 1: verdict probability (FR25)
        with self._gen_lock:
            s1 = self.generator.stage1(prompt)
            timings["encode_and_verdict_step"] = float(s1.ms)
            p = self.generator.probability(s1.gap)
            tau, unseen_high = self._tau(ctx)

            # 4. stage 2 only above the threshold
            decoded, gen_text, out_tokens = False, None, 0
            if (p >= tau) or not cfg.two_stage:
                gen_text, ms = self.generator.stage2(s1, cfg.max_new_tokens)
                decoded, out_tokens = True, len(gen_text.split())
                timings["decode"] = float(ms)

        # 5. grounding check (FR24)
        t = time.perf_counter()
        parsed = parse_output(gen_text) if decoded else None
        final = finalize(parsed, ctx, p, tau)
        if final.verdict == "error":
            raise ScanError(final.error_code or "SCAN_ERROR")
        timings["verify"] = _ms(t)

        # show original wording where the folded quote is a whole sentence of the clause
        for c in final.cited:
            clause_text = next(h.clause.text for h in hits if h.clause.clause_id == c.clause_id)
            raw = first_sentence(clause_text)
            if fold_text(raw).strip(" .") == c.quote.strip(" ."):
                c.quote = raw

        # 6. leak re-check on the answer and on generated text (FR7, FR27)
        t = time.perf_counter()
        manifest = req.get("placeholder_manifest") or []
        leak = self.leak.check(text, manifest)
        reason, reason_replaced = final.reason, False
        entry_text = {h.clause.clause_id: h.clause.text for h in hits}
        if final.verdict == "violation":
            gen_parts = [final.reason] + [c.quote for c in final.cited]
            if self.leak.check_generated(gen_parts, manifest):
                top = final.cited[0]
                reason, reason_replaced = template_reason(top.title), True
                for c in final.cited:             # the real first sentence cannot carry one
                    c.quote = first_sentence(entry_text[c.clause_id])
        timings["leak"] = _ms(t)

        # 7. decide
        violation = final.verdict == "violation" or leak.detected
        source = "generator" if final.verdict == "violation" else "leak_check"
        out = self._build(req, text, final, entry, sims, leak, violation, source, reason,
                          reason_replaced, p, tau, unseen_high, decoded, out_tokens, snap,
                          timings, t0)
        out["_reason_replaced"] = reason_replaced
        return out

    # ---- helpers ----------------------------------------------------------------------
    def _tau(self, ctx: dict[str, ContextItem]) -> tuple[float, bool]:
        tau = effective_tau(ctx, self.cfg.sev_tau)
        unseen_high = False
        if self.seen is not None:
            for it in ctx.values():
                if it.severity == "high" and self._hash_of(it) not in self.seen:
                    unseen_high = True
        if unseen_high:
            tau = min(tau, self.cfg.unseen_high_tau)
        return tau, unseen_high

    @staticmethod
    def _hash_of(it: ContextItem) -> str:
        from c3.policy.models import content_hash

        return content_hash(it.title, it.text)

    def _build(self, req, text, final: Final, entry, sims, leak, violation, source, reason,
               reason_replaced, p, tau, unseen_high, decoded, out_tokens, snap, timings, t0):
        cited = []
        for c in final.cited:
            e = entry[c.clause_id]
            doc = self.db.get_document(req["org_id"], e.doc_id) or {}
            cited.append({
                "clause_id": c.clause_id, "doc_id": e.doc_id,
                "doc_name": doc.get("name", e.doc_id), "title": c.title, "quote": c.quote,
                "citation_status": c.citation_status, "severity": c.severity,
                "retrieval_rank": c.rank,
            })
        cited.sort(key=lambda c: c["retrieval_rank"])        # top-ranked first (IR2)

        low = bool(unseen_high)
        if any(c["citation_status"] in LOW_ASSURANCE_STATUSES for c in cited):
            low = True
        if self.seen is not None and any(
            entry[c["clause_id"]].clause.content_hash not in self.seen for c in cited
        ):
            low = True
        if max(sims.values(), default=0.0) < self.cfg.s_min:
            low = True

        return {
            "request_id": req["request_id"],
            "c3_verdict": "VIOLATION" if violation else "CLEAN",
            "clause_id": cited[0]["clause_id"] if (violation and cited) else None,
            "cited_clauses": cited if violation else [],
            "leaked_pii_detected": leak.detected,
            "leaked_pii_count": leak.count,
            "decision_source": source if violation else "generator",
            "assurance": "low" if low else "high",
            "confidence": round(float(p), 4),
            "rationale": reason if (violation and cited) else None,
            "user_message_code": USER_MESSAGE if violation else None,
            "clean_response": None if violation else req["response_text"],
            "policy_index_version": snap.version,
            "model_version": self.cfg.model_version,
            "retriever_version": snap.retriever_version,
            "latency_ms": int(_ms(t0)),
            "latency_breakdown_ms": {k: round(v, 1) for k, v in timings.items()},
            "leak_check": leak.to_json(),
            "generation": {"mode": "two_stage" if self.cfg.two_stage else "always_decode",
                           "p_violation": round(float(p), 4), "tau": round(float(tau), 4),
                           "decoded": decoded, "output_tokens": out_tokens},
        }

    def _error(self, req: dict, code: str, t0: float) -> dict:
        return {
            "request_id": req["request_id"], "c3_verdict": "VIOLATION", "clause_id": None,
            "cited_clauses": [], "leaked_pii_detected": False, "leaked_pii_count": 0,
            "decision_source": "error_fail_closed", "error_code": code,
            "user_message_code": USER_MESSAGE, "clean_response": None,
            "latency_ms": int(_ms(t0)),
        }

    def _audit(self, req: dict, v: dict) -> None:
        """Hash only. No response text, no entity values (NFR11)."""
        replaced = int(bool(v.pop("_reason_replaced", False)))
        gen = v.get("generation", {})
        self.db.save_scan(
            {
                "request_id": v["request_id"], "org_id": req["org_id"], "user_id": req["user_id"],
                "session_id": req["session_id"],
                "response_hash": hashlib.sha256(req["response_text"].encode()).hexdigest(),
                "verdict": v["c3_verdict"], "decision_source": v["decision_source"],
                "assurance": v.get("assurance"), "confidence": v.get("confidence"),
                "tau": gen.get("tau"), "decoded": int(bool(gen.get("decoded"))),
                "output_tokens": gen.get("output_tokens"),
                "leaked_pii_count": v["leaked_pii_count"], "reason_replaced": replaced,
                "error_code": v.get("error_code"), "rationale": v.get("rationale"),
                "policy_index_version": v.get("policy_index_version"),
                "model_version": v.get("model_version"),
                "retriever_version": v.get("retriever_version"),
                "latency_total_ms": v["latency_ms"],
                "thresholds_json": _thresholds(self.cfg),
                "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            },
            v["cited_clauses"],
        )


def _thresholds(cfg: ScanSettings) -> str:
    import json

    return json.dumps({"sev_tau": cfg.sev_tau, "unseen_high_tau": cfg.unseen_high_tau,
                       "top_k": cfg.top_k})


def _ms(t: float) -> float:
    return (time.perf_counter() - t) * 1000.0
