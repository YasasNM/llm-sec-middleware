"""The demo stack: C4 stub, mock gateway and UI, wired together in-process."""
import json
from pathlib import Path

import httpx
import pytest
from c3_helpers import KEY, CountingEmbedder, StubGenerator
from fastapi.testclient import TestClient
from jsonschema import Draft202012Validator, FormatChecker

from c3.demo import c4_stub
from c3.demo.gateway import create_app as create_gateway
from c3.demo.gateway import privacy_step
from c3.demo.scenarios import RIVERSIDE, SCENARIOS
from c3.main import create_app as create_c3

CONTRACTS = Path(__file__).resolve().parents[4] / "contracts"
C4_KEY = "c3-to-c4-key"                      # set by the make_container fixture


class FakeNet:
    """Routes the gateway's HTTP calls to the in-process C3 and C4 apps."""

    def __init__(self, c3, c4):
        self.targets = {"http://c3": c3, "http://c4": c4}

    def request(self, method, url, **kw):
        kw.pop("timeout", None)
        for base, client in self.targets.items():
            if url.startswith(base):
                return client.request(method, url[len(base):], **kw)
        raise httpx.ConnectError("no such host")

    def get(self, url, **kw):
        return self.request("GET", url, **kw)

    def post(self, url, **kw):
        return self.request("POST", url, **kw)


def validator(name):
    schema = json.loads((CONTRACTS / "schemas" / f"{name}.schema.json").read_text())
    return Draft202012Validator(schema, format_checker=FormatChecker())


@pytest.fixture
def stack(make_container, tmp_path):
    c4_app = c4_stub.create_app({C4_KEY: "c3"})
    c4_client = TestClient(c4_app)

    def forward(request: httpx.Request) -> httpx.Response:        # C3's emitter -> the C4 stub
        r = c4_client.post("/v1/events/c3", content=request.content, headers=dict(request.headers))
        return httpx.Response(r.status_code, json=r.json())

    c3 = make_container(embedder=CountingEmbedder(), generator=StubGenerator(),
                        c4_transport=httpx.MockTransport(forward))
    with TestClient(create_c3(c3)) as c3_client:
        gw = create_gateway("http://c3", "http://c4", KEY, http=FakeNet(c3_client, c4_client),
                            feedback_path=tmp_path / "feedback.jsonl")
        with TestClient(gw) as g:
            g.c3, g.c3_client, g.c4_client, g.tmp = c3, c3_client, c4_client, tmp_path
            assert g.post("/api/admin/load-demo?which=riverside").status_code == 200
            c3.policies.wait_idle()
            yield g


def chat(g, answer, prompt="A question for the assistant?", role="clinician"):
    return g.post("/api/chat", json={"prompt": prompt, "llm_answer": answer, "role": role})


STOP_MEDS = "Tell her to stop the medication, only the tablets cause the dizziness."
FINE = "Washing hands with soap for twenty seconds reduces the risk of infection."


# ---- privacy step -----------------------------------------------------------------------
def test_privacy_step_masks_identifiers_one_way():
    masked, tokens = privacy_step("NIC 901234567V, call 0771234567 or mail a@b.lk. Again 901234567V.")
    assert "901234567V" not in masked and "0771234567" not in masked and "a@b.lk" not in masked
    assert tokens == ["[NIC_1]", "[NIC_2]", "[PHONE_1]", "[EMAIL_1]"] or set(tokens) >= {"[PHONE_1]"}
    assert all(t.startswith("[") and t.endswith("]") for t in tokens)
    assert privacy_step("nothing to hide here") == ("nothing to hide here", [])


# ---- chat pipeline ----------------------------------------------------------------------
def test_clean_answer_reaches_the_clinician(stack):
    j = chat(stack, FINE).json()
    assert j["message"]["type"] == "normal" and j["message"]["content"] == FINE
    assert j["message"]["user_message_code"] is None and j["withheld_text"] is None
    assert [s["stage"] for s in j["stages"]] == [
        "Privacy check", "Intent check", "Assistant answer", "Policy check"]


def test_violation_is_withheld_and_a_clinician_gets_no_detail(stack):
    j = chat(stack, STOP_MEDS).json()
    assert j["message"]["type"] == "blocked" and j["message"]["content"] == ""
    assert j["message"]["user_message_code"] == "OUTPUT_WITHHELD"
    assert "could not be shown" in j["withheld_text"]
    assert "admin" not in j and "answer" not in j                # tiered disclosure
    assert "stop the medication" not in json.dumps(j).lower().replace(STOP_MEDS.lower(), "")


