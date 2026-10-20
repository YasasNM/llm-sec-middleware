# MediGuard AI: Integration Contracts v1.0.0-rc2 (candidate for the Week 8 freeze, G2)

Location in the repository: `contracts/`. The current version is in `contracts/VERSION`. Changes since rc1: C3 scan request accepts an optional `categories` filter; the C3 verdict keeps `latency_ms` as the total and adds an optional `latency_breakdown_ms`; request ids use a readable daily format.

These are the only things the four components and the gateway must agree on. Everything else is internal to each owner.
Machine-readable copies are in `schemas/`, with valid samples in `examples/` and rejected samples in `examples/invalid/`.

```bash
pip install jsonschema
python contracts/validate.py                         # checks all samples
python contracts/validate.py c2_event my_event.json  # checks one payload of yours
```

**Change rule:** after the freeze, a contract changes only when every owner agrees. The version number goes up, and every component re-runs its contract tests.

---

## 1. Identifiers

| Field | Created by | When | Format (example) | Rule |
|---|---|---|---|---|
| `org_id` | Gateway (from the logged-in user's hospital) | Login | `org_001` | Copied into every call and event |
| `user_id` | Gateway (from the JWT) | Login | `user_001` | Copied into every call and event |
| `session_id` | Gateway | Chat opened, "New chat", or after 30 min idle | `sess_<ULID>` (`sess_01JAB3K9Q2`) | Copied into every call and event. C4 rule R7 uses it |
| `request_id` | Gateway | Each submitted prompt | `req_<YYYYMMDD>_<6-digit daily sequence>` (`req_20261020_000123`) | Copied into every call and event. **Also shown to the clinician as the reference.** There is no separate `reference` field |
| `event_id` | The component that sends the event | Each event | `evt-c1-<uuid4>` | Unique forever. C4 drops a repeated `event_id` |

All ids: string, 1 to 64 characters, letters, digits and `_ . : -` only. Components treat ids as opaque and never parse them.

Timestamps: UTC, ISO 8601, ending in `Z` (for example `2026-08-23T07:10:01Z`; milliseconds allowed).

---

## 2. Events to C4 (agreed by email)

**Rule:** every component sends **one event for every request it handles**, including normal requests (PASS, CLEAN, 0% masked). The gateway sends no event. C4 counts requests by unique `request_id`, so one request is never counted three times. Only verdicts add risk points.

**Transport:** each component POSTs to **its own** C4 endpoint, so no `source` field is needed:

| Sender | Endpoint |
|---|---|
| C1 | `POST http://c4:8004/v1/events/c1` |
| C2 | `POST http://c4:8004/v1/events/c2` |
| C3 | `POST http://c4:8004/v1/events/c3` |

- Header `X-Service-Key`.
- Reply `202 {"accepted": true, "request_counted": true|false}`, or `200 {"duplicate": true}` for a repeated `event_id`, `422` for a bad payload, `401` for a wrong key, or `403` when a sender's key is used on another sender's endpoint.
- Senders retry 3 times with backoff, then keep the event in a local SQLite outbox and resend later.
- A C4 outage never blocks or slows a request.
- Unknown extra fields are rejected (`422`), so contract drift is caught early.

### 2.1 Common fields (all three)

`event_id`, `org_id`, `user_id`, `request_id`, `session_id`, `event_timestamp`. All are required.

### 2.2 C1 adds

| Field | Type | Meaning |
|---|---|---|
| `masking_percentage` | number, 0 to 100, 1 decimal | Characters of the original prompt that were replaced by placeholders, divided by the prompt length, times 100. `0` = nothing masked |

- C1 sends this for every `/v1/check`, **including a blocked user's attempt**. In that case C1 still runs detection, so the number is real.
- `event_timestamp` is the time C1 handled the request.
- Because C1 always runs first, the C1 event is the one event every request has. C4 uses it to learn normal activity (R6).

### 2.3 C2 adds

| Field | Type | Meaning |
|---|---|---|
| `c2_verdict` | `PASS` or `BLOCK` | C2's decision |
| `c2_score` | number, 0 to 1 | The score behind the decision: the similarity score when the near-exact override fired, otherwise the ensemble score. Sent for every request, also PASS |

### 2.4 C3 adds

| Field | Type | Meaning |
|---|---|---|
| `c3_verdict` | `CLEAN` or `VIOLATION` | C3's decision |
| `clause_id` | string or null | The **top-ranked** cited clause. `null` when CLEAN. Also `null` for a leak-only VIOLATION with no policy clause |
| `leaked_pii_detected` | boolean | An identifier was found in the answer |
| `leaked_pii_count` | integer ≥ 0 | How many identifiers leaked. `0` when none |

Consistency rules (enforced by the schema):
- CLEAN means `clause_id = null`, `leaked_pii_detected = false` and `leaked_pii_count = 0`.
- If `leaked_pii_detected = true`, then `leaked_pii_count ≥ 1` and the verdict is VIOLATION.
- A VIOLATION without a leak must have a `clause_id`.

**No event when C3 itself fails** (fail-closed error). The answer is still withheld, but scanner errors never count against a user. C3 logs them locally.

### 2.5 Which events one request produces

| What happened | C1 event | C2 event | C3 event |
|---|---|---|---|
| Normal request, clean answer | yes | yes (PASS) | yes (CLEAN) |
| User on the block list | yes | no | no |
| C1 down, or no block list fresher than 5 min | no | no | no |
| C2 blocks | yes | yes (BLOCK) | no |
| C2 fails on both branches (fail safe) | yes | no | no |
| LLM unreachable | yes | yes (PASS) | no |
| C3 finds a violation or leak | yes | yes (PASS) | yes (VIOLATION) |
| C3 crashes (fail closed) | yes | yes (PASS) | no |

Not sent to C4: embeddings, prompt text, response text, entity values, severity, class names.

---

## 3. Component APIs

All component endpoints except `/health` need `X-Service-Key`. All paths are versioned `/v1/...`. A wrong key returns `401`, and a bad body returns `422`. Error bodies never contain prompt or response text.

### 3.1 C1 Privacy Assistant (port 8001)

| Method | Path | Called by | Body → Returns |
|---|---|---|---|
| GET | `/health` | Compose, gateway | `name_detector` (`ml` / `heuristic`), block-list cache age |
| POST | `/v1/analyze` | Gateway (live typing) | `{text}` → entities with type, offsets, confidence. **No event** |
| POST | `/v1/reid` | Gateway (live typing) | `{text}` → quasi-identifiers, rarity, suggestions, score, level, `safer_prompt`. **No event** |
| POST | `/v1/check` | Gateway (on submit) | `c1_check_request` → `c1_check_response`. **Sends the C1 event** |
| POST | `/v1/ocr` | Gateway (upload) | multipart file → extracted text + `/v1/analyze` result. No event |

`/v1/check` decision:
- It returns `ALLOW` with masked text and a manifest, or `ACCESS_BLOCKED`.
- Explicit identifiers (names, NIC, MRN, phone, email, DOB) are **always** masked.
- `apply_reid_suggestions` controls only the re-identification generalisations (age, rare condition, ward, small town).

Placeholders:
- Format is `[TYPE_n]`, numbered per request, for example `[PATIENT_NAME_1]`, `[NIC_1]`. The same value gets the same token within one request.
- The manifest lists tokens only, never values.

**Never masked** (internal pseudonyms the protected LLM needs): patient ids `P0001` to `P9999` and doctor ids `D001` to `D999`.

### 3.2 C2 Intent Classifier (port 8002)

| Method | Path | Body → Returns |
|---|---|---|
| GET | `/health` | `model_version`, ChromaDB status, classifier status |
| POST | `/v1/classify` | `c2_classify_request` → `c2_classify_response`. **Sends the C2 event** |

- `method` is `similarity_override`, `ensemble` or `fail_safe`.
- If one branch fails, the other decides alone and `degraded = true`.
- If both fail, the verdict is `BLOCK` with method `fail_safe`. **C2 sends no event in this case**, the same rule as C3, so an infrastructure failure never counts against a user. C2 logs it locally.

### 3.3 C3 Policy Scanner (port 8003)

| Method | Path | Body → Returns |
|---|---|---|
| GET | `/health` | model loaded, index state, watcher alive |
| POST | `/v1/scan` | `c3_scan_request` → `c3_scan_response` (PolicyVerdict). **Sends the C3 event** unless `decision_source = error_fail_closed` |
| POST | `/v1/orgs/{org_id}/policies` | multipart PDF or Markdown → `202` document record |
| GET | `/v1/orgs/{org_id}/policies` | document list |
| PUT | `/v1/orgs/{org_id}/policies/{doc_id}` | replace (keeps `doc_code`) → `202` |
| DELETE | `/v1/orgs/{org_id}/policies/{doc_id}` | `204` |
| GET | `/v1/orgs/{org_id}/policies/{doc_id}/clauses` | clause list (admin) |
| GET | `/v1/orgs/{org_id}/policies/status` | dashboard status line |

- A VIOLATION still returns `200`.
- `rationale` and `cited_clauses` go to admins only, never to clinicians (tiered disclosure).
- `clean_response` is filled only when CLEAN.

### 3.4 C4 Risk Profiler (port 8004)

| Method | Path | Called by | Purpose |
|---|---|---|---|
| GET | `/health` | Compose | MongoDB status |
| POST | `/v1/events/c1`, `/v1/events/c2`, `/v1/events/c3` | C1, C2, C3 | Section 2 |
| GET | `/v1/blocklist?org_id=` | C1 | `c4_blocklist_response` |
| GET | `/v1/users?org_id=&priority=` | Gateway (admin) | All profiles with score, priority, main reason, last updated; summary counts P1 to P4, blocked |
| GET | `/v1/users/{user_id}/profile?org_id=` | Gateway (admin) | Score, priority, recommendation, top contributors, behaviour summary, average masking %, status |
| GET | `/v1/users/{user_id}/timeline?org_id=&limit=` | Gateway (admin) | Events with rule fired, impact, running score |
| POST | `/v1/users/{user_id}/block` | Gateway (admin) | `{org_id, by, reason}` → profile with `status: blocked` |
| POST | `/v1/users/{user_id}/unblock` | Gateway (admin) | `{org_id, by, reason}` → profile with `status: active` |

- Profiles are keyed by (`org_id`, `user_id`).
- There is **no automatic blocking**: a P1 score produces `recommendation: BLOCK_RECOMMENDED`, and an admin decides.

### 3.5 Protected LLM API (onsite server, port 8010, already deployed)

| Method | Path | Body → Returns |
|---|---|---|
| GET | `/health` | database, Ollama, `model_pulled`, status |
| POST | `/v1/chat` | `llm_chat_request` → `llm_chat_response` |
| GET | `/v1/tools`, POST `/v1/tools/{name}` | Development only (direct database tools, no LLM) |

- Only the gateway calls `/v1/chat` in the integrated system. It sends **masked** text and masked history only.
- Set the client timeout to 300 s, because `qwen2.5:7b` runs on CPU.

### 3.6 Gateway to web app (port 8000)

`POST /api/chat/submit` returns a `chat_message` (the prototype's `ChatMessage` shape):

| `type` | When | `content` | `user_message_code` |
|---|---|---|---|
| `normal` | Clean answer, nothing masked | answer | none |
| `pii-masked` | Clean answer, `masking_percentage > 0` | answer (placeholders visible) | none |
| `blocked` | Any stop | empty | `ACCESS_BLOCKED`, `REQUEST_BLOCKED` or `OUTPUT_WITHHELD` |

`requestId` is the `request_id`.

---

## 4. User-facing codes and labels

| `user_message_code` | Source | Clinician sees |
|---|---|---|
| `ACCESS_BLOCKED` | C1 (block list) | "Your access to the assistant is paused. Please contact your administrator." + reference |
| `REQUEST_BLOCKED` | C2 | "This request could not be processed." + reference |
| `OUTPUT_WITHHELD` | C3 violation or leak, **or any fail-closed stop** (C1, C2, LLM or C3 unreachable) | "An answer could not be shown because it may not meet hospital policy or a safety check was unavailable." + reference |

The admin UI never shows component names. It uses these labels:

| Source | Label |
|---|---|
| C1 | Privacy check |
| C2 | Intent check |
| C3 | Policy check |
| C4 | Behaviour check |

---

## 5. Ports and auth

| Service | Port | Exposed |
|---|---|---|
| Web app (dev server) | 5173 | developer machine |
| Gateway | 8000 | only service reachable by browsers |
| C1, C2, C3, C4 | 8001, 8002, 8003, 8004 | internal Docker network |
| MongoDB | 27017 | internal |
| Protected LLM API | 8010 (onsite server) | middleware host only (firewall) |

- Internal calls use `X-Service-Key`, with **one key per caller**. Every service reads the same three variables, handled by the shared package `packages/mediguard-common`:
  - `MG_SERVICE_NAME` is its own name (`gateway`, `c1`, `c2`, `c3`, `c4`).
  - `MG_SERVICE_KEY` is the key it presents to others.
  - `MG_ACCEPTED_KEYS` lists the callers it accepts, as `name:key` pairs (for example `gateway:…,c1:…`). The receiver therefore knows **which** caller is calling, and C4 uses this to allow each sender only on its own `/v1/events/cN` endpoint.
- Keys live in `.env` files and are never committed.
- Browsers use JWT issued by the gateway.
