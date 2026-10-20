#!/usr/bin/env bash
# MediGuard AI: create the monorepo skeleton (PROJECT_BLUEPRINT.md Section 11).
#
# Run once from the repository root, after unzipping the starter pack into it:
#     bash scripts/bootstrap_repo.sh
#
# Safe to re-run: it creates what is missing and never overwrites an existing file.
# Optional: set the GitHub handles before running so CODEOWNERS is correct at once:
#     GH_LINUKA=@linuka GH_NISITH=@nisith GH_YASAS=@yasas GH_SANDUL=@sandul GH_GATEWAY=@linuka bash scripts/bootstrap_repo.sh

set -euo pipefail

if [ ! -f PROJECT_BLUEPRINT.md ] || [ ! -d contracts ]; then
  echo "Run this from the repository root after unzipping the starter pack (PROJECT_BLUEPRINT.md and contracts/ must exist)." >&2
  exit 1
fi

GH_LINUKA="${GH_LINUKA:-@REPLACE_LINUKA}"
GH_NISITH="${GH_NISITH:-@REPLACE_NISITH}"
GH_YASAS="${GH_YASAS:-@REPLACE_YASAS}"
GH_SANDUL="${GH_SANDUL:-@REPLACE_SANDUL}"
GH_GATEWAY="${GH_GATEWAY:-@REPLACE_GATEWAY_OWNER}"   # until the gateway owner is named, use one maintainer
GH_ALL="$GH_LINUKA $GH_NISITH $GH_YASAS $GH_SANDUL"

created_dirs=0
created_files=0

mk() {            # mk dir...   create directories, each with a .gitkeep so Git tracks them
  local d
  for d in "$@"; do
    if [ ! -d "$d" ]; then created_dirs=$((created_dirs + 1)); fi
    mkdir -p "$d"
    if [ -z "$(ls -A "$d")" ]; then : > "$d/.gitkeep"; fi
  done
}

put() {           # put path   write stdin to path only if the file does not exist
  local f="$1"
  mkdir -p "$(dirname "$f")"
  if [ -e "$f" ]; then cat > /dev/null; return 0; fi
  cat > "$f"
  created_files=$((created_files + 1))
}

# ---------------------------------------------------------------- 1. folders
# .github
mk .github/ISSUE_TEMPLATE .github/workflows

# shared library
mk packages/mediguard-common/src/mediguard_common packages/mediguard-common/tests

# apps/gateway (backend)
mk apps/gateway/src/gateway/{api,pipeline,clients,auth,db} apps/gateway/seed \
   apps/gateway/tests/{unit,integration,contract,security}

# apps/web (frontend)
mk apps/web/public apps/web/tests/{unit,e2e} \
   apps/web/src/{routes,api,auth,layouts,views,hooks,lib,styles} \
   apps/web/src/components/ui \
   apps/web/src/features/{chat,privacy,policies,risk,users,settings}

# components
mk components/c1-privacy-assistant/src/c1/{api,gate,detection,reid,masking,ocr,log} \
   components/c1-privacy-assistant/demo/falcon \
   components/c1-privacy-assistant/training/{notebooks,scripts,configs} \
   components/c1-privacy-assistant/data/samples components/c1-privacy-assistant/models \
   components/c1-privacy-assistant/{evaluation,stubs,configs} \
   components/c1-privacy-assistant/tests/{unit,integration,contract,security,performance}

mk components/c2-intent-classifier/src/c2/{api,embedding,similarity,classifier,ensemble,log} \
   components/c2-intent-classifier/training/{notebooks,scripts,configs} \
   components/c2-intent-classifier/scripts \
   components/c2-intent-classifier/data/samples components/c2-intent-classifier/models \
   components/c2-intent-classifier/{evaluation,stubs,configs} \
   components/c2-intent-classifier/tests/{unit,integration,contract,security,performance,fixtures}

mk components/c3-policy-scanner/src/c3/{api,policy,indexing,retrieval,generation,leakcheck,bench,storage} \
   components/c3-policy-scanner/training/notebooks \
   components/c3-policy-scanner/{baselines,tools,stubs,configs} \
   components/c3-policy-scanner/data/{policies,compliance,rubric} components/c3-policy-scanner/models \
   components/c3-policy-scanner/tests/{unit,integration,contract,security,performance,fixtures}

