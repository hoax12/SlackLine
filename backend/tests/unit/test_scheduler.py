"""Scheduler tests: determinism, inviolable anchors, native constraint
consumption. All offline against the frozen fixture index."""

import pytest

from slackline.core import scheduler
from slackline.core.state import (
    Anchor,
    BanCandidate,
    Candidate,
    LatLng,
    MustDepartBy,
    OpenWindow,
    PlanRequest,
    RankedCandidate,
    SelectorResult,
)
from slackline.schedule.index import ScheduleIndex

WEEKDAY = "2026-08-12"
HAYES_VALLEY = LatLng(lat=37.7767, lon=-122.4233)


@pytest.fixture(scope="module")
def index(request) -> ScheduleIndex:
    fixture = request.path.parent.parent / "fixtures" / "schedule_index_fixture.sqlite"
    return ScheduleIndex(str(fixture))


def _candidates() -> tuple[Candidate, ...]:
    return (
        Candidate(
            id="cafe-1", name="Mission Cafe", category="food",
            location=LatLng(lat=37.7599, lon=-122.4148), duration_min=45,
            is_food=True, price_usd=20.0,
            open_windows=(OpenWindow(start_min=8 * 60, end_min=15 * 60),),
        ),
        Candidate(
            id="museum-1", name="SFMOMA", category="museum",
            location=LatLng(lat=37.7857, lon=-122.4011), duration_min=120,
            price_usd=30.0,
            open_windows=(OpenWindow(start_min=10 * 60, end_min=17 * 60),),
        ),
        Candidate(
            id="park-1", name="Yerba Buena Gardens", category="park",
            location=LatLng(lat=37.7850, lon=-122.4020), duration_min=40,
        ),
    )


def _request() -> PlanRequest:
    return PlanRequest(
        service_date=WEEKDAY,
        origin=HAYES_VALLEY,
        anchors=(
            Anchor(
                id="dinner", title="Dinner meetup in Palo Alto",
                location=LatLng(lat=37.4429, lon=-122.1614),
                start_min=19 * 60, end_min=21 * 60,
            ),
        ),
    )


def _selector() -> SelectorResult:
    return SelectorResult(
        rankings=(
            RankedCandidate(candidate_id="museum-1", rank=1, reason="flagship"),
            RankedCandidate(candidate_id="cafe-1", rank=2, reason="lunch"),
            RankedCandidate(candidate_id="park-1", rank=3, reason="filler"),
        ),
        heuristic=True,
    )


def test_identical_input_identical_output(index):
    a = scheduler.build_itinerary(_request(), _candidates(), _selector(), (), index)
    b = scheduler.build_itinerary(_request(), _candidates(), _selector(), (), index)
    assert a == b


def test_input_order_does_not_matter(index):
    shuffled = tuple(reversed(_candidates()))
    a = scheduler.build_itinerary(_request(), _candidates(), _selector(), (), index)
    b = scheduler.build_itinerary(_request(), shuffled, _selector(), (), index)
    assert a == b


def test_anchor_is_inviolable(index):
    itinerary = scheduler.build_itinerary(
        _request(), _candidates(), _selector(), (), index
    )
    anchor_items = [i for i in itinerary.items if i.kind == "anchor"]
    assert len(anchor_items) == 1
    assert anchor_items[0].start_min == 19 * 60
    assert anchor_items[0].end_min == 21 * 60


def test_impossible_anchors_still_present(index):
    """Two overlapping anchors far apart are unsatisfiable, but the Scheduler
    never drops an anchor — the Auditor reports it instead."""
    request = _request().model_copy(
        update={
            "anchors": (
                Anchor(
                    id="a1", title="SF talk",
                    location=LatLng(lat=37.7793, lon=-122.4193),
                    start_min=14 * 60, end_min=15 * 60,
                ),
                Anchor(
                    id="a2", title="Palo Alto talk",
                    location=LatLng(lat=37.4429, lon=-122.1614),
                    start_min=15 * 60, end_min=16 * 60,
                ),
            )
        }
    )
    itinerary = scheduler.build_itinerary(request, (), None, (), index)
    anchor_ids = [i.ref_id for i in itinerary.items if i.kind == "anchor"]
    assert anchor_ids == ["a1", "a2"]


def test_ban_candidate_constraint(index):
    itinerary = scheduler.build_itinerary(
        _request(), _candidates(), _selector(),
        (BanCandidate(candidate_id="museum-1"),), index,
    )
    refs = {i.ref_id for i in itinerary.items}
    assert "museum-1" not in refs


def test_open_windows_respected(index):
    itinerary = scheduler.build_itinerary(
        _request(), _candidates(), _selector(), (), index
    )
    placed = {i.ref_id: i for i in itinerary.items}
    if "cafe-1" in placed:
        item = placed["cafe-1"]
        assert item.start_min >= 8 * 60
        assert item.end_min <= 15 * 60
    if "museum-1" in placed:
        item = placed["museum-1"]
        assert item.start_min >= 10 * 60
        assert item.end_min <= 17 * 60


def test_must_depart_by_is_respected_for_candidates(index):
    itinerary = scheduler.build_itinerary(
        _request(), _candidates(), _selector(),
        (MustDepartBy(from_ref="museum-1", depart_min=13 * 60),), index,
    )
    placed = {i.ref_id: i for i in itinerary.items}
    if "museum-1" in placed:
        assert placed["museum-1"].end_min <= 13 * 60


def test_every_transition_has_slack_and_binding(index):
    itinerary = scheduler.build_itinerary(
        _request(), _candidates(), _selector(), (), index
    )
    assert len(itinerary.transitions) == len(itinerary.items) - 1
    for transition in itinerary.transitions:
        assert transition.binding_constraint
        assert isinstance(transition.slack_min, int)


def test_candidates_actually_get_placed(index):
    itinerary = scheduler.build_itinerary(
        _request(), _candidates(), _selector(), (), index
    )
    placed = [i for i in itinerary.items if i.kind == "candidate"]
    assert len(placed) >= 2
