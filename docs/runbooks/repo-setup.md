# Creating the MediGuard Repository: Step by Step

**Status:** done. The repository skeleton is in place. Do not recreate it. `scripts/bootstrap_repo.sh` has been removed.

**Who did it:** one person, once (the gateway/infra maintainer, or Linuka until that role is named).
**Result:** one private GitHub repository holding frontend, backend, the four components, infrastructure, evaluation and all documentation, with each owner's folder protected by `CODEOWNERS`.
**Reference:** `PROJECT_BLUEPRINT.md` Section 11 is the specification. This file is the record of how the repository was built.

---

## 1. The finished structure

```text
mediguard/
├── README.md                         project front page (created by the script)
├── PROJECT_BLUEPRINT.md              the whole product (root, so everyone finds it first)
├── CONTRIBUTING.md  CHANGELOG.md  Makefile  pyproject.toml  .pre-commit-config.yaml
├── .editorconfig  .gitignore  .gitattributes  .env.example
│
├── .github/                          CODEOWNERS, PR template, issue templates, workflows/
├── contracts/                        CONTRACTS.md, schemas/, examples/, validate.py   (shared by all)
├── packages/mediguard-common/        shared Python library
│
├── apps/
│   ├── gateway/                      BACKEND  (FastAPI, port 8000)
│   └── web/                          FRONTEND (React + Vite)
│
├── components/
│   ├── c1-privacy-assistant/         BLUEPRINT.md  README.md  src/  training/  data/  models/  tests/ ...
│   ├── c2-intent-classifier/         BLUEPRINT.md  ...
│   ├── c3-policy-scanner/            BLUEPRINT.md  ...
│   └── c4-risk-profiler/             BLUEPRINT.md  ...
│
├── llm/protected-llm/                the LLM stack for the onsite Ubuntu server
├── infra/                            compose/, nginx/, mongo/, env/, tunnels/, firewall/, scripts/
├── tools/stubs/                      fake C1..C4 and fake LLM for development
├── evaluation/system/                layered-defence ablation
├── resources/                        seed/, policies/, prompts/, branding/
├── docs/
│   ├── blueprints/                   README.md (index) + the 5 shared-part blueprints
│   ├── architecture/  adr/  api/  figures/  reports/  meetings/
│   └── runbooks/                     repo-setup.md (this file), deploy, demo-day, incident ...
└── scripts/                          check_structure.sh, setup_dev.sh ...
```

**Frontend and backend are in the same repository** under `apps/`. Components are not in `apps/` because they are research modules with their own training and evaluation folders.

---

## 2. Where every Markdown file goes

One rule: **a blueprint sits next to what it describes. Anything shared sits in `docs/`. The product blueprint sits at the root.**

| File (as in the starter pack) | Goes to | Why |
|---|---|---|
| `PROJECT_BLUEPRINT.md` | `PROJECT_BLUEPRINT.md` (root) | The first file anyone should read; covers all parts |
| `C1_PRIVACY_ASSISTANT_BLUEPRINT.md` | `components/c1-privacy-assistant/BLUEPRINT.md` | Owner edits it in the same pull request as the code; `CODEOWNERS` covers it |
| `C2_INTENT_CLASSIFIER_BLUEPRINT.md` | `components/c2-intent-classifier/BLUEPRINT.md` | Same |
| `C3_POLICY_SCANNER_BLUEPRINT.md` | `components/c3-policy-scanner/BLUEPRINT.md` | Same |
| `C4_RISK_PROFILER_BLUEPRINT.md` | `components/c4-risk-profiler/BLUEPRINT.md` | Same |
| `GATEWAY_AND_WEB.md` | `docs/blueprints/GATEWAY_AND_WEB.md` | Covers two folders (`apps/gateway`, `apps/web`), so it has no single home |
| `SHARED_LIBRARY.md` | `docs/blueprints/SHARED_LIBRARY.md` | Shared by every service |
| `INFRASTRUCTURE_AND_DEVOPS.md` | `docs/blueprints/INFRASTRUCTURE_AND_DEVOPS.md` | Covers `infra/`, `.github/`, `scripts/` |
| `SYSTEM_EVALUATION.md` | `docs/blueprints/SYSTEM_EVALUATION.md` | Shared by all four owners |
| `PROTECTED_LLM.md` | `docs/blueprints/PROTECTED_LLM.md` | Deployment and hardening notes |
| `README.md` (pack index) | `docs/blueprints/README.md` | It is the index of blueprints; the repo root gets a new project `README.md` |
| `contracts/CONTRACTS.md` and the rest of `contracts/` | `contracts/` | Unchanged |
| this file | `docs/runbooks/repo-setup.md` | A runbook |
| `ENDPOINTS.md` (earlier LLM deliverable) | `llm/protected-llm/docs/API.md` | The LLM's own API reference, next to the LLM |
| `DEPLOYMENT_GUIDE.md` (earlier LLM deliverable) | `llm/protected-llm/docs/DEPLOYMENT.md` | Same |
| Sandul's C4 event email (as text) | `docs/meetings/2026-10-c4-event-agreement.md` | Decision record |
| Proposal and progress PDFs | `docs/reports/proposal/<c1..c4,team>/` | Report folders are per component |
| Per-component how-to-run notes | `components/cN-*/README.md` | Short; the blueprint holds the design |

