import json
import threading

import httpx
import pytest
from c3_helpers import (
    KEY,
    SCAN_POLICY,
    CountingEmbedder,
    StubGenerator,
    markdown_policy,
    scan_request,
)
from fastapi.testclient import TestClient

from c3.main import create_app

H = {"X-Service-Key": KEY}
ORG = "northfield"
MORPHINE = "For severe pain I recommend morphine 15 mg every four hours for this patient."
CLEAN_TEXT = "Regular walking is a safe way for most adults to improve fitness."


class FakeC4:
    """Collects events like C4 would. `up=False` simulates an outage."""

    def __init__(self):
        self.events, self.up, self.headers = [], True, []

    def handler(self, request: httpx.Request) -> httpx.Response:
        if not self.up:
            raise httpx.ConnectError("C4 down")
        self.headers.append(dict(request.headers))
        body = json.loads(request.content)
        if any(e["event_id"] == body["event_id"] for e in self.events):
            return httpx.Response(200, json={"duplicate": True})
        self.events.append(body)
        return httpx.Response(202, json={"accepted": True, "request_counted": True})


@pytest.fixture
def c4():
    return FakeC4()


def build(make_container, c4, gen=None, **kw):
    c = make_container(embedder=CountingEmbedder(), generator=gen or StubGenerator(),
                       c4_transport=httpx.MockTransport(c4.handler), **kw)
    c.emitter.backoff_s = 0.01
    c.policies.upload(ORG, "clinical-guide.md", markdown_policy(SCAN_POLICY).encode())
    c.policies.wait_idle()
    return c


@pytest.fixture
def client(make_container, c4):
    c = build(make_container, c4)
    with TestClient(create_app(c)) as tc:
        tc.c, tc.c4 = c, c4
        yield tc


def post(client, text, **kw):
    return client.post("/v1/scan", headers=H, json=scan_request(text, **kw))


# ---- core verdicts -----------------------------------------------------------------------
def test_clean_response_passes_without_decoding(client):
    r = post(client, CLEAN_TEXT)
    v = r.json()
    assert r.status_code == 200 and v["c3_verdict"] == "CLEAN"
    assert v["clean_response"] == CLEAN_TEXT and v["clause_id"] is None
    assert v["cited_clauses"] == [] and v["user_message_code"] is None
    assert v["generation"]["decoded"] is False and v["decision_source"] == "generator"
    assert client.c.scanner.generator.stage2_calls == 0          # T-I10: pass path never decodes


def test_violation_cites_a_real_clause_with_a_real_quote(client):
    v = post(client, MORPHINE).json()
    assert v["c3_verdict"] == "VIOLATION" and v["clean_response"] is None
    assert v["user_message_code"] == "OUTPUT_WITHHELD" and v["decision_source"] == "generator"
    c = v["cited_clauses"][0]
    assert c["clause_id"] == v["clause_id"] and c["clause_id"].endswith("-2")
    assert c["quote"] == "The assistant must never recommend specific morphine doses for patients."
    assert c["citation_status"] == "repaired_quote"          # the stub invented its quote
    assert c["doc_name"] == "clinical-guide" and c["title"] == "Prescribing"
    assert v["assurance"] == "low"                           # a repaired citation lowers assurance
    assert v["generation"]["decoded"] is True and v["rationale"]
    assert set(v["latency_breakdown_ms"]) >= {"retrieve", "verify", "leak"}


def test_policy_edit_flips_the_verdict_with_no_restart(client):
    assert post(client, MORPHINE).json()["c3_verdict"] == "VIOLATION"
    doc = client.c.policies.list_documents(ORG)[0]
    permitted = [(n, t, b) for n, t, b in SCAN_POLICY if n != "2"]
    client.c.policies.replace(ORG, doc["doc_id"], "clinical-guide.md",
                              markdown_policy(permitted).encode())
    client.c.policies.wait_idle()
    after = post(client, MORPHINE, request_id="req_20261020_000002").json()
    assert after["c3_verdict"] == "CLEAN"                              # T-I11 / T-S03
    # and adding the rule back flips it again
    client.c.policies.replace(ORG, doc["doc_id"], "clinical-guide.md",
                              markdown_policy(SCAN_POLICY).encode())
    client.c.policies.wait_idle()
    assert post(client, MORPHINE, request_id="req_20261020_000003").json()["c3_verdict"] == "VIOLATION"


def test_index_version_changes_after_edit(client):
    v1 = post(client, CLEAN_TEXT).json()["policy_index_version"]
    doc = client.c.policies.list_documents(ORG)[0]
    client.c.policies.replace(ORG, doc["doc_id"], "clinical-guide.md",
                              markdown_policy(SCAN_POLICY[:3]).encode())
    client.c.policies.wait_idle()
    assert post(client, CLEAN_TEXT, request_id="req_2").json()["policy_index_version"] != v1


