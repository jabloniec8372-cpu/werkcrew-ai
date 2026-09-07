# GEN2 M2 Unavailable Today Runtime Slice

- **Status:** IMPLEMENTED SECOND RUNTIME ATOM
- **Checkpoint base:** `8caa256 feat(m2): add durable plan activation API slice`
- **Scope:** `UNAVAILABLE_TODAY_REPORTED` only

## Proven path

The second narrow runtime path is:

```text
MobileWC-style HTTP input
-> strict FastAPI transport validation and controlled identity binding
-> M2DurableRepository.execute()
-> durable accept_input / process_input
-> deterministic reducer
-> canonical PlanDayRoot.worker_available mutation
-> durable receipt and RECORDED outbox effect
-> synchronous HTTP response
```

`POST /api/m2/field-events/unavailable-today-reported` accepts only an explicit
`UNAVAILABLE_TODAY_REPORTED` event with one of the frozen reason classes:
`SICK`, `PERSONAL_EMERGENCY` or `OTHER`. The event never arises from silence,
delay or a start exception. The transport does not infer `SICK` from another
reason.

For an existing canonical worker and that worker's existing `PlanDayRoot`, a
first report changes only `worker_available` from `true` to `false`. The plan
status and `day_close_reported` remain unchanged. The reducer emits exactly one
`UNAVAILABLE_TODAY_RECORDED` effect containing the submitted reason. It does not
mutate a task or assignment and does not emit `TASK_HELD`.

If a different new device `event_id` reports unavailable after
`worker_available` is already `false`, the frozen reducer still returns
`APPLIED`, records a new receipt and effect, but creates no root delta and does
not increment `plan_day_revision`. This is distinct from an identical retry of
the same device event.

For future unavailable events, TX2 also persists an explicit
`m2-reduction-input-proof-v2`. Before reduction it exhaustively enumerates the
exact plan day's assignment links, verifies their immutable routes, loads each
distinct linked job root in stable order, and captures those job-root
preimages with the plan-day preimage. This is event-time historical read
authority for the M2-to-M3 bridge; it does not authorize the unavailable
reducer to mutate jobs, tasks, or assignments. Proof v1 remains replayable by
M2 but is deliberately insufficient for historical bridge reconstruction.

## Identity and replay

The device creates `event_id`; the server creates the separate
`server_event_id`. `X-WERKcrew-Worker-ID` is a controlled identity placeholder,
explicitly **not production authentication**, and must exactly match `actor_id`.
The endpoint does not create a worker or plan day and performs no fuzzy or
global-current-worker lookup.

An identical retry returns the original persisted `server_event_id` and
historical `response_effects` with `replayed=true`. It creates no second root
mutation, receipt or outbox effect. A changed canonical payload under the same
device `event_id`, including a changed reason, is a durable conflict.

The fresh-process proof sends the first HTTP request in process A, terminates
that process, then starts process B against the same SQLite database and retries
the identical device event. Process B returns the original server identity,
`replayed=true`, `worker_available=false`, the same final revision and exactly
one inbox row, receipt and outbox effect.

## Outbox and downstream boundary

The durable effect is recorded as:

```text
effect_type: UNAVAILABLE_TODAY_RECORDED
dispatch_status: RECORDED
external_delivery: NOT_IMPLEMENTED
```

The HTTP response is built from `DurableReductionResult.response_effects`, not
`emitted_effects`. Neither the response nor the persisted outbox row proves
external delivery.

This atom deliberately stops before consuming the effect. It does not calculate
affected jobs, mutate assignments, reassign work or call M3. A future separately
authorized M2-to-M3 bridge may use the durable unavailable fact to request a
fresh feasibility/replan evaluation.

## Deliberate exclusions

This slice does not implement:

- automatic absence or sickness inference;
- task or assignment mutation;
- M3 planning, feasibility or reassignment;
- M6 Human Authority or owner ASK;
- Strands, Bedrock or any LLM transition authority;
- an outbox dispatcher, poller or `DISPATCHED` transition;
- SMS, push, email or other external delivery;
- production authentication;
- a Worker App or MobileWC UI;
- M1-to-M2 materialization;
- the remaining M2 field events.

The pure deterministic reducer remains the only semantic transition and effect
authority. M2 product/runtime remains **not closed**.
