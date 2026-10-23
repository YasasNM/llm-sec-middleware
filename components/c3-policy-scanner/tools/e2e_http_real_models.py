"""End-to-end through the real HTTP app with the real models and ChromaDB.

Starts the FastAPI app in-process with configs/with-models.yaml (hash check on the model
files, transformer embedder, fine-tuned generator on CPU, ChromaDB), uploads a policy PDF
through the API, scans three answers, and restarts the app to prove the index survives.

    python tools/e2e_http_real_models.py
"""
from __future__ import annotations

import os
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))


def main() -> None:
    from c3_helpers import SCAN_POLICY, make_pdf  # PDF generator from the tests
    from fastapi.testclient import TestClient

    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        os.environ.update({
            "C3_CONFIG_FILE": str(ROOT / "configs" / "with-models.yaml"),
            "C3_DB_PATH": f"{tmp}/c3.db", "C3_CHROMA_DIR": f"{tmp}/chroma",
            "C3_POLICY_DIR": f"{tmp}/policies", "C3_WATCH_DIR": f"{tmp}/watch",
            "C3_MODEL_DIR": str(ROOT / "models"), "C3_ENABLE_WATCHER": "false",
            "MG_ACCEPTED_KEYS": "gateway:e2e-key", "MG_SERVICE_KEY": "c3-key",
            "MG_C4_URL": "http://127.0.0.1:9",                   # nothing listens: outbox path
        })
        from c3.main import create_app

        h = {"X-Service-Key": "e2e-key"}
        t = time.time()
        app = create_app()
        print(f"start-up (hash check + models + chroma): {time.time() - t:.1f} s")
        with TestClient(app) as tc:
            print("health:", tc.get("/health").json())
            pdf = make_pdf([(n, ti, b) for n, ti, b in SCAN_POLICY])
            r = tc.post("/v1/orgs/riverside/policies", headers=h,
                        files={"file": ("riverside-ai-policy.pdf", pdf)})
            print("upload:", r.status_code, r.json()["status"])
            app.state.container.policies.wait_idle()
            print("status:", tc.get("/v1/orgs/riverside/policies/status", headers=h).json())
            for text in (
                "I would double the dose of your morphine tonight if the pain is bad.",
                "Hypertension is usually managed with regular monitoring and exercise.",
                "Tell his employer about the cancer result so they can arrange cover.",
            ):
                body = {"request_id": f"e2e_{abs(hash(text)) % 10**6}", "org_id": "riverside",
                        "user_id": "u1", "session_id": "s1", "response_text": text,
                        "placeholder_manifest": []}
                t = time.time()
                v = tc.post("/v1/scan", headers=h, json=body).json()
                print(f"{v['c3_verdict']:9s} p={v['confidence']} clause={v['clause_id']} "
                      f"{(time.time() - t) * 1000:.0f} ms  <- {text[:50]}")
            print("events:", app.state.container.db.event_counts())
        # restart: ChromaDB must bring the index back without re-embedding
        app2 = create_app()
        with TestClient(app2) as tc2:
            print("after restart:", tc2.get("/v1/orgs/riverside/policies/status", headers=h).json())


if __name__ == "__main__":
    main()
