# Gateway and Web App: Build Blueprint

**Owner:** to be named by the team (product blueprint Section 18, item 1). Each component owner reviews the gateway code that calls their service and owns their web feature folder.
**Paths:** `apps/gateway/` (backend), `apps/web/` (frontend), plus shared ownership of `packages/mediguard-common/`, `infra/`, `.github/` (see `PROJECT_BLUEPRINT.md` §11 for the full tree)
**Ports:** gateway 8000 (only service reachable by browsers), web dev 5173

**One line:** the gateway logs users in, creates `session_id` and `request_id`, and runs C1 → C2 → protected LLM → C3 with fail-closed handling and an ablation switch. It returns one chat message and protects the admin screens.

---

## 1. Decisions That Bind the Gateway

| Ref | Meaning |
|---|---|
| K4 | Login, JWT and roles live here: `org_admin`, `staff_admin`, `clinical` |
| K5 | The gateway calls each component in turn. Components never call each other |
| K6 | Gateway creates `session_id` and `request_id`. `request_id` is the clinician's reference |
| K9 | The gateway sends **no** events to C4 |
| K16, IR13 | Calls the onsite protected LLM `/v1/chat` with masked text and masked history only |
| K18, K19, K20 | One React app, friendly labels, three user message codes |
| K21 | Phrase Manager screen deleted |
| K23 | `X-Service-Key` on every internal call (shared `mediguard-common`: `MG_SERVICE_NAME=gateway`, `MG_SERVICE_KEY`) |
| K27 | `GW_PIPELINE_STAGES` ablation switch + system evaluation runner |
| IR10 | The protected LLM's own UI is not the product UI |
| IR15 | Any fail-closed stop returns `OUTPUT_WITHHELD` |

---

## 2. Gateway API (browser-facing)

| Method | Path | Role | Does |
|---|---|---|---|
| POST | `/api/auth/login` | public | `{email, password}` → JWT (60 min) + user (id, name, role, org) |
| GET | `/api/me` | any | Current user |
| POST | `/api/chat/session` | clinical | New `session_id` ("New chat") |
| POST | `/api/chat/analyze` | clinical | Live typing → C1 `/v1/analyze` + `/v1/reid` merged (debounced client-side, 350 ms) |
| POST | `/api/chat/ocr` | clinical | Upload → C1 `/v1/ocr` |
| POST | `/api/chat/submit` | clinical | `{session_id, text, apply_reid_suggestions}` → `chat_message` |
| GET | `/api/admin/risk/users` | admins | → C4 `/v1/users` (+ joins name, department, role) |
| GET | `/api/admin/risk/users/{id}` | admins | → C4 profile |
| GET | `/api/admin/risk/users/{id}/timeline` | admins | → C4 timeline |
| POST | `/api/admin/risk/users/{id}/block`, `/unblock` | `org_admin`, `staff_admin` | → C4 with `by` = admin id |
| GET/POST/PUT/DELETE | `/api/admin/policies...` | admins | → C3 policy API for the admin's org |
| GET | `/api/admin/requests/{request_id}` | admins | Request trace: stage outcomes, codes, C3 clause (from C3's verdict kept in the gateway's request log) |
| CRUD | `/api/admin/users...` | `org_admin` | User management (gateway `users` collection) |
| GET | `/health` | public | Status of C1 to C4, LLM API, Mongo, active pipeline |

---

## 3. Submit Pipeline

