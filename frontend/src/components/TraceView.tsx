import type { StreamEvent } from "../types";

const STAGE_LABELS: Record<string, string> = {
  pipeline: "Pipeline",
  scout: "Scout (sources)",
  selector: "Selector (model)",
  scheduler: "Scheduler",
  auditor: "Auditor",
  narrator: "Narrator (model)",
};

/** The live event stream: stages, findings, and — the moment that matters —
 * repair iterations, rendered loudly. */
export function TraceView({ events }: { events: StreamEvent[] }) {
  return (
    <ol className="trace">
      {events.map((event, i) => {
        switch (event.type) {
          case "stage_started":
            return (
              <li key={i} className="trace-row started">
                <span className="dot" />
                {STAGE_LABELS[event.stage] ?? event.stage}
                {event.iteration > 0 && (
                  <span className="iter">iteration {event.iteration}</span>
                )}
                <span className="ellipsis">…</span>
              </li>
            );
          case "stage_completed": {
            const payload = event.payload ?? {};
            const heuristic = payload["heuristic"] === true;
            return (
              <li key={i} className="trace-row completed">
                <span className="dot done" />
                {STAGE_LABELS[event.stage] ?? event.stage}
                <span className="ms">{Math.round(event.ms)} ms</span>
                {heuristic && (
                  <span className="badge badge-heuristic">heuristic ranking</span>
                )}
                {typeof payload["candidate_count"] === "number" && (
                  <span className="detail">
                    {String(payload["candidate_count"])} candidates
                  </span>
                )}
                {typeof payload["failures"] === "number" &&
                  ((payload["failures"] as number) > 0 ? (
                    <span className="badge badge-fail">
                      {String(payload["failures"])} failure(s)
                    </span>
                  ) : (
                    <span className="badge badge-ok">audit clean</span>
                  ))}
              </li>
            );
          }
          case "finding":
            return (
              <li
                key={i}
                className={`trace-row finding ${event.finding.severity}`}
              >
                <span className={`badge badge-${event.finding.severity}`}>
                  {event.finding.severity === "fail" ? "FAIL" : "WARN"}
                </span>
                <span className="check">{event.finding.check}</span>
                {event.finding.message}
              </li>
            );
          case "repair_iteration":
            return (
              <li key={i} className="trace-row repair">
                {event.degraded_to_anchors ? (
                  <>
                    <span className="badge badge-floor">deterministic floor</span>
                    plan degraded to hard anchors only — everything shown is
                    provably reachable
                  </>
                ) : (
                  <>
                    <span className="badge badge-repair">
                      repair iteration {event.iteration}
                    </span>
                    {event.constraints.length} typed constraint(s) added
                    {event.needs_selector ? ", re-ranking" : ", scheduler-only"}
                  </>
                )}
              </li>
            );
          default:
            return null;
        }
      })}
    </ol>
  );
}
