"""OFFLINE build step: curated seed (plus optional public-page JSON-LD
harvest) -> dated events artifact.

No viable free events API exists in 2026 (Meetup is Pro-gated, Eventbrite
removed public search in 2019), so events mirror the GTFS design: a
scheduled job builds a dated artifact, every entry carries a source URL and
a fetch timestamp, the runtime reads it with zero network, and the UI shows
the dataset age.

The seed is a curated list of real, recurring SF Bay events with their
official pages as source URLs. The build expands recurrences over a horizon
of days. ``--harvest`` additionally tries schema.org/Event JSON-LD from the
seed pages; it is best-effort and off by default so the default build is
fully offline.

Usage:
    python -m slackline.events.build_events --out data/events.json
    python -m slackline.events.build_events --out data/events.json --days 90
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import sys
import urllib.request

# Recurrence: "weekly" fires on the listed weekdays (0=Mon); "monthly_first"
# fires on the first occurrence of the weekday in each month.
CURATED_SEED = [
    {
        "slug": "ferry-plaza-farmers-market",
        "title": "Ferry Plaza Farmers Market",
        "category": "food",
        "lat": 37.7955, "lon": -122.3937,
        "recurrence": "weekly", "weekdays": [5],
        "start_min": 8 * 60, "duration_min": 6 * 60,
        "price_usd": None, "is_food": True,
        "source_url": "https://foodwise.org/markets/ferry-plaza-farmers-market/",
        "description": "Saturday farmers market at the Ferry Building.",
    },
    {
        "slug": "off-the-grid-fort-mason",
        "title": "Off the Grid: Fort Mason Center",
        "category": "food",
        "lat": 37.8065, "lon": -122.4322,
        "recurrence": "weekly", "weekdays": [4],
        "start_min": 17 * 60, "duration_min": 5 * 60,
        "price_usd": None, "is_food": True,
        "source_url": "https://offthegrid.com/otg-market/fort-mason/",
        "description": "Friday-evening food-truck gathering on the waterfront.",
    },
    {
        "slug": "exploratorium-after-dark",
        "title": "Exploratorium After Dark",
        "category": "museum",
        "lat": 37.8017, "lon": -122.3973,
        "recurrence": "weekly", "weekdays": [3],
        "start_min": 18 * 60, "duration_min": 4 * 60,
        "price_usd": 19.95, "is_food": False,
        "source_url": "https://www.exploratorium.edu/visit/calendar/after-dark",
        "description": "Adults-only Thursday evenings at the Exploratorium.",
    },
    {
        "slug": "golden-gate-park-band",
        "title": "Golden Gate Park Band concert",
        "category": "music",
        "lat": 37.7702, "lon": -122.4662,
        "recurrence": "weekly", "weekdays": [6],
        "start_min": 13 * 60, "duration_min": 2 * 60,
        "price_usd": 0.0, "is_food": False,
        "source_url": "https://www.goldengateparkband.org/",
        "description": "Free Sunday concert at the Spreckels Temple of Music.",
    },
    {
        "slug": "oakland-first-fridays",
        "title": "Oakland First Fridays",
        "category": "festival",
        "lat": 37.8144, "lon": -122.2708,
        "recurrence": "monthly_first", "weekdays": [4],
        "start_min": 17 * 60, "duration_min": 4 * 60,
        "price_usd": 0.0, "is_food": True,
        "source_url": "https://www.oaklandfirstfridays.org/",
        "description": "Telegraph Avenue street festival, first Friday monthly.",
    },
    {
        "slug": "berkeley-farmers-market",
        "title": "Downtown Berkeley Farmers' Market",
        "category": "food",
        "lat": 37.8690, "lon": -122.2726,
        "recurrence": "weekly", "weekdays": [5],
        "start_min": 10 * 60, "duration_min": 5 * 60,
        "price_usd": None, "is_food": True,
        "source_url": "https://ecologycenter.org/fm/",
        "description": "Saturday market at Center Street and MLK Jr Way.",
    },
    {
        "slug": "sfjazz-evening",
        "title": "SFJAZZ Center evening performance",
        "category": "music",
        "lat": 37.7764, "lon": -122.4212,
        "recurrence": "weekly", "weekdays": [3, 4, 5, 6],
        "start_min": 19 * 60 + 30, "duration_min": 2 * 60,
        "price_usd": None, "is_food": False,
        "source_url": "https://www.sfjazz.org/",
        "description": "Typical Thu-Sun program; check the calendar for the bill.",
    },
    {
        "slug": "de-young-free-saturday",
        "title": "de Young Museum Free Saturdays",
        "category": "museum",
        "lat": 37.7715, "lon": -122.4687,
        "recurrence": "weekly", "weekdays": [5],
        "start_min": 9 * 60 + 30, "duration_min": 8 * 60,
        "price_usd": 0.0, "is_food": False,
        "source_url": "https://www.famsf.org/visit/free-days",
        "description": "Free general admission Saturdays (SF Bay residents).",
    },
]


def expand_seed(start: dt.date, days: int, fetched_at: str) -> list[dict]:
    out = []
    for day_offset in range(days):
        date = start + dt.timedelta(days=day_offset)
        for seed in CURATED_SEED:
            if date.weekday() not in seed["weekdays"]:
                continue
            if seed["recurrence"] == "monthly_first" and date.day > 7:
                continue
            out.append(
                {
                    "id": f"ev-{seed['slug']}-{date.isoformat()}",
                    "title": seed["title"],
                    "category": seed["category"],
                    "lat": seed["lat"],
                    "lon": seed["lon"],
                    "date": date.isoformat(),
                    "start_min": seed["start_min"],
                    "duration_min": seed["duration_min"],
                    "price_usd": seed["price_usd"],
                    "is_food": seed["is_food"],
                    "source_url": seed["source_url"],
                    "fetched_at": fetched_at,
                    "description": seed["description"],
                }
            )
    return out


# --- optional JSON-LD harvest --------------------------------------------------

_LDJSON_RE = re.compile(
    r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
    re.DOTALL | re.IGNORECASE,
)


def harvest_page(url: str, fetched_at: str, timeout_s: float = 10.0) -> list[dict]:
    """Best-effort schema.org/Event extraction from one public page.
    Anything malformed is skipped; failures return an empty list."""
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:
            html = resp.read().decode("utf-8", errors="replace")
    except Exception as exc:
        print(f"[harvest] {url}: {type(exc).__name__}")
        return []
    out = []
    for block in _LDJSON_RE.findall(html):
        try:
            data = json.loads(block)
        except ValueError:
            continue
        nodes = data if isinstance(data, list) else [data]
        for node in nodes:
            if not isinstance(node, dict) or node.get("@type") != "Event":
                continue
            event = _event_from_jsonld(node, url, fetched_at)
            if event is not None:
                out.append(event)
    return out


def _event_from_jsonld(node: dict, url: str, fetched_at: str) -> dict | None:
    try:
        start = dt.datetime.fromisoformat(node["startDate"])
        location = node.get("location", {})
        geo = location.get("geo", {})
        lat, lon = float(geo["latitude"]), float(geo["longitude"])
    except (KeyError, ValueError, TypeError):
        return None
    end_raw = node.get("endDate")
    duration_min = 120
    if end_raw:
        try:
            duration_min = max(
                30,
                int(
                    (dt.datetime.fromisoformat(end_raw) - start).total_seconds()
                    // 60
                ),
            )
        except ValueError:
            pass
    name = str(node.get("name", "Untitled event"))[:120]
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:40]
    return {
        "id": f"ev-{slug}-{start.date().isoformat()}",
        "title": name,
        "category": "event",
        "lat": lat,
        "lon": lon,
        "date": start.date().isoformat(),
        "start_min": start.hour * 60 + start.minute,
        "duration_min": duration_min,
        "price_usd": None,
        "is_food": False,
        "source_url": url,
        "fetched_at": fetched_at,
        "description": str(node.get("description", ""))[:200],
    }


def build(out_path: str, days: int, harvest: bool) -> dict:
    today = dt.date.today()
    fetched_at = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    events = expand_seed(today, days, fetched_at)
    if harvest:
        seen_urls = set()
        for seed in CURATED_SEED:
            url = seed["source_url"]
            if url in seen_urls:
                continue
            seen_urls.add(url)
            harvested = harvest_page(url, fetched_at)
            print(f"[harvest] {url}: {len(harvested)} event(s)")
            events.extend(harvested)
    # Deterministic order, id-deduped (harvest may duplicate the seed).
    deduped = {e["id"]: e for e in sorted(events, key=lambda e: e["id"])}
    artifact = {
        "build_date": today.isoformat(),
        "horizon_days": days,
        "events": list(deduped.values()),
    }
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump(artifact, fh, indent=1)
    print(
        f"wrote {out_path}: {len(artifact['events'])} dated event entries "
        f"({today.isoformat()} +{days}d)"
    )
    return artifact


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", default="data/events.json")
    ap.add_argument("--days", type=int, default=60, help="horizon in days")
    ap.add_argument(
        "--harvest", action="store_true",
        help="also try JSON-LD harvest from seed pages (network)",
    )
    args = ap.parse_args(argv)
    build(args.out, args.days, args.harvest)
    return 0


if __name__ == "__main__":
    sys.exit(main())
