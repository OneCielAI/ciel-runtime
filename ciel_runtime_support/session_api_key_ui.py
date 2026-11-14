"""The dedicated web page for session API keys: ``GET /ca/session-api-keys/ui``.

Same sign-in as ``/ca/admin`` (the router's access check runs first).  The
page reads ``GET /ca/session-api-keys`` and posts actions with an
Idempotency-Key; it shows metadata only.  The key field is a password input
emptied before the request; no key is taken from the URL, rendered, or logged.
Router-wide notices (an unreadable marker file, full records) come first, with
the confirmed reset.

Every value from the server becomes a text node (``el`` below): the script
never assigns HTML (no innerHTML, outerHTML, insertAdjacentHTML or
document.write), so a provider, session, cause or source holding markup stays
inert text.
"""

from __future__ import annotations

from .web_access_ui import _STYLE

UI_PATH = "/ca/session-api-keys/ui"

_PAGE_STYLE = """
.notice { border: 1px solid #b91c1c; background: #2a0f14; color: #fecaca; border-radius: 8px; padding: 10px 12px; margin-bottom: 10px; }
.state { font-family: ui-monospace, Consolas, monospace; font-size: 12px; }
.warn { color: #fca5a5; }
@media (max-width: 640px) {
  .stack thead { display: none; }
  .stack, .stack tbody, .stack tr, .stack td { display: block; width: 100%; box-sizing: border-box; }
  .stack tr { border-top: 1px solid #1f2937; padding: 6px 0; }
  .stack td { border-top: 0; padding: 3px 6px; }
  .stack td[data-label]::before { content: attr(data-label); display: block; color: #93a4ba; font-size: 11px; text-transform: uppercase; }
  header { flex-wrap: wrap; }
}
"""

_SCRIPT = r"""
// Builds elements from text only: a string child becomes a text node.
function el(tag, props, ...kids) {
  const node = document.createElement(tag);
  for (const [name, value] of Object.entries(props || {})) {
    if (name === 'class') node.className = String(value);
    else node.setAttribute(name, String(value));
  }
  for (const kid of kids.flat()) {
    if (kid === null || kid === undefined || kid === false) continue;
    node.append(kid instanceof Node ? kid : String(kid));
  }
  return node;
}
const $ = id => document.getElementById(id);
const when = t => t ? new Date(t * 1000).toLocaleString() : '';
const age = s => { s = Number(s) || 0; return s < 120 ? s + 's' : s < 7200 ? Math.round(s / 60) + 'm' : Math.round(s / 3600) + 'h'; };
const say = text => { $('msg').textContent = String(text); };
let state = {sessions: [], keys: [], losses: [], staged: [], abandoned: [], notices: [], health: {}, reset: {}};
async function call(payload) {
  const init = payload ? {method: 'POST', headers: {'content-type': 'application/json',
    'idempotency-key': (crypto.randomUUID ? crypto.randomUUID() : String(Date.now()) + Math.random()).replace(/[^A-Za-z0-9._:-]/g, '')},
    body: JSON.stringify(payload)} : {};
  const res = await fetch('/ca/session-api-keys', init);
  if (res.status === 401) { location.href = '/ca/login?next=/ca/session-api-keys/ui'; throw new Error('signed out'); }
  const data = await res.json().catch(() => ({ok: false, message: 'Bad response'}));
  if (!res.ok || data.ok === false) throw new Error((data.error ? data.error + ': ' : '') + (data.message || ('HTTP ' + res.status)));
  return data;
}
function keyCell(k) {
  const parts = [el('span', {class: 'state'}, k.state || 'active'), ` · v${k.version} · `, k.source || '', ' · ',
                 k.fallback || 'fail_closed'];
  if (k.cause) parts.push(' · cause ', k.cause);
  if (k.fingerprint) parts.push(' · fp ', el('code', {}, k.fingerprint));
  parts.push(el('div', {class: 'muted'}, k.expires_at ? 'expires ' + when(k.expires_at) : 'lost ' + when(k.lost_at)));
  if (k.notice) parts.push(el('div', {class: 'warn'}, `${k.notice} · age ${age(k.age_seconds)}`));
  return parts;
}
function render(data) {
  state = data;
  const health = data.health || {};
  $('notices').replaceChildren(...(data.notices || []).map(n => el('div', {class: 'notice'}, n)));
  $('health').textContent = `records ${health.rows ?? 0} of ${health.limit ?? 0}`;
  $('resetBox').style.display = health.state_unreadable ? 'block' : 'none';
  $('resetWarning').textContent = String((data.reset || {}).warning || '');
  const rows = [];
  for (const s of data.sessions || []) {
    const keys = (s.keys || []).length ? s.keys : [null];
    for (const k of keys) rows.push(el('tr', {},
      el('td', {'data-label': 'Session'}, el('code', {}, s.session), el('div', {class: 'muted'}, s.runtime)),
      el('td', {'data-label': 'Last activity'}, when(s.last_activity)),
      el('td', {'data-label': 'Provider'}, k ? k.provider : (s.provider || '')),
      el('td', {'data-label': 'Key'}, k ? keyCell(k) : el('span', {class: 'muted'}, 'stored key')),
      el('td', {}, k ? el('button', {class: 'danger', type: 'button', 'data-clear': s.session,
                                     'data-provider': k.provider, 'data-version': k.version}, 'Clear') : '')));
  }
  if (!rows.length) rows.push(el('tr', {}, el('td', {colspan: 5, class: 'muted'}, 'No session has used this router recently.')));
  $('rows').replaceChildren(...rows);
  const pending = (data.staged || []).map(r => el('tr', {},
      el('td', {'data-label': 'State'}, el('span', {class: 'state'}, 'staged, awaiting first request')),
      el('td', {'data-label': 'Provider'}, r.provider), el('td', {'data-label': 'Source'}, r.source),
      el('td', {'data-label': 'Launcher'}, r.launcher_pid), el('td', {'data-label': 'Expires'}, when(r.expires_at))))
    .concat((data.abandoned || []).map(r => el('tr', {},
      el('td', {'data-label': 'State'}, el('span', {class: 'state warn'}, 'abandoned: scope rebound by another launch')),
      el('td', {'data-label': 'Provider'}, r.provider), el('td', {'data-label': 'Source'}, r.source),
      el('td', {'data-label': 'Launcher'}, r.launcher_pid), el('td', {'data-label': 'At'}, when(r.abandoned_at)))));
  if (!pending.length) pending.push(el('tr', {}, el('td', {colspan: 5, class: 'muted'}, 'None.')));
  $('pending').replaceChildren(...pending);
  const select = $('session'), current = select.value;
  select.replaceChildren(...(data.sessions || []).map(s => el('option', {value: s.session}, s.session)));
  if (current) select.value = current;
  if (!$('provider').value) $('provider').value = String(data.current_provider || '');
  if (!$('ttl').value) $('ttl').value = String((data.ttl_seconds || {}).default || 86400);
}
const load = async () => render(await call());
const version = (session, provider) => ((state.keys || []).concat(state.losses || [])
  .find(k => k.session === session && k.provider === provider) || {}).version;
async function put(action) {
  const input = $('key');
  const session = $('session').value, provider = $('provider').value.trim();
  const payload = {action, session, provider, ttl_seconds: Number($('ttl').value) || undefined,
    fallback: $('fallback').value, expected_version: action === 'inject' ? 0 : version(session, provider), api_key: input.value};
  input.value = '';
  try { const data = await call(payload); payload.api_key = '';
    say(`${data.action}: ${data.key.session} ${data.key.provider} v${data.key.version}`); render(data); }
  catch (e) { payload.api_key = ''; say(e.message); }
}
$('form').onsubmit = ev => { ev.preventDefault(); put('inject'); };
$('replace').onclick = () => put('replace');
$('rows').onclick = async ev => {
  const b = ev.target.closest('button[data-clear]'); if (!b) return;
  if (!window.confirm(`Clear the session key for ${b.dataset.clear} / ${b.dataset.provider}? This session then uses the stored key.`)) return;
  try { const data = await call({action: 'clear', session: b.dataset.clear, provider: b.dataset.provider, expected_version: Number(b.dataset.version)});
    say(`cleared: ${data.key.session} ${data.key.provider}; stored key in use`); render(data); }
  catch (e) { say(e.message); }
};
$('reset').onclick = async () => {
  const phrase = String((state.reset || {}).confirm || '');
  const typed = window.prompt(`${(state.reset || {}).warning || ''}\n\nType ${phrase} to reset.`);
  if (typed === null) return;
  if (typed.trim() !== phrase) { say('Not confirmed; nothing changed.'); return; }
  try { const data = await call({action: 'reset_state', confirm: phrase}); say('State reset; blocked sessions now use their stored key.'); render(data); }
  catch (e) { say(e.message); }
};
load().catch(e => say(e.message));
"""


