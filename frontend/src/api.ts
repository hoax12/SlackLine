import type { PlanRequest, StreamEvent } from "./types";

const API_BASE = import.meta.env.VITE_API_BASE ?? "";

export interface WarmupInfo {
  status: string;
  schedule_feed_date: string | null;
  schedule_agencies: string[];
  events_dataset_date: string | null;
  model_configured: boolean;
}

/** Fired on page load, before the user has typed anything, so the container
 * is warm by the time they click (plan section 8). */
export async function warmup(): Promise<WarmupInfo | null> {
  try {
    const resp = await fetch(`${API_BASE}/api/warmup`);
    return resp.ok ? await resp.json() : null;
  } catch {
    return null;
  }
}

/** POST + readable-stream SSE parser. EventSource cannot POST, and the
 * fetch reader gives us the same event granularity. */
export async function streamPlan(
  request: PlanRequest,
  onEvent: (event: StreamEvent) => void,
  signal?: AbortSignal,
): Promise<void> {
  const resp = await fetch(`${API_BASE}/api/plan`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(request),
    signal,
  });
  if (!resp.ok || !resp.body) {
    throw new Error(`plan request failed: HTTP ${resp.status}`);
  }
  const reader = resp.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    let sep;
    while ((sep = buffer.indexOf("\n\n")) >= 0) {
      const frame = buffer.slice(0, sep);
      buffer = buffer.slice(sep + 2);
      let eventName = "";
      let data = "";
      for (const line of frame.split("\n")) {
        if (line.startsWith("event:")) eventName = line.slice(6).trim();
        else if (line.startsWith("data:")) data += line.slice(5).trim();
      }
      if (!eventName || !data) continue;
      try {
        const parsed = JSON.parse(data);
        onEvent({ type: eventName, ...parsed } as StreamEvent);
      } catch {
        // ping/comment frames are fine to drop
      }
    }
  }
}
