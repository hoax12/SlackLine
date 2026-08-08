"""Per-request and batch telemetry records (plan section 6).

Three rules, enforced by shape:
1. Findings accumulate — the record stores every finding from every
   iteration; nothing is overwritten. Repair convergence is measured from
   exactly this.
2. Timing is per stage per iteration, never summed across iterations.
3. Records are written to disk on every run, not only during batches, so
   baselines exist before any optimization work.
"""

from __future__ import annotations

import json
import os
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Optional

DEFAULT_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "var", "telemetry")


@dataclass
class StageTiming:
    stage: str
    iteration: int
    ms: float
    tokens_in: int = 0
    tokens_out: int = 0


@dataclass
class RunRecord:
    run_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    timestamp: str = field(
        default_factory=lambda: time.strftime("%Y-%m-%dT%H:%M:%S%z")
    )
    request_id: str = ""            # scenario or user request identifier
    total_ms: float = 0.0
    stages: list[dict] = field(default_factory=list)
    repair_iterations: int = 0
    degraded_to_anchors: bool = False
    cache: dict[str, dict[str, int]] = field(default_factory=dict)
    legs_verified: int = 0
    legs_estimated: int = 0
    findings: list[dict] = field(default_factory=list)  # accumulated, all iterations
    errors: list[str] = field(default_factory=list)
    degraded_sources: list[str] = field(default_factory=list)

    def add_stage(
        self,
        stage: str,
        iteration: int,
        ms: float,
        tokens_in: int = 0,
        tokens_out: int = 0,
    ) -> None:
        self.stages.append(
            {
                "stage": stage,
                "iteration": iteration,
                "ms": round(ms, 2),
                "tokens_in": tokens_in,
                "tokens_out": tokens_out,
            }
        )

    def add_findings(self, findings) -> None:
        """Accumulate; never replace."""
        for f in findings:
            self.findings.append(f.model_dump())

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "timestamp": self.timestamp,
            "request_id": self.request_id,
            "total_ms": round(self.total_ms, 2),
            "stages": self.stages,
            "repair_iterations": self.repair_iterations,
            "degraded_to_anchors": self.degraded_to_anchors,
            "cache": self.cache,
            "legs_verified": self.legs_verified,
            "legs_estimated": self.legs_estimated,
            "findings": self.findings,
            "errors": self.errors,
            "degraded_sources": self.degraded_sources,
        }


def write_run_record(record: RunRecord, directory: Optional[str] = None) -> str:
    """Written to disk on every run. Returns the path."""
    directory = directory or DEFAULT_DIR
    os.makedirs(directory, exist_ok=True)
    path = os.path.join(directory, f"run_{record.run_id}.json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(record.to_dict(), fh, indent=2)
    return path


def percentile(values: list[float], pct: float) -> float:
    """Nearest-rank percentile; deterministic, no numpy."""
    if not values:
        return 0.0
    ordered = sorted(values)
    rank = max(1, int(round(pct / 100.0 * len(ordered))))
    return ordered[min(rank, len(ordered)) - 1]


def batch_record(run_dicts: list[dict[str, Any]]) -> dict[str, Any]:
    """Aggregate per-run records into the section-6 batch record."""
    latencies = [r["total_ms"] for r in run_dicts]
    tokens = [
        sum(s.get("tokens_in", 0) + s.get("tokens_out", 0) for s in r["stages"])
        for r in run_dicts
    ]

    initially_failing = 0
    cleared_after_1 = 0
    cleared_after_2 = 0
    fell_to_fallback = 0
    for r in run_dicts:
        fails_by_iter: dict[int, int] = {}
        for f in r["findings"]:
            if f["severity"] == "fail":
                fails_by_iter[f["iteration"]] = fails_by_iter.get(f["iteration"], 0) + 1
        if fails_by_iter.get(0, 0) == 0:
            continue
        initially_failing += 1
        if r["degraded_to_anchors"]:
            fell_to_fallback += 1
        elif fails_by_iter.get(1, 0) == 0:
            cleared_after_1 += 1
        elif fails_by_iter.get(2, 0) == 0:
            cleared_after_2 += 1
        else:
            fell_to_fallback += 1

    total_verified = sum(r["legs_verified"] for r in run_dicts)
    total_estimated = sum(r["legs_estimated"] for r in run_dicts)
    audit_outcomes = {
        "clean_first_pass": sum(
            1 for r in run_dicts
            if not any(f["severity"] == "fail" and f["iteration"] == 0
                       for f in r["findings"])
        ),
        "repaired": cleared_after_1 + cleared_after_2,
        "degraded_to_anchors": sum(1 for r in run_dicts if r["degraded_to_anchors"]),
    }
    return {
        "runs": len(run_dicts),
        "latency_ms": {
            "p50": round(percentile(latencies, 50), 2),
            "p95": round(percentile(latencies, 95), 2),
        },
        "tokens_per_plan": {
            "mean": round(sum(tokens) / len(tokens), 1) if tokens else 0,
            "p95": percentile([float(t) for t in tokens], 95),
        },
        "repair_convergence": {
            "initially_failing": initially_failing,
            "cleared_after_1_pass": cleared_after_1,
            "cleared_after_2_passes": cleared_after_2,
            "fell_to_deterministic_fallback": fell_to_fallback,
        },
        "audit_outcomes": audit_outcomes,
        "verified_leg_share": (
            round(total_verified / (total_verified + total_estimated), 3)
            if (total_verified + total_estimated)
            else None
        ),
    }
