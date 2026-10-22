# C4 Heuristic Cross-Session Risk Profiler

Owner: Sandul · Port: 8004 · Status: **PP2 build** (scoring backend working with dummy data)

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

Not built yet: risk dashboard screens (`apps/web/src/features/risk`), real C1 to C3 events,
tuning of the half-life, the full evaluation, 10,000-event performance run.

## Run it on Windows (PowerShell, from this folder)

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
copy .env.example .env          # then keep the default keys for the demo
pytest                          # 52 tests, no database needed
python evaluation\run_scenarios.py     # 7 scenarios, C4 vs baseline, no database needed
```

With MongoDB Community running on this computer (default `localhost:27017`):

```powershell
uvicorn c4.main:app --port 8004                              # terminal 1
python scripts\event_generator.py --all                      # terminal 2: dummy data
```

Then open http://localhost:8004/docs . Each endpoint has an `X-Service-Key` field. Use the
`gateway` key from your `.env` for the admin endpoints.
Look at the data in MongoDB Compass: database `c4`, five collections.

## MongoDB collections (database `c4`)

`security_events`, `rule_hits`, `user_behaviour_features`, `user_risk_profiles`, `admin_actions`.
See BLUEPRINT.md section 13.4 for fields and indexes.

## Files

```text
configs/rules.yaml        all scoring numbers (no thresholds in code)
src/c4/main.py            the app           src/c4/auth.py      service-key check
src/c4/api/               endpoints         src/c4/core/        rules, decay, features, profile
src/c4/db/                MongoDB           scripts/            dummy-data generator, reset
evaluation/               scenarios (dummy data), fixed event-count baseline, comparison run
tests/                    unit, integration, contract, security, performance
```

## Data and models

No models (C4 uses no machine learning by design). Only synthetic data. No secrets in Git.
