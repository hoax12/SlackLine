"""In-process TTL cache. The only cache this project has, by design.

Keys are ``(stage, sha256 of canonical JSON inputs)``. Transit-leg callers
bucket their departure time to 15 minutes before building the key (see
navigator.transit_cache_key) so repair iterations hit cache.
"""

from __future__ import annotations

import hashlib
import json
import time
from typing import Any, Optional

from slackline.core.constants import CACHE_TTL_SECONDS


def canonical_key(stage: str, payload: Any) -> tuple[str, str]:
    """(stage, sha256 of canonical JSON) — dict order never matters."""
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return stage, hashlib.sha256(raw.encode("utf-8")).hexdigest()


class TTLCache:
    def __init__(self, ttl_seconds: float = CACHE_TTL_SECONDS, clock=time.monotonic):
        self._ttl = ttl_seconds
        self._clock = clock
        self._store: dict[tuple[str, str], tuple[float, Any]] = {}
        self.hits: dict[str, int] = {}
        self.misses: dict[str, int] = {}

    def get(self, stage: str, key: str) -> Optional[Any]:
        entry = self._store.get((stage, key))
        if entry is not None and self._clock() - entry[0] <= self._ttl:
            self.hits[stage] = self.hits.get(stage, 0) + 1
            return entry[1]
        if entry is not None:
            del self._store[(stage, key)]
        self.misses[stage] = self.misses.get(stage, 0) + 1
        return None

    def set(self, stage: str, key: str, value: Any) -> None:
        self._store[(stage, key)] = (self._clock(), value)

    def stats(self) -> dict[str, dict[str, int]]:
        stages = set(self.hits) | set(self.misses)
        return {
            s: {"hits": self.hits.get(s, 0), "misses": self.misses.get(s, 0)}
            for s in sorted(stages)
        }
