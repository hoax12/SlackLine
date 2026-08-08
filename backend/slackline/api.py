"""FastAPI app: SSE plan endpoint, warmup endpoint, in-process token-bucket
rate limiting, explicit CORS.

Cold-start posture (plan section 8): the schedule index and events dataset
are opened lazily, the first SSE event is emitted before any real work, and
``/api/warmup`` lets the frontend warm the container while the user is still
reading the form.

Share URLs are zlib-deflated base64url JSON in the URL *fragment*, decoded
client-side only — they are never sent to this server, so there is nothing
here to receive them.
"""

from __future__ import annotations

import base64
import json
import os
import threading
import time
from typing import Iterator, Optional

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import ValidationError
from sse_starlette.sse import EventSourceResponse
from starlette.concurrency import iterate_in_threadpool

from slackline.core.state import PlanRequest
from slackline.events.dataset import EventsDataset, load_dataset
from slackline.llm import providers
from slackline.pipeline import Pipeline
from slackline.schedule.index import ScheduleIndex

# SLACKLINE_DOTENV=off skips .env loading — used to verify the zero-keys
# path on machines that do have a .env configured.
if os.environ.get("SLACKLINE_DOTENV", "").lower() != "off":
    load_dotenv()


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip().strip("'\"")


ALLOWED_ORIGINS = [o.strip() for o in _env("ALLOWED_ORIGINS").split(",") if o.strip()]
SCHEDULE_INDEX_PATH = _env("SCHEDULE_INDEX_PATH", "./data/schedule_index.sqlite")
EVENTS_DATASET_PATH = _env("EVENTS_DATASET_PATH", "./data/events.json")
RATE_LIMIT_RPM = int(_env("RATE_LIMIT_RPM", "20") or 20)

app = FastAPI(title="Slackline", version="1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS or ["http://localhost:5173"],
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)


# --- lazily opened shared resources ------------------------------------------

_lock = threading.Lock()
_index: Optional[ScheduleIndex] = None
_index_checked = False
_events: Optional[EventsDataset] = None
_events_checked = False


def get_index() -> Optional[ScheduleIndex]:
    global _index, _index_checked
    with _lock:
        if not _index_checked:
            _index_checked = True
            if os.path.exists(SCHEDULE_INDEX_PATH):
                _index = ScheduleIndex(SCHEDULE_INDEX_PATH)
        return _index


def get_events() -> Optional[EventsDataset]:
    global _events, _events_checked
    with _lock:
        if not _events_checked:
            _events_checked = True
            _events = load_dataset(EVENTS_DATASET_PATH)
        return _events


# --- in-process token bucket ---------------------------------------------------

class TokenBucket:
    """Per-client token bucket: RATE_LIMIT_RPM tokens, refilled per minute.
    In-process only, by design (non-negotiable in the plan)."""

    def __init__(self, rpm: int):
        self._rpm = rpm
        self._buckets: dict[str, tuple[float, float]] = {}
        self._lock = threading.Lock()

    def allow(self, client: str) -> bool:
        now = time.monotonic()
        with self._lock:
            tokens, last = self._buckets.get(client, (float(self._rpm), now))
            tokens = min(float(self._rpm), tokens + (now - last) * self._rpm / 60.0)
            if tokens < 1.0:
                self._buckets[client] = (tokens, now)
                return False
            self._buckets[client] = (tokens - 1.0, now)
            return True


_bucket = TokenBucket(RATE_LIMIT_RPM)


# --- endpoints -------------------------------------------------------------------

@app.get("/api/warmup")
def warmup() -> dict:
    """Fired by the frontend on page load, before the user has typed
    anything. Touches the lazy resources so the first real request is warm."""
    index = get_index()
    events = get_events()
    return {
        "status": "warm",
        "schedule_feed_date": index.build_date if index else None,
        "schedule_agencies": list(index.agencies) if index else [],
        "events_dataset_date": events.build_date if events else None,
        "model_configured": providers.from_env() is not None,
    }


@app.get("/api/health")
def health() -> dict:
    return {"status": "ok"}


@app.post("/api/plan")
async def plan_post(request: Request):
    payload = await request.json()
    return _plan_response(request, payload)


@app.get("/api/plan")
async def plan_get(request: Request, q: str):
    """GET variant for browser EventSource, which cannot POST. ``q`` is
    base64url-encoded JSON of the same PlanRequest body."""
    try:
        payload = json.loads(base64.urlsafe_b64decode(q + "=" * (-len(q) % 4)))
    except (ValueError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=422, detail="malformed q parameter") from exc
    return _plan_response(request, payload)


def _plan_response(request: Request, payload: dict) -> EventSourceResponse:
    client = request.client.host if request.client else "unknown"
    if not _bucket.allow(client):
        raise HTTPException(status_code=429, detail="rate limit exceeded")
    try:
        plan_request = PlanRequest.model_validate(payload)
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=exc.errors()) from exc

    def sse_events() -> Iterator[dict]:
        # First SSE event goes out before any real work (cold-start rule).
        yield {
            "event": "stage_started",
            "data": json.dumps({"stage": "pipeline", "iteration": 0}),
        }
        pipeline = Pipeline(
            index=get_index(),
            provider=providers.from_env(),
            fetch_weather=True,
            events_dataset=get_events(),
        )
        for event in pipeline.run(plan_request, request_id=plan_request.persona):
            name = event.pop("type")
            yield {"event": name, "data": json.dumps(event, default=str)}

    return EventSourceResponse(iterate_in_threadpool(sse_events()))
