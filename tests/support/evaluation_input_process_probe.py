"""M3-B restart probe using only durable source identity and trusted state."""

import sys

from werkcrew_ai.field.repository import M2DurableRepository
from werkcrew_ai.planning.evaluation_input import serialize_evaluation_input
from werkcrew_ai.planning.evaluation_repository import M3EvaluationRepository
from werkcrew_ai.planning.m2_bridge import M2EffectSourceIdentity, M2UnavailableToM3Bridge


if __name__ == "__main__":
    database_path, event_id = sys.argv[1:]
    request = M2UnavailableToM3Bridge(M2DurableRepository(database_path)).build_request(
        M2EffectSourceIdentity("FIELD_EVENT", event_id, 0)
    )
    value = M3EvaluationRepository(database_path).record_evaluation_input(request)
    print(serialize_evaluation_input(value))
