"""Read-only ScheduleIndex loader. Pure: zero network, zero keys.

Reads the compact SQLite index produced by ``build_gtfs.py``. The connection
is opened lazily (cold-start friendly) and read-only. All query results are
plain tuples/dataclasses so the deterministic core can consume them without
importing anything from here (the Navigator types the index structurally).

Time convention matches the rest of Slackline: minutes past midnight of the
service date, with post-midnight service above 1440 (GTFS 24:xx preserved).
"""

from __future__ import annotations

import datetime as dt
import math
import sqlite3
from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class Stop:
    agency: str
    stop_id: str
    name: str
    lat: float
    lon: float


@dataclass(frozen=True)
class Departure:
    """One scheduled ride between two stops on one trip."""

    agency: str
    trip_id: str
    route: str
    headsign: str
    from_stop: Stop
    to_stop: Stop
    dep_min: int
    arr_min: int


_WEEKDAY_COLUMNS = (
    "monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday",
)


class ScheduleIndex:
    """Lazy, read-only view over the schedule SQLite index."""

    def __init__(self, db_path: str):
        self._db_path = db_path
        self._con: Optional[sqlite3.Connection] = None
        self._meta: Optional[dict[str, str]] = None
        self._stops: Optional[list[Stop]] = None
        self._service_cache: dict[tuple[str, str], frozenset[str]] = {}

    # -- plumbing ----------------------------------------------------------

    @property
    def con(self) -> sqlite3.Connection:
        if self._con is None:
            uri = f"file:{self._db_path}?mode=ro"
            self._con = sqlite3.connect(uri, uri=True, check_same_thread=False)
        return self._con

    @property
    def meta(self) -> dict[str, str]:
        if self._meta is None:
            self._meta = dict(self.con.execute("SELECT key, value FROM meta"))
        return self._meta

    # -- metadata ----------------------------------------------------------

    @property
    def build_date(self) -> str:
        return self.meta.get("build_date", "unknown")

    @property
    def agencies(self) -> tuple[str, ...]:
        return tuple(a for a in self.meta.get("agencies", "").split(",") if a)

    def validity(self, agency: str) -> tuple[str, str]:
        """(start, end) as GTFS YYYYMMDD strings."""
        return (
            self.meta.get(f"feed_start_{agency}", ""),
            self.meta.get(f"feed_end_{agency}", ""),
        )

    def covers(self, service_date: str) -> bool:
        """True when every agency's calendar covers the ISO date."""
        compact = service_date.replace("-", "")
        for agency in self.agencies:
            start, end = self.validity(agency)
            if not start or not end or not (start <= compact <= end):
                return False
        return True

    # -- stops ---------------------------------------------------------------

    def all_stops(self) -> list[Stop]:
        if self._stops is None:
            self._stops = [
                Stop(*row)
                for row in self.con.execute(
                    "SELECT agency, stop_id, name, lat, lon FROM stops"
                )
            ]
        return self._stops

    def stops_near(
        self, lat: float, lon: float, radius_km: float = 1.2, limit: int = 4
    ) -> list[Stop]:
        """Stops within radius, nearest first. Station counts are small
        (tens), so a python-side scan is simpler and fast enough."""
        scored = []
        for stop in self.all_stops():
            d = _haversine_km(lat, lon, stop.lat, stop.lon)
            if d <= radius_km:
                scored.append((d, stop))
        scored.sort(key=lambda pair: (pair[0], pair[1].agency, pair[1].stop_id))
        return [stop for _, stop in scored[:limit]]

    # -- service calendars ------------------------------------------------------

    def active_services(self, agency: str, service_date: str) -> frozenset[str]:
        """Service ids running on the ISO date. calendar_dates overrides
        calendar: exception 1 adds, exception 2 removes."""
        key = (agency, service_date)
        if key in self._service_cache:
            return self._service_cache[key]
        date = dt.date.fromisoformat(service_date)
        compact = service_date.replace("-", "")
        weekday_col = _WEEKDAY_COLUMNS[date.weekday()]
        active = {
            row[0]
            for row in self.con.execute(
                f"SELECT service_id FROM calendar WHERE agency=? AND {weekday_col}=1 "
                "AND start_date<=? AND end_date>=?",
                (agency, compact, compact),
            )
        }
        for service_id, exception_type in self.con.execute(
            "SELECT service_id, exception_type FROM calendar_dates "
            "WHERE agency=? AND date=?",
            (agency, compact),
        ):
            if exception_type == 1:
                active.add(service_id)
            elif exception_type == 2:
                active.discard(service_id)
        result = frozenset(active)
        self._service_cache[key] = result
        return result

    # -- departures ----------------------------------------------------------------

    def departures_between(
        self,
        from_stops: list[Stop],
        to_stops: list[Stop],
        service_date: str,
    ) -> list[Departure]:
        """All same-trip rides from any from_stop to any to_stop on the date,
        sorted by departure time. Same-trip with increasing stop_sequence is
        what makes a ride real (right line, right direction)."""
        by_agency: dict[str, tuple[list[Stop], list[Stop]]] = {}
        for stop in from_stops:
            by_agency.setdefault(stop.agency, ([], []))[0].append(stop)
        for stop in to_stops:
            by_agency.setdefault(stop.agency, ([], []))[1].append(stop)

        out: list[Departure] = []
        for agency, (froms, tos) in by_agency.items():
            if not froms or not tos:
                continue
            services = self.active_services(agency, service_date)
            if not services:
                continue
            stop_by_id = {s.stop_id: s for s in froms + tos}
            f_marks = ",".join("?" * len(froms))
            t_marks = ",".join("?" * len(tos))
            sql = (
                "SELECT a.stop_id, b.stop_id, a.dep_min, b.arr_min, t.trip_id,"
                "       t.service_id, t.headsign, r.short_name, r.long_name "
                "FROM stop_times a "
                "JOIN stop_times b ON b.agency=a.agency AND b.trip_id=a.trip_id "
                "JOIN trips t ON t.agency=a.agency AND t.trip_id=a.trip_id "
                "LEFT JOIN routes r ON r.agency=t.agency AND r.route_id=t.route_id "
                f"WHERE a.agency=? AND a.stop_id IN ({f_marks}) "
                f"AND b.stop_id IN ({t_marks}) AND a.seq < b.seq"
            )
            params = [agency] + [s.stop_id for s in froms] + [s.stop_id for s in tos]
            for row in self.con.execute(sql, params):
                (f_id, t_id, dep, arr, trip_id, service_id, headsign,
                 short_name, long_name) = row
                if service_id not in services:
                    continue
                out.append(
                    Departure(
                        agency=agency,
                        trip_id=trip_id,
                        route=short_name or long_name or agency,
                        headsign=headsign or "",
                        from_stop=stop_by_id[f_id],
                        to_stop=stop_by_id[t_id],
                        dep_min=dep,
                        arr_min=arr,
                    )
                )
        out.sort(key=lambda d: (d.dep_min, d.arr_min, d.agency, d.trip_id))
        return out

    def next_departure(
        self,
        from_stops: list[Stop],
        to_stops: list[Stop],
        service_date: str,
        after_min: int,
    ) -> Optional[Departure]:
        """Earliest ride departing at or after ``after_min``; among equal
        departures the earliest arrival wins (then agency/trip id, stable)."""
        for dep in self.departures_between(from_stops, to_stops, service_date):
            if dep.dep_min >= after_min:
                return dep
        return None

    def last_departure(
        self,
        from_stops: list[Stop],
        to_stops: list[Stop],
        service_date: str,
    ) -> Optional[Departure]:
        """Last ride of the service day, post-midnight (24:xx) included."""
        departures = self.departures_between(from_stops, to_stops, service_date)
        return departures[-1] if departures else None

    # -- one-transfer journeys ------------------------------------------------
    #
    # Single-seat rides alone understate late service: the last SF-bound
    # train from Downtown Berkeley is not the last Red-line departure but an
    # Orange ride plus a Yellow transfer. A journey is one or two same-agency
    # rides with a transfer buffer between them. Cross-agency transfers are
    # out of scope for v1 (documented in DECISIONS.md).

    def _rides_from(self, from_stops: list[Stop], service_date: str) -> list[Departure]:
        """Rides from any of from_stops to every downstream stop."""
        return self._half_rides(from_stops, service_date, from_side=True)

    def _rides_to(self, to_stops: list[Stop], service_date: str) -> list[Departure]:
        """Rides into any of to_stops from every upstream stop."""
        return self._half_rides(to_stops, service_date, from_side=False)

    def _half_rides(
        self, stops: list[Stop], service_date: str, from_side: bool
    ) -> list[Departure]:
        stop_lookup = {(s.agency, s.stop_id): s for s in self.all_stops()}
        out: list[Departure] = []
        by_agency: dict[str, list[Stop]] = {}
        for stop in stops:
            by_agency.setdefault(stop.agency, []).append(stop)
        for agency, pinned in by_agency.items():
            services = self.active_services(agency, service_date)
            if not services:
                continue
            marks = ",".join("?" * len(pinned))
            condition = (
                f"a.stop_id IN ({marks})" if from_side else f"b.stop_id IN ({marks})"
            )
            sql = (
                "SELECT a.stop_id, b.stop_id, a.dep_min, b.arr_min, t.trip_id,"
                "       t.service_id, t.headsign, r.short_name, r.long_name "
                "FROM stop_times a "
                "JOIN stop_times b ON b.agency=a.agency AND b.trip_id=a.trip_id "
                "JOIN trips t ON t.agency=a.agency AND t.trip_id=a.trip_id "
                "LEFT JOIN routes r ON r.agency=t.agency AND r.route_id=t.route_id "
                f"WHERE a.agency=? AND {condition} AND a.seq < b.seq"
            )
            params = [agency] + [s.stop_id for s in pinned]
            for row in self.con.execute(sql, params):
                (f_id, t_id, dep, arr, trip_id, service_id, headsign,
                 short_name, long_name) = row
                if service_id not in services:
                    continue
                f_stop = stop_lookup.get((agency, f_id))
                t_stop = stop_lookup.get((agency, t_id))
                if f_stop is None or t_stop is None:
                    continue
                out.append(
                    Departure(
                        agency=agency,
                        trip_id=trip_id,
                        route=short_name or long_name or agency,
                        headsign=headsign or "",
                        from_stop=f_stop,
                        to_stop=t_stop,
                        dep_min=dep,
                        arr_min=arr,
                    )
                )
        return out

    def next_journey(
        self,
        from_stops: list[Stop],
        to_stops: list[Stop],
        service_date: str,
        after_min: int,
        transfer_buffer_min: int,
    ) -> Optional[tuple[Departure, ...]]:
        """Earliest-arriving journey (direct or one transfer) departing at or
        after ``after_min``. Deterministic: final arrival, then fewer rides,
        then departure time, then trip ids."""
        candidates: list[tuple[Departure, ...]] = []
        direct = self.next_departure(from_stops, to_stops, service_date, after_min)
        if direct is not None:
            candidates.append((direct,))

        to_ids = {(s.agency, s.stop_id) for s in to_stops}
        from_ids = {(s.agency, s.stop_id) for s in from_stops}
        # Earliest arrival at each intermediate stop.
        best_first: dict[tuple[str, str], Departure] = {}
        for ride in self._rides_from(from_stops, service_date):
            if ride.dep_min < after_min:
                continue
            key = (ride.agency, ride.to_stop.stop_id)
            if key in to_ids or key in from_ids:
                continue
            cur = best_first.get(key)
            if cur is None or (ride.arr_min, ride.dep_min, ride.trip_id) < (
                cur.arr_min, cur.dep_min, cur.trip_id
            ):
                best_first[key] = ride
        second_by_stop: dict[tuple[str, str], list[Departure]] = {}
        for ride in self._rides_to(to_stops, service_date):
            second_by_stop.setdefault(
                (ride.agency, ride.from_stop.stop_id), []
            ).append(ride)
        for rides in second_by_stop.values():
            rides.sort(key=lambda d: (d.dep_min, d.arr_min, d.trip_id))
        for key, first in best_first.items():
            for second in second_by_stop.get(key, ()):
                if second.dep_min >= first.arr_min + transfer_buffer_min:
                    candidates.append((first, second))
                    break

        if not candidates:
            return None
        return min(
            candidates,
            key=lambda j: (
                j[-1].arr_min,
                len(j),
                j[0].dep_min,
                tuple(r.trip_id for r in j),
            ),
        )

    def last_journey(
        self,
        from_stops: list[Stop],
        to_stops: list[Stop],
        service_date: str,
        transfer_buffer_min: int,
    ) -> Optional[tuple[Departure, ...]]:
        """Journey with the latest possible initial departure of the service
        day (direct or one transfer), post-midnight included."""
        candidates: list[tuple[Departure, ...]] = []
        direct = self.last_departure(from_stops, to_stops, service_date)
        if direct is not None:
            candidates.append((direct,))

        to_ids = {(s.agency, s.stop_id) for s in to_stops}
        from_ids = {(s.agency, s.stop_id) for s in from_stops}
        # Latest second-leg departure from each intermediate stop: a later
        # connection allows a later first-leg arrival, hence departure.
        last_second: dict[tuple[str, str], Departure] = {}
        for ride in self._rides_to(to_stops, service_date):
            key = (ride.agency, ride.from_stop.stop_id)
            if key in to_ids or key in from_ids:
                continue
            cur = last_second.get(key)
            if cur is None or (ride.dep_min, -ride.arr_min, ride.trip_id) > (
                cur.dep_min, -cur.arr_min, cur.trip_id
            ):
                last_second[key] = ride
        firsts_by_stop: dict[tuple[str, str], list[Departure]] = {}
        for ride in self._rides_from(from_stops, service_date):
            firsts_by_stop.setdefault(
                (ride.agency, ride.to_stop.stop_id), []
            ).append(ride)
        for key, second in last_second.items():
            latest_arr_allowed = second.dep_min - transfer_buffer_min
            best_first: Optional[Departure] = None
            for first in firsts_by_stop.get(key, ()):
                if first.arr_min > latest_arr_allowed:
                    continue
                if best_first is None or (
                    first.dep_min, -first.arr_min, first.trip_id
                ) > (best_first.dep_min, -best_first.arr_min, best_first.trip_id):
                    best_first = first
            if best_first is not None:
                candidates.append((best_first, second))

        if not candidates:
            return None
        return max(
            candidates,
            key=lambda j: (
                j[0].dep_min,
                -len(j),
                -j[-1].arr_min,
                tuple(r.trip_id for r in j),
            ),
        )


def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def load_index(db_path: str) -> ScheduleIndex:
    return ScheduleIndex(db_path)
