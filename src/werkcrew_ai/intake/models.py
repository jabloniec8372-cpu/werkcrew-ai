"""Canonical, partial M1 intake values without inferred business facts."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum


class CanonicalJobLifecycle(StrEnum):
    RECEIVED = "RECEIVED"


class CanonicalJobActivity(StrEnum):
    ACTIVE = "ACTIVE"
    DORMANT = "DORMANT"


class JobFactName(StrEnum):
    CLIENT_REFERENCE = "CLIENT_REFERENCE"
    CLIENT_NAME = "CLIENT_NAME"
    ADDRESS = "ADDRESS"
    PHONE = "PHONE"
    EMAIL = "EMAIL"
    SCOPE = "SCOPE"
    MATERIALS = "MATERIALS"
    PREFERRED_CONTACT_CHANNEL = "PREFERRED_CONTACT_CHANNEL"


class FactKnowledgeState(StrEnum):
    KNOWN = "KNOWN"
    UNKNOWN = "UNKNOWN"


class FactVerificationState(StrEnum):
    UNVERIFIED = "UNVERIFIED"
    VERIFIED = "VERIFIED"


def _canonical_nonblank(value: str, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be non-blank")
    return value.strip()


@dataclass(frozen=True, slots=True)
class CanonicalFactInput:
    name: JobFactName
    value: str | None
    knowledge_state: FactKnowledgeState
    verification_state: FactVerificationState
    provenance_source: str

    def __post_init__(self) -> None:
        source = _canonical_nonblank(
            self.provenance_source, "provenance_source"
        )
        object.__setattr__(self, "provenance_source", source)
        if self.knowledge_state is FactKnowledgeState.KNOWN:
            if self.value is None:
                raise ValueError("KNOWN fact requires a value")
            object.__setattr__(
                self,
                "value",
                _canonical_nonblank(self.value, "fact value"),
            )
        elif self.value is not None:
            raise ValueError("UNKNOWN fact cannot carry a value")
        if (
            self.knowledge_state is FactKnowledgeState.UNKNOWN
            and self.verification_state is FactVerificationState.VERIFIED
        ):
            raise ValueError("UNKNOWN fact cannot be VERIFIED")


@dataclass(frozen=True, slots=True)
class CanonicalJobIntake:
    intake_source: str
    raw_text: str | None
    raw_payload: str | None
    facts: tuple[CanonicalFactInput, ...]

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "intake_source",
            _canonical_nonblank(self.intake_source, "intake_source"),
        )
        names = [fact.name for fact in self.facts]
        if len(names) != len(set(names)):
            raise ValueError("Initial intake cannot contain duplicate facts")


@dataclass(frozen=True, slots=True)
class CanonicalJobFact:
    job_id: str
    name: JobFactName
    revision: int
    value: str | None
    knowledge_state: FactKnowledgeState
    verification_state: FactVerificationState
    provenance_source: str
    recorded_at: datetime
    follow_up_evidence_id: str | None = None


@dataclass(frozen=True, slots=True)
class CanonicalJob:
    job_id: str
    lifecycle_state: CanonicalJobLifecycle
    activity_state: CanonicalJobActivity
    source_revision: int
    created_at: datetime
    updated_at: datetime
    intake_source: str
    raw_text: str | None
    raw_payload: str | None
    facts: tuple[CanonicalJobFact, ...]

    def fact(self, name: JobFactName) -> CanonicalJobFact | None:
        """Return the current recorded fact; absence means not reported."""

        return next((fact for fact in self.facts if fact.name is name), None)