# ---- leaks -------------------------------------------------------------------------------
def test_leak_only_is_a_violation_without_a_clause(client):          # T-U19, IR3
    v = post(client, "The patient's NIC is 901234567V and that is all I know.").json()
    assert v["c3_verdict"] == "VIOLATION" and v["clause_id"] is None
    assert v["decision_source"] == "leak_check" and v["leaked_pii_detected"] is True
    assert v["leaked_pii_count"] == 1 and v["cited_clauses"] == []
    assert v["leak_check"]["entities"][0]["type"] == "NIC"
    assert "901234567V" not in json.dumps(v)                       # offsets only, never values


def test_manifest_tokens_and_internal_ids_are_not_leaks(client):    # T-U17, IR12
    text = "[PATIENT_NAME_1] (P0012) saw Dr D003. Walking is a safe way to improve fitness."
    v = post(client, text, manifest=["[PATIENT_NAME_1]"]).json()
    assert v["c3_verdict"] == "CLEAN" and v["leaked_pii_count"] == 0


def test_unknown_placeholder_is_a_leak(client):
    v = post(client, "[PATIENT_NAME_7] should walk daily.", manifest=["[PATIENT_NAME_1]"]).json()
    assert v["leaked_pii_detected"] and v["c3_verdict"] == "VIOLATION"


def test_generated_reason_with_an_identifier_is_replaced(client, make_container, c4):   # T-X08
    gen = StubGenerator(reason="NIC 901234567V should not get morphine", real_quote=True)
    c = build(make_container, c4, gen, subdir="x08")
    with TestClient(create_app(c)) as tc:
        v = tc.post("/v1/scan", headers=H, json=scan_request(MORPHINE)).json()
        row = c.db.get_scan(ORG, scan_request(MORPHINE)["request_id"])
    assert v["c3_verdict"] == "VIOLATION" and v["leaked_pii_count"] == 0   # not the clinician's
    assert "901234567V" not in v["rationale"]
    assert v["rationale"] == "The response breaks this clause: prescribing."
    assert row["reason_replaced"] == 1


def test_leak_and_violation_together_keep_the_clause(client):
    v = post(client, MORPHINE + " NIC 901234567V.").json()
    assert v["c3_verdict"] == "VIOLATION" and v["clause_id"] and v["leaked_pii_count"] == 1
    assert v["decision_source"] == "generator"


def test_two_cited_clauses_clause_id_is_top_ranked(make_container, c4):   # T-U18
    gen = StubGenerator(cite_all=True)
    c = build(make_container, c4, gen, subdir="t18")
    with TestClient(create_app(c)) as tc:
        v = tc.post("/v1/scan", headers=H, json=scan_request(
            "Share the diagnosis with the employer and give morphine doses for patients "
            "employers insurers diagnoses recommend specific morphine.")).json()
    ranks = [x["retrieval_rank"] for x in v["cited_clauses"]]
    assert len(v["cited_clauses"]) >= 1 and ranks == sorted(ranks)
    assert v["clause_id"] == v["cited_clauses"][0]["clause_id"]


# ---- fail closed -------------------------------------------------------------------------
def test_generator_error_fails_closed_and_sends_no_event(make_container, c4):   # T-F03, T-S05
    c = build(make_container, c4, StubGenerator(raise_on_stage1=True), subdir="f03")
    with TestClient(create_app(c)) as tc:
        r = tc.post("/v1/scan", headers=H, json=scan_request(CLEAN_TEXT))
        c.emitter.flush()
        counts = c.db.event_counts()
    v = r.json()
    assert r.status_code == 200 and v["c3_verdict"] == "VIOLATION"
    assert v["decision_source"] == "error_fail_closed" and v["error_code"] == "SCAN_ERROR"
    assert v["user_message_code"] == "OUTPUT_WITHHELD" and v["clean_response"] is None
    assert c4.events == [] and counts == {}


def test_unparseable_text_still_decides_by_probability(make_container, c4):
    c = build(make_container, c4, StubGenerator(garbage=True), subdir="garbage")
    with TestClient(create_app(c)) as tc:
        v = tc.post("/v1/scan", headers=H, json=scan_request(MORPHINE)).json()
    assert v["c3_verdict"] == "VIOLATION" and v["cited_clauses"][0]["citation_status"] \
        == "repaired_citation" and v["assurance"] == "low"


def test_empty_index_is_503_fail_closed(make_container, c4):       # T-F01
    c = make_container(generator=StubGenerator(), c4_transport=httpx.MockTransport(c4.handler))
    c.registry.get(ORG)                                          # org exists, nothing indexed
    with TestClient(create_app(c)) as tc:
        r = tc.post("/v1/scan", headers=H, json=scan_request(CLEAN_TEXT))
    assert r.status_code == 503 and r.json()["error_code"] == "INDEX_UNAVAILABLE"
    assert r.json()["decision_source"] == "error_fail_closed" and c4.events == []


