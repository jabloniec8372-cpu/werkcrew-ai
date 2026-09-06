# M2 Durable Persistence Checkpoint

- **Status:** IMPLEMENTED AND VERIFIED; M2 PRODUCT/RUNTIME NOT CLOSED
- **Scope:** canonical M2 durable SQLite persistence boundary
- **Migration:** `0006_m2_durable_inbox.sql`

## Implemented boundary

Canonical M2 has a durable SQLite boundary implemented by `M2DurableRepository` on the shared `SqlitePersistence` database. The boundary persists canonical worker-registry, job-execution, plan-day and directive roots together with immutable routing projections.

The implementation provides:

1. a durable input inbox with separate accept and process transactions, stable input identity and conflict recording;
2. deterministic replay from the immutable historical input proof and exact historical preimage;
3. revision-and-hash compare-and-swap checks for every authorized canonical mutation;
4. durable evidence identity, first-owner scope authority and rejected cross-scope attachment protection;
5. an atomic effect outbox whose entries are tied to the completed input and immutable payload;
6. canonical JSON serialization, schema/version checks, checksums and projection-integrity validation;
7. migration and trigger guards against destructive replacement, duplicate insertion and invalid state transitions;
8. restart, concurrency, replay, corruption and adversarial mutation tests.

The pure deterministic M2 reducer remains the sole transition authority. Persistence derives mutation capability from the accepted input and scope, validates the reducer result against that authority, and atomically records the accepted canonical transition. The repository does not grant authority based on reducer output and does not move business rules into storage code.

## Explicitly not implemented

This checkpoint does not wire `M2DurableRepository` into Worker App, FastAPI/UI, Strands or Bedrock. It does not implement an outbox dispatcher, new agent prompts or any M3+ product behavior. Existing demo adapters therefore do not yet exercise this canonical durable boundary end to end.

M2 must not be declared closed at this checkpoint. The next separately authorized closure phase is:

`Worker App / MobileWC -> API -> M2DurableRepository -> reducer -> canonical state -> outbox -> worker directive/response`

## Relationship to frozen contracts

This document records implementation status only. It does not change the semantics frozen in `M2_BEHAVIORAL_CONTRACT_v1.0_FINAL_FREEZE.md` or `M1_M2_BOUNDARY_v1.0_FINAL_FREEZE.md`. Any older implementation sequence in freeze-era documentation is historical context, not evidence that runtime integration has been completed.
