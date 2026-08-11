"""Scheduler: greedy insertion plus local improvement. Deterministic.

Anchors are inviolable: they are always present at their fixed times and are
never moved, shortened, or dropped. Candidates are inserted greedily in
Selector-rank order into the gaps between anchors, then a bounded local
improvement pass tries adjacent swaps that strictly reduce total travel time.

Tie-breaking (section 5): Selector rank ascending, then earliest feasible
start (gaps are tried chronologically), then candidate ID as the final
stable key. Identical input yields identical output, tested.

Typed repair constraints are consumed natively here:
* ban_candidate       — candidate is never placed
* must_depart_by      — departure leaving a ref is capped; candidate visits
                        that cannot fit their full dwell before the cap are
                        not placed there
* max_total_cost      — greedy skips candidates whose known price would push
                        the running total (plus transit allowance) over
* require_meal_window — if no food stop overlaps the window after greedy,
                        food candidates get a forced second pass
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Optional, Sequence

from slackline.core import constants, navigator
from slackline.core.state import (
    Anchor,
    Candidate,
    Itinerary,
    ItineraryItem,
    LatLng,
    Leg,
    PlanRequest,
    RepairConstraint,
    SelectorResult,
    Transition,
    minutes_to_hhmm,
)

LegResolver = Callable[..., Leg]
# Signature: (index, from_ref, to_ref, from_loc, to_loc, earliest_depart_min,
#             service_date) -> Leg


@dataclass(frozen=True)
class _Point:
    """A placed timeline point during layout."""

    kind: str  # "origin" | "anchor" | "candidate" | "home"
    ref: str
    loc: LatLng
    start: int
    end: int


def effective_day_bounds(request: PlanRequest) -> tuple[int, int]:
    """Day boundaries anchored around the hard anchors: the window always
    stretches to contain every anchor with 30 minutes of margin."""
    start, end = request.day_start_min, request.day_end_min
    for anchor in request.anchors:
        start = min(start, anchor.start_min - 30)
        end = max(end, anchor.end_min + 30)
    return start, end


def build_itinerary(
    request: PlanRequest,
    candidates: Sequence[Candidate],
    selector: Optional[SelectorResult],
    constraints: Sequence[RepairConstraint],
    index: Optional[navigator.ScheduleIndexLike],
    leg_resolver: Optional[LegResolver] = None,
) -> Itinerary:
    resolve = leg_resolver or navigator.resolve_leg
    day_start, day_end = effective_day_bounds(request)

    banned = {c.candidate_id for c in constraints if c.kind == "ban_candidate"}
    depart_caps: dict[str, int] = {}
    for c in constraints:
        if c.kind == "must_depart_by":
            cap = depart_caps.get(c.from_ref)
            # `is not None`, not truthiness: a cap of 0 (midnight) is a real
            # cap and must not be replaced by a looser one.
            depart_caps[c.from_ref] = (
                min(cap, c.depart_min) if cap is not None else c.depart_min
            )
    cost_cap: Optional[float] = None
    for c in constraints:
        if c.kind == "max_total_cost":
            cost_cap = c.amount_usd if cost_cap is None else min(cost_cap, c.amount_usd)
    meal_windows = [c for c in constraints if c.kind == "require_meal_window"]

    rank_of: dict[str, int] = {}
    if selector is not None:
        rank_of = {r.candidate_id: r.rank for r in selector.rankings}
    ordered = sorted(
        (c for c in candidates if c.id not in banned),
        key=lambda c: (rank_of.get(c.id, 10_000), c.id),
    )

    by_id = {c.id: c for c in candidates}

    def layout(order: Sequence[str]) -> tuple[list[_Point], int, frozenset[str]]:
        """Chain times through the given order. Always returns a layout, plus
        a violation measure so trials can be compared against the baseline:

        * ``lateness``: total minutes of anchor lateness, home-past-day-end,
          and departure-cap overruns already implied by this order
        * ``bad``: candidate refs whose venue window or full dwell cannot fit

        An insertion trial is acceptable when it does not worsen either. The
        base skeleton may itself be violated (a too-late anchor); candidates
        are still placeable into the slack that remains, and the Auditor —
        not the Scheduler — reports the violation.
        """
        points: list[_Point] = [
            _Point("origin", "origin", request.origin, day_start, day_start)
        ]
        lateness = 0
        bad: set[str] = set()
        for ref in order:
            prev = points[-1]
            if ref.startswith("anchor:"):
                anchor = next(a for a in request.anchors if a.id == ref[7:])
                leg = resolve(
                    index, prev.ref, anchor.id, prev.loc, anchor.location,
                    _depart_from(prev, depart_caps), request.service_date,
                )
                lateness += max(
                    0,
                    leg.arrive_min + constants.WALK_ARRIVAL_BUFFER_MIN
                    - anchor.start_min,
                )
                points.append(
                    _Point("anchor", anchor.id, anchor.location,
                           anchor.start_min, anchor.end_min)
                )
            else:
                cand = by_id[ref]
                leg = resolve(
                    index, prev.ref, cand.id, prev.loc, cand.location,
                    _depart_from(prev, depart_caps), request.service_date,
                )
                start = leg.arrive_min + constants.WALK_ARRIVAL_BUFFER_MIN
                start, ok = _fit_open_window(cand, start)
                if not ok:
                    bad.add(cand.id)
                end = start + cand.duration_min
                cap = depart_caps.get(cand.id)
                if cap is not None:
                    if cap >= start + cand.duration_min:
                        pass  # full dwell fits under the cap
                    elif cap > start:
                        end = cap
                        bad.add(cand.id)  # dwell would be cut short
                    else:
                        bad.add(cand.id)
                if end > day_end:
                    bad.add(cand.id)
                points.append(_Point("candidate", cand.id, cand.location, start, end))
        prev = points[-1]
        leg = resolve(
            index, prev.ref, "home", prev.loc, request.origin,
            _depart_from(prev, depart_caps), request.service_date,
        )
        lateness += max(0, leg.arrive_min - day_end)
        points.append(
            _Point("home", "home", request.origin, leg.arrive_min, leg.arrive_min)
        )
        return points, lateness, frozenset(bad)

    # --- skeleton: anchors only, in start order --------------------------------
    anchor_refs = [
        f"anchor:{a.id}"
        for a in sorted(request.anchors, key=lambda a: (a.start_min, a.id))
    ]
    order: list[str] = list(anchor_refs)

    # --- greedy insertion -------------------------------------------------------
    running_cost = 0.0
    placed_ids: list[str] = []
    for cand in ordered:
        if cost_cap is not None and cand.price_usd is not None:
            if running_cost + cand.price_usd + constants.TRANSIT_ALLOWANCE_USD > cost_cap:
                continue
        placed = _try_insert(order, cand.id, layout)
        if placed is not None:
            order = placed
            placed_ids.append(cand.id)
            if cand.price_usd is not None:
                running_cost += cand.price_usd

    # --- require_meal_window forced pass ---------------------------------------
    # Position search is window-aware: the meal must actually land inside
    # the required interval, not merely somewhere feasible. A meal beats
    # sightseeing: if nothing fits, evict the lowest-ranked placed non-food
    # candidate (deterministically) and retry, until it fits or nothing is
    # left to evict.
    def _placed_cost(refs: list[str]) -> float:
        return sum(
            by_id[r].price_usd or 0.0
            for r in refs
            if not r.startswith("anchor:")
        )

    for window in meal_windows:
        while not _has_food_in_window(
            order, by_id, layout, window.start_min, window.end_min
        ):
            inserted = False
            for cand in ordered:
                if not cand.is_food or cand.id in placed_ids:
                    continue
                if cost_cap is not None and cand.price_usd is not None:
                    projected = (
                        _placed_cost(order) + cand.price_usd
                        + constants.TRANSIT_ALLOWANCE_USD
                    )
                    if projected > cost_cap:
                        continue
                trial = _try_insert_in_window(
                    order, cand.id, layout, window.start_min, window.end_min
                )
                if trial is not None:
                    order = trial
                    placed_ids.append(cand.id)
                    inserted = True
                    break
            if inserted:
                break
            evictable = [
                ref for ref in order
                if not ref.startswith("anchor:") and not by_id[ref].is_food
            ]
            if not evictable:
                break
            victim = max(
                evictable, key=lambda ref: (rank_of.get(ref, 10_000), ref)
            )
            order = [ref for ref in order if ref != victim]
            placed_ids = [pid for pid in placed_ids if pid != victim]

    # --- local improvement: adjacent candidate swaps ------------------------------
    order = _improve(order, layout)

    # --- final layout and transition/slack computation -----------------------------
    points, _, _ = layout(order)
    return _to_itinerary(points, request, index, resolve, depart_caps, day_end, by_id)


def _depart_from(point: _Point, depart_caps: dict[str, int]) -> int:
    cap = depart_caps.get(point.ref)
    if cap is not None and point.kind == "candidate":
        return min(point.end, cap)
    return point.end


def _fit_open_window(cand: Candidate, start: int) -> tuple[int, bool]:
    """Earliest start >= requested that fits the dwell inside an open window.
    Unknown hours (None) allow any time — absent is absent."""
    if cand.open_windows is None:
        return start, True
    for window in sorted(cand.open_windows, key=lambda w: w.start_min):
        candidate_start = max(start, window.start_min)
        if candidate_start + cand.duration_min <= window.end_min:
            return candidate_start, True
    return start, False


def _try_insert(order: list[str], cand_id: str, layout) -> Optional[list[str]]:
    """Try every gap chronologically; the first acceptable position wins
    (this is the 'earliest feasible start' tie-break). A position is
    acceptable when the new candidate itself fits cleanly and no existing
    violation gets worse."""
    _, base_lateness, base_bad = layout(order)
    for pos in range(len(order) + 1):
        trial = order[:pos] + [cand_id] + order[pos:]
        _, lateness, bad = layout(trial)
        if lateness <= base_lateness and cand_id not in bad and bad <= base_bad:
            return trial
    return None


def _try_insert_in_window(
    order: list[str], cand_id: str, layout, win_start: int, win_end: int
) -> Optional[list[str]]:
    """Like _try_insert, but the inserted item must overlap
    [win_start, win_end), and among acceptable positions the one closest to
    the window midpoint wins (earlier position on ties). A meal at the edge
    of a long gap does not split it; one in the middle does."""
    _, base_lateness, base_bad = layout(order)
    window_mid = (win_start + win_end) // 2
    best: Optional[tuple[int, int, list[str]]] = None
    for pos in range(len(order) + 1):
        trial = order[:pos] + [cand_id] + order[pos:]
        points, lateness, bad = layout(trial)
        if lateness > base_lateness or cand_id in bad or not bad <= base_bad:
            continue
        placed = next(p for p in points if p.ref == cand_id)
        if not (placed.start < win_end and placed.end > win_start):
            continue
        distance = abs((placed.start + placed.end) // 2 - window_mid)
        if best is None or (distance, pos) < (best[0], best[1]):
            best = (distance, pos, trial)
    return best[2] if best else None


def _has_food_in_window(
    order: list[str], by_id: dict, layout, win_start: int, win_end: int
) -> bool:
    points, _, _ = layout(order)
    for p in points:
        if p.kind == "candidate" and by_id[p.ref].is_food:
            if p.start < win_end and p.end > win_start:
                return True
    return False


def _travel_minutes(points: list[_Point]) -> int:
    """Idle + travel span: minutes between leaving one point and starting the
    next, summed over the day (home arrival included)."""
    return sum(max(0, nxt.start - prev.end) for prev, nxt in zip(points, points[1:]))


def _improve(order: list[str], layout) -> list[str]:
    """Bounded local improvement: swap adjacent candidate pairs when the swap
    strictly reduces idle+travel span without worsening violations.
    Deterministic order, at most 3 passes."""
    def evaluate(o: list[str]) -> tuple[int, int, frozenset[str]]:
        points, lateness, bad = layout(o)
        return _travel_minutes(points), lateness, bad

    current = list(order)
    current_span, current_late, current_bad = evaluate(current)
    for _ in range(3):
        improved = False
        for i in range(len(current) - 1):
            a, b = current[i], current[i + 1]
            if a.startswith("anchor:") or b.startswith("anchor:"):
                continue
            trial = current[:i] + [b, a] + current[i + 2:]
            span, late, bad = evaluate(trial)
            if span < current_span and late <= current_late and bad <= current_bad:
                current = trial
                current_span, current_late, current_bad = span, late, bad
                improved = True
        if not improved:
            break
    return current


def _to_itinerary(
    points: list[_Point],
    request: PlanRequest,
    index,
    resolve,
    depart_caps: dict[str, int],
    day_end: int,
    by_id: dict,
) -> Itinerary:
    items = tuple(
        ItineraryItem(
            kind="origin" if p.kind in ("origin", "home") else p.kind,  # type: ignore[arg-type]
            ref_id=p.ref,
            start_min=p.start,
            end_min=p.end,
        )
        for p in points
    )
    transitions = []
    for prev, nxt in zip(points, points[1:]):
        leg = resolve(
            index, prev.ref, nxt.ref, prev.loc, nxt.loc,
            _depart_from(prev, depart_caps), request.service_date,
        )
        slack, binding = _slack_and_binding(leg, nxt, day_end, by_id)
        transitions.append(
            Transition(
                from_ref=prev.ref,
                to_ref=nxt.ref,
                leg=leg,
                slack_min=slack,
                binding_constraint=binding,
            )
        )
    return Itinerary(items=items, transitions=tuple(transitions))


def _slack_and_binding(
    leg: Leg, nxt: _Point, day_end: int, by_id: dict
) -> tuple[int, str]:
    """Per-transition slack: minutes of headroom against the tightest cap.

    Caps considered: the next fixed commitment's start, venue closing (must
    still fit the dwell), day end, and — for verified transit — the last
    departure of the day for the same stop pair. The binding constraint
    names whichever cap is tightest.
    """
    caps: list[tuple[int, str]] = []
    if nxt.kind == "anchor":
        caps.append(
            (
                nxt.start - constants.WALK_ARRIVAL_BUFFER_MIN - leg.arrive_min,
                f"anchor start {minutes_to_hhmm(nxt.start)}",
            )
        )
    elif nxt.kind == "candidate":
        cand = by_id[nxt.ref]
        if cand.open_windows:
            for window in cand.open_windows:
                if nxt.start >= window.start_min and nxt.end <= window.end_min:
                    caps.append(
                        (
                            window.end_min - cand.duration_min - leg.arrive_min,
                            f"venue closes {minutes_to_hhmm(window.end_min)}",
                        )
                    )
                    break
        caps.append(
            (
                day_end - cand.duration_min - leg.arrive_min,
                f"day ends {minutes_to_hhmm(day_end)}",
            )
        )
    else:  # home
        caps.append((day_end - leg.arrive_min, f"day ends {minutes_to_hhmm(day_end)}"))
    if (
        leg.provenance == "verified"
        and leg.last_depart_of_day_min is not None
        and leg.transit_depart_min is not None
    ):
        caps.append(
            (
                leg.last_depart_of_day_min - leg.transit_depart_min,
                f"last {leg.agency} departure {minutes_to_hhmm(leg.last_depart_of_day_min)}",
            )
        )
    slack, binding = min(caps, key=lambda pair: pair[0])
    return slack, binding
