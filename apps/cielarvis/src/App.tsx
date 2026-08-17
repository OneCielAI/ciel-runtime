import { FormEvent, useCallback, useEffect, useMemo, useRef, useState, useSyncExternalStore } from "react";
import { BROWSER_APP_ID, CHAT_APP_ID, BUILTIN_APPS, RUNTIME_APP_ID } from "./apps/builtinApps";
import { CielBrowserApp } from "./apps/CielBrowserApp";
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
import { capabilityRequestPrompt, requestsVisibleBrowser } from "./core/agentCapabilities";
import { CielDesktopKernel, type DesktopViewport } from "./core/desktopKernel";
import {
  discoverRuntime,
  isDesktop,
  loadBootstrapPlan,
  sendMessage,
  spawnTerminal,
  transcribeAudio,
  waitForMessages,
  writeTerminal,
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
const RUNTIME_READY_SETTLE_MS = 10_000;

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
  const [runtimeFullyLoaded, setRuntimeFullyLoaded] = useState(false);
  const [voiceActive, setVoiceActive] = useState(false);
  const [voiceBusy, setVoiceBusy] = useState(false);
  const runtimeStarted = useRef(false);
  const runtimeSessionId = useRef("");
  const defaultsOpened = useRef(false);
  const previousRuntimeFullyLoaded = useRef(false);
  const lastId = useRef(0);
  const voiceRecorder = useRef<MediaRecorder | null>(null);
  const voiceStream = useRef<MediaStream | null>(null);
  const voiceChunks = useRef<Blob[]>([]);

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
    if (request.kind === "runtime") runtimeSessionId.current = session.id;
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
    // A first bootstrap can race native MCP/WebView initialization. Retry the
    // idempotent supervisor path, not only discovery, so an offline Runtime
    // cannot leave the desktop permanently stuck at the boot surface.
    const timer = window.setInterval(() => void ensureRuntime(), 3500);
    return () => window.clearInterval(timer);
  }, [ensureRuntime]);

  const runtimeOnline = runtimeAgentReady(snapshot);
  const runtimeReadyForServices = runtimeOnline && runtimeFullyLoaded;
  const routerOnline = Boolean(snapshot?.connected);

  useEffect(() => {
    setRuntimeFullyLoaded(false);
    if (!runtimeOnline) return;
    const timer = window.setTimeout(() => setRuntimeFullyLoaded(true), RUNTIME_READY_SETTLE_MS);
    return () => window.clearTimeout(timer);
  }, [connection.endpoint, runtimeOnline]);

  useEffect(() => {
    if (runtimeReadyForServices && !previousRuntimeFullyLoaded.current) kernel.minimizeApp(RUNTIME_APP_ID);
    if (!runtimeReadyForServices && previousRuntimeFullyLoaded.current) kernel.restoreApp(RUNTIME_APP_ID, viewport);
    previousRuntimeFullyLoaded.current = runtimeReadyForServices;
  }, [kernel, runtimeReadyForServices, viewport]);

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

  const deliver = useCallback(async (rawText: string, options: { internal?: boolean; displayText?: string; inputMode?: "text" | "voice" } = {}) => {
    const text = rawText.trim();
    if (!text || !snapshot?.connected || sending) return false;
    setSending(true);
    try {
      const browserRequested = !options.internal && requestsVisibleBrowser(text);
      if (browserRequested) kernel.restoreApp(BROWSER_APP_ID, viewport, { avoidAppIds: [CHAT_APP_ID], gap: 18 });
      const message = browserRequested
        ? `${text}\n\n${capabilityRequestPrompt("browser.research", { browser_window: "visible", preferred_mcp_server: "cielarvis-browser" })}`
        : text;
      const directInternal = Boolean(options.internal && runtimeSessionId.current);
      const replyToken = directInternal ? crypto.randomUUID() : undefined;
      const posted = await sendMessage(connection, message, channel, sessionId, {
        inputMode: options.inputMode,
        internal: options.internal,
        displayText: options.displayText ?? (browserRequested ? text : undefined),
        delivery: directInternal ? ["web"] : undefined,
        replyToken,
      });
      if (directInternal) {
        const record = posted as { id?: unknown; message?: { id?: unknown } };
        const parentId = String(record.message?.id ?? record.id ?? "").trim();
        if (!parentId || !replyToken) throw new Error("Runtime did not return a correlated channel message id");
        const route = JSON.stringify({ channel, thread_id: sessionId, parent_id: parentId, reply_token: replyToken });
        const prompt = `[CIELARVIS internal] Task=${JSON.stringify(message)} Route=${route}. Call ciel-runtime-router.send_message with kind=ack immediately. Inspect only; do not deploy or authorize without approval. Then call it once with kind=reply containing current status and the next required step.`;
        await writeTerminal(runtimeSessionId.current, `${prompt}\r`);
      }
      setNotice("Message delivered to the active Ciel agent.");
      return true;
    } catch (error) {
      setNotice(`Message failed: ${String(error)}`);
      return false;
    } finally {
      setSending(false);
    }
  }, [connection, kernel, sending, snapshot?.connected, viewport]);

  async function submit(event: FormEvent) {
    event.preventDefault();
    const text = draft.trim();
    if (!text) return;
    setDraft("");
    await deliver(text);
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
        if (closed?.kind === "runtime") runtimeSessionId.current = "";
        if (activeSession === id) setActiveSession(remaining.at(-1)?.id ?? "");
      }}
    />
  );
  const speechServices = snapshot?.speech?.services ?? {};
  const voicePending = voiceNeedsSetup(snapshot);
  const asrProbe = speechServices.asr;
  const voiceReady = Boolean(runtimeReadyForServices && asrProbe?.enabled && asrProbe?.reachable);

  const requestVoiceAssistance = useCallback(async (reason?: string) => {
    const message = capabilityRequestPrompt("voice.input", {
      runtime_endpoint: connection.endpoint,
      asr: snapshot?.speech?.services?.asr ?? null,
      tts: snapshot?.speech?.services?.tts ?? null,
      error: reason ?? null,
      provisioning: "ciel-runtime-colab-speech",
      requested_action: "inspect Ciel Runtime speech health, then guide or perform the Colab ASR/TTS worker installation and connection flow",
    });
    const delivered = await deliver(message, {
      internal: true,
      displayText: "Voice setup requested — the active agent is checking ASR and microphone availability.",
    });
    setNotice(delivered
      ? "The active agent is inspecting voice tools and installation options."
      : "Voice needs assistance, but no active Runtime agent is available.");
  }, [connection.endpoint, deliver, snapshot?.speech?.services]);

  const toggleVoice = useCallback(async () => {
    if (voiceBusy) return;
    if (!voiceReady) {
      await requestVoiceAssistance().catch((error) => setNotice(`Voice assistance failed: ${String(error)}`));
      return;
    }
    if (voiceRecorder.current && voiceActive) {
      setVoiceBusy(true);
      voiceRecorder.current.stop();
      return;
    }
    try {
      if (!navigator.mediaDevices?.getUserMedia || typeof MediaRecorder === "undefined") {
        throw new Error("The desktop WebView does not expose microphone recording");
      }
      const stream = await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true } });
      const recorder = new MediaRecorder(stream);
      voiceStream.current = stream;
      voiceRecorder.current = recorder;
      voiceChunks.current = [];
      recorder.ondataavailable = (event) => { if (event.data.size) voiceChunks.current.push(event.data); };
      recorder.onstop = () => {
        const recording = new Blob(voiceChunks.current, { type: recorder.mimeType || "audio/webm" });
        voiceStream.current?.getTracks().forEach((track) => track.stop());
        voiceStream.current = null;
        voiceRecorder.current = null;
        setVoiceActive(false);
        void transcribeAudio(connection, recording, {
          model: String(snapshot?.speech_config?.asr && (snapshot.speech_config.asr as Record<string, unknown>).model || ""),
          language: String(snapshot?.speech_config?.asr && (snapshot.speech_config.asr as Record<string, unknown>).language || ""),
        }).then(async (text) => {
          setNotice(`Voice recognized: ${text}`);
          await deliver(text, { inputMode: "voice" });
        }).catch(async (error) => {
          setNotice(`Voice input failed: ${String(error)}`);
          await requestVoiceAssistance(String(error));
        }).finally(() => setVoiceBusy(false));
      };
      recorder.start(250);
      setVoiceActive(true);
      setNotice("Microphone open — click the red stop button to transcribe and send.");
    } catch (error) {
      voiceStream.current?.getTracks().forEach((track) => track.stop());
      setVoiceActive(false);
      setVoiceBusy(false);
      await requestVoiceAssistance(String(error));
    }
  }, [connection, deliver, requestVoiceAssistance, snapshot?.speech_config, voiceActive, voiceBusy, voiceReady]);

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
            content = <CielChatApp messages={messages} endpoint={connection.endpoint} draft={draft} online={runtimeOnline} sending={sending} notice={notice} onDraft={setDraft} onSubmit={submit} voiceReady={voiceReady} voiceActive={voiceActive} voiceBusy={voiceBusy} onVoice={() => void toggleVoice()} />;
          } else if (app.id === BROWSER_APP_ID) {
            content = <CielBrowserApp active={desktop.activeWindowId === instance.id} />;
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
