# MediGuard AI

A multi-layer security middleware framework for healthcare LLM applications (SLIIT IT4010, project J26-CS-321).

One repository holds the whole product: web app, gateway, four research components, shared library, infrastructure, evaluation and documentation.

## Start here

1. `PROJECT_BLUEPRINT.md`: the whole product, decisions and folder structure (Section 11).
2. `contracts/CONTRACTS.md`: the only shared interfaces.
3. Your component blueprint: `components/<your component>/BLUEPRINT.md`.
4. `docs/blueprints/README.md`: index of every blueprint.
5. `CONTRIBUTING.md`: branches, commits, pull requests.

## Layout

| Folder | What it holds |
|---|---|
| `apps/gateway`, `apps/web` | Backend and frontend of the product |
| `components/c1-privacy-assistant` ... `c4-risk-profiler` | The four research components (one owner each) |
| `packages/mediguard-common` | Shared Python library |
| `contracts/` | Schemas and examples (frozen at G2) |
| `llm/protected-llm` | The protected LLM stack for the onsite server |
| `infra/`, `tools/`, `scripts/` | Compose files, stubs, helper scripts |
| `evaluation/system` | Layered-defence ablation |
| `resources/` | Demo seed data, policies, prompts, branding |
| `docs/` | Blueprints, architecture, ADRs, reports, meetings, runbooks |

## Quick start

```bash
bash scripts/setup_dev.sh          # virtual environment, pre-commit, dependencies
make contracts                     # check the shared contracts
bash scripts/check_structure.sh    # check the folder structure
```

Synthetic data only. No real patient data, no secrets and no model weights in Git.