mk components/c4-risk-profiler/src/c4/{api,core,db} \
   components/c4-risk-profiler/{configs,scripts} \
   components/c4-risk-profiler/evaluation/scenarios \
   components/c4-risk-profiler/tests/{unit,integration,contract,security,performance}

# protected LLM (unzip protected-llm.zip contents here, see docs/runbooks/repo-setup.md)
mk llm/protected-llm/docs

# infra and tools
mk infra/{compose,nginx,mongo/init,env,tunnels,firewall,scripts} tools/stubs

# evaluation and resources
mk evaluation/system/testset/samples evaluation/system/results
mk resources/{seed,policies,prompts,branding}

# docs
mk docs/{architecture,adr,api,meetings,runbooks} docs/figures/{src,export}
for stage in proposal progress final; do
  for who in c1 c2 c3 c4 team; do mk "docs/reports/$stage/$who"; done
done

# repo level scripts
mk scripts

# ---------------------------------------------------------------- 2. root files
put README.md <<'EOF'
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
EOF

put CONTRIBUTING.md <<'EOF'
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
EOF

put CHANGELOG.md <<'EOF'
# Changelog

One entry per gate. Format: date, gate, what changed.

## Unreleased
- Repository created from the blueprint pack v3.1.
EOF

put contracts/CHANGELOG.md <<'EOF'
# Contracts changelog

| Version | Date | Change |
|---|---|---|
| 1.0.0-rc2 | 2026-10-20 | C3 scan request `categories` filter, per-stage timings in the scan response |
EOF

put .gitignore <<'EOF'
# secrets
.env
*.env
!*.env.example
*.pem
*.key
secrets/

# python
__pycache__/
*.py[cod]
.venv/
venv/
*.egg-info/
.pytest_cache/
.ruff_cache/
.mypy_cache/
.coverage
htmlcov/

# node
node_modules/
dist/
.vite/

# data and models (described by manifest.json, stored on Drive / HF Hub)
**/data/raw/
**/data/processed/
**/data/frozen_eval/
**/models/**/*.joblib
**/models/**/*.pt
**/models/**/*.pth
**/models/**/*.bin
**/models/**/*.safetensors
**/models/**/*.onnx
**/models/**/*.gguf
**/models/weights/
**/chroma/
**/chroma_data/
*.sqlite
*.sqlite3
*.db

# evaluation outputs (summaries go to docs/reports)
**/results/*
!**/results/.gitkeep

# notebooks and editors
.ipynb_checkpoints/
.idea/
.vscode/
.DS_Store
Thumbs.db
EOF

put .gitattributes <<'EOF'
# Same line endings on Windows, macOS and Linux (shell scripts must stay LF)
* text=auto eol=lf
*.png binary
*.jpg binary
*.pdf binary
*.ico binary
*.woff2 binary
EOF

put .editorconfig <<'EOF'
root = true

[*]
charset = utf-8
end_of_line = lf
insert_final_newline = true
trim_trailing_whitespace = true
indent_style = space
indent_size = 2

[*.py]
indent_size = 4

[Makefile]
indent_style = tab
EOF

put pyproject.toml <<'EOF'
# Shared tool settings for the whole repository (no packages are built from this file).
[tool.ruff]
line-length = 100
target-version = "py311"
extend-exclude = ["**/node_modules", "**/.venv"]

[tool.ruff.lint]
select = ["E", "F", "I", "B", "UP"]

[tool.pytest.ini_options]
addopts = "-q"
testpaths = ["packages", "apps/gateway/tests", "components"]
EOF

put .pre-commit-config.yaml <<'EOF'
repos:
  - repo: https://github.com/pre-commit/pre-commit-hooks
    rev: v4.6.0
    hooks:
      - id: end-of-file-fixer
      - id: trailing-whitespace
      - id: check-yaml
      - id: check-json
      - id: check-added-large-files
        args: ["--maxkb=1024"]
      - id: detect-private-key
  - repo: https://github.com/astral-sh/ruff-pre-commit
    rev: v0.6.9
    hooks:
      - id: ruff
      - id: ruff-format
EOF

put .env.example <<'EOF'
# Variables for infra/compose/docker-compose.yml. Copy to .env on the machine. Never commit .env.
# Real service keys are generated by infra/scripts/rotate_keys.sh.

# gateway
GW_JWT_SECRET=change-me
GW_PIPELINE_STAGES=c1,c2,c3
GW_MODE=prod
GW_LLM_API_URL=http://host.docker.internal:18010