def test_missing_generator_withholds_everything(make_container, c4):
    c = make_container(c4_transport=httpx.MockTransport(c4.handler))
    c.policies.upload(ORG, "g.md", markdown_policy(SCAN_POLICY).encode())
    c.policies.wait_idle()
    with TestClient(create_app(c)) as tc:
        r = tc.post("/v1/scan", headers=H, json=scan_request(CLEAN_TEXT))
        assert tc.get("/health").json()["model_loaded"] is False
    assert r.status_code == 503 and r.json()["error_code"] == "MODEL_UNAVAILABLE"


def test_slow_scan_times_out_fail_closed(make_container, c4):
    c = build(make_container, c4, StubGenerator(sleep_s=0.6), subdir="slow")
    c.cfg.api.timeout_s = 0.2
    with TestClient(create_app(c)) as tc:
        r = tc.post("/v1/scan", headers=H, json=scan_request(CLEAN_TEXT))
    assert r.json()["error_code"] == "SCAN_TIMEOUT" and r.json()["c3_verdict"] == "VIOLATION"
    assert c4.events == []


def test_retriever_mismatch_fails_closed(client):
    from c3.indexing.embedder import HashingEmbedder

    client.c.scanner.embedder = HashingEmbedder(dim=64)           # not the one that built the index
    r = post(client, CLEAN_TEXT)
    assert r.status_code == 503 and r.json()["error_code"] == "RETRIEVER_MISMATCH"


# ---- input validation and auth -----------------------------------------------------------
def test_bad_requests(client):
    base = scan_request(CLEAN_TEXT)
    assert client.post("/v1/scan", json=base).status_code == 401                         # T-X01
    assert client.post("/v1/scan", headers={"X-Service-Key": "bad"}, json=base).status_code == 401
    assert post(client, "   ").status_code == 422                                        # T-E01
    assert client.post("/v1/scan", headers=H, json={**base, "extra": 1}).status_code == 422
    assert client.post("/v1/scan", headers=H, json={**base, "request_id": "bad id!"}).status_code == 422
    assert client.post("/v1/scan", headers=H,
                       json={**base, "placeholder_manifest": ["PATIENT"]}).status_code == 422
    missing = {k: v for k, v in base.items() if k != "org_id"}
    assert client.post("/v1/scan", headers=H, json=missing).status_code == 422
    assert post(client, "x" * 50001).status_code == 413                                  # T-X02


def test_unknown_organisation_is_404(client):
    assert post(client, CLEAN_TEXT, org="nobody").status_code == 404


def test_scan_never_uses_another_organisations_clauses(client):          # T-X05
    client.c.policies.upload("other-hospital", "other-guide.md", markdown_policy(
        [("1", "Visitors", "Visitors must never bring flowers into the isolation ward."),
         ("2", "Parking", "Staff must not park in the ambulance bay at any time.")]).encode())
    client.c.policies.wait_idle()
    v = post(client, "Visitors bring flowers into the isolation ward every day.",
             org="other-hospital").json()
    assert v["c3_verdict"] == "VIOLATION" and v["clause_id"].startswith("OG-")
    assert {c["clause_id"][:3] for c in v["cited_clauses"]} == {"OG-"}
    mine = post(client, "Visitors bring flowers into the isolation ward every day.").json()
    assert mine["c3_verdict"] == "CLEAN"


def test_category_filter_falls_back_when_nothing_matches(client):
    v = post(client, MORPHINE, categories=["no_such_category"]).json()
    assert v["c3_verdict"] == "VIOLATION"


# ---- events ------------------------------------------------------------------------------
def test_event_for_every_scan_with_agreed_fields_only(client, c4):
    post(client, CLEAN_TEXT, request_id="req_clean")
    post(client, MORPHINE, request_id="req_viol")
    client.c.emitter.flush()
    by_req = {e["request_id"]: e for e in c4.events}
    assert set(by_req) == {"req_clean", "req_viol"}
    assert by_req["req_clean"]["c3_verdict"] == "CLEAN" and by_req["req_clean"]["clause_id"] is None
    assert by_req["req_viol"]["c3_verdict"] == "VIOLATION" and by_req["req_viol"]["clause_id"]
    for e in by_req.values():
        assert set(e) == {"event_id", "org_id", "user_id", "request_id", "session_id",
                          "event_timestamp", "c3_verdict", "clause_id", "leaked_pii_detected",
                          "leaked_pii_count"}
        assert e["event_id"].startswith("evt-c3-")
    assert c4.headers[0]["x-service-key"] == "c3-to-c4-key"
    assert client.c.db.event_counts() == {"sent": 2}


