"""OFFLINE build step: GTFS zips -> compact SQLite schedule index.

This is the only schedule module allowed to touch the network, and only at
build time. The runtime reads the resulting SQLite file with zero network.

Source preference order:
1. 511 SF Bay datafeeds API (one consistent curated source) when
   ``TRANSIT_511_API_KEY`` is set.
2. Direct agency URLs (Caltrain and BART publish GTFS with no key).
3. Pre-downloaded zips in ``--source-dir``.

Midnight boundary: GTFS encodes post-midnight service as hours past 24
(``24:40`` is 00:40 the next day). Times are stored as raw minutes past
midnight of the service date, so 24:40 -> 1480. Never normalized.

Usage:
    python -m slackline.schedule.build_gtfs --out data/schedule_index.sqlite
    python -m slackline.schedule.build_gtfs --out fixture.sqlite \
        --filter-stops "san francisco,palo alto,millbrae,embarcadero,downtown berkeley"
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import io
import os
import sqlite3
import sys
import urllib.parse
import urllib.request
import zipfile

AGENCIES = ("CT", "BA")

# Ordered fallbacks per agency. caltrain.com now serves an HTML page at its
# legacy path, so the Trillium producer URL (the one Transitland/Mobility
# Database list as current) comes first for CT.
DIRECT_URLS = {
    "CT": (
        "https://data.trilliumtransit.com/gtfs/caltrain-ca-us/caltrain-ca-us.zip",
        "https://www.caltrain.com/files/rt/GTFS/CT-GTFS.zip",
    ),
    "BA": ("https://www.bart.gov/dev/schedules/google_transit.zip",),
}
API_511_URL = "https://api.511.org/transit/datafeeds?api_key={key}&operator_id={op}"

USER_AGENT = "slackline-build/1.0 (+portfolio GTFS index builder)"

SCHEMA = """
CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE stops (
    agency TEXT NOT NULL, stop_id TEXT NOT NULL, name TEXT NOT NULL,
    lat REAL NOT NULL, lon REAL NOT NULL,
    PRIMARY KEY (agency, stop_id)
);
CREATE TABLE routes (
    agency TEXT NOT NULL, route_id TEXT NOT NULL,
    short_name TEXT, long_name TEXT, route_type INTEGER,
    PRIMARY KEY (agency, route_id)
);
CREATE TABLE trips (
    agency TEXT NOT NULL, trip_id TEXT NOT NULL,
    route_id TEXT NOT NULL, service_id TEXT NOT NULL,
    direction_id INTEGER, headsign TEXT,
    PRIMARY KEY (agency, trip_id)
);
CREATE TABLE stop_times (
    agency TEXT NOT NULL, trip_id TEXT NOT NULL, stop_id TEXT NOT NULL,
    seq INTEGER NOT NULL, arr_min INTEGER NOT NULL, dep_min INTEGER NOT NULL
);
CREATE INDEX idx_st_stop ON stop_times (agency, stop_id);
CREATE INDEX idx_st_trip ON stop_times (agency, trip_id);
CREATE TABLE calendar (
    agency TEXT NOT NULL, service_id TEXT NOT NULL,
    monday INTEGER, tuesday INTEGER, wednesday INTEGER, thursday INTEGER,
    friday INTEGER, saturday INTEGER, sunday INTEGER,
    start_date TEXT NOT NULL, end_date TEXT NOT NULL,
    PRIMARY KEY (agency, service_id)
);
CREATE TABLE calendar_dates (
    agency TEXT NOT NULL, service_id TEXT NOT NULL,
    date TEXT NOT NULL, exception_type INTEGER NOT NULL
);
"""


def parse_gtfs_time(value: str) -> int:
    """'24:40:00' -> 1480. GTFS hours may exceed 24 for post-midnight
    service; keep them that way."""
    parts = value.strip().split(":")
    hours, minutes = int(parts[0]), int(parts[1])
    return hours * 60 + minutes


def _download(url: str, dest: str) -> None:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=120) as resp:
        data = resp.read()
    if not data.startswith(b"PK"):
        raise RuntimeError(f"{url} did not return a zip (got {data[:16]!r})")
    with open(dest, "wb") as fh:
        fh.write(data)


def fetch_zip(agency: str, source_dir: str, download: bool) -> str:
    """Return a local path to the agency's GTFS zip, downloading if asked."""
    path = os.path.join(source_dir, f"{agency}.zip")
    if os.path.exists(path) and not download:
        with open(path, "rb") as fh:
            if fh.read(2) == b"PK":
                return path
        # Not a real zip (e.g. an HTML error page): re-download.
    os.makedirs(source_dir, exist_ok=True)
    # .env values are sometimes pasted with wrapping quotes; strip them.
    key = os.environ.get("TRANSIT_511_API_KEY", "").strip().strip("'\"")
    urls = []
    if key:
        urls.append(API_511_URL.format(key=urllib.parse.quote(key), op=agency))
    urls.extend(DIRECT_URLS[agency])
    last_error: Exception | None = None
    for url in urls:
        try:
            print(f"[{agency}] downloading {url.split('?')[0]} ...")
            _download(url, path)
            return path
        except Exception as exc:  # try next source
            last_error = exc
            print(f"[{agency}] failed: {exc}")
    raise RuntimeError(f"could not fetch GTFS for {agency}: {last_error}")