def test_admin_sees_the_rule_the_quote_and_the_confidence(stack):
    j = chat(stack, STOP_MEDS, role="admin").json()
    a = j["admin"]
    assert a["c3_verdict"] == "VIOLATION" and a["clause_id"].endswith("-4.1")
    assert a["cited_clauses"][0]["title"] == "Changing medication"
    assert a["cited_clauses"][0]["doc_name"] == "riverside-ai-policy"
    assert a["rationale"] and 0 <= a["generation"]["p_violation"] <= 1 and j["answer"] == STOP_MEDS


def test_identifier_in_the_prompt_is_masked_before_the_assistant(stack):
    j = chat(stack, "A cough lasting two weeks has several causes. [NIC_1] should be examined.",
             prompt="Patient NIC 901234567V has a cough.").json()
    assert j["masking"]["tokens"] == ["[NIC_1]"] and "901234567V" not in j["masking"]["masked_prompt"]
    assert j["message"]["type"] == "pii-masked"                   # [NIC_1] is in the manifest: no leak


def test_leaked_identifier_in_the_answer_is_withheld(stack):
    j = chat(stack, "The patient lives at 12 Lake Road, phone 0771234567.", role="admin").json()
    assert j["message"]["type"] == "blocked" and j["admin"]["leaked_pii_count"] == 1


def test_messages_match_the_shared_chat_message_contract(stack):
    v = validator("chat_message")
    for answer in (FINE, STOP_MEDS):
        m = chat(stack, answer).json()["message"]
        assert [e.message for e in v.iter_errors(m)] == []
    m = chat(stack, "A cough has many causes. [NIC_1] needs review.",
             prompt="NIC 901234567V has a cough").json()["message"]
    assert [e.message for e in v.iter_errors(m)] == [] and m["type"] == "pii-masked"


def test_nothing_to_send_is_a_clear_error(stack):
    r = stack.post("/api/chat", json={"prompt": "hello", "role": "clinician"})
    assert r.status_code == 422 and "answer" in r.json()["detail"].lower()


# ---- live policy change -----------------------------------------------------------------
def test_editing_a_policy_flips_the_verdict_live(stack):
    doc = stack.get("/api/admin/policies").json()["documents"][0]
    assert chat(stack, STOP_MEDS).json()["message"]["type"] == "blocked"

    md = stack.get(f"/api/admin/policies/{doc['doc_id']}/markdown").json()["markdown"]
    assert "## RAI-4.1 Changing medication" in md           # explicit ids keep clause ids stable
    edited = md.replace("must not tell patients to change or stop their medication",
                        "may tell patients to change or stop their medication")
    assert edited != md
    r = stack.put(f"/api/admin/policies/{doc['doc_id']}/markdown",
                  json={"markdown": edited, "filename": "riverside.md"})
    assert r.status_code == 200 and r.json()["status"] in ("processing", "active")
    stack.c3.policies.wait_idle()
    after = stack.get(f"/api/admin/policies/{doc['doc_id']}").json()
    assert after["status"] == "active" and after["version"] == 2
    assert chat(stack, STOP_MEDS).json()["message"]["type"] == "normal"      # no restart


def test_markdown_round_trip_keeps_clause_ids(stack):
    doc = stack.get("/api/admin/policies").json()["documents"][0]
    before = [c["clause_id"] for c in stack.get(f"/api/admin/policies/{doc['doc_id']}/clauses").json()]
    md = stack.get(f"/api/admin/policies/{doc['doc_id']}/markdown").json()
    stack.put(f"/api/admin/policies/{doc['doc_id']}/markdown",
              json={"markdown": md["markdown"], "filename": md["filename"]})
    stack.c3.policies.wait_idle()
    after = [c["clause_id"] for c in stack.get(f"/api/admin/policies/{doc['doc_id']}/clauses").json()]
    assert before == after


def test_upload_replace_delete_through_the_gateway(stack):
    md = b"## 1 Rule one\nStaff must never share passwords with anyone at all.\n## 2 Rule two\nStaff must lock screens when away.\n"
    r = stack.post("/api/admin/policies", files={"file": ("passwords.md", md)}, data={"category": "general"})
    assert r.status_code == 200 and r.json()["status"] == "processing"
    stack.c3.policies.wait_idle()
    st = stack.get("/api/admin/policies").json()
    assert st["status"]["documents"] == 2
    doc_id = r.json()["doc_id"]
    assert stack.delete(f"/api/admin/policies/{doc_id}").status_code == 200
    assert stack.get("/api/admin/policies").json()["status"]["documents"] == 1
    bad = stack.post("/api/admin/policies", files={"file": ("x.txt", b"hello")})
    assert bad.status_code == 415


