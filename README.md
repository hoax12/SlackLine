# Slackline

A day planner for the SF Bay Area whose product is not the itinerary but
**the proof that the itinerary works**.

Two model stages (Selector, Narrator) and four deterministic services
(Scout sources, Scheduler, Navigator, Auditor) run over one shared typed
state contract with a bounded repair loop. Only the two model stages touch
a model, and they are only allowed to rank with reasons and to describe.
All time arithmetic — scheduling, travel legs, feasibility — is
deterministic code, so the audit is trustworthy and cheap.

The demo moment: the system catches that a 7pm meetup in Palo Alto strands
you after the last northbound Caltrain, fixes it, and re-verifies —
visibly, in the event stream.

```
request ──> Scout ──> Selector ──> Scheduler ──> Navigator ──> Auditor ──> Narrator
            (data)    (model,      (determin.)   (determin.,   (determin.,  (model,
                       ranks only)               GTFS index)   6 checks)    describes only)
                          ▲                                        │
                          └───── bounded repair loop (≤2) ─────────┘
                                 typed constraints, no English
                                 floor: degrade to anchors
```

## What makes it different

* **Hard anchors are inviolable constraints.** The Scheduler plans around
  them and may never move, shorten, or drop them.
* **Per-transition slack in minutes is the headline metric**, with the
  binding constraint named ("last CT departure 23:57", "venue closes 17:00").
* **Every transit leg is either `verified` against the real published GTFS
  schedule or `estimated` — never conflated.** The UI badges both, plus the
  age of the schedule feed and the events dataset.
* **When the audit fails, a bounded repair loop runs** (two iterations,
  Scheduler-only for temporal findings, Selector re-invoked only when a
  choice is invalidated), with a deterministic degrade-to-anchors floor.
  Findings accumulate across iterations and are never overwritten.

## Running locally with zero keys

A reviewer can clone and run with nothing configured:

```powershell
cd backend
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt

# offline test suite (network-blocked by contract tests)
.venv\Scripts\python -m pytest

# build the schedule index (keyless fallback: direct agency GTFS URLs)
.venv\Scripts\python -m slackline.schedule.build_gtfs --out data/schedule_index.sqlite --download

# build the dated events artifact (curated seed, fully offline)
.venv\Scripts\python -m slackline.events.build_events --out data/events.json

# offline scenario batch: personas A and B, heuristic ranking, batch record
.venv\Scripts\python -m scenarios.runner --db data/schedule_index.sqlite --repeat 3

# API server
.venv\Scripts\python -m uvicorn slackline.api:app --port 8000
```

Frontend (dev server proxies `/api` to `localhost:8000`):

```powershell
cd frontend
npm install
npm test        # Vitest: fragment share codec
npm run dev     # http://localhost:5173
npm run build   # static build in dist/
```

Then stream a plan:

```bash
curl -N -X POST -H "Content-Type: application/json" \
  -d @scenarios/persona_a_request.json http://localhost:8000/api/plan
```

With no keys at all: Scout serves bundled fixture venues flagged as such,
the Selector degrades to a deterministic heuristic ranking flagged
`heuristic: true`, and the Narrator emits a template summary. The pipeline
still runs end to end and transit legs are still **verified** — the
schedule index needs no key.

### Keys (all free, none need a card)

Copy `backend/.env` from your own values (never committed):
`TRANSIT_511_API_KEY` (511.org, build-time only), `GEMINI_API_KEY`
(AI Studio), `GROQ_API_KEY` (fallback), `GEOAPIFY_API_KEY` (places).
Plus config: `ALLOWED_ORIGINS`, `SCHEDULE_INDEX_PATH`,
`EVENTS_DATASET_PATH`, `RATE_LIMIT_RPM`, `CACHE_TTL_SECONDS`.

## Verified against reality

The GTFS build pulls only operators CT and BA from 511 (never the regional
feed), compiles a compact SQLite index, and handles the GTFS post-midnight
encoding (`24:40` stays 1480 minutes). Hand-verified against public
schedules on 2026-08-08 and frozen as test assertions:

| journey | weekday (2026-08-12) | Saturday (2026-08-15) |
|---|---|---|
| Last northbound Caltrain, Palo Alto → SF | **23:57** (trip 173, arr 00:48+1d) | **23:58** (trip 665, arr 00:50+1d) |
| Last SF-bound BART, Downtown Berkeley → Embarcadero | **00:17+1d**, Orange → Yellow transfer at 19th St, arr 01:09+1d | same |

Holiday exceptions are tested too: Labor Day 2026-09-07 (a Monday) runs
Caltrain's weekend schedule via `calendar_dates`.

## Measured numbers (local, 2026-08-08)

* Offline plan, warm process: **~250 ms** end to end (p50 of batch runs);
  cold first plan pays ~10 s of first-touch SQL against the full index.
* Live plan with keys (Gemini 2.5 Flash, thinking disabled): Scout ~3 s,
  Selector ~6.6 s (≈2.6k tokens in / 1.3k out), Scheduler warm ~50 ms per
  repair iteration, Narrator ~1.7 s streaming.
* Repair convergence (offline batch): persona A clears after 2 passes;
  persona B exercises the deterministic degrade-to-anchors floor.
* Provider fallback verified live: invalid Gemini key → Groq
  (`llama-3.3-70b-versatile`) answered, `fallback_engaged=True`.
* Rate limiting verified: 20 requests pass per minute per client, then 429.

Telemetry records land in `backend/var/telemetry/` on every run; batch
records in `backend/var/batch/`.

## Repository layout

```
backend/
  slackline/
    core/        DETERMINISTIC: state contract, constants, scheduler,
                 navigator, auditor, repair. Zero network, zero keys —
                 enforced by a socket-ban contract test.
    schedule/    build_gtfs.py (offline build), index.py (pure loader)
    events/      build_events.py (offline build), dataset.py (pure loader)
    sources/     places.py (Geoapify), weather.py (Open-Meteo)
    llm/         providers.py (Gemini→Groq), selector.py, narrator.py
    pipeline.py  orchestration, repair wiring, SSE event emission
    telemetry.py per-request + batch records
    cache.py     in-process TTL cache
    api.py       FastAPI: SSE endpoint, warmup, rate limiting, CORS
  tests/         unit/, contract/, fixtures/ — fully offline
  scenarios/     runner.py + persona JSONs
frontend/        Vite + React + TS static build
docs/DECISIONS.md  every non-obvious choice, three lines each
```

## Honest limitations

* Events are a dated curated artifact (no viable free events API exists in
  2026); the UI shows dataset age, and every entry carries a source URL and
  fetch timestamp.
* Cross-agency transfers (BART↔Caltrain) are out of scope for v1; journeys
  are direct or one same-agency transfer.
* Weather is a context string for the prompts, never a feasibility input.
* v1 plans exactly one day.
* The first request after a cold start pays the first-touch schedule-index
  scan (~10 s on the full CT+BA index); the warmup endpoint hides most of
  it and the number is reported, not hidden.
