# Contributing

## Where you work
- You change files only in your own folder (see `.github/CODEOWNERS`). Another owner's folder needs a pull request they approve.
- Shared things have one home each: `contracts/`, `packages/`, `apps/gateway`, `apps/web`, `infra/`, `evaluation/system/`.

## Branches
`main` is protected. Work on a short-lived branch and open a pull request.

| Prefix | Use |
|---|---|
| `c1/` `c2/` `c3/` `c4/` | Component work, e.g. `c2/ensemble-threshold` |
| `gw/` `web/` | Gateway and web app |
| `infra/` `docs/` | Infrastructure and documentation |

## Commits
Conventional Commits with a scope: `feat(c3): add grounding verifier`, `fix(gateway): timeout on C2`, `data(c2): add paraphrase set`, `docs(c4): update rules table`.

## Pull requests
- Small, one topic.
- CI is green (lint, unit tests, contract tests).
- The `CODEOWNERS` owner of each touched path approves.
- Use the pull request template checklist.

## Changing a contract
1. Open a pull request that touches `contracts/` and bump `contracts/VERSION`.
2. Label it `contract-change`.
3. After G2 (Week 8), all four owners must approve. Say so in the description and tag them.

## Never commit
Secrets and `.env` files, model weights, raw or full datasets, real patient data, evaluation `results/`. Data and models are described by `manifest.json` files with SHA-256 hashes (K29).
