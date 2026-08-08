"""Navigator: deterministic leg resolution against a passed-in ScheduleIndex.

Pure. The index arrives as an argument and is typed structurally (Protocol),
so this module imports nothing outside the core package. With no index, or a
date outside the feed's validity horizon, every leg degrades to the estimate
model and is flagged estimated. Verified and estimated are never conflated.
"""

from __future__ import annotations

import math
from typing import Optional, Protocol, Sequence, runtime_checkable

from slackline.core import constants
from slackline.core.state import LatLng, Leg, minutes_to_hhmm

# Walks longer than this are not worth it; try transit or estimate instead.
WALK_MAX_MIN = 25
# How far we are willing to walk to/from a transit stop.
STOP_WALK_RADIUS_KM = 1.5


@runtime_checkable
class StopLike(Protocol):
    agency: str
    stop_id: str
    name: str
    lat: float
    lon: float


@runtime_checkable
class DepartureLike(Protocol):
    agency: str
    trip_id: str
    route: str
    headsign: str
    from_stop: StopLike
    to_stop: StopLike
    dep_min: int
    arr_min: int


class ScheduleIndexLike(Protocol):
    """Structural type for slackline.schedule.index.ScheduleIndex."""

    build_date: str

    def covers(self, service_date: str) -> bool: ...

    def stops_near(
        self, lat: float, lon: float, radius_km: float = ..., limit: int = ...
    ) -> Sequence[StopLike]: ...

    def next_journey(
        self,
        from_stops: Sequence[StopLike],
        to_stops: Sequence[StopLike],
        service_date: str,
        after_min: int,
        transfer_buffer_min: int,
    ) -> Optional[tuple[DepartureLike, ...]]: ...

    def last_journey(
        self,
        from_stops: Sequence[StopLike],
        to_stops: Sequence[StopLike],
        service_date: str,
        transfer_buffer_min: int,
    ) -> Optional[tuple[DepartureLike, ...]]: ...


def haversine_km(a: LatLng, b: LatLng) -> float:
    r = 6371.0
    p1, p2 = math.radians(a.lat), math.radians(b.lat)
    dp = math.radians(b.lat - a.lat)
    dl = math.radians(b.lon - a.lon)
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(h))


def walk_minutes(a: LatLng, b: LatLng) -> int:
    """Walking estimate: haversine times detour factor at constant speed."""
    km = haversine_km(a, b) * constants.DETOUR_FACTOR
    return math.ceil(km / constants.WALK_SPEED_KMH * 60)


def estimated_transit_minutes(a: LatLng, b: LatLng, mode: str = "rail") -> int:
    """Unverified transit estimate: distance over per-mode speed plus a flat
    wait. Always flagged estimated by the caller."""
    km = haversine_km(a, b) * constants.DETOUR_FACTOR
    speed = constants.TRANSIT_ESTIMATE_SPEED_KMH.get(
        mode, constants.TRANSIT_ESTIMATE_SPEED_KMH["rail"]
    )
    return math.ceil(km / speed * 60) + constants.TRANSIT_ESTIMATE_WAIT_MIN


def resolve_leg(
    index: Optional[ScheduleIndexLike],
    from_ref: str,
    to_ref: str,
    from_loc: LatLng,
    to_loc: LatLng,
    earliest_depart_min: int,
    service_date: str,
) -> Leg:
    """Resolve one door-to-door leg deterministically.

    Preference order: short walk; verified transit against the schedule
    index (walk to stop + transfer buffer + scheduled ride + walk from
    stop); estimate model as the floor. The chosen option is whichever
    arrives earliest, walks winning ties (fewer moving parts).
    """
    walk_min = walk_minutes(from_loc, to_loc)
    walk_leg = Leg(
        from_ref=from_ref,
        to_ref=to_ref,
        mode="walk",
        depart_min=earliest_depart_min,
        arrive_min=earliest_depart_min + walk_min,
        provenance="estimated",
        note=f"walk ~{walk_min} min (4.8 km/h, 1.3 detour factor)",
    )
    if walk_min <= WALK_MAX_MIN:
        return walk_leg

    verified = _verified_transit_leg(
        index, from_ref, to_ref, from_loc, to_loc, earliest_depart_min, service_date
    )
    if verified is not None:
        if verified.arrive_min < walk_leg.arrive_min:
            return verified
        return walk_leg

    est_min = estimated_transit_minutes(from_loc, to_loc)
    reason = "no schedule index" if index is None else (
        "date outside feed validity" if not index.covers(service_date)
        else "no verified ride found"
    )
    estimate_leg = Leg(
        from_ref=from_ref,
        to_ref=to_ref,
        mode="transit",
        depart_min=earliest_depart_min,
        arrive_min=earliest_depart_min + est_min,
        provenance="estimated",
        note=f"estimated transit ~{est_min} min ({reason})",
    )
    return estimate_leg if estimate_leg.arrive_min < walk_leg.arrive_min else walk_leg


