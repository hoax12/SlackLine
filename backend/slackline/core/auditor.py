"""Auditor: the six named checks with structured findings. Deterministic.

The audit is trustworthy precisely because no model is involved: every check
is arithmetic over the typed state. Findings never mutate state; the repair
loop decides what to do with them.

The six checks (constants.ALL_CHECKS):
1. feasibility          — negative slack on any transition
2. last_departure_risk  — a leg needed after (fail) or within 20 minutes of
                          (warn) the day's last scheduled departure
3. venue_hours          — visit outside known open hours fails; *unknown*
                          hours warn "hours unverified" and never fail
                          (absent is absent)
4. food_gap             — more than 6h (fail) / 4.5h (warn) without an
                          eating opportunity inside the 11:00–21:00 window
5. budget               — known venue prices plus the flat transit allowance
                          exceed the requested budget; absent prices are
                          excluded and disclosed, never guessed
6. out_of_area          — an item outside the nine-county Bay Area bounding
                          box: a soft warning plus best effort, never a fail
"""

from __future__ import annotations

from typing import Mapping, Sequence

from slackline.core import constants
from slackline.core.state import (
    Candidate,
    Finding,
    Itinerary,
    LatLng,
    PlanRequest,
    minutes_to_hhmm,
)


def audit(
    request: PlanRequest,
    itinerary: Itinerary,
    candidates_by_id: Mapping[str, Candidate],
    iteration: int,
) -> tuple[Finding, ...]:
    findings: list[Finding] = []
    findings += _check_feasibility(itinerary, iteration)
    findings += _check_last_departure(itinerary, iteration)
    findings += _check_venue_hours(itinerary, candidates_by_id, iteration)
    findings += _check_food_gap(request, itinerary, candidates_by_id, iteration)
    findings += _check_budget(request, itinerary, candidates_by_id, iteration)
    findings += _check_out_of_area(request, itinerary, candidates_by_id, iteration)
    return tuple(findings)


def has_failures(findings: Sequence[Finding]) -> bool:
    return any(f.severity == "fail" for f in findings)


def _check_feasibility(itinerary: Itinerary, iteration: int) -> list[Finding]:
    out = []
    for t in itinerary.transitions:
        if t.slack_min < 0:
            out.append(
                Finding(
                    check=constants.CHECK_FEASIBILITY,
                    severity="fail",
                    message=(
                        f"negative slack ({t.slack_min} min) between "
                        f"{t.from_ref} and {t.to_ref}; binding: "
                        f"{t.binding_constraint}"
                    ),
                    subject_ids=(t.from_ref, t.to_ref),
                    iteration=iteration,
                )
            )
    return out


def _check_last_departure(itinerary: Itinerary, iteration: int) -> list[Finding]:
    out = []
    for t in itinerary.transitions:
        leg = t.leg
        if leg.mode != "transit" or leg.last_depart_of_day_min is None:
            continue
        if leg.provenance == "estimated":
            # The Navigator found no ride at the needed hour but service
            # exists earlier: the planned departure is after the day's last.
            needed = leg.depart_min
            if needed + constants.TRANSFER_BUFFER_MIN > leg.last_depart_of_day_min:
                out.append(
                    Finding(
                        check=constants.CHECK_LAST_DEPARTURE,
                        severity="fail",
                        message=(
                            f"leg {leg.from_ref} -> {leg.to_ref} needs to leave at "
                            f"{minutes_to_hhmm(needed)} but the last "
                            f"{leg.agency or 'transit'} departure is "
                            f"{minutes_to_hhmm(leg.last_depart_of_day_min)}"
                        ),
                        subject_ids=(leg.from_ref, leg.to_ref),
                        iteration=iteration,
                    )
                )
            continue
        margin = leg.last_depart_of_day_min - (leg.transit_depart_min or 0)
        if margin < 0:
            out.append(
                Finding(
                    check=constants.CHECK_LAST_DEPARTURE,
                    severity="fail",
                    message=(
                        f"leg {leg.from_ref} -> {leg.to_ref} departs after the "
                        f"day's last service"
                    ),
                    subject_ids=(leg.from_ref, leg.to_ref),
                    iteration=iteration,
                )
            )
        elif margin <= constants.LAST_DEPARTURE_WARN_MIN:
            out.append(
                Finding(
                    check=constants.CHECK_LAST_DEPARTURE,
                    severity="warn",
                    message=(
                        f"leg {leg.from_ref} -> {leg.to_ref} rides the "
                        f"{minutes_to_hhmm(leg.transit_depart_min or 0)} "
                        f"{leg.agency}; only {margin} min before the last "
                        f"departure ({minutes_to_hhmm(leg.last_depart_of_day_min)})"
                    ),
                    subject_ids=(leg.from_ref, leg.to_ref),
                    iteration=iteration,
                )
            )
    return out


