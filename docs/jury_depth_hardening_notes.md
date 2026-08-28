# Jury Depth Hardening Notes

> Design / competition notes only. These ideas are **not yet implemented** and must not be presented as current product capabilities.

## Core direction

WERKcrew should deepen the quality of the existing milestones instead of growing by adding unrelated modules.

The competition goal is to demonstrate that one orchestration agent can handle messy, changing, real-world trade-business situations while respecting deterministic business rules, worker skill boundaries, human consent and truthful uncertainty.

A useful rule for future work:

> Every next change should increase the intelligence density, resilience or realism of an existing milestone before creating a new module.

## One agent, role-specific apps

Keep one WERKcrew agent and role-specific application views.

- Customer view: simple questions, photos, proposed appointments, confirmations and status.
- Worker / field app: today's tasks, route, brief, dynamic checklist, measurements, photos, new findings and direct dialogue with the agent.
- Owner / coordinator view: exceptions, risks, decisions, business impact, schedule changes and what the agent is waiting for.

All roles communicate through the same agent. The agent keeps the shared operational context and decides what should happen next.

## Open-ended customer intake

The jury should be able to provide a request that was not pre-scripted.

Example:

- customer uploads a photo of cracked tiles,
- writes only: `Can this be repaired?`

WERKcrew should not require the customer to fill a large form first.

Expected behavior:

1. Recognize the likely work category from text/photo.
2. Separate visible facts from assumptions.
3. Mark cause, exact quantity, substrate condition, materials and other unknowns as unknown unless supported.
4. Create a provisional customer request / scope candidate.
5. Determine which skills are needed.
6. Check workers, availability and operational options.
7. Ask only for information that is actually needed next.

The model may extract/triage, but safety, pricing, scheduling commitments and business decisions remain governed by validated data and deterministic rules.

## Provenance and uncertainty

Every important fact should retain provenance where practical.

Examples:

- `CUSTOMER_STATEMENT`
- `CUSTOMER_ESTIMATE`
- `PHOTO_OBSERVATION`
- `WORKER_MEASUREMENT`
- `WORKER_ESTIMATE`
- `SYSTEM_DERIVED`
- `NOT_PROVIDED`
- `UNVERIFIED`
- `CONFLICTING`

WERKcrew must not silently turn an estimate, image interpretation or worker guess into a confirmed fact.

## Skill-aware field assessment

The agent should know what a worker is qualified to assess.

Example: Anna has tiling skills. A customer shows her cracked tiles. Anna can measure and describe the tile work. The customer then shows mold around a window. Anna says she is not qualified to diagnose it.

Expected behavior:

1. Anna creates `New finding` in the field app.
2. She may attach a photo and short description.
3. WERKcrew identifies the probable work category and required skill(s).
4. If Anna lacks the skill, the system must not turn her into an expert by prompt.
5. The agent can ask Anna to collect safe, neutral facts that do not require specialist judgment, e.g.:
   - wider photo,
   - approximate affected area,
   - customer statement on how long the issue has existed.
6. The finding is routed to a qualified worker / specialist or marked for a dedicated assessment.

This is a general mechanism, not a hard-coded `mold` scenario.

## Dynamic field discovery / scope expansion

A site visit may reveal work that was not part of the original request.

Example flow:

1. Anna arrives for tile assessment.
2. Customer asks about mold around a window.
3. Customer then shows another room with old wallpaper and unknown wall condition.
4. Anna creates separate new findings.
5. WERKcrew determines what Anna can measure and what requires another skill.
6. The agent gives Anna the next safe data-collection instructions.
7. The additional findings become scope candidates, not silently merged confirmed work.
8. Scheduling and pricing are recalculated only when enough facts are available.

Possible domain concepts for future design:

- `FieldFinding`
- `FindingAssessment`
- `FieldInstruction`
- `ScopeCandidate`
- `CapacityProposal`

These should strengthen existing assessment, site-visit, planning, pricing and dispatch workflows rather than becoming a disconnected module.

## Field app should collect facts, not force workers to price jobs

A worker on site should generally provide observations, measurements and professional estimates within their competence, not final commercial pricing.

Example field inputs:

- actual dimensions,
- quantity,
- substrate condition,
- moisture observation,
- access conditions,
- material availability / who supplies material,
- photos,
- worker estimate of effort (`about 1 day`, `1.5 days`, etc.),
- additional skills required,
- unresolved risks.

The pricing engine and company policy should turn verified operational facts into commercial numbers.

## Calendar-aware customer conversation

