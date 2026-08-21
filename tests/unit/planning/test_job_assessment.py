from datetime import date
from decimal import Decimal

from werkcrew_ai.domain import JobRequest, JobRequirement
from werkcrew_ai.planning import AssessmentDecision, assess_job_request


def complete_job_request() -> JobRequest:
    return JobRequest(
        id="job-complete-test",
        title="Malowanie pokoju TEST",
        description="Malowanie ścian przygotowanego pokoju.",
        site_address="Teststraße 1, Berlin (TEST)",
        desired_start_date=date(2026, 9, 1),
        requirements=(
            JobRequirement(
                id="req-paint-test",
                description="Malowanie ścian",
                quantity=Decimal("42"),
                unit="m2",
                is_confirmed=True,
                requires_site_verification=False,
            ),
        ),
    )


def test_complete_low_risk_job_allows_remote_quote() -> None:
    result = assess_job_request(complete_job_request())

    assert result.decision is AssessmentDecision.REMOTE_QUOTE_POSSIBLE
    assert result.missing_information == ()
    assert result.detected_risks == ()
    assert result.site_visit_required is False
    assert result.peter_attention_required is False


def test_incomplete_risky_job_requires_site_visit() -> None:
    job_request = JobRequest(
        id="job-risky-test",
        title="Remont łazienki TEST",
        description="Remont łazienki ze śladami wilgoci.",
        site_address="Teststraße 2, Berlin (TEST)",
        desired_start_date=None,
        requirements=(
            JobRequirement(
                id="req-waterproofing-test",
                description="Hydroizolacja",
                quantity=None,
                unit="m2",
                is_confirmed=False,
                requires_site_verification=True,
            ),
        ),
        missing_information=("Brak dokładnych pomiarów",),
        reported_risks=("Możliwa wilgoć w podłożu",),
    )

    result = assess_job_request(job_request)

    assert result.decision is AssessmentDecision.SITE_VISIT_REQUIRED
    assert "Brak dokładnych pomiarów" in result.missing_information
    assert "Możliwa wilgoć w podłożu" in result.detected_risks
    assert result.site_visit_required is True
    assert result.peter_attention_required is True


def test_assessment_is_deterministic() -> None:
    job_request = complete_job_request()

    results = [assess_job_request(job_request) for _ in range(20)]

    assert all(result == results[0] for result in results)
