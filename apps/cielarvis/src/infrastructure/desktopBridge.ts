import { invoke } from "@tauri-apps/api/core";
import { listen, type UnlistenFn } from "@tauri-apps/api/event";
import type {
  BootstrapPlan,
  ChannelPage,
  RuntimeConnection,
  RuntimeSnapshot,
  TerminalOutput,
  TerminalSessionInfo,
  TerminalSpawnRequest,
} from "../core/contracts";
import type {
  BrowserBounds,
  BrowserController,
  BrowserKeyboardInput,
  BrowserPointerInput,
  BrowserScreenshot,
  BrowserSnapshot,
  BrowserTab,
} from "../core/browser";

export const isDesktop = () => "__TAURI_INTERNALS__" in window;

function authorization(token: string): HeadersInit {
  return token.trim() ? { authorization: `Bearer ${token.trim()}` } : {};
}

async function webJson<T>(url: string, init?: RequestInit): Promise<T> {
  const response = await fetch(url, init);
  const text = await response.text();
  const body = text ? JSON.parse(text) : {};
  if (!response.ok) throw new Error(body.error ?? text ?? `HTTP ${response.status}`);
  return body as T;
}

export async function discoverRuntime(connection: RuntimeConnection): Promise<RuntimeSnapshot> {
  if (isDesktop()) return invoke("runtime_discover", { connection });
  try {
    const base = connection.endpoint.replace(/\/$/, "");
    const headers = { accept: "application/json", ...authorization(connection.token) };
    const [channel, runtime, tui, speech, speechConfig] = await Promise.all([
      webJson<Record<string, unknown>>(`${base}/ca/channel/health`, { headers }),
      webJson<{ active_client_count?: number; active_client_pids?: number[] }>(`${base}/health`, { headers }),
      webJson<{ active_count?: number; active?: unknown[] }>(`${base}/ca/tui/status`, { headers }),
      webJson<Record<string, unknown>>(`${base}/ca/speech/health`, { headers }),
      webJson<Record<string, unknown>>(`${base}/ca/speech/config`, { headers }),
    ]);
    return { connected: true, endpoint: base, channel, runtime, tui, speech, speech_config: speechConfig } as RuntimeSnapshot;
  } catch (error) {
    return { connected: false, endpoint: connection.endpoint, error: String(error) };
  }
}

export async function waitForMessages(
  connection: RuntimeConnection,
  after: number,
  channel: string,
): Promise<ChannelPage> {
  if (isDesktop()) return invoke("runtime_wait_messages", { connection, after, channel });
  const base = connection.endpoint.replace(/\/$/, "");
  const query = new URLSearchParams({ after: String(after), channel, recipient: "web", timeout: "20" });
  return webJson(`${base}/ca/channel/wait?${query}`, {
    headers: { accept: "application/json", ...authorization(connection.token) },
  });
}

export async function sendMessage(
  connection: RuntimeConnection,
  message: string,
  channel: string,
  sessionId: string,
  options: {
    inputMode?: "text" | "voice";
    internal?: boolean;
    displayText?: string;
    delivery?: string[];
    replyToken?: string;
  } = {},
): Promise<ChannelPage | Record<string, unknown>> {
  const payload = {
    channel,
    sender_id: "cielarvis-user",
    recipients: ["all"],
    delivery: options.delivery ?? ["llm", "native"],
    thread_id: sessionId,
    kind: "web_chat",
    message,
    meta: {
      source: "cielarvis-desktop",
      source_kind: "web_chat",
      web_chat_session: sessionId,
      input_mode: options.inputMode ?? "text",
      cielarvis_ui_visibility: options.internal ? "internal" : "conversation",
      cielarvis_display_text: options.displayText ?? message,
      reply_channel: channel,
      reply_recipient: "web",
      ...(options.replyToken ? { web_reply_token: options.replyToken } : {}),
      ...(options.internal ? {
        reply_instruction: "Reply to reply_channel with status and the next step.",
      } : {
        response_contract: { version: 1, fields: ["spoken", "overview", "details"], tts_field: "spoken" },
        reply_instruction: "Acknowledge briefly, then reply through the Ciel channel with spoken, overview, and optional details fields.",
      }),
    },
  };
  if (isDesktop()) return invoke("runtime_send_message", { connection, payload });
  const base = connection.endpoint.replace(/\/$/, "");
  return webJson(`${base}/ca/channel/messages`, {
    method: "POST",
    headers: { "content-type": "application/json", accept: "application/json", ...authorization(connection.token) },
    body: JSON.stringify(payload),
  });
}

