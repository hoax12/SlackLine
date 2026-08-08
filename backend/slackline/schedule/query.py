"""CLI: print the last departures that anchor the signature demo.

    python -m slackline.schedule.query --db data/schedule_index.sqlite \
        --date 2026-08-12

Prints the last northbound Caltrain (Palo Alto -> San Francisco) and the
last SF-bound BART (Downtown Berkeley -> Embarcadero) for the given service
date, post-midnight 24:xx service included.
"""

from __future__ import annotations

import argparse

from slackline.core.state import minutes_to_hhmm
from slackline.schedule.index import ScheduleIndex, Stop


def stops_by_name(index: ScheduleIndex, agency: str, needle: str) -> list[Stop]:
    needle = needle.lower()
    return [
        s for s in index.all_stops()
        if s.agency == agency and needle in s.name.lower()
    ]


def report_last(
    index: ScheduleIndex,
    agency: str,
    from_name: str,
    to_name: str,
    date: str,
    label: str,
) -> None:
    from slackline.core.constants import TRANSFER_BUFFER_MIN

    from_stops = stops_by_name(index, agency, from_name)
    to_stops = stops_by_name(index, agency, to_name)
    if not from_stops or not to_stops:
        print(f"{label}: stops not found ({from_name!r} / {to_name!r})")
        return
    journey = index.last_journey(from_stops, to_stops, date, TRANSFER_BUFFER_MIN)
    if not journey:
        print(f"{label}: no service on {date}")
        return
    first, final = journey[0], journey[-1]
    via = (
        f" via {first.to_stop.name} (transfer)" if len(journey) > 1 else ""
    )
    print(
        f"{label}: {minutes_to_hhmm(first.dep_min)} from {first.from_stop.name}"
        f" (route {' > '.join(dict.fromkeys(r.route for r in journey))},"
        f" trip {first.trip_id}{via},"
        f" arrives {final.to_stop.name} {minutes_to_hhmm(final.arr_min)})"
    )


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", default="data/schedule_index.sqlite")
    ap.add_argument("--date", required=True, help="service date YYYY-MM-DD")
    args = ap.parse_args(argv)

    index = ScheduleIndex(args.db)
    print(f"index build date: {index.build_date}")
    for agency in index.agencies:
        start, end = index.validity(agency)
        print(f"{agency} feed validity: {start}..{end}")
    print(f"service date: {args.date}")
    report_last(
        index, "CT", "palo alto", "san francisco", args.date,
        "last northbound Caltrain (Palo Alto -> San Francisco)",
    )
    report_last(
        index, "BA", "downtown berkeley", "embarcadero", args.date,
        "last SF-bound BART (Downtown Berkeley -> Embarcadero)",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
