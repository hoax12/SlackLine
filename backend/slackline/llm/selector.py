"""Selector stage: rank candidates with one-line reasons. Structured output
only — the Selector never touches times, prices, or feasibility.

With no model provider the stage degrades to a deterministic heuristic
ranking, flagged ``heuristic=True`` so the UI can badge it. The heuristic:
interest match first, then (under an active budget constraint) cheaper
before pricier, then distance from the start point, then candidate ID.
"""

from __future__ import annotations

import json
from typing import Optional, Sequence

from slackline.core import navigator
from slackline.core.state import (
    Candidate,
    PlanRequest,
    RankedCandidate,
    RepairConstraint,
    SelectorResult,
)


def rank(
    request: PlanRequest,
    candidates: Sequence[Candidate],
    constraints: Sequence[RepairConstraint] = (),
    weather: Optional[str] = None,
    provider=None,
) -> SelectorResult:
    """Rank via the model provider when available, else heuristically."""
    if provider is not None:
        try:
            return _model_rank(request, candidates, constraints, weather, provider)
        except Exception:
            pass  # fall through to the deterministic path
    return heuristic_rank(request, candidates, constraints)


def heuristic_rank(
    request: PlanRequest,
    candidates: Sequence[Candidate],
    constraints: Sequence[RepairConstraint] = (),
) -> SelectorResult:
    banned = {c.candidate_id for c in constraints if c.kind == "ban_candidate"}
    budget_active = any(c.kind == "max_total_cost" for c in constraints)

    def sort_key(cand: Candidate):
        interest_miss = 0 if cand.category in request.interests else 1
        price_bucket = 0
        if budget_active and cand.price_usd is not None:
            price_bucket = int(cand.price_usd // 20)
        distance = round(navigator.haversine_km(request.origin, cand.location), 1)
        return (interest_miss, price_bucket, distance, cand.id)

    ordered = sorted((c for c in candidates if c.id not in banned), key=sort_key)
    rankings = []
    for pos, cand in enumerate(ordered, start=1):
        km = navigator.haversine_km(request.origin, cand.location)
        bits = []
        if cand.category in request.interests:
            bits.append(f"matches interest '{cand.category}'")
        if budget_active and cand.price_usd is not None:
            bits.append(f"${cand.price_usd:.0f}")
        bits.append(f"{km:.1f} km from start")
        rankings.append(
            RankedCandidate(
                candidate_id=cand.id, rank=pos, reason=", ".join(bits)
            )
        )
    return SelectorResult(rankings=tuple(rankings), heuristic=True)


def _model_rank(
    request: PlanRequest,
    candidates: Sequence[Candidate],
    constraints: Sequence[RepairConstraint],
    weather: Optional[str],
    provider,
) -> SelectorResult:
    """Structured-output ranking through the provider interface. Constraints
    are rendered into the prompt as data, never parsed back from English."""
    catalog = [
        {
            "id": c.id,
            "name": c.name,
            "category": c.category,
            "price_usd": c.price_usd,
            "description": c.description,
        }
        for c in candidates
    ]
    constraint_payload = [c.model_dump() for c in constraints]
    prompt = (
        "You rank day-trip venues for a visitor to the SF Bay Area. "
        "Respond ONLY with a JSON array of objects {\"id\", \"rank\", \"reason\"} "
        "covering every venue, rank 1 = best, reasons one short line. "
        "You may not invent venues, prices, or opening hours.\n"
        f"Visitor interests: {list(request.interests)}\n"
        f"Weather context: {weather or 'unknown'}\n"
        f"Active constraints (typed, already enforced elsewhere): "
        f"{json.dumps(constraint_payload)}\n"
        f"Venues: {json.dumps(catalog)}"
    )
    result = provider.complete_json(prompt)
    parsed = result.payload
    by_id = {c.id: c for c in candidates}
    rankings = []
    seen = set()
    for entry in sorted(parsed, key=lambda e: e.get("rank", 1_000_000)):
        cid = entry.get("id")
        if cid in by_id and cid not in seen:
            seen.add(cid)
            rankings.append(
                RankedCandidate(
                    candidate_id=cid,
                    rank=len(rankings) + 1,
                    reason=str(entry.get("reason", ""))[:200],
                )
            )
    # Anything the model skipped goes to the back, deterministically.
    for cand in sorted(candidates, key=lambda c: c.id):
        if cand.id not in seen:
            rankings.append(
                RankedCandidate(
                    candidate_id=cand.id,
                    rank=len(rankings) + 1,
                    reason="not ranked by model",
                )
            )
    return SelectorResult(
        rankings=tuple(rankings),
        heuristic=False,
        model=result.model,
        tokens_in=result.tokens_in,
        tokens_out=result.tokens_out,
    )
