"""Every buffer and threshold in one place, mirrored with rationale in
docs/DECISIONS.md. Each of these is a question an interviewer can ask.

Time convention used throughout Slackline: integer minutes since local
midnight of the plan's service date (America/Los_Angeles). Values may
exceed 1440 for post-midnight service, matching GTFS 24:xx encoding.
"""

# --- Transfer buffers -------------------------------------------------------
# Minutes required to board a *verified* transit departure after arriving at
# the stop. Covers finding the platform and schedule jitter.
TRANSFER_BUFFER_MIN = 10
# Minutes of walking-arrival buffer required at a venue before its scheduled
# activity begins.
WALK_ARRIVAL_BUFFER_MIN = 5

# --- Food gap ----------------------------------------------------------------
# Inside the eating window, a stretch without an eating opportunity longer
# than FAIL hours is a failure; longer than WARN hours is a warning.
FOOD_WINDOW_START_MIN = 11 * 60          # 11:00
FOOD_WINDOW_END_MIN = 21 * 60            # 21:00
FOOD_GAP_FAIL_MIN = int(6.0 * 60)        # 6 hours
FOOD_GAP_WARN_MIN = int(4.5 * 60)        # 4.5 hours

# --- Scheduler tie-breaking --------------------------------------------------
# Order: Selector rank ascending, then earliest feasible start, then candidate
# ID as the final stable key. Guarantees the determinism test passes.
# (Encoded in core/scheduler.py; listed here because it is a section-5 decision.)

# --- Last departure risk -----------------------------------------------------
# Fail if a planned leg departs after the day's last service; warn if the
# planned departure is within this many minutes of the last one.
LAST_DEPARTURE_WARN_MIN = 20

# --- Estimate model (always flagged "estimated") ------------------------------
WALK_SPEED_KMH = 4.8
DETOUR_FACTOR = 1.3                      # haversine -> street distance
# Unverified transit estimate per mode: effective door-to-door speed plus a
# flat wait. Used only when no verified schedule leg is available.
TRANSIT_ESTIMATE_SPEED_KMH = {"rail": 40.0, "bus": 16.0}
TRANSIT_ESTIMATE_WAIT_MIN = 12

# --- Day boundaries ------------------------------------------------------------
DAY_START_MIN_DEFAULT = 9 * 60           # 09:00
DAY_END_MIN_DEFAULT = 22 * 60 + 30       # 22:30

# --- Budget -------------------------------------------------------------------
# Budget = sum of venue price estimates + a flat transit allowance. Absent
# prices are excluded and disclosed, never guessed.
TRANSIT_ALLOWANCE_USD = 15.0

# --- Out of area ----------------------------------------------------------------
# Bounding box over the nine-county Bay Area (lat_min, lon_min, lat_max, lon_max).
# Outside is a soft warning plus best effort, never a failure.
BAY_AREA_BBOX = (36.85, -123.65, 38.95, -121.15)

# --- Cache ---------------------------------------------------------------------
# Cache keys: (stage, sha256 of canonical JSON inputs). Transit legs are keyed
# on origin, destination, service date, and departure time bucketed to this
# many minutes so repair iterations hit cache.
CACHE_TTL_SECONDS = 15 * 60
TRANSIT_CACHE_BUCKET_MIN = 15

# --- Repair loop -----------------------------------------------------------------
REPAIR_MAX_ITERATIONS = 2

# --- Scout -------------------------------------------------------------------------
SCOUT_POOL_MIN = 20
SCOUT_POOL_MAX = 40

# --- Streaming event schema ----------------------------------------------------------
SSE_EVENTS = (
    "stage_started",
    "stage_completed",
    "finding",
    "repair_iteration",
    "narration_delta",
    "done",
)

# --- Auditor check names (the six named checks) -----------------------------------------
CHECK_FEASIBILITY = "feasibility"                 # negative slack on any transition
CHECK_LAST_DEPARTURE = "last_departure_risk"      # stranded after last service
CHECK_VENUE_HOURS = "venue_hours"                 # closed venue fails, absent hours warn
CHECK_FOOD_GAP = "food_gap"                       # too long without eating opportunity
CHECK_BUDGET = "budget"                           # known prices + transit allowance vs cap
CHECK_OUT_OF_AREA = "out_of_area"                 # outside nine-county bbox, soft warn

ALL_CHECKS = (
    CHECK_FEASIBILITY,
    CHECK_LAST_DEPARTURE,
    CHECK_VENUE_HOURS,
    CHECK_FOOD_GAP,
    CHECK_BUDGET,
    CHECK_OUT_OF_AREA,
)
