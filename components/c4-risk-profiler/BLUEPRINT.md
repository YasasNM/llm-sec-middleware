# C4: Heuristic Cross-Session User Risk Profiler
## Implementation Blueprint (Prototype-Grounded, integration-aligned)

**Project:** A Multi-Layer Security Middleware Framework for Protecting Healthcare LLM Applications (J26-CS-321)
**Component:** C4, Heuristic Cross-Session User Risk Profiler
**Student:** S.D.W. Gunaratne, IT23241978, SLIIT, BSc (Hons) IT, Cyber Security
**Supervisor:** Mr. Kavinga Yapa · **Co-supervisor:** Ms. Helani Herath
**Repository path:** `components/c4-risk-profiler/` (service) and `apps/web/src/features/risk/` (risk admin UI) · **Port:** 8004
**Contracts:** `contracts/schemas/c1_event`, `c2_event`, `c3_event`, `c4_blocklist_response`
**This revision:** keeps the prototype-grounded analysis of the previous edition and aligns C4 with the team decisions K1 to K30, the C4 event agreement (email to Linuka, Nisith and Yasas), and integration rules IR1 to IR15 (`PROJECT_BLUEPRINT.md`). Section 2 lists what changed.

---

## 0. How to use this document

This file is the single working reference for C4, intended to be used through requirement analysis, implementation, integration, testing, progress meetings and the final viva. Every claim below is labeled:

| Label | Meaning |
|---|---|
| **[Proposal]** | Stated directly in the submitted, signed proposal `IT23241978.pdf`. Treated as ground truth / primary source. |
| **[Prototype]** | Observed directly in the `MediGuardAI` code (React/Vite/Tailwind app). Treated as the current, real implementation state. |
| **[Resolved]** | A point that looked ambiguous or contradictory across documents; resolved here using proposal text + prototype evidence together, with the reasoning shown. |
| **[Recommendation]** | Not fixed by the proposal or prototype; a reasonable engineering suggestion for you to adopt or override. |
| **[Gap]** | Something the proposal/dashboard spec calls for that the prototype does not yet implement. |
| **[Needs confirmation]** | Still genuinely open; raise with supervisor/team. |
| **[Agreed Kn / IRn]** | Team decision or integration rule (`PROJECT_BLUEPRINT.md`). Binding for integration. |

Nothing below is invented as if it were proposal content. Where the proposal is silent, this is stated explicitly.

---

## 1. Project context [Proposal]

Four-component healthcare LLM security middleware. A clinician's prompt flows through **C1 → C2 → protected LLM → C3 → user**, while **C4 sits alongside this pipeline** consuming the per-request events from C1, C2 and C3 (Section 7.1) rather than re-implementing any component's detection logic.

| Component | Owner | Role |
|---|---|---|
| C1 | Linuka | Real-Time Healthcare Privacy Assistant: block-list check (enforces admin blocks), PHI and re-identification-risk detection, OCR, one-way masking `[TYPE_n]` **[Agreed K4, K22]** |
| C2 | Nisith | Semantic Intent Classifier, screens the masked prompt for prompt-injection / jailbreak attempts |
| C3 | Yasas | RAG Output Policy Scanner, audits every generated response against the hospital's own policy documents, citing the clause violated |
| **C4** | **You** | **Heuristic Cross-Session User Risk Profiler**: aggregates the per-request events of C1 to C3 per user, across sessions, into an explainable 0 to 100 risk score and P1 to P4 priority, and **owns the risk admin UI** **[Agreed K12]** |

**Research problem [Proposal, Section 1.3]:** *"How can security events produced by independent LLM protection components be aggregated, without a machine-learning model, into a persistent, explainable, per-user risk score that reliably distinguishes escalating or repeated misuse from isolated or benign activity, while remaining auditable enough for an administrator to justify every elevated score?"*

**Design principle [Proposal, 1.1]:** C4 is deliberately transparent and rule-based. No machine-learning model (Isolation Forest was explicitly considered and rejected, see Appendix F evidence log). The contribution is the transfer of user-behaviour risk aggregation to LLM-security events, with an explainable, time-decayed scoring design and a controlled, baseline-compared evaluation.

---

## 2. What this revision changes

The earlier technical guide (the live Claude Doc artifact built previously in this engagement) was written from the proposal alone. This revision adds a second, independent source of truth, the `MediGuardAI` prototype, and uses it to:

1. **Resolve the enforcement-mechanism ambiguity** flagged earlier (Section 3 below), the prototype shows exactly how "C4 recommends, C1 enforces" is meant to work in practice.
2. **Confirm the admin dashboard's real shape**: the three-view dashboard specified in Appendix E of the proposal is now a working (if data-mocked) UI, so its exact screens, fields and navigation are no longer assumptions.
3. **Confirm the frontend technology stack** (React 19 / Vite / Tailwind v4), not specified anywhere in the proposal, and now fixed by the prototype.
4. **Surface the real implementation gap**: the prototype is a **pure frontend UI shell with hardcoded demo data**: none of C4's actual backend (event ingestion, feature extraction, the R1 to R7 rule engine, time decay, MongoDB persistence) exists yet anywhere. That backend is now the single largest remaining body of work, and Section 16 gives a concrete build order for it.
5. **Identify a few new, small inconsistencies inside the prototype itself** that are worth a team decision but don't block backend work.

**Integration alignment (this revision):**

