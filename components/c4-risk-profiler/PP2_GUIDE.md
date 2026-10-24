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
| Automatic tests (unit, integration, contract, security, speed, dashboard) | 66 passing |
| Risk dashboard in the browser (overview, user page, access control, scoring rules, demo tools) | Done (served by C4, demo version) |
| Block enforcement: block list published for C1; simulated gate shows a blocked user refused (HTTP 403) | Done for the demo. Real enforcement is inside C1 / the gateway (next phase) |
| Real events from C1, C2, C3 | Not started (integration phase) |
| Half-life tuning, full evaluation metrics, 10,000-event test | Not started (weeks 13 to 20) |

## 2. Demo (no commands, about 8 minutes)

Start: double-click `Start_C4_Demo.bat`. The browser opens the dashboard. On the day, always press **Load demo data** first: the data is dated from that moment, and scores fade with time.

1. **Demo tools** page: press **Load demo data**. 7 dummy users, about 1,800 events.
2. **Overview**: 3 users critical, 4 low. The meter ticks mark the priority levels.
3. Open **user_005**. Show the score, "Why this score" (rules and points), the score-over-time line and the event timeline.
4. Press **Block user**, write a reason, confirm. The status changes to Blocked.
5. **Access control**: the user is on the block list. In **Test the gate**, send a request as `user_005`: Blocked, HTTP 403. Send one as `user_001`: Allowed. The audit log shows who blocked and why.
6. **Demo tools**: press **Start attack** for `user_live`. Go to Overview and watch the score climb from Low to Critical in about 8 seconds.
7. **Demo tools, Move time forward**: press +3 days twice. A "Time moved" tag appears and scores fade (user_005 drops from critical to about 70). Press **Back to now**. Only the clock moves, no stored data changes.
8. **Scoring rules**: the 7 rules, the decay curve, the priority levels, all read from `rules.yaml`.
9. MongoDB Compass: database `c4`, five collections.
10. Tests (optional): `python -m pytest` and `python evaluation\run_scenarios.py`.

Honest limits to say if asked: the data is dummy; the gate here is a simulation of C1; real events from C1 to C3 and real enforcement come in the next phase.

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
