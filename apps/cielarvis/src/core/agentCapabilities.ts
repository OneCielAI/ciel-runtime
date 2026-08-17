export const CIELARVIS_CAPABILITY_REQUEST = "ai.oneciel.cielarvis.capability-request/v1" as const;

export type AgentCapability = "voice.input" | "browser.research";

export function capabilityRequestPrompt(capability: AgentCapability, details: Record<string, unknown>): string {
  if (capability === "voice.input") {
    return "[CIELARVIS voice recovery] Voice is unavailable. Check /ca/speech/health, then guide or run Google Colab ASR/TTS CLI setup: authenticate, deploy, connect, verify. Prefer the supervised setup terminal. Ask before Google, Tailscale, or Vault authorization. Report status and next step.";
  }
  return [
    `[CIELARVIS capability request: ${CIELARVIS_CAPABILITY_REQUEST}]`,
    `Capability: ${capability}`,
    `Observed state: ${JSON.stringify(details)}`,
    "First inspect the skills and MCP tools available in this session and use the most specific relevant tool.",
    "If the capability is unavailable, diagnose it and assist the user with installation or recovery.",
    "Never read or use a saved credential without explaining the target, purpose, and requested scope and receiving explicit user approval.",
    "Prefer completing safe local steps yourself; clearly identify any step that still requires the user.",
  ].join("\n");
}

export function requestsVisibleBrowser(text: string): boolean {
  const normalized = text.trim().toLocaleLowerCase();
  if (!normalized) return false;
  const browser = /\b(browser|browse|website|web page|google)\b|브라우저|웹사이트|웹 페이지/.test(normalized);
  const research = /\b(search|research|look up|find online|navigate|open)\b|검색|조사|찾아|접속|열어/.test(normalized);
  return browser || (/\b(web|online|internet)\b|인터넷|웹/.test(normalized) && research);
}

export function capabilityMessagePresentation(message: {
  message?: string;
  meta?: Record<string, unknown>;
}): { internal: boolean; text: string } {
  const raw = String(message.message ?? "");
  const explicit = message.meta?.cielarvis_display_text;
  const visibility = message.meta?.cielarvis_ui_visibility;
  if (visibility === "internal") {
    return {
      internal: true,
      text: typeof explicit === "string" && explicit.trim()
        ? explicit.trim()
        : "CIELARVIS is preparing the requested capability.",
    };
  }
  if (raw.startsWith("[CIELARVIS capability request:") || raw.startsWith("[CIELARVIS voice recovery]")) {
    const capability = raw.match(/^Capability:\s*(.+)$/m)?.[1]?.trim();
    const voice = capability === "voice.input" || raw.startsWith("[CIELARVIS voice recovery]") || raw.includes("Voice input is unavailable.");
    return {
      internal: true,
      text: voice
        ? "Voice setup requested — the active agent is checking ASR and microphone availability."
        : "CIELARVIS capability assistance requested.",
    };
  }
  return { internal: false, text: raw };
}
