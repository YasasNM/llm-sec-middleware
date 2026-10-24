"""Edit-to-enforcement latency harness (FR10, M8, blueprint 12.8).

T_index   : change accepted (upload request, or first file event) -> new snapshot visible.
T_enforce : same start -> a probe query sees the change.

Until the scan pipeline exists, "sees the change" means the retriever's top-1 clause for a
probe sentence flips (add/edit) or stops being that clause (remove). When /v1/scan is added,
pass `probe=` a callable that runs a real scan, and the same harness measures the verdict flip.

Run:  python -m c3.bench.latency --trials 50 --path upload --path file
"""
from __future__ import annotations

import argparse
import hashlib
import json
import statistics
import tempfile
import time
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from c3.config import Settings
from c3.container import Container, build_container

ORG = "bench-org"          # prefix; each entry path gets its own organisation
BASE = [
    ("1", "Purpose", "This guideline sets rules for using AI tools in clinical work."),
    ("2", "Scope", "It applies to all clinicians, nurses and administrative staff."),
    ("3", "Patient Data", "Staff must not paste identifiable patient data into any AI tool."),
    ("4", "Prescribing", "An AI tool must never be the sole basis for a prescribing decision."),
    ("5", "Incident Reporting", "Suspected data exposure must be reported within 24 hours."),
]
CHANGE_TYPES = ("add", "edit", "remove")


def percentile(values: list[float], q: float) -> float:
    if not values:
        return float("nan")
    s = sorted(values)
    pos = (len(s) - 1) * q
    lo, hi = int(pos), min(int(pos) + 1, len(s) - 1)
    return s[lo] + (s[hi] - s[lo]) * (pos - lo)


def _md(clauses) -> str:
    return "# Bench policy\n\n" + "\n".join(f"## {n} {t}\n\n{b}\n" for n, t, b in clauses)


def _probe_text(token: str) -> str:
    return f"Obligation {token}: staff must archive {token} records for seven years."


class LatencyBench:
    org: str = ORG

    def __init__(self, container: Container, probe: Callable[[str, str], str | None] | None = None):
        """`probe(org_id, text)` returns the top clause id for the text (default: retriever)."""
        self.c = container
        self._probe = probe or self._retrieval_probe

    def _retrieval_probe(self, org_id: str, text: str) -> str | None:
        vec = self.c.embedder.embed([text])[0]
        hits = self.c.registry.get(org_id).search(vec, k=1)
        return hits[0].clause.clause_id if hits else None

    def _poll(self, text: str, done: Callable[[str | None], bool], deadline: float) -> float | None:
        """Return the time at which `done(top_clause_id)` first became true."""
        while time.time() < deadline:
            if done(self._probe(self.org, text)):
                return time.time()
            time.sleep(0.002)
        return None

    # ---- one trial --------------------------------------------------------------------
    def trial(self, path: str, change_type: str, timeout_s: float = 20.0) -> dict:
        token = uuid.uuid4().hex[:8]
        probe_text = _probe_text(token)
        new_clause = ("6", f"Archive {token}", probe_text)
        edited = [(n, t, probe_text if n == "5" else b) for n, t, b in BASE]

        # Put the live document in the "before" state, outside the timed section
        self._ensure_state(path, BASE + [new_clause] if change_type == "remove" else BASE)
        previous_run = self.c.policies.last_run.get(self.org)

        after = {"add": BASE + [new_clause], "edit": edited, "remove": BASE}[change_type]
        id6, id5 = self._clause_id_for("6"), self._clause_id_for("5")
        done = {
            "add": lambda top: top == id6,
            "edit": lambda top: top == id5,
            "remove": lambda top: top != id6,
        }[change_type]

        t0 = time.time()                      # change accepted / file saved
        self._submit(path, after)
        t_enf = self._poll(probe_text, done, t0 + timeout_s)

        run = self._wait_new_run(previous_run, t0 + timeout_s)
        return {
            "path": path, "change_type": change_type,
            "t_index_ms": run.t_index_ms if run else None,
            "t_enforce_ms": None if t_enf is None else (t_enf - t0) * 1000.0,
            "stages": run.stages_ms() if run else {}, "ok": t_enf is not None,
        }

    def _wait_new_run(self, previous, deadline: float):
        while time.time() < deadline:
            run = self.c.policies.last_run.get(self.org)
            if run is not None and run is not previous:
                return run
            time.sleep(0.005)
        return None

    # ---- plumbing ---------------------------------------------------------------------
    def _clause_id_for(self, number: str) -> str:
        doc = self.c.policies.list_documents(self.org)[0]
        return f"{doc['doc_code']}-{number}"

    def _submit(self, path: str, clauses) -> None:
        svc = self.c.policies
        data = _md(clauses).encode()
        if path == "file":
            f = Path(self.c.watcher.root) / self.org / "bench.md"
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_bytes(data)
        else:
            docs = svc.list_documents(self.org)
            if docs:
                svc.replace(self.org, docs[0]["doc_id"], "bench.md", data)
            else:
                svc.upload(self.org, "bench.md", data)

    def _live_sha(self) -> str | None:
        docs = self.c.policies.list_documents(self.org)
        return docs[0]["sha256"] if docs and docs[0]["status"] != "processing" else None

    def _ensure_state(self, path: str, clauses, timeout_s: float = 30.0) -> None:
        """Bring the live document to `clauses`, outside the timed section.

        Waits until the stored hash matches, so a late debounce cannot leak into a trial.
        A write that would not change anything is skipped (it would only create a stray event).
        """
        data = _md(clauses).encode()
        want = hashlib.sha256(data).hexdigest()
        if self._live_sha() != want:
            self._submit(path, clauses)
            end = time.time() + timeout_s
            while time.time() < end and self._live_sha() != want:
                time.sleep(0.01)
        self.c.policies.wait_idle()
        time.sleep(self.c.watcher.debounce_s + 0.1 if self.c.watcher is not None else 0.0)

    # ---- many trials ------------------------------------------------------------------
    def run(self, trials: int, paths: list[str]) -> dict:
        results = []
        for path in paths:
            self.org = f"{ORG}-{path}"
            for change_type in CHANGE_TYPES:
                for _ in range(trials):
                    results.append(self.trial(path, change_type))
        return summarise(results)