A field worker may see the relevant part of their future schedule while talking to a customer.

Example:

- Anna estimates approximately 1.5 days of tile work.
- Customer prefers Tuesday.
- Anna's field app shows Tuesday as potentially available.
- Anna records `customer prefers Tuesday` and sends the report.
- WERKcrew re-checks authoritative capacity and dependencies.
- The system may create a provisional capacity hold.
- A customer-facing commitment is made only after the required checks/approvals pass.

Worker estimate and customer preference must not be treated as a guaranteed booking.

## Worker consent and private-life boundaries

A calendar showing that a worker is not assigned does not mean the agent may use the worker's private time.

Example:

- Anna finishes at 16:00.
- A short site assessment is possible after work.
- The agent asks Anna; she declines because of a personal obligation.
- The system accepts the answer and searches for another option.

Another option may be before work on the next day.

Private car usage should also require an explicit policy path and worker consent where applicable. The agent must not infer consent from availability.

## Pre-work routing to assessment

A realistic optimization scenario:

- before workers reach the company base, WERKcrew already knows about a short assessment,
- a worker may agree to visit the customer first,
- the agent can propose the route only after the required consent/policy checks,
- the worker opens the field app on site and receives the prepared brief/checklist.

The worker's job is to collect the requested facts; the agent keeps the larger business process.

## Sudden worker unavailability / injury

A worker should be able to tell the agent naturally:

`I injured my leg and need to go to hospital.`

Expected orchestration behavior:

1. Safety takes precedence over schedule.
2. Do not diagnose or pressure the worker to finish the job.
3. Mark the worker unavailable for new assignments; future return remains unknown until confirmed.
4. Ask only minimal operational safety questions if necessary (e.g. whether the work site is safe to leave).
5. The injured worker should not be responsible for reorganizing customers and colleagues.
6. WERKcrew identifies affected jobs and dependencies.
7. Search for qualified replacement workers.
8. Re-plan downstream work, vehicle/material dependencies and customer communication.
9. If no safe replacement exists, reschedule truthfully instead of inventing capacity.

This should harden dispatch/orchestration, not become a separate `accidents` module.

## Cross-milestone hardening map

### M1 — Assessment / intake

Strengthen with:

- unstructured customer text,
- very short requests,
- image-assisted intake,
- missing/conflicting facts,
- provenance,
- fail-closed uncertainty.

### M2 — Site visit / field work

Strengthen with:

- dynamic field findings,
- worker skill boundaries,
- agent-generated next questions/checkpoints,
- measurements/photos,
- new scope candidates,
- worker effort estimates as evidence, not final truth.

### M3 — Planning

Strengthen with:

- customer preferred windows,
- worker estimates,
- dynamic new scope,
- multi-skill dependencies,
- authoritative re-check before commitment,
- provisional holds / race awareness.

### M5 — Pricing

Strengthen with:

- better verified field inputs,
- explicit blocking on critical unknowns,
- no worker-made commercial price unless policy explicitly allows it,
- distinction between observed scope and unresolved scope.

### M6 — Human-in-the-loop

Strengthen with meaningful consent/decision boundaries:

- private-time request,
- private-car use,
- exceptions from company policy,
- owner commercial decision,
- unresolved risky work.

### M7 — Dispatch

Strengthen with:

- sudden worker unavailability,
- injury / illness,
- replacement search by skill,
- cascading schedule effects,
- route/vehicle/material consequences,
- field-discovered new work,
- no false promises when capacity disappears.

### M8 / Jury Lab

Use the lab to attack the existing system with unprepared situations rather than to showcase many isolated features.

Examples for adversarial jury testing:

- arbitrary customer text,
- arbitrary uploaded trade-related photo,
- conflicting quantities,
- customer asks to skip required assessment,
- worker discovers work outside their skill,
- customer adds a second and third scope item during a visit,
- requested slot becomes unavailable while the field report is being completed,
- no qualified worker exists,
- worker declines an out-of-hours request,
- private car is unavailable / consent denied,
- worker suddenly becomes ill or injured,
- no same-day replacement exists.

## Competition narrative

The strongest story is not:

`prepared JSON -> agent -> schedule`

It is:

`messy real-world event -> WERKcrew understands what is known -> identifies what is unknown -> finds the right human/skill -> collects evidence -> respects human boundaries -> updates the shared state -> protects the schedule/business rules -> drives the next action`

The agent should look less like a chatbot and more like a persistent operational coordinator that knows when to act, when to ask, when to stop and who is qualified to decide.
