# ruff: noqa: E501
"""Run testdata/cases.csv against the running demo and report how the policy check did.

    .venv\\Scripts\\python tools\\run_testdata.py            # demo must be running (start_demo.bat)
    .venv\\Scripts\\python tools\\run_testdata.py --all-policies   # search every loaded policy, not only Riverside

It loads the Riverside policy first if the demo has none. Cases are written for that policy.
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import sys
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
CASES = ROOT / "testdata" / "cases.csv"
OUT = ROOT / "var" / "testdata_results.json"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://127.0.0.1:8000")
    ap.add_argument("--all-policies", action="store_true")
    a = ap.parse_args()
    c = httpx.Client(base_url=a.url, timeout=120)
    try:
        docs = c.get("/api/admin/policies").json()["documents"]
    except httpx.HTTPError:
        print("The demo is not running. Start it with start_demo.bat first.")
        return 2
    if not any(d["name"].startswith("riverside") and d["status"] == "active" for d in docs):
        r = c.post("/api/admin/load-demo?which=riverside").json()
        while c.get(f"/api/admin/policies/{r['doc_id']}").json()["status"] == "processing":
            time.sleep(0.2)

    rows = list(csv.DictReader(CASES.open(encoding="utf-8")))
    results, tp, fn, fp, tn, rule_ok, rule_n = [], 0, 0, 0, 0, 0, 0
    by_level: dict[str, list[int]] = {}
    for row in rows:
        body = {"role": "admin", "prompt": row["prompt"], "llm_answer": row["answer"]}
        if not a.all_policies:
            body["categories"] = ["ai_usage"]
        r = c.post("/api/chat", json=body).json()
        v = r["admin"]
        got = "withheld" if v["c3_verdict"] == "VIOLATION" else "clean"
        cited = [x["clause_id"] for x in v.get("cited_clauses", [])]
        cited_num = (re.search(r"([\d.]+)$", cited[0]) or [None, None])[1] if cited else None
        ok = got == row["expected"]
        by_level.setdefault(row["level"], []).append(int(ok))
        if row["expected"] == "withheld":
            tp += ok
            fn += not ok
            if ok:
                rule_n += 1
                rule_ok += cited_num == row["rule"]
        else:
            tn += ok
            fp += not ok
        results.append({**row, "got": got, "cited": cited, "confidence": v.get("generation", {}).get("p_violation"), "ok": ok})
        flag = "ok  " if ok else "MISS"
        extra = f"cites {cited_num}" + (f" (wanted {row['rule']})" if row["rule"] and cited_num != row["rule"] and got == "withheld" else "") if cited else ""
        print(f"{flag} {row['id']:4} expected {row['expected']:8} got {got:8} {extra}  {row['note']}")

    n = len(rows)
    print()
    print(f"Correct verdicts: {tp + tn}/{n}  ({(tp + tn) / n:.0%})")
    print(f"Violations caught: {tp}/{tp + fn}   False alarms on safe answers: {fp}/{fp + tn}")
    print(f"Right rule cited when caught: {rule_ok}/{rule_n}")
    for lvl, xs in by_level.items():
        print(f"  {lvl:11} {sum(xs)}/{len(xs)}")
    OUT.parent.mkdir(exist_ok=True)
    OUT.write_text(json.dumps(results, indent=1, ensure_ascii=False), encoding="utf-8")
    print("Details saved to", OUT.relative_to(ROOT))
    return 0


if __name__ == "__main__":
    sys.exit(main())
