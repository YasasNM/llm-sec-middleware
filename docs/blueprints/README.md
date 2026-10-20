# MediGuard AI (J26-CS-321): Blueprint Index

All blueprints are conflict-free across components. They apply:
- the team's answers on K1 to K30;
- the C4 event agreement (email and field tables);
- the onsite protected LLM deployment (`qwen2.5:7b`).

Each component blueprint is the **updated version of its owner's own document**. The research content is kept, and each one lists what changed.

## Where each blueprint lives

The rule: a blueprint that describes one component sits next to that component's code. Blueprints for shared parts stay here.

| Blueprint | Path | Owner | Status |
|---|---|---|---|
| Product blueprint | `PROJECT_BLUEPRINT.md` (repo root) | all | Whole product, decisions K1 to K30 and IR1 to IR15, folder structure (Section 11) |
| Interfaces | `contracts/CONTRACTS.md` | all | The only shared interfaces (v1.0.0-rc2) |
| C1 Privacy Assistant | `components/c1-privacy-assistant/BLUEPRINT.md` | Linuka | Rewritten, integration-aligned |
| C2 Intent Classifier | `components/c2-intent-classifier/BLUEPRINT.md` | Nisith | Updated in place, changes in §32 |
| C3 Policy Scanner | `components/c3-policy-scanner/BLUEPRINT.md` | Yasas | v4, changes in Part 0.6 |
| C4 Risk Profiler | `components/c4-risk-profiler/BLUEPRINT.md` | Sandul | Updated in place, changes in §2 (includes the risk admin UI) |
| Gateway and web | `docs/blueprints/GATEWAY_AND_WEB.md` | to be named | Gateway backend and web app shell |
| Shared library | `docs/blueprints/SHARED_LIBRARY.md` | to be named | `packages/mediguard-common` and shared stubs |
| Infrastructure and DevOps | `docs/blueprints/INFRASTRUCTURE_AND_DEVOPS.md` | to be named + Linuka | Compose, environments, secrets, CI/CD, operations |
| System evaluation | `docs/blueprints/SYSTEM_EVALUATION.md` | all | Layered-defence ablation |
| Protected LLM | `docs/blueprints/PROTECTED_LLM.md` | Linuka (deployed) | Access now, hardening at Week 14 |
| Repository setup | `docs/runbooks/repo-setup.md` | gateway owner | How this repository is created |

## Reading order

1. `PROJECT_BLUEPRINT.md`
2. `contracts/CONTRACTS.md`
3. Your own component blueprint (table above)
4. The shared-part blueprints that touch your work (gateway and web, shared library, infrastructure)

## Contracts check

```bash
pip install jsonschema
python contracts/validate.py                          # 13 valid samples pass, 7 invalid samples rejected
python contracts/validate.py c2_event my_event.json   # check one payload of yours
```

## Still open

1. Owner of the gateway, web shell, shared library, infra and CI (Week 5).
2. K28: placeholders in C2 training (Nisith, Week 12).
3. Calendar dates for the 24 weeks (next meeting).
4. Middleware host: separate machine or the onsite server (Week 14).
