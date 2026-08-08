import type { PlanState } from "../types";
import { minutesToHhmm } from "../types";

function ageDays(iso: string | null): string {
  if (!iso) return "missing";
  const days = Math.floor((Date.now() - new Date(iso).getTime()) / 86_400_000);
  return days <= 0 ? "today" : `${days}d old`;
}

/** The finished plan: timeline, per-transition slack with its binding
 * constraint, provenance badges, and both artifact ages. */
export function PlanView({ state }: { state: PlanState }) {
  const itinerary = state.itinerary;
  if (!itinerary) return null;
  const names = new Map<string, string>();
  for (const c of state.candidates) names.set(c.id, c.name);
  for (const a of state.request.anchors) names.set(a.id, a.title);
  names.set("origin", state.request.origin_label);
  names.set("home", `back at ${state.request.origin_label}`);
  const sources = new Map(state.candidates.map((c) => [c.id, c]));

  return (
    <div className="plan">
      <div className="freshness">
        <span title="GTFS schedule index build date">
          transit feed: {state.schedule_feed_date ?? "none"} (
          {ageDays(state.schedule_feed_date)})
        </span>
        <span title="events artifact build date">
          events dataset: {state.events_dataset_date ?? "none"} (
          {ageDays(state.events_dataset_date)})
        </span>
        {state.weather && <span>weather: {state.weather}</span>}
      </div>

      {state.degraded_to_anchors && (
        <div className="banner floor">
          Repair could not clear every failure — this plan is your hard
          anchors only, and every leg shown is provably reachable.
        </div>
      )}
      {state.notices.map((n) => (
        <div key={n} className="banner notice">
          {n}
        </div>
      ))}

      <ol className="timeline">
        {itinerary.items.map((item, i) => {
          const transition = i > 0 ? itinerary.transitions[i - 1] : null;
          const candidate = sources.get(item.ref_id);
          return (
            <li key={`${item.ref_id}-${i}`}>
              {transition && (
                <div className="transition">
                  <span
                    className={`badge badge-${transition.leg.provenance}`}
                    title={transition.leg.note ?? ""}
                  >
                    {transition.leg.provenance === "verified"
                      ? `VERIFIED ${transition.leg.agency ?? ""} ${transition.leg.route ?? ""}`
                      : `ESTIMATED ${transition.leg.mode}`}
                  </span>
                  <span className="leg-times">
                    {minutesToHhmm(transition.leg.depart_min)} →{" "}
                    {minutesToHhmm(transition.leg.arrive_min)}
                  </span>
                  <span
                    className={`slack ${transition.slack_min < 0 ? "neg" : transition.slack_min <= 20 ? "tight" : ""}`}
                  >
                    {transition.slack_min} min slack
                  </span>
                  <span className="binding">
                    binding: {transition.binding_constraint}
                  </span>
                </div>
              )}
              <div className={`stop ${item.kind}`}>
                <span className="time">
                  {minutesToHhmm(item.start_min)}
                  {item.end_min !== item.start_min &&
                    `–${minutesToHhmm(item.end_min)}`}
                </span>
                <span className="name">
                  {names.get(item.ref_id) ?? item.ref_id}
                </span>
                {item.kind === "anchor" && (
                  <span className="badge badge-anchor">hard anchor</span>
                )}
                {candidate && (
                  <span className={`badge badge-src-${candidate.source}`}>
                    {candidate.source === "geoapify"
                      ? "live place data"
                      : candidate.source === "events"
                        ? "events dataset"
                        : "bundled fixture"}
                  </span>
                )}
                {candidate?.source_url && (
                  <a
                    className="src-link"
                    href={candidate.source_url}
                    target="_blank"
                    rel="noreferrer"
                  >
                    source
                  </a>
                )}
              </div>
            </li>
          );
        })}
      </ol>

      {state.selector && (
        <p className="selector-note">
          Ranked by{" "}
          {state.selector.heuristic
            ? "deterministic heuristic (no model key configured)"
            : `model ${state.selector.model}`}
          .
        </p>
      )}
    </div>
  );
}
