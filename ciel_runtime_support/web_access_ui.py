"""HTML for the web sign-in page and the admin page (OAuth tokens, access)."""

from __future__ import annotations

import json

_STYLE = """
:root { color-scheme: dark; font-family: ui-sans-serif, system-ui, -apple-system, Segoe UI, sans-serif; }
body { margin: 0; background: #090b0f; color: #e8edf4; }
header { padding: 18px 24px; border-bottom: 1px solid #253044; background: #101722; display: flex; justify-content: space-between; align-items: center; gap: 12px; }
h1 { margin: 0; font-size: 22px; }
h2 { margin: 0 0 12px; font-size: 18px; }
main { max-width: 1100px; margin: 0 auto; padding: 18px; }
.box { background: #0d131d; border: 1px solid #253044; border-radius: 8px; padding: 14px; margin-bottom: 14px; }
.row { display: flex; flex-wrap: wrap; gap: 8px; align-items: center; margin: 8px 0; }
input, select, button, textarea { min-height: 34px; border-radius: 6px; border: 1px solid #334155; background: #080d14; color: #e8edf4; padding: 6px 8px; font: inherit; }
textarea { width: 100%; min-height: 90px; font-family: ui-monospace, Consolas, monospace; font-size: 12px; box-sizing: border-box; }
button { cursor: pointer; background: #12304f; border-color: #2563eb; }
button:hover { background: #17406a; }
button.danger { background: #3b1219; border-color: #b91c1c; }
table { width: 100%; border-collapse: collapse; font-size: 14px; }
th, td { text-align: left; padding: 8px 6px; border-top: 1px solid #1f2937; vertical-align: top; }
th { color: #93a4ba; font-size: 12px; text-transform: uppercase; border-top: 0; }
.muted { color: #93a4ba; font-size: 13px; }
.msg { margin-top: 10px; color: #c4b5fd; font-family: ui-monospace, Consolas, monospace; white-space: pre-wrap; word-break: break-all; }
.tabs { display: flex; gap: 6px; padding: 10px 24px; background: #0b111a; border-bottom: 1px solid #253044; }
.tab { min-width: 110px; }
.tab.active { background: #1d4ed8; }
.view { display: none; } .view.active { display: block; }
a { color: #93c5fd; }
code { color: #bfdbfe; word-break: break-all; }
"""


def render_login_page(next_path: str, has_accounts: bool) -> str:
    notice = (
        ""
        if has_accounts
        else '<p class="muted">No web accounts exist yet. Create one in the ciel-runtime menu (Web access) or on the admin page from this machine.</p>'
    )
    return (
        """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>Ciel Runtime · Sign in</title>
<style>""" + _STYLE + """ .login { max-width: 380px; margin: 10vh auto; } .login input { width: 100%; box-sizing: border-box; margin-bottom: 10px; }</style></head>
<body><div class="login box"><h2>Ciel Runtime sign in</h2>""" + notice + """
<form id="f"><input id="email" type="email" placeholder="email" autocomplete="username" required>
<input id="password" type="password" placeholder="password" autocomplete="current-password" required>
<button type="submit">Sign in</button></form><div id="msg" class="msg"></div></div>
<script>
const nextPath = """ + json.dumps(next_path) + """;
document.getElementById('f').onsubmit = async ev => {
  ev.preventDefault();
  const res = await fetch('/ca/auth/login', {method: 'POST', headers: {'content-type': 'application/json'},
    body: JSON.stringify({email: email.value, password: password.value, next: nextPath})});
  const data = await res.json().catch(() => ({}));
  if (res.ok && data.ok) { location.href = data.next || '/ca/admin'; return; }
  document.getElementById('msg').textContent = data.message || 'Sign in failed.';
};
</script></body></html>"""
    )


