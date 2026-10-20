# Shared Library and Stubs: Build Blueprint

**Path:** `packages/mediguard-common/` (library), `tools/stubs/` (shared stand-ins)
**Owner:** gateway owner (to be named); every component owner reviews changes that affect their service
**Used by:** gateway, C1, C2, C3, C4

**Why it exists:** without it, every service would write its own service-key check, event sender, outbox, id formats and logging, each slightly different. That is exactly where integration bugs come from. One small, well-tested package removes the duplication, and each component stays independent: it imports the package, and its Dockerfile copies it in.

---

## 1. Scope

| In scope | Out of scope |
|---|---|
| Service-key check and caller identity (K23) | Any detection or scoring logic |
| Event building, background sending, retry, SQLite outbox (K8) | HTTP clients for specific services (they live in the gateway) |
| Id formats and validators (K6) | Business rules of any component |
| JSON logging that refuses text fields (no-PHI rule) | |
| Loading and validating contracts | |
| `/health` helpers and an `MG_*` settings base class | |

---

## 2. Modules

| Module | API | Notes |
|---|---|---|
| `settings.py` | `class MGSettings(BaseSettings)` with `service_name`, `service_key`, `accepted_keys`, `c4_url`, `outbox_path`, `log_level` (`env_prefix="MG_"`) | Components extend it with their own prefix |
| `auth.py` | `require_caller(*names)` (FastAPI dependency); `caller_name(request)` | Parses `MG_ACCEPTED_KEYS="gateway:abc,c1:def"`. Constant-time compare. `401` if missing or wrong; `403` if the key is valid but the caller is not allowed on that route |
| `ids.py` | `new_event_id(source)` → `evt-c2-<uuid4>`; `is_request_id(s)`; `is_session_id(s)`; `now_utc()` → `2026-10-20T07:10:02Z` | The gateway generates `request_id` (`req_<YYYYMMDD>_<6-digit seq>`) and `session_id` (`sess_<ULID>`) |
| `events.py` | `new_event(source, ids, **fields)`; `EventSender(endpoint).send(event)` | Validates against the contract before sending. Background thread. Retries at 0.5 s, 2 s and 5 s, then the SQLite outbox (`MG_OUTBOX_PATH`), resent every 30 s. **Never blocks the request path.** Exposes `pending_count()` for `/health` |
| `logging.py` | `get_logger(name)` | JSON lines. Drops or rejects keys named `text`, `prompt`, `response`, `response_text`, `message`, `content`, `value`. Includes `request_id` when present |
| `contracts.py` | `validate(name, payload)`; `load_schema(name)` | Reads `contracts/schemas/`. Used by tests, stubs and `EventSender` |
| `health.py` | `health_payload(status, **extra)` | Common shape: `{status, service, version, ...}` |

Example (C2):

```python
from mediguard_common.auth import require_caller
from mediguard_common.events import EventSender, new_event

events = EventSender("/v1/events/c2")

@router.post("/v1/classify", dependencies=[Depends(require_caller("gateway"))])
async def classify(req: C2Input):
    ...
    events.send(new_event("c2", req, c2_verdict=verdict, c2_score=score))   # returns immediately
```

---

## 3. Outbox table (SQLite, created by the library)

| Column | Type | Note |
|---|---|---|
| `event_id` | TEXT PK | |
| `endpoint` | TEXT | `/v1/events/c1` etc. |
| `payload_json` | TEXT | agreed fields only (already validated) |
| `status` | TEXT | `pending`, `sent`, `failed` |
| `attempts` | INTEGER | |
| `next_try_at` | TEXT | backoff schedule |
| `created_at` | TEXT | |

C4 drops duplicate `event_id`s, so a resend after a lost reply is safe.

---

## 4. Shared stubs (`tools/stubs/`)

| Stub | Port | Behaviour |
|---|---|---|
| `c1_stub.py` | 8001 | Regex masking (names after "Patient", NIC, phone, email) to `[TYPE_n]`; editable block list file; sends nothing |
| `c2_stub.py` | 8002 | BLOCK if the prompt contains phrases from a small list ("ignore previous instructions", "export all"), else PASS; `c2_score` 0.95 / 0.05 |
| `c3_stub.py` | 8003 | VIOLATION if the answer contains a dose pattern or an unmasked phone or NIC, else CLEAN |
| `c4_stub.py` | 8004 | Accepts the three event endpoints, validates against contracts, keeps events in memory, `GET /events` to inspect, serves a block list |
| `mock_llm.py` | 8010 | `/v1/chat` returns canned answers instantly; `/health` ok |

Every stub validates its own outputs against `contracts/`. Each component's own `stubs/` folder may copy or extend these.

---

## 5. Plan

| Week | Task | Done when |
|---|---|---|
| 3 | Package skeleton, `settings`, `ids`, `auth`, tests | Installed by all services |
| 4 | `events` + outbox, `contracts`, `logging` | Used by C1 to C3 for their first events |
| 4 to 5 | `tools/stubs/` | Gateway runs the chain against stubs |
| 8 | Freeze the package API with the contracts (G2) | Tag `common-v1.0.0` |

---

## 6. Tests

| ID | Case | Expected |
|---|---|---|
| CM-01 | Wrong key / missing key | 401 |
| CM-02 | Valid C2 key on a route that requires `c3` | 403 |
| CM-03 | C4 down, 20 events sent | All in the outbox; delivered when C4 returns; request latency unaffected |
| CM-04 | Event with an extra field | Rejected before sending (contract) |
| CM-05 | Logger given `prompt="..."` | Field dropped, warning logged |
| CM-06 | Id validators on contract examples | Pass |