def summarise(results: list[dict]) -> dict:
    out: dict = {"created_at": datetime.now(UTC).isoformat(), "groups": {}, "trials": results}
    keys = sorted({(r["path"], r["change_type"]) for r in results})
    for path, ct in keys:
        rows = [r for r in results if r["path"] == path and r["change_type"] == ct]
        ok = [r for r in rows if r["ok"]]
        te = [r["t_enforce_ms"] for r in ok]
        ti = [r["t_index_ms"] for r in ok if r["t_index_ms"] is not None]
        stage_names = sorted({s for r in ok for s in r["stages"]})
        out["groups"][f"{path}/{ct}"] = {
            "trials": len(rows), "failed": len(rows) - len(ok),
            "t_enforce_p50_ms": percentile(te, 0.5), "t_enforce_p95_ms": percentile(te, 0.95),
            "t_index_p50_ms": percentile(ti, 0.5), "t_index_p95_ms": percentile(ti, 0.95),
            "stage_p50_ms": {s: statistics.median(r["stages"].get(s, 0.0) for r in ok)
                             for s in stage_names} if ok else {},
        }
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--trials", type=int, default=50, help="trials per change type per path")
    ap.add_argument("--path", action="append", choices=["upload", "file"])
    ap.add_argument("--embedder", default="hashing", choices=["hashing", "sentence_transformers"])
    ap.add_argument("--debounce-ms", type=int, default=400)
    ap.add_argument("--polling", action="store_true", help="use the polling file observer")
    ap.add_argument("--out", type=Path, default=None, help="write JSON results here")
    args = ap.parse_args()
    paths = args.path or ["upload", "file"]

    with tempfile.TemporaryDirectory() as tmp:
        s = Settings(
            db_path=f"{tmp}/c3.db", chroma_dir=f"{tmp}/chroma", policy_dir=f"{tmp}/policies",
            watch_dir=f"{tmp}/watch", model_dir=f"{tmp}/models", enable_watcher="file" in paths,
        )
        c = build_container(s, keys={})
        c.cfg.retrieval.embedder = args.embedder
        if c.watcher is not None:
            c.watcher.debounce_s = args.debounce_ms / 1000.0
            if args.polling:
                from c3.indexing.watcher import MarkdownWatcher

                c.watcher = MarkdownWatcher(
                    c.policies, s.watch_dir, debounce_ms=args.debounce_ms, use_polling=True
                )
        c.start()
        try:
            report = LatencyBench(c).run(args.trials, paths)
        finally:
            c.stop()
    text = json.dumps({k: v for k, v in report.items() if k != "trials"}, indent=2)
    print(text)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
