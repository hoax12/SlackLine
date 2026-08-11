# How to use Slackline

A practical run guide. Every command here was run on Windows / PowerShell and
produced the output shown. For *what the system is and why*, see
[README.md](README.md); for design rationale, [docs/DECISIONS.md](docs/DECISIONS.md).

Slackline needs **no API keys**. Without keys it still verifies transit legs
against the real published GTFS schedule — only ranking and prose degrade to
deterministic fallbacks, and both are flagged in the UI.

---

## 1. One-time setup

### Backend

```powershell
cd backend
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
```

### Data artifacts

Two artifacts live in `backend/data/`. If they are already present
(`schedule_index.sqlite`, `events.json`), skip this step.

```powershell
.venv\Scripts\python -m slackline.schedule.build_gtfs --out data/schedule_index.sqlite --download
.venv\Scripts\python -m slackline.events.build_events --out data/events.json
```

The schedule build pulls Caltrain (CT) and BART (BA) only. It uses
`TRANSIT_511_API_KEY` if set and falls back to direct agency GTFS URLs if not.
The events build is fully offline from a curated seed.

### Frontend

```powershell
cd frontend
npm install
```

---

## 2. Run it

You need **two terminals**. The Vite dev server proxies `/api` to
`http://localhost:8000`, so the backend must be on port **8000** — see
[vite.config.ts](frontend/vite.config.ts).

**Terminal 1 — backend:**

```powershell
cd backend
.venv\Scripts\python -m uvicorn slackline.api:app --port 8000
```

**Terminal 2 — frontend:**

```powershell
cd frontend
npm run dev
```

Open <http://localhost:5173>.

To force the zero-keys path even when you have a `.env`, set
`SLACKLINE_DOTENV=off` before starting the backend:

```powershell
$env:SLACKLINE_DOTENV = "off"
```

Confirm the backend is healthy:

```powershell
curl.exe http://localhost:8000/api/warmup
```

```json
{"status":"warm","schedule_feed_date":"2026-08-08","schedule_agencies":["CT","BA"],
 "events_dataset_date":"2026-08-08","model_configured":false}
```

`model_configured: false` means keyless mode — expected without keys.

---

## 3. Using the app

1. **Pick a persona** (01 or 02). This fills the whole request — origin, hard
   anchor, interests, budget.
2. **Adjust date or budget** if you want. Everything else is fixed by the persona.
3. **Click "Verify my day."**
4. **Click "Share"** to copy a link that encodes the request in the URL
   fragment. Fragments never reach the server; the request is decoded entirely
   in the browser. If the clipboard is blocked, the button says
   "Link in address bar" — the URL is there either way.

### What you are looking at

The page fills in three numbered panels as the stream arrives:

| Panel | What it shows |
|---|---|
| **01 Live pipeline** | Every stage, its duration, each audit finding, and each repair pass as it happens. |
| **02 The proof** | Tightest slack, verified-leg ratio, feed ages, and the full timeline with the binding constraint on every transition. |
| **03 Plain English** | The readable summary, streamed token by token. |

The two numbers that matter are **minutes of slack at the tightest
transition** and the **binding constraint** naming what eats it
("anchor start 19:00", "last CT departure 23:57", "venue closes 22:00").

Provenance is never conflated. Each leg is badged `Verified <agency> <route>`
or `Estimated <mode>`, and the verified ratio counts **transit legs only** —
a walk is neither verified nor an unverified ride.

### What each persona demonstrates

**Persona A — SoMa → Palo Alto** (hard 19:00 dinner). The audit catches a
budget overrun, repair re-ranks, that opens an 8-hour food gap, a second pass
places a meal, and the third audit is clean:

```
Auditor  iter 0  → FAIL budget: estimated cost $87 exceeds budget $85
REPAIR · PASS 1    1 typed constraint · re-ranking
Auditor  iter 1  → FAIL food_gap: 8h00m without an eating opportunity
REPAIR · PASS 2    1 typed constraint · scheduler only
Auditor  iter 2  → AUDIT CLEAN
```

Result: 8 stops, 3 of 6 transit legs schedule-verified, 21 min at the tightest
transition (bound by the 19:00 anchor).

**Persona B — Berkeley → Civic Center** (Saturday show). Repair cannot clear
every failure, so the deterministic floor drops to hard anchors only. You get
a banner saying so and a 3-stop plan where everything shown is provably
reachable. This path is *supposed* to happen — it is the honest floor, not a
crash.