def render_admin_page() -> str:
    return (
        """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>Ciel Runtime · Admin</title>
<style>""" + _STYLE + """</style></head><body>
<header><h1>Ciel Runtime Admin</h1><div class="row"><span id="who" class="muted"></span><a href="/">Router home</a><button id="logout">Sign out</button></div></header>
<nav class="tabs"><button class="tab active" data-view="tokens">OAuth tokens</button><button class="tab" data-view="access">Access</button></nav>
<main>
<section id="view-tokens" class="view active">
  <div class="box"><h2>Stored OAuth tokens</h2>
    <p class="muted">Tokens rotate in Codex routed and Anthropic routed modes. Changes apply to the next request of running agents.</p>
    <table><thead><tr><th>Provider</th><th>Label</th><th>State</th><th>Usage</th><th>ID</th><th></th></tr></thead><tbody id="tokens"></tbody></table>
  </div>
  <div class="box"><h2>Add by sign-in</h2>
    <div class="row"><select id="siProvider"><option value="codex">Codex</option><option value="claude">Claude</option></select>
      <input id="siLabel" placeholder="label (optional)"><button id="siStart">Start sign-in</button></div>
    <div id="siStep" style="display:none">
      <p class="muted">1. Open the link and sign in. 2. The browser ends on a localhost page that may not load; copy that page's full address. 3. Paste it here.</p>
      <p><a id="siLink" target="_blank" rel="noopener">Open the sign-in page</a></p>
      <div class="row"><input id="siRedirect" placeholder="http://localhost:.../callback?code=...&state=..." style="flex:1"><button id="siFinish">Finish</button></div>
    </div>
  </div>
  <div class="box"><h2>Import credentials</h2>
    <div class="row"><select id="imProvider"><option value="codex">Codex (auth.json)</option><option value="claude">Claude (.credentials.json)</option></select>
      <input id="imLabel" placeholder="label (optional)"></div>
    <textarea id="imContent" placeholder="paste the file contents"></textarea>
    <div class="row"><button id="imGo">Import</button></div>
  </div>
  <div id="tokMsg" class="msg"></div>
</section>
<section id="view-access" class="view">
  <div class="box"><h2>Admin API token</h2>
    <p class="muted">Remote API clients send it as <code>Authorization: Bearer &lt;token&gt;</code>. Rotating replaces it immediately; the new value is shown once.</p>
    <div class="row"><span id="tokenHint" class="muted"></span><button id="rotate" class="danger">Rotate token</button></div>
    <div id="newToken" class="msg"></div>
  </div>
  <div class="box"><h2>Web accounts</h2>
    <table><thead><tr><th>Email</th><th>Sessions</th><th>Password changed</th><th></th></tr></thead><tbody id="accounts"></tbody></table>
    <div class="row"><input id="acEmail" type="email" placeholder="email"><input id="acPassword" type="password" placeholder="password (8+)" autocomplete="new-password"><button id="acAdd">Add account</button></div>
    <div class="row"><button id="revokeAll" class="danger">Sign out every session</button></div>
  </div>
  <div id="accMsg" class="msg"></div>
</section>
</main>
<script>
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const when = t => t ? new Date(t * 1000).toLocaleString() : '';
function showView(name) {
  document.querySelectorAll('.tab').forEach(t => t.classList.toggle('active', t.dataset.view === name));
  document.querySelectorAll('.view').forEach(v => v.classList.toggle('active', v.id === 'view-' + name));
  history.replaceState(null, '', '#' + name);
}
document.querySelectorAll('.tab').forEach(tab => tab.onclick = () => showView(tab.dataset.view));
if (location.hash === '#access') showView('access');
async function call(path, payload) {
  const res = await fetch(path, payload ? {method: 'POST', headers: {'content-type': 'application/json'}, body: JSON.stringify(payload)} : {});
  if (res.status === 401) { location.href = '/ca/login?next=/ca/admin'; throw new Error('signed out'); }
  const data = await res.json().catch(() => ({ok: false, message: 'Bad response'}));
  if (!res.ok || data.ok === false) throw new Error(data.message || data.error || ('HTTP ' + res.status));
  return data;
}
const say = (id, text) => { document.getElementById(id).textContent = text; };
async function loadTokens() {
  const data = await call('/ca/oauth/tokens');
  document.getElementById('tokens').innerHTML = data.tokens.map(t => {
    const usage = Object.entries(t.usage || {}).map(([k, w]) => `${esc(k)} ${esc(w.used_percent)}%`).join(', ') || 'no usage yet';
    const on = t.status === 'active';
    return `<tr><td>${esc(t.provider)}</td><td><input data-label="${esc(t.token_id)}" value="${esc(t.label)}"></td><td>${esc(t.state)}${t.last_error ? '<div class="muted">' + esc(t.last_error) + '</div>' : ''}</td><td>${usage}</td><td><code>${esc(t.token_id)}</code></td>
      <td><button data-act="label" data-id="${esc(t.token_id)}">Save label</button> <button data-act="${on ? 'disable' : 'enable'}" data-id="${esc(t.token_id)}">${on ? 'Disable' : 'Enable'}</button> <button data-act="refresh" data-id="${esc(t.token_id)}">Refresh</button> <button class="danger" data-act="remove" data-id="${esc(t.token_id)}">Remove</button></td></tr>`;
  }).join('') || '<tr><td colspan="6" class="muted">No tokens yet.</td></tr>';
}
document.getElementById('tokens').onclick = async ev => {
  const b = ev.target.closest('button[data-act]'); if (!b) return;
  const id = b.dataset.id, act = b.dataset.act;
  try {
    if (act === 'remove' && !window.confirm('Remove token ' + id + '?')) return;
    let payload = {action: act, token_id: id};
    if (act === 'label') payload = {action: 'update', token_id: id, label: document.querySelector(`input[data-label="${CSS.escape(id)}"]`).value};
    if (act === 'enable' || act === 'disable') payload = {action: 'update', token_id: id, enabled: act === 'enable'};
    const data = await call('/ca/oauth/tokens', payload);
    say('tokMsg', act === 'refresh' ? `${id}: ${data.detail}` : `${id}: done.`);
  } catch (e) { say('tokMsg', e.message); }
  loadTokens().catch(e => say('tokMsg', e.message));
};
let signIn = null;
document.getElementById('siStart').onclick = async () => {
  try {
    const data = await call('/ca/oauth/tokens', {action: 'sign_in_start', provider: siProvider.value});
    signIn = data.sign_in_id; siLink.href = data.authorize_url; siStep.style.display = 'block';
    say('tokMsg', `Sign-in started; finish within ${Math.round(data.expires_in / 60)} minutes.`);
  } catch (e) { say('tokMsg', e.message); }
};
document.getElementById('siFinish').onclick = async () => {
  try {
    const data = await call('/ca/oauth/tokens', {action: 'sign_in_finish', sign_in_id: signIn, redirect: siRedirect.value, label: siLabel.value});
    siStep.style.display = 'none'; siRedirect.value = ''; say('tokMsg', `Stored ${data.token.token_id}.`); loadTokens();
  } catch (e) { say('tokMsg', e.message); }
};
document.getElementById('imGo').onclick = async () => {
  try {
    const data = await call('/ca/oauth/tokens', {action: 'import', provider: imProvider.value, content: imContent.value, label: imLabel.value});
    imContent.value = ''; say('tokMsg', `Stored ${data.token.token_id}.`); loadTokens();
  } catch (e) { say('tokMsg', e.message); }
};
async function loadAccess() {
  const data = await call('/ca/access');
  say('who', data.signed_in_as ? 'Signed in as ' + data.signed_in_as : 'Local access');
  say('tokenHint', data.admin_token.configured ? 'Current token ' + data.admin_token.hint : 'No admin token yet');
  document.getElementById('accounts').innerHTML = data.accounts.map(a => `<tr><td>${esc(a.email)}</td><td>${esc(a.active_sessions)}</td><td>${esc(when(a.password_changed_at))}</td>
    <td><button data-acc="reset" data-email="${esc(a.email)}">Reset password</button> <button class="danger" data-acc="remove" data-email="${esc(a.email)}">Remove</button></td></tr>`).join('') || '<tr><td colspan="4" class="muted">No accounts.</td></tr>';
}
document.getElementById('accounts').onclick = async ev => {
  const b = ev.target.closest('button[data-acc]'); if (!b) return;
  const email = b.dataset.email;
  try {
    if (b.dataset.acc === 'reset') {
      const password = window.prompt('New password for ' + email + ' (8+ characters)'); if (!password) return;
      await call('/ca/access', {action: 'reset_password', email, password}); say('accMsg', 'Password reset; its sessions were signed out.');
    } else {
      if (!window.confirm('Remove account ' + email + '?')) return;
      await call('/ca/access', {action: 'remove_account', email}); say('accMsg', 'Removed ' + email + '.');
    }
  } catch (e) { say('accMsg', e.message); }
  loadAccess().catch(e => say('accMsg', e.message));
};
document.getElementById('acAdd').onclick = async () => {
  try { await call('/ca/access', {action: 'add_account', email: acEmail.value, password: acPassword.value}); acPassword.value = ''; say('accMsg', 'Added ' + acEmail.value + '.'); loadAccess(); }
  catch (e) { say('accMsg', e.message); }
};
document.getElementById('rotate').onclick = async () => {
  if (!window.confirm('Rotate the admin API token? Remote clients using the old token stop working.')) return;
  try { const data = await call('/ca/access', {action: 'rotate_admin_token'}); say('newToken', 'New admin token (shown once): ' + data.admin_token); loadAccess(); }
  catch (e) { say('accMsg', e.message); }
};
document.getElementById('revokeAll').onclick = async () => {
  if (!window.confirm('Sign out every web session, including this one?')) return;
  try { const data = await call('/ca/access', {action: 'revoke_sessions'}); say('accMsg', `Signed out ${data.revoked} session(s).`); loadAccess(); }
  catch (e) { say('accMsg', e.message); }
};
document.getElementById('logout').onclick = async () => { await fetch('/ca/auth/logout', {method: 'POST'}); location.href = '/ca/login'; };
loadTokens().catch(e => say('tokMsg', e.message));
loadAccess().catch(e => say('accMsg', e.message));
</script></body></html>"""
    )


__all__ = ["render_admin_page", "render_login_page"]

