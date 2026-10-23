"""Human review of generated answers (blueprint 8.4, Appendix E decision rules).

    python -m c3.training.public.review export  generated_answers.jsonl  review.csv
    ... open review.csv in a spreadsheet, fill the `human_ok` column with yes or no ...
    python -m c3.training.public.review apply   generated_answers.jsonl  review.csv  reviewed.jsonl

For every generated pair the sheet has two rows: the answer meant to break the clause and the
answer meant to follow it. A person answers one question: "Does this answer do what the label
says?" (yes/no). A human `no` always removes the answer. A human `yes` keeps it even if the
judge disagreed. Blank means: leave the judge's decision. The result is written with
`human_reviewed` so reports can say how much was checked by a person.
"""
from __future__ import annotations

import csv
import json
import sys
from pathlib import Path

COLUMNS = ["row_id", "clause_id", "intended", "clause_text_hint", "answer", "judge_score",
           "kept_by_judge", "human_ok", "note"]


def export(src: Path, dst: Path) -> int:
    rows = [json.loads(x) for x in src.read_text(encoding="utf-8").splitlines() if x.strip()]
    with dst.open("w", newline="", encoding="utf-8-sig") as fh:
        w = csv.DictWriter(fh, fieldnames=COLUMNS)
        w.writeheader()
        for i, r in enumerate(rows):
            for kind, key, score, kept in (
                ("VIOLATES the clause", "violating", "judge_p_violating", "keep_violating"),
                ("FOLLOWS the clause", "compliant", "judge_p_compliant", "keep_compliant"),
            ):
                w.writerow({"row_id": f"{i}:{key}", "clause_id": r["clause_id"], "intended": kind,
                            "clause_text_hint": r.get("question", "")[:160],
                            "answer": r[key], "judge_score": round(r[score], 2),
                            "kept_by_judge": "yes" if r[kept] else "no", "human_ok": "", "note": ""})
    return len(rows) * 2


def apply(src: Path, sheet: Path, dst: Path) -> dict:
    rows = [json.loads(x) for x in src.read_text(encoding="utf-8").splitlines() if x.strip()]
    decisions: dict[str, str] = {}
    with sheet.open(newline="", encoding="utf-8-sig") as fh:
        for rec in csv.DictReader(fh):
            ok = (rec.get("human_ok") or "").strip().lower()
            if ok in ("yes", "no", "y", "n"):
                decisions[rec["row_id"]] = "yes" if ok.startswith("y") else "no"
    stats = {"reviewed": len(decisions), "removed_by_human": 0, "added_by_human": 0}
    with dst.open("w", encoding="utf-8") as out:
        for i, r in enumerate(rows):
            for key, flag in (("violating", "keep_violating"), ("compliant", "keep_compliant")):
                d = decisions.get(f"{i}:{key}")
                if d == "no" and r[flag]:
                    stats["removed_by_human"] += 1
                if d == "yes" and not r[flag]:
                    stats["added_by_human"] += 1
                if d:
                    r[flag] = d == "yes"
                r[f"human_reviewed_{key}"] = bool(d)
            out.write(json.dumps(r, ensure_ascii=False) + "\n")
    return stats


def main() -> None:
    if len(sys.argv) >= 4 and sys.argv[1] == "export":
        print("rows written:", export(Path(sys.argv[2]), Path(sys.argv[3])))
    elif len(sys.argv) >= 5 and sys.argv[1] == "apply":
        print(apply(Path(sys.argv[2]), Path(sys.argv[3]), Path(sys.argv[4])))
    else:
        raise SystemExit(__doc__)


if __name__ == "__main__":
    main()
