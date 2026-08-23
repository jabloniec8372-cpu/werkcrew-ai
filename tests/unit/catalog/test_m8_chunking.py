from decimal import Decimal

from werkcrew_ai.catalog import (
    M8_CONFIGURATION,
    ChunkedJobItem,
    conditional_predecessor_chunk_ids,
    estimated_worker_hours,
    generate_job_item_chunks,
)


def _chunks(job_item_id: str, sku: str, quantity: str):
    return generate_job_item_chunks(
        job_item_id=job_item_id,
        quantity=Decimal(quantity),
        profile=M8_CONFIGURATION.profile(sku),
        policy=M8_CONFIGURATION.chunking_policy,
    )


def test_plaster_skim_200_square_meters_is_ten_stable_eight_hour_chunks() -> None:
    profile = M8_CONFIGURATION.profile("plaster.skim")
    assert estimated_worker_hours(Decimal("200"), profile) == Decimal("80.00")
    chunks = _chunks("skim-item-1", "plaster.skim", "200")
    assert len(chunks) == 10
    assert tuple(item.chunk_id for item in chunks) == tuple(
        f"skim-item-1:chunk:{index:03d}" for index in range(1, 11)
    )
    assert all(item.quantity == Decimal("20") for item in chunks)
    assert all(item.estimated_worker_hours == Decimal("8.00") for item in chunks)
    assert chunks[0].predecessor_chunk_id is None
    assert tuple(item.predecessor_chunk_id for item in chunks[1:]) == tuple(
        item.chunk_id for item in chunks[:-1]
    )


def test_worker_hour_quantity_13_is_eight_plus_five() -> None:
    chunks = _chunks("demo-item-1", "demo.interior", "13")
    assert tuple(item.estimated_worker_hours for item in chunks) == (
        Decimal("8.00"),
        Decimal("5.00"),
    )
    assert tuple(item.quantity for item in chunks) == (
        Decimal("8"),
        Decimal("5"),
    )


def test_two_white_showers_are_two_unsplit_piece_chunks() -> None:
    chunks = _chunks("shower-item-1", "white.shower", "2")
    assert len(chunks) == 2
    assert tuple(item.quantity for item in chunks) == (Decimal("1"), Decimal("1"))
    assert tuple(item.estimated_worker_hours for item in chunks) == (
        Decimal("4.50"),
        Decimal("4.50"),
    )


def test_five_furniture_carry_rooms_are_four_plus_one() -> None:
    chunks = _chunks("carry-item-1", "furniture.carry", "5")
    assert tuple(item.quantity for item in chunks) == (Decimal("4"), Decimal("1"))
    assert tuple(item.estimated_worker_hours for item in chunks) == (
        Decimal("8.00"),
        Decimal("2.00"),
    )


def test_painting_coat2_is_its_own_two_coat_sku_and_does_not_create_coat1() -> None:
    profile = M8_CONFIGURATION.profile("painting.coat2")
    assert profile.norm_worker_hours_per_unit == Decimal("0.22")
    assert profile.predecessor_if_present == ("painting.coat1",)
    coat2_chunks = _chunks("coat2-item", "painting.coat2", "10")
    assert all(item.chunk_id.startswith("coat2-item:chunk:") for item in coat2_chunks)
    explicit = (
        ChunkedJobItem("job-1", "coat2-item", "painting.coat2", coat2_chunks),
    )
    assert conditional_predecessor_chunk_ids(
        job_id="job-1",
        dependent_profile=profile,
        explicit_job_items=explicit,
    ) == ()


def test_conditional_predecessor_applies_only_to_explicit_same_job_items() -> None:
    coat1 = ChunkedJobItem(
        "job-1",
        "coat1-item",
        "painting.coat1",
        _chunks("coat1-item", "painting.coat1", "100"),
    )
    cross_job = ChunkedJobItem(
        "job-2",
        "coat1-other-job",
        "painting.coat1",
        _chunks("coat1-other-job", "painting.coat1", "10"),
    )
    assert conditional_predecessor_chunk_ids(
        job_id="job-1",
        dependent_profile=M8_CONFIGURATION.profile("painting.coat2"),
        explicit_job_items=(cross_job, coat1),
    ) == (coat1.chunks[-1].chunk_id,)


def test_all_explicit_matching_job_items_contribute_their_last_chunk() -> None:
    fill_1 = ChunkedJobItem(
        "job-1",
        "fill-item-1",
        "painting.fill",
        _chunks("fill-item-1", "painting.fill", "50"),
    )
    fill_2 = ChunkedJobItem(
        "job-1",
        "fill-item-2",
        "painting.fill",
        _chunks("fill-item-2", "painting.fill", "100"),
    )
    unrelated = ChunkedJobItem(
        "job-1",
        "tile-item",
        "tile.floor",
        _chunks("tile-item", "tile.floor", "5"),
    )
    assert conditional_predecessor_chunk_ids(
        job_id="job-1",
        dependent_profile=M8_CONFIGURATION.profile("painting.prime"),
        explicit_job_items=(unrelated, fill_2, fill_1),
    ) == (
        fill_1.chunks[-1].chunk_id,
        fill_2.chunks[-1].chunk_id,
    )


def test_explicit_coat1_and_coat2_remain_two_job_items_not_merged() -> None:
    coat1 = _chunks("coat1-explicit", "painting.coat1", "10")
    coat2 = _chunks("coat2-explicit", "painting.coat2", "10")
    assert coat1[0].chunk_id == "coat1-explicit:chunk:001"
    assert coat2[0].chunk_id == "coat2-explicit:chunk:001"
    assert estimated_worker_hours(
        Decimal("10"), M8_CONFIGURATION.profile("painting.coat1")
    ) + estimated_worker_hours(
        Decimal("10"), M8_CONFIGURATION.profile("painting.coat2")
    ) == Decimal("3.40")
