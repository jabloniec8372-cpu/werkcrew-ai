from __future__ import annotations

import json
import sys

from werkcrew_ai.planning.feasibility_repository import M3FeasibilitySupportRepository
from werkcrew_ai.planning.feasibility_support import serialize_feasibility_support


repository = M3FeasibilitySupportRepository(sys.argv[1])
value = repository.get_feasibility_support(sys.argv[2])
print(json.dumps({
    "serialized": serialize_feasibility_support(value),
    "support_snapshot_id": value.support_snapshot_id,
}, sort_keys=True))
