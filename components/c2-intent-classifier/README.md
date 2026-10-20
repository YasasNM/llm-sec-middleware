# C2 Semantic Intent Classifier

Owner: Nisith · Port: 8002

- Build blueprint: [BLUEPRINT.md](BLUEPRINT.md)
- Shared interfaces: [../../contracts/CONTRACTS.md](../../contracts/CONTRACTS.md)
- Whole product: [../../PROJECT_BLUEPRINT.md](../../PROJECT_BLUEPRINT.md)

## Run and test on your own

Fill this in when the service exists:

```bash
docker compose -f docker-compose.dev.yml up
pytest tests/unit tests/contract
```

## Data and models

Only `manifest.json` files and small synthetic samples are committed. Weights and full datasets live on Drive or HF Hub (K29).
