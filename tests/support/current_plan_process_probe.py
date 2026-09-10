"""Read-only B0 restart probe: only DB path and durable M2 event identity enter."""
import sys

from werkcrew_ai.field.repository import M2DurableRepository
from werkcrew_ai.field.serialization import canonical_json
from werkcrew_ai.planning.current_plan import serialize_plan_revision
from werkcrew_ai.planning.current_plan_repository import CurrentPlanRepository
from werkcrew_ai.planning.m2_bridge import M2EffectSourceIdentity, M2UnavailableToM3Bridge
from werkcrew_ai.planning.m3_unavailability_impact import serialize_unavailability_impact_result


if __name__ == "__main__":
    path, event_id = sys.argv[1:]
    request = M2UnavailableToM3Bridge(M2DurableRepository(path)).build_request(M2EffectSourceIdentity("FIELD_EVENT", event_id, 0))
    repository = CurrentPlanRepository(path)
    snapshot = repository.produce_current_snapshot(request)
    print(canonical_json({"revision": serialize_plan_revision(snapshot.revision),
        "revision_id": snapshot.revision.revision_id,
        "impact": serialize_unavailability_impact_result(repository.evaluate_current_unavailability(request))}))
