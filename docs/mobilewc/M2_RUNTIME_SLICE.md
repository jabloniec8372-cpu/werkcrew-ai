# GEN2 M2 Plan Activation Runtime Slice

- **Status:** IMPLEMENTED FIRST RUNTIME ATOM
- **Checkpoint base:** `9d497b7 feat(m2): add durable canonical persistence boundary`
- **Scope:** `DAY_PLAN_ACTIVATED` only

## Proven path

The first narrow runtime path is:

```text
MobileWC-style HTTP input
-> FastAPI transport validation and controlled identity binding
-> M2DurableRepository.execute()
-> durable accept_input / process_input
-> deterministic reducer
-> canonical PlanDayRoot mutation
-> durable receipt and RECORDED outbox effect
-> synchronous HTTP response
```

`POST /api/m2/field-events/day-plan-activated` accepts only the frozen
`DAY_PLAN_ACTIVATED` event shape. A legal activation changes an existing
`PlanDayRoot` from `DRAFT` or `ISSUED` to `ACTIVE` and increments its revision
exactly once. The reducer remains the only authority for the outcome, root
transition and effects. The HTTP adapter does not reproduce reducer rules.

The device creates `event_id` once and retains it for retry. The server creates
the separate `server_event_id`; the client cannot supply it. An identical retry
returns the original persisted `server_event_id` and `response_effects` with
`replayed=true`, without a second root mutation, receipt or outbox effect. A
changed canonical payload under the same `event_id` is a durable conflict.

The fresh-process durable HTTP restart/retry proof sends the first request in
process A, terminates that process, then starts process B against the same SQLite
database and retries the identical device `event_id`. The second response keeps
the original `server_event_id`, reports `replayed=true`, and the database still
contains exactly one canonical mutation, one receipt and one outbox effect. This
is a bounded persistence proof across fresh HTTP processes, not a claim of
production deployment or recovery infrastructure.

## Identity boundary

`X-WERKcrew-Worker-ID` is a controlled identity placeholder. It is explicitly
**not production authentication**. It must match the event `actor_id` exactly,
but it never creates a worker. Canonical `WorkerIdentityRegistry` and the exact
`PlanDayRoot` must already exist. The endpoint performs no fuzzy lookup, current
job inference, worker provisioning or plan-day materialization.

The client cannot provide `server_event_id`, `received_at`, a revision or policy
context. The server generates the event identity used by `FieldEventInput` and
uses one timezone-aware timestamp for both `received_at` and
`PolicyTimeContext.now`.

## Outbox boundary

For an applied first event, the receipt and one `PLAN_DAY_ACTIVATED` effect are
committed atomically with the canonical plan-day root. The outbox status remains
`RECORDED`.

```text
outbox persisted: yes
external delivery: not implemented
```

The synchronous HTTP response is built from
`DurableReductionResult.response_effects`, not `emitted_effects`, so a replay can
return the historical response without re-emitting the effect. The response is
not proof of SMS, push, email or any other external delivery.

## Deliberate exclusions

This slice does not implement:

- any of the other fifteen M2 field events;
- a generic field-event endpoint;
- Worker App or a new HTML/UI screen;
- M1-to-M2 task or plan-day materialization;
- production authentication or authorization infrastructure;
- the complete offline/temporal admission policy;
- an outbox dispatcher, poller, lease or `DISPATCHED` transition;
- SMS, push or email delivery;
- Strands, Bedrock, LLM prompts or agent tools;
- M3 planning, M6 Human Authority or M7 dispatch changes.

The existing Gen1 Field routes and templates remain a regression safety net and
do not use this endpoint. M2 product/runtime is therefore **not closed**.
