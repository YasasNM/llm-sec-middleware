# C1 Real-Time Healthcare Privacy Assistant

Owner: Linuka · Port: 8001

- Build blueprint: [BLUEPRINT.md](BLUEPRINT.md)
- Shared interfaces: [../../contracts/CONTRACTS.md](../../contracts/CONTRACTS.md)
- Whole product: [../../PROJECT_BLUEPRINT.md](../../PROJECT_BLUEPRINT.md)

## Run and test on your own

```bash
pip install -e ".[dev]"                      # once, inside .venv
ruff check . && pytest                       # same checks CI runs
MG_ACCEPTED_KEYS="gateway:dev-key" uvicorn c1.main:create_app --factory --port 8001
```

Then open http://localhost:8001/docs, or call it with curl and the header `X-Service-Key: dev-key`.
Routes: `GET /health`, `POST /v1/analyze`, `POST /v1/reid`, `POST /v1/check`. Settings: `.env.example`.

## Run C1 with the C4 stub (block list and events)

`/v1/check` needs C4 for the block list, and sends one event to C4 per request. For
development, `stubs/c4_stub.py` stands in for C4 on port 8004. Use two terminals, both in
`components/c1-privacy-assistant`, with the `.venv` active:

```bash
# Terminal 1: the C4 stub (block list from stubs/blocklist.json; it checks every event)
MG_ACCEPTED_KEYS="c1:dev-c1-key" uvicorn stubs.c4_stub:app --port 8004

# Terminal 2: C1, presenting dev-c1-key to C4 and accepting dev-key from the gateway
MG_ACCEPTED_KEYS="gateway:dev-key" MG_SERVICE_KEY="dev-c1-key" MG_C4_URL="http://localhost:8004" \
  uvicorn c1.main:create_app --factory --port 8001
```

Check a prompt as the gateway would:

```bash
curl -s -X POST localhost:8001/v1/check -H "X-Service-Key: dev-key" -H "Content-Type: application/json" \
  -d '{"request_id":"req_20261023_000001","org_id":"org_001","user_id":"user_001","session_id":"sess_dev",
       "text":"Patient Nimal Perera NIC 199512345678, 94-year-old in Galle ward 3, review P0012"}'
```

You get `ALLOW` with `masked_text`, `placeholder_manifest`, `masking_percentage`, `entity_counts`
and `reid`. Add `"apply_reid_suggestions": false` to keep the age, ward and town as typed (names
and ids are masked either way, K2). Terminal 1 prints one line per event: its `event_id` and
`masking_percentage`, nothing else.

Things to try:

- **Blocked user:** send `"user_id":"user_017"`. The answer is `ACCESS_BLOCKED` with `masked_text: null`,
  and an event is still sent. Edit `stubs/blocklist.json` while both run; C1 picks it up within 30 s.
- **C4 down:** stop terminal 1. C1 keeps using its last good list for up to 300 s. After that, or if it
  never loaded one, `/v1/check` returns `503 {"code": "BLOCKLIST_UNAVAILABLE"}` and sends no event.
- **Events while C4 is down** are retried 3 times, then kept in the SQLite outbox (`c1.db`).
- `GET /health` shows `blocklist_cache_age_s`, the age of the oldest cached list.

`c1.db` holds the event outbox and the decision log (one row per `/v1/check`: ids, decision,
counts, percentages, latency; never text). It is gitignored. Set `MG_OUTBOX_PATH` to move it.

Temporary demo page (off by default, never enable it in production):

```bash
C1_DEMO=1 MG_ACCEPTED_KEYS="gateway:dev-key" uvicorn c1.main:create_app --factory --port 8001
# then open http://localhost:8001/demo
```

## Data and models

Only `manifest.json` files and small synthetic samples are committed. Weights and full datasets live on Drive or HF Hub (K29).

## Dataset (synthetic corpus)

```bash
python training/scripts/build_corpus.py --sample --manifest   # builds data/processed, samples, manifest
python training/scripts/validate_corpus.py                    # all checks
```

Dataset card: [data/README.md](data/README.md). Everything is synthetic, in the clinician's record layout.
