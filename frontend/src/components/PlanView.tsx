import type { PlanState } from "../types";
import { minutesToHhmm } from "../types";

function ageDays(iso: string | null): string {
  if (!iso) return "missing";
  const days = Math.floor((Date.now() - new Date(iso).getTime()) / 86_400_000);
  return days <= 0 ? "today" : `${days}d old`;
}

function slackClass(slack: number): string {
  if (slack < 0) return "neg";
  if (slack <= 20) return "tight";
  return "";
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

  const slacks = itinerary.transitions.map((t) => t.slack_min);
  const minSlack = slacks.length ? Math.min(...slacks) : null;
  // Only transit legs are schedule-verifiable, so only they belong in this
  // ratio; a walk is neither verified nor an unverified ride. This matches
  // the backend's telemetry counts, which filter to transit the same way.
  const transitLegs = itinerary.transitions.filter(
    (t) => t.leg.mode === "transit",
  );
  const verified = transitLegs.filter(
    (t) => t.leg.provenance === "verified",
  ).length;
  const estimated = transitLegs.length - verified;

  return (
    <div className="plan">
      <div className="proof-stats">
        {minSlack !== null && (
          <div className={`stat ${slackClass(minSlack)}`}>
            <span className="stat-value">{minSlack}</span>
            <span className="stat-label">minutes at the tightest transition</span>
          </div>
        )}
        <div className="stat">
          <span className="stat-value">
            {verified}
            <span className="stat-of">/{transitLegs.length}</span>
          </span>
          <span className="stat-label">transit legs schedule-verified</span>
        </div>
        {estimated > 0 && (
          <div className="stat warn">
            <span className="stat-value">{estimated}</span>
            <span className="stat-label">estimated (flagged)</span>
          </div>
        )}
      </div>

      <div className="freshness">
        <span title="GTFS schedule index build date">
          Transit feed {state.schedule_feed_date ?? "none"}
          <em>{ageDays(state.schedule_feed_date)}</em>
        </span>
        <span title="events artifact build date">
          Events {state.events_dataset_date ?? "none"}
          <em>{ageDays(state.events_dataset_date)}</em>
        </span>
        {state.weather && <span>Weather {state.weather}</span>}
      </div>

      {state.degraded_to_anchors && (
        <div className="banner floor">
          Repair could not clear every failure — this plan is your hard anchors
          only, and every leg shown is provably reachable.
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
            <li key={`${item.ref_id}-${i}`} className="timeline-item">
              {transition && (
                <div className={`transition ${slackClass(transition.slack_min)}`}>
                  <div className="slack-block">
                    <span
                      className={`slack ${slackClass(transition.slack_min)}`}
                    >
                      {transition.slack_min}
                      <small>min slack</small>
                    </span>
                  </div>
                  <div className="leg-meta">
                    <span
                      className={`badge badge-${transition.leg.provenance}`}
                      title={transition.leg.note ?? ""}
                    >
                      {transition.leg.provenance === "verified"
                        ? `Verified ${[transition.leg.agency, transition.leg.route]
                            .filter(Boolean)
                            .join(" ")}`
                        : `Estimated ${transition.leg.mode}`}
                    </span>
                    <span className="leg-times">
                      {minutesToHhmm(transition.leg.depart_min)} →{" "}
                      {minutesToHhmm(transition.leg.arrive_min)}
                    </span>
                    <span className="binding">
                      Binding: {transition.binding_constraint}
                    </span>
                  </div>
                </div>
              )}
              <div className={`stop ${item.kind}`}>
                <span className="time">
                  {minutesToHhmm(item.start_min)}
                  {item.end_min !== item.start_min &&
                    `–${minutesToHhmm(item.end_min)}`}
                </span>
                <div className="stop-body">
                  <span className="name">
                    {names.get(item.ref_id) ?? item.ref_id}
                  </span>
                  <div className="stop-tags">
                    {item.kind === "anchor" && (
                      <span className="badge badge-anchor">Hard anchor</span>
                    )}
                    {candidate && (
                      <span className={`badge badge-src-${candidate.source}`}>
                        {candidate.source === "geoapify"
                          ? "Live place data"
                          : candidate.source === "events"
                            ? "Events dataset"
                            : "Bundled fixture"}
                      </span>
                    )}
                    {candidate?.source_url && (
                      <a
                        className="src-link"
                        href={candidate.source_url}
                        target="_blank"
                        rel="noreferrer"
                      >
                        Source
                      </a>
                    )}
                  </div>
                </div>
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
