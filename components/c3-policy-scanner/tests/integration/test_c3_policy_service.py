import time

from c3_helpers import (
    POLICY,
    CountingEmbedder,
    make_pdf,
    make_scanned_pdf,
    markdown_policy,
    revised,
    wait_until,
)

ORG = "northfield-general"


def test_upload_replace_delete_lifecycle(make_container):
    c = make_container()
    svc = c.policies
    doc = svc.upload(ORG, "clinical-ai-guidelines-2024.pdf", make_pdf(), name="Clinical AI",
                     category="ai_usage", uploaded_by="u1")
    assert doc["status"] == "processing" and doc["doc_code"] == "CAIG"
    svc.wait_idle()
    doc = svc.get(ORG, doc["doc_id"])
    assert doc["status"] == "active" and doc["clause_count"] == 10 and doc["version"] == 1
    assert svc.status(ORG)["state"] == "ready"
    assert svc.status(ORG)["clauses"] == 10

    # Replace with a new filename: clause ids and doc_code must not change (FR20)
    ids_before = {x["clause_id"] for x in svc.clauses(ORG, doc["doc_id"])}
    rev = revised(change={"4.1": "Dose advice must be verified by two pharmacists."})
    svc.replace(ORG, doc["doc_id"], "totally-different-name.pdf", make_pdf(rev))
    svc.wait_idle()
    doc2 = svc.get(ORG, doc["doc_id"])
    assert doc2["doc_code"] == "CAIG" and doc2["version"] == 2 and doc2["status"] == "active"
    assert {x["clause_id"] for x in svc.clauses(ORG, doc["doc_id"])} == ids_before
    # only the changed clause was embedded again
    assert c.embedder.texts_embedded == 11

    svc.delete(ORG, doc["doc_id"])
    assert svc.list_documents(ORG) == []
    assert svc.status(ORG)["state"] == "empty"


def test_bad_replacement_keeps_last_good_version_live(make_container):
    c = make_container()
    svc = c.policies
    doc = svc.upload(ORG, "guide.pdf", make_pdf())
    svc.wait_idle()
    version_before = c.registry.get(ORG).version

    svc.replace(ORG, doc["doc_id"], "guide.pdf", make_scanned_pdf())
    svc.wait_idle()
    after = svc.get(ORG, doc["doc_id"])
    assert after["status"] == "needs_attention"
    assert "text-based PDF" in after["status_message"]
    assert after["version"] == 1 and after["clause_count"] == 10      # old version still live
    assert c.registry.get(ORG).version == version_before             # index untouched
    assert svc.status(ORG)["state"] == "attention"

    # a good replacement clears the warning
    svc.replace(ORG, doc["doc_id"], "guide.pdf", make_pdf())
    svc.wait_idle()
    assert svc.get(ORG, doc["doc_id"])["status"] == "active"


def test_first_upload_with_bad_file_is_needs_attention(make_container):
    c = make_container()
    doc = c.policies.upload(ORG, "scan.pdf", make_scanned_pdf())
    c.policies.wait_idle()
    got = c.policies.get(ORG, doc["doc_id"])
    assert got["status"] == "needs_attention" and got["clause_count"] == 0
    assert c.registry.get(ORG).snapshot().entries == {}


def test_two_uploads_in_a_row_do_not_interleave(make_container):
    c = make_container()
    svc = c.policies
    a = svc.upload(ORG, "alpha-policy.md", markdown_policy().encode())
    other = [("1", "Visitors", "Visitors must sign in at reception desk."),
             ("2", "Hours", "Visiting hours run from nine in the morning to five.")]
    b = svc.upload(ORG, "visitor-rules.md", markdown_policy(other).encode())
    svc.wait_idle()
    assert svc.get(ORG, a["doc_id"])["clause_count"] == 10
    assert svc.get(ORG, b["doc_id"])["clause_count"] == 2
    assert svc.status(ORG)["clauses"] == 12
    assert a["doc_code"] != b["doc_code"]


def test_rebuild_from_files_after_restart(make_container):
    c1 = make_container(subdir="persist")
    c1.policies.upload(ORG, "guide.pdf", make_pdf())
    c1.policies.wait_idle()
    c1.db.close()
    # new process, same folders: the in-memory index is empty until start() rebuilds it
    c2 = make_container(subdir="persist")
    assert c2.registry.get(ORG).snapshot().entries == {}
    c2.start()
    assert len(c2.registry.get(ORG).snapshot().entries) == 10


def test_scans_use_old_snapshot_while_upload_is_processing(make_container):
    """NFR13: a slow embedder must not block readers."""
    class Slow(CountingEmbedder):
        def embed(self, texts):
            time.sleep(0.4)
            return super().embed(texts)

    c = make_container(embedder=Slow())
    svc = c.policies
    svc.upload(ORG, "guide.md", markdown_policy().encode())
    svc.wait_idle()
    q = c.embedder.embed(["patient data"])[0]
    svc.replace(ORG, svc.list_documents(ORG)[0]["doc_id"], "guide.md",
                markdown_policy(revised(change={"2": "New scope wording for everyone."})).encode())
    t = time.time()
    hits = c.registry.get(ORG).search(q, k=3)           # runs while the worker is embedding
    assert time.time() - t < 0.2 and len(hits) == 3
    svc.wait_idle()