# service keys (name:key pairs in MG_ACCEPTED_KEYS on each receiver)
MG_KEY_GATEWAY=change-me
MG_KEY_C1=change-me
MG_KEY_C2=change-me
MG_KEY_C3=change-me
MG_KEY_C4=change-me

# databases
MONGO_ROOT_PASSWORD=change-me
EOF

put Makefile <<'EOF'
.PHONY: help setup contracts structure lint test dev up down smoke eval models

help:
<TAB>@echo "make setup | contracts | structure | lint | test | dev | up | down | smoke | eval ARM=c1,c2 | models"

setup:
<TAB>bash scripts/setup_dev.sh

contracts:
<TAB>python contracts/validate.py

structure:
<TAB>bash scripts/check_structure.sh

lint:
<TAB>ruff check .

test:
<TAB>pytest

dev:
<TAB>docker compose -f infra/compose/docker-compose.dev.yml up --build

up:
<TAB>docker compose -f infra/compose/docker-compose.yml up -d --build

down:
<TAB>docker compose -f infra/compose/docker-compose.yml down

smoke:
<TAB>bash infra/scripts/smoke_test.sh

eval:
<TAB>python evaluation/system/runner.py --arm $(ARM)

models:
<TAB>python scripts/download_models.py
EOF
# real tabs in the Makefile (only if we just created it)
if grep -q '<TAB>' Makefile; then sed 's/<TAB>/\t/' Makefile; fi

put scripts/setup_dev.sh <<'EOF'
#!/usr/bin/env bash
# Prepare a development machine: virtual environment, tools, editable packages, web dependencies.
set -euo pipefail
cd "$(dirname "$0")/.."

python3 -m venv .venv
# shellcheck disable=SC1091
. .venv/bin/activate
python -m pip install --upgrade pip
pip install pre-commit ruff pytest jsonschema

# editable installs for every Python project that already has a pyproject.toml
for p in packages/mediguard-common apps/gateway components/c1-privacy-assistant \
         components/c2-intent-classifier components/c3-policy-scanner components/c4-risk-profiler; do
  if [ -f "$p/pyproject.toml" ]; then pip install -e "$p"; fi
done

pre-commit install

if [ -f apps/web/package.json ]; then
  (cd apps/web && corepack enable && pnpm install --frozen-lockfile)
fi

echo "Done. Activate with: . .venv/bin/activate"
EOF

put scripts/check_structure.sh <<'EOF'
#!/usr/bin/env bash
# Check the repository against PROJECT_BLUEPRINT.md Section 11. Exit code 1 if anything is wrong.
cd "$(dirname "$0")/.."
bad=0
need() { [ -e "$1" ] || { echo "MISSING  $1"; bad=1; }; }

for f in README.md PROJECT_BLUEPRINT.md CONTRIBUTING.md CHANGELOG.md Makefile pyproject.toml \
         .pre-commit-config.yaml .editorconfig .gitignore .gitattributes .env.example \
         .github/CODEOWNERS .github/pull_request_template.md .github/workflows/contracts.yml \
         contracts/CONTRACTS.md contracts/VERSION contracts/validate.py contracts/schemas contracts/examples \
         docs/blueprints/README.md docs/blueprints/GATEWAY_AND_WEB.md docs/blueprints/SHARED_LIBRARY.md \
         docs/blueprints/INFRASTRUCTURE_AND_DEVOPS.md docs/blueprints/SYSTEM_EVALUATION.md \
         docs/blueprints/PROTECTED_LLM.md docs/runbooks/repo-setup.md \
         packages/mediguard-common/src/mediguard_common apps/gateway/src/gateway apps/web/src \
         llm/protected-llm infra/compose infra/env tools/stubs evaluation/system resources/seed scripts; do
  need "$f"
done

for c in c1-privacy-assistant c2-intent-classifier c3-policy-scanner c4-risk-profiler; do
  for f in BLUEPRINT.md README.md src configs tests/unit tests/contract stubs; do
    # C4 has no stubs folder in its template (it uses scripts/event_generator.py)
    if [ "$c" = "c4-risk-profiler" ] && [ "$f" = "stubs" ]; then continue; fi
    need "components/$c/$f"
  done
done

# the blueprint must exist exactly once per component, and not also under docs/
for old in C1_PRIVACY_ASSISTANT C2_INTENT_CLASSIFIER C3_POLICY_SCANNER C4_RISK_PROFILER; do
  if [ -e "docs/blueprints/${old}_BLUEPRINT.md" ]; then echo "DUPLICATE docs/blueprints/${old}_BLUEPRINT.md (belongs in components/)"; bad=1; fi