def render_session_keys_page() -> str:
    return (
        """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>Session API Keys</title>
<style>""" + _STYLE + _PAGE_STYLE + """</style></head><body>
<header><h1>Session API keys</h1><div class="row"><span id="health" class="muted"></span><a href="/ca/admin">Admin</a><a href="/">Router home</a></div></header>
<main>
<div id="notices"></div>
<div id="resetBox" class="box" style="display:none"><h2>Reset session-key state</h2>
  <p id="resetWarning" class="muted"></p><div class="row"><button id="reset" class="danger" type="button">Reset state…</button></div></div>
<div class="box"><h2>Sessions (memory only)</h2>
  <p class="muted">A session key is used only by one running session for one provider. It lives in this router's memory, expires, and is never shown again. If it is lost (expired, router restart, abandoned) the session is blocked with an explicit error unless it was injected with fallback "stored"; clear it to use the stored key. Stored API keys (persistent: <code>api-key</code>, <code>set-api-key</code>, Management) are not changed here.</p>
  <div style="overflow-x:auto"><table class="stack"><thead><tr><th>Session</th><th>Last activity</th><th>Provider</th><th>Key</th><th></th></tr></thead><tbody id="rows"></tbody></table></div>
</div>
<div class="box"><h2>Launch keys</h2>
  <div style="overflow-x:auto"><table class="stack"><thead><tr><th>State</th><th>Provider</th><th>Source</th><th>Launcher</th><th>Time</th></tr></thead><tbody id="pending"></tbody></table></div>
</div>
<div class="box"><h2>Inject or replace</h2>
  <form id="form" autocomplete="off">
    <div class="row"><select id="session" aria-label="session" style="flex:1;min-width:220px"></select>
      <input id="provider" aria-label="provider" placeholder="provider" style="width:140px">
      <input id="ttl" aria-label="ttl seconds" type="number" min="60" max="86400" style="width:110px">
      <select id="fallback" aria-label="fallback"><option value="fail_closed">on loss: block</option><option value="stored">on loss: stored key</option></select></div>
    <div class="row"><input id="key" aria-label="api key" type="password" autocomplete="off" spellcheck="false" placeholder="API key (hidden, never shown again)" style="flex:1;min-width:220px">
      <button type="submit">Inject</button><button id="replace" type="button">Replace</button></div>
    <p class="muted">Replace and clear use the version shown, so a stale page cannot undo a newer key.</p>
  </form>
</div>
<div id="msg" class="msg"></div>
</main>
<script>""" + _SCRIPT + """</script></body></html>"""
    )


__all__ = ["UI_PATH", "render_session_keys_page"]
