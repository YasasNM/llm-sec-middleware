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
