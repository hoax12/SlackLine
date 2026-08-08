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
  const [shareCopied, setShareCopied] = useState(false);
  const abortRef = useRef<AbortController | null>(null);

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

  async function run() {
    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;
    setEvents([]);
    setNarration("");
    setFinalState(null);
    setError(null);
    setPhase("running");
    try {
      await streamPlan(
        request,
        (event) => {
          if (event.type === "narration_delta") {
            setNarration((n) => n + event.text);
          } else if (event.type === "done") {
            setFinalState(event.state);
            setPhase("done");
          } else {
            setEvents((prev) => [...prev, event]);
          }
        },
        controller.signal,
      );
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
    await navigator.clipboard.writeText(url);
    setShareCopied(true);
    setTimeout(() => setShareCopied(false), 2000);
  }

  return (
    <main>
      <header>
        <h1>Slackline</h1>
        <p className="tagline">
          Day plans for the SF Bay Area where every transit leg is verified
          against the published schedule — or honestly flagged as an estimate.
        </p>
        {warm && (
          <p className="warm-line">
            transit feed {warm.schedule_feed_date ?? "missing"} ·{" "}
            events dataset {warm.events_dataset_date ?? "missing"} ·{" "}
            {warm.model_configured
              ? "model configured"
              : "keyless mode: heuristic ranking + template narration"}
          </p>
        )}
      </header>

      <section className="personas">
        {PERSONAS.map((p) => (
          <button
            key={p.request.persona}
            className={
              request.persona === p.request.persona ? "persona active" : "persona"
            }
            onClick={() => setRequest(p.request)}
            title={p.blurb}
          >
            {p.label}
          </button>
        ))}
        <div className="controls">
          <label>
            date{" "}
            <input
              type="date"
              value={request.service_date}
              onChange={(e) =>
                setRequest({ ...request, service_date: e.target.value })
              }
            />
          </label>
          <label>
            budget ${" "}
            <input
              type="number"
              value={request.budget_usd ?? ""}
              placeholder="none"
              onChange={(e) =>
                setRequest({
                  ...request,
                  budget_usd:
                    e.target.value === "" ? null : Number(e.target.value),
                })
              }
            />
          </label>
          <button className="primary" onClick={run} disabled={phase === "running"}>
            {phase === "running" ? "planning…" : "plan my day"}
          </button>
          <button onClick={share}>{shareCopied ? "copied!" : "share"}</button>
        </div>
      </section>

      {error && <div className="banner error">{error}</div>}

      {events.length > 0 && (
        <section>
          <h2>What the system is doing</h2>
          <TraceView events={events} />
        </section>
      )}

      {narration && (
        <section>
          <h2>Your day</h2>
          <p className="narration">{narration}</p>
        </section>
      )}

      {finalState && (
        <section>
          <h2>The proof</h2>
          <PlanView state={finalState} />
        </section>
      )}
    </main>
  );
}
