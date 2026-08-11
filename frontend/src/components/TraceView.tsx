import type { StreamEvent } from "../types";

const STAGE_LABELS: Record<string, string> = {
  pipeline: "Pipeline",
  scout: "Scout",
  selector: "Selector",
  scheduler: "Scheduler",
  auditor: "Auditor",
  narrator: "Narrator",
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
              <li key={i} className="trace-row started enter">
                <span className="dot pulse" />
                <span className="trace-label">
                  {STAGE_LABELS[event.stage] ?? event.stage}
                </span>
                {event.iteration > 0 && (
                  <span className="iter">iter {event.iteration}</span>
                )}
                <span className="ellipsis">running</span>
              </li>
            );
          case "stage_completed": {
            const payload = event.payload ?? {};
            const heuristic = payload["heuristic"] === true;
            return (
              <li key={i} className="trace-row completed enter">
                <span className="dot done" />
                <span className="trace-label">
                  {STAGE_LABELS[event.stage] ?? event.stage}
                </span>
                <span className="ms">{Math.round(event.ms)} ms</span>
                {heuristic && (
                  <span className="badge badge-heuristic">Heuristic</span>
                )}
                {typeof payload["candidate_count"] === "number" && (
                  <span className="detail">
                    {String(payload["candidate_count"])} candidates
                  </span>
                )}
                {typeof payload["failures"] === "number" &&
                  ((payload["failures"] as number) > 0 ? (
                    <span className="badge badge-fail">
                      {String(payload["failures"])} failure
                      {(payload["failures"] as number) === 1 ? "" : "s"}
                    </span>
                  ) : (
                    <span className="badge badge-ok">Audit clean</span>
                  ))}
              </li>
            );
          }
          case "finding":
            return (
              <li
                key={i}
                className={`trace-row finding enter ${event.finding.severity}`}
              >
                <span className={`badge badge-${event.finding.severity}`}>
                  {event.finding.severity === "fail" ? "Fail" : "Warn"}
                </span>
                <span className="check">{event.finding.check}</span>
                <span className="finding-msg">{event.finding.message}</span>
              </li>
            );
          case "repair_iteration":
            return (
              <li key={i} className="trace-row repair enter">
                {event.degraded_to_anchors ? (
                  <>
                    <span className="badge badge-floor">Anchors floor</span>
                    <span>
                      Degraded to hard anchors — everything shown is provably
                      reachable
                    </span>
                  </>
                ) : (
                  <>
                    <span className="badge badge-repair">
                      Repair · pass {event.iteration}
                    </span>
                    <span>
                      {event.constraints.length} typed constraint
                      {event.constraints.length === 1 ? "" : "s"}
                      {event.needs_selector
                        ? " · re-ranking"
                        : " · scheduler only"}
                    </span>
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
