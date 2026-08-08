"""Phase-5 checkpoint: persona A runs end to end fully offline with
heuristic ranking, and the emitted trace shows a repair iteration firing
and clearing. This is the core demo, proven before any model or UI exists."""

import json
import pathlib

import pytest

from slackline.core.constants import SSE_EVENTS
from slackline.core.state import PlanRequest
from slackline.pipeline import Pipeline
from slackline.schedule.index import ScheduleIndex

BACKEND = pathlib.Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def index() -> ScheduleIndex:
    return ScheduleIndex(
        str(BACKEND / "tests" / "fixtures" / "schedule_index_fixture.sqlite")
    )


@pytest.fixture(scope="module")
def persona_a() -> PlanRequest:
    raw = json.loads((BACKEND / "scenarios" / "persona_a.json").read_text("utf-8"))
    return PlanRequest.model_validate(raw["request"])


def _run(request, index, tmp_path):
    pipeline = Pipeline(index=index, telemetry_dir=str(tmp_path))
    return list(pipeline.run(request, request_id="test"))


def test_persona_a_end_to_end_offline(index, persona_a, tmp_path):
    events = _run(persona_a, index, tmp_path)

    types = {e["type"] for e in events}
    assert types <= set(SSE_EVENTS), f"unknown event types: {types - set(SSE_EVENTS)}"
    assert events[-1]["type"] == "done"

    # Keyless: selector ran the heuristic path and said so.
    selector_done = [
        e for e in events
        if e["type"] == "stage_completed" and e["stage"] == "selector"
    ]
    assert selector_done and all(e["payload"]["heuristic"] for e in selector_done)

    # The repair loop fired...
    repairs = [e for e in events if e["type"] == "repair_iteration"]
    assert repairs, "expected at least one repair iteration for persona A"

    # ...because iteration 0 failed the audit...
    telemetry = events[-1]["telemetry"]
    fails_by_iter = {}
    for f in telemetry["findings"]:
        if f["severity"] == "fail":
            fails_by_iter.setdefault(f["iteration"], []).append(f)
    assert 0 in fails_by_iter, "persona A should fail its first audit"

    # ...and the failure cleared without hitting the deterministic floor.
    assert not telemetry["degraded_to_anchors"]
    last_iter = max(f["iteration"] for f in telemetry["findings"])
    assert last_iter not in fails_by_iter, (
        f"failures persisted to final iteration: {fails_by_iter.get(last_iter)}"
    )

    # Findings accumulated across iterations, never overwritten.
    iterations_seen = {f["iteration"] for f in telemetry["findings"]}
    assert {0, last_iter} <= iterations_seen

    # Narration streamed and the state is complete.
    assert any(e["type"] == "narration_delta" for e in events)
    state = events[-1]["state"]
    assert state["itinerary"] is not None
    assert state["schedule_feed_date"] == "2026-08-08"


def test_pipeline_is_deterministic(index, persona_a, tmp_path):
    a = _run(persona_a, index, tmp_path)
    b = _run(persona_a, index, tmp_path)
    assert a[-1]["state"]["itinerary"] == b[-1]["state"]["itinerary"]
    assert a[-1]["state"]["findings"] == b[-1]["state"]["findings"]
    assert a[-1]["narration"] == b[-1]["narration"]


def test_timing_is_per_stage_per_iteration(index, persona_a, tmp_path):
    events = _run(persona_a, index, tmp_path)
    stages = events[-1]["telemetry"]["stages"]
    scheduler_runs = [s for s in stages if s["stage"] == "scheduler"]
    auditor_runs = [s for s in stages if s["stage"] == "auditor"]
    # Repair ran, so scheduler and auditor must appear once per iteration.
    assert len(scheduler_runs) >= 2
    assert len(auditor_runs) >= 2
    assert {s["iteration"] for s in scheduler_runs} == {
        s["iteration"] for s in auditor_runs
    }


def test_verified_legs_counted(index, persona_a, tmp_path):
    events = _run(persona_a, index, tmp_path)
    telemetry = events[-1]["telemetry"]
    # Persona A rides Caltrain to Palo Alto: at least one verified leg.
    assert telemetry["legs_verified"] >= 1
    # Verified and estimated are disjoint counts over transit legs.
    transitions = events[-1]["state"]["itinerary"]["transitions"]
    transit = [t for t in transitions if t["leg"]["mode"] == "transit"]
    assert telemetry["legs_verified"] + telemetry["legs_estimated"] == len(transit)


def test_run_record_written_to_disk(index, persona_a, tmp_path):
    events = _run(persona_a, index, tmp_path)
    path = pathlib.Path(events[-1]["telemetry_path"])
    assert path.exists()
    record = json.loads(path.read_text("utf-8"))
    assert record["request_id"] == "test"
    assert record["stages"]
