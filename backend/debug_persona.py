"""Scratch: run persona A offline and print the trace compactly."""
import json

from slackline.core.state import PlanRequest, minutes_to_hhmm
from slackline.pipeline import Pipeline
from slackline.schedule.index import ScheduleIndex

raw = json.load(open("scenarios/persona_a.json", encoding="utf-8"))
request = PlanRequest.model_validate(raw["request"])
index = ScheduleIndex("tests/fixtures/schedule_index_fixture.sqlite")
pipe = Pipeline(index=index, telemetry_dir="var/telemetry")

done = None
for ev in pipe.run(request, "debug"):
    t = ev["type"]
    if t == "stage_completed" and ev["stage"] != "scheduler":
        print(f"[{t}] {ev['stage']} iter={ev['iteration']} ms={ev['ms']:.0f}")
    elif t == "finding":
        f = ev["finding"]
        print(f"  finding it={f['iteration']} {f['severity']:4s} {f['check']}: {f['message']}")
    elif t == "repair_iteration":
        print(f"[repair] iter={ev['iteration']} needs_selector={ev.get('needs_selector')} "
              f"constraints={[c['kind'] for c in ev.get('constraints', [])]} "
              f"unresolvable={ev.get('unresolvable')}")
    elif t == "done":
        done = ev

state = done["state"]
print("\n--- itinerary ---")
names = {c["id"]: c["name"] for c in state["candidates"]}
for item in state["itinerary"]["items"]:
    label = names.get(item["ref_id"], item["ref_id"])
    print(f"  {minutes_to_hhmm(item['start_min'])}-{minutes_to_hhmm(item['end_min'])} "
          f"[{item['kind']}] {label}")
print("--- transitions ---")
for tr in state["itinerary"]["transitions"]:
    leg = tr["leg"]
    print(f"  {tr['from_ref']} -> {tr['to_ref']}: {leg['mode']}/{leg['provenance']} "
          f"depart {minutes_to_hhmm(leg['depart_min'])} arrive {minutes_to_hhmm(leg['arrive_min'])} "
          f"slack={tr['slack_min']} binding='{tr['binding_constraint']}' note='{leg['note'][:70]}'")
tel = done["telemetry"]
print(f"\nverified={tel['legs_verified']} estimated={tel['legs_estimated']} "
      f"repairs={tel['repair_iterations']} degraded={tel['degraded_to_anchors']} "
      f"total_ms={tel['total_ms']:.0f}")