done

# things that must never be in the tree
if find . -path ./.git -prune -o \( -name '.env' -o -name '*.safetensors' -o -name '*.joblib' -o -name '*.gguf' \) -print | grep -q .; then
  echo "FORBIDDEN secret or weight file found:"
  find . -path ./.git -prune -o \( -name '.env' -o -name '*.safetensors' -o -name '*.joblib' -o -name '*.gguf' \) -print
  bad=1
fi
if find . -path ./.git -prune -o -path ./apps/web/node_modules -prune -o -type f -size +5M -print | grep -q .; then
  echo "LARGE file (over 5 MB) found, use Drive or HF Hub and a manifest:"
  find . -path ./.git -prune -o -path ./apps/web/node_modules -prune -o -type f -size +5M -print
  bad=1
fi
if grep -rn "REPLACE_" .github/CODEOWNERS > /dev/null 2>&1; then
  echo "NOTE     .github/CODEOWNERS still has REPLACE_ placeholders (set real GitHub handles before enabling branch protection)"
fi

if [ "$bad" -eq 0 ]; then echo "Structure OK"; else echo "Structure has problems (see above)"; fi
exit $bad
EOF

put .github/CODEOWNERS <<EOF
# Last matching pattern wins. Replace the REPLACE_ handles with real GitHub usernames.
# Component owners
/components/c1-privacy-assistant/        $GH_LINUKA
/components/c2-intent-classifier/        $GH_NISITH
/components/c3-policy-scanner/           $GH_YASAS
/components/c4-risk-profiler/            $GH_SANDUL
/llm/protected-llm/                      $GH_LINUKA

# Product shell: gateway owner (to be named, Open Item 1)
/apps/gateway/                           $GH_GATEWAY
/apps/web/                               $GH_GATEWAY
/packages/                               $GH_GATEWAY
/infra/                                  $GH_GATEWAY
/tools/                                  $GH_GATEWAY
/scripts/                                $GH_GATEWAY
/.github/                                $GH_GATEWAY
/Makefile                                $GH_GATEWAY
/pyproject.toml                          $GH_GATEWAY
/.pre-commit-config.yaml                 $GH_GATEWAY

# Web feature folders belong to the component owners (they override the line above)
/apps/web/src/features/privacy/          $GH_LINUKA
/apps/web/src/features/policies/         $GH_YASAS
/apps/web/src/features/risk/             $GH_SANDUL

# Shared by all four (any one of them can approve; contract changes also follow CONTRIBUTING.md)
/contracts/                              $GH_ALL
/evaluation/system/                      $GH_ALL
/resources/                              $GH_ALL
/docs/                                   $GH_ALL
/PROJECT_BLUEPRINT.md                    $GH_ALL
/README.md                               $GH_ALL
/CONTRIBUTING.md                         $GH_ALL
/CHANGELOG.md                            $GH_ALL
EOF

put .github/pull_request_template.md <<'EOF'
## What changed
<!-- One or two sentences. Which component or part? -->

## Checklist
- [ ] I only changed files in my own folder (or the owner is a reviewer)
- [ ] Tests pass locally (`pytest tests/unit tests/contract` in my component)
- [ ] Requests, responses and events still validate against `contracts/`
- [ ] No secrets, no `.env`, no model weights, no real patient data
- [ ] Logs and tests contain no prompt or answer text
- [ ] Documentation updated (`BLUEPRINT.md` or `README.md`) if behaviour changed
- [ ] If `contracts/` changed: label `contract-change`, `contracts/VERSION` bumped, all four owners tagged
EOF

put .github/ISSUE_TEMPLATE/task.md <<'EOF'
---
name: Task
about: A piece of planned work
labels: task
---

**Component / part:** (c1, c2, c3, c4, gateway, web, infra, docs)
**Requirement or blueprint section:**
**Gate:** (G1 to G5, or none)

## Done when
-
EOF

put .github/ISSUE_TEMPLATE/bug.md <<'EOF'
---
name: Bug
about: Something does not work as the blueprint says
labels: bug
---

**Component / part:**
**What happened:**
**What should happen (blueprint section):**
**How to reproduce (use synthetic data only, never real patient text):**
EOF

