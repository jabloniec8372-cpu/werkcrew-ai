# M2 Freeze Record

**Artifact:** M2 / MobileWC Behavioral Contract v1.0  
**Status:** FINAL FREEZE  
**Date:** 2026-08-31

## Decision

M2 / MobileWC behavioral semantics are frozen. The prior A–L uncertainty list is closed. Further product brainstorming must not reopen A–L unless implementation or deterministic contract tests expose a real contradiction.

## Final deltas before freeze

1. `START_DELAY_REPORTED` and `START_EXCEPTION_REPORTED` are disjoint; `DELAY` is not a `START_EXCEPTION` reason.
2. `WORKER_ACTION_EXCEPTION` uses the last **safe, acknowledged** state; STOP/safety exceptions enter persistent `SAFE_HOLD / BLOCKED_PENDING_RESOLUTION`.
3. `DAY_CLOSE_REPORTED` is worker intent; `DAY_CLOSED` follows only after end-of-day policy is satisfied.
4. Test S7 covers retry of the **same `event_id`**, not two user taps.
5. Test S31 guarantees `SAFE_HOLD` survives restart, cold start, sync and day rollover until explicit authorized resolution.

## Out of M2

- transport profile / private vehicle / mileage reimbursement -> M3
- Estimated vs Actual numeric thresholds -> M3/M4 policy
- SMS/phone fallback execution and orchestration -> M4
- HUMAN_DECISION authority -> M4/M6
- visual MobileWC screens -> after tests + event models/backend contract

## Next sequence

1. Implement deterministic Given/When/Then contract tests.
2. Green critical suite.
3. Define event models / JSON Schema / TypeSpec.
4. Implement backend contract.
5. Build thin MobileWC UI from stable contract.
6. Resume M3 product work.
