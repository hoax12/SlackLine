export interface LatLng {
  lat: number;
  lon: number;
}

export interface Anchor {
  id: string;
  title: string;
  location: LatLng;
  start_min: number;
  end_min: number;
  notes?: string;
}

export interface PlanRequest {
  persona: string;
  service_date: string;
  origin: LatLng;
  origin_label: string;
  day_start_min: number;
  day_end_min: number;
  interests: string[];
  budget_usd: number | null;
  anchors: Anchor[];
}

export interface Leg {
  from_ref: string;
  to_ref: string;
  mode: "walk" | "transit";
  depart_min: number;
  arrive_min: number;
  provenance: "verified" | "estimated";
  agency?: string | null;
  route?: string | null;
  from_stop?: string | null;
  to_stop?: string | null;
  transit_depart_min?: number | null;
  last_depart_of_day_min?: number | null;
  note?: string;
}

export interface Transition {
  from_ref: string;
  to_ref: string;
  leg: Leg;
  slack_min: number;
  binding_constraint: string;
}

export interface ItineraryItem {
  kind: "anchor" | "candidate" | "origin";
  ref_id: string;
  start_min: number;
  end_min: number;
}

export interface Finding {
  check: string;
  severity: "fail" | "warn";
  message: string;
  subject_ids: string[];
  iteration: number;
}

export interface Candidate {
  id: string;
  name: string;
  category: string;
  source: "fixture" | "geoapify" | "events";
  source_url?: string | null;
  fetched_at?: string | null;
  price_usd?: number | null;
}

export interface PlanState {
  request: PlanRequest;
  weather: string | null;
  candidates: Candidate[];
  selector: {
    heuristic: boolean;
    model: string | null;
    rankings: { candidate_id: string; rank: number; reason: string }[];
  } | null;
  itinerary: { items: ItineraryItem[]; transitions: Transition[] } | null;
  findings: Finding[];
  degraded_to_anchors: boolean;
  schedule_feed_date: string | null;
  events_dataset_date: string | null;
  notices: string[];
}

export type StreamEvent =
  | { type: "stage_started"; stage: string; iteration: number }
  | {
      type: "stage_completed";
      stage: string;
      iteration: number;
      ms: number;
      payload: Record<string, unknown>;
    }
  | { type: "finding"; finding: Finding; iteration: number }
  | {
      type: "repair_iteration";
      iteration: number;
      constraints: Record<string, unknown>[];
      needs_selector?: boolean;
      degraded_to_anchors?: boolean;
    }
  | { type: "narration_delta"; text: string }
  | {
      type: "done";
      state: PlanState;
      narration: string;
      telemetry: Record<string, unknown>;
    };

export function minutesToHhmm(m: number): string {
  const h = Math.floor(m / 60);
  const mm = (m % 60).toString().padStart(2, "0");
  if (h >= 24) return `${(h - 24).toString().padStart(2, "0")}:${mm} +1d`;
  return `${h.toString().padStart(2, "0")}:${mm}`;
}
