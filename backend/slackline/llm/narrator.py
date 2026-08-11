"""Narrator stage: describe the verified plan. Streamed prose.

The Narrator may only describe state that already exists — it never invents
times, venues, or claims of verification. Keyless it emits a deterministic
template summary, streamed in chunks so the UI path is identical.
"""

from __future__ import annotations

from typing import Iterator, Optional

from slackline.core.state import PlanState, minutes_to_hhmm


def narrate(state: PlanState, provider=None) -> Iterator[str]:
    if provider is not None:
        try:
            yield from _model_narrate(state, provider)
            return
        except Exception:
            pass  # fall through to the template
    yield from template_narrate(state)


def template_narrate(state: PlanState) -> Iterator[str]:
    """Deterministic keyless summary built purely from state."""
    itinerary = state.itinerary
    if itinerary is None:
        yield "No itinerary was produced."
        return
    date = state.request.service_date
    yield f"Here is your verified plan for {date}.\n\n"
    by_id = {c.id: c for c in state.candidates}
    anchors = {a.id: a for a in state.request.anchors}

    def name_of(ref: str) -> str:
        """Human name for a plan ref. Internal ids never reach the reader —
        nor the model, since this text is also the Narrator's prompt."""
        if ref in ("origin", "home"):
            return state.request.origin_label
        if ref in anchors:
            return anchors[ref].title
        cand = by_id.get(ref)
        return cand.name if cand else ref

    for item in itinerary.items:
        if item.kind == "origin":
            label = (
                f"Start from {state.request.origin_label}"
                if item.ref_id == "origin"
                else f"Back at {state.request.origin_label}"
            )
            yield f"- {minutes_to_hhmm(item.start_min)}  {label}\n"
        elif item.kind == "anchor":
            anchor = anchors.get(item.ref_id)
            title = anchor.title if anchor else item.ref_id
            yield (
                f"- {minutes_to_hhmm(item.start_min)}-"
                f"{minutes_to_hhmm(item.end_min)}  {title} (hard anchor)\n"
            )
        else:
            cand = by_id.get(item.ref_id)
            name = cand.name if cand else item.ref_id
            yield (
                f"- {minutes_to_hhmm(item.start_min)}-"
                f"{minutes_to_hhmm(item.end_min)}  {name}\n"
            )
    verified = sum(
        1 for t in itinerary.transitions if t.leg.provenance == "verified"
    )
    total = len(itinerary.transitions)
    tightest = min(itinerary.transitions, key=lambda t: t.slack_min, default=None)
    yield (
        f"\n{verified} of {total} legs are verified against published "
        f"schedules; the rest are estimates and are flagged as such.\n"
    )
    if tightest is not None:
        yield (
            f"Tightest transition: {name_of(tightest.from_ref)} to "
            f"{name_of(tightest.to_ref)} "
            f"with {tightest.slack_min} min of slack "
            f"({tightest.binding_constraint}).\n"
        )
    if state.degraded_to_anchors:
        yield (
            "Repair could not clear every failure, so the plan was reduced "
            "to your hard anchors — everything shown is provably reachable.\n"
        )
    yield "(Template summary: no model key configured.)"


def _model_narrate(state: PlanState, provider) -> Iterator[str]:
    facts = template_narrate(state)
    prompt = (
        "Rewrite this verified day-plan summary as warm, concise prose, "
        "2 short paragraphs maximum. You may rephrase but may NOT add, "
        "remove, or alter any time, venue, or verification claim:\n\n"
        + "".join(facts)
    )
    yield from provider.stream_text(prompt)
