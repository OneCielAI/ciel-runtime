"""Pure HTML renderers for the router web interface."""

from __future__ import annotations

import html as html_lib
import json

def render_web_chat_page(
    *,
    model: str,
    provider: str,
    mode: str,
    api_status: str,
    timeout_ms: int,
    workspace: str,
    router_port: int,
    instance_id: str,
) -> str:
    escaped_model = html_lib.escape(model)
    escaped_provider = html_lib.escape(provider)
    escaped_mode = html_lib.escape(mode)
    escaped_api_status = html_lib.escape(api_status)
    escaped_workspace = html_lib.escape(workspace)
    escaped_instance_id = html_lib.escape(instance_id)
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Ciel Runtime Web Chat</title>
  <style>
    :root {{
      color-scheme: dark;
      font-family: ui-sans-serif, system-ui, -apple-system, Segoe UI, sans-serif;
      --bg: #0a0d12;
      --panel: #111827;
      --panel-2: #162033;
      --line: #283548;
      --text: #eef2f8;
      --muted: #a9b4c6;
      --user: #174c6b;
      --assistant: #243447;
      --accent: #2f9e8f;
      --danger: #fca5a5;
      --ok: #86efac;
    }}
    * {{ box-sizing: border-box; }}
    body {{ margin: 0; min-height: 100vh; background: var(--bg); color: var(--text); }}
    .shell {{ display: grid; grid-template-columns: 280px minmax(0, 1fr); min-height: 100vh; }}
    aside {{ border-right: 1px solid var(--line); background: #0e1521; padding: 18px; }}
    .brand {{ font-size: 19px; font-weight: 700; letter-spacing: 0; margin: 0 0 12px; }}
    .status-card {{ border: 1px solid var(--line); border-radius: 8px; background: var(--panel); padding: 12px; display: grid; gap: 10px; }}
    .meta-label {{ color: var(--muted); font-size: 11px; text-transform: uppercase; }}
    .meta-value {{ margin-top: 3px; font-family: ui-monospace, SFMono-Regular, Consolas, monospace; font-size: 12px; word-break: break-word; }}
    .nav {{ margin-top: 14px; display: grid; gap: 8px; }}
    .nav a, .ghost {{
      display: flex; align-items: center; justify-content: center;
      min-height: 36px; border-radius: 6px; border: 1px solid var(--line);
      background: #0b111b; color: var(--text); text-decoration: none; cursor: pointer;
    }}
    .nav a:hover, .ghost:hover {{ border-color: var(--accent); }}
    main {{ display: grid; grid-template-rows: auto minmax(0, 1fr) auto; min-width: 0; }}
    header {{ min-height: 66px; display: flex; align-items: center; justify-content: space-between; gap: 12px; padding: 14px 18px; border-bottom: 1px solid var(--line); background: #0d1420; }}
    h1 {{ margin: 0; font-size: 18px; letter-spacing: 0; }}
    .sub {{ color: var(--muted); font-size: 12px; margin-top: 4px; }}
    .pill {{ border: 1px solid var(--line); border-radius: 999px; padding: 5px 9px; color: var(--muted); font-size: 12px; white-space: nowrap; }}
    #transcript {{ overflow-y: auto; padding: 18px; display: flex; flex-direction: column; gap: 12px; }}
    .row {{ display: flex; width: 100%; }}
    .row.user {{ justify-content: flex-end; }}
    .bubble {{
      max-width: min(760px, 86%);
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 10px 12px;
      line-height: 1.45;
      white-space: normal;
      word-break: break-word;
      box-shadow: 0 1px 0 rgba(255,255,255,.03) inset;
    }}
    .row.user .bubble {{ background: var(--user); border-color: #276a8d; }}
    .row.assistant .bubble {{ background: var(--assistant); }}
    .row.system .bubble {{ background: #191f2b; color: var(--muted); font-family: ui-monospace, SFMono-Regular, Consolas, monospace; font-size: 12px; white-space: pre-wrap; }}
    .markdown > :first-child {{ margin-top: 0; }}
    .markdown > :last-child {{ margin-bottom: 0; }}
    .markdown p {{ margin: 0 0 10px; }}
    .markdown h1, .markdown h2, .markdown h3, .markdown h4 {{ margin: 12px 0 8px; line-height: 1.2; }}
    .markdown h1 {{ font-size: 1.35rem; }}
    .markdown h2 {{ font-size: 1.2rem; }}
    .markdown h3 {{ font-size: 1.08rem; }}
    .markdown ul, .markdown ol {{ margin: 0 0 10px 20px; padding: 0; }}
    .markdown li {{ margin: 3px 0; }}
    .markdown blockquote {{ margin: 0 0 10px; padding-left: 12px; border-left: 3px solid #4b6585; color: var(--muted); }}
    .markdown pre {{ margin: 0 0 10px; padding: 10px; overflow-x: auto; border: 1px solid #33445b; border-radius: 6px; background: #0b111b; white-space: pre; }}
    .markdown code {{ font-family: ui-monospace, SFMono-Regular, Consolas, monospace; font-size: .92em; }}
    .markdown :not(pre) > code {{ padding: 1px 4px; border-radius: 4px; background: rgba(191, 219, 254, .12); }}
    .markdown a {{ color: #8bd7ff; text-decoration: underline; text-underline-offset: 2px; }}
    .markdown table {{ width: 100%; border-collapse: collapse; margin: 0 0 10px; display: block; overflow-x: auto; }}
    .markdown th, .markdown td {{ border: 1px solid #3a4b63; padding: 6px 8px; text-align: left; vertical-align: top; }}
    .markdown th {{ background: rgba(255,255,255,.06); font-weight: 700; }}
    .markdown hr {{ border: 0; border-top: 1px solid var(--line); margin: 12px 0; }}
    .composer {{ border-top: 1px solid var(--line); padding: 12px 18px; background: #0d1420; }}
    .composer-inner {{ display: grid; grid-template-columns: minmax(0, 1fr) auto; gap: 10px; align-items: end; }}
    textarea {{
      width: 100%; min-height: 54px; max-height: 180px; resize: vertical;
      border: 1px solid var(--line); border-radius: 8px; background: #080d14; color: var(--text);
      padding: 10px 12px; line-height: 1.4; font: inherit;
    }}
    button.primary {{
      width: 86px; min-height: 54px; border: 1px solid #37b7a4; border-radius: 8px;
      background: #127668; color: white; font-weight: 700; cursor: pointer;
    }}
    button.primary:disabled {{ opacity: .55; cursor: not-allowed; }}
    .composer-actions {{ display: flex; gap: 8px; align-items: center; margin-top: 8px; flex-wrap: wrap; }}
    .attach-button {{
      min-height: 34px; border: 1px solid var(--line); border-radius: 6px;
      background: #0b111b; color: var(--text); padding: 0 12px; cursor: pointer;
    }}
    .attach-button:hover {{ border-color: var(--accent); }}
    .attach-button:disabled {{ opacity: .55; cursor: not-allowed; }}
    .recording {{ border-color: #ef4444 !important; color: #fecaca !important; }}
    .message-actions {{ display: flex; align-items: flex-start; padding: 4px; }}
    .message-actions button {{ border: 1px solid var(--line); border-radius: 999px; background: #0b111b; color: var(--muted); cursor: pointer; padding: 4px 8px; }}
    .structured-response {{ display: grid; gap: 10px; }}
    .response-section {{ display: grid; gap: 4px; }}
    .response-section + .response-section {{ border-top: 1px solid rgba(255,255,255,.1); padding-top: 9px; }}
    .response-label {{ color: #9fb1c8; font-size: 10px; font-weight: 700; letter-spacing: .08em; text-transform: uppercase; }}
    .response-spoken {{ color: #f0fdfa; }}
    .live-transcript {{ display: none; width: 100%; border: 1px solid #315b66; border-radius: 6px; background: #0d1d25; color: #bcecf3; padding: 7px 9px; font-size: 13px; }}
    .live-transcript.active {{ display: block; }}
    dialog {{ width: min(720px, calc(100vw - 28px)); max-height: calc(100vh - 28px); overflow: auto; border: 1px solid var(--line); border-radius: 10px; background: var(--panel); color: var(--text); padding: 0; }}
    dialog::backdrop {{ background: rgba(0,0,0,.72); }}
    .settings-head {{ position: sticky; top: 0; display: flex; justify-content: space-between; align-items: center; gap: 12px; padding: 14px 16px; border-bottom: 1px solid var(--line); background: var(--panel); }}
    .settings-body {{ padding: 16px; display: grid; gap: 16px; }}
    .settings-section {{ border: 1px solid var(--line); border-radius: 8px; padding: 12px; display: grid; gap: 10px; }}
    .settings-section h3 {{ margin: 0; font-size: 14px; }}
    .settings-grid {{ display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 10px; }}
    .settings-grid label {{ display: grid; gap: 5px; color: var(--muted); font-size: 12px; }}
    .settings-grid label.wide {{ grid-column: 1 / -1; }}
    .settings-grid input, .settings-grid select {{ width: 100%; border: 1px solid var(--line); border-radius: 6px; background: #080d14; color: var(--text); padding: 8px; }}
    .check {{ display: flex !important; grid-auto-flow: column; justify-content: start; align-items: center; gap: 7px !important; }}
    .check input {{ width: auto; }}
    .settings-actions {{ display: flex; justify-content: flex-end; gap: 8px; }}
    #fileInput {{ display: none; }}
    .attachment-tray {{ display: flex; gap: 7px; flex-wrap: wrap; min-height: 0; }}
    .attachment-chip {{
      display: inline-flex; align-items: center; gap: 7px; max-width: min(360px, 100%);
      border: 1px solid #33445b; border-radius: 999px; background: #121b2a;
      padding: 5px 8px; color: var(--muted); font-size: 12px;
    }}
    .attachment-chip span {{ overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }}
    .attachment-chip button {{
      width: 18px; height: 18px; display: inline-flex; align-items: center; justify-content: center;
      border: 0; border-radius: 999px; background: #243447; color: var(--text); cursor: pointer;
    }}
    .drop-active textarea {{ border-color: var(--accent); box-shadow: 0 0 0 2px rgba(47, 158, 143, .18); }}
    .hint {{ margin-top: 7px; color: var(--muted); font-size: 12px; }}
    .error {{ color: var(--danger); }}
    .ok {{ color: var(--ok); }}
    code {{ color: #bfdbfe; }}
    @media (max-width: 820px) {{
      .shell {{ grid-template-columns: 1fr; }}
      aside {{ display: none; }}
      .bubble {{ max-width: 94%; }}
      header {{ align-items: flex-start; flex-direction: column; }}
      .pill {{ white-space: normal; }}
      .settings-grid {{ grid-template-columns: 1fr; }}
    }}
  </style>
</head>
<body>
  <div class="shell">
    <aside>
      <div class="brand">Ciel Runtime</div>
      <div class="status-card">
        <div><div class="meta-label">Provider</div><div class="meta-value">{escaped_provider}</div></div>
        <div><div class="meta-label">Mode</div><div class="meta-value">{escaped_mode}</div></div>
        <div><div class="meta-label">Model</div><div class="meta-value">{escaped_model}</div></div>
        <div><div class="meta-label">API</div><div class="meta-value">{escaped_api_status}</div></div>
        <div><div class="meta-label">Timeout</div><div class="meta-value">{timeout_ms:,} ms</div></div>
        <div><div class="meta-label">Bridge</div><div class="meta-value">active session channel</div></div>
        <div><div class="meta-label">Instance</div><div class="meta-value">{escaped_instance_id}</div></div>
        <div><div class="meta-label">Workspace</div><div class="meta-value">{escaped_workspace}</div></div>
      </div>
      <div class="nav">
        <a href="/">Router Home</a>
        <a href="/ca/events">Events</a>
        <a href="/health">Health JSON</a>
        <a href="/ca/web/chat/api">Chat API JSON</a>
        <button class="ghost" id="speechSettingsButton" type="button">Speech Settings</button>
        <button class="ghost" id="shareButton" type="button">Copy Chat Link</button>
        <button class="ghost" id="clearButton" type="button">Clear Chat</button>
      </div>
    </aside>
    <main>
      <header>
        <div>
          <h1>Session Web Chat</h1>
          <div class="sub">Send messages into the active coding-agent session through the Ciel Runtime channel bridge and stream replies from the same channel.</div>
        </div>
        <div class="pill" id="statePill">ready</div>
      </header>
      <section id="transcript" aria-live="polite"></section>
      <form class="composer" id="composer">
        <div class="composer-inner">
          <textarea id="prompt" placeholder="Type a message..." autocomplete="off"></textarea>
          <button class="primary" id="sendButton" type="submit">Send</button>
        </div>
        <div class="composer-actions">
          <button class="attach-button" id="micButton" type="button">Start live voice</button>
          <button class="attach-button" id="attachButton" type="button">Attach files</button>
          <input id="fileInput" type="file" multiple>
          <div class="attachment-tray" id="attachmentTray" aria-live="polite"></div>
        </div>
        <div class="live-transcript" id="liveTranscript" aria-live="polite"></div>
        <div class="hint">Enter sends. Shift+Enter inserts a new line. Live voice detects the end of each utterance, transcribes and sends it automatically, and supports interruption while TTS is speaking. The active coding-agent session handles the message, so its configured tools and MCP servers remain available.</div>
      </form>
    </main>
  </div>
  <dialog id="speechSettingsDialog">
    <form id="speechSettingsForm">
      <div class="settings-head"><strong>Speech Settings</strong><button class="ghost" id="speechSettingsClose" type="button">Close</button></div>
      <div class="settings-body">
        <section class="settings-section">
          <h3>STT / Qwen ASR</h3>
          <div class="settings-grid">
            <label class="check"><input id="asrEnabled" type="checkbox"> Enable STT</label>
            <label>Language<input id="asrLanguage" placeholder="auto"></label>
            <label>End silence (ms)<input id="asrSilenceMs" type="number" min="250" max="3000" step="50"></label>
            <label>Minimum speech (ms)<input id="asrMinSpeechMs" type="number" min="100" max="2000" step="50"></label>
            <label>VAD threshold<input id="asrVadThreshold" type="number" min="0.005" max="0.2" step="0.001"></label>
            <label class="wide">Tailscale base URL<input id="asrBaseUrl" placeholder="http://ciel-asr:8000"></label>
            <label class="wide">Model<input id="asrModel" placeholder="Qwen/Qwen3-ASR-0.6B"></label>
            <label class="wide">Remote bearer token<input id="asrApiKey" type="password" autocomplete="new-password" placeholder="Leave blank to keep current token"></label>
          </div>
        </section>
        <section class="settings-section">
          <h3>TTS / MOSS-TTS-Nano</h3>
          <div class="settings-grid">
            <label class="check"><input id="ttsEnabled" type="checkbox"> Enable TTS</label>
            <label class="check"><input id="ttsAutoSpeak" type="checkbox"> Speak replies automatically</label>
            <label class="check"><input id="ttsStreaming" type="checkbox"> Stream audio while it is generated</label>
            <label class="wide">Tailscale base URL<input id="ttsBaseUrl" placeholder="http://ciel-tts:8091"></label>
            <label>Voice<input id="ttsVoice" placeholder="default"></label>
            <label>Language<input id="ttsLanguage" placeholder="ko"></label>
            <label>PCM sample rate<input id="ttsSampleRate" type="number" min="8000" max="192000" step="1000"></label>
            <label class="wide">Model<input id="ttsModel" placeholder="OpenMOSS-Team/MOSS-TTS-Nano"></label>
            <label class="wide">Reference voice (required by MOSS-TTS-Nano)<input id="ttsReferenceAudio" type="file" accept="audio/*"><span class="hint" id="ttsReferenceAudioStatus">No reference voice configured</span></label>
            <label class="wide">Reference transcript (required by CosyVoice 3)<input id="ttsReferenceText" placeholder="Exact transcript of the reference clip"></label>
            <label class="check wide"><input id="ttsClearReferenceAudio" type="checkbox"> Remove the saved reference voice</label>
            <label class="wide">Remote bearer token<input id="ttsApiKey" type="password" autocomplete="new-password" placeholder="Leave blank to keep current token"></label>
          </div>
        </section>
        <section class="settings-section">
          <h3>Colab CLI connection</h3>
          <div class="settings-grid">
            <label class="check"><input id="colabEnabled" type="checkbox"> Manage workers with Colab CLI</label>
            <label>WSL distribution<input id="colabDistribution" placeholder="Ubuntu-26.04"></label>
            <label>Authentication<select id="colabAuth"><option value="adc">ADC</option><option value="oauth2">OAuth2</option></select></label>
            <label>Account profile<input id="colabProfile" placeholder="default"></label>
            <label>TTS engine<select id="colabTtsBackend"><option value="moss">MOSS-TTS-Nano</option><option value="cosyvoice3">Fun-CosyVoice 3</option></select></label>
            <label>ASR session<input id="colabAsrSession" placeholder="ciel-asr"></label>
            <label>TTS session<input id="colabTtsSession" placeholder="ciel-tts"></label>
            <label>ASR GPU<select id="colabAsrAccelerator"><option>T4</option><option>L4</option><option>G4</option><option>A100</option><option>H100</option></select></label>
            <label>TTS GPU<select id="colabTtsAccelerator"><option>T4</option><option>L4</option><option>G4</option><option>A100</option><option>H100</option></select></label>
            <label class="wide">Tailscale auth key for this run<input id="colabTailscaleAuthKey" type="password" autocomplete="new-password" placeholder="Not saved; Colab Secret may be used instead"></label>
            <label class="wide">Speech API key for this run<input id="colabSpeechApiKey" type="password" autocomplete="new-password" placeholder="Not saved; optional"></label>
            <label class="check wide"><input id="colabResetAuthentication" type="checkbox"> Forget this profile's current login before generating a login command</label>
          </div>
          <div class="settings-actions"><button class="ghost" id="colabLoginButton" type="button">Copy login command</button><button class="ghost" id="colabStatusButton" type="button">Check sessions</button><button class="ghost" id="colabStartButton" type="button">Start missing</button><button class="primary" id="colabDeployButton" type="button">Recover &amp; deploy</button><button class="ghost" id="colabRecreateButton" type="button">Recreate all</button></div>
          <pre class="hint" id="colabJobStatus">No Colab deployment job has been started.</pre>
          <div class="hint">Named account profiles get an isolated WSL HOME, OAuth token, session state, and history; <code>default</code> keeps the existing Colab CLI login for compatibility. Login remains an interactive CLI step; deployment jobs run in the background. Ephemeral keys above are passed only to that job and are never saved by Ciel.</div>
        </section>
        <section class="settings-section">
          <h3>Tailscale tunnel</h3>
          <div class="settings-grid">
            <label class="check"><input id="tailscaleEnabled" type="checkbox"> Use tailnet-only addresses</label>
            <label>ASR hostname<input id="tailscaleAsrHostname" placeholder="ciel-asr"></label>
            <label>TTS hostname<input id="tailscaleTtsHostname" placeholder="ciel-tts"></label>
          </div>
          <div class="hint">The browser calls Ciel locally. Only the Ciel router connects to these Tailscale services.</div>
        </section>
        <div class="settings-actions"><button class="ghost" id="speechHealthButton" type="button">Test connections</button><button class="ghost" id="speechPlaybackTestButton" type="button">Test voice</button><button class="primary" type="submit">Save</button></div>
      </div>
    </form>
  </dialog>
  <script>
    const MODEL = {json.dumps(model)};
    const EXPECTED_INSTANCE_ID = {json.dumps(instance_id)};
    const EXPECTED_WORKSPACE = {json.dumps(workspace)};
    const EXPECTED_ROUTER_PORT = {int(router_port)};
    const ORIGIN_INSTANCE_KEY = 'ciel-runtime-web-chat-origin-instance:' + location.origin;
    const bootstrapParams = new URLSearchParams(location.search);
    const previousOriginInstance = localStorage.getItem(ORIGIN_INSTANCE_KEY) || '';
    const rebindOriginInstance = bootstrapParams.get('rebind') === '1';
    if (!previousOriginInstance || rebindOriginInstance) {{
      localStorage.setItem(ORIGIN_INSTANCE_KEY, EXPECTED_INSTANCE_ID);
    }}
    const boundOriginInstance = rebindOriginInstance ? EXPECTED_INSTANCE_ID : previousOriginInstance;
    let instanceIdentityBlocked = boundOriginInstance && boundOriginInstance !== EXPECTED_INSTANCE_ID
      ? `This browser origin is bound to ${{previousOriginInstance}}, but the page came from ${{EXPECTED_INSTANCE_ID}}.`
      : '';
    const transcript = document.getElementById('transcript');
    const composer = document.getElementById('composer');
    const prompt = document.getElementById('prompt');
    const sendButton = document.getElementById('sendButton');
    const attachButton = document.getElementById('attachButton');
    const micButton = document.getElementById('micButton');
    const fileInput = document.getElementById('fileInput');
    const attachmentTray = document.getElementById('attachmentTray');
    const liveTranscript = document.getElementById('liveTranscript');
    const shareButton = document.getElementById('shareButton');
    const clearButton = document.getElementById('clearButton');
    const speechSettingsButton = document.getElementById('speechSettingsButton');
    const speechSettingsDialog = document.getElementById('speechSettingsDialog');
    const speechSettingsForm = document.getElementById('speechSettingsForm');
    const speechSettingsClose = document.getElementById('speechSettingsClose');
    const speechHealthButton = document.getElementById('speechHealthButton');
    const speechPlaybackTestButton = document.getElementById('speechPlaybackTestButton');
    const statePill = document.getElementById('statePill');
    const SESSION_KEY = 'ciel-runtime-web-chat-session';
    const LAST_ID_KEY = 'ciel-runtime-web-chat-last-id';
    const HISTORY_PAGE_SIZE = 80;
    const renderedIds = new Set();
    let oldestId = 0;
    let historyLoading = false;
    let historyExhausted = false;
    function cleanSessionId(value) {{
      return String(value || '').replace(/[^a-zA-Z0-9_.:-]/g, '').slice(0, 128);
    }}
    const urlParams = new URLSearchParams(location.search);
    const urlSessionId = cleanSessionId(urlParams.get('session') || urlParams.get('s') || '');
    const storedSessionId = cleanSessionId(localStorage.getItem(SESSION_KEY) || '');
    const sessionId = urlSessionId || storedSessionId || (crypto.randomUUID ? crypto.randomUUID() : String(Date.now()) + '-' + Math.random().toString(16).slice(2));
    localStorage.setItem(SESSION_KEY, sessionId);
    if (!urlSessionId) {{
      urlParams.set('session', sessionId);
      const nextUrl = location.pathname + '?' + urlParams.toString() + location.hash;
      history.replaceState(null, '', nextUrl);
    }}
    const channel = 'web-chat-' + sessionId;
    const scopedLastIdKey = LAST_ID_KEY + ':' + sessionId;
    let lastId = Number(localStorage.getItem(scopedLastIdKey) || '0') || 0;
    let eventSource = null;
    let selectedFiles = [];
    let speechConfig = {{asr: {{enabled: false}}, tts: {{enabled: false, auto_speak: false}}}};
    let mediaStream = null;
    let audioContext = null;
    let audioInput = null;
    let audioProcessor = null;
    let liveVoiceEnabled = false;
    let vadSpeechActive = false;
    let vadSpeechChunks = [];
    let vadPreRollChunks = [];
    let vadSpeechStartedAt = 0;
    let vadLastVoiceAt = 0;
    let vadVoicedSamples = 0;
    let vadNoiseFloor = 0.006;
    let liveTranscriptionQueue = Promise.resolve();
    let livePartialInFlight = false;
    let livePartialLastAt = 0;
    let liveUtteranceSerial = 0;
    let activeSpeechAudio = null;
    let activeSpeechUrl = '';
    let speechPlaybackContext = null;
    let activeSpeechSource = null;
    const activeSpeechSources = new Set();
    let speechGenerationController = null;
    let pendingTtsReferenceAudio = '';
    function setState(text, cls = '') {{
      statePill.textContent = text;
      statePill.className = 'pill ' + cls;
    }}
    function escapeHtml(value) {{
      return String(value ?? '').replace(/[&<>"']/g, ch => ({{
        '&': '&amp;',
        '<': '&lt;',
        '>': '&gt;',
        '"': '&quot;',
        "'": '&#39;'
      }}[ch]));
    }}
    function safeHref(value) {{
      const href = String(value || '').trim();
      if (/^(https?:|mailto:)/i.test(href)) return escapeHtml(href);
      return '#';
    }}
    function renderInlineMarkdown(value) {{
      const codeBlocks = [];
      const linkBlocks = [];
      let raw = String(value ?? '').replace(/`([^`\\n]+)`/g, (_match, code) => {{
        const token = '\\u0000CODE' + codeBlocks.length + '\\u0000';
        codeBlocks.push('<code>' + escapeHtml(code) + '</code>');
        return token;
      }});
      raw = raw.replace(/\\[([^\\]]+)\\]\\(([^)\\s]+)\\)/g, (_match, label, href) => {{
        const token = '\\u0000LINK' + linkBlocks.length + '\\u0000';
        linkBlocks.push('<a href="' + safeHref(href) + '" target="_blank" rel="noopener noreferrer">' + escapeHtml(label) + '</a>');
        return token;
      }});
      let html = escapeHtml(raw);
      html = html.replace(/\\*\\*([^*]+)\\*\\*/g, '<strong>$1</strong>');
      html = html.replace(/(^|[\\s(])\\*([^*\\n]+)\\*/g, '$1<em>$2</em>');
      html = html.replace(/~~([^~]+)~~/g, '<del>$1</del>');
      linkBlocks.forEach((link, index) => {{
        html = html.replace('\\u0000LINK' + index + '\\u0000', link);
      }});
      codeBlocks.forEach((code, index) => {{
        html = html.replace('\\u0000CODE' + index + '\\u0000', code);
      }});
      return html;
    }}
    function splitMarkdownTableRow(line) {{
      let row = String(line || '').trim();
      if (row.startsWith('|')) row = row.slice(1);
      if (row.endsWith('|')) row = row.slice(0, -1);
      return row.split('|').map(cell => cell.trim());
    }}
    function isMarkdownDelimiterCell(cell) {{
      const compact = String(cell || '').replace(/\\s+/g, '');
      const core = compact.replace(/^:/, '').replace(/:$/, '');
      return core.length >= 3 && /^-+$/.test(core);
    }}
    function isMarkdownTableDelimiter(line) {{
      const cells = splitMarkdownTableRow(line);
      return cells.length > 1 && cells.every(isMarkdownDelimiterCell);
    }}
    function isMarkdownTableStart(lines, index) {{
      return index + 1 < lines.length
        && String(lines[index] || '').includes('|')
        && splitMarkdownTableRow(lines[index]).length > 1
        && isMarkdownTableDelimiter(lines[index + 1]);
    }}
    function isMarkdownBlockStart(lines, index) {{
      const line = String(lines[index] || '');
      const trimmed = line.trim();
      if (!trimmed) return true;
      return trimmed.startsWith('```')
        || isMarkdownTableStart(lines, index)
        || /^(####|###|##|#)\\s+/.test(trimmed)
        || /^([-*+]\\s+|\\d+[.)]\\s+|>\\s?)/.test(trimmed)
        || /^(---+|\\*\\*\\*+|___+)$/.test(trimmed);
    }}
    function renderMarkdownTable(lines, startIndex) {{
      const headers = splitMarkdownTableRow(lines[startIndex]);
      const rows = [];
      let index = startIndex + 2;
      while (index < lines.length && String(lines[index] || '').trim() && String(lines[index] || '').includes('|')) {{
        rows.push(splitMarkdownTableRow(lines[index]));
        index += 1;
      }}
      const head = '<thead><tr>' + headers.map(cell => '<th>' + renderInlineMarkdown(cell) + '</th>').join('') + '</tr></thead>';
      const body = '<tbody>' + rows.map(row => {{
        const cells = headers.map((_header, cellIndex) => '<td>' + renderInlineMarkdown(row[cellIndex] || '') + '</td>').join('');
        return '<tr>' + cells + '</tr>';
      }}).join('') + '</tbody>';
      return {{ html: '<table>' + head + body + '</table>', nextIndex: index }};
    }}
    function renderMarkdown(text) {{
      const lines = String(text ?? '').replace(/\\r\\n?/g, '\\n').split('\\n');
      const blocks = [];
      let index = 0;
      while (index < lines.length) {{
        const line = lines[index];
        const trimmed = String(line || '').trim();
        if (!trimmed) {{
          index += 1;
          continue;
        }}
        if (trimmed.startsWith('```')) {{
          const code = [];
          index += 1;
          while (index < lines.length && !String(lines[index] || '').trim().startsWith('```')) {{
            code.push(lines[index]);
            index += 1;
          }}
          if (index < lines.length) index += 1;
          blocks.push('<pre><code>' + escapeHtml(code.join('\\n')) + '</code></pre>');
          continue;
        }}
        if (isMarkdownTableStart(lines, index)) {{
          const table = renderMarkdownTable(lines, index);
          blocks.push(table.html);
          index = table.nextIndex;
          continue;
        }}
        const heading = trimmed.match(/^(####|###|##|#)\\s+(.+)$/);
        if (heading) {{
          const level = Math.min(4, heading[1].length);
          blocks.push('<h' + level + '>' + renderInlineMarkdown(heading[2]) + '</h' + level + '>');
          index += 1;
          continue;
        }}
        if (/^(---+|\\*\\*\\*+|___+)$/.test(trimmed)) {{
          blocks.push('<hr>');
          index += 1;
          continue;
        }}
        if (/^[-*+]\\s+/.test(trimmed)) {{
          const items = [];
          while (index < lines.length && /^[-*+]\\s+/.test(String(lines[index] || '').trim())) {{
            items.push(String(lines[index] || '').trim().replace(/^[-*+]\\s+/, ''));
            index += 1;
          }}
          blocks.push('<ul>' + items.map(item => '<li>' + renderInlineMarkdown(item) + '</li>').join('') + '</ul>');
          continue;
        }}
        if (/^\\d+[.)]\\s+/.test(trimmed)) {{
          const items = [];
          while (index < lines.length && /^\\d+[.)]\\s+/.test(String(lines[index] || '').trim())) {{
            items.push(String(lines[index] || '').trim().replace(/^\\d+[.)]\\s+/, ''));
            index += 1;
          }}
          blocks.push('<ol>' + items.map(item => '<li>' + renderInlineMarkdown(item) + '</li>').join('') + '</ol>');
          continue;
        }}
        if (/^>\\s?/.test(trimmed)) {{
          const quotes = [];
          while (index < lines.length && /^>\\s?/.test(String(lines[index] || '').trim())) {{
            quotes.push(String(lines[index] || '').trim().replace(/^>\\s?/, ''));
            index += 1;
          }}
          blocks.push('<blockquote>' + renderInlineMarkdown(quotes.join('\\n')) + '</blockquote>');
          continue;
        }}
        const paragraph = [trimmed];
        index += 1;
        while (index < lines.length && !isMarkdownBlockStart(lines, index)) {{
          paragraph.push(String(lines[index] || '').trim());
          index += 1;
        }}
        blocks.push('<p>' + renderInlineMarkdown(paragraph.join(' ')) + '</p>');
      }}
      return blocks.join('');
    }}
    function addBubble(role, text, mode = 'append', id = null) {{
      if (id !== null && id !== undefined) {{
        const key = String(id);
        if (renderedIds.has(key)) return null;
        renderedIds.add(key);
      }}
      const row = document.createElement('div');
      row.className = 'row ' + role;
      const bubble = document.createElement('div');
      bubble.className = 'bubble';
      if (role === 'system') {{
        bubble.textContent = text;
      }} else {{
        bubble.classList.add('markdown');
        bubble.innerHTML = renderMarkdown(text);
      }}
      row.appendChild(bubble);
      if (role === 'assistant') {{
        const actions = document.createElement('div');
        actions.className = 'message-actions';
        const speak = document.createElement('button');
        speak.type = 'button';
        speak.textContent = 'Speak';
        speak.addEventListener('click', () => speakText(text));
        actions.appendChild(speak);
        row.appendChild(actions);
      }}
      if (mode === 'prepend') {{
        transcript.insertBefore(row, transcript.firstChild);
      }} else {{
        transcript.appendChild(row);
        transcript.scrollTop = transcript.scrollHeight;
      }}
      return bubble;
    }}
    function blockRuntimeIdentity(reason) {{
      const detail = String(reason || 'Runtime identity changed.');
      if (instanceIdentityBlocked === detail && sendButton.disabled) return;
      instanceIdentityBlocked = detail;
      if (eventSource) eventSource.close();
      eventSource = null;
      sendButton.disabled = true;
      attachButton.disabled = true;
      micButton.disabled = true;
      stopActiveSpeech();
      setState('instance mismatch', 'error');
      addBubble('system', 'Web Chat stopped to prevent cross-instance delivery. ' + detail + ' Restore the original proxy target, or intentionally open this URL once with ?rebind=1.');
    }}
    async function verifyRuntimeIdentity(options = {{}}) {{
      if (instanceIdentityBlocked) {{
        if (options.announce !== false) blockRuntimeIdentity(instanceIdentityBlocked);
        return false;
      }}
      try {{
        const response = await fetch('/health', {{headers: {{'accept': 'application/json'}}, cache: 'no-store'}});
        if (!response.ok) throw new Error(`HTTP ${{response.status}}`);
        const health = await response.json();
        const actualInstance = String(health.instance_id || '');
        const actualWorkspace = String(health.workspace || '');
        const actualPort = Number(health.router_port || 0);
        if (actualInstance !== EXPECTED_INSTANCE_ID || actualWorkspace !== EXPECTED_WORKSPACE || actualPort !== EXPECTED_ROUTER_PORT) {{
          blockRuntimeIdentity(`Expected ${{EXPECTED_INSTANCE_ID}} (${{EXPECTED_WORKSPACE}}:${{EXPECTED_ROUTER_PORT}}), received ${{actualInstance || 'unknown'}} (${{actualWorkspace || 'unknown'}}:${{actualPort || 'unknown'}}).`);
          return false;
        }}
        return true;
      }} catch (err) {{
        if (options.announce !== false) setState('identity check failed', 'error');
        return false;
      }}
    }}
    function structuredWebResponse(message) {{
      const value = message && message.meta && message.meta.web_response;
      if (!value || typeof value !== 'object') return null;
      const response = {{
        spoken: String(value.spoken || '').trim(),
        overview: String(value.overview || '').trim(),
        details: String(value.details || '').trim(),
      }};
      return response.spoken || response.overview || response.details ? response : null;
    }}
    function addStructuredBubble(response, mode = 'append', id = null) {{
      if (id !== null && id !== undefined) {{
        const key = String(id);
        if (renderedIds.has(key)) return null;
        renderedIds.add(key);
      }}
      const row = document.createElement('div');
      row.className = 'row assistant';
      const bubble = document.createElement('div');
      bubble.className = 'bubble structured-response';
      const sections = [
        ['Voice', response.spoken, 'response-spoken'],
        ['Overview', response.overview, 'markdown'],
        ['Details', response.details, 'markdown'],
      ];
      sections.forEach(([labelText, value, className]) => {{
        if (!value) return;
        const section = document.createElement('section');
        section.className = 'response-section ' + className;
        const label = document.createElement('div');
        label.className = 'response-label';
        label.textContent = labelText;
        const content = document.createElement('div');
        if (className === 'markdown') content.innerHTML = renderMarkdown(value);
        else content.textContent = value;
        section.appendChild(label);
        section.appendChild(content);
        bubble.appendChild(section);
      }});
      row.appendChild(bubble);
      const actions = document.createElement('div');
      actions.className = 'message-actions';
      const speak = document.createElement('button');
      speak.type = 'button';
      speak.textContent = 'Speak';
      speak.addEventListener('click', () => speakText(response.spoken || response.overview));
      actions.appendChild(speak);
      row.appendChild(actions);
      if (mode === 'prepend') transcript.insertBefore(row, transcript.firstChild);
      else {{ transcript.appendChild(row); transcript.scrollTop = transcript.scrollHeight; }}
      return bubble;
    }}
    function rememberLastId(id) {{
      const numeric = Number(id || 0) || 0;
      if (numeric > lastId) {{
        lastId = numeric;
        localStorage.setItem(scopedLastIdKey, String(lastId));
      }}
    }}
    function roleForMessage(message) {{
      return message.sender_id === 'web-user' ? 'user' : 'assistant';
    }}
    function renderIncomingMessage(message, mode = 'append') {{
      if (mode !== 'prepend') rememberLastId(message.id);
      const text = message.message || '';
      const structured = structuredWebResponse(message);
      if (!text.trim() && !structured) return;
      if (structured && roleForMessage(message) === 'assistant') addStructuredBubble(structured, mode, message.id);
      else addBubble(roleForMessage(message), text, mode, message.id);
      if (mode !== 'prepend' && message.sender_id !== 'web-user') {{
        setState('reply received', 'ok');
        const speechText = structured ? (structured.spoken || structured.overview) : text;
        if (speechConfig.tts && speechConfig.tts.enabled && speechText && (speechConfig.tts.auto_speak || liveVoiceEnabled)) speakText(speechText);
      }}
    }}
    function formatBytes(bytes) {{
      const value = Number(bytes || 0);
      if (value < 1024) return value + ' B';
      if (value < 1024 * 1024) return (value / 1024).toFixed(1).replace(/\\.0$/, '') + ' KB';
      return (value / (1024 * 1024)).toFixed(1).replace(/\\.0$/, '') + ' MB';
    }}
    function renderAttachmentTray() {{
      attachmentTray.innerHTML = '';
      selectedFiles.forEach((file, index) => {{
        const chip = document.createElement('div');
        chip.className = 'attachment-chip';
        const label = document.createElement('span');
        label.textContent = file.name + ' (' + formatBytes(file.size) + ')';
        const remove = document.createElement('button');
        remove.type = 'button';
        remove.setAttribute('aria-label', 'Remove ' + file.name);
        remove.textContent = 'x';
        remove.addEventListener('click', () => {{
          selectedFiles.splice(index, 1);
          renderAttachmentTray();
        }});
        chip.appendChild(label);
        chip.appendChild(remove);
        attachmentTray.appendChild(chip);
      }});
    }}
    function addSelectedFiles(fileList) {{
      const incoming = Array.from(fileList || []);
      if (!incoming.length) return;
      selectedFiles = selectedFiles.concat(incoming);
      renderAttachmentTray();
      setState(selectedFiles.length + ' file(s) ready', 'ok');
    }}
    function fileToBase64(file) {{
      return new Promise((resolve, reject) => {{
        const reader = new FileReader();
        reader.onload = () => {{
          const dataUrl = String(reader.result || '');
          const comma = dataUrl.indexOf(',');
          resolve(comma >= 0 ? dataUrl.slice(comma + 1) : dataUrl);
        }};
        reader.onerror = () => reject(reader.error || new Error('Could not read file'));
        reader.readAsDataURL(file);
      }});
    }}
    function fileToDataUrl(file) {{
      return new Promise((resolve, reject) => {{
        const reader = new FileReader();
        reader.onload = () => resolve(String(reader.result || ''));
        reader.onerror = () => reject(reader.error || new Error('Could not read file'));
        reader.readAsDataURL(file);
      }});
    }}
    function setSpeechForm(config) {{
      const asr = config.asr || {{}};
      const tts = config.tts || {{}};
      const colab = config.colab || {{}};
      const tailscale = config.tailscale || {{}};
      document.getElementById('asrEnabled').checked = Boolean(asr.enabled);
      document.getElementById('asrBaseUrl').value = asr.base_url || '';
      document.getElementById('asrModel').value = asr.model || '';
      document.getElementById('asrLanguage').value = asr.language || 'auto';
      document.getElementById('asrSilenceMs').value = asr.silence_ms || 900;
      document.getElementById('asrMinSpeechMs').value = asr.min_speech_ms || 300;
      document.getElementById('asrVadThreshold').value = asr.vad_threshold || 0.018;
      document.getElementById('asrApiKey').value = '';
      document.getElementById('asrApiKey').placeholder = asr.api_key_set ? 'Token is set; leave blank to keep it' : 'Optional remote bearer token';
      document.getElementById('ttsEnabled').checked = Boolean(tts.enabled);
      document.getElementById('ttsAutoSpeak').checked = Boolean(tts.auto_speak);
      document.getElementById('ttsStreaming').checked = Boolean(tts.streaming);
      document.getElementById('ttsBaseUrl').value = tts.base_url || '';
      document.getElementById('ttsModel').value = tts.model || '';
      document.getElementById('ttsVoice').value = tts.voice || 'default';
      document.getElementById('ttsLanguage').value = tts.language || 'ko';
      document.getElementById('ttsSampleRate').value = tts.sample_rate || 48000;
      document.getElementById('ttsReferenceText').value = tts.ref_text || '';
      document.getElementById('ttsReferenceAudioStatus').textContent = tts.ref_audio_set ? 'Reference voice saved securely on this Ciel router' : 'No reference voice configured';
      document.getElementById('ttsClearReferenceAudio').checked = false;
      document.getElementById('ttsReferenceAudio').value = '';
      pendingTtsReferenceAudio = '';
      document.getElementById('ttsApiKey').value = '';
      document.getElementById('ttsApiKey').placeholder = tts.api_key_set ? 'Token is set; leave blank to keep it' : 'Optional remote bearer token';
      document.getElementById('colabEnabled').checked = colab.enabled !== false;
      document.getElementById('colabDistribution').value = colab.distribution || 'Ubuntu-26.04';
      document.getElementById('colabAuth').value = colab.auth || 'adc';
      document.getElementById('colabProfile').value = colab.profile || 'default';
      document.getElementById('colabTtsBackend').value = colab.tts_backend || (String(tts.model || '').includes('CosyVoice3') ? 'cosyvoice3' : 'moss');
      document.getElementById('colabAsrSession').value = colab.asr_session || 'ciel-asr';
      document.getElementById('colabTtsSession').value = colab.tts_session || 'ciel-tts';
      document.getElementById('colabAsrAccelerator').value = colab.asr_accelerator || 'T4';
      document.getElementById('colabTtsAccelerator').value = colab.tts_accelerator || 'T4';
      document.getElementById('colabTailscaleAuthKey').value = '';
      document.getElementById('colabSpeechApiKey').value = '';
      document.getElementById('colabResetAuthentication').checked = false;
      document.getElementById('tailscaleEnabled').checked = tailscale.enabled !== false;
      document.getElementById('tailscaleAsrHostname').value = tailscale.asr_hostname || 'ciel-asr';
      document.getElementById('tailscaleTtsHostname').value = tailscale.tts_hostname || 'ciel-tts';
      micButton.disabled = !asr.enabled;
      micButton.title = asr.enabled ? 'Continuously detect, transcribe, and send speech' : 'Enable STT in Speech Settings first';
    }}
    async function loadSpeechConfig() {{
      const response = await fetch('/ca/speech/config', {{headers: {{'accept': 'application/json'}}}});
      const data = await response.json();
      if (!response.ok || !data.ok) throw new Error(data.error || `HTTP ${{response.status}}`);
      speechConfig = data;
      setSpeechForm(data);
      return data;
    }}
    async function saveSpeechConfig() {{
      const payload = {{
        asr: {{
          enabled: document.getElementById('asrEnabled').checked,
          base_url: document.getElementById('asrBaseUrl').value,
          model: document.getElementById('asrModel').value,
          language: document.getElementById('asrLanguage').value,
          silence_ms: Number(document.getElementById('asrSilenceMs').value || 900),
          min_speech_ms: Number(document.getElementById('asrMinSpeechMs').value || 300),
          vad_threshold: Number(document.getElementById('asrVadThreshold').value || 0.018),
          api_key: document.getElementById('asrApiKey').value,
        }},
        tts: {{
          enabled: document.getElementById('ttsEnabled').checked,
          auto_speak: document.getElementById('ttsAutoSpeak').checked,
          streaming: document.getElementById('ttsStreaming').checked,
          base_url: document.getElementById('ttsBaseUrl').value,
          model: document.getElementById('ttsModel').value,
          voice: document.getElementById('ttsVoice').value,
          language: document.getElementById('ttsLanguage').value,
          sample_rate: Number(document.getElementById('ttsSampleRate').value || 48000),
          ref_audio: pendingTtsReferenceAudio,
          ref_text: document.getElementById('ttsReferenceText').value,
          clear_ref_audio: document.getElementById('ttsClearReferenceAudio').checked,
          api_key: document.getElementById('ttsApiKey').value,
        }},
        colab: {{
          enabled: document.getElementById('colabEnabled').checked,
          distribution: document.getElementById('colabDistribution').value,
          auth: document.getElementById('colabAuth').value,
          profile: document.getElementById('colabProfile').value,
          tts_backend: document.getElementById('colabTtsBackend').value,
          asr_session: document.getElementById('colabAsrSession').value,
          tts_session: document.getElementById('colabTtsSession').value,
          asr_accelerator: document.getElementById('colabAsrAccelerator').value,
          tts_accelerator: document.getElementById('colabTtsAccelerator').value,
        }},
        tailscale: {{
          enabled: document.getElementById('tailscaleEnabled').checked,
          asr_hostname: document.getElementById('tailscaleAsrHostname').value,
          tts_hostname: document.getElementById('tailscaleTtsHostname').value,
        }},
      }};
      const response = await fetch('/ca/speech/config', {{method: 'POST', headers: {{'content-type': 'application/json', 'accept': 'application/json'}}, body: JSON.stringify(payload)}});
      const data = await response.json();
      if (!response.ok || !data.ok) throw new Error(data.error || `HTTP ${{response.status}}`);
      speechConfig = data;
      setSpeechForm(data);
      return data;
    }}
    function renderColabJob(payload) {{
      const output = document.getElementById('colabJobStatus');
      const job = payload && payload.job;
      if (!job) {{ output.textContent = 'No Colab deployment job has been started.'; return; }}
      const state = job.running ? 'running' : job.return_code === 0 ? 'completed' : `failed (${{job.return_code}})`;
      output.textContent = `${{job.action}} · profile ${{job.profile}} · ${{state}}\n${{job.output || ''}}`.trim();
      output.scrollTop = output.scrollHeight;
    }}
    async function pollColabJob() {{
      const response = await fetch('/ca/speech/colab/job', {{headers: {{'accept': 'application/json'}}, cache: 'no-store'}});
      const data = await response.json();
      renderColabJob(data);
      if (data.job && data.job.running) setTimeout(() => pollColabJob().catch(() => {{}}), 2000);
      else if (data.job) setState(data.job.return_code === 0 ? 'Colab job complete' : 'Colab job failed', data.job.return_code === 0 ? 'ok' : 'error');
      return data;
    }}
    async function runColabAction(action) {{
      await saveSpeechConfig();
      const payload = {{
        action,
        reset_authentication: document.getElementById('colabResetAuthentication').checked,
        secrets: {{
          tailscale_auth_key: document.getElementById('colabTailscaleAuthKey').value,
          speech_api_key: document.getElementById('colabSpeechApiKey').value,
        }},
      }};
      const response = await fetch('/ca/speech/colab/action', {{method: 'POST', headers: {{'content-type': 'application/json', 'accept': 'application/json'}}, body: JSON.stringify(payload)}});
      const data = await response.json();
      document.getElementById('colabTailscaleAuthKey').value = '';
      document.getElementById('colabSpeechApiKey').value = '';
      if (!response.ok || !data.ok) throw new Error(data.error || `HTTP ${{response.status}}`);
      if (data.requires_terminal) {{
        await navigator.clipboard.writeText(data.command);
        document.getElementById('colabJobStatus').textContent = 'Login command copied. Run it in a local terminal and complete authentication for the selected Google account.\\n\\n' + data.command;
        setState('login command copied', 'ok');
        return data;
      }}
      renderColabJob(data);
      setState(`Colab ${{action}} started`, 'ok');
      setTimeout(() => pollColabJob().catch(() => {{}}), 1000);
      return data;
    }}
    function unlockSpeechPlayback() {{
      const AudioContextClass = window.AudioContext || window.webkitAudioContext;
      if (!AudioContextClass) return null;
      if (!speechPlaybackContext || speechPlaybackContext.state === 'closed') speechPlaybackContext = new AudioContextClass();
      if (speechPlaybackContext.state === 'suspended') speechPlaybackContext.resume().catch(() => {{}});
      return speechPlaybackContext;
    }}
    function stopActiveSpeech() {{
      if (speechGenerationController) speechGenerationController.abort();
      speechGenerationController = null;
      if (activeSpeechSource) {{
        try {{ activeSpeechSource.stop(); }} catch {{}}
        activeSpeechSource = null;
      }}
      activeSpeechSources.forEach(source => {{ try {{ source.stop(); }} catch {{}} }});
      activeSpeechSources.clear();
      if (activeSpeechAudio) {{
        activeSpeechAudio.pause();
        activeSpeechAudio.currentTime = 0;
      }}
      activeSpeechAudio = null;
      if (activeSpeechUrl) URL.revokeObjectURL(activeSpeechUrl);
      activeSpeechUrl = '';
    }}
    async function playSpeechBlob(blob) {{
      const context = speechPlaybackContext;
      if (context && context.state === 'running') {{
        try {{
          const audioBuffer = await context.decodeAudioData(await blob.arrayBuffer());
          const source = context.createBufferSource();
          source.buffer = audioBuffer;
          source.connect(context.destination);
          activeSpeechSource = source;
          activeSpeechSources.add(source);
          source.addEventListener('ended', () => {{
            activeSpeechSources.delete(source);
            if (activeSpeechSource === source) {{
              activeSpeechSource = null;
              setState(liveVoiceEnabled ? 'listening' : 'ready', 'ok');
            }}
          }}, {{once: true}});
          source.start();
          return;
        }} catch {{}}
      }}
      activeSpeechUrl = URL.createObjectURL(blob);
      activeSpeechAudio = new Audio(activeSpeechUrl);
      const audio = activeSpeechAudio;
      audio.preload = 'auto';
      audio.playsInline = true;
      const cleanup = () => {{
        if (activeSpeechAudio === audio) {{
          activeSpeechAudio = null;
          if (activeSpeechUrl) URL.revokeObjectURL(activeSpeechUrl);
          activeSpeechUrl = '';
          setState(liveVoiceEnabled ? 'listening' : 'ready', 'ok');
        }}
      }};
      audio.addEventListener('ended', cleanup, {{once: true}});
      audio.addEventListener('error', cleanup, {{once: true}});
      await audio.play();
    }}
    async function playPcmSpeechStream(response, controller, sampleRate) {{
      const context = unlockSpeechPlayback();
      if (context && context.state === 'suspended') await context.resume();
      if (!context || context.state !== 'running' || !response.body) throw new Error('Browser audio is locked; click Test voice once to enable playback');
      const reader = response.body.getReader();
      let remainder = new Uint8Array(0);
      let nextStart = context.currentTime + 0.06;
      let received = false;
      while (true) {{
        const part = await reader.read();
        if (part.done) break;
        if (controller.signal.aborted) {{ await reader.cancel(); return; }}
        let bytes = part.value;
        if (remainder.length) {{
          const joined = new Uint8Array(remainder.length + bytes.length);
          joined.set(remainder);
          joined.set(bytes, remainder.length);
          bytes = joined;
        }}
        const usable = bytes.length - (bytes.length % 2);
        remainder = usable < bytes.length ? bytes.slice(usable) : new Uint8Array(0);
        if (!usable) continue;
        const samples = usable / 2;
        const audioBuffer = context.createBuffer(1, samples, sampleRate);
        const channelData = audioBuffer.getChannelData(0);
        const view = new DataView(bytes.buffer, bytes.byteOffset, usable);
        for (let index = 0; index < samples; index += 1) channelData[index] = view.getInt16(index * 2, true) / 32768;
        const source = context.createBufferSource();
        source.buffer = audioBuffer;
        source.connect(context.destination);
        activeSpeechSources.add(source);
        source.addEventListener('ended', () => {{
          activeSpeechSources.delete(source);
          if (!activeSpeechSources.size && !speechGenerationController) setState(liveVoiceEnabled ? 'listening' : 'ready', 'ok');
        }}, {{once: true}});
        const startsAt = Math.max(nextStart, context.currentTime + 0.03);
        source.start(startsAt);
        nextStart = startsAt + audioBuffer.duration;
        if (!received) setState('speaking', 'ok');
        received = true;
      }}
      if (!received) throw new Error('TTS returned an empty PCM stream');
    }}
    async function speakText(text) {{
      if (!speechConfig.tts || !speechConfig.tts.enabled) {{
        setState('TTS disabled', 'error');
        return;
      }}
      if (liveVoiceEnabled && vadSpeechActive) return;
      stopActiveSpeech();
      const controller = new AbortController();
      speechGenerationController = controller;
      try {{
        setState('generating speech');
        const streamAudio = Boolean(speechConfig.tts.streaming);
        const requestBody = {{input: String(text || ''), model: speechConfig.tts.model, voice: speechConfig.tts.voice, language: speechConfig.tts.language, response_format: streamAudio ? 'pcm' : (speechConfig.tts.response_format || 'wav')}};
        if (streamAudio) Object.assign(requestBody, {{stream: true, stream_format: 'audio'}});
        const response = await fetch('/v1/audio/speech', {{
          method: 'POST',
          headers: {{'content-type': 'application/json', 'accept': 'audio/*'}},
          body: JSON.stringify(requestBody),
          signal: controller.signal,
        }});
        if (!response.ok) throw new Error(await response.text() || `HTTP ${{response.status}}`);
        if (streamAudio) {{
          await playPcmSpeechStream(response, controller, Number(speechConfig.tts.sample_rate || 24000));
          speechGenerationController = null;
          if (!activeSpeechSources.size) setState(liveVoiceEnabled ? 'listening' : 'ready', 'ok');
          return;
        }}
        const blob = await response.blob();
        if (!blob.size) throw new Error('TTS returned empty audio');
        if (blob.type && !blob.type.startsWith('audio/')) throw new Error(`TTS returned ${{blob.type}} instead of audio`);
        if (controller.signal.aborted) return;
        speechGenerationController = null;
        await playSpeechBlob(blob);
        setState('speaking', 'ok');
      }} catch (err) {{
        if (err && err.name === 'AbortError') return;
        speechGenerationController = null;
        setState('TTS error', 'error');
        addBubble('system', 'TTS failed: ' + String(err && err.message ? err.message : err));
      }}
    }}
    async function transcribeRecording(blob, populatePrompt = true, options = {{}}) {{
      if (!options.quiet) setState('transcribing');
      const audio_base64 = await fileToBase64(blob);
      const response = await fetch('/v1/audio/transcriptions', {{
        method: 'POST',
        headers: {{'content-type': 'application/json', 'accept': 'application/json'}},
        body: JSON.stringify({{audio_base64, filename: 'web-chat-recording.wav', content_type: blob.type || 'audio/wav', model: speechConfig.asr.model, language: speechConfig.asr.language}}),
        signal: options.signal,
      }});
      const text = await response.text();
      let data = {{}};
      try {{ data = text ? JSON.parse(text) : {{}}; }} catch {{}}
      if (!response.ok) throw new Error((data.error && (data.error.message || data.error)) || text || `HTTP ${{response.status}}`);
      const transcriptText = String(data.text || data.transcript || '').trim();
      if (!transcriptText) throw new Error('ASR returned no transcript');
      if (populatePrompt) {{
        prompt.value = prompt.value ? prompt.value + ' ' + transcriptText : transcriptText;
        prompt.focus();
      }}
      if (!options.quiet) setState('transcribed', 'ok');
      return transcriptText;
    }}
    function encodePcmWav(chunks, sampleRate) {{
      const sampleCount = chunks.reduce((total, chunk) => total + chunk.length, 0);
      const buffer = new ArrayBuffer(44 + sampleCount * 2);
      const view = new DataView(buffer);
      const writeAscii = (offset, value) => {{
        for (let index = 0; index < value.length; index += 1) view.setUint8(offset + index, value.charCodeAt(index));
      }};
      writeAscii(0, 'RIFF');
      view.setUint32(4, 36 + sampleCount * 2, true);
      writeAscii(8, 'WAVE');
      writeAscii(12, 'fmt ');
      view.setUint32(16, 16, true);
      view.setUint16(20, 1, true);
      view.setUint16(22, 1, true);
      view.setUint32(24, sampleRate, true);
      view.setUint32(28, sampleRate * 2, true);
      view.setUint16(32, 2, true);
      view.setUint16(34, 16, true);
      writeAscii(36, 'data');
      view.setUint32(40, sampleCount * 2, true);
      let offset = 44;
      chunks.forEach(chunk => chunk.forEach(rawSample => {{
        const sample = Math.max(-1, Math.min(1, rawSample));
        view.setInt16(offset, sample < 0 ? sample * 0x8000 : sample * 0x7fff, true);
        offset += 2;
      }}));
      return new Blob([buffer], {{type: 'audio/wav'}});
    }}
    function resetVadUtterance() {{
      vadSpeechActive = false;
      vadSpeechChunks = [];
      vadSpeechStartedAt = 0;
      vadLastVoiceAt = 0;
      vadVoicedSamples = 0;
    }}
    function setLiveTranscript(text, active = true) {{
      liveTranscript.textContent = text;
      liveTranscript.classList.toggle('active', Boolean(active && text));
    }}
    function requestLivePartial(now) {{
      if (!vadSpeechActive || livePartialInFlight || !audioContext) return;
      if (now - livePartialLastAt < 1200 || now - vadSpeechStartedAt < 900) return;
      const serial = liveUtteranceSerial;
      const blob = encodePcmWav(vadSpeechChunks.slice(), audioContext.sampleRate);
      livePartialInFlight = true;
      livePartialLastAt = now;
      transcribeRecording(blob, false, {{quiet: true}}).then(text => {{
        if (liveVoiceEnabled && vadSpeechActive && serial === liveUtteranceSerial) {{
          setLiveTranscript('Live: ' + text);
        }}
      }}).catch(() => {{
        // Partial transcription is best-effort; final transcription reports actionable errors.
      }}).finally(() => {{
        livePartialInFlight = false;
      }});
    }}
    function queueLiveUtterance(chunks, sampleRate, serial) {{
      if (!chunks.length) return;
      const blob = encodePcmWav(chunks, sampleRate);
      liveTranscriptionQueue = liveTranscriptionQueue.then(async () => {{
        const transcriptText = await transcribeRecording(blob, false);
        if (serial === liveUtteranceSerial) setLiveTranscript('Heard: ' + transcriptText);
        setState('sending voice');
        await sendMessage(transcriptText, [], {{inputMode: 'voice'}});
        if (liveVoiceEnabled) setState('listening', 'ok');
      }}).catch(err => {{
        setState('STT error', 'error');
        addBubble('system', 'STT failed: ' + String(err && err.message ? err.message : err));
      }});
    }}
    function finishVadUtterance() {{
      const chunks = vadSpeechChunks.slice();
      const sampleRate = audioContext ? audioContext.sampleRate : 48000;
      const serial = liveUtteranceSerial;
      const partialText = liveTranscript.textContent.replace(/^Live:\\s*/, '');
      setLiveTranscript(partialText ? 'Finalizing: ' + partialText : 'Finalizing speech...');
      resetVadUtterance();
      vadPreRollChunks = [];
      queueLiveUtterance(chunks, sampleRate, serial);
    }}
    function processVadFrame(event) {{
      if (!liveVoiceEnabled || !audioContext) return;
      const chunk = new Float32Array(event.inputBuffer.getChannelData(0));
      let sumSquares = 0;
      for (let index = 0; index < chunk.length; index += 1) sumSquares += chunk[index] * chunk[index];
      const rms = Math.sqrt(sumSquares / Math.max(1, chunk.length));
      const configuredThreshold = Number((speechConfig.asr && speechConfig.asr.vad_threshold) || 0.018);
      const threshold = Math.max(configuredThreshold, Math.min(0.08, vadNoiseFloor * 2.8));
      const voiceDetected = rms >= threshold;
      const now = performance.now();
      if (!vadSpeechActive) {{
        if (!voiceDetected) {{
          vadNoiseFloor = Math.max(0.002, Math.min(0.03, vadNoiseFloor * 0.98 + rms * 0.02));
          vadPreRollChunks.push(chunk);
          const maxPreRollSamples = audioContext.sampleRate * 0.25;
          let preRollSamples = vadPreRollChunks.reduce((total, item) => total + item.length, 0);
          while (preRollSamples > maxPreRollSamples && vadPreRollChunks.length > 1) preRollSamples -= vadPreRollChunks.shift().length;
          return;
        }}
        vadSpeechActive = true;
        liveUtteranceSerial += 1;
        livePartialLastAt = now;
        vadSpeechStartedAt = now;
        vadLastVoiceAt = now;
        vadVoicedSamples = chunk.length;
        vadSpeechChunks = vadPreRollChunks.concat([chunk]);
        vadPreRollChunks = [];
        stopActiveSpeech();
        setLiveTranscript('Listening to speech...');
        setState('hearing speech', 'ok');
        return;
      }}
      vadSpeechChunks.push(chunk);
      if (voiceDetected) {{
        vadLastVoiceAt = now;
        vadVoicedSamples += chunk.length;
      }}
      const silenceMs = Number((speechConfig.asr && speechConfig.asr.silence_ms) || 900);
      const minSpeechMs = Number((speechConfig.asr && speechConfig.asr.min_speech_ms) || 300);
      const voicedMs = vadVoicedSamples * 1000 / audioContext.sampleRate;
      const utteranceMs = now - vadSpeechStartedAt;
      requestLivePartial(now);
      if (now - vadLastVoiceAt >= silenceMs) {{
        if (voicedMs >= minSpeechMs) finishVadUtterance();
        else {{ resetVadUtterance(); setLiveTranscript('Listening...', liveVoiceEnabled); }}
      }} else if (utteranceMs >= 30000) {{
        finishVadUtterance();
      }}
    }}
    async function startVoiceInput() {{
      if (!await verifyRuntimeIdentity()) throw new Error('Runtime identity verification failed');
      if (!navigator.mediaDevices) throw new Error('This browser does not support microphone recording');
      const AudioContextClass = window.AudioContext || window.webkitAudioContext;
      if (!AudioContextClass) throw new Error('Live voice requires Web Audio support');
      mediaStream = await navigator.mediaDevices.getUserMedia({{audio: {{echoCancellation: true, noiseSuppression: true, autoGainControl: true}}}});
      audioContext = new AudioContextClass();
      if (audioContext.state === 'suspended') await audioContext.resume();
      audioInput = audioContext.createMediaStreamSource(mediaStream);
      audioProcessor = audioContext.createScriptProcessor(2048, 1, 1);
      resetVadUtterance();
      vadPreRollChunks = [];
      vadNoiseFloor = 0.006;
      liveVoiceEnabled = true;
      audioProcessor.onaudioprocess = processVadFrame;
      audioInput.connect(audioProcessor);
      audioProcessor.connect(audioContext.destination);
      micButton.textContent = 'Stop live voice';
      micButton.classList.add('recording');
      setLiveTranscript('Listening...');
      setState('listening', 'ok');
    }}
    async function stopVoiceInput() {{
      if (!liveVoiceEnabled) return;
      liveVoiceEnabled = false;
      if (vadSpeechActive && audioContext) {{
        const minSpeechMs = Number((speechConfig.asr && speechConfig.asr.min_speech_ms) || 300);
        if (vadVoicedSamples * 1000 / audioContext.sampleRate >= minSpeechMs) finishVadUtterance();
      }}
      resetVadUtterance();
      vadPreRollChunks = [];
      if (audioProcessor) {{ audioProcessor.onaudioprocess = null; audioProcessor.disconnect(); }}
      if (audioInput) audioInput.disconnect();
      if (audioContext) await audioContext.close();
      if (mediaStream) mediaStream.getTracks().forEach(track => track.stop());
      audioProcessor = null;
      audioInput = null;
      audioContext = null;
      mediaStream = null;
      stopActiveSpeech();
      setLiveTranscript('', false);
      micButton.textContent = 'Start live voice';
      micButton.classList.remove('recording');
      setState('ready');
    }}
    async function uploadAttachment(file) {{
      const content = await fileToBase64(file);
      const response = await fetch('/ca/channel/files', {{
        method: 'POST',
        headers: {{'content-type': 'application/json', 'accept': 'application/json'}},
        body: JSON.stringify({{
          channel,
          sender_id: 'web-user',
          recipients: ['all'],
          thread_id: sessionId,
          announce: false,
          name: file.name,
          content_type: file.type || 'application/octet-stream',
          encoding: 'base64',
          content
        }})
      }});
      const text = await response.text();
      let json = {{}};
      try {{ json = text ? JSON.parse(text) : {{}}; }} catch {{}}
      if (!response.ok || !json.ok) {{
        throw new Error(json.error || text || `Upload failed with HTTP ${{response.status}}`);
      }}
      return {{
        name: json.name,
        original_name: json.original_name || file.name,
        url: json.url,
        path: json.path,
        bytes: json.bytes,
        content_type: json.content_type || file.type || 'application/octet-stream'
      }};
    }}
    async function uploadAttachments(files) {{
      const uploads = [];
      for (const file of files) {{
        setState('uploading ' + file.name);
        uploads.push(await uploadAttachment(file));
      }}
      return uploads;
    }}
    function attachmentSummary(uploads) {{
      if (!uploads.length) return '';
      const lines = uploads.map(file => {{
        const label = file.original_name || file.name || 'file';
        const size = formatBytes(file.bytes);
        const type = file.content_type || 'application/octet-stream';
        const url = file.url || file.path || '';
        return '- [' + label + '](' + url + ') (' + size + ', ' + type + ') - router URL: ' + url;
      }});
      return 'Attached files:\\n' + lines.join('\\n');
    }}
    function buildOutboundText(text, uploads) {{
      const trimmed = String(text || '').trim();
      const summary = attachmentSummary(uploads);
      if (trimmed && summary) return trimmed + '\\n\\n' + summary;
      return trimmed || summary;
    }}
    function updateHistoryBounds(messages) {{
      if (!Array.isArray(messages) || messages.length === 0) return;
      const ids = messages.map(message => Number(message.id || 0)).filter(id => id > 0);
      if (!ids.length) return;
      const minId = Math.min(...ids);
      const maxId = Math.max(...ids);
      oldestId = oldestId ? Math.min(oldestId, minId) : minId;
      rememberLastId(maxId);
    }}
    async function fetchMessagePage(params) {{
      const query = new URLSearchParams({{
        channel,
        recipient: 'web',
        limit: String(HISTORY_PAGE_SIZE),
        ...params
      }});
      const response = await fetch('/ca/channel/messages?' + query.toString(), {{headers: {{'accept': 'application/json'}}}});
      if (!response.ok) throw new Error(await response.text() || `HTTP ${{response.status}}`);
      return await response.json();
    }}
    async function loadInitialHistory() {{
      try {{
        const json = await fetchMessagePage({{latest: '1'}});
        const messages = Array.isArray(json.messages) ? json.messages : [];
        messages.forEach(message => renderIncomingMessage(message, 'append'));
        updateHistoryBounds(messages);
        historyExhausted = messages.length < HISTORY_PAGE_SIZE;
      }} catch (err) {{
        addBubble('system', 'Could not load chat history: ' + String(err && err.message ? err.message : err));
      }}
    }}
    async function loadOlderHistory() {{
      if (historyLoading || historyExhausted || !oldestId) return;
      historyLoading = true;
      const previousHeight = transcript.scrollHeight;
      try {{
        const json = await fetchMessagePage({{before: String(oldestId)}});
        const messages = Array.isArray(json.messages) ? json.messages : [];
        if (!messages.length) {{
          historyExhausted = true;
          return;
        }}
        for (let i = messages.length - 1; i >= 0; i -= 1) {{
          renderIncomingMessage(messages[i], 'prepend');
        }}
        updateHistoryBounds(messages);
        historyExhausted = messages.length < HISTORY_PAGE_SIZE;
        transcript.scrollTop = transcript.scrollHeight - previousHeight;
      }} catch (err) {{
        addBubble('system', 'Could not load older history: ' + String(err && err.message ? err.message : err));
      }} finally {{
        historyLoading = false;
      }}
    }}
    async function startChannelStream() {{
      if (!await verifyRuntimeIdentity()) return;
      if (eventSource) eventSource.close();
      const url = `/ca/channel/stream?channel=${{encodeURIComponent(channel)}}&recipient=web&after=${{lastId}}&timeout=3600`;
      eventSource = new EventSource(url);
      eventSource.onopen = () => setState('listening', 'ok');
      eventSource.onmessage = ev => {{
        try {{
          const message = JSON.parse(ev.data);
          renderIncomingMessage(message);
        }} catch {{}}
      }};
      eventSource.onerror = () => {{
        if (eventSource) eventSource.close();
        setState('reconnecting');
        setTimeout(startChannelStream, 1200);
      }};
    }}
    async function sendMessage(text, files = [], options = {{}}) {{
      if (!await verifyRuntimeIdentity()) return;
      setState('queued');
      sendButton.disabled = true;
      attachButton.disabled = true;
      try {{
        const uploads = await uploadAttachments(files);
        const outboundText = buildOutboundText(text, uploads);
        const response = await fetch('/ca/channel/messages', {{
          method: 'POST',
          headers: {{'content-type': 'application/json', 'accept': 'application/json'}},
          body: JSON.stringify({{
            channel,
            sender_id: 'web-user',
            recipients: ['all'],
            delivery: ['llm', 'native'],
            thread_id: sessionId,
            kind: 'web_chat',
            message: outboundText,
            meta: {{
              source: 'ciel-runtime-web-chat',
              web_chat_session: sessionId,
              input_mode: options.inputMode || 'text',
              reply_channel: channel,
              reply_recipient: 'web',
              response_contract: {{version: 1, fields: ['spoken', 'overview', 'details'], tts_field: 'spoken'}},
              reply_instruction: 'Acknowledge briefly first, then use the ciel-runtime-router send_message tool with response.spoken, response.overview, and optional response.details. The browser speaks only response.spoken. Use send_file when returning a file attachment.',
              attachments: uploads
            }}
          }})
        }});
        if (!response.ok) {{
          const fallback = await response.text();
          throw new Error(fallback || `HTTP ${{response.status}}`);
        }}
        const json = await response.json();
        if (json.message) renderIncomingMessage(json.message);
        else addBubble('user', outboundText);
        addBubble('system', 'Message queued for the active coding-agent session. Waiting for a channel reply. If this never changes, restart Ciel Runtime so the session wake bridge is active.');
        setState('waiting for session');
      }} catch (err) {{
        const bubble = addBubble('assistant', String(err && err.message ? err.message : err));
        bubble.classList.add('error');
        setState('error', 'error');
      }} finally {{
        sendButton.disabled = false;
        attachButton.disabled = false;
        prompt.focus();
      }}
    }}
    composer.addEventListener('submit', ev => {{
      ev.preventDefault();
      const text = prompt.value.trim();
      const files = selectedFiles.slice();
      if (!text && !files.length) return;
      prompt.value = '';
      selectedFiles = [];
      renderAttachmentTray();
      sendMessage(text, files);
    }});
    prompt.addEventListener('keydown', ev => {{
      if (ev.key === 'Enter' && !ev.shiftKey) {{
        ev.preventDefault();
        composer.requestSubmit();
      }}
    }});
    document.addEventListener('pointerdown', unlockSpeechPlayback, {{capture: true}});
    document.addEventListener('keydown', unlockSpeechPlayback, {{capture: true}});
    attachButton.addEventListener('click', () => fileInput.click());
    micButton.addEventListener('click', async () => {{
      if (liveVoiceEnabled) {{
        await stopVoiceInput();
        return;
      }}
      try {{ await startVoiceInput(); }} catch (err) {{
        setState('microphone error', 'error');
        addBubble('system', 'Microphone failed: ' + String(err && err.message ? err.message : err));
      }}
    }});
    speechSettingsButton.addEventListener('click', async () => {{
      try {{ await loadSpeechConfig(); }} catch (err) {{ addBubble('system', 'Could not load speech settings: ' + String(err && err.message ? err.message : err)); }}
      speechSettingsDialog.showModal();
    }});
    speechSettingsClose.addEventListener('click', () => speechSettingsDialog.close());
    document.getElementById('colabLoginButton').addEventListener('click', () => runColabAction('login').catch(err => addBubble('system', 'Colab login command failed: ' + String(err && err.message ? err.message : err))));
    document.getElementById('colabStatusButton').addEventListener('click', () => runColabAction('status').catch(err => addBubble('system', 'Colab status failed: ' + String(err && err.message ? err.message : err))));
    document.getElementById('colabStartButton').addEventListener('click', () => runColabAction('start').catch(err => addBubble('system', 'Colab start failed: ' + String(err && err.message ? err.message : err))));
    document.getElementById('colabDeployButton').addEventListener('click', () => runColabAction('deploy').catch(err => addBubble('system', 'Colab deployment failed: ' + String(err && err.message ? err.message : err))));
    document.getElementById('colabRecreateButton').addEventListener('click', () => {{
      if (!confirm('Release both sessions in this account profile, create new instances, and redeploy ASR/TTS?')) return;
      runColabAction('recreate').catch(err => addBubble('system', 'Colab recreation failed: ' + String(err && err.message ? err.message : err)));
    }});
    document.getElementById('ttsReferenceAudio').addEventListener('change', async event => {{
      const file = event.target.files && event.target.files[0];
      if (!file) return;
      if (file.size > 10 * 1024 * 1024) {{
        event.target.value = '';
        addBubble('system', 'Reference voice must be 10 MB or smaller.');
        return;
      }}
      pendingTtsReferenceAudio = await fileToDataUrl(file);
      document.getElementById('ttsClearReferenceAudio').checked = false;
      document.getElementById('ttsReferenceAudioStatus').textContent = file.name + ' (' + formatBytes(file.size) + ') ready to save';
    }});
    speechSettingsForm.addEventListener('submit', async event => {{
      event.preventDefault();
      try {{
        await saveSpeechConfig();
        speechSettingsDialog.close();
        setState('speech settings saved', 'ok');
      }} catch (err) {{
        setState('settings error', 'error');
        addBubble('system', 'Speech settings failed: ' + String(err && err.message ? err.message : err));
      }}
    }});
    speechHealthButton.addEventListener('click', async () => {{
      try {{
        await saveSpeechConfig();
        const response = await fetch('/ca/speech/health', {{headers: {{'accept': 'application/json'}}}});
        const data = await response.json();
        const asr = data.services && data.services.asr;
        const tts = data.services && data.services.tts;
        addBubble('system', `Speech health — ASR: ${{asr && asr.reachable ? 'reachable' : asr && asr.enabled ? 'unreachable' : 'disabled'}}, TTS: ${{tts && tts.reachable ? 'reachable' : tts && tts.enabled ? 'unreachable' : 'disabled'}}.`);
      }} catch (err) {{ addBubble('system', 'Speech health check failed: ' + String(err && err.message ? err.message : err)); }}
    }});
    speechPlaybackTestButton.addEventListener('click', async () => {{
      try {{
        await saveSpeechConfig();
        unlockSpeechPlayback();
        await speakText('음성 재생 테스트입니다.');
      }} catch (err) {{ addBubble('system', 'Voice playback test failed: ' + String(err && err.message ? err.message : err)); }}
    }});
    fileInput.addEventListener('change', () => {{
      addSelectedFiles(fileInput.files);
      fileInput.value = '';
    }});
    composer.addEventListener('dragover', ev => {{
      if (!ev.dataTransfer || !ev.dataTransfer.files || !ev.dataTransfer.files.length) return;
      ev.preventDefault();
      composer.classList.add('drop-active');
    }});
    composer.addEventListener('dragleave', () => composer.classList.remove('drop-active'));
    composer.addEventListener('drop', ev => {{
      if (!ev.dataTransfer || !ev.dataTransfer.files || !ev.dataTransfer.files.length) return;
      ev.preventDefault();
      composer.classList.remove('drop-active');
      addSelectedFiles(ev.dataTransfer.files);
    }});
    clearButton.addEventListener('click', () => {{
      transcript.innerHTML = '';
      renderedIds.clear();
      oldestId = 0;
      historyExhausted = false;
      selectedFiles = [];
      renderAttachmentTray();
      addBubble('system', `Chat cleared. This browser sends to active coding-agent session channel ${{channel}}.`);
      startChannelStream();
    }});
    shareButton.addEventListener('click', async () => {{
      const url = new URL(location.href);
      url.searchParams.set('session', sessionId);
      try {{
        await navigator.clipboard.writeText(url.toString());
        setState('link copied', 'ok');
      }} catch {{
        prompt.value = url.toString();
        prompt.focus();
        prompt.select();
        setState('copy manually');
      }}
    }});
    transcript.addEventListener('scroll', () => {{
      if (transcript.scrollTop < 48) loadOlderHistory();
    }});
    addBubble('system', `Connecting to runtime ${{EXPECTED_INSTANCE_ID}} for ${{MODEL}} on ${{EXPECTED_WORKSPACE}}.`);
    verifyRuntimeIdentity().then(ok => {{
      if (!ok) return;
      loadSpeechConfig().catch(() => {{ micButton.disabled = true; }});
      loadInitialHistory().finally(startChannelStream);
      prompt.focus();
      setInterval(() => verifyRuntimeIdentity({{announce: false}}), 5000);
    }});
  </script>
</body>
</html>"""

def render_router_home_page(
    *,
    version: str,
    provider: str,
    model: str,
    context_text: str,
    timeout_ms: int,
    idle_ms: int,
    rpm_text: str,
    upstream_text: str,
) -> str:
    links = [
        ("Events UI", "/ca/events", "Live router event stream with filters"),
        ("Session web chat", "/ca/web/chat", "Bridge messages into the active coding-agent session"),
        ("Recent events JSON", "/ca/events/recent", "Latest structured event records"),
        ("Events SSE", "/ca/events/stream", "Server-sent events stream"),
        ("Chat health", "/ca/chat/health", "Agent chat component status"),
        ("Chat messages", "/ca/chat/messages", "Stored agent chat messages"),
        ("Channel bridge", "/ca/channel/health", "External channel bridge API"),
        ("Channel messages", "/ca/channel/messages", "Messages posted through channel bridge"),
        ("Plan artifacts", "/ca/plan/artifacts", "Plan mode artifacts served by router"),
        ("Models", "/v1/models", "Claude-compatible model list"),
        ("Health", "/health", "Machine-readable health JSON"),
    ]
    link_html = "\n".join(
        f'<a class="link" href="{html_lib.escape(href)}"><strong>{html_lib.escape(label)}</strong><span>{html_lib.escape(desc)}</span></a>'
        for label, href, desc in links
    )
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Ciel Runtime Router</title>
  <style>
    :root {{ color-scheme: dark; font-family: ui-sans-serif, system-ui, -apple-system, Segoe UI, sans-serif; }}
    body {{ margin: 0; background: #090b0f; color: #e8edf4; }}
    header {{ padding: 22px 24px 16px; border-bottom: 1px solid #253044; background: #101722; }}
    h1 {{ margin: 0 0 6px; font-size: 24px; letter-spacing: 0; }}
    .sub {{ color: #a8b3c5; font-family: ui-monospace, SFMono-Regular, Consolas, monospace; }}
    .topnav {{ position: sticky; top: 0; z-index: 10; display: flex; gap: 6px; padding: 10px 24px; background: #0b111a; border-bottom: 1px solid #253044; overflow-x: auto; }}
    .tab, .chat-tab {{ box-sizing: border-box; min-width: 96px; min-height: 34px; border-radius: 6px; border: 1px solid #334155; background: #101722; color: #cbd5e1; cursor: pointer; }}
    .chat-tab {{ display: inline-flex; align-items: center; justify-content: center; padding: 6px 12px; border-color: #2563eb; background: #12304f; color: #eff6ff; font-weight: 700; text-decoration: none; white-space: nowrap; }}
    .chat-tab:hover {{ background: #17406a; border-color: #60a5fa; }}
    .tab:hover {{ border-color: #60a5fa; color: #eff6ff; }}
    .tab.active {{ background: #1d4ed8; border-color: #60a5fa; color: white; }}
    main {{ max-width: 1180px; margin: 0 auto; padding: 18px; }}
    .view {{ display: none; }}
    .view.active {{ display: block; }}
    .view h2 {{ margin: 0 0 12px; font-size: 20px; }}
    .grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(210px, 1fr)); gap: 10px; }}
    .card, .link, .events {{ background: #0d131d; border: 1px solid #253044; border-radius: 8px; padding: 12px; }}
    .label {{ color: #93a4ba; font-size: 12px; text-transform: uppercase; }}
    .value {{ margin-top: 5px; font-size: 15px; word-break: break-word; }}
    .overview-action {{ display: flex; align-items: center; justify-content: space-between; gap: 14px; margin-top: 14px; padding: 14px; border: 1px solid #1d4ed8; border-radius: 8px; background: #0d1b2d; }}
    .overview-action strong {{ display: block; color: #eff6ff; }}
    .overview-action span {{ display: block; margin-top: 4px; color: #a8b3c5; font-size: 13px; }}
    .overview-action .chat-tab {{ flex: 0 0 auto; }}
    .links {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(230px, 1fr)); gap: 10px; margin-top: 18px; }}
    a.link {{ display: block; color: #dbeafe; text-decoration: none; }}
    a.link:hover {{ border-color: #60a5fa; }}
    a.link span {{ display: block; margin-top: 4px; color: #93a4ba; font-size: 13px; }}
    .events {{ margin-top: 18px; }}
    .settings {{ background: #0d131d; border: 1px solid #253044; border-radius: 8px; padding: 12px; }}
    .settings h2, .events h2 {{ margin: 0 0 10px; font-size: 18px; }}
    .settings-grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(260px, 1fr)); gap: 10px; }}
    .control {{ display: grid; gap: 6px; }}
    .control label {{ color: #93a4ba; font-size: 12px; text-transform: uppercase; }}
    input, select, button {{ min-height: 34px; border-radius: 6px; border: 1px solid #334155; background: #080d14; color: #e8edf4; padding: 6px 8px; }}
    button {{ cursor: pointer; background: #12304f; border-color: #2563eb; }}
    button:hover {{ background: #17406a; }}
    .option-row {{ display: grid; grid-template-columns: minmax(240px, 1fr) minmax(160px, 260px) auto; gap: 8px; align-items: center; padding: 8px 0; border-top: 1px solid #1f2937; }}
    .option-row .name {{ color: #dbeafe; word-break: break-word; }}
    .messages {{ margin-top: 10px; color: #c4b5fd; font-family: ui-monospace, SFMono-Regular, Consolas, monospace; white-space: pre-wrap; }}
    .event {{ padding: 8px 0; border-top: 1px solid #1f2937; }}
    .event:first-child {{ border-top: 0; }}
    .meta {{ color: #93a4ba; font-family: ui-monospace, SFMono-Regular, Consolas, monospace; font-size: 12px; }}
    .preview {{ margin-top: 4px; color: #cbd5e1; font-family: ui-monospace, SFMono-Regular, Consolas, monospace; font-size: 12px; white-space: pre-wrap; word-break: break-word; }}
    code {{ color: #bfdbfe; }}
  </style>
</head>
<body>
  <header>
    <h1>Ciel Runtime Router</h1>
    <div class="sub">v{html_lib.escape(version)} · {html_lib.escape(provider)} · {html_lib.escape(model)}</div>
  </header>
  <nav class="topnav" aria-label="Router sections">
    <button class="tab active" data-view="overview">Overview</button>
    <a class="chat-tab" href="/ca/web/chat">Web Chat</a>
    <button class="tab" data-view="settings">LLM Settings</button>
    <button class="tab" data-view="events">Events</button>
    <button class="tab" data-view="endpoints">Endpoints</button>
  </nav>
  <main>
    <section id="view-overview" class="view active">
      <h2>Overview</h2>
      <div class="grid">
      <div class="card"><div class="label">Provider</div><div class="value">{html_lib.escape(provider)}</div></div>
      <div class="card"><div class="label">Model</div><div class="value">{html_lib.escape(model)}</div></div>
      <div class="card"><div class="label">Context</div><div class="value">{html_lib.escape(context_text)}</div></div>
      <div class="card"><div class="label">Timeout</div><div class="value">{timeout_ms:,} ms · idle {idle_ms:,} ms</div></div>
      <div class="card"><div class="label">RPM</div><div class="value">{html_lib.escape(rpm_text)}</div></div>
      <div class="card"><div class="label">Upstream</div><div class="value">{html_lib.escape(upstream_text)}</div></div>
      </div>
      <div class="overview-action">
        <div><strong>Session Web Chat</strong><span>Send messages and files to the active coding-agent session.</span></div>
        <a class="chat-tab" href="/ca/web/chat">Open Web Chat</a>
      </div>
    </section>
    <section id="view-settings" class="view">
      <h2>LLM Settings</h2>
      <div class="settings">
      <div class="settings-grid">
        <div class="control"><label>Model</label><input id="modelInput"><button id="modelApply">Apply model</button></div>
        <div class="control"><label>Advisor Model</label><input id="advisorInput" placeholder="off or model id"><button id="advisorApply">Apply advisor</button></div>
        <div class="control"><label>Preset</label><select id="presetSelect"></select><button id="presetApply">Apply preset</button></div>
        <div class="control"><label>Context Setup</label><select id="contextSelect"></select><button id="contextApply">Apply context</button></div>
        <div class="control"><label>Timeout Profile</label><select id="timeoutSelect"></select><button id="timeoutApply">Apply timeout</button></div>
      </div>
      <div id="optionRows"></div>
      <div id="settingsMessages" class="messages"></div>
      </div>
    </section>
    <section id="view-events" class="view events">
      <h2>Recent Events</h2>
      <div id="events"><div class="meta">Loading /ca/events/recent...</div></div>
    </section>
    <section id="view-endpoints" class="view">
      <h2>Endpoints</h2>
      <div class="links">{link_html}</div>
    </section>
  </main>
  <script>
    const tabs = Array.from(document.querySelectorAll('.tab'));
    const views = Array.from(document.querySelectorAll('.view'));
    function showView(name) {{
      tabs.forEach(tab => tab.classList.toggle('active', tab.dataset.view === name));
      views.forEach(view => view.classList.toggle('active', view.id === 'view-' + name));
      if (location.hash !== '#' + name) history.replaceState(null, '', '#' + name);
    }}
    tabs.forEach(tab => tab.addEventListener('click', () => showView(tab.dataset.view)));
    const initialView = (location.hash || '#overview').slice(1);
    if (tabs.some(tab => tab.dataset.view === initialView)) showView(initialView);
    const el = document.getElementById('events');
    const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({{'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}}[c]));
    const modelInput = document.getElementById('modelInput');
    const advisorInput = document.getElementById('advisorInput');
    const presetSelect = document.getElementById('presetSelect');
    const contextSelect = document.getElementById('contextSelect');
    const timeoutSelect = document.getElementById('timeoutSelect');
    const optionRows = document.getElementById('optionRows');
    const settingsMessages = document.getElementById('settingsMessages');
    function fillSelect(select, rows, current) {{
      select.innerHTML = (rows || []).map(item => `<option value="${{esc(item.value)}}">${{esc(item.label)}}</option>`).join('');
      if (current) select.value = current;
    }}
    async function loadSettings(messages = []) {{
      const res = await fetch('/ca/config/llm');
      const data = await res.json();
      modelInput.value = data.model || '';
      advisorInput.value = data.advisor_model || '';
      fillSelect(presetSelect, data.presets, data.preset);
      fillSelect(contextSelect, data.contexts);
      fillSelect(timeoutSelect, data.timeouts);
      optionRows.innerHTML = (data.options || []).map(item => `<div class="option-row"><div class="name">${{esc(item.label)}}</div><input data-key="${{esc(item.key)}}" value="${{esc(item.value)}}"><button data-key="${{esc(item.key)}}">Apply</button></div>`).join('');
      settingsMessages.textContent = (messages.length ? messages : data.messages || []).join('\\n');
    }}
    async function postSettings(payload) {{
      settingsMessages.textContent = 'Saving...';
      const res = await fetch('/ca/config/llm', {{method:'POST', headers:{{'content-type':'application/json'}}, body: JSON.stringify(payload)}});
      const data = await res.json();
      if (!res.ok || !data.ok) {{
        settingsMessages.textContent = (data.messages || [data.error || 'Update failed']).join('\\n');
        return;
      }}
      await loadSettings(data.messages || []);
    }}
    document.getElementById('modelApply').onclick = () => postSettings({{action:'model', value:modelInput.value}});
    document.getElementById('advisorApply').onclick = () => postSettings({{action:'advisor_model', value:advisorInput.value}});
    document.getElementById('presetApply').onclick = () => postSettings({{action:'preset', value:presetSelect.value}});
    document.getElementById('contextApply').onclick = () => postSettings({{action:'context_setup', value:contextSelect.value}});
    document.getElementById('timeoutApply').onclick = () => postSettings({{action:'timeout_profile', value:timeoutSelect.value}});
    optionRows.addEventListener('click', ev => {{
      const button = ev.target.closest('button[data-key]');
      if (!button) return;
      const input = optionRows.querySelector(`input[data-key="${{CSS.escape(button.dataset.key)}}"]`);
      postSettings({{action:'option', key:button.dataset.key, value:input ? input.value : ''}});
    }});
    loadSettings();
    fetch('/ca/events/recent?limit=20').then(r => r.json()).then(j => {{
      const events = j.events || [];
      el.innerHTML = events.length ? events.reverse().map(e => {{
        const preview = e.data && e.data.message_preview ? `<div class="preview">${{esc(e.data.message_preview)}}${{e.data.message_preview_truncated ? '…' : ''}}</div>` : '';
        return `<div class="event"><div class="meta">#${{e.id}} ${{esc(e.time)}} · ${{esc(e.level)}} · ${{esc(e.category)}} · ${{esc(e.provider)}} ${{esc(e.model)}}</div><div>${{esc(e.message)}}</div>${{preview}}</div>`;
      }}).join('') : '<div class="meta">No events yet.</div>';
    }}).catch(err => {{ el.innerHTML = '<div class="meta">Could not load events: ' + esc(err) + '</div>'; }});
  </script>
</body>
</html>"""

__all__ = ["render_router_home_page", "render_web_chat_page"]
