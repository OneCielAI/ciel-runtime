import { describe, expect, it } from "vitest";
import { voiceNeedsSetup } from "./contracts";

describe("voiceNeedsSetup", () => {
  it("waits until the Runtime is connected", () => {
    expect(voiceNeedsSetup({ connected: false, endpoint: "http://127.0.0.1:6969" })).toBe(false);
  });

  it("requires both reachable speech workers", () => {
    expect(voiceNeedsSetup({
      connected: true,
      endpoint: "http://127.0.0.1:6969",
      speech: { services: { asr: { enabled: true, reachable: true }, tts: { enabled: true, reachable: false } } },
    })).toBe(true);
    expect(voiceNeedsSetup({
      connected: true,
      endpoint: "http://127.0.0.1:6969",
      speech: { services: { asr: { enabled: true, reachable: true }, tts: { enabled: true, reachable: true } } },
    })).toBe(false);
  });
});
