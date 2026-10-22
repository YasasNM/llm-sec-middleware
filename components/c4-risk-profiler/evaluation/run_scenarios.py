"""Run C4's scoring on the 7 scenarios and compare with the baseline. No database needed.

python evaluation/run_scenarios.py
python evaluation/run_scenarios.py --seed 7
"""

from __future__ import annotations

import argparse
import sys
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from c4.core.classify import priority_for  # noqa: E402
from c4.core.decay import score_at  # noqa: E402
from c4.core.rules import replay  # noqa: E402
from c4.core.ruleset import load_ruleset  # noqa: E402
from c4.db.mongo import parse_ts  # noqa: E402

from evaluation.baseline import baseline_score  # noqa: E402
from evaluation.scenarios import SCENARIOS, build_scenario  # noqa: E402


def internal(items: list[dict]) -> list[dict]:
    return [
        {
            **i["payload"],
            "source": i["source"],
            "event_timestamp": parse_ts(i["payload"]["event_timestamp"]),
        }
        for i in items
    ]


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--rules", default=str(ROOT / "configs" / "rules.yaml"))
    args = ap.parse_args()

    rs = load_ruleset(args.rules)
    end = datetime.now(UTC).replace(microsecond=0)
    print(f"ruleset {rs.version}, half-life {rs.half_life_hours} h, seed {args.seed}\n")
    head = (
        f"{'scenario':<22}{'events':>7}{'peak':>7}{'C4 score':>10}{'C4':>5}"
        f"{'baseline':>10}{'base':>6}  rules fired"
    )
    print(head)
    print("-" * len(head))
    for name in SCENARIOS:
        events = internal(build_scenario(name, seed=args.seed, end=end))
        hits = replay(events, rs)
        score = round(score_at(hits, end, rs), 1)
        peak = max([h["score_after"] for h in hits], default=0.0)
        base = baseline_score(events, end, rs.window_days)
        reasons = sorted({h["rule_id"] for h in hits})
        print(
            f"{name:<22}{len(events):>7}{peak:>7.1f}{score:>10.1f}{priority_for(score, rs):>5}"
            f"{base:>10.1f}{priority_for(base, rs):>6}  {','.join(reasons) or '-'}"
        )
    print("\npeak = highest score reached at any time; C4 score = score now (after time decay).")


if __name__ == "__main__":
    main()
