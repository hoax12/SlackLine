"""Phase 2 tests: schedule index over the frozen GTFS fixture extract.

The fixture (tests/fixtures/schedule_index_fixture.sqlite) is a real derived
extract of the CT + BA feeds (build date 2026-08-08), filtered to the
stations the assertions need. All tests run offline.

The frozen departure values below were hand-verified against the published
schedules on 2026-08-08:

* Caltrain weekday table (caltrain.com): train 173 departs Palo Alto
  11:57pm, arrives San Francisco 12:48am; weekend train 665 departs
  11:58pm, arrives 12:50am.
* BART August 10, 2026 timetable PDFs (bart.gov): last SF-bound journey
  from Downtown Berkeley is the 12:17am Orange toward Berryessa, transfer
  at 19th St Oakland to the 12:56am late-night Yellow, arriving
  Embarcadero 1:09am. Identical weekday and Saturday.
"""

import pathlib

import pytest

from slackline.core.constants import TRANSFER_BUFFER_MIN
from slackline.schedule.build_gtfs import parse_gtfs_time
from slackline.schedule.index import ScheduleIndex, Stop

FIXTURE_DB = (
    pathlib.Path(__file__).resolve().parents[1]
    / "fixtures"
    / "schedule_index_fixture.sqlite"
)

WEEKDAY = "2026-08-12"   # Wednesday
SATURDAY = "2026-08-15"
LABOR_DAY = "2026-09-07"  # Monday holiday: runs weekend service via calendar_dates


@pytest.fixture(scope="module")
def index() -> ScheduleIndex:
    assert FIXTURE_DB.exists(), "run build_gtfs with --filter-stops to refresh"
    return ScheduleIndex(str(FIXTURE_DB))


def stops(index: ScheduleIndex, agency: str, needle: str) -> list[Stop]:
    needle = needle.lower()
    return [
        s for s in index.all_stops()
        if s.agency == agency and needle in s.name.lower()
    ]


# --- midnight boundary -------------------------------------------------------


def test_parse_gtfs_time_post_midnight():
    """GTFS 24:xx is minutes past 1440, never normalized."""
    assert parse_gtfs_time("24:40:00") == 1480
    assert parse_gtfs_time("25:03:00") == 1503
    assert parse_gtfs_time("09:05:00") == 545


def test_last_departures_are_post_midnight_encoded(index):
    """The signature-demo values sit past the naive midnight boundary; a
    parser that wrapped 24:xx would report evening trains instead."""
    ct = index.last_departure(
        stops(index, "CT", "palo alto"),
        stops(index, "CT", "san francisco"),
        WEEKDAY,
    )
    assert ct is not None
    assert ct.arr_min > 1440  # arrives 00:48 (+1d)


# --- hand-verified frozen assertions ----------------------------------------


def test_last_northbound_caltrain_weekday(index):
    """Caltrain train 173: Palo Alto 23:57 -> San Francisco 00:48 (+1d)."""
    last = index.last_departure(
        stops(index, "CT", "palo alto"),
        stops(index, "CT", "san francisco"),
        WEEKDAY,
    )
    assert last is not None
    assert last.trip_id == "173"
    assert last.dep_min == 23 * 60 + 57          # 1437
    assert last.arr_min == 24 * 60 + 48          # 1488

def test_last_northbound_caltrain_saturday(index):
    """Caltrain train 665: Palo Alto 23:58 -> San Francisco 00:50 (+1d)."""
    last = index.last_departure(
        stops(index, "CT", "palo alto"),
        stops(index, "CT", "san francisco"),
        SATURDAY,
    )
    assert last is not None
    assert last.trip_id == "665"
    assert last.dep_min == 23 * 60 + 58          # 1438
    assert last.arr_min == 24 * 60 + 50          # 1490


@pytest.mark.parametrize("date", [WEEKDAY, SATURDAY])
def test_last_sf_bound_bart_from_berkeley(index, date):
    """Last SF-bound journey from Downtown Berkeley: 00:17 Orange to 19th
    St Oakland, transfer, late-night Yellow, Embarcadero 01:09. The direct
    Red line stops earlier in the evening, so this must be a transfer."""
    journey = index.last_journey(
        stops(index, "BA", "downtown berkeley"),
        stops(index, "BA", "embarcadero"),
        date,
        TRANSFER_BUFFER_MIN,
    )
    assert journey is not None
    assert len(journey) == 2, "late-night SF service requires a transfer"
    first, second = journey
    assert first.dep_min == 24 * 60 + 17         # 1457, i.e. 00:17 (+1d)
    assert first.to_stop.name == "19th Street Oakland"
    assert second.dep_min == 24 * 60 + 56        # 00:56 Yellow from 19th St
    assert second.arr_min == 24 * 60 + 69        # 1509, i.e. 01:09 (+1d)


# --- calendar_dates overrides calendar ---------------------------------------


def test_holiday_runs_weekend_service(index):
    """Labor Day 2026-09-07 is a Monday but Caltrain runs the weekend
    schedule: exception dates must override the weekday calendar."""
    last = index.last_departure(
        stops(index, "CT", "palo alto"),
        stops(index, "CT", "san francisco"),
        LABOR_DAY,
    )
    assert last is not None
    assert last.trip_id == "665"                 # weekend trip, not weekday 173
    assert last.dep_min == 23 * 60 + 58

def test_holiday_exception_present_in_calendar_dates(index):
    rows = list(
        index.con.execute(
            "SELECT COUNT(*) FROM calendar_dates WHERE agency='CT' AND date='20260907'"
        )
    )
    assert rows[0][0] > 0, "expected a Labor Day exception in the CT feed"


# --- validity horizon ---------------------------------------------------------


def test_covers_inside_and_outside_horizon(index):
    assert index.covers(WEEKDAY)
    assert index.covers(SATURDAY)
    assert not index.covers("2026-08-05")   # before BA feed start 2026-08-10
    assert not index.covers("2027-06-01")   # after BA feed end 2027-01-10
