# M3-E0 Authoritative Policy and Consent Foundation

## Responsibility and trust boundary

M3-E0 records immutable authority evidence for the OWNER-approved Company
Policy Profile v1, its issuance, scoped OWNER approvals, and scoped worker
consent. It does not evaluate M3-C candidates, compare M3-D consequences,
rank or select candidates, produce an operational verdict, mutate a plan,
create a human task, or apply anything.

The atom reuses AUTH-0 `CompanyAuthorityRoot`, `TrustedPrincipal`,
`PrincipalType`, and repository validation. AUTH-0 proves the durable identity
and company binding of an OWNER or WORKER; it does not prove that the human
performed a particular approval or consent action. Constructing an M3-E0
dataclass is not authority.

Human approval and consent are accepted only through
`DeploymentPolicyEvidenceRecorder`, an internal deployment-held capture
capability requiring a 32-byte Ed25519 private signing key. The class is not
exported from `werkcrew_ai.authority` or from the recording module's public
surface. The deployment/bootstrap boundary first provisions an immutable
`HumanActionCaptureRoot` for the exact AUTH-0 root. It binds the public
verification key to that company/root and cannot be replaced by a recorder
holding a different key. Merely possessing the database path, authority IDs,
principal IDs, worker ID and scope is therefore insufficient to create
evidence that a runtime reader will trust.

`PolicyEvidenceRepository` reconstructs that capture root and obtains its
Ed25519 public verification key from the durable, AUTH-0-bound trust cut; the
reader does not accept a caller-selected verification key. The private key is
not stored in the database and is not given to the runtime reader. Each signed
action also binds the exact capture-root ID/fingerprint. The signature attests
that the exact action, principal, AUTH-0 root, scope, source capture reference,
authoritative event timestamp and consent lineage were accepted by the trusted
human-action ingress. Deployment/bootstrap provisioning plus custody of the
private key is the explicit v1 trust anchor for captured human acts; it is not
request/session authentication. An AUTH-1 adapter may later authenticate a
session and feed that ingress, but no such adapter or HTTP boundary exists in
M3-E0.

## Company Policy Profile v1

Schema `company-policy-profile-v1` and rule
`owner-company-policy-profile-v1` bind the exact AUTH-0 company/root and
CompanyPlan provenance to the policy frozen in
`docs/decisions/0013-owner-company-policy-profile-v1-freeze.md`:

- P-ASSIGN is `CONSTRAINED_AUTO`, conditional on every other applicable gate.
- P-WINDOW-01 preserves M3-C rejection for movement outside the confirmed
  window. An intra-window slot shift is not a separate v1 authority gate.
  Required `UNKNOWN` or `ABSENT` window knowledge never grants authority.
- P-COST is an inclusive `50.00 EUR` maximum additional internal labor cost;
  M3-D must be `COMPLETE`, unknown is not zero, and zero or negative deltas may
  pass this policy gate.
- P-OVERTIME is `ASK_ALWAYS`; missing evidence is not inferred.
- P-VEHICLE requires exact OWNER approval and `FREELY_GIVEN` worker consent.
- P-TAG treats `OWNER_APPROVAL_REQUIRED` as `ASK_ALWAYS` / `SOFT_EXCEPTION`
  and never as permission to override a hard, safety, legal, qualification,
  consent, or feasibility boundary.
- P-HORIZON is `INCIDENT_DAY_ONLY`; the M3-C `D..D+2` technical envelope does
  not grant later-day authority.
- P-SEARCH preserves `SEARCH_ENVELOPE_EXHAUSTED`; without a feasible candidate
  its later authority interpretation is `ABSTAIN` with
  `ENVELOPE_EXHAUSTED`, without claiming global impossibility. Exhaustion alone
  does not block or penalize an existing feasible candidate.

The policy payload is fixed by code rather than accepted as an input. It uses
the canonical monetary string `"50.00"` in `EUR`, never a float. Every policy
value participates in canonical JSON, the SHA-256 profile fingerprint, and the
content-addressed profile ID.

## Issuance, approval, and consent evidence

`PolicyIssuanceEvidence` binds the exact profile ID/fingerprint to the exact
AUTH-0 root and that root's sole OWNER principal. A stored profile without a
valid issuance resolves deterministically to `POLICY_UNSIGNED`; no fallback
profile or autonomous permission is implied.

`OwnerApprovalEvidence` binds the exact OWNER and root to an
`AuthorityEvidenceScope` and to a verified `HumanActionProvenance` receipt.
The scope content-addresses its kind, subject type, subject ID, operational
date, job ID, and optional worker ID. Consequently an approval cannot be reused
for another subject, job, day, worker, company, or root. Approval evidence does
not imply worker consent and does not encode an override of any hard boundary.

`WorkerConsentEvidence` additionally binds the exact AUTH-0 WORKER principal,
worker ID, and historical worker-registry cut to the same exact scope. Consent
history is an immutable signed chain per exact authority root, WORKER principal,
worker ID and scope. The repository assigns a transactional lineage sequence;
every item after the first signs the exact preceding provenance ID and
fingerprint. This ingestion order, not wall-clock time, defines the current
state deterministically.

Every authoritative approval or consent also consumes one global durable
capture identity: `(capture_key_id, capture_reference)`. The immutable capture
consumption ledger has a database-level unique constraint over that exact pair
and binds it to the signed provenance, AUTH-0 root, action kind and value,
principal, worker where applicable, scope, occurrence time, and evidence
identity. An exact semantic retry returns the original evidence. Reuse for a
different action kind, value, principal, worker, scope, or root fails closed.
The ledger is shared by all human-action recorders so future action kinds do
not depend on pairwise evidence-table checks.

`get_worker_consent()` returns one verified immutable historical record and
validates its complete lineage. `resolve_worker_consent()` returns usable
consent only when the requested record is the latest lineage member and that
member is `FREELY_GIVEN`. A later `REVOKED` record therefore leaves the old
evidence intact but makes it no longer currently usable. Revocation in another
company, root, worker, job, day, vehicle, subject or scope has no effect.
Absent, unknown, requested, declined, pressured, or revoked current states
resolve as no usable consent. M3-E0 records evidence; it does not request
consent.

## Persistence and replay

Migration `0012_m3e0_authoritative_policy_evidence.sql` adds six append-only
SQLite tables with canonical JSON and SHA-256 checks, an immutable human-action
capture root, the global capture-consumption ledger, exact AUTH-0 foreign and
semantic bindings, signed action-provenance columns, consent predecessor-chain
constraints, and triggers rejecting update, delete, duplicate, replacement and
invalid chronology attempts. Capture reservation and evidence insertion occur
inside one `BEGIN IMMEDIATE` transaction. Reads reconstruct complete values,
recompute their fingerprints and IDs, compare denormalized metadata, require
the exact ledger binding, independently reload the referenced roots and
principal records, and verify Ed25519 provenance against the capture root's
bound public key. Recomputing every stored content hash after changing a scope
or ledger interpretation cannot create a valid signature.

The evidence contains an authoritative supplied human-action timestamp and
capture reference, but no process-clock time, UUID, random identity, mutable
policy lookup, candidate, ranking, verdict, APPLY, or HUMAN_TASK state.
Historical replay therefore retains the original authority root, CompanyPlan
provenance, worker-registry cut, policy identity, signed action and scope.
