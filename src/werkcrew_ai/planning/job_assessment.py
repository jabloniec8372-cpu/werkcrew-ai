"""Deterministic assessment of whether a job can be quoted remotely."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from werkcrew_ai.domain import JobRequest


class AssessmentDecision(StrEnum):
    REMOTE_QUOTE_POSSIBLE = "REMOTE_QUOTE_POSSIBLE"
    SITE_VISIT_REQUIRED = "SITE_VISIT_REQUIRED"


@dataclass(frozen=True, slots=True)
class JobAssessment:
    decision: AssessmentDecision
    missing_information: tuple[str, ...]
    detected_risks: tuple[str, ...]
    rationale: str
    site_visit_required: bool
    peter_attention_required: bool
    rule_version: str = "job-assessment-v1"


def assess_job_request(job_request: JobRequest) -> JobAssessment:
    """Assess quote readiness using only explicit completeness and risk inputs."""

    missing_information = list(job_request.missing_information)
    detected_risks = list(job_request.reported_risks)

    if not job_request.description.strip():
        missing_information.append("Brak opisu zlecenia")
    if not job_request.site_address or not job_request.site_address.strip():
        missing_information.append("Brak adresu realizacji")
    if not job_request.requirements:
        missing_information.append("Brak wymagań zlecenia")

    for requirement in job_request.requirements:
        requirement_name = requirement.description.strip() or requirement.id
        if not requirement.description.strip():
            missing_information.append(f"Brak opisu wymagania: {requirement.id}")
        if requirement.quantity is None or not requirement.unit:
            missing_information.append(f"Brak ilości lub jednostki: {requirement_name}")
        if not requirement.is_confirmed:
            missing_information.append(f"Niepotwierdzony zakres: {requirement_name}")
        if requirement.requires_site_verification:
            detected_risks.append(
                f"Wymaga weryfikacji na miejscu: {requirement_name}"
            )

    missing = tuple(sorted(set(missing_information)))
    risks = tuple(sorted(set(detected_risks)))
    site_visit_required = bool(missing or risks)

    if site_visit_required:
        decision = AssessmentDecision.SITE_VISIT_REQUIRED
        rationale = (
            "Nie można przygotować pewnej zdalnej wyceny, ponieważ zlecenie "
            "zawiera braki lub ryzyka wymagające weryfikacji na miejscu."
        )
    else:
        decision = AssessmentDecision.REMOTE_QUOTE_POSSIBLE
        rationale = (
            "Zakres zawiera komplet podstawowych danych i nie zgłoszono ryzyk "
            "wymagających oględzin; można przejść do przygotowania zdalnej wyceny."
        )

    return JobAssessment(
        decision=decision,
        missing_information=missing,
        detected_risks=risks,
        rationale=rationale,
        site_visit_required=site_visit_required,
        peter_attention_required=site_visit_required,
    )
