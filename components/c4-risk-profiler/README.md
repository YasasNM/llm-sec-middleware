# C4 Heuristic Cross-Session Risk Profiler

Owner: Sandul · Port: 8004 in the blueprint (the demo uses 8000) · Status: **PP2 build** (scoring backend and dashboard working with dummy data)

- Build blueprint: [BLUEPRINT.md](BLUEPRINT.md) · PP2 notes and demo script: [PP2_GUIDE.md](PP2_GUIDE.md)
- Shared interfaces: [../../contracts/CONTRACTS.md](../../contracts/CONTRACTS.md)
- Whole product: [../../PROJECT_BLUEPRINT.md](../../PROJECT_BLUEPRINT.md)

C4 receives one event per request from C1, C2 and C3 (ids, verdicts, scores and counts only, never
text). It turns them into an explainable, time-decayed 0 to 100 risk score per user, a priority
P1 to P4, and a block recommendation. An admin blocks; C1 enforces through `/v1/blocklist`.

## What works now

| FR | What | Where |
|---|---|---|
| FR-01 | Receive and validate events (contract schemas, duplicates, per-sender keys) | `src/c4/api/events.py` |
| FR-02 | One profile per (`org_id`, `user_id`) | `src/c4/core/recompute.py` |
| FR-03 | Behaviour features | `src/c4/core/features.py` |
| FR-04 | Reproducible 0 to 100 score: rules R1 to R7 and time decay | `src/c4/core/rules.py`, `decay.py` |
| FR-05 | Priority P1 to P4 | `src/c4/core/classify.py` |
| FR-06 | Top contributing reasons, every point traceable to a stored rule hit | `features.py`, collection `rule_hits` |
| FR-07 | Profile updates when a new event arrives (also late events) | `recompute.py` |
| FR-08 | P1 gives `BLOCK_RECOMMENDED`; admin block and unblock; block list for C1 | `api/actions.py`, `api/blocklist.py` |

| FR-09 | Risk dashboard in the browser (overview, user page, access control, scoring rules) | `src/c4/dashboard/`, `api/dashboard.py` |

Still to do: real C1 to C3 events instead of dummy data, the live block in C1 / the gateway
(C4 already publishes the block list), tuning of the half-life, the full evaluation, the
10,000-event performance run.

## Run it on Windows

Easiest: double-click **`Start_C4_Demo.bat`** in this folder (MongoDB must be running, which
it does by itself after installation). The dashboard opens at http://localhost:8000/dashboard/ .
On the page, open **Demo tools** and press **Load demo data**.

From a terminal (this folder), for the tests and the scenario table:

```powershell
pip install -e ".[dev]"
python -m pytest                       # 66 tests, no database needed
python evaluation\run_scenarios.py     # 7 scenarios, C4 vs baseline, no database needed
python -m uvicorn c4.main:app --port 8000
```

Look at the data in MongoDB Compass: database `c4`, five collections. The API list with
`X-Service-Key` is at http://localhost:8000/docs (use the `gateway` key from `.env`).

## MongoDB collections (database `c4`)

`security_events`, `rule_hits`, `user_behaviour_features`, `user_risk_profiles`, `admin_actions`.
See BLUEPRINT.md section 13.4 for fields and indexes.

## Files

```text
configs/rules.yaml        all scoring numbers (no thresholds in code)
src/c4/main.py            the app           src/c4/auth.py      service-key check
src/c4/api/               endpoints         src/c4/core/        rules, decay, features, profile
src/c4/db/                MongoDB           src/c4/dashboard/   the browser page (HTML, CSS, JS)
src/c4/demo/              dummy scenarios and the live-attack helper
scripts/                  dummy-data generator, reset
evaluation/               fixed event-count baseline and comparison run
tests/                    unit, integration, contract, security, performance, dashboard
```
A full explanation of every file is in FILE_GUIDE.docx (Word).

## Data and models

No models (C4 uses no machine learning by design). Only synthetic data. No secrets in Git.
