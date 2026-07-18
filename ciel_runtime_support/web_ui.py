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
) -> str:
    escaped_model = html_lib.escape(model)
    escaped_provider = html_lib.escape(provider)
    escaped_mode = html_lib.escape(mode)
    escaped_api_status = html_lib.escape(api_status)
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
      </div>
      <div class="nav">
        <a href="/">Router Home</a>
        <a href="/ca/events">Events</a>
        <a href="/health">Health JSON</a>
        <button class="ghost" id="shareButton" type="button">Copy Chat Link</button>
        <button class="ghost" id="clearButton" type="button">Clear Chat</button>
      </div>
    </aside>
    <main>
      <header>
        <div>
          <h1>Session Web Chat</h1>
          <div class="sub">Send messages into the active Claude Code session through the Ciel Runtime channel bridge and stream replies from the same channel.</div>
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
          <button class="attach-button" id="attachButton" type="button">Attach files</button>
          <input id="fileInput" type="file" multiple>
          <div class="attachment-tray" id="attachmentTray" aria-live="polite"></div>
        </div>
        <div class="hint">Enter sends. Shift+Enter inserts a new line. The active Claude Code session handles the message, so its configured tools and MCP servers remain available. If replies stay queued, restart Ciel Runtime so the session wake bridge wraps the terminal.</div>
      </form>
    </main>
  </div>
  <script>
    const MODEL = {json.dumps(model)};
    const transcript = document.getElementById('transcript');
    const composer = document.getElementById('composer');
    const prompt = document.getElementById('prompt');
    const sendButton = document.getElementById('sendButton');
    const attachButton = document.getElementById('attachButton');
    const fileInput = document.getElementById('fileInput');
    const attachmentTray = document.getElementById('attachmentTray');
    const shareButton = document.getElementById('shareButton');
    const clearButton = document.getElementById('clearButton');
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
      if (mode === 'prepend') {{
        transcript.insertBefore(row, transcript.firstChild);
      }} else {{
        transcript.appendChild(row);
        transcript.scrollTop = transcript.scrollHeight;
      }}
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
      if (!text.trim()) return;
      addBubble(roleForMessage(message), text, mode, message.id);
      if (mode !== 'prepend' && message.sender_id !== 'web-user') setState('reply received', 'ok');
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
    function startChannelStream() {{
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
    async function sendMessage(text, files = []) {{
      setState('queued');
      sendButton.disabled = true;
      attachButton.disabled = true;
      try {{
        const uploads = await uploadAttachments(files);
        const outboundText = buildOutboundText(text, uploads);
        addBubble('user', outboundText);
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
              reply_channel: channel,
              reply_recipient: 'web',
              reply_instruction: 'Use the ciel-runtime-router send_message tool to answer this browser chat on the same channel/thread_id with recipients web and delivery web. Use send_file when returning a file attachment to this browser chat.',
              attachments: uploads
            }}
          }})
        }});
        if (!response.ok) {{
          const fallback = await response.text();
          throw new Error(fallback || `HTTP ${{response.status}}`);
        }}
        const json = await response.json();
        if (json.message) rememberLastId(json.message.id);
        addBubble('system', 'Message queued for the active Claude Code session. Waiting for a channel reply. If this never changes, restart Ciel Runtime so the session wake bridge is active.');
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
    attachButton.addEventListener('click', () => fileInput.click());
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
      addBubble('system', `Chat cleared. This browser sends to active Claude Code session channel ${{channel}}.`);
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
    addBubble('system', `Connected to active session bridge for ${{MODEL}}. Messages are queued on channel ${{channel}} and replies stream back from /ca/channel/stream.`);
    loadInitialHistory().finally(startChannelStream);
    prompt.focus();
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
        ("Session web chat", "/ca/web/chat", "Bridge messages into the active Claude Code session"),
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
    .tab {{ min-width: 96px; min-height: 34px; border-radius: 6px; border: 1px solid #334155; background: #101722; color: #cbd5e1; cursor: pointer; }}
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
