"""Amazon Location point-to-point fact adapter; never performs optimization."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable

import boto3

from werkcrew_ai.dispatch import (
    AddressStatus,
    PersistentJob,
    RouteSnapshot,
    RouteSnapshotStatus,
    require_aware,
)


AMAZON_LOCATION_PROVIDER = "AMAZON_LOCATION_CALCULATE_ROUTES"


def _hash(payload: object) -> str:
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return "sha256:" + hashlib.sha256(raw.encode("utf-8")).hexdigest()


def location_fingerprint(job: PersistentJob) -> str:
    if job.address_status is not AddressStatus.CONFIRMED or job.coordinates is None:
        raise ValueError("Routing requires a CONFIRMED address with fixture coordinates")
    return _hash(
        {
            "schema": "werkcrew-location-reference-v1",
            "job_id": job.job_id,
            "address": {
                "street": job.address.street,
                "house_number": job.address.house_number,
                "postal_code": job.address.postal_code,
                "city": job.address.city,
                "country": job.address.country,
            },
            "coordinates": {
                "latitude": job.coordinates.latitude,
                "longitude": job.coordinates.longitude,
                "source": job.coordinates.source,
            },
        }
    )


@dataclass(frozen=True, slots=True)
class AmazonLocationSettings:
    region: str


class AmazonLocationRouteProvider:
    def __init__(
        self,
        settings: AmazonLocationSettings,
        *,
        session_factory: Callable[..., Any] = boto3.Session,
    ) -> None:
        self.settings = settings
        self._session_factory = session_factory

    def calculate_route(
        self,
        *,
        origin: PersistentJob,
        destination: PersistentJob,
        departure_time: datetime,
        retrieved_at: datetime,
        transport_mode: str = "Car",
    ) -> RouteSnapshot:
        require_aware(departure_time, "departure_time")
        require_aware(retrieved_at, "retrieved_at")
        origin_fp = location_fingerprint(origin)
        destination_fp = location_fingerprint(destination)
        departure_basis = departure_time.isoformat()
        input_payload = {
            "schema": "werkcrew-route-input-v1",
            "origin_reference": origin.job_id,
            "origin_fingerprint": origin_fp,
            "destination_reference": destination.job_id,
            "destination_fingerprint": destination_fp,
            "transport_mode": transport_mode,
            "departure_time_basis": departure_basis,
        }
        input_fingerprint = _hash(input_payload)
        session = self._session_factory(region_name=self.settings.region)
        client = session.client("geo-routes", region_name=self.settings.region)
        response = client.calculate_routes(
            Origin=[
                float(origin.coordinates.longitude),
                float(origin.coordinates.latitude),
            ],
            Destination=[
                float(destination.coordinates.longitude),
                float(destination.coordinates.latitude),
            ],
            TravelMode=transport_mode,
            DepartureTime=departure_basis,
        )
        routes = response.get("Routes") or []
        if not routes or not isinstance(routes[0].get("Summary"), dict):
            raise RuntimeError("Amazon Location returned no usable route summary")
        summary = routes[0]["Summary"]
        distance = int(round(summary["Distance"]))
        duration = int(round(summary["Duration"]))
        return RouteSnapshot(
            route_snapshot_id=(
                "route-" + input_fingerprint.removeprefix("sha256:")[:24]
            ),
            origin_reference=origin.job_id,
            origin_fingerprint=origin_fp,
            destination_reference=destination.job_id,
            destination_fingerprint=destination_fp,
            transport_mode=transport_mode,
            departure_time_basis=departure_basis,
            distance_meters=distance,
            travel_duration_seconds=duration,
            provider=AMAZON_LOCATION_PROVIDER,
            retrieved_at=retrieved_at,
            status=RouteSnapshotStatus.VALID,
            input_fingerprint=input_fingerprint,
        )
