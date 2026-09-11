# M3-C — Bounded feasibility and candidate generation

## Responsibility and boundary

M3-C answers which hypothetical, technically legal repair candidates exist in
the M3 v0.1 envelope. `M3BoundedFeasibilityService` accepts only an
`M3EvaluationInput` ID and a `FeasibilitySupportSnapshot` ID, reloads both
immutable records through the existing repository, and delegates to the pure
`generate_bounded_repair_candidates` function. The function revalidates both
canonical documents and requires their EvaluationInput, CompanyPlan and exact
PlanRevision bindings to match.

The evaluator never rereads worker registry, availability, readiness, task/M8,
schedule, vehicle, route, window, deadline or dependency sources. C0 is the
complete feasibility evidence cut. Missing captured knowledge stays UNKNOWN and
cannot pass a technical gate.

Candidate results are content-addressed deterministic recomputation. The
committed architecture does not require durable M3-C results and correctness
does not depend on storing them, so no migration `0010` is added. The persisted
M3-B and C0 source rows remain the replay authority.

## Immutable model

Schema `m3-bounded-repair-candidate-v1`, result schema
`m3-bounded-feasibility-result-v1`, and rule
`m3-bounded-feasibility-v1` cover:

- both source IDs and fingerprints;
- CompanyPlan and exact base PlanRevision identity;
- one to three modified commitment IDs and exact job/task identities;
- proposed single-worker, canonical-vehicle and UTC interval placements with a
  Europe/Berlin business date;
- affected explicit `FINISH_BEFORE_START` edges;
- preserved direct and dependency impact, newly derived candidate-induced
  impact, restored impact, and residual unresolved impact;
- every technical constraint result, a filtered deterministic rejection trace,
  and later-authority impact facts;
- non-ranking technical tie-break evidence: restored count, protected known
  windows, modified count, and captured travel-plus-buffer seconds.

Candidate and result fingerprints are SHA-256 over canonical semantic JSON.
Stable IDs use the full digest. There is no clock, UUID, randomness, Python
`hash()`, SQL row order, LLM output, cost, or scalar business ranking.

## Search envelope and exact enumeration

The horizon is local calendar dates `D`, `D+1`, and `D+2` in
`Europe/Berlin`; the end is exclusive local midnight at `D+3`. Only commitments
proven SINGLE by exact historical assignment evidence may be autonomously
modified. A multi-worker placement or historical CREW kind produces
`CREW_REPAIR_UNSUPPORTED`; absent SINGLE proof produces
`SINGLE_KIND_EVIDENCE_UNKNOWN` and insufficient evidence.

Primary seeds use this exact order:

1. directly impacted commitment by `(job_id, task_id, commitment_id)`;
2. worker by exact `worker_id`, forming one canonical seed pair;
3. for the reached pair only, original start anchor first and remaining UTC
   anchors in ascending order;
4. for each anchor, exact eligible vehicle ID in lexical order (`None` is the
   sole option when no vehicle is required).

Anchors come only from captured event points: the base interval; worker-window
start and end-minus-duration; customer-window start and end-minus-duration;
explicit predecessor end and successor start-minus-duration; and the end of a
conflict-visible placement, both raw and plus a known captured route/buffer.
Vehicle IDs are exact and lexical. `MAX_SEED_PAIR_PROBES = 13` is a separate,
hard pre-evaluation budget: `_anchor_starts` can be called at most 13 times for
one search, irrespective of the direct-commitment-by-worker Cartesian size. Each
call returns at most `MAX_ANCHORS_PER_SEED_PAIR = 13` anchors. Pairs and their
anchors are streamed; later pairs are neither probed nor materialized. The
iterator knows from its indices whether the current pair has unconsumed anchors
or canonical pairs remain, so it does not scan the tail merely to prove
truncation. Reaching the pair-probe limit with pairs left records
`SEARCH_ENVELOPE_LIMIT` and search exhaustion.

Drafts are not candidate evidence. The exact 12-candidate guard occurs
immediately before `_evaluate_draft`; only a draft that reaches that function
and receives `FEASIBLE` or `REJECTED` counts. Seed drafts and expansions consume
that same total budget. No Cartesian product is materialized.