---

## 4. Calling the API directly

Streams Server-Sent Events. `-N` disables buffering so you see them arrive:

```powershell
curl.exe -N -X POST -H "Content-Type: application/json" --data "@scenarios/persona_a_request.json" http://localhost:8000/api/plan
```

Run it from `backend/`. Event types: `stage_started`, `stage_completed`,
`finding`, `repair_iteration`, `narration_delta`, `done`. The `done` event
carries the complete final state.

Note the wire format is **CRLF-delimited** (`\r\n\r\n` between frames). Any
custom client must split on that, not on `\n\n`.

Other endpoints: `GET /api/health`, `GET /api/warmup`, and `GET /api/plan?q=`
(base64url JSON, for `EventSource` which cannot POST).

Rate limit is 20 requests/minute per client, then HTTP 429.

---

## 5. Offline scenario batch

Runs both personas with no network and writes a batch record to
`backend/var/batch/`:

```powershell
.venv\Scripts\python -m scenarios.runner --db data/schedule_index.sqlite --repeat 3
```

```
persona_a run 0: 20088 ms, repairs=2, degraded=False, verified_legs=3/6
persona_b run 0: 30508 ms, repairs=2, degraded=True,  verified_legs=1/2
batch record -> var\batch\batch_20260809_183122.json
```

Each run gets a **fresh cache on purpose** (honest latency), so run 0 of each
persona pays the cold first-touch scan of the schedule index. Later repeats
are much faster — use `--repeat 3` or more if you want a meaningful p50.

Per-run telemetry lands in `backend/var/telemetry/` on every run, including
runs triggered from the UI.

---

## 6. Tests

```powershell
cd backend
.venv\Scripts\python -m pytest
```

46 tests, fully offline — a contract test bans sockets, so the deterministic
core is proven to need no network.

```powershell
cd frontend
npm test
```

4 tests covering the share-link codec. Type-check and production build:

```powershell
npm run build
```

---

## 7. Troubleshooting

**Plan never appears; button stuck on "Building your plan…"**
The backend is not reachable on port 8000, or is serving stale code. Check
`curl.exe http://localhost:8000/api/health`. The UI now surfaces an error
instead of hanging if the stream dies mid-flight.

**Backend seems to ignore your code changes**
`uvicorn` without `--reload` will not pick up edits, and on Windows a stale
process keeps port 8000 bound — the new one exits with
`WinError 10048: only one usage of each socket address ... is permitted`.
`pkill` does not work here. Kill it properly:

```powershell
Get-CimInstance Win32_Process -Filter "Name='python.exe'" | Where-Object { $_.CommandLine -like '*uvicorn*slackline*' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }
```

Or start with `--reload` during development.

**First plan takes 10–30 seconds**
Expected. The first request pays the first-touch scan of the full CT+BA
schedule index. The frontend calls `/api/warmup` on page load to hide most of
it; subsequent plans are a few seconds.

**Everything says "Estimated" rather than "Verified"**
`data/schedule_index.sqlite` is missing, or your date falls outside the feed's
validity window. Check `schedule_feed_date` from `/api/warmup` and rebuild the
index if it is stale.

**"Deterministic ranking" / "Heuristic" badges**
No model key configured. This is the supported zero-keys path, not a failure —
transit legs are still verified.

**Short in-city hops show as estimated transit**
Only Caltrain and BART are indexed. Muni and other local feeder transit are
out of scope for v1, so intra-SF hops fall back to the estimate model and are
flagged accordingly.

---

## 8. Optional keys

All free, none need a card. Put them in `backend/.env` (never committed):

| Variable | Used for |
|---|---|
| `TRANSIT_511_API_KEY` | GTFS download at build time only |
| `GEMINI_API_KEY` | Selector ranking + Narrator prose |
| `GROQ_API_KEY` | Fallback provider if Gemini fails |
| `GEOAPIFY_API_KEY` | Live place data instead of bundled fixtures |

Config: `ALLOWED_ORIGINS`, `SCHEDULE_INDEX_PATH`, `EVENTS_DATASET_PATH`,
`RATE_LIMIT_RPM`, `CACHE_TTL_SECONDS`.

Adding keys changes ranking quality and prose. It does **not** change whether
a leg is verified — that comes from the schedule index, which needs no key.
