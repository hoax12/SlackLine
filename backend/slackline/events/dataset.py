"""Read-only loader for the dated events artifact. Pure: zero network.

The artifact is built offline by ``build_events.py``, mirrors the GTFS
approach exactly, and carries a build date the UI must display. Every entry
carries a source URL and a fetch timestamp.
"""

from __future__ import annotations

import json
from typing import Optional

from slackline.core.state import Candidate, LatLng, OpenWindow

# A visit to an event does not have to span the whole event.
MAX_VISIT_MIN = 120


class EventsDataset:
    def __init__(self, path: str):
        with open(path, encoding="utf-8") as fh:
            raw = json.load(fh)
        self.build_date: str = raw["build_date"]
        self._events: list[dict] = raw["events"]

    def __len__(self) -> int:
        return len(self._events)

    def candidates_for(self, service_date: str) -> tuple[Candidate, ...]:
        """Events on the service date as Scout candidates, provenance
        ``events`` with source URL and fetch timestamp intact."""
        out = []
        for e in self._events:
            if e["date"] != service_date:
                continue
            start = int(e["start_min"])
            end = start + int(e["duration_min"])
            out.append(
                Candidate(
                    id=e["id"],
                    name=e["title"],
                    category=e["category"],
                    location=LatLng(lat=e["lat"], lon=e["lon"]),
                    duration_min=min(int(e["duration_min"]), MAX_VISIT_MIN),
                    is_food=bool(e["is_food"]),
                    price_usd=e["price_usd"],
                    open_windows=(OpenWindow(start_min=start, end_min=end),),
                    source="events",
                    source_url=e["source_url"],
                    fetched_at=e["fetched_at"],
                    description=e.get("description", ""),
                )
            )
        return tuple(sorted(out, key=lambda c: c.id))


def load_dataset(path: str) -> Optional[EventsDataset]:
    """None when the artifact is missing or unreadable; the pipeline then
    runs without event candidates and records a notice."""
    try:
        return EventsDataset(path)
    except (OSError, KeyError, ValueError):
        return None