**Do not commit** (superseded, kept in your own archive): `C1_Technical_Context_and_Implementation_Guide.md`, `MediGuard_Blueprint_Integration_Analysis.md`, the v2 pack, and the old `mediguard.zip` stack. Git history is the archive from now on.

---

## 3. Steps

### Step 1. Create the empty repository on GitHub

1. GitHub, **New repository**. Owner: your GitHub organisation if the team has one, otherwise your account.
2. Name `mediguard`, visibility **Private**.
3. Leave **all** options off (no README, no .gitignore, no licence). The script creates them.
4. **Settings, Collaborators:** add Nisith, Yasas and Sandul with the **Write** role. Add the supervisors with **Read**.
5. **Settings, General, Pull Requests:** allow squash merging, turn on "Automatically delete head branches".

### Step 2. Clone it

```bash
git clone git@github.com:<owner>/mediguard.git
cd mediguard
git checkout -b main        # only if the clone is empty and has no branch yet
```

Windows: do Steps 2 to 7 in WSL or Git Bash. The scripts are Bash.

### Step 3. Unzip the starter pack into the repository root

```bash
unzip -o ~/Downloads/MediGuard_Repo_Starter.zip -d .
ls            # PROJECT_BLUEPRINT.md  contracts  components  docs  scripts
```

You should now see the four `BLUEPRINT.md` files inside `components/`, the five shared blueprints in `docs/blueprints/`, `contracts/` and `scripts/`.

### Step 4. Bring in the material you already have

```bash
# the protected LLM stack (the folder inside the zip is called protected-llm/)
unzip -o ~/Downloads/protected-llm.zip -d /tmp/pl
mkdir -p llm/protected-llm/docs
cp -r /tmp/pl/protected-llm/. llm/protected-llm/
cp ~/Downloads/ENDPOINTS.md        llm/protected-llm/docs/API.md
cp ~/Downloads/DEPLOYMENT_GUIDE.md llm/protected-llm/docs/DEPLOYMENT.md

# the React prototype (MediGuardAI/) becomes the frontend
rsync -a --exclude node_modules --exclude .git --exclude dist ~/path/to/MediGuardAI/ apps/web/
```

Check after copying:
- `llm/protected-llm/docker-compose.yml` sits **directly** in that folder (not in a nested `protected-llm/`).
- `apps/web/package.json` sits directly in `apps/web/`.
- Do not copy any `.env`, `node_modules/` or model weights. `.gitignore` blocks them, but check anyway.

Linuka's existing C1 code and Falcon demo go in later, by Linuka, on a `c1/import-existing` branch: code to `components/c1-privacy-assistant/src/c1/`, the standalone UI to `components/c1-privacy-assistant/demo/falcon/`.

### Step 5. Bootstrap (already completed)

The skeleton, `.github/` files, component README stubs and helper scripts were created once. `scripts/bootstrap_repo.sh` is no longer in the tree. Do not try to run it.

If `CODEOWNERS` still has `REPLACE_` placeholders, put the real GitHub handles in `.github/CODEOWNERS` now.

### Step 6. Check the result

```bash
bash scripts/check_structure.sh     # prints "Structure OK" or lists what is missing
pip install jsonschema && python contracts/validate.py     # "all good"
git status --short | head -30
```

If `check_structure.sh` says `NOTE ... REPLACE_`, fix the handles in `.github/CODEOWNERS` now.

### Step 7. First commits and push

Several small commits make the history readable. Use the commit style from `CONTRIBUTING.md`.

```bash
git add .gitignore .gitattributes .editorconfig .pre-commit-config.yaml pyproject.toml Makefile \
        .env.example CONTRIBUTING.md CHANGELOG.md README.md .github scripts
git commit -m "chore: repository skeleton, tooling and collaboration files"

git add contracts
git commit -m "feat(contracts): contracts v1.0.0-rc2 with schemas and examples"

git add PROJECT_BLUEPRINT.md docs
git commit -m "docs: product blueprint, shared blueprints and runbooks"

git add components
git commit -m "docs(components): component blueprints and folder templates"

git add llm
git commit -m "feat(llm): protected LLM stack for the onsite server"

git add apps packages infra tools evaluation resources
git commit -m "feat: gateway, web, shared library, infra, evaluation and resource folders"

git push -u origin main
git tag pack-v3.1 && git push origin pack-v3.1
```

### Step 8. Protect `main` (after the first push)

