"""A deliberately broken fixture plan must yield exactly the expected
findings, offline. Every one of the six checks fires."""

import pytest

from slackline.core import auditor, constants
from slackline.core.state import (
    Anchor,
    Candidate,
    Itinerary,
    ItineraryItem,
    LatLng,
    Leg,
    OpenWindow,
    PlanRequest,
    Transition,
)

SF = LatLng(lat=37.7793, lon=-122.4193)
MONTEREY = LatLng(lat=36.6002, lon=-121.8947)  # outside the nine-county bbox


def _broken_fixture():
    """Day 09:00-22:00, no eating opportunity at all, museum visited after
    close, an unpriced venue plus a priced one blowing a $50 budget, a
    transit leg needed after the last departure, negative slack into the
    anchor, and a Monterey detour."""
    candidates = {
        "museum": Candidate(
            id="museum", name="Closed Museum", category="museum",
            location=SF, duration_min=90, price_usd=60.0,
            open_windows=(OpenWindow(start_min=10 * 60, end_min=12 * 60),),
        ),
        "mystery": Candidate(
            id="mystery", name="Mystery Venue", category="attraction",
            location=MONTEREY, duration_min=60,  # no hours, no price
        ),
    }
    request = PlanRequest(
        service_date="2026-08-12",
        origin=SF,
        budget_usd=50.0,
        anchors=(
            Anchor(
                id="talk", title="Evening talk",
                location=SF, start_min=19 * 60, end_min=20 * 60,
            ),
        ),
    )
    items = (
        ItineraryItem(kind="origin", ref_id="origin", start_min=540, end_min=540),
        # Museum visited 14:00-15:30 against known hours 10:00-12:00.
        ItineraryItem(kind="candidate", ref_id="museum", start_min=840, end_min=930),
        ItineraryItem(kind="candidate", ref_id="mystery", start_min=1000, end_min=1060),
        ItineraryItem(kind="anchor", ref_id="talk", start_min=1140, end_min=1200),
        ItineraryItem(kind="origin", ref_id="home", start_min=1320, end_min=1320),
    )
    legs = {
        "to_museum": Leg(
            from_ref="origin", to_ref="museum", mode="walk",
            depart_min=540, arrive_min=560, provenance="estimated",
        ),
        "to_mystery": Leg(
            from_ref="museum", to_ref="mystery", mode="transit",
            depart_min=930, arrive_min=995, provenance="estimated",
        ),
        # Needed at 17:40 but the last CT departure was 17:00.
        "to_talk": Leg(
            from_ref="mystery", to_ref="talk", mode="transit",
            depart_min=1060, arrive_min=1150, provenance="estimated",
            agency="CT", last_depart_of_day_min=17 * 60,
        ),
        "home": Leg(
            from_ref="talk", to_ref="home", mode="walk",
            depart_min=1200, arrive_min=1320, provenance="estimated",
        ),
    }
    transitions = (
        Transition(from_ref="origin", to_ref="museum", leg=legs["to_museum"],
                   slack_min=180, binding_constraint="venue closes 12:00"),
        Transition(from_ref="museum", to_ref="mystery", leg=legs["to_mystery"],
                   slack_min=30, binding_constraint="day ends 22:00"),
        # Arrives 19:10 for a 19:00 anchor: negative slack.
        Transition(from_ref="mystery", to_ref="talk", leg=legs["to_talk"],
                   slack_min=-15, binding_constraint="anchor start 19:00"),
        Transition(from_ref="talk", to_ref="home", leg=legs["home"],
                   slack_min=0, binding_constraint="day ends 22:00"),
    )
    return request, Itinerary(items=items, transitions=transitions), candidates


def test_broken_plan_raises_all_six_checks():
    request, itinerary, candidates = _broken_fixture()
    findings = auditor.audit(request, itinerary, candidates, iteration=0)
    by_check = {}
    for f in findings:
        by_check.setdefault(f.check, []).append(f)

    assert set(by_check) == set(constants.ALL_CHECKS)

    # 1. feasibility: exactly the one negative-slack transition.
    fails = by_check[constants.CHECK_FEASIBILITY]
    assert len(fails) == 1
    assert fails[0].severity == "fail"
    assert fails[0].subject_ids == ("mystery", "talk")

    # 2. last departure: stranded after the 17:00 last CT departure.
    strand = by_check[constants.CHECK_LAST_DEPARTURE]
    assert len(strand) == 1
    assert strand[0].severity == "fail"
    assert "17:00" in strand[0].message

    # 3. venue hours: museum fails (outside known hours), mystery warns
    #    (hours unverified) — absent is absent, never a failure.
    hours = sorted(by_check[constants.CHECK_VENUE_HOURS], key=lambda f: f.severity)
    assert [f.severity for f in hours] == ["fail", "warn"]
    assert hours[0].subject_ids == ("museum",)
    assert "hours unverified" in hours[1].message
    assert hours[1].subject_ids == ("mystery",)

    # 4. food gap: no eating opportunity 11:00-21:00 -> single 10h fail.
    food = by_check[constants.CHECK_FOOD_GAP]
    assert len(food) == 1
    assert food[0].severity == "fail"

    # 5. budget: $60 museum + $15 transit allowance > $50, with the unpriced
    #    venue excluded and disclosed by name.
    budget = by_check[constants.CHECK_BUDGET]
    assert len(budget) == 1
    assert budget[0].severity == "fail"
    assert "Mystery Venue" in budget[0].message

    # 6. out of area: Monterey is soft-warned, never failed.
    area = by_check[constants.CHECK_OUT_OF_AREA]
    assert len(area) == 1
    assert area[0].severity == "warn"


def test_clean_transition_raises_nothing():
    request, itinerary, candidates = _broken_fixture()
    ok = itinerary.model_copy(
        update={
            "transitions": tuple(
                t.model_copy(update={"slack_min": 30}) for t in itinerary.transitions
            )
        }
    )
    findings = auditor.audit(request, ok, candidates, iteration=1)
    assert not [f for f in findings if f.check == constants.CHECK_FEASIBILITY]
    # Iteration number stamps every finding for accumulation accounting.
    assert all(f.iteration == 1 for f in findings)


def test_verified_leg_close_to_last_departure_warns():
    request, itinerary, candidates = _broken_fixture()
    close_leg = Leg(
        from_ref="talk", to_ref="home", mode="transit",
        depart_min=1200, arrive_min=1260, provenance="verified",
        agency="BA", transit_depart_min=1215,
        last_depart_of_day_min=1215 + constants.LAST_DEPARTURE_WARN_MIN,
    )
    warned = itinerary.model_copy(
        update={
            "transitions": itinerary.transitions[:3]
            + (
                Transition(
                    from_ref="talk", to_ref="home", leg=close_leg,
                    slack_min=5, binding_constraint="last BA departure",
                ),
            )
        }
    )
    findings = auditor.audit(request, warned, candidates, iteration=0)
    warns = [
        f for f in findings
        if f.check == constants.CHECK_LAST_DEPARTURE and f.severity == "warn"
    ]
    assert len(warns) == 1
