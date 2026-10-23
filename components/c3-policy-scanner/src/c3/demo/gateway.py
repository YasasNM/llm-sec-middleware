"""Mock gateway + demo web UI (blueprint 19.5). NOT the real gateway (`apps/gateway`).

It plays the pipeline around C3 so C3 can be shown on its own:
  privacy step (regex stand-in for C1) -> intent step (always allows, stand-in for C2) ->
  LLM (scripted, typed by the presenter, or a real Ollama server) -> POST /v1/scan on C3 ->
  clinician message (tiered disclosure: no clause, no reason) or admin detail.
It also proxies the admin calls to C3's policy API and keeps a feedback log, so real
judgements from a demo can later become labelled data.

    python -m c3.demo.launcher        # starts C3, the C4 stub and this gateway, opens the UI
"""
from __future__ import annotations

import json
import os
import re
import threading
import time
from datetime import UTC, datetime
from pathlib import Path

import httpx
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from c3.config import COMPONENT_ROOT
from c3.demo.scenarios import DEMO_ORG, RIVERSIDE, SCENARIOS
from c3.leakcheck.checker import EMAIL, NIC_NEW, NIC_OLD, PHONE

UI = Path(__file__).parent / "ui"
WITHHELD_TEXT = ("An answer could not be shown because it may not meet hospital policy or a safety "
                 "check was unavailable.")
FEEDBACK = COMPONENT_ROOT / "var" / "feedback.jsonl"


def now_z() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def privacy_step(text: str) -> tuple[str, list[str]]:
    """Stand-in for C1: replace identifiers with numbered placeholders (one-way, K1)."""
    tokens: list[str] = []
    counts: dict[str, int] = {}

    def swap(kind: str):
        def inner(m: re.Match) -> str:
            counts[kind] = counts.get(kind, 0) + 1
            tok = f"[{kind}_{counts[kind]}]"
            tokens.append(tok)
            return tok
        return inner

    for kind, rx in (("NIC", NIC_OLD), ("NIC", NIC_NEW), ("PHONE", PHONE), ("EMAIL", EMAIL)):
        text = rx.sub(swap(kind), text)
    return text, tokens


class ChatIn(BaseModel):
    user_id: str = "dr.silva"
    role: str = "clinician"             # clinician | admin  (admin also receives the policy detail)
    prompt: str
    llm_answer: str | None = None       # the presenter types what the "LLM" said
    use_llm: bool = False               # ask the real LLM (Ollama) instead
    categories: list[str] | None = None


class FeedbackIn(BaseModel):
    request_id: str
    c3_verdict: str
    answer_text: str
    judged_violation: bool              # what the person says is right
    note: str = ""


class MarkdownIn(BaseModel):
    markdown: str
    filename: str = "policy.md"