# T-I05 ---------------------------------------------------------------------------------
def test_watcher_debounces_quick_saves_into_one_run(make_container):
    c = make_container(watcher=True)
    c.cfg.indexer.debounce_ms = 300
    c.watcher.debounce_s = 0.3
    c.start()
    org_dir = c.watcher.root / ORG
    org_dir.mkdir()
    f = org_dir / "guide.md"
    f.write_text(markdown_policy(), encoding="utf-8")
    time.sleep(0.05)
    f.write_text(markdown_policy(revised(change={"2": "Edited scope wording one."})), encoding="utf-8")
    time.sleep(0.05)
    f.write_text(markdown_policy(revised(change={"2": "Edited scope wording two."})), encoding="utf-8")

    assert wait_until(lambda: len(c.db.list_runs(ORG)) >= 1, timeout=6)
    time.sleep(0.6)                                      # a late second run would appear here
    runs = c.db.list_runs(ORG)
    assert len(runs) == 1 and runs[0]["path"] == "file"
    assert runs[0]["queue_or_detect_ms"] >= 280          # detect_debounce stage was recorded
    scope = [x for x in c.policies.clauses(ORG, c.policies.list_documents(ORG)[0]["doc_id"])
             if x["clause_id"].endswith("-2")][0]
    assert "two" in scope["text"]


def test_watcher_handles_delete_and_ignores_other_files(make_container):
    c = make_container(watcher=True)
    c.watcher.debounce_s = 0.2
    c.start()
    d = c.watcher.root / ORG
    d.mkdir()
    (d / "notes.txt").write_text("not a policy")
    (d / "guide.md").write_text(markdown_policy(), encoding="utf-8")
    assert wait_until(lambda: c.policies.status(ORG)["clauses"] == 10, timeout=6)
    (d / "guide.md").unlink()
    assert wait_until(lambda: c.policies.status(ORG)["clauses"] == 0, timeout=6)


def test_watcher_polling_observer_works(make_container):
    c = make_container(watcher=True)
    from c3.indexing.watcher import MarkdownWatcher

    c.watcher.stop()
    c.watcher = MarkdownWatcher(c.policies, c.watcher.root, debounce_ms=200, use_polling=True,
                                poll_interval_s=0.2)
    c.start()
    d = c.watcher.root / ORG
    d.mkdir()
    (d / "guide.md").write_text(markdown_policy(), encoding="utf-8")
    assert wait_until(lambda: c.policies.status(ORG)["clauses"] == 10, timeout=8)


def test_watcher_loads_files_already_present_at_start(make_container):
    c = make_container(watcher=True)
    c.watcher.debounce_s = 0.1
    d = c.watcher.root / ORG
    d.mkdir()
    (d / "guide.md").write_text(markdown_policy(), encoding="utf-8")
    c.start()
    assert wait_until(lambda: c.policies.status(ORG)["clauses"] == 10, timeout=6)


# T-I06 ---------------------------------------------------------------------------------
def test_chroma_backend_gives_same_results_and_survives_restart(make_container):
    c = make_container(backend="chroma", subdir="chroma-run")
    svc = c.policies
    doc = svc.upload(ORG, "guide.md", markdown_policy().encode())
    svc.wait_idle()
    col = c.registry.mirror._col(ORG)
    assert col.count() == 10
    svc.replace(ORG, doc["doc_id"], "guide.md",
                markdown_policy(revised(drop={"7"}, add=POLICY[:0])).encode())
    svc.wait_idle()
    assert col.count() == 9

    # A new registry on the same folder reloads vectors without embedding anything
    from c3.indexing.chroma_mirror import ChromaMirror
    from c3.indexing.registry import OrgIndexRegistry

    emb = CountingEmbedder()
    reg = OrgIndexRegistry(emb, ChromaMirror(c.settings.chroma_dir))
    snap = reg.get(ORG).snapshot()
    assert len(snap.entries) == 9 and emb.texts_embedded == 0
    q = emb.embed(["identifiable patient information"])[0]
    assert reg.get(ORG).search(q, k=1)[0].clause.clause_id == "GUID-3"


def test_chroma_collections_are_separate_per_org(make_container):
    c = make_container(backend="chroma", subdir="chroma-org")
    c.policies.upload("org-a", "a-guide.md", markdown_policy().encode())
    c.policies.upload("org:a", "a-guide.md", markdown_policy().encode())     # looks like org-a
    c.policies.wait_idle()
    names = c.registry.mirror.collection_names()
    assert len(names) == 2 and len(set(names)) == 2


def test_stale_vectors_from_another_retriever_are_ignored(make_container):
    c = make_container(backend="chroma", subdir="chroma-stale")
    c.policies.upload(ORG, "guide.md", markdown_policy().encode())
    c.policies.wait_idle()
    from c3.indexing.chroma_mirror import ChromaMirror
    from c3.indexing.embedder import HashingEmbedder
    from c3.indexing.registry import OrgIndexRegistry

    reg = OrgIndexRegistry(HashingEmbedder(dim=128), ChromaMirror(c.settings.chroma_dir))
    assert reg.get(ORG).snapshot().entries == {}