```python
# apps/gateway/src/gateway/pipeline/orchestrator.py
async def submit(user, session, text, apply_reid):
    rid = new_request_id()                                   # req_<…>
    ids = dict(request_id=rid, org_id=user.org_id, user_id=user.id, session_id=session.id)
    try:
        prompt, manifest, masked_pct = text, [], 0.0                 # arm "none" (evaluation only)
        if "c1" in STAGES:
            c1 = await call("c1", "/v1/check", {**ids, "text": text, "apply_reid_suggestions": apply_reid}, timeout=2)
            if c1["decision"] == "ACCESS_BLOCKED": return blocked(rid, "ACCESS_BLOCKED")
            prompt, manifest, masked_pct = c1["masked_text"], c1["placeholder_manifest"], c1["masking_percentage"]
        if "c2" in STAGES:
            c2 = await call("c2", "/v1/classify", {**ids, "prompt": prompt, "c1_metadata": meta(c1) if "c1" in STAGES else {}}, timeout=2)
            if c2["c2_verdict"] == "BLOCK": return blocked(rid, "REQUEST_BLOCKED")
        llm = await call("llm", "/v1/chat", {"message": prompt, "history": session.masked_history[-6:], "request_id": rid}, timeout=300)
        answer = llm["answer"]
        if "c3" in STAGES:
            c3 = await call("c3", "/v1/scan", {**ids, "response_text": answer, "placeholder_manifest": manifest}, timeout=5)
            if c3["c3_verdict"] == "VIOLATION": return blocked(rid, "OUTPUT_WITHHELD", admin_detail=c3)
            answer = c3["clean_response"]
        session.add_masked_turns(prompt, answer)
        return message(rid, answer, "pii-masked" if masked_pct > 0 else "normal")
    except (Timeout, ConnectionError, HTTPStatusError):
        return blocked(rid, "OUTPUT_WITHHELD")               # fail closed
```

- `STAGES` comes from `GW_PIPELINE_STAGES`, which takes one of `none`, `c1`, `c1,c2` or `c1,c2,c3`. `none` sends raw text straight to the LLM and is for evaluation only; the gateway refuses any arm other than `c1,c2,c3` unless `GW_MODE=eval`.
- Each request writes one line to the gateway request log (Mongo `requests`):
  - `request_id`, `org_id`, `user_id`, `session_id`
  - stage outcomes and latencies
  - final type and code
  - C3 `clause_id` and `rationale` for admins

  No prompt or answer text.
- Masked history holds the last 6 turns per session, masked text only.

---

## 4. Data (MongoDB database `gateway`)

| Collection | Fields |
|---|---|
| `orgs` | `org_id`, name, slug |
| `users` | `user_id`, `org_id`, email, name, department, role, argon2 hash, active |
| `sessions` | `session_id`, `user_id`, `org_id`, created, last_active, `masked_history[]` (≤ 6 turns) |
| `requests` | Section 3 request log |

Seed: one demo org (`org_001`), users for each role, Sri Lankan demo names (synthetic).

---

## 5. Web App (`apps/web`, from the prototype)

