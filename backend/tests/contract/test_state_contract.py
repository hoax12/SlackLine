"""Contract tests for the typed state: frozen everywhere, lossless
round-trips, and typed repair constraints with a working discriminator."""

import pytest
from pydantic import TypeAdapter, ValidationError

from slackline.core import constants
from slackline.core.state import (
    Anchor,
    BanCandidate,
    Candidate,
    Finding,
    LatLng,
    Leg,
    MustDepartBy,
    PlanRequest,
    PlanState,
    RepairConstraint,
    minutes_to_hhmm,
)

ORIGIN = LatLng(lat=37.7793, lon=-122.4193)


def _request() -> PlanRequest:
    return PlanRequest(
        service_date="2026-08-15",
        origin=ORIGIN,
        anchors=(
            Anchor(
                id="a1",
                title="Dinner meetup",
                location=LatLng(lat=37.4419, lon=-122.143),
                start_min=19 * 60,
                end_min=21 * 60,
            ),
        ),
    )


def test_models_are_frozen():
    state = PlanState(request=_request())
    with pytest.raises(ValidationError):
        state.iteration = 3  # type: ignore[misc]
    with pytest.raises(ValidationError):
        state.request.service_date = "2020-01-01"  # type: ignore[misc]


def test_state_evolves_via_model_copy():
    state = PlanState(request=_request())
    finding = Finding(check="feasibility", severity="fail", message="x", iteration=0)
    evolved = state.model_copy(update={"findings": state.findings + (finding,)})
    assert state.findings == ()
    assert evolved.findings == (finding,)


def test_round_trip_serialization():
    state = PlanState(
        request=_request(),
        candidates=(
            Candidate(
                id="c1",
                name="Cafe",
                category="food",
                location=ORIGIN,
                duration_min=45,
                is_food=True,
                source="fixture",
            ),
        ),
        constraints=(
            BanCandidate(candidate_id="c9"),
            MustDepartBy(from_ref="a1", depart_min=21 * 60 + 30),
        ),
    )
    raw = state.model_dump_json()
    again = PlanState.model_validate_json(raw)
    assert again == state


def test_repair_constraint_discriminator():
    adapter = TypeAdapter(RepairConstraint)
    c = adapter.validate_python(
        {"kind": "must_depart_by", "from_ref": "a1", "depart_min": 1290}
    )
    assert isinstance(c, MustDepartBy)
    with pytest.raises(ValidationError):
        adapter.validate_python({"kind": "reroute_everything"})


def test_finding_check_names_match_constants():
    for check in constants.ALL_CHECKS:
        Finding(check=check, severity="warn", message="ok")  # must validate
    with pytest.raises(ValidationError):
        Finding(check="vibes", severity="warn", message="no")


def test_leg_provenance_is_binary():
    leg = Leg(
        from_ref="origin", to_ref="a1", mode="transit",
        depart_min=600, arrive_min=650, provenance="verified",
    )
    assert leg.provenance == "verified"
    with pytest.raises(ValidationError):
        Leg(
            from_ref="origin", to_ref="a1", mode="transit",
            depart_min=600, arrive_min=650, provenance="probably",
        )


def test_post_midnight_rendering():
    assert minutes_to_hhmm(24 * 60 + 40) == "00:40 (+1d)"
    assert minutes_to_hhmm(9 * 60 + 5) == "09:05"