put .github/ISSUE_TEMPLATE/decision.md <<'EOF'
---
name: Decision
about: A question the team has to answer
labels: decision
---

**Question:**
**Options and cost of each:**
**Needed by (week or gate):**
**Who decides:**
EOF

put .github/dependabot.yml <<'EOF'
version: 2
updates:
  - package-ecosystem: "github-actions"
    directory: "/"
    schedule:
      interval: "monthly"
EOF

put .github/workflows/contracts.yml <<'EOF'
name: contracts

on:
  pull_request:
    paths:
      - "contracts/**"
      - ".github/workflows/contracts.yml"
  push:
    branches: [main]
    paths:
      - "contracts/**"

jobs:
  validate:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.11"
      - run: pip install jsonschema
      - run: python contracts/validate.py
EOF

# ---------------------------------------------------------------- 3. component README stubs
component_readme() {   # component_readme folder "Title" owner port
  put "components/$1/README.md" <<EOF
# $2

Owner: $3 · Port: $4

- Build blueprint: [BLUEPRINT.md](BLUEPRINT.md)
- Shared interfaces: [../../contracts/CONTRACTS.md](../../contracts/CONTRACTS.md)
- Whole product: [../../PROJECT_BLUEPRINT.md](../../PROJECT_BLUEPRINT.md)

## Run and test on your own

Fill this in when the service exists:

\`\`\`bash
docker compose -f docker-compose.dev.yml up
pytest tests/unit tests/contract
\`\`\`

## Data and models

Only \`manifest.json\` files and small synthetic samples are committed. Weights and full datasets live on Drive or HF Hub (K29).
EOF
}
component_readme c1-privacy-assistant "C1 Real-Time Healthcare Privacy Assistant" "Linuka" 8001
component_readme c2-intent-classifier "C2 Semantic Intent Classifier" "Nisith" 8002
component_readme c3-policy-scanner "C3 RAG Output Policy Scanner" "Yasas" 8003
component_readme c4-risk-profiler "C4 Heuristic Cross-Session Risk Profiler" "Sandul" 8004

put apps/gateway/README.md <<'EOF'
# Gateway (backend)

Port 8000. Blueprint: [../../docs/blueprints/GATEWAY_AND_WEB.md](../../docs/blueprints/GATEWAY_AND_WEB.md)
EOF
put apps/web/README.md <<'EOF'
# Web app (frontend)

React 19, TypeScript, Vite, Tailwind v4. Talks only to the gateway.
Blueprint: [../../docs/blueprints/GATEWAY_AND_WEB.md](../../docs/blueprints/GATEWAY_AND_WEB.md)

Feature folders under `src/features/` belong to the component owners (see `.github/CODEOWNERS`).
EOF
put packages/mediguard-common/README.md <<'EOF'
# mediguard-common

Shared library: service auth, event sender with SQLite outbox, ids, no-text logging, contract helpers.
Blueprint: [../../docs/blueprints/SHARED_LIBRARY.md](../../docs/blueprints/SHARED_LIBRARY.md)
EOF
put infra/compose/README.md <<'EOF'
# Compose files

| File | Use |
|---|---|
| `docker-compose.dev.yml` | Whole product on a laptop with stubs and the mock LLM |
| `docker-compose.yml` | Integration and demo on the middleware host |
| `docker-compose.eval.yml` | System evaluation arms |

Blueprint: [../../docs/blueprints/INFRASTRUCTURE_AND_DEVOPS.md](../../docs/blueprints/INFRASTRUCTURE_AND_DEVOPS.md)
EOF
put evaluation/system/README.md <<'EOF'
# System evaluation

Layered-defence ablation. Blueprint: [../../docs/blueprints/SYSTEM_EVALUATION.md](../../docs/blueprints/SYSTEM_EVALUATION.md)
EOF
put evaluation/system/arms.yaml <<'EOF'
arms:
  A0: "none"
  A1: "c1"
  A2: "c1,c2"
  A3: "c1,c2,c3"
EOF
put llm/protected-llm/README.md <<'EOF'
# Protected LLM

Replace this folder's content with the contents of protected-llm.zip (see docs/runbooks/repo-setup.md).
Blueprint: [../../docs/blueprints/PROTECTED_LLM.md](../../docs/blueprints/PROTECTED_LLM.md)
EOF

echo "Bootstrap finished: $created_dirs new folders, $created_files new files."
echo "Next: bash scripts/check_structure.sh"