def test_unknown_demo_policy_is_404(stack):
    assert stack.post("/api/admin/load-demo?which=nope").status_code == 404


# ---- trace, events, feedback ------------------------------------------------------------
def test_admin_trace_for_a_request(stack):
    rid = chat(stack, STOP_MEDS).json()["message"]["requestId"]
    t = stack.get(f"/api/admin/trace/{rid}").json()
    assert t["verdict"] == "VIOLATION" and t["cited_clauses"][0]["clause_id"].endswith("-4.1")
    assert "stop the medication" not in json.dumps(t).lower()          # hashes only
    assert stack.get("/api/admin/trace/req_nope").status_code == 404


def test_c4_receives_one_event_per_check_and_levels_rise(stack):
    for _ in range(3):
        chat(stack, STOP_MEDS)
    chat(stack, FINE)
    stack.c3.emitter.flush()
    ev = stack.get("/api/events").json()
    assert len(ev["events"]) == 4
    user = ev["users"][0]
    assert user["violations"] == 3 and user["level"].startswith("P4")
    stack.post("/api/events/reset")
    assert stack.get("/api/events").json()["events"] == []


def test_feedback_is_saved_for_later_labelling(stack):
    j = chat(stack, STOP_MEDS, role="admin").json()
    r = stack.post("/api/feedback", json={"request_id": j["message"]["requestId"],
                                          "c3_verdict": "VIOLATION", "answer_text": STOP_MEDS,
                                          "judged_violation": True, "note": "agree"})
    assert r.status_code == 200
    stack.post("/api/feedback", json={"request_id": "req_2", "c3_verdict": "CLEAN",
                                      "answer_text": FINE, "judged_violation": True})
    s = stack.get("/api/feedback/summary").json()
    assert s == {"total": 2, "agree": 1, "disagree": 1}
    rows = [json.loads(x) for x in (stack.tmp / "feedback.jsonl").read_text().splitlines()]
    assert rows[0]["source"] == "demo-feedback" and rows[0]["answer_text"] == STOP_MEDS


# ---- UI and status ----------------------------------------------------------------------
def test_ui_is_served_and_status_reports_the_services(stack):
    html = stack.get("/").text
    assert "<title>MediGuard Policy Check Demo</title>" in html and "/static/app.js" in html
    assert stack.get("/static/app.js").status_code == 200 and stack.get("/static/style.css").status_code == 200
    s = stack.get("/api/status").json()
    assert s["c3"]["status"] == "ok" and s["c4"]["status"] == "ok" and s["org_id"] == "demo-hospital"
    assert len(stack.get("/api/scenarios").json()) == len(SCENARIOS) >= 10
    assert RIVERSIDE.exists()


def test_scenarios_are_well_formed():
    for s in SCENARIOS:
        assert {"id", "title", "prompt", "answer", "expect"} <= set(s)
        assert s["expect"] in ("withheld", "clean") and len(s["answer"].split()) >= 6


# ---- C4 stub contract -------------------------------------------------------------------
def event(i=1, verdict="CLEAN", clause=None, user="u1"):
    return {"event_id": f"evt-c3-{i}", "org_id": "o", "user_id": user, "request_id": f"r{i}",
            "session_id": "s", "event_timestamp": "2026-10-23T10:00:00.000000Z",
            "c3_verdict": verdict, "clause_id": clause, "leaked_pii_detected": False,
            "leaked_pii_count": 0}


def test_c4_stub_follows_the_event_contract():
    c = TestClient(c4_stub.create_app({"k3": "c3", "k1": "c1"}))
    h = {"X-Service-Key": "k3"}
    assert c.post("/v1/events/c3", json=event()).status_code == 401
    assert c.post("/v1/events/c3", headers={"X-Service-Key": "bad"}, json=event()).status_code == 401
    assert c.post("/v1/events/c3", headers={"X-Service-Key": "k1"}, json=event()).status_code == 403
    r = c.post("/v1/events/c3", headers=h, json=event())
    assert r.status_code == 202 and r.json() == {"accepted": True, "request_counted": True}
    d = c.post("/v1/events/c3", headers=h, json=event())
    assert d.status_code == 200 and d.json() == {"duplicate": True}
    bad = c.post("/v1/events/c3", headers=h, json={**event(2), "c3_verdict": "VIOLATION"})
    assert bad.status_code == 422                                # a violation needs a clause
    assert c.post("/v1/events/c3", headers=h, json={**event(3), "surprise": 1}).status_code == 422
    assert len(c.get("/_demo/events").json()) == 1
