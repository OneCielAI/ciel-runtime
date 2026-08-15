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
    const [channel, tui, speech, speechConfig] = await Promise.all([
      webJson<Record<string, unknown>>(`${base}/ca/channel/health`, { headers }),
      webJson<{ active_count?: number; active?: unknown[] }>(`${base}/ca/tui/status`, { headers }),
      webJson<Record<string, unknown>>(`${base}/ca/speech/health`, { headers }),
      webJson<Record<string, unknown>>(`${base}/ca/speech/config`, { headers }),
    ]);
    return { connected: true, endpoint: base, channel, tui, speech, speech_config: speechConfig } as RuntimeSnapshot;
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
  const query = new URLSearchParams({ after: String(after), channel, recipient: "cielavis", timeout: "20" });
  return webJson(`${base}/ca/channel/wait?${query}`, {
    headers: { accept: "application/json", ...authorization(connection.token) },
  });
}

export async function sendMessage(
  connection: RuntimeConnection,
  message: string,
  channel: string,
  sessionId: string,
): Promise<ChannelPage | Record<string, unknown>> {
  const payload = {
    channel,
    sender_id: "cielavis-user",
    recipients: ["all"],
    delivery: ["llm", "native"],
    thread_id: sessionId,
    kind: "web_chat",
    message,
    meta: {
      source: "cielavis-desktop",
      source_kind: "web_chat",
      web_chat_session: sessionId,
      input_mode: "text",
      reply_channel: channel,
      reply_recipient: "cielavis",
      response_contract: { version: 1, fields: ["spoken", "overview", "details"], tts_field: "spoken" },
      reply_instruction: "Acknowledge briefly, then reply through the Ciel channel with spoken, overview, and optional details fields.",
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

export async function loadBootstrapPlan(connection: RuntimeConnection): Promise<BootstrapPlan> {
  if (!isDesktop()) throw new Error("Process supervision is available only in the desktop shell.");
  return invoke("bootstrap_plan", { connection });
}

export async function spawnTerminal(request: TerminalSpawnRequest): Promise<TerminalSessionInfo> {
  return invoke("terminal_spawn", { request });
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
  return listen<TerminalOutput>("cielavis://terminal-output", (event) => handler(event.payload));
}
