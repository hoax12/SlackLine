# DECISIONS

Every non-obvious choice: what was chosen, the alternative, and why.
Constants live in `backend/slackline/core/constants.py`; this file is their
rationale mirror. Format: three lines per decision, roughly.

## Naming and framing

**"Two model stages and four deterministic services", never "agents".**
Alternative: call everything an agent, as the fashion goes.
The thesis is that verification must not be probabilistic; calling the
verifier an agent implies model involvement exactly where there is none.

## Architecture

**Precompiled GTFS SQLite index, queried offline at request time.**
Alternative: live 511/agency API calls per request.
Build-time compilation makes the Navigator zero-network, deterministic, and
offline-testable — strictly stronger than live checks. "Verified" means
verified against the official published schedule of a stated age, and the
UI shows the feed date.

**CT (Caltrain) and BA (BART) only, never the regional RG feed.**
Alternative: the consolidated RG feed covering every Bay Area operator.
RG bloats the image and slows index load; cold start is the tightest budget
in the project, and CT+BA cover both personas.

**Events are a dated precompiled artifact, no live API, no runtime scraping.**
Alternative: request-time scraping of public pages (previous plan) or paid
APIs. Meetup is Pro-gated, Eventbrite killed public search in 2019, and
runtime scraping is slow, blockable, and legally grey inside a 20-second
budget. Every entry carries a source URL + fetch timestamp; the UI shows
dataset age. Mirrors the GTFS design exactly.

**Repair does not always re-run the Selector.**
Alternative: full re-rank every iteration.
Most findings (negative slack, last-departure risk) are purely temporal;
re-ranking burns tokens to solve nothing. Scheduler-only repair for
temporal/ordering constraints; the Selector is re-invoked only when a
finding invalidates a choice (closed venue, budget overrun).

**Bounded repair: two iterations, then a deterministic degrade-to-anchors floor.**
Alternative: iterate until clean, or let the model "try harder".
An honest "here is what provably works" beats a fourth guess. Findings
accumulate across iterations and are never overwritten — repair convergence
is the strongest number this project produces and overwriting would make it
unmeasurable.

**Typed repair constraints (`ban_candidate`, `must_depart_by`,
`max_total_cost`, `require_meal_window`), no English parsing anywhere.**
Alternative: natural-language feedback loops between stages.
Typed objects are consumed natively by the Scheduler and rendered into the
Selector prompt as data; there is no parser to get wrong.

**Boundary rule with CI teeth: `core/` and `schedule/index` import nothing
from `sources/` or `llm/`, do no I/O, take all data as arguments.**
Enforced by `tests/contract/test_purity.py`, which imports core with sockets
monkeypatched to raise. Proves Scheduler/Auditor/Navigator run keyless and
offline, always.

## Stack

**Python 3.12 + FastAPI + Pydantic v2 frozen models.** Alternative:
TypeScript end to end. Frozen models give the typed state contract and
stage-ownership enforcement directly; state evolves only via `model_copy`.

**SSE via sse-starlette, not WebSockets.** Unidirectional is all this
needs, survives proxies and scale-to-zero better, and demos with `curl -N`.

**Plain REST via httpx for Gemini and Groq, no vendor SDKs.** Keeps the
image slim and the cold start short; both APIs are a single POST.

**Gemini Flash primary, Groq (Llama) automatic fallback, one interface.**
Both card-free with permanent free tiers, so a reviewer reproduces for $0.
Fallback engagement is recorded and was verified live (invalid Gemini key →
Groq answered, `fallback_engaged=True`).

**Gemini `thinkingBudget: 0` for both stages.** The 2.5 models spend
10-20 s "thinking" by default; ranking with one-line reasons and constrained
rewriting need none of it. Measured: Selector 19.2 s → 6.6 s, Narrator
9.8 s → 1.7 s.

**In-process TTL cache and in-process token-bucket rate limiting only.**
Alternative: Redis et al. Non-negotiable in the plan: one process, zero
infra dependencies, portfolio-scale traffic.

**Share URLs are zlib-deflated base64url JSON in the URL fragment.**
Fragments are never sent to the server, so shared plans cost zero backend
state and leak nothing into logs.