| Area | Owner | Notes |
|---|---|---|
| Shell, routing, login, onboarding (`views/`, `layouts/`, `routes/`, `auth/`) | Gateway owner | Real login against `/api/auth/login`; role from JWT |
| `features/chat` | Gateway owner | Message list, composer, blocked cards, "Reference: req_..." |
| `features/users`, `features/settings` | Gateway owner | User management; org settings; read-only onsite LLM status (replaces the prototype's AI Configuration editor) |
| `features/privacy` (Clinical view) | Linuka | Live underline (red = explicit, will be masked; orange = re-id with suggestion), tooltip, one-click apply, toggle for re-id suggestions. Prototype UK regex deleted |
| `features/policies` | Yasas | Upload, replace, delete, status, clause count, category. Copy text: "used to check AI answers" |
| `features/risk` | Sandul | Dashboard, managed users, profile, timeline, block/unblock, action log |
| `api/` client | Gateway owner | One typed client generated from the gateway OpenAPI |

Removed: Phrase Manager / Phrase Database (K21), any "Source: C1/C2/C3" text (K19).

---

## 6. Configuration

| Env var | Example |
|---|---|
| `JWT_SECRET`, `JWT_TTL_MIN` | random 64 bytes, 60 |
| `MG_SERVICE_NAME`, `MG_SERVICE_KEY` | `gateway`, key presented to C1 to C4 |
| `GW_C1_URL` … `GW_C4_URL` | `http://c1:8001` … `http://c4:8004` |
| `GW_LLM_API_URL`, `GW_LLM_API_KEY` | `http://host.docker.internal:18010` (SSH tunnel, see `PROTECTED_LLM.md` §4) or `http://<llm-server-lan-ip>:8010`; the LLM server's rotated `SERVICE_KEY` |
| `GW_PIPELINE_STAGES` | `c1,c2,c3` (`none`, `c1`, `c1,c2` only in `GW_MODE=eval`) |
| `GW_MONGO_URL` | `mongodb://…/gateway` |
| `GW_CORS_ORIGINS` | web app origin only |
| `GW_MODE` | `prod` or `eval` |

---

## 7. Integrated Compose (`infra/compose/docker-compose.yml`, middleware host)

Services:

| Service | Source |
|---|---|
| `gateway` | built here |
| `web` | static build served by nginx, proxying `/api` to the gateway |
| `c1`, `c2`, `c3`, `c4` | built from `components/cN/Dockerfile` (context = repo root), tagged at G3 and G5 |
| `mongo` | MongoDB 7 |

The LLM is external (onsite server), reached through `infra/tunnels/autossh-llm.service` or a filtered LAN bind (`PROTECTED_LLM.md` §4).

- Only `web` (80/443) is published. Everything else is on the internal network.
- Volumes: `mongo_data`, `c2_chroma`, `c3_chroma`, `c3_policies`, `c3_models`, `c2_models`, `c1_models`, and `cN_db` (SQLite) for each of C1 to C3.

---

## 8. Week-by-Week Plan

| Week | Tasks | Done when |
|---|---|---|
| 3 to 5 | Monorepo skeleton (Section 11 of the product blueprint); `packages/mediguard-common`; `tools/stubs/` for C1 to C4 that pass contract tests; gateway skeleton with login + submit against stubs + real LLM; CI workflows | Chat works end to end with stubs |
| 8 (G2) | Contracts signed and tagged | Tag `contracts-v1.0.0` |
| 9 to 10 (G3) | Swap stubs for real services as each MVP lands; fail-closed tests | Chain demo with at least C1 real |
| 10 to 12 | Admin proxy + role checks; request log; web shell with real login; system test set assembled | Frozen at G4 |
| 14 to 16 | Integrated Compose on middleware host; tunnel or firewall to the LLM server; all features wired | Full demo script (`resources/prompts/demo_script.md`) passes |
| 14 to 18 | Security: JWT expiry, role tests, CORS, rate limit on login, no text in logs | Checklist done |
| 17 to 20 | System ablation runner over the frozen set (4 arms), McNemar, bootstrap | Results table |

---

## 9. Testing

| ID | Case | Expected |
|---|---|---|
| GW-01 | C1 returns `ACCESS_BLOCKED` | blocked, code `ACCESS_BLOCKED`, no C2/LLM/C3 call |
| GW-02 | C2 BLOCK | `REQUEST_BLOCKED`, no LLM call |
| GW-03 | C3 VIOLATION | `OUTPUT_WITHHELD`, content empty, clause only in admin trace |
| GW-04 | Any stage times out | `OUTPUT_WITHHELD` |
| GW-05 | Clinical role calls admin route | 403 |
| GW-06 | History sent to LLM | contains no text that C1 flagged (check against manifests) |
| GW-07 | `GW_PIPELINE_STAGES=none` (or `c1`, `c1,c2`) with `GW_MODE=prod` | refuses to start |
| GW-08 | Contract tests on all outgoing calls and the `chat_message` reply | pass |

---

## 10. Definition of Done

- Real login with roles.
- The full chain runs on the middleware host against the onsite LLM.
- The three codes are shown correctly.
- The admin block loop works through C4 and C1.
- The ablation runner produces the system table.
- No prompt or answer text is in any gateway log.
