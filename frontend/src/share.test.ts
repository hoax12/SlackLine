import { describe, expect, it } from "vitest";

import { decodeShare, encodeShare } from "./share";

describe("fragment share codec", () => {
  it("round-trips a plan request losslessly", async () => {
    const request = {
      persona: "persona_a",
      service_date: "2026-08-12",
      origin: { lat: 37.7785, lon: -122.3985 },
      origin_label: "SoMa hotel — “fancy” & <weird> chars / ünïcode",
      day_start_min: 540,
      day_end_min: 1425,
      interests: ["museum", "park"],
      budget_usd: 85.0,
      anchors: [
        {
          id: "dinner-meetup",
          title: "Dinner meetup",
          location: { lat: 37.4429, lon: -122.1614 },
          start_min: 1140,
          end_min: 1260,
        },
      ],
    };
    const encoded = await encodeShare(request);
    expect(await decodeShare(encoded)).toEqual(request);
  });

  it("produces URL-fragment-safe output", async () => {
    const encoded = await encodeShare({ text: "a".repeat(2000) });
    expect(encoded).toMatch(/^[A-Za-z0-9_-]+$/);
  });

  it("compresses repetitive payloads", async () => {
    const value = { list: new Array(200).fill("repetitive content") };
    const encoded = await encodeShare(value);
    expect(encoded.length).toBeLessThan(JSON.stringify(value).length / 5);
  });

  it("rejects corrupted fragments", async () => {
    const encoded = await encodeShare({ ok: true });
    const corrupted = encoded.slice(0, -6) + "XXXXXX";
    await expect(decodeShare(corrupted)).rejects.toThrow();
  });
});