def _rows(zf: zipfile.ZipFile, name: str):
    """Iterate DictReader rows for a member file; empty if absent."""
    try:
        info = zf.getinfo(name)
    except KeyError:
        return
    with zf.open(info) as fh:
        text = io.TextIOWrapper(fh, encoding="utf-8-sig", newline="")
        yield from csv.DictReader(text)


def load_agency(
    con: sqlite3.Connection,
    agency: str,
    zip_path: str,
    stop_name_filter: list[str] | None,
) -> dict:
    """Load one agency's GTFS into the index. Returns summary stats."""
    zf = zipfile.ZipFile(zip_path)

    kept_stop_ids: set[str] | None = None
    stops = []
    for row in _rows(zf, "stops.txt"):
        if not row.get("stop_lat") or not row.get("stop_lon"):
            continue
        stops.append(
            (
                agency,
                row["stop_id"],
                row.get("stop_name", ""),
                float(row["stop_lat"]),
                float(row["stop_lon"]),
            )
        )
    if stop_name_filter:
        kept_stop_ids = {
            s[1]
            for s in stops
            if any(f in s[2].lower() for f in stop_name_filter)
        }
        stops = [s for s in stops if s[1] in kept_stop_ids]
    con.executemany("INSERT OR REPLACE INTO stops VALUES (?,?,?,?,?)", stops)

    routes = [
        (
            agency,
            row["route_id"],
            row.get("route_short_name", ""),
            row.get("route_long_name", ""),
            int(row.get("route_type") or 0),
        )
        for row in _rows(zf, "routes.txt")
    ]
    con.executemany("INSERT OR REPLACE INTO routes VALUES (?,?,?,?,?)", routes)

    trips = [
        (
            agency,
            row["trip_id"],
            row["route_id"],
            row["service_id"],
            int(row["direction_id"]) if row.get("direction_id") else None,
            row.get("trip_headsign", ""),
        )
        for row in _rows(zf, "trips.txt")
    ]
    con.executemany("INSERT OR REPLACE INTO trips VALUES (?,?,?,?,?,?)", trips)

    n_stop_times = 0
    batch = []
    kept_trip_ids: set[str] = set()
    for row in _rows(zf, "stop_times.txt"):
        stop_id = row["stop_id"]
        if kept_stop_ids is not None and stop_id not in kept_stop_ids:
            continue
        arr = row.get("arrival_time") or row.get("departure_time")
        dep = row.get("departure_time") or row.get("arrival_time")
        if not arr or not dep:
            continue
        batch.append(
            (
                agency,
                row["trip_id"],
                stop_id,
                int(row["stop_sequence"]),
                parse_gtfs_time(arr),
                parse_gtfs_time(dep),
            )
        )
        kept_trip_ids.add(row["trip_id"])
        n_stop_times += 1
        if len(batch) >= 20000:
            con.executemany("INSERT INTO stop_times VALUES (?,?,?,?,?,?)", batch)
            batch = []
    con.executemany("INSERT INTO stop_times VALUES (?,?,?,?,?,?)", batch)

    if kept_stop_ids is not None:
        # Trim trips that no longer touch any kept stop.
        con.execute(
            "DELETE FROM trips WHERE agency=? AND trip_id NOT IN "
            "(SELECT DISTINCT trip_id FROM stop_times WHERE agency=?)",
            (agency, agency),
        )

    cal = [
        (
            agency,
            row["service_id"],
            int(row["monday"]),
            int(row["tuesday"]),
            int(row["wednesday"]),
            int(row["thursday"]),
            int(row["friday"]),
            int(row["saturday"]),
            int(row["sunday"]),
            row["start_date"],
            row["end_date"],
        )
        for row in _rows(zf, "calendar.txt")
    ]
    con.executemany(
        "INSERT OR REPLACE INTO calendar VALUES (?,?,?,?,?,?,?,?,?,?,?)", cal
    )

    cal_dates = [
        (agency, row["service_id"], row["date"], int(row["exception_type"]))
        for row in _rows(zf, "calendar_dates.txt")
    ]
    con.executemany("INSERT INTO calendar_dates VALUES (?,?,?,?)", cal_dates)

    # Validity horizon: calendar range plus any added exception dates.
    dates = [c[9] for c in cal] + [c[10] for c in cal] + [c[2] for c in cal_dates]
    start = min(dates) if dates else ""
    end = max(dates) if dates else ""
    con.execute(
        "INSERT OR REPLACE INTO meta VALUES (?,?)", (f"feed_start_{agency}", start)
    )
    con.execute(
        "INSERT OR REPLACE INTO meta VALUES (?,?)", (f"feed_end_{agency}", end)
    )
    return {
        "stops": len(stops),
        "trips": len(trips) if kept_stop_ids is None else len(kept_trip_ids),
        "stop_times": n_stop_times,
        "calendar": len(cal),
        "calendar_dates": len(cal_dates),
        "validity": (start, end),
    }


