"""Exact-SKU skill eligibility helpers; no skill families or assistance inference."""

from werkcrew_ai.catalog.models import CrewMember, SkuPlanningProfile


def exact_skill_level(worker: CrewMember, sku: str) -> int:
    matches = tuple(item.level for item in worker.skills if item.sku == sku)
    if len(matches) != 1:
        raise ValueError(
            f"Worker {worker.worker_id} must have exactly one skill entry for {sku}"
        )
    return matches[0]


def is_worker_schedulable(
    worker: CrewMember,
    profile: SkuPlanningProfile,
) -> bool:
    """Level 1 remains informational and never satisfies M8 scheduling."""

    return exact_skill_level(worker, profile.required_skill_key) >= profile.min_skill_level
