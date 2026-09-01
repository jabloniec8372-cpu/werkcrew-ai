"""Canonical durable M1 intake boundary."""

from werkcrew_ai.intake.models import (
    CanonicalFactInput,
    CanonicalJob,
    CanonicalJobFact,
    CanonicalJobIntake,
    CanonicalJobLifecycle,
    FactKnowledgeState,
    FactVerificationState,
    JobFactName,
)
from werkcrew_ai.intake.normalization import normalize_job_intake
from werkcrew_ai.intake.repository import (
    CanonicalJobNotFoundError,
    CanonicalJobRepository,
    DuplicateCanonicalJobError,
    FactRevisionConflictError,
)

__all__ = [
    "CanonicalFactInput",
    "CanonicalJob",
    "CanonicalJobFact",
    "CanonicalJobIntake",
    "CanonicalJobLifecycle",
    "CanonicalJobNotFoundError",
    "CanonicalJobRepository",
    "DuplicateCanonicalJobError",
    "FactKnowledgeState",
    "FactRevisionConflictError",
    "FactVerificationState",
    "JobFactName",
    "normalize_job_intake",
]
