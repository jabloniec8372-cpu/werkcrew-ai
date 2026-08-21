"""Deterministic M2 workflow for briefing, assigning, and closing site visits."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date, datetime

from werkcrew_ai.domain import (
    Employee,
    JobRequest,
    PostVisitValidation,
    SiteVisit,
    SiteVisitBrief,
    SiteVisitReport,
    WorkflowState,
)
from werkcrew_ai.planning import AssessmentDecision, JobAssessment, assess_job_request

SITE_ASSESSMENT_SKILL_ID = "site-assessment"


@dataclass(frozen=True, slots=True)
class SiteAssessorAssignment:
    employee: Employee | None
    scheduled_at: datetime | None
    message: str

    @property
    def assigned(self) -> bool:
        return self.employee is not None and self.scheduled_at is not None


def build_site_visit_brief(
    job_request: JobRequest, assessment: JobAssessment
) -> SiteVisitBrief:
    if assessment.decision is not AssessmentDecision.SITE_VISIT_REQUIRED:
        raise ValueError(
            "Brief oględzin można utworzyć tylko dla zlecenia wymagającego oględzin"
        )

    verification_items = tuple(
        requirement.description
        for requirement in job_request.requirements
        if requirement.requires_site_verification
    )
    checkpoints = (
        "Zapisz rzeczywiste wymiary oraz ilości dla niepotwierdzonego zakresu.",
        "Oceń stan podłoża po demontażu.",
        "Sprawdź źródło i zakres ewentualnej wilgoci.",
        "Potwierdź dostęp do miejsca pracy i możliwość wniesienia materiałów.",
        "Sprawdź instalacje objęte zakresem oraz potrzebne korekty.",
        *tuple(f"Uzupełnij brak: {item}" for item in assessment.missing_information),
        *tuple(f"Zweryfikuj ryzyko: {item}" for item in assessment.detected_risks),
    )
    return SiteVisitBrief(
        job_request=job_request,
        missing_information=assessment.missing_information,
        detected_risks=assessment.detected_risks,
        verification_items=verification_items,
        checkpoints=tuple(dict.fromkeys(checkpoints)),
    )


def assign_site_assessor(
    employees: tuple[Employee, ...], desired_start_date: date | None
) -> SiteAssessorAssignment:
    if desired_start_date is None:
        return SiteAssessorAssignment(
            employee=None,
            scheduled_at=None,
            message=(
                "Brak planowanej daty rozpoczęcia zlecenia; "
                "nie można dobrać terminu oględzin."
            ),
        )

    qualified = tuple(
        employee
        for employee in employees
        if employee.is_active and SITE_ASSESSMENT_SKILL_ID in employee.skill_ids
    )
    if not qualified:
        return SiteAssessorAssignment(
            employee=None,
            scheduled_at=None,
            message=(
                "Brak aktywnej osoby posiadającej wymagany skill "
                f"{SITE_ASSESSMENT_SKILL_ID}."
            ),
        )

    candidates = [
        (slot.start_at, employee.id, employee)
        for employee in qualified
        for slot in employee.availability
        if slot.is_available and slot.end_at.date() < desired_start_date
    ]
    if not candidates:
        return SiteAssessorAssignment(
            employee=None,
            scheduled_at=None,
            message=(
                "Brak dostępnego terminu osoby z wymaganym skillem przed "
                f"planowanym rozpoczęciem {desired_start_date.isoformat()}."
            ),
        )

    scheduled_at, _, employee = min(candidates, key=lambda candidate: candidate[:2])
    return SiteAssessorAssignment(
        employee=employee,
        scheduled_at=scheduled_at,
        message=(
            f"Przydzielono {employee.name} na {scheduled_at.isoformat()} "
            f"na podstawie aktywności, skillu {SITE_ASSESSMENT_SKILL_ID} i dostępności."
        ),
    )


def create_site_visit(
    job_request: JobRequest,
    assessment: JobAssessment,
    employees: tuple[Employee, ...],
) -> tuple[SiteVisit, SiteAssessorAssignment]:
    brief = build_site_visit_brief(job_request, assessment)
    assignment = assign_site_assessor(employees, job_request.desired_start_date)
    status = (
        WorkflowState.SITE_VISIT_SCHEDULED
        if assignment.assigned
        else WorkflowState.SITE_VISIT_REQUIRED
    )
    return (
        SiteVisit(
            id=f"site-visit-{job_request.id}",
            job_request_id=job_request.id,
            status=status,
            brief=brief,
            scheduled_at=assignment.scheduled_at,
            assigned_employee_id=(
                assignment.employee.id if assignment.employee is not None else None
            ),
        ),
        assignment,
    )


def report_validation_errors(
    job_request: JobRequest, report: SiteVisitReport
) -> tuple[str, ...]:
    errors: list[str] = []
    required_text = (
        ("rzeczywiste wymiary", report.measured_dimensions),
        ("stan podłoża", report.substrate_condition),
        ("wynik kontroli wilgoci", report.moisture_findings),
        ("warunki dostępu", report.access_conditions),
        ("wynik oględzin instalacji", report.installation_findings),
        ("notatka z oględzin", report.notes),
    )
    errors.extend(
        f"Brak pola raportu: {label}"
        for label, value in required_text
        if not value.strip()
    )

    measurements = {
        item.requirement_id: item
        for item in report.measurements
        if item.quantity > 0 and item.unit.strip()
    }
    required_measurement_ids = {
        requirement.id
        for requirement in job_request.requirements
        if requirement.quantity is None
        or not requirement.is_confirmed
        or requirement.requires_site_verification
    }
    for requirement in job_request.requirements:
        if (
            requirement.id in required_measurement_ids
            and requirement.id not in measurements
        ):
            errors.append(f"Brak pomiaru dla wymagania: {requirement.description}")

    return tuple(sorted(set(errors)))


def apply_site_visit_report(
    job_request: JobRequest, report: SiteVisitReport
) -> JobRequest:
    measurements = {
        item.requirement_id: item
        for item in report.measurements
        if item.quantity > 0 and item.unit.strip()
    }
    updated_requirements = tuple(
        replace(
            requirement,
            quantity=measurements[requirement.id].quantity,
            unit=measurements[requirement.id].unit,
            is_confirmed=True,
            requires_site_verification=False,
        )
        if requirement.id in measurements
        else requirement
        for requirement in job_request.requirements
    )

    report_errors = report_validation_errors(job_request, report)
    report_is_complete = not report_errors
    unresolved_risks = job_request.reported_risks
    if report.unresolved_risk:
        detail = (
            report.unresolved_risk_details.strip()
            or "Nierozwiązane ryzyko bez opisu"
        )
        unresolved_risks = tuple(sorted(set((*unresolved_risks, detail))))
    elif report_is_complete:
        unresolved_risks = ()

    return replace(
        job_request,
        requirements=updated_requirements,
        missing_information=(
            () if report_is_complete else job_request.missing_information
        ),
        reported_risks=unresolved_risks,
    )


def validate_after_site_visit(
    job_request: JobRequest, report: SiteVisitReport
) -> PostVisitValidation:
    report_errors = report_validation_errors(job_request, report)
    updated_job_request = apply_site_visit_report(job_request, report)
    updated_assessment = assess_job_request(updated_job_request)
    remaining_missing = tuple(
        sorted(set((*report_errors, *updated_assessment.missing_information)))
    )
    unresolved_risks = updated_assessment.detected_risks

    if not remaining_missing and not unresolved_risks:
        state = WorkflowState.READY_FOR_PLANNING
        rationale = (
            "Raport uzupełnił wymagane dane, potwierdził zakres i nie pozostawił "
            "nierozwiązanych ryzyk. Zlecenie jest gotowe do planowania."
        )
    else:
        state = WorkflowState.SITE_VISIT_COMPLETED
        rationale = (
            "Oględziny zakończono, ale raport nadal zawiera braki lub nierozwiązane "
            "ryzyka. Zlecenie nie jest gotowe do planowania."
        )

    return PostVisitValidation(
        workflow_state=state,
        updated_job_request=updated_job_request,
        remaining_missing_information=remaining_missing,
        unresolved_risks=unresolved_risks,
        rationale=rationale,
    )


def complete_site_visit(
    site_visit: SiteVisit,
    original_job_request: JobRequest,
    report: SiteVisitReport,
) -> tuple[SiteVisit, PostVisitValidation]:
    if site_visit.status not in {
        WorkflowState.SITE_VISIT_SCHEDULED,
        WorkflowState.SITE_VISIT_COMPLETED,
    }:
        raise ValueError("Oględziny muszą być przydzielone przed wysłaniem raportu")
    if report.site_visit_id != site_visit.id:
        raise ValueError("Raport dotyczy innego zadania oględzin")

    completed_visit = replace(
        site_visit,
        status=WorkflowState.SITE_VISIT_COMPLETED,
        report=report,
    )
    return completed_visit, validate_after_site_visit(original_job_request, report)
