import time

import pytest
from c3_helpers import POLICY, CountingEmbedder, markdown_policy, revised

from c3.policy import PolicyLintError

ORG = "northfield-general"


def index(parser, indexer, md, org=ORG, doc_id="doc-1", version=1, code="CAIG", path="upload"):
    parsed = parser.parse_markdown(md, doc_code=code)
    return indexer.index_document(org, doc_id, version, parsed.clauses, path=path, t0=time.time())


def search(registry, embedder, org, query, k=3):
    idx = registry.get(org)
    return idx.search(embedder.embed([query])[0], k=k)


# T-I01 ---------------------------------------------------------------------------------
def test_add_clause_flips_probe_and_remove_flips_back(parser, indexer, registry, embedder):
    index(parser, indexer, markdown_policy())
    probe = "chatbot transcripts must be deleted after thirty days"
    before = search(registry, embedder, ORG, probe, k=1)[0]
    assert before.clause.clause_id != "CAIG-8"

    new = revised(add=[("8", "Transcript Deletion",
                        "Chatbot transcripts must be deleted after thirty days.")])
    index(parser, indexer, markdown_policy(new), version=2)
    after = search(registry, embedder, ORG, probe, k=1)[0]
    assert after.clause.clause_id == "CAIG-8"

    index(parser, indexer, markdown_policy(POLICY), version=3)
    gone = search(registry, embedder, ORG, probe, k=1)[0]
    assert gone.clause.clause_id != "CAIG-8"


# T-I02 ---------------------------------------------------------------------------------
def test_only_changed_clauses_are_embedded(parser, indexer, registry, embedder):
    run1 = index(parser, indexer, markdown_policy())
    assert run1.embedded == 10 and embedder.texts_embedded == 10

    rev = revised(change={"4.1": "Dose advice from an AI tool must be checked by two pharmacists."})
    run2 = index(parser, indexer, markdown_policy(rev), version=2)
    assert run2.diff.summary()["changed"] == 1
    assert run2.embedded == 1
    assert embedder.texts_embedded == 11                      # 10 first time + 1 changed
    assert run2.diff.summary()["unchanged"] == 9


def test_identical_reupload_embeds_nothing_but_bumps_version(parser, indexer, registry, embedder):
    r1 = index(parser, indexer, markdown_policy())
    r2 = index(parser, indexer, markdown_policy(), version=2)
    assert r2.embedded == 0 and embedder.texts_embedded == 10
    assert r2.index_version != r1.index_version


def test_renumbered_clause_reuses_its_vector(parser, indexer, registry, embedder):
    index(parser, indexer, markdown_policy())
    shifted = [("1", "Purpose", POLICY[0][2]), ("2", "Governance", "A brand new governance section."),
               ("3", "Scope", POLICY[1][2])]
    base = embedder.texts_embedded
    index(parser, indexer, markdown_policy(shifted), version=2)
    # 'Scope' moved from 2 to 3 with the same text: not embedded again. 'Governance' is new.
    assert embedder.texts_embedded - base == 1


def test_removed_clauses_leave_the_index(parser, indexer, registry):
    index(parser, indexer, markdown_policy())
    index(parser, indexer, markdown_policy(revised(drop={"7", "6"})), version=2)
    ids = {c.clause_id for c in registry.get(ORG).snapshot().clauses()}
    assert "CAIG-7" not in ids and "CAIG-6" not in ids and len(ids) == 8


# T-I03 ---------------------------------------------------------------------------------
def test_organisation_isolation(parser, indexer, registry, embedder):
    index(parser, indexer, markdown_policy(), org="org-a")
    assert search(registry, embedder, "org-a", "patient identifiers")
    assert search(registry, embedder, "org-b", "patient identifiers") == []
    only_b = [("1", "Visitors", "Visitors must sign in at the front desk.")]
    index(parser, indexer, markdown_policy(only_b + [("2", "Hours", "Visiting hours are 9 to 5.")]),
          org="org-b", doc_id="doc-9", code="VIS")
    ids_b = {c.clause_id for c in registry.get("org-b").snapshot().clauses()}
    ids_a = {c.clause_id for c in registry.get("org-a").snapshot().clauses()}
    assert ids_a.isdisjoint(ids_b)
    top_b = search(registry, embedder, "org-b", "patient identifiers", k=8)
    assert all(h.clause.clause_id.startswith("VIS") for h in top_b)


def test_invalid_org_id_is_refused(registry):
    with pytest.raises(ValueError):
        registry.get("../etc")


# T-I04 ---------------------------------------------------------------------------------
def test_copy_on_write_old_snapshot_is_unchanged(parser, indexer, registry):
    index(parser, indexer, markdown_policy())
    old = registry.get(ORG).snapshot()
    old_ids, old_version = set(old.entries), old.version
    index(parser, indexer, markdown_policy(revised(drop={"7"})), version=2)
    assert set(old.entries) == old_ids and old.version == old_version     # reader kept its view
    assert registry.get(ORG).snapshot().version != old_version


def test_clause_id_owned_by_another_document_is_refused(parser, indexer):
    index(parser, indexer, markdown_policy(), doc_id="doc-1")
    with pytest.raises(PolicyLintError) as e:
        index(parser, indexer, markdown_policy(), doc_id="doc-2")          # same code, other doc
    assert e.value.code == "CLAUSE_ID_TAKEN"


def test_search_filters_by_category(parser, indexer, registry, embedder):
    parsed = parser.parse_markdown(markdown_policy(), doc_code="CAIG", category="prescribing")
    indexer.index_document(ORG, "d1", 1, parsed.clauses, path="upload", t0=time.time())
    idx = registry.get(ORG)
    q = embedder.embed(["anything"])[0]
    assert idx.search(q, k=3, categories=["prescribing"])
    assert idx.search(q, k=3, categories=["disclosure"]) == []


def test_remove_document_clears_its_clauses(parser, indexer, registry):
    index(parser, indexer, markdown_policy())
    run = indexer.remove_document(ORG, "doc-1")
    assert len(run.diff.removed) == 10
    assert registry.get(ORG).snapshot().entries == {}


def test_stage_timers_and_run_row(parser, indexer):
    run = index(parser, indexer, markdown_policy())
    stages = run.stages_ms()
    assert set(stages) == {"queue", "parse", "embed", "upsert"}
    assert run.t_index_ms >= 0 and run.index_version.startswith("idx-")
    assert run.to_row()["added"] == 10


def test_reindex_after_retriever_change(parser, registry, embedder):
    from c3.indexing import PolicyIndexer

    indexer = PolicyIndexer(registry, embedder)
    index(parser, indexer, markdown_policy())
    before = embedder.texts_embedded
    old_version = registry.get(ORG).version
    indexer.reindex_org(ORG)
    assert embedder.texts_embedded - before == 10
    assert registry.get(ORG).version != old_version


def test_failed_embedding_leaves_old_snapshot_live(parser, registry):
    from c3.indexing import PolicyIndexer

    class Boom(CountingEmbedder):
        fail = False

        def embed(self, texts):
            if self.fail:
                raise RuntimeError("model crashed")
            return super().embed(texts)

    emb = Boom()
    reg = type(registry)(emb)
    idx = PolicyIndexer(reg, emb)
    index(parser, idx, markdown_policy())
    emb.fail = True
    with pytest.raises(RuntimeError):
        index(parser, idx, markdown_policy(revised(change={"2": "Totally new scope text here."})),
              version=2)
    assert len(reg.get(ORG).snapshot().entries) == 10
