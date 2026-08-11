import type { PlanRequest } from "./types";

/** Persona templates. Persona B is deliberately scoped to a Saturday —
 * v1 state is strictly single-day and the last-BART-home constraint is the
 * point that survives. */
export const PERSONAS: {
  id: string;
  short: string;
  label: string;
  blurb: string;
  request: PlanRequest;
}[] = [
  {
    id: "persona_a",
    short: "SoMa → Palo Alto",
    label: "SoMa visitor, Palo Alto dinner",
    blurb:
      "Museum-and-park day on a budget; hard 19:00 dinner in Palo Alto. Watch the audit catch an overrun and repair it.",
    request: {
      persona: "persona_a",
      service_date: "2026-08-12",
      origin: { lat: 37.7785, lon: -122.3985 },
      origin_label: "SoMa hotel",
      day_start_min: 540,
      day_end_min: 1425,
      interests: ["museum", "park"],
      budget_usd: 85.0,
      anchors: [
        {
          id: "dinner-meetup",
          title: "Dinner meetup with college friends",
          location: { lat: 37.4429, lon: -122.1614 },
          start_min: 1140,
          end_min: 1260,
          notes: "University Ave, Palo Alto. Cannot move.",
        },
      ],
    },
  },
  {
    id: "persona_b",
    short: "Berkeley → Civic Center",
    label: "Berkeley local, Saturday show in SF",
    blurb:
      "Saturday with an evening show at Civic Center. The last BART home is the binding constraint — that is the whole point.",
    request: {
      persona: "persona_b",
      service_date: "2026-08-15",
      origin: { lat: 37.8703, lon: -122.268 },
      origin_label: "Downtown Berkeley",
      day_start_min: 600,
      day_end_min: 1440,
      interests: ["museum", "food"],
      budget_usd: 40.0,
      anchors: [
        {
          id: "sf-show",
          title: "Live show at the Civic Center",
          location: { lat: 37.7793, lon: -122.4193 },
          start_min: 1200,
          end_min: 1350,
          notes: "Doors 20:00, ends 22:30. Ticket already bought.",
        },
      ],
    },
  },
];