| Before | Now | Ref |
|---|---|---|
| Appendix A `SecurityEvent` (`ts`, `source`, `class`, `severity`, `details`, `prompt_id`) | Three agreed per-component events with `event_timestamp`; sender known from the endpoint (`/v1/events/c1`, `/c2`, `/c3`) | K7, IR1 |
| Events "when something noteworthy occurs" | One event from each component for **every** request it handles; requests counted by unique `request_id`; only verdicts add points | K9 |
| No gateway event | No `REQUEST_SEEN`; the C1 event is the per-request activity signal | K9, IR6 |
| Profiles per `user_id` | Profiles per (`org_id`, `user_id`); block list per org | K14 |
| R2 "severity meets threshold" | Severity derived from `c2_score` and `leaked_pii_count` | IR5 |
| C1 events with no rule | `masking_percentage` stored and shown; no rule yet | K11 |
| Recommendation "to C1" vs "to dashboard" | Dashboard + admin action; C1 reads `/v1/blocklist` | K12 |
| Dashboard owner unclear | Risk admin UI is C4's deliverable, served through the gateway | K12 |
| "Phrase Database" question | Removed | K21 |
| Auth approach open | Gateway JWT + roles; C4 trusts only service keys | K4, K23 |
| Scanner errors | C2 fail-safe and C3 scanner errors send **no** event, so they never score | IR4 |

---

## 3. Resolved design decision: the enforcement hand-off

This was the main open question carried into this revision. Re-reading the actually-submitted proposal (not an earlier draft) together with the prototype resolves it.

### 3.1 What the proposal actually says

| Location | Exact wording |
|---|---|
| Abstract | "Within the wider solution, C4 recommends a response; C1 remains responsible for enforcement." |
| Table 3.3 (P1 Critical row) | "Blocked / **block recommendation to C1**." |
| Section 3.2.3, below Table 3.3 | "C4 performs the risk assessment and recommendation only; the enforcement action itself remains the responsibility of **C1**." |
| Section 4.2, step 12 | "When P1 is reached, emit a block recommendation **to dashboard**, **admin** remains responsible for enforcement (manually checked and block)." |
| FR-08 | "Emit a P1 block recommendation **to C1**." Acceptance condition: "C1 receives a structured recommendation when, and only when, a user reaches P1." |

So the proposal names two different recipients of the recommendation in different places, **"C1"** (Sections 1/3/5) and **"the dashboard" / "admin"** (Section 4.2), without explicitly saying these are the same hand-off described two ways. That is the ambiguity; it is a wording-precision issue, not a logical contradiction (nothing says C4 blocks directly, and nothing says enforcement is automatic).

### 3.2 What the prototype actually shows [Prototype]

`src/views/Admin.tsx`, `RiskProfile` component (the individual User Risk Profile screen):

```tsx
{flaggedUser.status === 'active' ? (
  <button onClick={onBlock} ...>Block User</button>
) : (
  <button onClick={onUnblock} ...>Unblock User</button>
)}
```

There is **no automatic blocking anywhere in the code**. Every status change (`active` → `blocked`) in the prototype happens only through an explicit button click by a logged-in administrator, on the C4-style Risk Profile screen, the Flagged Users list, or User Management. This is a human-in-the-loop action, not a programmatic hand-off from C4's score into C1's request pipeline.

### 3.3 Resolution [Resolved]

Treat the hand-off as a **two-step, human-in-the-loop design**, which is consistent with every passage above once read together:

1. **C4's job (automatic):** when a user's score crosses into P1 (≥80), C4 writes a block **recommendation** into the `RiskProfile` and surfaces it prominently on the **admin dashboard** (Section 4.2's "emit... to dashboard" wording, and FR-08's acceptance condition that something concrete is produced at the moment P1 is reached).
2. **Administrator's job (manual, human-in-the-loop):** a security/compliance admin reviews the flagged user's profile and clicks **Block User**: exactly the control built in the prototype.
3. **C1's job (enforcement, out of C4's scope):** once a block decision exists (admin-confirmed), C1 is the component that actually gates that user's future requests at the input layer, this is what "C1 remains responsible for enforcement" means. C4 never calls an enforcement API directly; it never needs to, because C1 sits upstream of every future request regardless of who triggered the block decision.

This reading requires no design change and no further proposal edit, it is already what Section 4.2 step 12 describes ("admin... manually checked and block"), it already matches Appendix E's dashboard spec (block/unblock is a dashboard action), and it is now independently confirmed by the one piece of code that actually implements this flow. **Use "FR-08 delivers the P1 recommendation to the admin dashboard; the administrator's manual Block/Unblock action is what C1 subsequently enforces" as the one-sentence answer if a supervisor or panel asks about this.**

**Confirmed by the team [Agreed K12]:** "Admin manually blocks risk users; C4 creates the UI for all that." The admin's Block/Unblock goes through the gateway (role check) to C4 `POST /v1/users/{user_id}/block|unblock`; C4 stores `status: blocked`; C1 reads `GET /v1/blocklist?org_id=` (cached up to 30 s) and refuses that user's next request with `ACCESS_BLOCKED`.

**Recommendation for implementation:** keep FR-08's wording ("emit a P1 block recommendation to C1") but implement it as *writing the recommendation + top contributing reasons into the `RiskProfile` document and the dashboard's "Users Requiring Attention" feed*, not as a direct API call into a C1 service. If C1 later exposes a `/block` endpoint, the admin's **Block User** button is the natural place to call it, C4 itself stays a recommendation engine, matching the proposal's explainability-first framing.

---

## 4. Requirements: status against the prototype

### 4.1 Functional Requirements [Proposal, Table 5.1]