def _check_venue_hours(
    itinerary: Itinerary,
    candidates_by_id: Mapping[str, Candidate],
    iteration: int,
) -> list[Finding]:
    out = []
    for item in itinerary.items:
        if item.kind != "candidate":
            continue
        cand = candidates_by_id.get(item.ref_id)
        if cand is None:
            continue
        if cand.open_windows is None:
            out.append(
                Finding(
                    check=constants.CHECK_VENUE_HOURS,
                    severity="warn",
                    message=f"{cand.name}: hours unverified",
                    subject_ids=(cand.id,),
                    iteration=iteration,
                )
            )
            continue
        inside = any(
            item.start_min >= w.start_min and item.end_min <= w.end_min
            for w in cand.open_windows
        )
        if not inside:
            out.append(
                Finding(
                    check=constants.CHECK_VENUE_HOURS,
                    severity="fail",
                    message=(
                        f"{cand.name}: visit {minutes_to_hhmm(item.start_min)}-"
                        f"{minutes_to_hhmm(item.end_min)} falls outside "
                        f"known open hours"
                    ),
                    subject_ids=(cand.id,),
                    iteration=iteration,
                )
            )
    return out


def _check_food_gap(
    request: PlanRequest,
    itinerary: Itinerary,
    candidates_by_id: Mapping[str, Candidate],
    iteration: int,
) -> list[Finding]:
    day_start = min(i.start_min for i in itinerary.items)
    day_end = max(i.end_min for i in itinerary.items)
    win_start = max(constants.FOOD_WINDOW_START_MIN, day_start)
    win_end = min(constants.FOOD_WINDOW_END_MIN, day_end)
    if win_end <= win_start:
        return []

    # Eating opportunities: food candidates and anchors that look like meals.
    meals: list[tuple[int, int]] = []
    for item in itinerary.items:
        if item.kind == "candidate":
            cand = candidates_by_id.get(item.ref_id)
            if cand is not None and cand.is_food:
                meals.append((item.start_min, item.end_min))
        elif item.kind == "anchor":
            anchor = next((a for a in request.anchors if a.id == item.ref_id), None)
            if anchor is not None and _looks_like_meal(anchor.title):
                meals.append((item.start_min, item.end_min))
    meals.sort()

    # Walk the window; measure stretches not covered by a meal.
    gaps: list[tuple[int, int]] = []
    cursor = win_start
    for start, end in meals:
        if start > cursor:
            gaps.append((cursor, min(start, win_end)))
        cursor = max(cursor, end)
        if cursor >= win_end:
            break
    if cursor < win_end:
        gaps.append((cursor, win_end))

    out = []
    for start, end in gaps:
        length = end - start
        if length > constants.FOOD_GAP_FAIL_MIN:
            severity = "fail"
        elif length > constants.FOOD_GAP_WARN_MIN:
            severity = "warn"
        else:
            continue
        out.append(
            Finding(
                check=constants.CHECK_FOOD_GAP,
                severity=severity,
                message=(
                    f"{length // 60}h{length % 60:02d}m without an eating "
                    f"opportunity ({minutes_to_hhmm(start)}-{minutes_to_hhmm(end)})"
                ),
                iteration=iteration,
            )
        )
    return out


def _looks_like_meal(title: str) -> bool:
    lowered = title.lower()
    return any(
        word in lowered
        for word in ("dinner", "lunch", "brunch", "breakfast", "meal", "food")
    )


def _check_budget(
    request: PlanRequest,
    itinerary: Itinerary,
    candidates_by_id: Mapping[str, Candidate],
    iteration: int,
) -> list[Finding]:
    if request.budget_usd is None:
        return []
    known = 0.0
    unpriced: list[str] = []
    for item in itinerary.items:
        if item.kind != "candidate":
            continue
        cand = candidates_by_id.get(item.ref_id)
        if cand is None:
            continue
        if cand.price_usd is None:
            unpriced.append(cand.name)
        else:
            known += cand.price_usd
    total = known + constants.TRANSIT_ALLOWANCE_USD
    if total <= request.budget_usd:
        return []
    disclosure = (
        f" (excludes {len(unpriced)} unpriced venue(s): {', '.join(unpriced)})"
        if unpriced
        else ""
    )
    return [
        Finding(
            check=constants.CHECK_BUDGET,
            severity="fail",
            message=(
                f"estimated cost ${total:.0f} (venues ${known:.0f} + transit "
                f"allowance ${constants.TRANSIT_ALLOWANCE_USD:.0f}) exceeds "
                f"budget ${request.budget_usd:.0f}{disclosure}"
            ),
            iteration=iteration,
        )
    ]


def _check_out_of_area(
    request: PlanRequest,
    itinerary: Itinerary,
    candidates_by_id: Mapping[str, Candidate],
    iteration: int,
) -> list[Finding]:
    lat_min, lon_min, lat_max, lon_max = constants.BAY_AREA_BBOX

    def outside(loc: LatLng) -> bool:
        return not (lat_min <= loc.lat <= lat_max and lon_min <= loc.lon <= lon_max)

    out = []
    for item in itinerary.items:
        loc = None
        name = item.ref_id
        if item.kind == "candidate":
            cand = candidates_by_id.get(item.ref_id)
            if cand is not None:
                loc, name = cand.location, cand.name
        elif item.kind == "anchor":
            anchor = next((a for a in request.anchors if a.id == item.ref_id), None)
            if anchor is not None:
                loc, name = anchor.location, anchor.title
        if loc is not None and outside(loc):
            out.append(
                Finding(
                    check=constants.CHECK_OUT_OF_AREA,
                    severity="warn",
                    message=(
                        f"{name} is outside the nine-county Bay Area; plan is "
                        f"best effort there"
                    ),
                    subject_ids=(item.ref_id,),
                    iteration=iteration,
                )
            )
    return out
