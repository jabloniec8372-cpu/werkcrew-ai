from __future__ import annotations

from dataclasses import replace
from decimal import Decimal
from pathlib import Path

import pytest

from werkcrew_ai.agent import (
    AgentConfigurationError,
    StrandsSessionSettings,
    build_file_session_manager,
    workflow_session_id,
)
from werkcrew_ai.domain import (
    owner_response_fingerprint,
    pricing_gate_fingerprint,
)
from werkcrew_ai.infrastructure.demo_repository import load_demo_site_visit_report
from werkcrew_ai.infrastructure.demo_workflow_store import DemoWorkflowStore


def pricing_results():
    store = DemoWorkflowStore()
    store.assess_job()
    store.create_site_visit()
    store.submit_report(load_demo_site_visit_report())
    store.generate_plans()
    return store.calculate_plan_quotes().pricing_results


def test_pricing_gate_fingerprint_is_order_independent_and_view_complete() -> None:
    results = pricing_results()
    job_id = results[0].job_request_id
    baseline = pricing_gate_fingerprint(job_id, results)

    assert pricing_gate_fingerprint(job_id, tuple(reversed(results))) == baseline
    assert pricing_gate_fingerprint(
        job_id,
        (replace(results[0], result_id="changed-result"), results[1]),
    ) != baseline
    assert pricing_gate_fingerprint(
        job_id,
        (replace(results[0], input_fingerprint="changed-input"), results[1]),
    ) != baseline
    assert pricing_gate_fingerprint(
        job_id,
        (replace(results[0], pricing_rule_version="changed-rule"), results[1]),
    ) != baseline
    assert pricing_gate_fingerprint(
        job_id,
        (
            replace(
                results[0],
                category_totals=(
                    replace(
                        results[0].category_totals[0],
                        total=results[0].category_totals[0].total
                        + Decimal("0.01"),
                    ),
                    *results[0].category_totals[1:],
                ),
            ),
            results[1],
        ),
    ) != baseline
    assert pricing_gate_fingerprint(
        job_id,
        (replace(results[0], gross_price=Decimal("0.01")), results[1]),
    ) != baseline


def test_owner_response_fingerprint_is_canonical() -> None:
    first = {
        "gate_id": "gate",
        "action": "APPROVE_PLAN",
        "selected_plan_id": "plan-a",
        "pricing_gate_fingerprint": "pricing",
    }
    reordered = dict(reversed(tuple(first.items())))
    changed = dict(first, selected_plan_id="plan-b")

    assert owner_response_fingerprint(first) == owner_response_fingerprint(reordered)
    assert owner_response_fingerprint(first) != owner_response_fingerprint(changed)


def test_session_configuration_uses_existing_environment_pattern(
    tmp_path: Path,
) -> None:
    local_app_data = tmp_path / "local-app-data"
    configured = StrandsSessionSettings.from_environment(
        {"WERKCREW_STRANDS_SESSION_DIR": str(tmp_path)}
    )
    defaulted = StrandsSessionSettings.from_environment(
        {"LOCALAPPDATA": str(local_app_data)}
    )

    assert configured.storage_dir == str(tmp_path.resolve())
    assert defaulted.storage_dir == str(
        (local_app_data / "WERKcrew_AI" / "strands-sessions").resolve()
    )


def test_real_file_session_manager_creates_session_storage(tmp_path: Path) -> None:
    storage = tmp_path / "strands-sessions"
    session_id = "werkcrew-storage-test-instance"

    manager = build_file_session_manager(session_id, str(storage))

    assert manager is not None
    assert storage.is_dir()
    assert (storage / f"session_{session_id}").is_dir()


def test_invalid_session_storage_fails_with_clear_configuration_error(
    tmp_path: Path,
) -> None:
    invalid_storage = tmp_path / "not-a-directory"
    invalid_storage.write_text("file collision", encoding="utf-8")

    with pytest.raises(
        AgentConfigurationError,
        match="Nie można utworzyć zapisywalnego katalogu sesji Strands",
    ):
        build_file_session_manager("werkcrew-storage-test", str(invalid_storage))


def test_session_id_is_stable_for_instance_and_changes_on_reset() -> None:
    store = DemoWorkflowStore()
    first = store.get()
    first_session = workflow_session_id(
        first.current_job_request.id,
        first.workflow_instance_id,
    )
    assert first_session == workflow_session_id(
        first.current_job_request.id,
        first.workflow_instance_id,
    )

    reset = store.reset()
    assert workflow_session_id(
        reset.current_job_request.id,
        reset.workflow_instance_id,
    ) != first_session
