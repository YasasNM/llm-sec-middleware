"""MarkdownWatcher: debounced file events for the research path (blueprint 19.4).

Layout: <watch_dir>/<org_id>/<file>.md . Several quick saves in a row become one index run.
Rename events carry a source and a destination path, and both are handled.
"""
from __future__ import annotations

import logging
import threading
import time
from pathlib import Path

from watchdog.events import FileSystemEvent, FileSystemEventHandler
from watchdog.observers import Observer
from watchdog.observers.polling import PollingObserver

from c3.policy.models import PolicyLintError
from c3.policy_service import PolicyService, UploadRejected

log = logging.getLogger("c3.watcher")
MD_EXT = (".md", ".markdown")


class MarkdownWatcher(FileSystemEventHandler):
    def __init__(
        self,
        service: PolicyService,
        watch_dir: str | Path,
        *,
        debounce_ms: int = 400,
        use_polling: bool = False,
        poll_interval_s: float = 0.25,
    ):
        self.service = service
        self.root = Path(watch_dir).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.debounce_s = debounce_ms / 1000.0
        self._timers: dict[Path, threading.Timer] = {}
        self._first_event: dict[Path, float] = {}
        self._lock = threading.Lock()
        self._observer = (
            PollingObserver(timeout=poll_interval_s) if use_polling else Observer()
        )
        self._observer.schedule(self, str(self.root), recursive=True)

    # ---- lifecycle --------------------------------------------------------------------
    def start(self) -> None:
        self._observer.start()

    def stop(self) -> None:
        if self._observer.is_alive():
            self._observer.stop()
            self._observer.join(timeout=5)
        with self._lock:
            for t in self._timers.values():
                t.cancel()
            self._timers.clear()

    @property
    def alive(self) -> bool:
        return self._observer.is_alive()

    def scan_existing(self) -> None:
        """Index files that were already in the folder when the service started."""
        for p in sorted(self.root.rglob("*")):
            if p.is_file() and p.suffix.lower() in MD_EXT:
                self._schedule(p)

    # ---- events -----------------------------------------------------------------------
    def on_created(self, event: FileSystemEvent) -> None:
        self._handle(event.src_path, event.is_directory)

    def on_modified(self, event: FileSystemEvent) -> None:
        self._handle(event.src_path, event.is_directory)

    def on_deleted(self, event: FileSystemEvent) -> None:
        self._handle(event.src_path, event.is_directory)

    def on_moved(self, event: FileSystemEvent) -> None:
        # Editors save by writing a temp file and renaming it. Treat both ends.
        self._handle(event.src_path, event.is_directory)
        self._handle(getattr(event, "dest_path", ""), event.is_directory)

    def _handle(self, raw: str, is_dir: bool) -> None:
        if is_dir or not raw:
            return
        p = Path(raw).resolve()
        if p.suffix.lower() not in MD_EXT or p.name.startswith((".", "~")):
            return
        self._schedule(p)

    def _schedule(self, p: Path) -> None:
        now = time.time()
        with self._lock:
            self._first_event.setdefault(p, now)
            old = self._timers.pop(p, None)
            if old:
                old.cancel()
            t = threading.Timer(self.debounce_s, self._fire, args=(p,))
            t.daemon = True
            self._timers[p] = t
            t.start()

    # ---- after the debounce -----------------------------------------------------------
    def _org_and_name(self, p: Path) -> tuple[str, str] | None:
        try:
            rel = p.relative_to(self.root)
        except ValueError:
            return None
        if len(rel.parts) < 2:
            return None                       # files must sit inside an organisation folder
        return rel.parts[0], "/".join(rel.parts[1:])

    def _fire(self, p: Path) -> None:
        t_fire = time.time()
        with self._lock:
            self._timers.pop(p, None)
            t_first = self._first_event.pop(p, t_fire)
        info = self._org_and_name(p)
        if info is None:
            return
        org_id, name = info
        try:
            if not p.exists():
                self.service.remove_file(org_id, name)
                return
            data = p.read_bytes()
            self.service.ingest_file(org_id, name, data, t_first_event=t_first, t_fire=t_fire)
        except (UploadRejected, PolicyLintError) as exc:
            log.warning("file %s rejected: %s", name, getattr(exc, "message", exc))
        except Exception:
            log.exception("watcher failed on %s", name)