After a rejection, one non-branching expansion pipeline inspects repair classes
in this exact order: first overlap, then dependency. The first overlap counterpart
by commitment ID is moved after the latest proposed interval plus captured route
and buffer. If no overlap repair applies, the first violated explicit
`FINISH_BEFORE_START` edge by `(predecessor_task_id, successor_task_id)` is used.
The affected counterpart keeps its canonical identity, current worker, vehicle,
and duration and is moved to the dependency boundary; a known captured route and
buffer is added only when the dependency endpoints share that worker or vehicle.
The expanded draft is not feasible by construction: it re-enters the ordinary
full technical validator and may expose the next stable overlap or dependency.

The exact three-commitment guard runs before adding any unmodified overlap or
dependency counterpart and again in both candidate validation and immutable
model construction. A required fourth change is not generated, records
`SEARCH_ENVELOPE_LIMIT`, and marks the top-level search envelope exhausted. This
allows only one deterministic chain and never branches across alternative
dependency repairs.

## Technical validation

Validation uses half-open interval overlap (`start < other_end` and
`other_start < end`) against every current active schedule placement, including
placements outside the modified set. This is the only reused Gen1 helper; it is
a pure four-instant predicate whose semantics exactly match the Gen2 interval
contract. No Gen1 DTO, calendar ownership, policy or mutable truth is reused.

For each modified placement M3-C checks:

- exact C0 task SKU → required M8 skill key/minimum → worker actual level;
- full interval containment in proven worker availability;
- captured readiness, where only `READY` passes;
- exact vehicle capability, availability and conflicts when required;
- hard customer-window and deadline facts;
- all candidate-touched worker and vehicle overlaps;
- captured directional routes between consecutive different locations,
  including travel and buffer;
- exact explicit dependency direction and transitive downstream edges.

DONE tasks are absent from the active EvaluationInput scope and are never repair
targets or conflict placements. No dependency is inferred from M2 supersession,
task order, M8 predecessor hints or Gen1 data.

Private vehicle use can be technically feasible but records
`PRIVATE_VEHICLE_AUTHORITY_REQUIRED`; no permission is decided. C0 does not
capture whether an otherwise window-compliant time change specifically requires
new customer consent, so M3-C records only the technical window fact and does
not invent a consent requirement.

## Outcomes

- `FEASIBLE_CANDIDATES_FOUND`: at least one bounded technical candidate passes.
- `ALL_EVALUATED_CANDIDATES_REJECTED`: every actually supported/examined
  candidate was conclusively rejected and no declared envelope bound truncated
  a required or unexplored path.
- `SEARCH_ENVELOPE_EXHAUSTED`: no feasible candidate was found before a
  seed-pair, 12-evaluation, three-modification, or horizon search bound stopped
  further exploration; this is not global impossibility.
- `CREW_REPAIR_UNSUPPORTED`: no direct impact has autonomous SINGLE support and
  at least one is proven CREW.
- `INSUFFICIENT_CRITICAL_EVIDENCE`: UNKNOWN evidence or missing SINGLE proof
  prevents a supported conclusion.
- `NO_REPAIR_REQUIRED`: the authoritative EvaluationInput has no direct current
  impact.

Outcome precedence is: any feasible candidate; then any declared search-envelope
truncation; then insufficient critical evidence; then CREW-only unsupported;
then conclusive rejection. `NO_REPAIR_REQUIRED` returns before enumeration when
there is no direct impact. `global_solution_status` is always
`NOT_EVALUATED_GLOBALLY`. If feasible candidates exist when a limit is reached,
the outcome remains `FEASIBLE_CANDIDATES_FOUND` and
`search_envelope_exhausted=true` separately reports the truncation. Residual
impact is explicit on every candidate; a partial repair never claims to solve
the incident.

## Explicit non-goals

M3-C does not price with M5, rank business choices, decide M6 authority,
ACT/ASK/BLOCK, apply a PlanRevision, reserve a worker or vehicle, mutate or
publish M2, create a publication or worker directive, contact a human, or invoke
Strands, Bedrock, an LLM, a network route service, or any external side effect.
