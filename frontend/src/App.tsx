import { useEffect, useRef, useState } from "react";

import { streamPlan, warmup, WarmupInfo } from "./api";
import { PlanView } from "./components/PlanView";
import { TraceView } from "./components/TraceView";
import { PERSONAS } from "./personas";
import { decodeShare, encodeShare } from "./share";
import type { PlanRequest, PlanState, StreamEvent } from "./types";

type Phase = "idle" | "running" | "done" | "error";

export default function App() {
  const [request, setRequest] = useState<PlanRequest>(PERSONAS[0].request);
  const [events, setEvents] = useState<StreamEvent[]>([]);
  const [narration, setNarration] = useState("");
  const [finalState, setFinalState] = useState<PlanState | null>(null);
  const [phase, setPhase] = useState<Phase>("idle");
  const [error, setError] = useState<string | null>(null);
  const [warm, setWarm] = useState<WarmupInfo | null>(null);
  const [shareNote, setShareNote] = useState<string | null>(null);
  const abortRef = useRef<AbortController | null>(null);
  const resultsRef = useRef<HTMLDivElement>(null);

  const activePersona =
    PERSONAS.find((p) => p.request.persona === request.persona) ?? PERSONAS[0];

  useEffect(() => {
    // Warm the container while the user is still reading the form, and
    // decode a shared request from the fragment if one is present.
    warmup().then(setWarm);
    const fragment = window.location.hash.slice(1);
    if (fragment) {
      decodeShare(fragment)
        .then((decoded) => setRequest(decoded as PlanRequest))
        .catch(() => undefined);
    }
  }, []);

  useEffect(() => {
    if (phase === "running" && events.length === 1) {
      resultsRef.current?.scrollIntoView({ behavior: "smooth", block: "start" });
    }
  }, [phase, events.length]);

  async function run() {
    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;
    setEvents([]);
    setNarration("");
    setFinalState(null);
    setError(null);
    setPhase("running");
    let sawDone = false;
    try {
      await streamPlan(
        request,
        (event) => {
          if (event.type === "narration_delta") {
            setNarration((n) => n + event.text);
          } else if (event.type === "done") {
            sawDone = true;
            setFinalState(event.state);
            setPhase("done");
          } else {
            setEvents((prev) => [...prev, event]);
          }
        },
        controller.signal,
      );
      // A stream that ends without `done` (backend crash, proxy timeout)
      // must not leave the button disabled on "running" forever.
      if (!sawDone && !controller.signal.aborted) {
        setError("The plan stream ended before finishing. Please try again.");
        setPhase("error");
      }
    } catch (exc) {
      if (!controller.signal.aborted) {
        setError(String(exc));
        setPhase("error");
      }
    }
  }

  async function share() {
    const fragment = await encodeShare(request);
    const url = `${window.location.origin}${window.location.pathname}#${fragment}`;
    window.history.replaceState(null, "", `#${fragment}`);
    // Clipboard needs a secure context and can be denied. The share URL is in
    // the address bar either way, so a failed copy is a different message,
    // not an unhandled rejection that kills the handler.
    const copied = await navigator.clipboard
      ?.writeText(url)
      .then(() => true)
      .catch(() => false);
    setShareNote(copied ? "Link copied" : "Link in address bar");
    setTimeout(() => setShareNote(null), 2000);
  }

  const hasResults = events.length > 0 || narration || finalState || error;

  return (
    <div className="page">
      <main>
        <nav className="topbar" aria-label="Primary navigation">
          <a className="wordmark" href="/" aria-label="Slackline home">
            <svg viewBox="0 0 36 36" aria-hidden="true">
              <path d="M5 25.5 13.5 10l9 16L31 10.5" />
              <circle cx="5" cy="25.5" r="2.5" />
              <circle cx="13.5" cy="10" r="2.5" />
              <circle cx="22.5" cy="26" r="2.5" />
              <circle cx="31" cy="10.5" r="2.5" />
            </svg>
            Slackline
          </a>
          <div className="system-status">
            <span className={warm ? "status-dot ready" : "status-dot"} />
            {warm ? "Planner ready" : "Warming planner"}
          </div>
        </nav>

        <header className="hero">
          <div className="hero-copy">
            <p className="eyebrow">
              <span>SF Bay Area</span>
              <span>Constraint-aware planning</span>
            </p>
            <h1>
              A day that
              <br />
              <em>actually works.</em>
            </h1>
            <p className="tagline">
              Not another plausible list. Slackline checks every transfer,
              protects your fixed commitments, and repairs the plan when the
              math does not work.
            </p>
            <div className="hero-proof" aria-label="Slackline guarantees">
              <span><b>01</b> Published schedules</span>
              <span><b>02</b> Hard-anchor safe</span>
              <span><b>03</b> Self-repairing</span>
            </div>
          </div>

          <div className="route-card" aria-hidden="true">
            <div className="route-card-head">
              <span>Feasibility preview</span>
              <span className="route-live">live audit</span>
            </div>
            <div className="route-visual">
              <div className="route-stop first">
                <i />
                <div><strong>SoMa</strong><small>09:00 · start</small></div>
              </div>
              <div className="route-leg">
                <span>Caltrain 142</span>
                <span>verified</span>
              </div>
              <div className="route-stop anchor-preview">
                <i />
                <div><strong>Palo Alto</strong><small>19:00 · hard anchor</small></div>
              </div>
              <div className="route-margin">
                <span>Binding margin</span>
                <strong>+18 <small>min</small></strong>
              </div>
            </div>
          </div>
        </header>

        <section className="composer" aria-label="Plan controls">
          <div className="composer-head">
            <div>
              <span className="section-kicker">Start with a real scenario</span>
              <h2>Where does your day need to hold?</h2>
            </div>
            <span className="step-label">01 / Configure</span>
          </div>
          <div className="persona-toggle" role="tablist" aria-label="Persona">
            {PERSONAS.map((p, index) => {
              const active = request.persona === p.request.persona;
              return (
                <button
                  key={p.id}
                  role="tab"
                  aria-selected={active}
                  className={active ? "persona active" : "persona"}
                  onClick={() => setRequest(p.request)}
                >
                  <span className="persona-index">0{index + 1}</span>
                  <span className="persona-copy">
                    <span className="persona-short">{p.short}</span>
                    <span className="persona-label">{p.label}</span>
                  </span>
                  <span className="persona-check" aria-hidden="true">✓</span>
                </button>
              );
            })}
          </div>

          <div className="scenario-summary">
            <p>{activePersona.blurb}</p>
            <div>
              <span>{request.origin_label}</span>
              <span>{request.anchors.length} hard anchor</span>
              <span>{request.interests.join(" + ")}</span>
            </div>
          </div>

          <div className="controls">
            <label className="field">
              <span>Date</span>
              <input
                type="date"
                value={request.service_date}
                onChange={(e) =>
                  setRequest({ ...request, service_date: e.target.value })
                }
              />
            </label>
            <label className="field field-budget">
              <span>Budget</span>
              <span className="budget-wrap">
                <span className="budget-prefix">$</span>
                <input
                  type="number"
                  value={request.budget_usd ?? ""}
                  placeholder="—"
                  onChange={(e) =>
                    setRequest({
                      ...request,
                      budget_usd:
                        e.target.value === "" ? null : Number(e.target.value),
                    })
                  }
                />
              </span>
            </label>
            <div className="actions">
              <button
                className="primary"
                onClick={run}
                disabled={phase === "running"}
              >
                <span>{phase === "running" ? "Building your plan…" : "Verify my day"}</span>
                <span aria-hidden="true">{phase === "running" ? "···" : "→"}</span>
              </button>
              <button className="ghost" onClick={share} type="button">
                {shareNote ?? "Share"}
              </button>
            </div>
          </div>

          {warm && (
            <p className="warm-line">
              <span>
                <i className="data-dot verified" />
                Schedule <strong>{warm.schedule_feed_date ?? "missing"}</strong>
              </span>
              <span>
                <i className="data-dot" />
                Events <strong>{warm.events_dataset_date ?? "missing"}</strong>
              </span>
              <span>
                <i className="data-dot model" />
                {warm.model_configured
                  ? "Model configured"
                  : "Deterministic ranking"}
              </span>
            </p>
          )}
        </section>

        {hasResults && (
          <div className="results" ref={resultsRef}>
            {error && <div className="banner error">{error}</div>}

            {events.length > 0 && (
              <section className="panel panel-trace">
                <div className="panel-head">
                  <span className="panel-number">01</span>
                  <div>
                    <h2>Live pipeline</h2>
                    <p>
                      {phase === "running"
                        ? "Checking your day now."
                        : "Every stage and repair pass."}
                    </p>
                  </div>
                  <span className={`phase-pill ${phase}`}>{phase}</span>
                </div>
                <TraceView events={events} />
              </section>
            )}

            {finalState && (
              <section className="panel panel-proof">
                <div className="panel-head">
                  <span className="panel-number">02</span>
                  <div>
                    <h2>The proof</h2>
                    <p>Every margin, source, and binding constraint.</p>
                  </div>
                </div>
                <PlanView state={finalState} />
              </section>
            )}

            {narration && (
              <section className="panel panel-narration">
                <div className="panel-head">
                  <span className="panel-number">03</span>
                  <div>
                    <h2>Your day, in plain English</h2>
                    <p>The readable version of the verified plan.</p>
                  </div>
                </div>
                <p className="narration">{narration}</p>
              </section>
            )}
          </div>
        )}
      </main>
    </div>
  );
}
