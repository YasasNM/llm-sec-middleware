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
Routes so far: `GET /health`, `POST /v1/analyze`, `POST /v1/reid`. Settings: `.env.example`.

## Data and models

Only `manifest.json` files and small synthetic samples are committed. Weights and full datasets live on Drive or HF Hub (K29).

## Dataset (synthetic corpus)

```bash
python training/scripts/build_corpus.py --sample --manifest   # builds data/processed, samples, manifest
python training/scripts/validate_corpus.py                    # all checks
```

Dataset card: [data/README.md](data/README.md). Everything is synthetic, in the clinician's record layout.
