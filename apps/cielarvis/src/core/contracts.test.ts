import { describe, expect, it } from "vitest";
import { runtimeAgentReady, voiceNeedsSetup } from "./contracts";

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

describe("runtimeAgentReady", () => {
  it("does not treat a router-only channel as a running agent", () => {
    expect(runtimeAgentReady({ connected: true, endpoint: "http://127.0.0.1:6969", runtime: { active_client_count: 0 }, tui: { active_count: 0 } })).toBe(false);
  });

  it("becomes ready when the router reports a registered CLI runtime", () => {
    expect(runtimeAgentReady({ connected: true, endpoint: "http://127.0.0.1:6969", runtime: { active_client_count: 1 }, tui: { active_count: 0 } })).toBe(true);
  });
});
