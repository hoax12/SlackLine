"""Navigator tests: pure leg resolution over the frozen fixture index."""

import pytest

from slackline.core import constants, navigator
from slackline.core.state import LatLng
from slackline.schedule.index import ScheduleIndex

WEEKDAY = "2026-08-12"

DOWNTOWN_BERKELEY = LatLng(lat=37.8703, lon=-122.2680)
FERRY_BUILDING = LatLng(lat=37.7955, lon=-122.3937)   # near Embarcadero BART
HAYES_VALLEY = LatLng(lat=37.7767, lon=-122.4233)


@pytest.fixture(scope="module")
def index(request) -> ScheduleIndex:
    fixture = request.path.parent.parent / "fixtures" / "schedule_index_fixture.sqlite"
    return ScheduleIndex(str(fixture))


def test_short_hop_is_a_walk(index):
    leg = navigator.resolve_leg(
        index, "a", "b", FERRY_BUILDING,
        LatLng(lat=37.7902, lon=-122.4005),  # ~700 m away
        10 * 60, WEEKDAY,
    )
    assert leg.mode == "walk"
    assert leg.provenance == "estimated"
    assert leg.arrive_min - leg.depart_min <= navigator.WALK_MAX_MIN


def test_berkeley_to_sf_is_verified_transit(index):
    leg = navigator.resolve_leg(
        index, "origin", "dinner", DOWNTOWN_BERKELEY, FERRY_BUILDING,
        18 * 60, WEEKDAY,
    )
    assert leg.mode == "transit"
    assert leg.provenance == "verified"
    assert leg.agency == "BA"
    assert leg.trip_id is not None
    assert leg.transit_depart_min is not None
    # Door-to-door depart includes the walk to the stop plus transfer buffer.
    assert leg.depart_min <= leg.transit_depart_min - constants.TRANSFER_BUFFER_MIN
    assert leg.last_depart_of_day_min is not None
    assert leg.last_depart_of_day_min >= leg.transit_depart_min


def test_no_index_degrades_to_estimate():
    leg = navigator.resolve_leg(
        None, "origin", "dinner", DOWNTOWN_BERKELEY, FERRY_BUILDING,
        18 * 60, WEEKDAY,
    )
    assert leg.provenance == "estimated"
    assert "no schedule index" in leg.note


def test_date_outside_horizon_degrades_to_estimate(index):
    leg = navigator.resolve_leg(
        index, "origin", "dinner", DOWNTOWN_BERKELEY, FERRY_BUILDING,
        18 * 60, "2027-06-01",
    )
    assert leg.provenance == "estimated"
    assert "outside feed validity" in leg.note


def test_resolution_is_deterministic(index):
    legs = [
        navigator.resolve_leg(
            index, "origin", "dinner", DOWNTOWN_BERKELEY, FERRY_BUILDING,
            18 * 60, WEEKDAY,
        )
        for _ in range(3)
    ]
    assert legs[0] == legs[1] == legs[2]


def test_walk_minutes_uses_detour_factor():
    a = LatLng(lat=37.7793, lon=-122.4193)
    b = LatLng(lat=37.7793, lon=-122.4093)  # ~0.88 km east
    km = navigator.haversine_km(a, b)
    expected = -(-km * constants.DETOUR_FACTOR / constants.WALK_SPEED_KMH * 60 // 1)
    assert navigator.walk_minutes(a, b) == int(expected)


def test_transit_cache_key_buckets_departure():
    a = LatLng(lat=37.8703, lon=-122.2680)
    b = LatLng(lat=37.7955, lon=-122.3937)
    k1 = navigator.transit_cache_key(a, b, WEEKDAY, 18 * 60)      # 1080
    k2 = navigator.transit_cache_key(a, b, WEEKDAY, 18 * 60 + 14)  # same bucket
    k3 = navigator.transit_cache_key(a, b, WEEKDAY, 18 * 60 + 15)  # next bucket
    assert k1 == k2
    assert k1 != k3
