"""Stage orchestration: two model stages, four deterministic services, one
typed state object, a bounded repair loop, and an event stream.

Event schema (section 5): ``stage_started``, ``stage_completed(payload)``,
``finding``, ``repair_iteration``, ``narration_delta``, ``done``. The API
layer adapts these dicts to SSE; the scenario runner consumes them directly.
"""

from __future__ import annotations

import time
from typing import Iterator, Optional

from slackline import telemetry
from slackline.cache import TTLCache
from slackline.core import auditor, constants, navigator, repair, scheduler
from slackline.core.state import Finding, PlanRequest, PlanState
from slackline.llm import narrator as narrator_stage
from slackline.llm import selector as selector_stage
from slackline.sources import places


class Pipeline:
    def __init__(
        self,
        index=None,                     # ScheduleIndex or None (degrades to estimates)
        provider=None,                  # llm provider or None (heuristic/template)
        telemetry_dir: Optional[str] = None,
        fetch_weather: bool = False,    # scenario runs stay fully offline
        events_dataset=None,            # dated events artifact (loader result)
    ):
        self.index = index
        self.provider = provider
        self.telemetry_dir = telemetry_dir
        self.fetch_weather = fetch_weather
        self.events_dataset = events_dataset
        self.cache = TTLCache()

    # ------------------------------------------------------------------

    def run(self, request: PlanRequest, request_id: str = "") -> Iterator[dict]:
        record = telemetry.RunRecord(request_id=request_id)
        run_start = time.perf_counter()
        resolve = self._cached_resolver()

        state = PlanState(
            request=request,
            schedule_feed_date=(
                self.index.build_date if self.index is not None else None
            ),
            events_dataset_date=(
                self.events_dataset.build_date
                if self.events_dataset is not None
                else None
            ),
        )

        # --- Scout ------------------------------------------------------
        yield _ev("stage_started", stage="scout", iteration=0)
        t0 = time.perf_counter()
        candidates, notices = places.fetch_candidates(request)
        weather = None
        if self.fetch_weather:
            weather, weather_notices = _try_weather(request)
            notices = notices + weather_notices
        event_candidates, event_notices = self._event_candidates(request)
        candidates = candidates + event_candidates
        notices = notices + event_notices
        state = state.model_copy(
            update={
                "candidates": candidates,
                "weather": weather,
                "notices": state.notices + notices,
            }
        )
        ms = _ms_since(t0)
        record.add_stage("scout", 0, ms)
        record.degraded_sources.extend(notices)
        yield _ev(
            "stage_completed", stage="scout", iteration=0, ms=ms,
            payload={
                "candidate_count": len(candidates),
                "sources": sorted({c.source for c in candidates}),
                "notices": list(notices),
                "weather": weather,
            },
        )

        # --- Selector (iteration 0) --------------------------------------
        state, events = self._run_selector(state, 0, record)
        yield from events

        # --- Scheduler + Auditor (iteration 0) ----------------------------
        state, events = self._run_schedule_and_audit(state, 0, record, resolve)
        yield from events

        # --- Bounded repair loop ------------------------------------------
        # state.iteration always names the last *audited* iteration; the
        # degrade decision below keys off exactly that.
        iteration = 0
        while (
            _fails_at(state.findings, state.iteration)
            and iteration < constants.REPAIR_MAX_ITERATIONS
        ):
            iteration += 1
            plan = repair.plan_repairs(
                request,
                state.itinerary,
                [f for f in state.findings if f.iteration == state.iteration],
                state.constraints,
            )
            yield _ev(
                "repair_iteration",
                iteration=iteration,
                constraints=[c.model_dump() for c in plan.constraints],
                needs_selector=plan.needs_selector,
                unresolvable=list(plan.unresolvable),
            )
            record.repair_iterations = iteration
            if not plan.constraints:
                break  # nothing actionable; the floor below decides
            state = state.model_copy(
                update={"constraints": state.constraints + plan.constraints}
            )
            if plan.needs_selector:
                state, events = self._run_selector(state, iteration, record)
                yield from events
            state, events = self._run_schedule_and_audit(
                state, iteration, record, resolve
            )
            yield from events

        # --- Deterministic floor -------------------------------------------
        if _fails_at(state.findings, state.iteration):
            final_iter = state.iteration + 1
            yield _ev(
                "repair_iteration",
                iteration=final_iter,
                degraded_to_anchors=True,
                constraints=[],
                needs_selector=False,
                unresolvable=[],
            )
            state = state.model_copy(update={"degraded_to_anchors": True})
            record.degraded_to_anchors = True
            t0 = time.perf_counter()
            anchors_only = scheduler.build_itinerary(
                request, (), None, (), self.index, leg_resolver=resolve
            )
            record.add_stage("scheduler", final_iter, _ms_since(t0))
            t0 = time.perf_counter()
            findings = auditor.audit(
                request, anchors_only,
                {c.id: c for c in state.candidates}, final_iter,
            )
            record.add_stage("auditor", final_iter, _ms_since(t0))
            record.add_findings(findings)
            state = state.model_copy(
                update={
                    "itinerary": anchors_only,
                    "findings": state.findings + findings,
                    "iteration": final_iter,
                }
            )
            for f in findings:
                yield _ev("finding", finding=f.model_dump(), iteration=final_iter)

        # --- Narrator ---------------------------------------------------------
        yield _ev("stage_started", stage="narrator", iteration=state.iteration)
        t0 = time.perf_counter()
        chunks: list[str] = []
        for chunk in narrator_stage.narrate(state, provider=self.provider):
            chunks.append(chunk)
            yield _ev("narration_delta", text=chunk)
        narrator_ms = _ms_since(t0)
        tokens = getattr(self.provider, "last_usage", None)
        record.add_stage(
            "narrator", state.iteration, narrator_ms,
            tokens_in=tokens.tokens_in if tokens else 0,
            tokens_out=tokens.tokens_out if tokens else 0,
        )
        yield _ev(
            "stage_completed", stage="narrator", iteration=state.iteration,
            ms=narrator_ms, payload={"length": sum(len(c) for c in chunks)},
        )

        # --- Done ----------------------------------------------------------------
        if state.itinerary is not None:
            transit_legs = [
                t.leg for t in state.itinerary.transitions if t.leg.mode == "transit"
            ]
            record.legs_verified = sum(
                1 for leg in transit_legs if leg.provenance == "verified"
            )
            record.legs_estimated = sum(
                1 for leg in transit_legs if leg.provenance == "estimated"
            )
        record.cache = self.cache.stats()
        record.total_ms = _ms_since(run_start)
        record_path = telemetry.write_run_record(record, self.telemetry_dir)
        yield _ev(
            "done",
            state=state.model_dump(),
            narration="".join(chunks),
            telemetry=record.to_dict(),
            telemetry_path=record_path,
        )

    # ------------------------------------------------------------------

    def _run_selector(self, state: PlanState, iteration: int, record):
        events = [_ev("stage_started", stage="selector", iteration=iteration)]
        t0 = time.perf_counter()
        result = selector_stage.rank(
            state.request, state.candidates, state.constraints,
            state.weather, provider=self.provider,
        )
        ms = _ms_since(t0)
        record.add_stage(
            "selector", iteration, ms,
            tokens_in=result.tokens_in, tokens_out=result.tokens_out,
        )
        state = state.model_copy(update={"selector": result})
        events.append(
            _ev(
                "stage_completed", stage="selector", iteration=iteration, ms=ms,
                payload={
                    "heuristic": result.heuristic,
                    "model": result.model,
                    "top": [
                        {"id": r.candidate_id, "rank": r.rank, "reason": r.reason}
                        for r in result.rankings[:8]
                    ],
                },
            )
        )
        return state, events

    def _run_schedule_and_audit(self, state, iteration, record, resolve):
        events = [_ev("stage_started", stage="scheduler", iteration=iteration)]
        t0 = time.perf_counter()
        itinerary = scheduler.build_itinerary(
            state.request, state.candidates, state.selector,
            state.constraints, self.index, leg_resolver=resolve,
        )
        ms = _ms_since(t0)
        record.add_stage("scheduler", iteration, ms)
        state = state.model_copy(update={"itinerary": itinerary})
        events.append(
            _ev(
                "stage_completed", stage="scheduler", iteration=iteration, ms=ms,
                payload={"itinerary": itinerary.model_dump()},
            )
        )

        events.append(_ev("stage_started", stage="auditor", iteration=iteration))
        t0 = time.perf_counter()
        findings = auditor.audit(
            state.request, itinerary, {c.id: c for c in state.candidates}, iteration
        )
        ms = _ms_since(t0)
        record.add_stage("auditor", iteration, ms)
        record.add_findings(findings)
        state = state.model_copy(
            update={"findings": state.findings + findings, "iteration": iteration}
        )
        for f in findings:
            events.append(_ev("finding", finding=f.model_dump(), iteration=iteration))
        events.append(
            _ev(
                "stage_completed", stage="auditor", iteration=iteration, ms=ms,
                payload={
                    "findings": [f.model_dump() for f in findings],
                    "failures": sum(1 for f in findings if f.severity == "fail"),
                },
            )
        )
        return state, events

    def _event_candidates(self, request: PlanRequest):
        if self.events_dataset is None:
            return (), ()
        try:
            return self.events_dataset.candidates_for(request.service_date), ()
        except Exception as exc:
            return (), (f"events dataset unavailable ({type(exc).__name__})",)

    def _cached_resolver(self):
        """navigator.resolve_leg behind the TTL cache. Keyed per section 5
        with the departure bucketed to 15 minutes; a cached leg is only
        reused when it is still boardable at the requested time."""

        def resolve(index, from_ref, to_ref, from_loc, to_loc, depart, date):
            key = navigator.transit_cache_key(from_loc, to_loc, date, depart)
            cached = self.cache.get("navigator", key)
            if cached is not None and cached.depart_min >= depart:
                return cached.model_copy(
                    update={"from_ref": from_ref, "to_ref": to_ref}
                )
            leg = navigator.resolve_leg(
                index, from_ref, to_ref, from_loc, to_loc, depart, date
            )
            self.cache.set("navigator", key, leg)
            return leg

        return resolve


def _try_weather(request: PlanRequest):
    """Open-Meteo is keyless but still a network call; failures are a
    notice, never an error."""
    try:
        from slackline.sources import weather

        summary = weather.fetch_summary(request.origin, request.service_date)
        if summary is None:
            return None, ("weather unavailable",)
        return summary, ()
    except Exception as exc:
        return None, (f"weather unavailable ({type(exc).__name__})",)


def _ev(event_type: str, **fields) -> dict:
    return {"type": event_type, **fields}


def _ms_since(t0: float) -> float:
    return (time.perf_counter() - t0) * 1000.0


def _fails_at(findings: tuple[Finding, ...], iteration: int) -> bool:
    return any(
        f.severity == "fail" and f.iteration == iteration for f in findings
    )
