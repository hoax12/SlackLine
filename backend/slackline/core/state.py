"""The typed state contract every stage and service reads and writes.

All models are frozen (immutable). Stages evolve the state via
``model_copy(update=...)``, which makes ownership auditable: nothing can
mutate another stage's section in place.

Time convention: integer minutes since local midnight of the plan's service
date. Values may exceed 1440 for post-midnight service (GTFS 24:xx).
"""

from __future__ import annotations

from typing import Annotated, Literal, Optional, Union

from pydantic import BaseModel, ConfigDict, Field

from slackline.core import constants


class Frozen(BaseModel):
    model_config = ConfigDict(frozen=True)


class LatLng(Frozen):
    lat: float
    lon: float


class OpenWindow(Frozen):
    """One open interval on the plan's service date, minutes since midnight."""

    start_min: int
    end_min: int


class Anchor(Frozen):
    """A hard, inviolable commitment. The Scheduler plans around anchors and
    may never move, shorten, or drop them (except the degrade-to-anchors
    floor, which keeps *only* them)."""

    id: str
    title: str
    location: LatLng
    start_min: int
    end_min: int
    notes: str = ""


VenueSource = Literal["fixture", "geoapify", "events"]


class Candidate(Frozen):
    """A visitable venue or event produced by Scout sources.

    ``open_windows is None`` means hours are unknown — absent is absent; the
    Auditor warns "hours unverified" and never fails on missing data.
    ``price_usd is None`` means unknown; budget math excludes and discloses it.
    """

    id: str
    name: str
    category: str
    location: LatLng
    duration_min: int
    is_food: bool = False
    price_usd: Optional[float] = None
    open_windows: Optional[tuple[OpenWindow, ...]] = None
    source: VenueSource = "fixture"
    source_url: Optional[str] = None
    fetched_at: Optional[str] = None  # ISO-8601 provenance timestamp
    description: str = ""


class RankedCandidate(Frozen):
    candidate_id: str
    rank: int  # 1 = best
    reason: str


class SelectorResult(Frozen):
    """Output of the Selector stage: rank plus one-line reasons, nothing else.

    ``heuristic=True`` marks the deterministic keyless fallback ranking, which
    the UI must flag as such.
    """

    rankings: tuple[RankedCandidate, ...]
    heuristic: bool
    model: Optional[str] = None
    tokens_in: int = 0
    tokens_out: int = 0


LegMode = Literal["walk", "transit"]
LegProvenance = Literal["verified", "estimated"]


class Leg(Frozen):
    """A movement between two plan points.

    ``provenance="verified"`` means the departure/arrival times come from the
    official published GTFS schedule of a stated age. ``"estimated"`` means
    the haversine/speed model. The two are never conflated.
    """

    from_ref: str  # "origin" or an anchor/candidate id
    to_ref: str
    mode: LegMode
    depart_min: int
    arrive_min: int
    provenance: LegProvenance
    agency: Optional[str] = None
    route: Optional[str] = None
    trip_id: Optional[str] = None
    from_stop: Optional[str] = None
    to_stop: Optional[str] = None
    # Scheduled transit departure/arrival at the stops (verified legs only);
    # depart_min/arrive_min above are door-to-door including stop walks.
    transit_depart_min: Optional[int] = None
    transit_arrive_min: Optional[int] = None
    # Last scheduled departure of the day for the same stop pair, so the
    # Auditor and UI can name the binding constraint without index access.
    last_depart_of_day_min: Optional[int] = None
    note: str = ""


class ItineraryItem(Frozen):
    kind: Literal["anchor", "candidate", "origin"]
    ref_id: str
    start_min: int
    end_min: int


class Transition(Frozen):
    """The gap between consecutive itinerary items: the headline metric.

    ``slack_min`` is minutes of spare time after travel and buffers.
    ``binding_constraint`` names what eats the slack (e.g. "last BART 24:07",
    "venue closes 17:00", "next anchor start 19:00").
    """

    from_ref: str
    to_ref: str
    leg: Leg
    slack_min: int
    binding_constraint: str


class Itinerary(Frozen):
    items: tuple[ItineraryItem, ...]
    transitions: tuple[Transition, ...]


CheckName = Literal[
    "feasibility",
    "last_departure_risk",
    "venue_hours",
    "food_gap",
    "budget",
    "out_of_area",
]
Severity = Literal["fail", "warn"]


class Finding(Frozen):
    """A structured audit result. Findings accumulate across repair
    iterations and are never overwritten.

    ``window`` carries the concrete time interval a check refers to (e.g.
    the exact food gap), so repair can consume it as data — no English
    parsing anywhere.
    """

    check: CheckName
    severity: Severity
    message: str
    subject_ids: tuple[str, ...] = ()
    window: Optional[tuple[int, int]] = None
    iteration: int = 0


# --- Typed repair constraints (no English parsing anywhere) -----------------


class BanCandidate(Frozen):
    kind: Literal["ban_candidate"] = "ban_candidate"
    candidate_id: str
    reason: str = ""


class MustDepartBy(Frozen):
    """The leg leaving ``from_ref`` must depart no later than ``depart_min``."""

    kind: Literal["must_depart_by"] = "must_depart_by"
    from_ref: str
    depart_min: int
    reason: str = ""


class MaxTotalCost(Frozen):
    kind: Literal["max_total_cost"] = "max_total_cost"
    amount_usd: float
    reason: str = ""


class RequireMealWindow(Frozen):
    """A food item must be present inside [start_min, end_min]."""

    kind: Literal["require_meal_window"] = "require_meal_window"
    start_min: int
    end_min: int
    reason: str = ""


RepairConstraint = Annotated[
    Union[BanCandidate, MustDepartBy, MaxTotalCost, RequireMealWindow],
    Field(discriminator="kind"),
]


class PlanRequest(Frozen):
    persona: str = "custom"
    service_date: str  # YYYY-MM-DD
    origin: LatLng
    origin_label: str = "Home"
    day_start_min: int = constants.DAY_START_MIN_DEFAULT
    day_end_min: int = constants.DAY_END_MIN_DEFAULT
    anchors: tuple[Anchor, ...] = ()
    budget_usd: Optional[float] = None
    interests: tuple[str, ...] = ()


class PlanState(Frozen):
    """The one shared state object the whole pipeline runs over."""

    request: PlanRequest
    weather: Optional[str] = None  # context string only; never affects feasibility
    candidates: tuple[Candidate, ...] = ()
    selector: Optional[SelectorResult] = None
    itinerary: Optional[Itinerary] = None
    findings: tuple[Finding, ...] = ()          # accumulated, never overwritten
    constraints: tuple[RepairConstraint, ...] = ()
    iteration: int = 0
    degraded_to_anchors: bool = False
    schedule_feed_date: Optional[str] = None    # build date of the GTFS index
    events_dataset_date: Optional[str] = None   # build date of the events artifact
    notices: tuple[str, ...] = ()               # degraded-source flags, disclosures


def minutes_to_hhmm(m: int) -> str:
    """Render minutes-since-midnight, tolerating post-midnight (>=1440)."""
    h, mm = divmod(m, 60)
    if h >= 24:
        return f"{h - 24:02d}:{mm:02d} (+1d)"
    return f"{h:02d}:{mm:02d}"
