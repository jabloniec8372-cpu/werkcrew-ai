# GEN2 M2 Worker Acknowledged Runtime Slice

- **Status:** IMPLEMENTED DURABLE HTTP ATOM
- **Checkpoint base:** `8b1889a feat(m2): add historical unavailable bridge boundary`
- **Scope:** `WORKER_ACKNOWLEDGED` only

## Proven path

The runtime path is:

```text
MobileWC-style HTTP input
-> strict FastAPI transport validation and controlled identity binding
-> M2DurableRepository.execute()
-> durable accept_input / process_input
-> deterministic reducer
-> exact canonical DirectiveRoot mutation
-> optional canonical PlanDayRoot confirmation mutation
-> durable receipt and RECORDED outbox effect
-> synchronous HTTP response
```

`POST /api/m2/field-events/worker-acknowledged` accepts one explicit
`WORKER_ACKNOWLEDGED` event naming the exact canonical `directive_id`. An ACK
records that the directive was seen and understood. It is not owner approval,
an unconditional promise to execute, acknowledgement of another directive or
authority to weaken STOP/safe-hold state.

For a first applicable ACK, the existing reducer changes that exact directive's
`delivery_evidence` to `ACKED`, records the device `event_id` as
`acknowledged_event_id` and advances its revision once. The same atomic durable
transaction records one immutable receipt and one `DIRECTIVE_ACKED` effect in
the outbox with `RECORDED` status. External delivery remains
`NOT_IMPLEMENTED`.

## ACTION and plan confirmation

An applicable current `ACTION_REQUIRED` directive may carry a canonical
`proposed_plan_reference`. The reducer, not the HTTP adapter, decides whether
the ACK confirms it. When it does, the exact directive and exact
`PlanDayRoot.confirmed_plan_reference` are updated atomically and each changed
root revision advances once.

A late ACK may still acknowledge its exact historical directive. It does not
replace the confirmed plan selected by a later governing directive and does
not advance the plan revision when the plan is no longer governed by the
acknowledged directive.

## STOP and safe hold

Acknowledging a STOP directive records ACK evidence on that directive but does
not clear `stop_in_force`, remove a task's `SAFE_HOLD`, clear its blocked state,
authorize execution or create plan confirmation. The HTTP layer contains no
special STOP rule; these outcomes come from the canonical reducer and are
committed through the existing repository authority.

## Identity, replay and conflict

The device creates `event_id`; durable identity is
`FIELD_EVENT` plus that exact ID. The server creates the separate
`server_event_id`. `X-WERKcrew-Worker-ID` is a controlled identity placeholder,
explicitly **not production authentication**, and must exactly match
`actor_id`. The canonical directive must belong to that worker.

An identical retry returns the original persisted `server_event_id`, outcome
and historical response effects with `replayed=true`. It creates no second
directive or plan mutation, receipt or outbox effect. Reusing the same
`event_id` with a changed canonical payload produces the existing durable
`M2_INPUT_CONFLICT` response.

A distinct device `event_id` against an already acknowledged directive follows
the frozen reducer behavior: durable `NOOP`, its own immutable receipt, no new
`DIRECTIVE_ACKED` effect and no directive or plan revision change.

Fresh-repository and fresh-process tests reopen the same SQLite database and
prove that ACK state, plan confirmation, the original server identity and the
single receipt/effect survive reconstruction. A retry of an input left in
`RECEIVED` resumes processing under the original durable server claim.

## Fail-closed boundary

Transport-invalid requests and missing or mismatched controlled identity are
rejected before durable acceptance. Unknown directives and directive/actor
mismatches are canonical completed rejections with receipts and no effects.
Missing canonical bootstrap, corrupt canonical data and stale CAS state fail
closed; no rejected or failed transaction leaves partial root mutation,
receipt or outbox effect.

## Deliberate exclusions

This slice does not implement:

- directive discovery, delivery, polling or a `DISPATCHED` transition;
- `APP_OBSERVED` or acknowledgement inference;
- production authentication;
- owner approval or automatic execution commitment;
- Flutter, Worker App or MobileWC screens;
- M1-to-M2 materialization;
- M3 planning or feasibility execution;
- M4, M5, M6 or M7 behavior;
- Strands, Bedrock or LLM transition authority;
- any other M2 field-event endpoint.

M2 product/runtime remains **not closed**.
