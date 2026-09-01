"""Deterministic normalization for partial client intake."""

from __future__ import annotations

from collections.abc import Iterable, Mapping

from werkcrew_ai.intake.models import (
    CanonicalFactInput,
    CanonicalJobIntake,
    FactKnowledgeState,
    FactVerificationState,
    JobFactName,
)


def _optional_text(value: str | None, field_name: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise TypeError(f"{field_name} must be text or None")
    normalized = value.strip()
    return normalized or None


def normalize_job_intake(
    *,
    intake_source: str,
    raw_text: str | None = None,
    raw_payload: str | None = None,
    client_reference: str | None = None,
    client_name: str | None = None,
    address: str | None = None,
    phone: str | None = None,
    email: str | None = None,
    scope: str | None = None,
    materials: str | None = None,
    preferred_contact_channel: str | None = None,
    explicitly_unknown: Iterable[JobFactName] = (),
    verified_facts: Iterable[JobFactName] = (),
    provenance_by_fact: Mapping[JobFactName, str] | None = None,
) -> CanonicalJobIntake:
    """Normalize explicit facts only; never infer missing values."""

    source = intake_source.strip() if isinstance(intake_source, str) else ""
    if not source:
        raise ValueError("intake_source must be non-blank")
    values = {
        JobFactName.CLIENT_REFERENCE: _optional_text(
            client_reference, "client_reference"
        ),
        JobFactName.CLIENT_NAME: _optional_text(client_name, "client_name"),
        JobFactName.ADDRESS: _optional_text(address, "address"),
        JobFactName.PHONE: _optional_text(phone, "phone"),
        JobFactName.EMAIL: _optional_text(email, "email"),
        JobFactName.SCOPE: _optional_text(scope, "scope"),
        JobFactName.MATERIALS: _optional_text(materials, "materials"),
        JobFactName.PREFERRED_CONTACT_CHANNEL: _optional_text(
            preferred_contact_channel, "preferred_contact_channel"
        ),
    }
    unknown = {JobFactName(item) for item in explicitly_unknown}
    verified = {JobFactName(item) for item in verified_facts}
    known = {name for name, value in values.items() if value is not None}
    if unknown & known:
        names = ", ".join(sorted(item.value for item in unknown & known))
        raise ValueError(f"Facts cannot be both known and unknown: {names}")
    if verified - known:
        names = ", ".join(sorted(item.value for item in verified - known))
        raise ValueError(f"Only known facts can be verified: {names}")

    provenance = dict(provenance_by_fact or {})
    recorded_names = known | unknown
    if set(provenance) - recorded_names:
        raise ValueError("Provenance cannot be attached to an absent fact")

    facts: list[CanonicalFactInput] = []
    for name in JobFactName:
        if name not in recorded_names:
            continue
        facts.append(
            CanonicalFactInput(
                name=name,
                value=values[name],
                knowledge_state=(
                    FactKnowledgeState.KNOWN
                    if name in known
                    else FactKnowledgeState.UNKNOWN
                ),
                verification_state=(
                    FactVerificationState.VERIFIED
                    if name in verified
                    else FactVerificationState.UNVERIFIED
                ),
                provenance_source=provenance.get(name, source),
            )
        )
    return CanonicalJobIntake(
        intake_source=source,
        raw_text=raw_text,
        raw_payload=raw_payload,
        facts=tuple(facts),
    )
