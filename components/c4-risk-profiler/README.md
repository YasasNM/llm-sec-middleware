# C4 Heuristic Cross-Session Risk Profiler

Owner: Sandul · Port: 8004

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
