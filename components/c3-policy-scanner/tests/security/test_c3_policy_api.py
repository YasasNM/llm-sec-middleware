import pytest
from c3_helpers import KEY, make_pdf, make_scanned_pdf, markdown_policy, revised
from fastapi.testclient import TestClient

from c3.main import create_app

H = {"X-Service-Key": KEY}
ORG = "northfield-general"


@pytest.fixture
def client(make_container):
    c = make_container()
    with TestClient(create_app(c)) as tc:
        tc.c = c
        yield tc


def upload(client, name="guide.pdf", data=None, org=ORG, headers=H, **form):
    data = make_pdf() if data is None else data
    return client.post(f"/v1/orgs/{org}/policies", headers=headers,
                       files={"file": (name, data)}, data=form)


# FR18 ----------------------------------------------------------------------------------
def test_health_needs_no_key(client):
    r = client.get("/health")
    assert r.status_code == 200 and r.json()["status"] == "ok"


# T-X01 ---------------------------------------------------------------------------------
@pytest.mark.parametrize("headers", [{}, {"X-Service-Key": "wrong"}, {"X-Service-Key": ""}])
def test_no_or_wrong_key_is_401_on_every_policy_endpoint(client, headers):
    base = f"/v1/orgs/{ORG}/policies"
    calls = [
        client.get(base, headers=headers),
        client.get(base + "/status", headers=headers),
        client.get(base + "/doc-0001", headers=headers),
        client.get(base + "/doc-0001/clauses", headers=headers),
        client.delete(base + "/doc-0001", headers=headers),
        client.post(base, headers=headers, files={"file": ("a.pdf", make_pdf())}),
        client.put(base + "/doc-0001", headers=headers, files={"file": ("a.pdf", make_pdf())}),
    ]
    assert [r.status_code for r in calls] == [401] * 7


def test_service_with_no_configured_keys_refuses_everything(make_container):
    c = make_container()
    c.keys = {}
    with TestClient(create_app(c)) as tc:
        assert tc.get(f"/v1/orgs/{ORG}/policies", headers=H).status_code == 401


# FR13 ----------------------------------------------------------------------------------
def test_full_api_flow(client):
    r = upload(client, name="clinical-ai-guidelines-2024.pdf", category="ai_usage",
               uploaded_by="u1")
    assert r.status_code == 202
    doc = r.json()
    assert doc["status"] == "processing" and doc["doc_code"] == "CAIG"
    assert set(doc) >= {"doc_id", "org_id", "name", "filename", "sha256", "size_bytes", "version",
                        "status", "status_message", "clause_count", "uploaded_at", "uploaded_by"}
    client.c.policies.wait_idle()

    listed = client.get(f"/v1/orgs/{ORG}/policies", headers=H).json()
    assert len(listed) == 1 and listed[0]["status"] == "active" and listed[0]["clause_count"] == 10

    st = client.get(f"/v1/orgs/{ORG}/policies/status", headers=H).json()
    assert st["state"] == "ready" and st["clauses"] == 10 and st["index_version"].startswith("idx-")

    cl = client.get(f"/v1/orgs/{ORG}/policies/{doc['doc_id']}/clauses", headers=H).json()
    assert cl[0]["clause_id"] == "CAIG-1" and {"title", "severity", "category"} <= set(cl[0])

    rev = make_pdf(revised(drop={"7"}))
    r = client.put(f"/v1/orgs/{ORG}/policies/{doc['doc_id']}", headers=H,
                   files={"file": ("new-name.pdf", rev)})
    assert r.status_code == 202
    client.c.policies.wait_idle()
    got = client.get(f"/v1/orgs/{ORG}/policies/{doc['doc_id']}", headers=H).json()
    assert got["clause_count"] == 9 and got["doc_code"] == "CAIG" and got["version"] == 2

    assert client.delete(f"/v1/orgs/{ORG}/policies/{doc['doc_id']}", headers=H).status_code == 204
    assert client.get(f"/v1/orgs/{ORG}/policies", headers=H).json() == []
    assert client.get(f"/v1/orgs/{ORG}/policies/{doc['doc_id']}", headers=H).status_code == 404