| ID | Requirement | Prototype status |
|---|---|---|
| FR-01 | Ingest the agreed events from C1, C2, C3 (Section 7.1) | **[Gap]** No ingestion endpoint exists. Prototype has no backend at all. A dev-build reference (old envelope) exists in `mediguard.zip` |
| FR-02 | Group events by (`org_id`, `user_id`) **[Agreed K14]**, one persistent `RiskProfile` per user per organisation | **[Gap]** Not implemented; `RISK_PROFILE_DATA` in `Admin.tsx` is a hardcoded 2-entry object, not derived from events. |
| FR-03 | Calculate configured behavioural features | **[Gap]** Not implemented anywhere. |
| FR-04 | Calculate a reproducible 0 to 100 risk score | **[Gap]** Scores (84, 72) are hardcoded literals, not computed. |
| FR-05 | Assign a P1 to P4 risk priority | **[Partial, Prototype]** The *display* exists and matches the proposal's labels exactly ("P1 - Critical" etc.), but nothing computes it. |
| FR-06 | Store top contributing factors per score | **[Gap]** Not present in the data model or UI (see Section 9 gap list). |
| FR-07 | Update a user's profile when new events arrive | **[Gap]** No event stream exists to update from. |
| FR-08 | Emit a P1 block recommendation to C1 | **[Resolved in Section 3]**, implement as dashboard surfacing + admin-triggered Block button, which the prototype UI already has (manually, with mock data). |

### 4.2 Non-Functional Requirements [Proposal, Table 5.2]

