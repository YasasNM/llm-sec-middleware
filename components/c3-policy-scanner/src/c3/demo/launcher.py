"""Start the whole demo with one command.

    python -m c3.demo.launcher                 # needs the trained models in models/
    python -m c3.demo.launcher --ollama http://localhost:11434 --ollama-model qwen2.5:7b

Starts three local services: C3 policy check (8003), a C4 stub (8004) and the mock gateway with
the web UI (8000). Press Ctrl+C to stop them all.
"""
from __future__ import annotations

import argparse
import os
import shutil
import socket
import subprocess
import sys
import time
import webbrowser
from pathlib import Path

import httpx

from c3.config import COMPONENT_ROOT

STATE = COMPONENT_ROOT / "var" / "demo"


def port_free(port: int) -> bool:
    with socket.socket() as s:
        s.settimeout(0.3)
        return s.connect_ex(("127.0.0.1", port)) != 0


def wait_for(url: str, name: str, timeout: float = 180) -> None:
    end = time.time() + timeout
    while time.time() < end:
        try:
            if httpx.get(url, timeout=2).status_code == 200:
                return
        except Exception:
            pass
        time.sleep(0.5)
    raise SystemExit(f"{name} did not start in {timeout:.0f}s. See {STATE / 'logs'}.")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--keep", action="store_true", help="keep policies and history from the last run")
    ap.add_argument("--no-load", action="store_true", help="do not load the Riverside policy")
    ap.add_argument("--no-browser", action="store_true")
    ap.add_argument("--ollama", default="", help="URL of an Ollama server for the real LLM option")
    ap.add_argument("--ollama-model", default="qwen2.5:7b")
    ap.add_argument("--gateway-port", type=int, default=8000)
    ap.add_argument("--models", default="", help="folder with generator/, retriever/, manifest.json")
    a = ap.parse_args()

    from c3.demo.c3_app import MISSING, models_available

    model_dir = Path(a.models or os.getenv("C3_MODEL_DIR", COMPONENT_ROOT / "models"))
    if not models_available(model_dir):
        raise SystemExit(MISSING.format(path=model_dir))
    for port in (8003, 8004, a.gateway_port):
        if not port_free(port):
            raise SystemExit(f"Port {port} is already in use. Stop the other program first.")
    if not a.keep and STATE.exists():
        shutil.rmtree(STATE, ignore_errors=True)
    (STATE / "logs").mkdir(parents=True, exist_ok=True)

    base = {**os.environ, "PYTHONIOENCODING": "utf-8", "HF_HUB_DISABLE_SYMLINKS_WARNING": "1"}
    env_c3 = {**base,
              "C3_DB_PATH": str(STATE / "c3.db"), "C3_POLICY_DIR": str(STATE / "policies"),
              "C3_CHROMA_DIR": str(STATE / "chroma"), "C3_WATCH_DIR": str(STATE / "watch"),
              "C3_ENABLE_WATCHER": "false", "MG_ACCEPTED_KEYS": "gateway:demo-gateway-key",
              "MG_SERVICE_KEY": "demo-c3-key", "MG_C4_URL": "http://127.0.0.1:8004",
              "C3_MODEL_DIR": str(model_dir)}
    env_c4 = {**base, "C4_ACCEPTED_KEYS": "c3:demo-c3-key"}
    env_gw = {**base, "C3_URL": "http://127.0.0.1:8003", "C4_URL": "http://127.0.0.1:8004",
              "GW_KEY": "demo-gateway-key",
              "OLLAMA_URL": a.ollama, "OLLAMA_MODEL": a.ollama_model}

    procs: list[subprocess.Popen] = []

    def start(name: str, module: str, port: int, env: dict, factory: bool = False) -> None:
        log = open(STATE / "logs" / f"{name}.log", "w", encoding="utf-8")        # noqa: SIM115
        cmd = [sys.executable, "-m", "uvicorn", module, "--host", "127.0.0.1", "--port", str(port),
               "--workers", "1", "--log-level", "warning"]
        if factory:
            cmd.append("--factory")
        procs.append(subprocess.Popen(cmd, env=env, stdout=log, stderr=subprocess.STDOUT,
                                      cwd=str(COMPONENT_ROOT)))

    try:
        start("c4", "c3.demo.c4_stub:app", 8004, env_c4)
        wait_for("http://127.0.0.1:8004/health", "C4 stub")
        print("C4 stub ready", flush=True)
        start("c3", "c3.demo.c3_app:app", 8003, env_c3)
        wait_for("http://127.0.0.1:8003/health", "C3 policy check")
        health = httpx.get("http://127.0.0.1:8003/health", timeout=5).json()
        print(f"C3 ready (model: {health.get('model_version')}, retriever: {health.get('embedder')})",
              flush=True)
        start("gateway", "c3.demo.gateway:get_app", a.gateway_port, env_gw, factory=True)
        wait_for(f"http://127.0.0.1:{a.gateway_port}/api/status", "Demo gateway")
        url = f"http://127.0.0.1:{a.gateway_port}"
        if not a.no_load:
            r = httpx.post(f"{url}/api/admin/load-demo?which=riverside", timeout=120)
            print("Riverside policy loaded" if r.status_code < 300 else f"Policy not loaded: {r.text}",
                  flush=True)
        print(f"\nDemo ready: {url}\nPress Ctrl+C to stop.\n", flush=True)
        if not a.no_browser:
            webbrowser.open(url)
        while all(p.poll() is None for p in procs):
            time.sleep(1)
        print("A service stopped. See", STATE / "logs")
    except KeyboardInterrupt:
        pass
    finally:
        for p in procs:
            p.terminate()
        for p in procs:
            try:
                p.wait(timeout=8)
            except subprocess.TimeoutExpired:
                p.kill()


if __name__ == "__main__":
    main()
