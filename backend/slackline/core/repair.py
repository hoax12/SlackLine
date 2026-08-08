"""Repair loop semantics: findings -> typed constraints, bounded iterations,
deterministic degrade-to-anchors floor. Pure.

Design positions (section 7 of the plan):
* Most findings are purely temporal (negative slack, last-departure risk).
  Re-ranking burns tokens to solve nothing, so those produce Scheduler-only
  constraints. The Selector is re-invoked only when a finding invalidates a
  *choice*: a closed venue or a budget overrun.
* The loop is capped at REPAIR_MAX_ITERATIONS (2). If failures persist, the
  deterministic floor drops every candidate and keeps only the anchors —
  an honest "here is what provably works" rather than a fourth guess.
* No English parsing anywhere: constraints are typed objects consumed
  natively by the Scheduler and rendered into the Selector prompt.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Sequence

from slackline.core import constants
from slackline.core.state import (
    BanCandidate,
    Finding,
    Itinerary,
    MaxTotalCost,
    MustDepartBy,
    PlanRequest,
    RepairConstraint,
    RequireMealWindow,
)


@dataclass(frozen=True)
class RepairPlan:
    """What one repair iteration should do."""

    constraints: tuple[RepairConstraint, ...]
    needs_selector: bool          # only choice-invalidating findings set this
    unresolvable: tuple[str, ...] = field(default_factory=tuple)


def plan_repairs(
    request: PlanRequest,
    itinerary: Itinerary,
    findings: Sequence[Finding],
    existing: Sequence[RepairConstraint],
) -> RepairPlan:
    """Map failing findings to new typed constraints, deterministically.

    Only ``fail`` findings drive repair; warnings are surfaced, not fixed.
    Constraints already present are not duplicated.
    """
    anchor_ids = {a.id for a in request.anchors}
    legs_by_from = {t.leg.from_ref: t.leg for t in itinerary.transitions}
    new: list[RepairConstraint] = []
    needs_selector = False
    unresolvable: list[str] = []

    for finding in sorted(
        (f for f in findings if f.severity == "fail"),
        key=lambda f: (f.check, f.subject_ids, f.message),
    ):
        if finding.check == constants.CHECK_FEASIBILITY:
            from_ref = finding.subject_ids[0] if finding.subject_ids else ""
            deficit = -_slack_of(itinerary, finding.subject_ids)
            if from_ref in anchor_ids or from_ref in ("origin", "home"):
                unresolvable.append(
                    f"{finding.check}: cannot leave {from_ref} earlier "
                    f"(hard anchor)"
                )
                continue
            leg = legs_by_from.get(from_ref)
            if leg is None:
                unresolvable.append(f"{finding.check}: no leg from {from_ref}")
                continue
            new.append(
                MustDepartBy(
                    from_ref=from_ref,
                    depart_min=leg.depart_min - deficit,
                    reason=finding.message,
                )
            )
        elif finding.check == constants.CHECK_LAST_DEPARTURE:
            from_ref = finding.subject_ids[0] if finding.subject_ids else ""
            leg = legs_by_from.get(from_ref)
            if leg is None or leg.last_depart_of_day_min is None:
                unresolvable.append(f"{finding.check}: no last-departure info")
                continue
            # Leave early enough to board the last ride: buffer to reach the
            # stop plus the boarding buffer itself.
            depart_by = (
                leg.last_depart_of_day_min - 2 * constants.TRANSFER_BUFFER_MIN
            )
            if from_ref in anchor_ids or from_ref in ("origin", "home"):
                unresolvable.append(
                    f"{finding.check}: last departure "
                    f"precedes the end of hard anchor {from_ref}"
                )
                continue
            new.append(
                MustDepartBy(
                    from_ref=from_ref, depart_min=depart_by, reason=finding.message
                )
            )
        elif finding.check == constants.CHECK_VENUE_HOURS:
            # A closed venue invalidates the Selector's choice.
            for cand_id in finding.subject_ids:
                new.append(BanCandidate(candidate_id=cand_id, reason=finding.message))
            needs_selector = True
        elif finding.check == constants.CHECK_BUDGET:
            if request.budget_usd is not None:
                new.append(
                    MaxTotalCost(
                        amount_usd=request.budget_usd, reason=finding.message
                    )
                )
                needs_selector = True
        elif finding.check == constants.CHECK_FOOD_GAP:
            # Target the *actual* gap the Auditor measured, not the whole
            # eating window: a meal elsewhere in the window would satisfy
            # the loose constraint without splitting the gap.
            start, end = finding.window or (
                constants.FOOD_WINDOW_START_MIN,
                constants.FOOD_WINDOW_END_MIN,
            )
            new.append(
                RequireMealWindow(start_min=start, end_min=end, reason=finding.message)
            )
        # out_of_area is warn-only by construction; nothing to repair.

    deduped = _dedupe(existing, new)
    return RepairPlan(
        constraints=tuple(deduped),
        needs_selector=needs_selector,
        unresolvable=tuple(unresolvable),
    )


def should_degrade(iteration: int, findings: Sequence[Finding]) -> bool:
    """The deterministic floor: after the bounded iterations, persistent
    failures degrade the plan to anchors only."""
    return iteration >= constants.REPAIR_MAX_ITERATIONS and any(
        f.severity == "fail" and f.iteration == iteration for f in findings
    )


def _slack_of(itinerary: Itinerary, subject_ids: tuple[str, ...]) -> int:
    for t in itinerary.transitions:
        if (t.from_ref, t.to_ref) == tuple(subject_ids[:2]):
            return t.slack_min
    return 0


def _dedupe(
    existing: Sequence[RepairConstraint], new: Sequence[RepairConstraint]
) -> list[RepairConstraint]:
    seen = {c.model_dump_json() for c in existing}
    out: list[RepairConstraint] = []
    for c in new:
        raw = c.model_dump_json()
        if raw not in seen:
            seen.add(raw)
            out.append(c)
    return out