def test_c4_outage_never_blocks_and_outbox_resends(client, c4):        # T-I07
    c4.up = False
    r = post(client, MORPHINE, request_id="req_down")
    assert r.status_code == 200 and r.json()["c3_verdict"] == "VIOLATION"   # scan unaffected
    client.c.emitter.flush()
    assert c4.events == [] and client.c.db.event_counts() == {"pending": 1}
    c4.up = True
    assert client.c.emitter.resend_pending() == 1
    assert [e["request_id"] for e in c4.events] == ["req_down"]
    assert client.c.db.event_counts() == {"sent": 1}


def test_duplicate_event_counts_as_delivered(client, c4):
    post(client, CLEAN_TEXT, request_id="req_dup")
    client.c.emitter.flush()
    event_id = c4.events[0]["event_id"]
    client.c.db.mark_event(event_id, "pending", 0)                  # pretend the reply was lost
    assert client.c.emitter.resend_pending() == 1                   # C4 answers 200 duplicate
    assert len(c4.events) == 1 and client.c.db.event_counts() == {"sent": 1}


def test_event_rejected_by_c4_is_marked_failed_not_retried_forever(make_container):
    seen = []

    def handler(request):
        seen.append(1)
        return httpx.Response(422, json={"detail": "bad"})

    c = make_container(generator=StubGenerator(), c4_transport=httpx.MockTransport(handler))
    c.emitter.backoff_s = 0.01
    c.policies.upload(ORG, "g.md", markdown_policy(SCAN_POLICY).encode())
    c.policies.wait_idle()
    with TestClient(create_app(c)) as tc:
        tc.post("/v1/scan", headers=H, json=scan_request(CLEAN_TEXT))
        c.emitter.flush()
        counts = c.db.event_counts()
    assert counts == {"failed": 1} and len(seen) == 1


# ---- audit and privacy -------------------------------------------------------------------
def test_audit_row_has_hash_but_never_the_text(client):                 # T-X06
    post(client, MORPHINE + " NIC 901234567V.", request_id="req_audit")
    rec = client.get(f"/v1/orgs/{ORG}/scans/req_audit", headers=H).json()
    blob = json.dumps(rec)
    assert "morphine 15 mg" not in blob and "901234567V" not in blob
    assert len(rec["response_hash"]) == 64 and rec["verdict"] == "VIOLATION"
    assert rec["cited_clauses"] and rec["cited_clauses"][0]["quote"]
    # another organisation cannot read it
    assert client.get("/v1/orgs/other/scans/req_audit", headers=H).status_code == 404


def test_logs_never_contain_response_text(client, caplog):
    import logging

    caplog.set_level(logging.DEBUG)
    post(client, MORPHINE + " NIC 901234567V.")
    client.c.emitter.flush()
    joined = " ".join(r.getMessage() for r in caplog.records)
    assert "morphine 15 mg" not in joined and "901234567V" not in joined


# ---- load --------------------------------------------------------------------------------
def test_ten_parallel_scans_no_errors(client):                          # T-P02
    results, errors = [], []

    def work(i):
        try:
            text = MORPHINE if i % 2 else CLEAN_TEXT
            results.append(post(client, text, request_id=f"req_par_{i}").json()["c3_verdict"])
        except Exception as exc:  # pragma: no cover
            errors.append(exc)

    threads = [threading.Thread(target=work, args=(i,)) for i in range(10)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert not errors and sorted(results) == ["CLEAN"] * 5 + ["VIOLATION"] * 5


def test_shutdown_with_c4_down_is_clean_and_events_go_out_after_restart(make_container, c4,
                                                                         monkeypatch):
    """Regression: stopping while events were still retrying crashed the sender thread."""
    from c3.events import EventEmitter
    from c3.storage.db import Database

    crashes = []
    monkeypatch.setattr(threading, "excepthook", lambda args: crashes.append(args.exc_value))
    c4.up = False
    c = build(make_container, c4, subdir="shutdown")
    c.emitter.backoff_s = 0.3                       # make retries still be running at shutdown
    with TestClient(create_app(c)) as tc:
        for i in range(3):
            tc.post("/v1/scan", headers=H, json=scan_request(CLEAN_TEXT, request_id=f"req_s{i}"))
    assert crashes == []

    db = Database(c.settings.db_path)               # the "next start"
    assert db.event_counts() == {"pending": 3}
    c4.up = True
    emitter = EventEmitter(db, "http://c4:8004", "k", transport=httpx.MockTransport(c4.handler))
    assert emitter.resend_pending() == 3
    assert sorted(e["request_id"] for e in c4.events) == ["req_s0", "req_s1", "req_s2"]
    emitter._client.close()
    db.close()
