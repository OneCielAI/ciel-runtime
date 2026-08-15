import { FormEvent, useCallback, useEffect, useMemo, useRef, useState, useSyncExternalStore } from "react";
import { CHAT_APP_ID, BUILTIN_APPS, RUNTIME_APP_ID } from "./apps/builtinApps";
import { CielChatApp } from "./apps/CielChatApp";
import { AgentStage } from "./components/AgentStage";
import { DesktopTaskbar } from "./components/DesktopTaskbar";
import { DesktopWindow } from "./components/DesktopWindow";
import { JavascriptAppHost } from "./components/JavascriptAppHost";
import { TerminalDeck } from "./components/TerminalDeck";
import type {
  BootstrapPlan,
  ChannelMessage,
  RuntimeConnection,
  RuntimeSnapshot,
  TerminalSessionInfo,
  TerminalSpawnRequest,
} from "./core/contracts";
import { runtimeAgentReady, voiceNeedsSetup } from "./core/contracts";
import { CielDesktopKernel, type DesktopViewport } from "./core/desktopKernel";
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
    const saved = JSON.parse(localStorage.getItem("cielarvis.connection") ?? "{}");
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
const channel = `cielarvis-${sessionId}`;

export function App() {
  const kernel = useMemo(() => new CielDesktopKernel(BUILTIN_APPS), []);
  const desktop = useSyncExternalStore(kernel.subscribe, kernel.getSnapshot);
  const surfaceRef = useRef<HTMLElement | null>(null);
  const [viewport, setViewport] = useState<DesktopViewport>({ width: window.innerWidth, height: Math.max(400, window.innerHeight - 58) });
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
  const defaultsOpened = useRef(false);
  const previousRuntimeOnline = useRef(false);
  const lastId = useRef(0);

  useEffect(() => {
    const node = surfaceRef.current;
    if (!node) return;
    const measure = () => setViewport({ width: node.clientWidth, height: node.clientHeight });
    measure();
    const observer = new ResizeObserver(measure);
    observer.observe(node);
    return () => observer.disconnect();
  }, []);

  useEffect(() => {
    if (defaultsOpened.current || viewport.width < 1 || viewport.height < 1) return;
    defaultsOpened.current = true;
    for (const app of desktop.apps) {
      if (app.windows.some((surface) => surface.defaultOpen)) kernel.openApp(app.id, viewport);
    }
    kernel.restoreApp(RUNTIME_APP_ID, viewport);
  }, [desktop.apps, kernel, viewport]);

  const spawn = useCallback(async (request: TerminalSpawnRequest) => {
    const session = await spawnTerminal(request);
    setSessions((current) => [...current.filter((item) => item.id !== session.id), session]);
    setActiveSession(session.id);
    return session;
  }, []);

  const refresh = useCallback(async () => {
    const next = await discoverRuntime(connection);
    setSnapshot(next);
    setNotice(
      runtimeAgentReady(next)
        ? "Runtime agent online"
        : next.connected
          ? "Router online; waiting for an agent to launch"
          : next.error || "Runtime is offline",
    );
    return next;
  }, [connection]);

  const ensureRuntime = useCallback(async () => {
    const next = await refresh();
    if (next.connected || !isDesktop() || runtimeStarted.current) return next;
    // Multiple discovery effects can complete in the same frame during boot.
    // Claim the launch synchronously before loading the asynchronous plan so
    // only one supervised PTY can be created for this desktop instance.
    runtimeStarted.current = true;
    try {
      const bootstrap = await loadBootstrapPlan(connection);
      setPlan(bootstrap);
      if (bootstrap.endpoint !== connection.endpoint) {
        setConnection({ ...connection, endpoint: bootstrap.endpoint });
        setNotice(`Resolved the isolated Runtime endpoint: ${bootstrap.endpoint}`);
        runtimeStarted.current = false;
        return next;
      }
      await spawn(bootstrap.runtime);
      kernel.restoreApp(RUNTIME_APP_ID, viewport);
      setNotice("Runtime was not reachable. The supervised Runtime app is booting.");
    } catch (error) {
      runtimeStarted.current = false;
      setNotice(`Runtime bootstrap failed: ${String(error)}`);
    }
    return next;
  }, [connection, kernel, refresh, spawn, viewport]);

  useEffect(() => {
    localStorage.setItem("cielarvis.connection", JSON.stringify({
      endpoint: connection.endpoint,
      workspace: connection.workspace,
    }));
  }, [connection.endpoint, connection.workspace]);

  useEffect(() => {
    void ensureRuntime();
    const timer = window.setInterval(() => void refresh(), 3500);
    return () => window.clearInterval(timer);
  }, [ensureRuntime, refresh]);

  const runtimeOnline = runtimeAgentReady(snapshot);
  const routerOnline = Boolean(snapshot?.connected);

  useEffect(() => {
    if (runtimeOnline && !previousRuntimeOnline.current) kernel.minimizeApp(RUNTIME_APP_ID);
    if (!runtimeOnline && previousRuntimeOnline.current) kernel.restoreApp(RUNTIME_APP_ID, viewport);
    previousRuntimeOnline.current = runtimeOnline;
  }, [kernel, runtimeOnline, viewport]);

  useEffect(() => {
    if (!runtimeOnline || !isDesktop()) return;
    if (!plan) void loadBootstrapPlan(connection).then(setPlan).catch(() => undefined);
    if (!voiceNeedsSetup(snapshot) || voiceStatusOpened.current) return;
    voiceStatusOpened.current = true;
    const openStatus = async () => {
      const bootstrap = plan ?? (await loadBootstrapPlan(connection));
      setPlan(bootstrap);
      if (bootstrap.speech_status) await spawn(bootstrap.speech_status);
      kernel.restoreApp(RUNTIME_APP_ID, viewport);
      setNotice("Speech services need attention. Voice status opened in the Runtime app.");
    };
    void openStatus().catch((error) => setNotice(`Voice status failed: ${String(error)}`));
  }, [snapshot, plan, connection, kernel, runtimeOnline, spawn, viewport]);

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
    return () => { cancelled = true; };
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
    kernel.restoreApp(RUNTIME_APP_ID, viewport);
  }

  const terminalDeck = (
    <TerminalDeck
      sessions={sessions}
      activeId={activeSession}
      variant="default"
      onActivate={setActiveSession}
      onClosed={(id) => {
        const closed = sessions.find((session) => session.id === id);
        const remaining = sessions.filter((session) => session.id !== id);
        setSessions(remaining);
        if (closed?.kind === "runtime") runtimeStarted.current = false;
        if (activeSession === id) setActiveSession(remaining.at(-1)?.id ?? "");
      }}
    />
  );
  const speechServices = snapshot?.speech?.services ?? {};
  const voicePending = voiceNeedsSetup(snapshot);

  return (
    <main className="agentic-shell">
      <header className="app-header">
        <div className="brand">
          <span className="brand-mark">C</span>
          <div><strong>CIELARVIS</strong><small>AGENTIC DESKTOP · SDK v1</small></div>
        </div>
        <div className="runtime-pill" data-online={runtimeOnline}>
          <i />
          <span>{runtimeOnline ? "RUNTIME ONLINE" : routerOnline ? "ROUTER ONLINE" : "RUNTIME OFFLINE"}</span>
          <code>{connection.endpoint}</code>
        </div>
        <button className="icon-button" onClick={() => setShowConnection((value) => !value)}>SYSTEM</button>
      </header>

      {showConnection && (
        <section className="connection-panel">
          <label>Runtime endpoint<input value={connection.endpoint} onChange={(event) => setConnection({ ...connection, endpoint: event.target.value })} /></label>
          <label>Workspace<input placeholder="Defaults to your Windows profile" value={connection.workspace} onChange={(event) => setConnection({ ...connection, workspace: event.target.value })} /></label>
          <label>Bearer token<input type="password" value={connection.token} onChange={(event) => setConnection({ ...connection, token: event.target.value })} /></label>
          <button onClick={() => { runtimeStarted.current = false; kernel.restoreApp(RUNTIME_APP_ID, viewport); void ensureRuntime(); }}>RECONNECT</button>
        </section>
      )}

      <section className="desktop-surface" ref={surfaceRef}>
        <div className="agent-desktop-background"><AgentStage snapshot={snapshot} listening={sending} /></div>
        <div className="desktop-status-hud">
          <span className="eyebrow">CIEL SYSTEM</span>
          <strong>{runtimeOnline ? "Agent synchronized" : "Runtime booting"}</strong>
          <small>{notice}</small>
          <div className="hud-services">
            {(["asr", "tts"] as const).map((name) => {
              const probe = speechServices[name];
              const okay = Boolean(probe?.enabled && probe?.reachable);
              return <button key={name} data-ok={okay} onClick={() => void openSpeech("status")}><i />{name.toUpperCase()}</button>;
            })}
            {voicePending && isDesktop() && <button onClick={() => void openSpeech("deploy")}>VOICE SETUP</button>}
          </div>
        </div>

        {desktop.windows.map((instance) => {
          const app = desktop.apps.find((candidate) => candidate.id === instance.appId);
          if (!app) return null;
          const definition = kernel.definition(instance);
          let content = <div className="unsupported-app">No renderer is registered for {app.host.kind}.</div>;
          if (app.id === RUNTIME_APP_ID) {
            content = (
              <section className="runtime-window-app" data-online={runtimeOnline}>
                <div className="runtime-app-status"><i /><span>{runtimeOnline ? "Agent active — terminal remains attached" : "Launching the last workspace Runtime…"}</span><code>{connection.endpoint}</code></div>
                {terminalDeck}
              </section>
            );
          } else if (app.id === CHAT_APP_ID) {
            content = <CielChatApp messages={messages} endpoint={connection.endpoint} draft={draft} online={runtimeOnline} sending={sending} notice={notice} onDraft={setDraft} onSubmit={submit} />;
          } else if (app.host.kind === "javascript") {
            content = <JavascriptAppHost app={app} />;
          }
          return (
            <DesktopWindow
              key={instance.id}
              app={app}
              definition={definition}
              instance={instance}
              active={desktop.activeWindowId === instance.id}
              viewport={viewport}
              onFocus={() => kernel.focus(instance.id)}
              onBounds={(bounds) => kernel.updateBounds(instance.id, bounds, viewport)}
              onMinimize={() => kernel.minimize(instance.id)}
              onMaximize={() => kernel.toggleMaximize(instance.id, viewport)}
              onClose={() => kernel.close(instance.id)}
            >
              {content}
            </DesktopWindow>
          );
        })}

        <DesktopTaskbar apps={desktop.apps} windows={desktop.windows} activeWindowId={desktop.activeWindowId} onActivate={(appId) => kernel.activateApp(appId, viewport)} />
      </section>
    </main>
  );
}
