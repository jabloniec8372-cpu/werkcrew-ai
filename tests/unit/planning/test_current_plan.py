from dataclasses import FrozenInstanceError, replace
from datetime import date
from itertools import permutations

import pytest

from werkcrew_ai.planning.current_plan import (
    CompanyPlan, M3BootstrapStorageError, M3BootstrapValidationError,
    PlanDayAssociation, PlanningCommitment, PlanRevision, TaskDependency,
    deserialize_plan_revision, serialize_plan_revision,
)


DAY = date(2026, 9, 6)


def commitment(task="task-1", **changes):
    values = dict(
        commitment_id="commitment-" + task, task_id=task, job_id="job-1",
        task_definition_version="v1", source_handoff_id="handoff-1", source_revision=1,
        business_date=DAY, planned_worker_ids=("w1", "w2"),
    )
    values.update(changes)
    return PlanningCommitment(**values)


def initial(**changes):
    values = dict(
        company_plan_id="company-plan", revision=0, previous_revision_id=None,
        provenance_reference="trusted-bootstrap-record-1",
        plan_days=(PlanDayAssociation(plan_day_id="p1", worker_id="w1", business_date=DAY),
                   PlanDayAssociation(plan_day_id="p2", worker_id="w2", business_date=DAY)),
        commitments=(commitment(), commitment("task-2")), dependencies=(),
    )
    values.update(changes)
    return PlanRevision(**values)


def edge(before="task-1", after="task-2", **changes):
    return TaskDependency(predecessor_task_id=before, successor_task_id=after,
                          provenance_reference="explicit-planning-constraint", **changes)


def test_revision_content_roundtrip_replay_and_ordering():
    baseline = initial(dependencies=(edge(),))
    raw = serialize_plan_revision(baseline)
    assert deserialize_plan_revision(raw) == baseline
    assert baseline.revision_id == "m3-plan-revision-" + baseline.fingerprint
    for ordering in permutations(baseline.commitments):
        changed = replace(baseline, commitments=[replace(c, planned_worker_ids=["w2", "w1"]) for c in ordering],
                          plan_days=list(reversed(baseline.plan_days)))
        assert changed == baseline
        assert serialize_plan_revision(changed) == raw


def test_revision_semantic_changes_change_identity():
    baseline = initial()
    for value in (replace(baseline, dependencies=(edge(),)),
                  replace(baseline, provenance_reference="different-provenance"),
                  replace(baseline, commitments=()),
                  replace(baseline, revision=1, previous_revision_id=baseline.revision_id)):
        assert value.fingerprint != baseline.fingerprint


@pytest.mark.parametrize("changes", [
    {"revision": True}, {"revision": -1}, {"revision": 2**63},
    {"previous_revision_id": "unexpected-parent"}, {"company_plan_id": "\ud800"},
    {"provenance_reference": ""}, {"plan_days": {}}, {"commitments": None},
])
def test_invalid_revision_is_typed(changes):
    with pytest.raises(M3BootstrapValidationError):
        initial(**changes)


@pytest.mark.parametrize("edges", [
    (edge(), edge()), (edge(after="unknown"),), (edge(), edge("task-2", "task-1")),
])
def test_invalid_dependencies_rejected(edges):
    with pytest.raises(M3BootstrapValidationError):
        initial(dependencies=edges)


def test_self_dependency_relation_and_provenance_are_validated():
    with pytest.raises(M3BootstrapValidationError):
        edge("task-1", "task-1")
    with pytest.raises(M3BootstrapValidationError):
        edge(relation="SUPERSEDES")
    with pytest.raises(M3BootstrapValidationError):
        TaskDependency(predecessor_task_id="task-1", successor_task_id="task-2", provenance_reference="")


def test_duplicate_task_placement_and_unassociated_worker_rejected():
    with pytest.raises(M3BootstrapValidationError):
        initial(commitments=(commitment(), replace(commitment(), commitment_id="another")))
    with pytest.raises(M3BootstrapValidationError):
        initial(commitments=(replace(commitment(), planned_worker_ids=("unassociated",)),))


def test_immutability_and_defensive_normalization():
    members = ["w2", "w1"]
    item = replace(commitment(), planned_worker_ids=members)
    values = [item]
    revision = initial(commitments=values)
    members.clear()
    values.clear()
    assert revision.commitments == (item,)
    assert item.planned_worker_ids == ("w1", "w2")
    for value, name in ((revision, "revision"), (item, "task_id"),
                        (revision.plan_days[0], "worker_id"),
                        (CompanyPlan(company_plan_id="p", provenance_reference="source"), "company_plan_id")):
        with pytest.raises(FrozenInstanceError):
            setattr(value, name, None)


@pytest.mark.parametrize("target,name,value", [
    ("revision", "commitments", []), ("revision", "dependencies", []),
    ("revision", "fingerprint", "wrong"), ("revision", "schema_version", "wrong"),
    ("commitment", "task_id", {}), ("commitment", "planned_worker_ids", ["w1", "w2"]),
    ("commitment", "source_m2_assignment_ids", ["a1"]),
    ("day", "business_date", "2026-09-06"),
])
def test_reflective_mutation_fails_without_normalizing(target, name, value):
    revision = initial()
    item = {"revision": revision, "commitment": revision.commitments[0], "day": revision.plan_days[0]}[target]
    object.__setattr__(item, name, value)
    with pytest.raises(M3BootstrapValidationError):
        serialize_plan_revision(revision)
    assert getattr(item, name) is value


@pytest.mark.parametrize("raw", ["{}", "null", "[]", "{", '{"schema_version":"unknown"}'])
def test_invalid_persisted_document_fails_closed(raw):
    with pytest.raises(M3BootstrapStorageError):
        deserialize_plan_revision(raw)


def test_nested_identity_and_dependency_order_do_not_change_fingerprint():
    baseline = initial(commitments=(commitment(source_m2_assignment_ids=("a1", "a2")),
        commitment("task-2"), commitment("task-3")), dependencies=(edge(), edge("task-2", "task-3")))
    changed = replace(baseline,
        commitments=list(reversed(tuple(replace(c, planned_worker_ids=list(reversed(c.planned_worker_ids)),
            source_m2_assignment_ids=list(reversed(c.source_m2_assignment_ids))) for c in baseline.commitments))),
        dependencies=list(reversed(baseline.dependencies)))
    assert serialize_plan_revision(changed) == serialize_plan_revision(baseline)
    assert changed.revision_id == baseline.revision_id


@pytest.mark.parametrize("name,value", [
    ("task_id", "\ud800"), ("planned_worker_ids", ("\udfff",)),
    ("source_m2_assignment_ids", ({"bad": "id"},)), ("source_revision", True),
    ("source_revision", 1.0), ("business_date", "2026-09-06"),
    ("planned_worker_ids", ("w1", "w1")), ("source_m2_assignment_ids", ("a1", "a1")),
])
def test_malformed_nested_fields_are_typed(name, value):
    with pytest.raises(M3BootstrapValidationError):
        commitment(**{name: value})


@pytest.mark.parametrize("target,name", [
    ("revision", "commitments"), ("commitment", "task_id"),
    ("day", "plan_day_id"), ("edge", "provenance_reference"),
])
def test_unset_reflected_nested_fields_fail_typed(target, name):
    revision = initial(dependencies=(edge(),))
    values = {"revision": revision, "commitment": revision.commitments[0],
              "day": revision.plan_days[0], "edge": revision.dependencies[0]}
    object.__delattr__(values[target], name)
    with pytest.raises(M3BootstrapValidationError):
        serialize_plan_revision(revision)