| ID | Requirement | Prototype status |
|---|---|---|
| NFR-01 Explainability | 100% of non-zero score contributions traceable | **[Gap]** No scoring exists yet to trace. |
| NFR-02 Performance | Profile update completes within 2s | **[Not applicable yet]**, no backend. |
| NFR-03 Reliability | No duplicate profiles from duplicate events | **[Gap]** No dedup logic exists (no events exist). |
| NFR-04 Security | Dashboard access limited to authorised admin roles | **[Resolved by design, K4: the gateway issues JWTs and checks `org_admin` / `staff_admin` on every `/api/admin/risk/*` route; C4 accepts admin calls only with the gateway's service key. Still to build.]** Prototype finding: **[Gap, important]** Login screens (`AdminLogin`, `ClinicalLogin`) are **cosmetic**: `handleLogin` just does `setTimeout(...) ; onLogin()` with no credential check at all. Role switching (`org_admin` / `staff_admin`) is a plain UI toggle on the Landing page, not an authenticated claim. **This must be flagged as unimplemented, not as "needs minor polish", there is currently zero access control.** |
| NFR-05 Privacy | C4 uses event metadata, not raw prompt content | **[Guaranteed by contract]** The agreed events contain ids, time, verdicts, scores and counts only; the schemas reject extra fields. |
| NFR-06 Capacity | Processes full evaluation dataset + thousands-of-events replay | **[Not applicable yet]**, no backend to load-test. |
| NFR-07 Usability | Admin reaches an individual risk profile in one interaction | **[Confirmed, Prototype]** `Dashboard` → "Review" button → `RiskProfile` is exactly one click, satisfied by the current UI flow. |
| NFR-08 Maintainability | Rules/thresholds externally configurable, not hardcoded | **[Gap]** N/A yet (no rule engine); keep this requirement in mind when the backend is built, don't hardcode R1 to R7 weights in application code (see Section 16). |

---

## 5. System architecture [Proposal, Section 4]

Unchanged from the proposal; the prototype does not touch the backend architecture, only the admin-facing presentation layer.

**Overall pipeline [Agreed K5, K9]:** Clinician prompt → **gateway** (login, `request_id`, `session_id`) → **C1** (block-list check, PHI detection, masking) → **C2** (prompt-injection/jailbreak screening) → protected LLM (onsite Ollama `qwen2.5:7b`) → **C3** (policy-clause verdict, PHI re-check) → user. **C1, C2 and C3 each send one event for every request they handle** (including PASS, CLEAN, 0% masked); C4 consumes that stream beside the main pipeline and is never in the request path, except for C1's cached block-list read.

**C4 internal modules (M1 to M9) [Proposal, Figure 4.2]:**

| Module | Function |
|---|---|
| M1 | Validation & de-duplication (schema check per sender endpoint, reject duplicate `event_id`, count unique `request_id`) |
| M2 | User grouping & history retrieval (group by `org_id` + `user_id`, retrieve relevant event window) |
| M3 | Feature extraction (frequency, derived severity, repetition, escalation across sessions, unfamiliar time, PII leakage, masking behaviour for display) |
| M4 | Rule engine, weighted, additive contributions, R1 to R7 (Appendix C) |
| M5 | Time decay, older evidence weighted down by a calibrated decay parameter |
| M6 | Score aggregator & P1 to P4 classifier, bounds score to 0 to 100, applies thresholds |
| M7 | RiskProfile store (MongoDB), `security_events`, `user_behaviour_features`, `user_risk_profiles` |
| M8 | Administrator dashboard, Managed Users view, Individual User Risk Profile, Event & Rule Timeline |
| M9 | Evaluation harness, baseline (fixed event-count) vs proposed scorer, scenario replay, feature ablation |

**None of M1 to M7 or M9 exist in the prototype.** M8 exists as a UI shell with mock data (Section 9).

---

## 6. Prototype coverage map

A single table answering "what's actually built vs. what the proposal specifies."

| Area | Proposal spec | Prototype reality | Status |
|---|---|---|---|
| Event ingestion (FR-01) | REST/queue ingestion from C1 to C3 | None | **Not started** |
| Feature extraction (M3) | 8 behavioural features | None | **Not started** |
| Rule engine (M4, R1 to R7) | Weighted additive rules | None | **Not started** |
| Time decay (M5) | Exponential decay on older evidence | None | **Not started** |
| Score aggregator / classifier (M6) | 0 to 100 score → P1 to P4 | Hardcoded display values only | **UI mock only** |
| Persistence (M7, MongoDB) | 3 collections | None, React `useState`, resets on reload | **Not started** |
| Evaluation harness (M9) | Baseline vs. proposed, 7 scenarios, ablation | None | **Not started** |
| Admin dashboard shell (M8) | 3-view navigation per Appendix E | **Built**, Dashboard, User Management, Flagged Users, Flagged Detail, Risk Profile, Policy Documents, AI Configuration, Onboarding | **Implemented (UI only, mock data)** |
| Auth / RBAC (NFR-04) | Admin-only dashboard access | Fake login (`setTimeout`, no credential check); role is a UI toggle | **Not implemented, flagged as a real gap, see 4.2** |
| C1-style PII detection | Full privacy assistant (NLP-based, per C1's own proposal) | Client-side regex demo in the Clinical chat UI only (Section 10) | **Toy implementation, not representative of final C1** |
| C2 / C3 | Semantic classifier / RAG policy scanner | Canned static strings only; no real detection logic anywhere | **Not implemented (expected, these are teammates' components)** |

---

## 7. Data contracts

### 7.1 Incoming events [Agreed by email, K7, K9, K14] (replaces Appendix A as the integration contract)

Every component sends one event for every request it handles. Common fields (all required): `event_id` (unique, never repeated), `org_id`, `user_id`, `request_id`, `session_id`, `event_timestamp` (UTC ISO 8601 ending in `Z`).

| Sender | Endpoint | Extra fields | Example |
|---|---|---|---|
| C1 (Linuka) | `POST /v1/events/c1` | `masking_percentage` 0 to 100 (0 = nothing masked) | `12.5` |
| C2 (Nisith) | `POST /v1/events/c2` | `c2_verdict` PASS/BLOCK; `c2_score` 0 to 1, also for PASS | `BLOCK`, `0.87` |
| C3 (Yasas) | `POST /v1/events/c3` | `c3_verdict` CLEAN/VIOLATION; `clause_id` (null when CLEAN); `leaked_pii_detected`; `leaked_pii_count` (0 when none) | `VIOLATION`, `POL-PHI-002`, `true`, `3` |

```json
{"event_id":"evt-c3-0001","org_id":"org_001","user_id":"user_001","request_id":"req_20261020_000123",
 "session_id":"sess_01JAB3K9Q2","event_timestamp":"2026-10-20T07:10:03Z",
 "c3_verdict":"VIOLATION","clause_id":"POL-PHI-002","leaked_pii_detected":true,"leaked_pii_count":3}
```

Rules on the C4 side:
- **Sender identity [IR1]:** the endpoint tells C4 the source. Each sender has its own service key, and a key used on another sender's endpoint gets `403`.
- **Validation:** `contracts/schemas/cN_event.schema.json`. Unknown extra fields return `422`, and so does a timestamp more than 5 minutes in the future.
- **Dedup (NFR-03):** a repeated `event_id` returns `200 {"duplicate": true}` and changes nothing.
- **Request counting:** by unique `request_id` across the three sources (one request = one count).
- **Late events** (outbox replays) are accepted, and the profile is recomputed by event time.
- **Never arriving:**
  - C2 `fail_safe` and C3 scanner errors send no event, so infrastructure failures never score [IR4].
  - C1 sends no event when it cannot confirm the block list.
- **Field meaning:**
  - `masking_percentage` is masked characters ÷ prompt length × 100 [IR7].
  - `c2_score` is the score behind C2's verdict [IR8].
  - `clause_id` is the top cited clause, and is null for a leak-only violation [IR2, IR3].

### 7.2 `RiskProfile` [Proposal, Appendix B, extended]

```json
{
  "org_id": "org_001", "user_id": "user_001",
  "score": 84, "priority": "P1", "priority_label": "P1 - Critical",
  "recommendation": "BLOCK_RECOMMENDED", "status": "active",
  "top_contributors": [
    {"rule_id": "R5", "title": "Identifier leakage in answers", "points": 31.2},
    {"rule_id": "R3", "title": "Repeated suspicious activity", "points": 22.5}
  ],
  "behaviour_summary": {
    "requests": 142, "sessions": 19, "intent_blocks": 6, "policy_violations": 4,
    "leak_events": 2, "leaked_items": 5, "high_severity": 3, "unfamiliar_time_events": 2,
    "avg_masking_percentage_30d": 9.4, "requests_with_masking_pct": 31.0
  },
  "first_seen": "2026-08-01T03:10:00Z", "last_activity": "2026-10-20T07:10:03Z",
  "updated_at": "2026-10-20T07:10:04Z", "event_count": 398, "ruleset_version": "rules-v1"
}
```

`recommendation` is `BLOCK_RECOMMENDED` (P1), `CLOSE_MONITORING` (P2), `WATCH` (P3) or `NONE` (P4). `status` is `active` or `blocked` (set only by an admin action).

### 7.3 Cross-check against the prototype's UI data shape [Resolved]

- **`department`, `role`, `firstSeen`:** `firstSeen` is computed by C4. Display name, department and role come from the **gateway's** `users` collection, which the gateway joins into the admin API responses. C4 does not store HR data.
- **Severity labels:** the prototype's High/Medium/Low is a **display** value, derived per event (Section 8.4). There is no integer severity in the agreed events.

## 8. Core scoring algorithm [Proposal, Table 3.2 / Appendix C, mapped to the agreed fields]

### 8.1 Definitions

| Term | Meaning |
|---|---|
| Request | A unique `request_id` seen for the user from any sender (normally the C1 event arrives first) |
| Request time | Earliest `event_timestamp` among that request's events |
| Suspicious event | C2 event with `c2_verdict = BLOCK`, or C3 event with `c3_verdict = VIOLATION` |
| Session | Unique `session_id` |

### 8.2 Rules (initial impacts from the proposal; every number lives in `configs/rules.yaml`, NFR-08)

| Rule | Title shown to admins | Fires on | Impact |
|---|---|---|---|
| R1 | Suspicious prompt | C2 event with `BLOCK` | +10 |
| R2 | High-severity event | C2 `BLOCK` with `c2_score ≥ r2.c2_score` (0.90), **or** C3 `VIOLATION` with `leaked_pii_count ≥ r2.leak_count` (3) [IR5] | +15 |
| R3 | Repeated suspicious activity | At least `r3.count` (3) suspicious events within `r3.window_minutes` (30); fires on the event that completes the set, once per window | +15 |
| R4 | Policy violation | C3 event with `VIOLATION` | +15 |
| R5 | Identifier leakage in answers | C3 event with `leaked_pii_detected = true` | +20 |
| R6 | Unusual time with suspicious activity | Suspicious event in an hour holding < `r6.min_share` (5%) of the user's requests over 30 days; only after at least `r6.min_requests` (20) requests. PASS/CLEAN/C1 events build the baseline [K9] | +10 |
| R7 | Escalating across sessions | Suspicious-event count rises across the user's last `r7.sessions` (3) sessions; fires once, on the first suspicious event of the third rising session | +10 |
| (future) | Masking behaviour | **Inactive [K11].** `masking_percentage` is stored and displayed; a rule needs evidence first (careless power-user scenario) | 0 |

One event can fire several rules (for example a leak-heavy violation fires R4 + R5 + R2 = +50 raw), matching the proposal's additive design.

**Worked example [Proposal, Appendix C]:** suspicious C2 event (+10) → repeated suspicious event in the same short window (+15) → C3 policy violation (+15) = raw 40, *before* time decay. Show each of the three contributions separately on the dashboard, never only the aggregate 40.

### 8.3 Decay, score and priority

```
score = min(100, Σ impact_i × exp(-λ × age_hours_i))      λ = ln 2 / half_life_hours
```

- Sum over every stored rule hit in the window (default 30 days).
- `half_life_hours` starts at 72. It is tuned on the tuning subset and frozen at G5. The slow-and-low scenario is the test.
- The profile is recomputed from stored rule hits on each new event, and when the dashboard reads it (decay moves with time). It is deterministic: the same events give the same score.

| Priority | Score range | Operational status | `recommendation` |
|---|---|---|---|
| P1 Critical | 80 to 100 | Block recommendation (admin decides) | `BLOCK_RECOMMENDED` |
| P2 High | 60 to 79 | Restricted / close monitoring | `CLOSE_MONITORING` |
| P3 Medium | 30 to 59 | Watch / investigation | `WATCH` |
| P4 Low | 0 to 29 | Normal | `NONE` |

**Explainability (NFR-01):** every rule hit is stored with `event_id`, `rule_id`, `impact`, `ruleset_version` and the running `score_after`. `top_contributors` sums the decayed points per rule. Target: 100% of non-zero points traceable.

### 8.4 Display severity (UI only)

| Display | Rule |
|---|---|
| High | R2 or R5 fired on the event |
| Medium | Suspicious event (BLOCK or VIOLATION) |
| Low | Everything else (PASS, CLEAN, C1 events) |

**Explicitly out of scope [Proposal, Appendix F evidence log]:** no machine-learning model (Isolation Forest was considered and rejected in Sep 2026: "Do not include an ML model in the C4 research scope, compare against a fixed event-count baseline only").

## 9. Admin dashboard: spec vs. what's built

**Ownership [Agreed K12]:** the risk admin UI is C4's deliverable, built in `apps/web/src/features/risk/` and calling only the gateway (`/api/admin/risk/...`). The gateway checks the admin role and proxies to C4. The UI never shows component names: C1 rows are "Privacy check", C2 rows "Intent check", C3 rows "Policy check" **[Agreed K19]**. No prompt text appears anywhere.

The proposal's Appendix E dashboard spec and the prototype line up closely; here's the field-by-field comparison so you know exactly what remains to be added to the existing screens (not rebuilt from scratch).

### 9.1 Main Dashboard

| Appendix E spec | Prototype (`Dashboard` in `Admin.tsx`) |
|---|---|
| Summary cards for total managed users + P1 to P4 distribution | **Present**, as 5 cards: TOTAL USERS / CRITICAL / HIGH / MEDIUM / LOW, a direct, if differently-labelled, match to P1 to P4 |
| Managed Users table: User ID, Score, Priority/Status, Main Reason, Last Updated, View Profile | **[Gap]** Only a short "Users Requiring Attention" list (flagged users only) exists on the dashboard; a full managed-users table with every user's score is not on this screen (the closest analog, `UserManagement`, shows role/status but not risk score). |
| Filter controls by risk priority | **Present** on the separate Flagged Users screen (`RISK_LEVELS` filter chips), not yet on the main dashboard table. **[Recommendation]** reuse the same filter component on a new "all managed users with scores" table. |

### 9.2 Individual User Risk Profile

| Appendix E spec | Prototype (`RiskProfile` in `Admin.tsx`) |
|---|---|
| User identity + basic context | **Present** |
| 0 to 100 score, P1 to P4 priority, operational status | **Present**, matches Table 3.3 wording exactly |
| First-seen, last activity, event count, status | **Present** |
| Behavioural summary (suspicious events, C3 violations, high-severity events, attack types, unfamiliar-time events, leakage indicators) | **[Gap]** not shown as a distinct summary block |
| Top risk contributors with human-readable explanations | **[Gap]** not present, this is the field that should carry `RiskProfile.top_features` (Appendix B) once the backend exists |
| Recent Events & Violations table (time, event, source, severity, details) | **Present**, matches Appendix E.2 exactly |
| Manual Block/Unblock | **Present**, this is the control discussed in Section 3 |

### 9.3 Event & Rule Timeline

| Appendix E spec | Prototype |
|---|---|
| Time, source, event, severity, details per event | **Present** (as the events table inside Risk Profile, not a separate standalone timeline screen yet) |
| The specific rule triggered (R1 to R7) and its impact | **[Gap]** not shown anywhere, needs a `rule_id` / `impact` field threaded through once the rule engine exists |
| Resulting risk score immediately after that event | **[Gap]** not shown, needs a running-score field per event |
| Navigation back to Individual User Risk Profile | N/A, currently embedded in that same screen, not a separate navigable view |

**[Recommendation]** When the backend is wired up, promote the existing events table into its own "Event & Rule Timeline" screen (reachable from the Risk Profile, per Appendix E.3), and add the two missing columns (rule fired, running score), this is a small UI change once the data exists, not a redesign.

---

## 10. C1-style privacy detection in the prototype [Resolved]

The prototype's `Clinical.tsx` had a client-side regex demo (UK `+44` numbers, `USER-` ids, simple name patterns). **[Agreed K18]** This regex is removed from the product. Live detection in the Clinical view now comes from the real C1 service through the gateway (`/api/chat/analyze`), built by Linuka in `features/privacy`. For C4 this changes nothing: C4 is decoupled from how C1 detects and only receives the C1 event (`masking_percentage`).

The "Phrase Database, 7 phrases configured" panel and the "Multiple requests matched security phrases" reason were placeholder UI copy. **[Agreed K21]** The Phrase Manager is removed, so delete both from the dashboard.

## 11. Technology stack

| Layer | Proposal [Section 6.5 / Table 7.1] | Prototype [confirmed] | Status |
|---|---|---|---|
| Backend language/runtime | Python | n/a | **[Proposal]**, not yet started |
| Backend API | (implied) FastAPI (named in Table 7.1 budget line) | n/a | **[Proposal]**, not yet started |
| Database | MongoDB (Community edition) | n/a | **[Proposal]**, not yet started |
| Admin dashboard frontend | Not specified | **React 19, TypeScript 5.7, Vite 8, Tailwind CSS v4** (`package.json`) | **[Confirmed, Prototype]**, new information this revision adds |
| Styling | Not specified | Tailwind v4 via `@tailwindcss/vite`, no PostCSS/Tailwind-config file needed | **[Confirmed, Prototype]** |
| Python version | Not specified | n/a | **[Agreed K30]** Python 3.11 |
| Shared package | n/a | n/a | `mediguard-common` for service-key auth (caller identity), ids, logging |
| Admin auth | Not specified | Fake login | **[Agreed K4]** Gateway JWT + roles; C4 trusts the gateway's service key |
| Contract tests | n/a | n/a | jsonschema against `contracts/` |

**[Agreed K24]** Build the backend as a Python/FastAPI service exposing a versioned REST surface (Section 13.3) backed by MongoDB, and have the existing React dashboard call it instead of reading local `useState`. This requires no frontend rewrite, only replacing the hardcoded `INIT_USERS` / `INIT_FLAGGED` / `RISK_PROFILE_DATA` constants with `fetch` calls, which is a small, well-scoped integration task (see Section 16).

---

## 12. Security notes [Proposal, Table 8 / Section 5.2, updated]

- **Authentication [Agreed K4]:** the prototype's login was cosmetic. In the product, the gateway issues JWTs after a real password check (argon2) and enforces roles server-side. Only `org_admin` and `staff_admin` reach `/api/admin/risk/*`, and only they can block or unblock. Until the gateway exists, state plainly in the viva: "specified, implemented in the gateway, C4 trusts only the gateway's service key".
- **Service auth [Agreed K23]:** every C4 endpoint except `/health` needs `X-Service-Key`. C4 knows the caller from the key:

| Caller | Allowed endpoints |
|---|---|
| c1 | `/v1/events/c1`, `/v1/blocklist` |
| c2 | `/v1/events/c2` |
| c3 | `/v1/events/c3` |
| gateway | admin endpoints |

- **Privacy (NFR-05):** the agreed events contain no text, and C4 stores none.
- **Isolation [K14]:** every query filters by `org_id`, and the block list is per org.
- **Audit:** every block and unblock is stored in `admin_actions`, with who, when and why.
- **Network:** C4 is internal only; the browser never calls it directly.

## 13. Repository structure

### 13.1 What existed [Prototype]
The prototype is a single React app (`MediGuardAI/`: `src/views/{Landing,Onboarding,Clinical,Admin}.tsx`). It moves into the monorepo as `apps/web/` (see `PROJECT_BLUEPRINT.md` §11), and the admin risk screens are split out of `Admin.tsx` into `apps/web/src/features/risk/`.

### 13.2 C4 in the team monorepo

```text
components/c4-risk-profiler/
├── src/c4/
│   ├── main.py
│   ├── config.py                 # pydantic-settings (C4_* + MG_* env vars)
│   ├── api/
│   │   ├── events.py             # POST /v1/events/c1|c2|c3   (FR-01, M1)
│   │   ├── users.py              # GET /v1/users, /v1/users/{id}/profile, /timeline   (FR-02, FR-05, FR-06)
│   │   ├── actions.py            # POST /v1/users/{id}/block|unblock   (Section 3)
│   │   ├── blocklist.py          # GET /v1/blocklist   (read by C1)
│   │   └── health.py
│   ├── core/
│   │   ├── features.py           # M3
│   │   ├── rules.py              # M4, R1 to R7 from rules.yaml (NFR-08)
│   │   ├── decay.py              # M5
│   │   ├── classify.py           # M6, score bounding + P1 to P4
│   │   └── recompute.py          # deterministic profile rebuild from rule hits
│   └── db/{mongo.py, collections.py, indexes.py}
├── configs/rules.yaml
├── scripts/event_generator.py    # 7 scenarios → agreed events through /v1/events/*
├── evaluation/{baseline.py, scenarios/, ablation.py, metrics.py}
├── tests/{unit,integration,contract,security,performance}/
├── Dockerfile                    # python:3.11-slim, copies packages/mediguard-common
├── docker-compose.dev.yml        # c4 + mongo
├── pyproject.toml  .env.example  README.md

apps/web/src/features/risk/       # risk admin UI (owner: Sandul)
├── pages/{RiskDashboard.tsx, UserRiskProfile.tsx, EventRuleTimeline.tsx, AdminActionLog.tsx}
├── components/{PriorityCards.tsx, ManagedUsersTable.tsx, PriorityFilter.tsx, ContributorsList.tsx,
│               BehaviourSummary.tsx, BlockDialog.tsx, SeverityBadge.tsx}
├── api.ts                        # calls /api/admin/risk/* on the gateway only
└── types.ts                      # generated from the gateway OpenAPI
```

### 13.3 C4 API

| Method | Path | Caller | Returns |
|---|---|---|---|
| POST | `/v1/events/c1`, `/v1/events/c2`, `/v1/events/c3` | C1, C2, C3 | `202 {accepted, request_counted}` / `200 {duplicate}` / `422` / `403` |
| GET | `/v1/blocklist?org_id=` | C1 | `{org_id, user_ids, generated_at}` |
| GET | `/v1/users?org_id=&priority=&status=&q=` | Gateway | summary (total, P1 to P4, blocked) + users sorted by score |
| GET | `/v1/users/{user_id}/profile?org_id=` | Gateway | RiskProfile (7.2) |
| GET | `/v1/users/{user_id}/timeline?org_id=&limit=` | Gateway | events with source label, verdict fields, rules fired, impact, `score_after` |
| POST | `/v1/users/{user_id}/block`, `/unblock` | Gateway | `{org_id, by, reason}` → profile |
| GET | `/health` | Compose | Mongo status |

### 13.4 MongoDB (database `c4`)

| Collection | Key fields | Indexes |
|---|---|---|
| `security_events` | `event_id`, `source` (from endpoint), agreed fields, `received_at` | unique `event_id`; (`org_id`, `user_id`, `event_timestamp`); `request_id` |
| `rule_hits` | `org_id`, `user_id`, `event_id`, `request_id`, `session_id`, `rule_id`, `impact`, `event_timestamp`, `ruleset_version` | (`org_id`, `user_id`, `event_timestamp`) |
| `user_behaviour_features` | hour histogram, per-session suspicious counts, counters, masking stats | unique (`org_id`, `user_id`) |
| `user_risk_profiles` | 7.2 | unique (`org_id`, `user_id`); (`org_id`, `score`) |
| `admin_actions` | `org_id`, `user_id`, `action`, `by`, `reason`, `at` | (`org_id`, `user_id`, `at`) |

### 13.5 Configuration

```yaml
# configs/rules.yaml
ruleset_version: rules-v1
half_life_hours: 72
window_days: 30
rules:
  R1: {impact: 10}
  R2: {impact: 15, c2_score: 0.90, leak_count: 3}
  R3: {impact: 15, count: 3, window_minutes: 30}
  R4: {impact: 15}
  R5: {impact: 20}
  R6: {impact: 10, min_share: 0.05, min_requests: 20}
  R7: {impact: 10, sessions: 3}
priorities: {P1: 80, P2: 60, P3: 30}
```

| Variable | Purpose |
|---|---|
| `MG_SERVICE_NAME=c4`, `MG_ACCEPTED_KEYS=c1:…,c2:…,c3:…,gateway:…` | Caller identity per key (contracts §5) |
| `C4_MONGO_URL` | `mongodb://…/c4` (`mongomock://` in tests) |
| `C4_RULES_FILE` | `configs/rules.yaml` |

## 14. Testing & evaluation: unchanged from proposal [Proposal, Sections 3.4/3.5, Appendix D]

**7 controlled behavioural scenarios**, fixed before evaluation so outcomes aren't tuned after the fact:

| Scenario | Pattern | Expected C4 behaviour |
|---|---|---|
| Benign clinician | Normal requests, occasional legitimate sensitive-data handling | Remains mostly P4 / Normal |
| Careless power-user | Frequent low-severity findings, no malicious pattern | Avoids unnecessary escalation (tests false-restriction control) |
| Curious tester | A few suspicious probes, then normal use | May enter P3 temporarily; shouldn't stay elevated without continued evidence |
| Gradual escalator | Risk indicators increase over several sessions | Risk rises progressively |
| Burst attacker | Many suspicious events in a short period | Rapid movement to P1 |
| Slow-and-low | Suspicious events deliberately spaced apart | Tests whether decay lets risk disappear too easily |
| Compromised account | Normal history, then an abrupt change | Risk increases promptly after the change |

**7 evaluation metrics:** classification accuracy (vs. baseline), confusion matrix (P1 to P4), detection lead time, false-restriction rate, explainability coverage (target 100%), feature ablation, processing latency (p50/p95).

**Integration and contract tests (added):**

| ID | Case | Expected |
|---|---|---|
| C4-U-01 | Same `event_id` twice | stored once, `duplicate: true` |
| C4-U-02 | C1 + C2 + C3 events with one `request_id` | request count +1 |
| C4-U-03 | C2 PASS, C3 CLEAN, any C1 masking % | 0 points |
| C4-U-04 | C3 VIOLATION with 3 leaked items | R4 + R5 + R2 = 50 raw |
| C4-U-05 | Decay at one half-life | half the points |
| C4-U-06 | R6 before 20 requests | does not fire |
| C4-I-01 | Block, then `/v1/blocklist` | user listed at once; C1 refuses within 30 s |
| C4-C-01 | Valid and invalid samples in `contracts/examples` | accepted / `422` |
| C4-S-01 | C2's key on `/v1/events/c3` | `403` |
| C4-P-01 | 10,000-event replay | p95 profile update ≤ 2 s |

The **careless power-user** scenario now has a concrete signal. That user sends many C1 events with a high `masking_percentage` and no suspicious verdicts. The expected result is that they stay P4, which is the reason K11 keeps masking out of the score for now.

**Validation discipline [Proposal, 3.5]:** tuning subset vs. held-out evaluation subset, weights/decay frozen before final evaluation, reproducibility check (same event stream → same score), duplicate-event injection test.

---

## 15. Clarifications: status

| # | Item | Status |
|---|---|---|
| 1 | "Phrase Database" in the prototype | **Closed [K21]:** removed |
| 2 | Behavioural dataset scale | Open by design; confirm after generator throughput is tested (Week 5) |
| 3 | Dashboard authentication/RBAC approach | **Closed [K4]:** gateway JWT + roles |
| 4 | Where `department` / `role` / `firstSeen` live | **Closed:** gateway joins user data; C4 computes `first_seen` |
| 5 | Event contract | **Closed [K7, K9, email]:** Section 7.1 |
| 6 | C1 events in rules | **Closed [K11]:** stored and shown, no rule yet |
| 7 | Final half-life and thresholds | Yours; tuned on the tuning subset, frozen at G5 |

## 16. Implementation plan (24-week plan, shared gates) [Agreed K26]

| Week | Tasks | Done when |
|---|---|---|
| 1 to 2 (G0) | Apply this revision; read `contracts/` | Scope accepted |
| 3 to 4 | MongoDB + collections + indexes; `/v1/events/c1,c2,c3` with validation, dedup, caller-key check; contract tests | Duplicate and wrong-key tests pass |
| 4 to 5 | `scripts/event_generator.py` emitting the agreed events for the 7 scenarios (seeded) | Streams reproducible |
| 5 to 7 | Features, R1 to R7, decay, score, priority, rule hits, recompute; `rules.yaml` | Worked example (raw 40) reproduced |
| 7 | Fixed event-count baseline scorer | Baseline runs on scenarios |
| 8 (G2) | Sign the contracts (you consume all three events) | Signed |
| 8 to 10 (G3) | Admin endpoints + block list; risk UI pages wired to the gateway (or a gateway stub) | **Demo:** generator → P1 → admin blocks → `/v1/blocklist` lists the user |
| 10 to 12 | Event & Rule Timeline screen (rule fired, running score); Managed Users table with filters; admin action log; remove Phrase Database panel | All Appendix E screens live |
| 12 (G4) | Freeze scenario streams (tuning vs held-out), hashed | Tag `c4-frozen-v1` |
| 13 (G5) | Tune half-life and thresholds on the tuning subset only; freeze `rules-v1` | Frozen |
| 14 to 16 | Real events from C1, C2, C3 on the middleware host; C1 enforces a real block | End-to-end block loop works |
| 14 to 18 | Security (per-caller keys, admin role via gateway, no text stored); performance (thousands of events) | Checklist done |
| 17 to 20 | 7 metrics on the held-out set (Section 14) | Results with intervals |
| 21 to 24 | Report, viva | Submitted |

**Developing alone:**

```bash
cd components/c4-risk-profiler
docker compose -f docker-compose.dev.yml up          # c4 + mongo
python scripts/event_generator.py --scenario burst_attacker --org org_001 --user user_009 --url http://localhost:8004
curl -s "localhost:8004/v1/users/user_009/profile?org_id=org_001" -H "X-Service-Key: $GW_KEY"
```

The generator posts exactly the agreed payloads, so the C4 tests double as contract checks for C1, C2 and C3.

## 17. Viva quick-reference

- **"What does C4 actually do that C1 to C3 don't?"**: aggregates each user's security evidence *across sessions* into one explainable score; C1 to C3 each evaluate a single request/response in isolation.
- **"Why no machine learning?"**: deliberate design choice (Appendix F decision log, Sep 2026): explainability was prioritised over marginal accuracy gains, consistent with the explainable-AI literature cited in the proposal (RuDi, [14]).
- **"Who actually blocks a user?"**: C4 recommends via the admin dashboard when a user reaches P1; a human administrator confirms the block; C1 is the layer that then enforces it on future requests (Section 3, resolved).
- **"What's built vs. what's design?"**: the admin dashboard UI (all screens, navigation, the block/unblock interaction) is built and demoable; the scoring backend (feature extraction, rule engine, decay, persistence, evaluation) is fully designed per the proposal but not yet implemented, see the coverage map (Section 6) and checklist (Section 16).
- **"Why does every component send an event, even for normal requests?"**: C4 needs normal activity to learn each user's usual hours and request rate (R6). Only verdicts add points, and requests are counted once by `request_id`.
- **"What if a component crashes, does the user get blamed?"**: No. C2's fail-safe mode and C3's scanner errors send no event, so outages never add risk.
- **"How do you know your score is trustworthy?"**: every non-zero contribution must trace to a specific stored rule + event (NFR-01, the worked example in Appendix C); evaluated against a fixed event-count baseline on 7 pre-defined controlled scenarios so results aren't tuned after the fact.

---

## 18. Change log

| Revision | What changed |
|---|---|
| Integration-aligned revision (Oct 2026) | Agreed per-component events and endpoints (7.1), (org, user) profiles, derived severity for R2, masking stored without a rule, risk UI ownership and API (13), gateway-based auth (12), 24-week plan (16). Prototype-grounded analysis kept |
| Prototype-grounded edition | Rebuilt from the actually-submitted proposal PDF (re-read in full) + first inspection of the `MediGuardAI` prototype. Resolved the enforcement-mechanism question (Section 3). Added prototype coverage map (Section 6), dashboard spec-vs-built comparison (Section 9), and a concrete backend build order (Section 16). |

