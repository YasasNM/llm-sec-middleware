"""Send dummy events for the 7 scenarios to a running C4 (through the real /v1/events/* API).

Examples (from the components/c4-risk-profiler folder):
    python scripts/event_generator.py --all
    python scripts/event_generator.py --scenario burst_attacker --user user_009
    python scripts/event_generator.py --scenario burst_attacker --dry-run

Keys: taken from the MG_ACCEPTED_KEYS environment variable or from the .env file in this folder
(the same "c1:key,c2:key,c3:key,gateway:key" list C4 uses).
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from evaluation.scenarios import SCENARIOS, USER_FOR_SCENARIO, build_scenario  # noqa: E402


def read_keys() -> dict[str, str]:
    raw = os.environ.get("MG_ACCEPTED_KEYS", "")
    env_file = ROOT / ".env"
    if not raw and env_file.exists():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            if line.startswith("MG_ACCEPTED_KEYS="):
                raw = line.split("=", 1)[1].strip()
    keys = {}
    for pair in raw.split(","):
        if ":" in pair:
            name, key = pair.split(":", 1)
            keys[name.strip()] = key.strip()
    return keys


# One counter per outcome, so the summary line shows accepted / duplicate / rejected
def send(client: httpx.Client, keys: dict[str, str], items: list[dict]) -> dict[str, int]:
    counts = {"accepted": 0, "duplicate": 0, "rejected": 0}
    for n, item in enumerate(items, start=1):
        src = item["source"]
        # Each source posts with its own key, exactly like the real components will
        r = client.post(
            f"/v1/events/{src}", json=item["payload"], headers={"X-Service-Key": keys[src]}
        )
        if r.status_code == 202:  # 202 = stored
            counts["accepted"] += 1
        elif r.status_code == 200:  # 200 = already had it (duplicate event_id)
            counts["duplicate"] += 1
        else:
            counts["rejected"] += 1
            print(f"  rejected ({r.status_code}): {r.text[:200]}")
        if n % 100 == 0:
            print(f"  ... {n}/{len(items)} events sent")
    return counts


def main() -> None:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--scenario", choices=list(SCENARIOS))
    ap.add_argument("--all", action="store_true", help="run all 7 scenarios (user_001 .. user_007)")
    ap.add_argument("--user")
    ap.add_argument("--org", default="org_001")
    ap.add_argument("--url", default="http://localhost:8004")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--dry-run", action="store_true", help="only count the events, send nothing")
    args = ap.parse_args()
    if not args.all and not args.scenario:
        ap.error("choose --scenario NAME or --all")

    names = list(SCENARIOS) if args.all else [args.scenario]
    end = datetime.now(UTC)
    keys = {} if args.dry_run else read_keys()
    if not args.dry_run and not {"c1", "c2", "c3"} <= set(keys):
        sys.exit("No keys found. Set MG_ACCEPTED_KEYS (or create the .env file, see .env.example).")

    with httpx.Client(base_url=args.url, timeout=30) as client:
        for name in names:
            user = args.user if (args.user and not args.all) else USER_FOR_SCENARIO[name]
            items = build_scenario(name, user, args.org, args.seed, end)
            print(f"{name}: user {user}, {len(items)} events")
            if args.dry_run:
                continue
            print("  ", send(client, keys, items))


if __name__ == "__main__":
    main()
