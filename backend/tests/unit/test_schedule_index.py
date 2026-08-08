"""Offline tests against the frozen GTFS fixture extract.

The fixture was built 2026-08-08 from the real Caltrain (Trillium producer
feed, valid 2026-01-31..2027-01-31) and BART (bart.gov, valid
2026-08-10..2027-01-10) GTFS. The last-departure assertions below were
checked by hand against the CLI output and the agencies' published
late-night service patterns before freezing.
"""

import pytest

from slackline.core.constants import TRANSFER_BUFFER_MIN
from slackline.schedule.build_gtfs import parse_gtfs_time
from slackline.schedule.index import ScheduleIndex

WEEKDAY = "2026-08-12"   # Wednesday
SATURDAY = "2026-08-15"
LABOR_DAY = "2026-09-07"  # Monday holiday: calendar_dates swaps in weekend service


@pytest.fixture(scope="module")
def index(request) -> ScheduleIndex:
    fixture = request.path.parent.parent / "fixtures" / "schedule_index_fixture.sqlite"
    return ScheduleIndex(str(fixture))


def _stops(index, agency, needle):
    return [
        s for s in index.all_stops()
        if s.agency == agency and needle in s.name.lower()
    ]


def test_parse_gtfs_time_handles_post_midnight():
    assert parse_gtfs_time("24:40:00") == 24 * 60 + 40
    assert parse_gtfs_time("09:05:00") == 9 * 60 + 5
    assert parse_gtfs_time("25:01:00") == 25 * 60 + 1


def test_metadata(index):
    assert index.agencies == ("CT", "BA")
    assert index.build_date == "2026-08-08"
    assert index.validity("CT") == ("20260131", "20270131")
    assert index.validity("BA") == ("20260810", "20270110")


def test_covers_respects_both_agencies(index):
    assert index.covers(WEEKDAY)
    assert index.covers(SATURDAY)
    assert not index.covers("2026-08-08")  # before BART feed start
    assert not index.covers("2027-06-01")  # after both feeds end


def test_last_northbound_caltrain_weekday(index):
    """Hand-verified: last weekday northbound Caltrain from Palo Alto is the
    23:57 local (trip 173) arriving San Francisco 00:48 next day. The 24:xx
    arrival is the midnight-boundary case that must not be dropped."""
    journey = index.last_journey(
        _stops(index, "CT", "palo alto"),
        _stops(index, "CT", "san francisco caltrain"),
        WEEKDAY,
        TRANSFER_BUFFER_MIN,
    )
    assert journey is not None and len(journey) == 1
    ride = journey[0]
    assert ride.dep_min == 23 * 60 + 57
    assert ride.arr_min == 24 * 60 + 48   # 00:48 (+1d), stored past 1440
    assert ride.route == "Local Weekday"


def test_last_northbound_caltrain_saturday(index):
    journey = index.last_journey(
        _stops(index, "CT", "palo alto"),
        _stops(index, "CT", "san francisco caltrain"),
        SATURDAY,
        TRANSFER_BUFFER_MIN,
    )
    assert journey is not None and len(journey) == 1
    assert journey[0].dep_min == 23 * 60 + 58
    assert journey[0].arr_min == 24 * 60 + 50
    assert journey[0].route == "Local Weekend"


def test_holiday_runs_weekend_schedule(index):
    """calendar_dates overrides calendar: Labor Day (a Monday) runs the
    weekend schedule, so the last departure matches Saturday, not Wednesday."""
    journey = index.last_journey(
        _stops(index, "CT", "palo alto"),
        _stops(index, "CT", "san francisco caltrain"),
        LABOR_DAY,
        TRANSFER_BUFFER_MIN,
    )
    assert journey is not None
    assert journey[0].route == "Local Weekend"
    assert journey[0].dep_min == 23 * 60 + 58


def test_last_sf_bound_bart_needs_transfer(index):
    """The Red line from Downtown Berkeley toward SF ends early evening; the
    true last SF-bound departure is an Orange ride with a Yellow transfer at
    19th Street Oakland, leaving at 00:17 (+1d). Single-seat-only logic would
    wrongly answer 19:44."""
    from_stops = _stops(index, "BA", "downtown berkeley")
    to_stops = _stops(index, "BA", "embarcadero")
    direct = index.last_departure(from_stops, to_stops, WEEKDAY)
    assert direct is not None
    assert direct.dep_min == 19 * 60 + 44  # last single-seat Red-line ride

    journey = index.last_journey(from_stops, to_stops, WEEKDAY, TRANSFER_BUFFER_MIN)
    assert journey is not None and len(journey) == 2
    first, second = journey
    assert first.dep_min == 24 * 60 + 17   # 00:17 (+1d)
    assert first.to_stop.name == "19th Street Oakland"
    assert second.arr_min == 25 * 60 + 9   # 01:09 (+1d)
    assert second.dep_min >= first.arr_min + TRANSFER_BUFFER_MIN


def test_next_journey_is_deterministic_and_ordered(index):
    from_stops = _stops(index, "BA", "downtown berkeley")
    to_stops = _stops(index, "BA", "embarcadero")
    a = index.next_journey(from_stops, to_stops, WEEKDAY, 18 * 60, TRANSFER_BUFFER_MIN)
    b = index.next_journey(from_stops, to_stops, WEEKDAY, 18 * 60, TRANSFER_BUFFER_MIN)
    assert a == b
    assert a is not None
    assert a[0].dep_min >= 18 * 60
    for first, second in zip(a, a[1:]):
        assert second.dep_min >= first.arr_min + TRANSFER_BUFFER_MIN


def test_active_services_empty_before_feed_start(index):
    assert index.active_services("BA", "2026-08-08") == frozenset()
    assert index.active_services("BA", WEEKDAY)
