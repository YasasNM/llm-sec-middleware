"""PolicyService: upload, replace, delete, status (FR2, FR11, FR13, FR20, NFR13).

Uploads return quickly with status `processing`. A background worker parses, lints and
indexes. One lock per organisation keeps two changes from interleaving. A bad file never
touches the live index: the previous good version stays active.
"""
from __future__ import annotations

import hashlib
import logging
import threading
import time
from collections import defaultdict
from concurrent.futures import Future, ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import PurePath

from c3.indexing.indexer import IndexRun, PolicyIndexer
from c3.indexing.registry import OrgIndexRegistry
from c3.policy.doc_code import make_doc_code
from c3.policy.models import PolicyLintError
from c3.policy.parser import PolicyParser
from c3.storage.db import Database
from c3.storage.files import PolicyFileStore

log = logging.getLogger("c3.policy")

ALLOWED_EXT = (".pdf", ".md", ".markdown")


class PolicyNotFound(Exception):
    pass


class UploadRejected(Exception):
    """Fast checks that happen before the 202: wrong type or too large."""

    def __init__(self, http_status: int, code: str, message: str):
        super().__init__(message)
        self.http_status, self.code, self.message = http_status, code, message


def now_iso() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


class PolicyService:
    def __init__(
        self,
        db: Database,
        files: PolicyFileStore,
        parser: PolicyParser,
        indexer: PolicyIndexer,
        registry: OrgIndexRegistry,
        workers: int = 2,
    ):
        self.db, self.files, self.parser = db, files, parser
        self.indexer, self.registry = indexer, registry
        self._pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="c3-index")
        self._org_locks: dict[str, threading.Lock] = defaultdict(threading.Lock)
        self._guard = threading.Lock()
        self._pending: list[Future] = []
        self._doc_seq = threading.Lock()
        self.last_run: dict[str, IndexRun] = {}

    # ---- helpers ----------------------------------------------------------------------
    def _org_lock(self, org_id: str) -> threading.Lock:
        with self._guard:
            return self._org_locks[org_id]

    def _precheck(self, filename: str, data: bytes) -> str:
        ext = PurePath(filename).suffix.lower()
        if ext not in ALLOWED_EXT:
            raise UploadRejected(
                415, "UNSUPPORTED_TYPE", "Only PDF and Markdown files are accepted."
            )
        lim = self.parser.limits
        cap = lim.max_pdf_mb * 1024 * 1024 if ext == ".pdf" else lim.max_md_kb * 1024
        if len(data) > cap:
            raise UploadRejected(413, "FILE_TOO_LARGE", "The file is larger than the allowed size.")
        if ext == ".pdf" and not data.lstrip()[:5].startswith(b"%PDF"):
            raise UploadRejected(415, "NOT_A_PDF", "The file is not a PDF.")
        if not data:
            raise UploadRejected(422, "EMPTY_FILE", "The file is empty.")
        return ext

    def _submit(self, fn, *args) -> Future:
        fut = self._pool.submit(fn, *args)
        with self._guard:
            self._pending = [f for f in self._pending if not f.done()] + [fut]
        return fut

    def wait_idle(self, timeout: float = 30.0) -> None:
        """Block until queued work is done. Used by tests and the bench."""
        with self._guard:
            pending = list(self._pending)
        for f in pending:
            f.result(timeout=timeout)

    # ---- product path: upload API -----------------------------------------------------
    def upload(
        self,
        org_id: str,
        filename: str,
        data: bytes,
        *,
        name: str | None = None,
        category: str | None = None,
        severity: str | None = None,
        uploaded_by: str | None = None,
    ) -> dict:
        t0 = time.time()                                  # request accepted
        self._precheck(filename, data)
        with self._doc_seq:
            doc_id = self.db.next_doc_id()
            doc_code = make_doc_code(filename, self.db.doc_codes(org_id))
            doc = {
                "doc_id": doc_id, "org_id": org_id, "name": name or PurePath(filename).stem,
                "filename": filename, "doc_code": doc_code,
                "category": category or self.parser.default_category, "severity": severity,
                "sha256": hashlib.sha256(data).hexdigest(), "size_bytes": len(data),
                "version": 0, "status": "processing", "status_message": None,
                "clause_count": 0, "source": "upload", "uploaded_at": now_iso(),
                "uploaded_by": uploaded_by,
            }
            self.db.insert_document(doc)
        self._submit(self._process, org_id, doc_id, filename, data, "upload", t0, t0)
        return self.db.get_document(org_id, doc_id)

    def replace(
        self,
        org_id: str,
        doc_id: str,
        filename: str,
        data: bytes,
        *,
        name: str | None = None,
        category: str | None = None,
        severity: str | None = None,
        uploaded_by: str | None = None,
    ) -> dict:
        t0 = time.time()
        doc = self.db.get_document(org_id, doc_id)
        if doc is None or doc["status"] == "removed":
            raise PolicyNotFound(doc_id)
        self._precheck(filename, data)
        fields = {"status": "processing", "uploaded_at": now_iso()}
        if name:
            fields["name"] = name
        if category:
            fields["category"] = category
        if severity:
            fields["severity"] = severity
        if uploaded_by:
            fields["uploaded_by"] = uploaded_by
        self.db.update_document(doc_id, **fields)
        self._submit(self._process, org_id, doc_id, filename, data, "upload", t0, t0)
        return self.db.get_document(org_id, doc_id)

    def delete(self, org_id: str, doc_id: str) -> None:
        doc = self.db.get_document(org_id, doc_id)
        if doc is None or doc["status"] == "removed":
            raise PolicyNotFound(doc_id)
        with self._org_lock(org_id):
            run = self.indexer.remove_document(org_id, doc_id)
            self.db.insert_run(run.to_row())
            self.files.delete(org_id, doc_id)
            self.db.update_document(doc_id, status="removed", clause_count=0, status_message=None)

    # ---- research path: file watcher --------------------------------------------------
    def ingest_file(
        self, org_id: str, filename: str, data: bytes, *, t_first_event: float, t_fire: float
    ) -> IndexRun | None:
        """Called by the watcher after the debounce. Runs in the caller's thread."""
        self._precheck(filename, data)
        existing = self.db.find_by_source(org_id, filename, "file")
        if existing is None:
            with self._doc_seq:
                doc_id = self.db.next_doc_id()
                doc_code = make_doc_code(filename, self.db.doc_codes(org_id))
                self.db.insert_document({
                    "doc_id": doc_id, "org_id": org_id, "name": PurePath(filename).stem,
                    "filename": filename, "doc_code": doc_code,
                    "category": self.parser.default_category, "severity": None,
                    "sha256": hashlib.sha256(data).hexdigest(), "size_bytes": len(data),
                    "version": 0, "status": "processing", "status_message": None,
                    "clause_count": 0, "source": "file", "uploaded_at": now_iso(),
                    "uploaded_by": "file-watch",
                })
        else:
            doc_id = existing["doc_id"]
            self.db.update_document(doc_id, status="processing")
        return self._process(
            org_id, doc_id, filename, data, "file", t_first_event, t_fire,
            detect_ms=(t_fire - t_first_event) * 1000.0,
        )

    def remove_file(self, org_id: str, filename: str) -> None:
        doc = self.db.find_by_source(org_id, filename, "file")
        if doc is not None:
            self.delete(org_id, doc["doc_id"])

    # ---- the worker -------------------------------------------------------------------
    def _process(
        self,
        org_id: str,
        doc_id: str,
        filename: str,
        data: bytes,
        path: str,
        t0: float,
        t_start_ref: float,
        detect_ms: float | None = None,
    ) -> IndexRun | None:
        with self._org_lock(org_id):
            doc = self.db.get_document(org_id, doc_id)
            if doc is None or doc["status"] == "removed":
                return None
            queue_ms = detect_ms if detect_ms is not None else (time.time() - t_start_ref) * 1000.0
            ext = PurePath(filename).suffix.lower()
            t = time.perf_counter()
            try:
                parsed = self.parser.parse(
                    filename, data, doc_code=doc["doc_code"], category=doc["category"],
                    severity=doc["severity"],
                )
                parse_ms = (time.perf_counter() - t) * 1000.0
                new_version = doc["version"] + 1
                run = self.indexer.index_document(
                    org_id, doc_id, new_version, parsed.clauses,
                    path=path, t0=t0, queue_or_detect_ms=queue_ms, parse_ms=parse_ms,
                )
            except PolicyLintError as exc:
                return self._reject(org_id, doc_id, doc, ext, data, exc, path, t0, queue_ms)
            except Exception as exc:  # never leave a document stuck in `processing`
                log.exception("indexing failed for %s", doc_id)
                err = PolicyLintError(
                    "INDEX_FAILED", "Indexing failed. The previous version stays live."
                )
                err.__cause__ = exc
                return self._reject(org_id, doc_id, doc, ext, data, err, path, t0, queue_ms)

            self.files.save(org_id, doc_id, new_version, ext, data)
            self.db.update_document(
                doc_id, version=new_version, status="active",
                status_message=" ".join(parsed.warnings) or None,
                clause_count=len(parsed.clauses), filename=filename,
                sha256=hashlib.sha256(data).hexdigest(), size_bytes=len(data),
            )
            self.db.insert_run(run.to_row())
            self.last_run[org_id] = run
            log.info(
                "indexed doc=%s org=%s v=%d diff=%s embedded=%d index=%s",
                doc_id, org_id, new_version, run.diff.summary(), run.embedded, run.index_version,
            )
            return run

    def _reject(self, org_id, doc_id, doc, ext, data, exc: PolicyLintError, path, t0, queue_ms):
        self.files.save_rejected(org_id, doc_id, ext, data)
        self.db.update_document(doc_id, status="needs_attention", status_message=exc.message)
        run = IndexRun(
            run_id="run-rejected-" + doc_id, org_id=org_id, doc_id=doc_id, path=path, t0=t0,
            queue_or_detect_ms=queue_ms, ok=False, message=f"{exc.code}: {exc.message}",
            t_done=time.time(),
        )
        run.index_version = self.registry.get(org_id).version
        self.db.insert_run(run.to_row() | {"run_id": f"run-{doc_id}-{int(t0 * 1000)}"})
        log.warning("rejected doc=%s org=%s code=%s", doc_id, org_id, exc.code)
        return run

    # ---- restart ----------------------------------------------------------------------
    def rebuild_org(self, org_id: str) -> int:
        """Rebuild an organisation's index from the stored policy files (blueprint 11.3).

        Used at start-up when the in-memory index is empty, and after a retriever change.
        """
        count = 0
        with self._org_lock(org_id):
            for doc in self.db.list_documents(org_id):
                if doc["version"] < 1:
                    continue
                path = self.files.latest(org_id, doc["doc_id"])
                if path is None:
                    continue
                try:
                    parsed = self.parser.parse(
                        path.name, path.read_bytes(), doc_code=doc["doc_code"],
                        category=doc["category"], severity=doc["severity"],
                    )
                    self.indexer.index_document(
                        org_id, doc["doc_id"], doc["version"], parsed.clauses,
                        path="upload", t0=time.time(),
                    )
                    count += 1
                except Exception:
                    log.exception("rebuild failed for %s", doc["doc_id"])
        return count

    def known_orgs(self) -> list[str]:
        return self.db.orgs()

    # ---- reads ------------------------------------------------------------------------
    def list_documents(self, org_id: str) -> list[dict]:
        return self.db.list_documents(org_id)

    def get(self, org_id: str, doc_id: str) -> dict:
        doc = self.db.get_document(org_id, doc_id)
        if doc is None or doc["status"] == "removed":
            raise PolicyNotFound(doc_id)
        return doc

    def clauses(self, org_id: str, doc_id: str) -> list[dict]:
        self.get(org_id, doc_id)
        snap = self.registry.get(org_id).snapshot()
        out = []
        for e in sorted(snap.entries.values(), key=lambda e: e.clause.order):
            if e.doc_id == doc_id:
                c = e.clause
                out.append({
                    "clause_id": c.clause_id, "number": c.number, "title": c.title,
                    "category": c.category, "severity": c.severity, "page": c.page,
                    "content_hash": c.content_hash, "text": c.text,
                })
        return out

    def status(self, org_id: str) -> dict:
        docs = self.db.list_documents(org_id)
        index = self.registry.get(org_id)
        snap = index.snapshot()
        busy = any(d["status"] == "processing" for d in docs)
        attention = any(d["status"] == "needs_attention" for d in docs)
        if busy:
            state = "updating"
        elif not snap.entries:
            state = "empty"
        elif attention:
            state = "attention"
        else:
            state = "ready"
        last = max((d["uploaded_at"] for d in docs), default=None)
        return {
            "org_id": org_id, "state": state, "documents": len(docs),
            "clauses": len(snap.entries), "index_version": snap.version,
            "retriever_version": snap.retriever_version, "last_updated": last,
        }
