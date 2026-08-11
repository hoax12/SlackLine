"""Scenario batch runner. Fully offline: fixture venues, local schedule
index, no weather, no model keys required.

    python -m scenarios.runner --db data/schedule_index.sqlite --repeat 5

Emits one structured batch record (section 6) with p50/p95 latency, token
stats, repair convergence, audit outcomes, and verified leg share, plus the
per-run records the pipeline already writes on every run.
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from slackline.core.state import PlanRequest  # noqa: E402
from slackline.pipeline import Pipeline  # noqa: E402
from slackline.schedule.index import ScheduleIndex  # noqa: E402
from slackline.telemetry import batch_record  # noqa: E402

SCENARIO_DIR = os.path.dirname(os.path.abspath(__file__))


def load_scenarios() -> list[dict]:
    """Scenario files only. The glob also matches request fixtures like
    persona_a_request.json (a bare PlanRequest body used by the curl
    example), so a scenario is identified by carrying both keys rather
    than by filename alone."""
    out = []
    for path in sorted(glob.glob(os.path.join(SCENARIO_DIR, "persona_*.json"))):
        with open(path, encoding="utf-8") as fh:
            doc = json.load(fh)
        if isinstance(doc, dict) and "id" in doc and "request" in doc:
            out.append(doc)
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", default=os.path.join("data", "schedule_index.sqlite"))
    ap.add_argument("--repeat", type=int, default=5, help="runs per scenario")
    ap.add_argument("--out", default=os.path.join("var", "batch"))
    args = ap.parse_args(argv)

    index = ScheduleIndex(args.db) if os.path.exists(args.db) else None
    if index is None:
        print(f"warning: no schedule index at {args.db}; legs will be estimates")

    runs: list[dict] = []
    for scenario in load_scenarios():
        request = PlanRequest.model_validate(scenario["request"])
        for i in range(args.repeat):
            pipeline = Pipeline(index=index)  # fresh cache per run: honest latency
            done = None
            repair_events = 0
            for event in pipeline.run(request, request_id=f"{scenario['id']}#{i}"):
                if event["type"] == "repair_iteration":
                    repair_events += 1
                elif event["type"] == "done":
                    done = event
            assert done is not None
            runs.append(done["telemetry"])
            print(
                f"{scenario['id']} run {i}: {done['telemetry']['total_ms']:.0f} ms, "
                f"repairs={done['telemetry']['repair_iterations']}, "
                f"degraded={done['telemetry']['degraded_to_anchors']}, "
                f"verified_legs={done['telemetry']['legs_verified']}/"
                f"{done['telemetry']['legs_verified'] + done['telemetry']['legs_estimated']}"
            )

    batch = batch_record(runs)
    os.makedirs(args.out, exist_ok=True)
    stamp = time.strftime("%Y%m%d_%H%M%S")
    path = os.path.join(args.out, f"batch_{stamp}.json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(batch, fh, indent=2)
    print(json.dumps(batch, indent=2))
    print(f"batch record -> {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
