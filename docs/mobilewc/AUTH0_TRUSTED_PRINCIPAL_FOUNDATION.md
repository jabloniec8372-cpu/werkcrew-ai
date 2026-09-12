# AUTH-0 Trusted Principal and Company Authority Foundation

## Responsibility

AUTH-0 establishes durable identities that later authority evidence may cite.
It does not authenticate an HTTP request, issue company policy, record approval
or consent, evaluate a candidate, decide ACT/ASK/BLOCK, or execute anything.

Deployment/bootstrap provisioning is the explicit v1 root of trust. Access to
`DeploymentAuthorityBootstrapRepository` belongs only to trusted host bootstrap
code. It is not registered as an API dependency or HTTP route. Content
addressing detects substitution; it does not authenticate a caller.

Runtime code receives `TrustedAuthorityRepository`, which has read and binding
operations only. The existing `WorkerPrincipal` header adapter remains a
controlled placeholder and is not promoted to production authentication.

## Company authority root

Schema `company-authority-root-v1` and rule
`auth0-deployment-bootstrap-v1` bind:

- a deployment-provisioned `company_id`;
- an existing immutable `CompanyPlan` ID and provenance reference;
- the deployment identity subject designated as the sole OWNER;
- deployment provisioning provenance;
- the exact canonical M2 worker-registry document, revision and fingerprint.

The repository reuses `CompanyPlan` as the operational company-plan scope. It
does not introduce another planning-company model. `company_id` identifies the
external company authority domain and exists only in AUTH-0 evidence.

The complete worker-registry cut is embedded so historical authority replay is
not reinterpreted against a later mutable registry revision. Root fingerprints
and IDs cover every semantic field and use the repository's canonical JSON and
full SHA-256 identity conventions.

## Trusted principals

Schema `trusted-principal-v1` binds each principal to the exact root fingerprint,
company, CompanyPlan and worker-registry cut.

The OWNER principal is created atomically with its authority root. There is no
general provisioning operation accepting a caller-selected role and no
operation for adding another OWNER. Its deployment identity subject must equal
the subject frozen in the root.

WORKER principals use a separate deployment-only operation whose type is fixed
to `WORKER`. The requested worker ID must occur in the root's exact canonical
registry and in the durable M2 worker-identity table. One subject cannot be
replayed as another worker, and one worker cannot be rebound to another subject
under the same root.

Constructing a `CompanyAuthorityRoot` or `TrustedPrincipal` dataclass does not
make it authoritative. Future consumers must resolve IDs through the read-only
repository and require an exact root/principal/worker binding.

## Persistence and replay

Migration `0011_auth0_trusted_principals.sql` provides immutable, append-only
root and principal tables with canonical JSON/hash checks, exact CompanyPlan and
registry triggers, duplicate/replacement guards and foreign keys. Repository
reads reconstruct full objects, recompute fingerprints/IDs, compare denormalized
metadata, validate CompanyPlan provenance, require exactly one bound OWNER, and
validate every WORKER against the frozen registry.

AUTH-0 identities contain no timestamp, current time, UUID, randomness, policy,
consent, approval, candidate, verdict, ranking, APPLY or HUMAN_TASK state.