def _verified_transit_leg(
    index: Optional[ScheduleIndexLike],
    from_ref: str,
    to_ref: str,
    from_loc: LatLng,
    to_loc: LatLng,
    earliest_depart_min: int,
    service_date: str,
) -> Optional[Leg]:
    if index is None or not index.covers(service_date):
        return None
    from_stops = list(
        index.stops_near(from_loc.lat, from_loc.lon, radius_km=STOP_WALK_RADIUS_KM)
    )
    to_stops = list(
        index.stops_near(to_loc.lat, to_loc.lon, radius_km=STOP_WALK_RADIUS_KM)
    )
    if not from_stops or not to_stops:
        return None

    best: Optional[Leg] = None
    for origin_stop in from_stops:
        walk_to_stop = walk_minutes(
            from_loc, LatLng(lat=origin_stop.lat, lon=origin_stop.lon)
        )
        board_after = (
            earliest_depart_min + walk_to_stop + constants.TRANSFER_BUFFER_MIN
        )
        journey = index.next_journey(
            [origin_stop], to_stops, service_date, board_after,
            constants.TRANSFER_BUFFER_MIN,
        )
        if not journey:
            continue
        first, final = journey[0], journey[-1]
        walk_from_stop = walk_minutes(
            LatLng(lat=final.to_stop.lat, lon=final.to_stop.lon), to_loc
        )
        last = index.last_journey(
            [origin_stop], [final.to_stop], service_date,
            constants.TRANSFER_BUFFER_MIN,
        )
        route = " > ".join(dict.fromkeys(r.route for r in journey))
        note = "; ".join(
            f"{r.agency} {r.route} {r.from_stop.name} "
            f"{minutes_to_hhmm(r.dep_min)} -> {r.to_stop.name} "
            f"{minutes_to_hhmm(r.arr_min)}"
            for r in journey
        )
        if len(journey) > 1:
            note += f" (transfer at {journey[0].to_stop.name})"
        leg = Leg(
            from_ref=from_ref,
            to_ref=to_ref,
            mode="transit",
            # Door-to-door: leave in time to walk to the stop and buffer in.
            depart_min=first.dep_min - constants.TRANSFER_BUFFER_MIN - walk_to_stop,
            arrive_min=final.arr_min + walk_from_stop,
            provenance="verified",
            agency=first.agency,
            route=route,
            trip_id=first.trip_id,
            from_stop=first.from_stop.name,
            to_stop=final.to_stop.name,
            transit_depart_min=first.dep_min,
            transit_arrive_min=final.arr_min,
            last_depart_of_day_min=last[0].dep_min if last else None,
            note=note,
        )
        if (
            best is None
            or leg.arrive_min < best.arrive_min
            or (
                leg.arrive_min == best.arrive_min
                and (leg.depart_min, leg.trip_id or "")
                > (best.depart_min, best.trip_id or "")
            )
        ):
            # Earliest arrival wins; on ties prefer the later departure
            # (more slack beforehand), then trip id for stability.
            best = leg
    return best


def transit_cache_key(
    from_loc: LatLng, to_loc: LatLng, service_date: str, depart_min: int
) -> str:
    """Cache key for leg resolution: origin, destination, service date, and
    departure bucketed to 15 minutes so repair iterations hit cache."""
    bucket = depart_min // constants.TRANSIT_CACHE_BUCKET_MIN
    return (
        f"{from_loc.lat:.4f},{from_loc.lon:.4f}|{to_loc.lat:.4f},{to_loc.lon:.4f}"
        f"|{service_date}|{bucket}"
    )