GitHub, **Settings, Rules, Rulesets, New branch ruleset**, target `main`:

| Setting | Value |
|---|---|
| Require a pull request before merging | on, 1 approval |
| Require review from Code Owners | **on** (this is what makes each owner the reviewer of their folder) |
| Dismiss stale approvals when new commits are pushed | on |
| Require status checks to pass | add `contracts / validate` now; add the component, gateway and web checks when those workflows exist (Week 3 to 5) |
| Block force pushes and deletions | on |
| Require conversation resolution | on |

Notes:
- Branch protection on a **private** repository needs a paid GitHub plan or GitHub Education/Student benefits. If the account cannot, ask the supervisors for a university organisation, or follow the rules by convention and keep CI as a warning.
- `CODEOWNERS` lists all four owners on `contracts/`, and GitHub accepts any one of them. The rule "all four approve after G2" is enforced by the `contract-change` label and the pull request template, not by GitHub. Tag all four in the pull request.
- Create labels once: `contract-change`, `c1`, `c2`, `c3`, `c4`, `gateway`, `web`, `infra`, `docs`, `decision`.

### Step 9. Tell each owner what to do

Send this to the team:

```bash
git clone git@github.com:<owner>/mediguard.git && cd mediguard
bash scripts/setup_dev.sh                       # WSL/Linux/macOS
git checkout -b c2/setup                        # c1/..., c3/..., c4/..., gw/..., web/...
cd components/c2-intent-classifier              # your folder
# read BLUEPRINT.md, build, test, then:
git add . && git commit -m "feat(c2): first working /v1/classify" && git push -u origin c2/setup
# open a pull request; your CODEOWNERS entry makes you the required reviewer
```

Everyone works only inside their own folder. The blueprint's own week plan says what to build first.

---

## 4. Rules that keep it conflict-free

| Rule | Why it prevents conflicts |
|---|---|
| One folder per owner, enforced by `CODEOWNERS` | Two people almost never edit the same file |
| Every shared thing has exactly one home (`contracts/`, `packages/`, `apps/gateway`, `apps/web`, `infra/`) | No duplicate versions of a shared file |
| Component blueprint lives only in its component folder (no copy in `docs/`) | One source of truth; `check_structure.sh` fails if a duplicate appears in `docs/blueprints/` |
| Web feature folders have owners (`privacy` Linuka, `policies` Yasas, `risk` Sandul) | Frontend work of different people never meets in one file |
| Lock files (`pnpm-lock.yaml`) are changed only by the person adding a dependency, in `apps/web` | Avoids lock file merge fights |
| `.gitattributes` forces LF line endings | Windows and Linux teammates do not rewrite every line of each other's files |
| Data and weights are never committed, only `manifest.json` | Repository stays small; no merge of binaries |
| Contract changes need a pull request labelled `contract-change` | Interfaces never move silently |
| Short branches, squash merge, `main` protected | Small diffs, linear history |

Two people sometimes need the same file (for example `apps/web/src/App.tsx` for routes). Make that change in a tiny pull request of its own and merge it first.

---

## 5. After the repository exists

| When | Task | Owner |
|---|---|---|
| Week 3 | `packages/mediguard-common` skeleton, `ci-components.yml`, `.github` workflows from `INFRASTRUCTURE_AND_DEVOPS.md` | Gateway/infra owner |
| Week 3 to 5 | Each owner adds `pyproject.toml`, `Dockerfile`, `docker-compose.dev.yml`, `.env.example`, `/health` and contract tests to their component | Component owners |
| Week 4 to 5 | `tools/stubs/*`, `infra/compose/docker-compose.dev.yml` | Gateway/infra owner |
| Week 5 | Name the gateway/infra owner (Open Item 1) and update `CODEOWNERS` | Whole team |
| Week 8 | Tag `contracts-v1.0.0` (G2) | All |

`bash scripts/check_structure.sh` can run in CI as a cheap guard against a folder or blueprint going missing.

---

## 6. Troubleshooting

| Problem | Fix |
|---|---|
| `check_structure.sh` says `DUPLICATE docs/blueprints/C?_...` | Delete the duplicate; the blueprint belongs in `components/cN-*/BLUEPRINT.md` |
| Empty folders do not show in GitHub | They need a file; the script adds `.gitkeep`. Re-run the script |
| `bash: $'\r': command not found` | Windows line endings. Run `git config core.autocrlf false`, delete the clone and clone again (`.gitattributes` fixes it afterwards) |
| CODEOWNERS shows errors in GitHub | A handle is wrong or the user has no Write access. Fix the username in `.github/CODEOWNERS` |
| A push is rejected for a large file | Do not commit it. Put it on Drive or HF Hub and add a `manifest.json` entry (K29). `git rm --cached <file>` and amend |
| Someone committed `.env` | Rotate every key in it (`infra/scripts/rotate_keys.sh`), then remove it from history with `git filter-repo` |