## Section-5 constants

**Transit boarding buffer: 10 min; walking arrival buffer at venues: 5 min.**
Covers finding the platform plus schedule jitter; venues forgive more than
train doors do.

**Food gap: fail > 6 h, warn > 4.5 h, inside 11:00-21:00 only.**
Outside the eating window a gap is sleep or ambition, not a planning error.

**Scheduler tie-breaking: Selector rank asc, then earliest feasible start,
then candidate ID.** The final stable key guarantees the
identical-input-identical-output test passes.

**Last-departure risk: fail if after the last service, warn within 20 min.**
20 minutes is one missed connection or one long goodbye.

**Estimates: walking 4.8 km/h over haversine × 1.3 detour factor; per-mode
transit speeds + flat wait. Always flagged `estimated`.** Verified and
estimated are never conflated — that distinction is the product.

**Day boundaries default 09:00-22:30, stretched to contain every anchor
with 30 min margin.** Anchors are inviolable; the window bends, not them.

**Budget = known venue prices + flat $15 transit allowance. Absent prices
excluded and disclosed, never guessed.** Absent is absent.

**Missing venue hours: warn "hours unverified", never fail.** OSM hours
coverage is incomplete; punishing missing data would teach sources to lie.

**Out of area: nine-county bounding box, outside is a soft warning.**
Best effort beats refusal; the warning keeps the claim honest.

**Cache keys: (stage, sha256 of canonical JSON inputs); transit legs
bucketed to 15-min departures** so repair iterations hit cache.

**Scout pool 20-40 candidates.** Fewer starves the Selector; more burns
tokens and scheduling time for options nobody reaches.

**GTFS validity horizon: dates outside any agency's calendar degrade to
estimates with an explicit notice.** A stale artifact is visible, never
silent.

**v1 is strictly single-day.** Persona B is scoped to the Saturday; the
last-BART-home constraint — the whole point — survives intact.

## Schedule index internals

**GTFS post-midnight times (24:xx) are stored as minutes > 1440, never
normalized.** Naive parsing concludes the last train is much earlier than
it is, which breaks the signature demo in the most embarrassing direction.
Frozen tests assert the 24:xx values survive end to end.

**`calendar_dates` overrides `calendar`.** Tested against Labor Day
2026-09-07, a Monday that runs Caltrain's weekend schedule (trip 665,
23:58) rather than the weekday 173 at 23:57.

**One-transfer same-agency journeys in the index (e.g. Orange line +
Yellow line at 19th St Oakland).** Single-seat rides understate late
service: after ~21:00 BART runs 3-line service and the last SF-bound
journey from Downtown Berkeley is Orange 00:17 → transfer → Yellow 00:56 →
Embarcadero 01:09. Cross-agency transfers are out of scope for v1.

**Aggressive pure memoization inside `ScheduleIndex`** (departure lists,
half-ride tables, per-intermediate bisect tables). The Scheduler probes
thousands of insertion positions per plan; measured effect 29.4 s → 3.4 s
per offline plan, warm scenario runs ~250 ms.

**Hand-verified frozen assertions** (checked against caltrain.com tables
and the official BART Aug 10, 2026 timetable PDFs on 2026-08-08):
* Caltrain weekday: trip 173 departs Palo Alto 23:57, arrives SF 00:48 (+1d)
* Caltrain Saturday: trip 665 departs Palo Alto 23:58, arrives SF 00:50 (+1d)
* BART weekday & Saturday: last SF-bound from Downtown Berkeley departs
  00:17 (+1d), transfers at 19th St Oakland to the 00:56 Yellow, arrives
  Embarcadero 01:09 (+1d)

## Rejected outright

* **SerpAPI** — 100 searches/month ≈ 3/day; one curious visitor exhausts it.
* **BART Legacy real-time API** — demo garnish; GTFS covers the requirement.
* **Foursquare / Yelp Fusion** — free tier too tight / paid-only (both
  unverified claims from the earlier plan; both rejected anyway).
* **Next.js** — SSR machinery unusable on a static host; Vite static build.
* **Playwright** — one Vitest suite for the URL codec is the only frontend
  logic that can corrupt data.
