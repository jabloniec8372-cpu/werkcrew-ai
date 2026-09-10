"""Read-only fresh-process probe; authority files are trusted TEST fixtures.

This is not a production adapter or an authorization mechanism for client JSON.
"""

from __future__ import annotations

import argparse
import json
from datetime import date
from pathlib import Path

from werkcrew_ai.field.repository import M2DurableRepository
from werkcrew_ai.planning.m2_bridge import M2EffectSourceIdentity, M2UnavailableToM3Bridge
from werkcrew_ai.planning.m3_unavailability_impact import (
    M3CurrentCommitment,
    M3CurrentPlanningScope,
    M3ImpactFingerprintError,
    M3PlanningScopeAuthority,
    evaluate_unavailability_impact,
    serialize_unavailability_impact_result,
)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("database", type=Path)
    parser.add_argument("scope", type=Path)
    parser.add_argument("trusted_test_authority", type=Path)
    parser.add_argument("--event-id", required=True)
    args = parser.parse_args()
    request = M2UnavailableToM3Bridge(M2DurableRepository(args.database)).build_request(
        M2EffectSourceIdentity("FIELD_EVENT", args.event_id, 0)
    )
    scope_data = json.loads(args.scope.read_text(encoding="utf-8"))
    fingerprint = scope_data.pop("snapshot_fingerprint")
    scope_data["business_date"] = date.fromisoformat(scope_data["business_date"])
    scope_data["commitments"] = tuple(M3CurrentCommitment(**item) for item in scope_data["commitments"])
    scope = M3CurrentPlanningScope(**scope_data)
    if fingerprint != scope.snapshot_fingerprint:
        raise M3ImpactFingerprintError("SCOPE_FINGERPRINT_MISMATCH", "test_fixture")
    authority_data = json.loads(args.trusted_test_authority.read_text(encoding="utf-8"))
    authority_data["business_date"] = date.fromisoformat(authority_data["business_date"])
    authority = M3PlanningScopeAuthority(**authority_data)
    result = evaluate_unavailability_impact(request, scope, authority=authority)
    print(serialize_unavailability_impact_result(result))


if __name__ == "__main__":
    main()
