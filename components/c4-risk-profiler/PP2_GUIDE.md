# C4 PP2 Guide: what is built, how to demo it, what to say

## 1. Status at PP2

| Area | Status |
|---|---|
| Event receiver for C1, C2, C3 (contract check, duplicates, per-sender keys) | Done |
| MongoDB storage: 5 collections (`security_events`, `rule_hits`, `user_behaviour_features`, `user_risk_profiles`, `admin_actions`) | Done |
| Rules R1 to R7, time decay, score 0 to 100, priority P1 to P4 | Done |
| Explainability: every point traces to a stored rule hit with the score after it | Done |
| Admin endpoints: users list, profile, timeline, block, unblock, action log, block list for C1 | Done |
| Dummy data: 7 behaviour scenarios, seeded and reproducible | Done |
| Fixed event-count baseline and comparison run | Done (first version) |
| Automatic tests (unit, integration, contract, security, speed) | 52 passing |
| Risk dashboard screens | Not started (next phase, needs the gateway) |
| Real events from C1, C2, C3 | Not started (integration phase) |
| Half-life tuning, full evaluation metrics, 10,000-event test | Not started (weeks 13 to 20) |

## 2. Five-minute demo

1. Start MongoDB and C4 (`uvicorn c4.main:app --port 8004`). Open `http://localhost:8004/docs`.
2. Run `python scripts\event_generator.py --all`. It sends about 1,900 dummy events through the real API.
3. `GET /v1/users` (gateway key): 7 users. Three are P1, four are P4. This is the dashboard data.
4. `GET /v1/users/user_005/profile`: burst attacker, score 100, `BLOCK_RECOMMENDED`, top reasons listed.
5. `GET /v1/users/user_005/timeline`: each event with the rule that fired and the score after it.
6. `POST /v1/users/user_005/block` with `{"org_id":"org_001","by":"admin_001","reason":"Burst of attacks"}`.
7. `GET /v1/blocklist?org_id=org_001` (c1 key): `user_005` is listed. C1 reads this to refuse the user.
8. `GET /v1/users/user_002/profile`: careless power-user, 480 events, high masking, still P4.
9. Show MongoDB Compass: database `c4`, the five collections, one document of each.
10. Run `python evaluation\run_scenarios.py`: C4 against the event-count baseline.

## 3. What to say (plain version)

- **What C4 does that C1 to C3 do not:** it looks at one user over many sessions. C1 to C3 judge one request or one answer.
- **Why no machine learning:** the score must be explainable. An admin must be able to say why a user is at 84.
- **How the score works:** each rule adds points when it fires. Old points fade with a half-life of 72 hours. The sum is capped at 100.
- **Who blocks:** C4 only recommends at P1. An admin clicks Block. C1 reads the block list and refuses the user.
- **Why every component sends an event, even for normal requests:** C4 learns the user's normal hours (rule R6). Only verdicts add points.
- **If a component crashes:** it sends no event, so outages never count against a user.
- **Careless power-user:** high masking percentage but no verdicts. The score stays 0. This is why masking is stored but not scored (decision K11).

## 4. Results on dummy data (seed 42)

| Scenario | C4 now | Baseline | Comment |
|---|---|---|---|
| benign_clinician | P4 | P4 | correct |
| careless_power_user | P4 | P4 | no false alarm |
| curious_tester | P4 (peak 54.7, P3) | P3 | rose to P3, then decayed |
| gradual_escalator | P1 | P1 | rises over the days |
| burst_attacker | P1 | P1 | baseline only 80 |
| slow_and_low | P4 (14.2) | P1 (100) | decay lets slow misuse fade. This is the test the blueprint names |
| compromised_account | P1 | P3 | C4 reacts to the abrupt change (rules R2, R3, R6); baseline under-reacts |

These are first runs on dummy data, not final evaluation results. Do not quote them as findings.

## 5. Points to confirm with the supervisor and team

1. **Worked example (proposal Appendix C, raw 40).** It lists R1 + R3 + R4 from one C2 block and one C3 violation. With `r3.count = 3` (the blueprint default) R3 needs three suspicious events, so that sequence gives 25, not 40. The test `test_worked_example_raw_40_with_two_event_window` shows 40 with `count = 2`. Decide which value is right.
2. **R7 wording.** "Fires on the first suspicious event of the third rising session" cannot hold literally (a first event is count 1, which cannot exceed an earlier count). Built as: fires once per session, when that session's suspicious count first exceeds the previous session's, and the last three sessions rise strictly.
3. **R3 "once per window".** Built as: after R3 fires, the counting set is cleared, so three new suspicious events are needed to fire again.
4. **R6 hour.** Uses the UTC hour of the event. Confirm whether hospital local time should be used instead.
5. **Baseline definition.** 10 points per suspicious event, no decay, capped at 100. Confirm against the proposal.
6. **`points` in top contributors** are decayed points per rule and are not capped, so their sum can be above the capped score of 100.
7. **Shared library.** C4 has its own small service-key check (`src/c4/auth.py`). Replace it with `packages/mediguard-common` when that exists.
