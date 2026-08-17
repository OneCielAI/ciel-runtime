import { describe, expect, it } from "vitest";
import { capabilityMessagePresentation, capabilityRequestPrompt, requestsVisibleBrowser } from "./agentCapabilities";

describe("CIELARVIS agent capability requests", () => {
  it("requires tool discovery and explicit credential consent", () => {
    const prompt = capabilityRequestPrompt("browser.research", { browser: "offline" });
    expect(prompt).toContain("inspect the skills and MCP tools");
    expect(prompt).toContain("explicit user approval");
  });

  it("routes missing voice services to the Ciel Runtime Colab installer", () => {
    const prompt = capabilityRequestPrompt("voice.input", { asr: { enabled: false }, tts: { reachable: false } });
    expect(prompt).toContain("Google Colab ASR/TTS CLI setup");
    expect(prompt).toContain("Prefer the supervised setup terminal");
    expect(prompt).toContain("Tailscale");
    expect(prompt.length).toBeLessThan(500);
  });

  it("recognizes explicit browser research requests without opening for ordinary chat", () => {
    expect(requestsVisibleBrowser("인터넷에서 최신 정보를 검색해줘")).toBe(true);
    expect(requestsVisibleBrowser("Open the browser and research this company")).toBe(true);
    expect(requestsVisibleBrowser("이 코드의 버그를 고쳐줘")).toBe(false);
  });
});

describe("capabilityMessagePresentation", () => {
  it("collapses structured internal prompts to their display text", () => {
    expect(capabilityMessagePresentation({
      message: "private control prompt",
      meta: { cielarvis_ui_visibility: "internal", cielarvis_display_text: "Voice setup requested" },
    })).toEqual({ internal: true, text: "Voice setup requested" });
  });

  it("collapses capability prompts sent by an older desktop build", () => {
    const message = capabilityRequestPrompt("voice.input", {});
    expect(capabilityMessagePresentation({ message })).toEqual({
      internal: true,
      text: "Voice setup requested — the active agent is checking ASR and microphone availability.",
    });
  });
});
