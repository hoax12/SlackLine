"""Scout place source: Geoapify when a key is present, bundled fixture
venues otherwise. Either way the pipeline keeps running; provenance is
always explicit on every candidate.

Geoapify notes: OSM-backed hours coverage is incomplete. Missing hours stay
None — absent is absent; the Auditor warns and never fails on them.
"""

from __future__ import annotations

import datetime as dt
import json
import math
import os
import urllib.parse
import urllib.request
from typing import Optional

from slackline.core import constants
from slackline.core.state import Candidate, LatLng, OpenWindow, PlanRequest

_FIXTURE_PATH = os.path.join(os.path.dirname(__file__), "fixture_venues.json")

GEOAPIFY_URL = "https://api.geoapify.com/v2/places"
_CATEGORY_MAP = {
    "museum": "entertainment.museum",
    "park": "leisure.park",
    "food": "catering.restaurant,catering.cafe",
    "attraction": "tourism.attraction",
}
_DEFAULT_DURATIONS = {"museum": 100, "park": 60, "food": 50, "attraction": 60}


def fetch_candidates(
    request: PlanRequest,
    api_key: Optional[str] = None,
    timeout_s: float = 6.0,
) -> tuple[tuple[Candidate, ...], tuple[str, ...]]:
    """Return (candidates, notices). Never raises: any live failure falls
    back to fixtures with a notice."""
    api_key = api_key if api_key is not None else os.environ.get("GEOAPIFY_API_KEY", "")
    if not api_key:
        return _fixture_candidates(request), (
            "venues are bundled fixtures (no GEOAPIFY_API_KEY configured)",
        )
    try:
        live = _geoapify_candidates(request, api_key, timeout_s)
        if len(live) < constants.SCOUT_POOL_MIN:
            fixtures = _fixture_candidates(request)
            merged = live + tuple(
                f for f in fixtures if f.id not in {c.id for c in live}
            )
            return merged[: constants.SCOUT_POOL_MAX], (
                f"Geoapify returned only {len(live)} venues; topped up with fixtures",
            )
        return live[: constants.SCOUT_POOL_MAX], ()
    except Exception as exc:
        return _fixture_candidates(request), (
            f"Geoapify unavailable ({type(exc).__name__}); using fixture venues",
        )


def _fixture_candidates(request: PlanRequest) -> tuple[Candidate, ...]:
    with open(_FIXTURE_PATH, encoding="utf-8") as fh:
        data = json.load(fh)
    out = []
    for v in data["venues"]:
        out.append(
            Candidate(
                id=v["id"],
                name=v["name"],
                category=v["category"],
                location=LatLng(lat=v["lat"], lon=v["lon"]),
                duration_min=v["duration_min"],
                is_food=v["is_food"],
                price_usd=v["price_usd"],
                open_windows=(
                    tuple(OpenWindow(start_min=w[0], end_min=w[1]) for w in v["open"])
                    if v["open"] is not None
                    else None
                ),
                source="fixture",
                description=v.get("description", ""),
            )
        )
    return tuple(sorted(out, key=lambda c: c.id))


def _geoapify_candidates(
    request: PlanRequest, api_key: str, timeout_s: float
) -> tuple[Candidate, ...]:
    per_category = max(6, constants.SCOUT_POOL_MAX // len(_CATEGORY_MAP))
    fetched_at = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    out: list[Candidate] = []
    for category, geo_cats in _CATEGORY_MAP.items():
        params = urllib.parse.urlencode(
            {
                "categories": geo_cats,
                "filter": f"circle:{request.origin.lon},{request.origin.lat},8000",
                "bias": f"proximity:{request.origin.lon},{request.origin.lat}",
                "limit": per_category,
                "apiKey": api_key,
            }
        )
        req = urllib.request.Request(
            f"{GEOAPIFY_URL}?{params}", headers={"User-Agent": "slackline/1.0"}
        )
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:
            payload = json.load(resp)
        for feature in payload.get("features", []):
            props = feature.get("properties", {})
            place_id = props.get("place_id")
            name = props.get("name")
            if not place_id or not name:
                continue
            out.append(
                Candidate(
                    id=f"geo-{place_id[:24]}",
                    name=name,
                    category=category,
                    location=LatLng(lat=props["lat"], lon=props["lon"]),
                    duration_min=_DEFAULT_DURATIONS[category],
                    is_food=category == "food",
                    price_usd=None,  # Geoapify has no prices; never guessed
                    open_windows=_parse_hours(props.get("opening_hours")),
                    source="geoapify",
                    source_url=props.get("website"),
                    fetched_at=fetched_at,
                    description=props.get("address_line2", ""),
                )
            )
    deduped = {c.id: c for c in out}
    return tuple(sorted(deduped.values(), key=lambda c: c.id))


def _parse_hours(raw: Optional[str]) -> Optional[tuple[OpenWindow, ...]]:
    """Parse the trivial 'Mo-Su 09:00-17:00' shape; anything more complex
    stays None (absent is absent) rather than being misread."""
    if not raw:
        return None
    try:
        span = raw.split()[-1]
        start_txt, end_txt = span.split("-")
        sh, sm = start_txt.split(":")
        eh, em = end_txt.split(":")
        start, end = int(sh) * 60 + int(sm), int(eh) * 60 + int(em)
        if 0 <= start < end <= 24 * 60:
            return (OpenWindow(start_min=start, end_min=end),)
    except (ValueError, IndexError):
        pass
    return None