async function blobBase64(blob: Blob): Promise<string> {
  const bytes = new Uint8Array(await blob.arrayBuffer());
  let binary = "";
  const step = 0x8000;
  for (let offset = 0; offset < bytes.length; offset += step) {
    binary += String.fromCharCode(...bytes.subarray(offset, offset + step));
  }
  return btoa(binary);
}

export async function transcribeAudio(
  connection: RuntimeConnection,
  audio: Blob,
  options: { model?: string; language?: string } = {},
): Promise<string> {
  const payload = await desktopOnly<{ text?: string; transcript?: string }>("runtime_transcribe_audio", {
    connection,
    audioBase64: await blobBase64(audio),
    contentType: audio.type || "audio/webm",
    model: options.model,
    language: options.language,
  });
  const text = String(payload.text || payload.transcript || "").trim();
  if (!text) throw new Error("ASR returned no transcript");
  return text;
}

export async function loadBootstrapPlan(connection: RuntimeConnection): Promise<BootstrapPlan> {
  if (!isDesktop()) throw new Error("Process supervision is available only in the desktop shell.");
  const browserMcp = await browserMcpStatus().catch(() => undefined);
  const plan = await invoke<BootstrapPlan>("bootstrap_plan", { connection, browserMcp });
  await invoke("browser_mcp_configure_runtime", { endpoint: plan.endpoint, token: connection.token });
  return plan;
}

export async function spawnTerminal(request: TerminalSpawnRequest): Promise<TerminalSessionInfo> {
  return invoke("terminal_spawn", { request });
}

export async function listTerminalSessions(): Promise<TerminalSessionInfo[]> {
  if (!isDesktop()) return [];
  return invoke("terminal_list");
}

export async function writeTerminal(id: string, data: string): Promise<void> {
  return invoke("terminal_write", { id, data });
}

export async function resizeTerminal(id: string, cols: number, rows: number): Promise<void> {
  return invoke("terminal_resize", { id, cols, rows });
}

export async function killTerminal(id: string): Promise<void> {
  return invoke("terminal_kill", { id });
}

export async function onTerminalOutput(handler: (output: TerminalOutput) => void): Promise<UnlistenFn> {
  return listen<TerminalOutput>("cielarvis://terminal-output", (event) => handler(event.payload));
}

async function desktopOnly<T>(command: string, args: Record<string, unknown> = {}): Promise<T> {
  if (!isDesktop()) throw new Error("The isolated browser engine is available only in a native CIELARVIS shell.");
  return invoke<T>(command, args);
}

export const nativeBrowserController: BrowserController = {
  createTab: (url?: string) => desktopOnly<BrowserTab>("browser_create_tab", { url }),
  listTabs: () => desktopOnly<BrowserTab[]>("browser_list_tabs"),
  closeTab: (tabId: string) => desktopOnly<void>("browser_close_tab", { tabId }),
  activateTab: (tabId: string) => desktopOnly<BrowserTab>("browser_activate_tab", { tabId }),
  navigate: (tabId: string, url: string) => desktopOnly<BrowserTab>("browser_navigate", { tabId, url }),
  back: (tabId: string) => desktopOnly("browser_back", { tabId }),
  forward: (tabId: string) => desktopOnly("browser_forward", { tabId }),
  reload: (tabId: string) => desktopOnly<void>("browser_reload", { tabId }),
  snapshot: (tabId: string) => desktopOnly<BrowserSnapshot>("browser_snapshot", { tabId }),
  evaluate: (tabId: string, script: string) => desktopOnly("browser_evaluate", { tabId, script }),
  screenshot: (tabId: string) => desktopOnly<BrowserScreenshot>("browser_screenshot", { tabId }),
  pointer: (tabId: string, input: BrowserPointerInput) => desktopOnly("browser_pointer", { tabId, input }),
  keyboard: (tabId: string, input: BrowserKeyboardInput) => desktopOnly("browser_keyboard", { tabId, input }),
  setBounds: (tabId: string, bounds: BrowserBounds) => desktopOnly<void>("browser_set_bounds", { tabId, bounds }),
  setVisible: (tabId: string, visible: boolean) => desktopOnly<void>("browser_set_visible", { tabId, visible }),
};

export async function onBrowserEvent(handler: (tab: BrowserTab) => void): Promise<UnlistenFn> {
  return listen<{ kind: string; tab: BrowserTab }>("cielarvis://browser-event", (event) => handler(event.payload.tab));
}

export type BrowserMcpStatus = {
  ready: boolean;
  endpoint?: string;
  authorization?: string;
  error?: string;
};

export async function browserMcpStatus(): Promise<BrowserMcpStatus> {
  return desktopOnly<BrowserMcpStatus>("browser_mcp_status");
}
