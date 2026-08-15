import { FormEvent, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { AgentStage } from "./components/AgentStage";
import { TerminalDeck } from "./components/TerminalDeck";
import type {
  BootstrapPlan,
  ChannelMessage,
  RuntimeConnection,
  RuntimeSnapshot,
  TerminalSessionInfo,
  TerminalSpawnRequest,
} from "./core/contracts";
import { voiceNeedsSetup } from "./core/contracts";
import {
  discoverRuntime,
  isDesktop,
  loadBootstrapPlan,
  sendMessage,
  spawnTerminal,
  waitForMessages,
} from "./infrastructure/desktopBridge";

const savedConnection = (): RuntimeConnection => {
  try {
    const saved = JSON.parse(localStorage.getItem("cielavis.connection") ?? "{}");
    return {
      endpoint: String(saved.endpoint || "http://127.0.0.1:6969"),
      token: "",
      workspace: String(saved.workspace || ""),
    };
  } catch {
    return { endpoint: "http://127.0.0.1:6969", token: "", workspace: "" };
  }
};

const sessionId = crypto.randomUUID();
const channel = `cielavis-${sessionId}`;

function messageText(message: ChannelMessage): string {
  const structured = message.meta?.response;
  if (structured && typeof structured === "object") {
    const response = structured as Record<string, unknown>;
    return [response.spoken, response.overview, response.details].filter((value) => typeof value === "string").join("\n\n");
  }
  return String(message.message ?? "");
}

export function App() {
  const [connection, setConnection] = useState<RuntimeConnection>(savedConnection);
  const [snapshot, setSnapshot] = useState<RuntimeSnapshot | null>(null);
  const [sessions, setSessions] = useState<TerminalSessionInfo[]>([]);
  const [activeSession, setActiveSession] = useState("");
  const [plan, setPlan] = useState<BootstrapPlan | null>(null);
  const [messages, setMessages] = useState<ChannelMessage[]>([]);
  const [draft, setDraft] = useState("");
  const [notice, setNotice] = useState("Initializing native bridge…");
  const [sending, setSending] = useState(false);
  const [showConnection, setShowConnection] = useState(false);
  const runtimeStarted = useRef(false);
  const voiceStatusOpened = useRef(false);
  const lastId = useRef(0);

  const spawn = useCallback(async (request: TerminalSpawnRequest) => {
    const session = await spawnTerminal(request);
    setSessions((current) => [...current.filter((item) => item.id !== session.id), session]);
    setActiveSession(session.id);
    return session;
  }, []);

  const refresh = useCallback(async () => {
    const next = await discoverRuntime(connection);
    setSnapshot(next);
    setNotice(next.connected ? "Runtime channel online" : next.error || "Runtime is offline");
    return next;
  }, [connection]);

  const ensureRuntime = useCallback(async () => {
    const next = await refresh();
    if (next.connected || !isDesktop() || runtimeStarted.current) return next;
    try {
      const bootstrap = await loadBootstrapPlan(connection);
      setPlan(bootstrap);
      if (bootstrap.endpoint !== connection.endpoint) {
        setConnection({ ...connection, endpoint: bootstrap.endpoint });
        setNotice(`Resolved the isolated Runtime endpoint: ${bootstrap.endpoint}`);
        return next;
      }
      runtimeStarted.current = true;
      await spawn(bootstrap.runtime);
      setNotice("Runtime was not reachable. A supervised setup session is open above.");
    } catch (error) {
      runtimeStarted.current = false;
      setNotice(`Runtime bootstrap failed: ${String(error)}`);
    }
    return next;
  }, [connection, refresh, spawn]);

  useEffect(() => {
    localStorage.setItem("cielavis.connection", JSON.stringify({
      endpoint: connection.endpoint,
      workspace: connection.workspace,
    }));
  }, [connection.endpoint, connection.workspace]);

  useEffect(() => {
    let cancelled = false;
    void ensureRuntime();
    const timer = window.setInterval(() => void refresh(), 3500);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [ensureRuntime, refresh]);

  useEffect(() => {
    if (!snapshot?.connected || !isDesktop()) return;
    if (!plan) void loadBootstrapPlan(connection).then(setPlan).catch(() => undefined);
    if (!voiceNeedsSetup(snapshot) || voiceStatusOpened.current) return;
    voiceStatusOpened.current = true;
    const openStatus = async () => {
      const bootstrap = plan ?? (await loadBootstrapPlan(connection));
      setPlan(bootstrap);
      if (bootstrap.speech_status) await spawn(bootstrap.speech_status);
      setNotice("Speech services need attention. Voice status opened in a separate session.");
    };
    void openStatus().catch((error) => setNotice(`Voice status failed: ${String(error)}`));
  }, [snapshot, plan, connection, spawn]);

  useEffect(() => {
    if (!snapshot?.connected) return;
    let cancelled = false;
    async function poll() {
      while (!cancelled) {
        try {
          const page = await waitForMessages(connection, lastId.current, channel);
          if (cancelled) break;
          if (page.messages.length) {
            lastId.current = page.last_id;
            setMessages((current) => {
              const known = new Set(current.map((message) => message.id));
              return [...current, ...page.messages.filter((message) => !known.has(message.id))];
            });
          }
        } catch (error) {
          if (!cancelled) setNotice(`Channel reconnecting: ${String(error)}`);
          await new Promise((resolve) => window.setTimeout(resolve, 1200));
        }
      }
    }
    void poll();
    return () => {
      cancelled = true;
    };
  }, [snapshot?.connected, connection]);

  async function submit(event: FormEvent) {
    event.preventDefault();
    const text = draft.trim();
    if (!text || !snapshot?.connected || sending) return;
    setDraft("");
    setSending(true);
    try {
      await sendMessage(connection, text, channel, sessionId);
      setNotice("Message delivered to the active Ciel agent.");
    } catch (error) {
      setNotice(`Message failed: ${String(error)}`);
    } finally {
      setSending(false);
    }
  }

  async function openSpeech(kind: "status" | "login" | "deploy") {
    const bootstrap = plan ?? (await loadBootstrapPlan(connection));
    setPlan(bootstrap);
    const request = bootstrap[`speech_${kind}`];
    if (!request) throw new Error("The installed Runtime does not expose the Colab speech installer.");
    await spawn(request);
  }

  const voicePending = voiceNeedsSetup(snapshot);
  const speechServices = snapshot?.speech?.services ?? {};
  const agentReplies = useMemo(
    () => messages.filter((message) => message.sender_id !== "cielavis-user"),
    [messages],
  );

  return (
    <main className="app-shell">
      <header className="app-header">
        <div className="brand">
          <span className="brand-mark">C</span>
          <div><strong>CIELAVIS</strong><small>VISUAL AGENT CONSOLE</small></div>
        </div>
        <div className="runtime-pill" data-online={snapshot?.connected || false}>
          <i />
          <span>{snapshot?.connected ? "RUNTIME ONLINE" : "RUNTIME OFFLINE"}</span>
          <code>{connection.endpoint}</code>
        </div>
        <button className="icon-button" onClick={() => setShowConnection((value) => !value)}>CONNECTION</button>
      </header>

      {showConnection && (
        <section className="connection-panel">
          <label>Runtime endpoint<input value={connection.endpoint} onChange={(event) => setConnection({ ...connection, endpoint: event.target.value })} /></label>
          <label>Workspace<input placeholder="Defaults to your Windows profile" value={connection.workspace} onChange={(event) => setConnection({ ...connection, workspace: event.target.value })} /></label>
          <label>Bearer token<input type="password" value={connection.token} onChange={(event) => setConnection({ ...connection, token: event.target.value })} /></label>
          <button onClick={() => { runtimeStarted.current = false; void ensureRuntime(); }}>RECONNECT</button>
        </section>
      )}

      <TerminalDeck
        sessions={sessions}
        activeId={activeSession}
        onActivate={setActiveSession}
        onClosed={(id) => {
          const remaining = sessions.filter((session) => session.id !== id);
          setSessions(remaining);
          if (activeSession === id) setActiveSession(remaining.at(-1)?.id ?? "");
        }}
      />

      <section className="workspace">
        <aside className="activity-rail">
          <span className="eyebrow">LIVE THREAD</span>
          <h2>Channel activity</h2>
          <div className="message-stack">
            {agentReplies.length ? agentReplies.slice(-8).map((message) => (
              <article key={message.id}>
                <small>{message.sender_id || "agent"}</small>
                <p>{messageText(message)}</p>
              </article>
            )) : <p className="empty-copy">Agent replies will materialize here.</p>}
          </div>
        </aside>

        <AgentStage snapshot={snapshot} listening={sending} />

        <aside className="systems-rail">
          <span className="eyebrow">SYSTEMS</span>
          <h2>Capability matrix</h2>
          <div className="service-card" data-ok={snapshot?.connected || false}><i /><div><strong>Channel bridge</strong><small>{snapshot?.connected ? "synchronized" : "unreachable"}</small></div></div>
          {(["asr", "tts"] as const).map((name) => {
            const probe = speechServices[name];
            const okay = Boolean(probe?.enabled && probe?.reachable);
            return <div className="service-card" data-ok={okay} key={name}><i /><div><strong>{name.toUpperCase()}</strong><small>{okay ? "ready" : probe?.error || "setup required"}</small></div></div>;
          })}
          {voicePending && isDesktop() && (
            <div className="voice-actions">
              <p>Speech workers are missing or disconnected. Use an isolated setup session.</p>
              <button onClick={() => void openSpeech("status")}>STATUS</button>
              <button onClick={() => void openSpeech("login")}>LOGIN</button>
              <button className="primary" onClick={() => void openSpeech("deploy")}>DEPLOY</button>
            </div>
          )}
        </aside>
      </section>

      <footer className="command-dock">
        <div className="notice"><i /><span>{notice}</span></div>
        <form onSubmit={submit}>
          <textarea value={draft} onChange={(event) => setDraft(event.target.value)} placeholder="Direct the active Ciel agent…" rows={2} />
          <button disabled={!snapshot?.connected || sending || !draft.trim()}>{sending ? "SENDING" : "TRANSMIT"}</button>
        </form>
      </footer>
    </main>
  );
}
