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