def test_markdown_upload_and_unreadable_pdf_status_message(client):
    assert upload(client, name="rules.md", data=markdown_policy().encode()).status_code == 202
    r = upload(client, name="scan.pdf", data=make_scanned_pdf())
    assert r.status_code == 202                   # accepted, then flagged by the worker
    client.c.policies.wait_idle()
    docs = {d["filename"]: d for d in client.get(f"/v1/orgs/{ORG}/policies", headers=H).json()}
    assert docs["rules.md"]["status"] == "active"
    assert docs["scan.pdf"]["status"] == "needs_attention"
    assert "text-based PDF" in docs["scan.pdf"]["status_message"]


def test_wrong_type_is_415(client):
    assert upload(client, name="notes.txt", data=b"hello").status_code == 415
    assert upload(client, name="fake.pdf", data=b"this is not a pdf").status_code == 415
    assert upload(client, name="empty.md", data=b"").status_code == 422


# T-X02 ---------------------------------------------------------------------------------
def test_oversize_files_are_413(make_container):
    c = make_container()
    c.parser.limits = type(c.parser.limits)(max_pdf_mb=0.001, max_md_kb=1)
    with TestClient(create_app(c)) as tc:
        assert upload(tc, data=make_pdf()).status_code == 413
        big = ("## 1 A\n" + "word " * 2000).encode()
        assert upload(tc, name="big.md", data=big).status_code == 413


# T-X04 ---------------------------------------------------------------------------------
@pytest.mark.parametrize("payload", [
    b"%PDF-1.4\n1 0 obj<</Type/Catalog>>endobj\ntrailer<</Root 1 0 R>>\n%%EOF",
    b"%PDF-1.7\n" + b"\x00\xff" * 500,
    b"%PDF-" + b"A" * 10000,
    b"%PDF-1.4\n%%EOF",
])
def test_malformed_pdfs_do_not_crash_the_service(client, payload):
    r = upload(client, data=payload)
    assert r.status_code in (202, 415)
    client.c.policies.wait_idle()
    docs = client.get(f"/v1/orgs/{ORG}/policies", headers=H).json()
    assert all(d["status"] in ("needs_attention", "processing") for d in docs)
    assert client.get("/health").status_code == 200


# T-X05 ---------------------------------------------------------------------------------
def test_org_a_cannot_read_or_change_org_b(client):
    d = upload(client, org="org-b").json()
    client.c.policies.wait_idle()
    base = f"/v1/orgs/org-a/policies/{d['doc_id']}"
    assert client.get(base, headers=H).status_code == 404
    assert client.get(base + "/clauses", headers=H).status_code == 404
    assert client.put(base, headers=H, files={"file": ("x.pdf", make_pdf())}).status_code == 404
    assert client.delete(base, headers=H).status_code == 404
    assert client.get("/v1/orgs/org-a/policies", headers=H).json() == []
    assert client.get("/v1/orgs/org-a/policies/status", headers=H).json()["clauses"] == 0
    still = client.get(f"/v1/orgs/org-b/policies/{d['doc_id']}", headers=H)
    assert still.status_code == 200 and still.json()["clause_count"] == 10


@pytest.mark.parametrize("org", ["a b", "../etc", "x" * 65, "org/evil", "a;b"])
def test_bad_org_ids_are_rejected(client, org):
    r = client.get(f"/v1/orgs/{org}/policies", headers=H)
    assert r.status_code in (404, 422)


def test_filename_with_path_tricks_stays_inside_policy_folder(client):
    r = upload(client, name="../../../../evil.pdf")
    assert r.status_code == 202
    client.c.policies.wait_idle()
    root = client.c.files.root.resolve()
    for p in root.rglob("*"):
        assert root in p.resolve().parents or p.resolve() == root


def test_bad_severity_form_value_is_422(client):
    assert upload(client, severity="critical").status_code == 422


# T-X06 ---------------------------------------------------------------------------------
def test_logs_never_contain_policy_text(client, caplog):
    import logging

    caplog.set_level(logging.DEBUG)
    upload(client, name="rules.md", data=markdown_policy().encode())
    upload(client, name="scan.pdf", data=make_scanned_pdf())
    client.c.policies.wait_idle()
    joined = " ".join(r.getMessage() for r in caplog.records)
    assert "identifiable patient information" not in joined
