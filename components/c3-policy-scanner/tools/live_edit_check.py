"""Does editing the policy change the verdict with the REAL models? (the live-update promise)

For every violating demo example: scan it, remove the one rule that governs it from the policy,
scan again, put the rule back, scan a third time. A model that reads the rules withholds,
releases, then withholds again, with no retraining and no restart.

    python tools/live_edit_check.py            # uses models/ or the folder in C3_RUN_DIR
"""
from __future__ import annotations

import re
import tempfile

# demo example -> the rule (heading number) that governs it
GOVERNING = {"meds-stop": "4.1", "chest-pain": "5.1", "employer": "6.1", "opioid": "4.2",
             "diagnosis": "7.1", "breach": "8.1", "inject": "4.1"}


def without_clause(md: str, number: str) -> str:
    """The policy text with the `### number ...` block removed."""
    blocks = re.split(r"(?m)^(?=#{2,3} )", md)
    return "".join(b for b in blocks if not re.match(rf"#{{2,3}} {re.escape(number)}\s", b))


def main() -> None:
    import torch

    from c3.config import MgSettings, Settings
    from c3.container import _load_calibration, build_container
    from c3.demo.scenarios import RIVERSIDE, SCENARIOS
    from c3.generation.t5 import T5Generator
    from c3.indexing.embedder import TransformerEmbedder
    from c3.training.pipeline import MODELS

    _ = MgSettings
    torch.set_num_threads(4)
    full = RIVERSIDE.read_text(encoding="utf-8")
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        s = Settings(db_path=f"{tmp}/c3.db", chroma_dir=f"{tmp}/c", policy_dir=f"{tmp}/p",
                     watch_dir=f"{tmp}/w", enable_watcher=False)
        emb = TransformerEmbedder(MODELS / "retriever", "sentence-transformers/all-MiniLM-L6-v2")
        calib = _load_calibration(MODELS / "generator")
        gen = T5Generator(MODELS / "generator", device="cpu",
                          temperature=calib["temperature"] if calib else 1.0)
        c = build_container(s, embedder=emb, generator=gen, keys={})
        doc = c.policies.upload("demo", "riverside.md", full.encode())
        c.policies.wait_idle()
        doc_id = doc["doc_id"]

        def verdict(text: str, rid: str) -> str:
            v = c.scanner.scan({"request_id": rid, "org_id": "demo", "user_id": "u",
                                "session_id": "s", "response_text": text,
                                "placeholder_manifest": []})
            return "withheld" if v["c3_verdict"] == "VIOLATION" else "clean"

        results = []
        for sc in SCENARIOS:
            if sc["id"] not in GOVERNING:
                continue
            num = GOVERNING[sc["id"]]
            c.policies.replace("demo", doc_id, "riverside.md", full.encode())
            c.policies.wait_idle()
            before = verdict(sc["answer"], f"a_{sc['id']}".replace("-", "_"))
            c.policies.replace("demo", doc_id, "riverside.md", without_clause(full, num).encode())
            c.policies.wait_idle()
            removed = verdict(sc["answer"], f"b_{sc['id']}".replace("-", "_"))
            c.policies.replace("demo", doc_id, "riverside.md", full.encode())
            c.policies.wait_idle()
            restored = verdict(sc["answer"], f"c_{sc['id']}".replace("-", "_"))
            ok = before == "withheld" and removed == "clean" and restored == "withheld"
            results.append(ok)
            print(f"{'ok  ' if ok else '----'} {sc['id']:11s} rule {num}: with={before:8s} "
                  f"removed={removed:8s} restored={restored}")
        c.stop()
    print(f"\nflipped as a rule-reading model should: {sum(results)}/{len(results)}")


if __name__ == "__main__":
    main()