def create_app(
    c3_url: str | None = None,
    c4_url: str | None = None,
    gateway_key: str | None = None,
    ollama_url: str | None = None,
    ollama_model: str | None = None,
    http=None,
    feedback_path: Path | None = None,
) -> FastAPI:
    c3_url = (c3_url or os.getenv("C3_URL", "http://127.0.0.1:8003")).rstrip("/")
    c4_url = (c4_url or os.getenv("C4_URL", "http://127.0.0.1:8004")).rstrip("/")
    key = gateway_key or os.getenv("GW_KEY", "demo-gateway-key")
    ollama_url = ollama_url or os.getenv("OLLAMA_URL", "")
    ollama_model = ollama_model or os.getenv("OLLAMA_MODEL", "qwen2.5:7b")
    http = http or httpx.Client(timeout=120)
    fb_path = feedback_path or FEEDBACK
    H = {"X-Service-Key": key}
    app = FastAPI(title="MediGuard mock gateway (demo)")
    counter = {"n": 0}
    lock = threading.Lock()
    last: dict[str, dict] = {}

    def c3(method: str, path: str, **kw) -> httpx.Response:
        try:
            return http.request(method, f"{c3_url}{path}", headers=H, **kw)
        except httpx.HTTPError as exc:
            raise HTTPException(502, f"Policy service not reachable: {type(exc).__name__}") from exc

    def check(r: httpx.Response) -> httpx.Response:
        if r.status_code >= 400:
            try:
                detail = r.json().get("detail", r.text)
            except Exception:
                detail = r.text
            raise HTTPException(r.status_code, detail)
        return r

    # ---- status and presets ---------------------------------------------------------------
    @app.get("/api/status")
    def status():
        out = {"org_id": DEMO_ORG, "ollama": bool(ollama_url)}
        for name, url in (("c3", c3_url), ("c4", c4_url)):
            try:
                out[name] = http.get(f"{url}/health", timeout=5).json()
            except Exception:
                out[name] = None
        return out

    @app.get("/api/scenarios")
    def scenarios():
        return SCENARIOS

    # ---- the chat pipeline ----------------------------------------------------------------
    @app.post("/api/chat")
    def chat(body: ChatIn):
        t0 = time.perf_counter()
        with lock:
            counter["n"] += 1
            n = counter["n"]
        request_id = f"req_{time.strftime('%Y%m%d')}_{n:06d}"
        stages = []

        t = time.perf_counter()
        masked, tokens = privacy_step(body.prompt)
        stages.append({"stage": "Privacy check", "ms": round((time.perf_counter() - t) * 1000, 1),
                       "note": f"{len(tokens)} identifier(s) masked"})
        stages.append({"stage": "Intent check", "ms": 0.0, "note": "allowed (demo stand-in)"})

        t = time.perf_counter()
        if body.llm_answer is not None and body.llm_answer.strip():
            answer, source = body.llm_answer, "typed by presenter"
        elif body.use_llm and ollama_url:
            try:
                r = http.post(f"{ollama_url.rstrip('/')}/api/chat", json={
                    "model": ollama_model, "stream": False,
                    "messages": [{"role": "user", "content": masked}]})
                r.raise_for_status()
                answer, source = r.json()["message"]["content"], f"Ollama {ollama_model}"
            except Exception as exc:
                raise HTTPException(502, f"LLM not reachable: {type(exc).__name__}") from exc
        else:
            raise HTTPException(422, "Type the assistant's answer, or turn on the real LLM.")
        stages.append({"stage": "Assistant answer", "ms": round((time.perf_counter() - t) * 1000, 1),
                       "note": source})

        t = time.perf_counter()
        scan_body = {"request_id": request_id, "org_id": DEMO_ORG, "user_id": body.user_id,
                     "session_id": "sess_demo", "response_text": answer,
                     "placeholder_manifest": tokens}
        if body.categories:
            scan_body["categories"] = body.categories
        r = c3("POST", "/v1/scan", json=scan_body)
        if r.status_code in (404, 422, 413):
            raise HTTPException(r.status_code, r.json().get("detail", "scan rejected"))
        verdict = r.json()
        stages.append({"stage": "Policy check", "ms": round((time.perf_counter() - t) * 1000, 1),
                       "note": verdict["c3_verdict"].lower()})

        withheld = verdict["c3_verdict"] == "VIOLATION"
        message = {
            "id": f"msg_{n}", "role": "assistant", "timestamp": now_z(), "requestId": request_id,
            "content": "" if withheld else (verdict.get("clean_response") or ""),
            "type": "blocked" if withheld else ("pii-masked" if tokens else "normal"),
            "user_message_code": "OUTPUT_WITHHELD" if withheld else None,
        }
        out = {"message": message, "withheld_text": WITHHELD_TEXT if withheld else None,
               "masking": {"masked_prompt": masked, "tokens": tokens}, "stages": stages,
               "total_ms": round((time.perf_counter() - t0) * 1000, 1)}
        if not withheld or body.role == "admin":
            out["answer"] = answer                    # a withheld answer never reaches a clinician
        if body.role == "admin":                       # tiered disclosure: clinicians never get this
            out["admin"] = verdict | {"http_status": r.status_code}
        last[request_id] = {"answer": answer, "verdict": verdict}
        return out

    # ---- admin: policy documents (proxy to C3, role check is a demo stand-in) ---------------
    base = f"/v1/orgs/{DEMO_ORG}/policies"

    @app.get("/api/admin/policies")
    def policies():
        docs = check(c3("GET", base)).json()
        status = check(c3("GET", f"{base}/status")).json()
        return {"documents": docs, "status": status}

    @app.post("/api/admin/policies")
    async def upload(file: UploadFile = File(...), category: str = Form(default="general")):
        data = await file.read()
        r = check(c3("POST", base, files={"file": (file.filename, data)},
                     data={"category": category, "uploaded_by": "demo-admin"}))
        return r.json()

    @app.put("/api/admin/policies/{doc_id}")
    async def replace(doc_id: str, file: UploadFile = File(...)):
        data = await file.read()
        return check(c3("PUT", f"{base}/{doc_id}", files={"file": (file.filename, data)},
                        data={"uploaded_by": "demo-admin"})).json()

    @app.put("/api/admin/policies/{doc_id}/markdown")
    def replace_markdown(doc_id: str, body: MarkdownIn):
        return check(c3("PUT", f"{base}/{doc_id}",
                        files={"file": (body.filename, body.markdown.encode("utf-8"))},
                        data={"uploaded_by": "demo-admin"})).json()

    @app.delete("/api/admin/policies/{doc_id}")
    def delete(doc_id: str):
        check(c3("DELETE", f"{base}/{doc_id}"))
        return {"deleted": doc_id}

    @app.get("/api/admin/policies/{doc_id}")
    def doc(doc_id: str):
        return check(c3("GET", f"{base}/{doc_id}")).json()

    @app.get("/api/admin/policies/{doc_id}/clauses")
    def clauses(doc_id: str):
        return check(c3("GET", f"{base}/{doc_id}/clauses")).json()

    @app.get("/api/admin/policies/{doc_id}/markdown")
    def as_markdown(doc_id: str):
        """The clauses written back as Markdown with explicit ids, ready to edit and re-upload."""
        d = check(c3("GET", f"{base}/{doc_id}")).json()
        cl = check(c3("GET", f"{base}/{doc_id}/clauses")).json()
        lines = [f"# {d['name']}", ""]
        for c in cl:
            lines += [f"## {c['clause_id']} {c['title']}", "", c["text"], ""]
        return {"markdown": "\n".join(lines), "filename": f"{d['doc_code'].lower()}.md",
                "doc_code": d["doc_code"]}

    @app.post("/api/admin/load-demo")
    def load_demo(which: str = "riverside"):
        """Upload a bundled policy: the small Riverside policy, or a real public document."""
        raw = COMPONENT_ROOT / "data" / "raw"
        pub = COMPONENT_ROOT / "data" / "policies" / "public"
        sources = {
            "riverside": (RIVERSIDE, "riverside-ai-policy.md", "ai_usage"),
            "pdpa": (raw / "lk_pdpa.pdf", "sri-lanka-pdpa-2022.pdf", "data_handling"),
            "hipaa": (pub / "hipaa_privacy.md", "hipaa-privacy-rule.md", "disclosure"),
        }
        if which not in sources:
            raise HTTPException(404, "Unknown demo policy")
        path, name, cat = sources[which]
        if not path.exists():
            raise HTTPException(404, f"{path.name} is not available. Run: python -m c3.training.public.fetch")
        return check(c3("POST", base, files={"file": (name, path.read_bytes())},
                        data={"category": cat, "uploaded_by": "demo-admin"})).json()

    @app.get("/api/admin/trace/{request_id}")
    def trace(request_id: str):
        return check(c3("GET", f"/v1/orgs/{DEMO_ORG}/scans/{request_id}")).json()

    # ---- C4 view and feedback ---------------------------------------------------------------
    @app.get("/api/events")
    def events():
        try:
            return {"events": http.get(f"{c4_url}/_demo/events", timeout=5).json(),
                    "users": http.get(f"{c4_url}/_demo/users", timeout=5).json()}
        except Exception:
            return {"events": [], "users": []}

    @app.post("/api/events/reset")
    def reset_events():
        try:
            http.post(f"{c4_url}/_demo/reset", timeout=5)
        except Exception:
            pass
        return {"reset": True}

    @app.post("/api/feedback")
    def feedback(body: FeedbackIn):
        fb_path.parent.mkdir(parents=True, exist_ok=True)
        rec = body.model_dump() | {"time": now_z(), "org_id": DEMO_ORG, "source": "demo-feedback"}
        with fb_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
        return {"saved": True}

    @app.get("/api/feedback/summary")
    def feedback_summary():
        rows = []
        if fb_path.exists():
            rows = [json.loads(x) for x in fb_path.read_text(encoding="utf-8").splitlines() if x]
        agree = sum(1 for r in rows if r["judged_violation"] == (r["c3_verdict"] == "VIOLATION"))
        return {"total": len(rows), "agree": agree, "disagree": len(rows) - agree}

    # ---- static UI -----------------------------------------------------------------------------
    @app.get("/")
    def index():
        return FileResponse(UI / "index.html")

    app.mount("/static", StaticFiles(directory=UI), name="static")

    @app.exception_handler(Exception)
    async def unhandled(_, exc):                      # never leak internals into the UI
        return JSONResponse({"detail": f"{type(exc).__name__}"}, status_code=500)

    return app


def get_app() -> FastAPI:
    return create_app()
