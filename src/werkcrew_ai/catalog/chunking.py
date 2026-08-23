"""Pure deterministic chunking and conditional-predecessor utilities for M8.0."""

from __future__ import annotations

from decimal import Decimal, ROUND_FLOOR

from werkcrew_ai.catalog.models import (
    ChunkedJobItem,
    ChunkingPolicy,
    ScheduledChunk,
    SkuPlanningProfile,
    UnitType,
)


CONTINUOUS_UNITS = (UnitType.SQUARE_METER, UnitType.WORKER_HOUR)
DISCRETE_UNITS = (UnitType.PIECE, UnitType.ROOM)


def _positive_decimal(value: object, name: str) -> Decimal:
    if not isinstance(value, Decimal) or not value.is_finite() or value <= 0:
        raise ValueError(f"{name} must be a positive finite Decimal")
    return value


def estimated_worker_hours(
    quantity: Decimal,
    profile: SkuPlanningProfile,
) -> Decimal:
    return _positive_decimal(quantity, "quantity") * _positive_decimal(
        profile.norm_worker_hours_per_unit,
        "norm_worker_hours_per_unit",
    )


def generate_job_item_chunks(
    *,
    job_item_id: str,
    quantity: Decimal,
    profile: SkuPlanningProfile,
    policy: ChunkingPolicy,
) -> tuple[ScheduledChunk, ...]:
    """Chunk one explicit JobItem without inferring any additional SKU."""

    if not job_item_id.strip():
        raise ValueError("job_item_id is required")
    quantity = _positive_decimal(quantity, "quantity")
    norm = _positive_decimal(
        profile.norm_worker_hours_per_unit,
        "norm_worker_hours_per_unit",
    )
    maximum = _positive_decimal(
        policy.max_scheduled_hours_per_task_day,
        "max_scheduled_hours_per_task_day",
    )
    chunk_values: list[tuple[Decimal, Decimal]] = []

    if profile.unit in CONTINUOUS_UNITS:
        total_hours = quantity * norm
        full_count = int((total_hours / maximum).to_integral_value(rounding=ROUND_FLOOR))
        full_quantity = maximum / norm
        chunk_values.extend((full_quantity, maximum) for _ in range(full_count))
        remainder_hours = total_hours - (maximum * full_count)
        if remainder_hours > 0:
            chunk_values.append((remainder_hours / norm, remainder_hours))
    elif profile.unit in DISCRETE_UNITS:
        if quantity != quantity.to_integral_value():
            raise ValueError("PIECE and ROOM quantities must be whole units")
        units_per_full_chunk = int(
            (maximum / norm).to_integral_value(rounding=ROUND_FLOOR)
        )
        if units_per_full_chunk < 1:
            raise ValueError("One PIECE/ROOM unit does not fit in a task day")
        remaining = int(quantity)
        while remaining > 0:
            units = min(units_per_full_chunk, remaining)
            unit_quantity = Decimal(units)
            chunk_values.append((unit_quantity, unit_quantity * norm))
            remaining -= units
    else:
        raise ValueError(f"Unsupported UnitType: {profile.unit}")

    chunks: list[ScheduledChunk] = []
    for index, (chunk_quantity, hours) in enumerate(chunk_values, start=1):
        chunk_id = f"{job_item_id}:chunk:{index:03d}"
        chunks.append(
            ScheduledChunk(
                chunk_id=chunk_id,
                sequence=index,
                quantity=chunk_quantity,
                estimated_worker_hours=hours,
                predecessor_chunk_id=(chunks[-1].chunk_id if chunks else None),
            )
        )
    return tuple(chunks)


def conditional_predecessor_chunk_ids(
    *,
    job_id: str,
    dependent_profile: SkuPlanningProfile,
    explicit_job_items: tuple[ChunkedJobItem, ...],
) -> tuple[str, ...]:
    """Resolve last chunks of explicit matching predecessors in the same job only."""

    predecessor_skus = set(dependent_profile.predecessor_if_present)
    matching = sorted(
        (
            item
            for item in explicit_job_items
            if item.job_id == job_id and item.sku in predecessor_skus
        ),
        key=lambda item: item.job_item_id,
    )
    if any(not item.chunks for item in matching):
        raise ValueError("Explicit predecessor JobItem must have at least one chunk")
    return tuple(item.chunks[-1].chunk_id for item in matching)