def build(
    out_path: str,
    source_dir: str,
    download: bool,
    filter_stops: list[str] | None,
    agencies: tuple[str, ...] = AGENCIES,
) -> None:
    if os.path.exists(out_path):
        os.remove(out_path)
    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    con = sqlite3.connect(out_path)
    con.executescript(SCHEMA)
    con.execute(
        "INSERT INTO meta VALUES ('build_date', ?)",
        (dt.date.today().isoformat(),),
    )
    con.execute("INSERT INTO meta VALUES ('agencies', ?)", (",".join(agencies),))
    for agency in agencies:
        zip_path = fetch_zip(agency, source_dir, download)
        stats = load_agency(con, agency, zip_path, filter_stops)
        print(f"[{agency}] {stats}")
    con.commit()
    con.execute("VACUUM")
    con.close()
    size_kb = os.path.getsize(out_path) // 1024
    print(f"wrote {out_path} ({size_kb} KB)")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", required=True, help="output SQLite path")
    ap.add_argument("--source-dir", default="data/raw", help="where GTFS zips live")
    ap.add_argument(
        "--download", action="store_true",
        help="force re-download even if zips exist locally",
    )
    ap.add_argument(
        "--filter-stops", default=None,
        help="comma-separated stop-name substrings; keeps only matching stops "
             "(used to build the small committed test fixture)",
    )
    args = ap.parse_args(argv)
    filters = (
        [f.strip().lower() for f in args.filter_stops.split(",") if f.strip()]
        if args.filter_stops
        else None
    )
    build(args.out, args.source_dir, args.download, filters)
    return 0


if __name__ == "__main__":
    sys.exit(main())
