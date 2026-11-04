"""Owned loopback probe v2: can a dropped socket prompt be bridged to native /btw?

Arm A (socket-bridge): an interactive Claude Code process with an explicit
messaging socket receives one authenticated type=user frame `/btw ...`. A probe
plugin intercepts the non-composer prompt.submit, records the origin, schedules
`$.command.run({command:'btw'})` through `$.clock.after`, and returns {drop}.
Arm B (tty): a second process gets the same text typed into its composer.

Both arms record what reaches the loopback Anthropic gateway (tools offered,
message text) and the terminal output. No live router, provider, or user config
is used.
"""
import json, os, re, socket, sys, threading, time, uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[8]))
from ciel_runtime_support.windows_conpty import WindowsConPtySession

ROOT = Path(__file__).resolve().parent
CLI = "C:/Users/djlov/AppData/Roaming/npm/node_modules/@anthropic-ai/claude-code/bin/claude.exe"
PROD_REGISTER = Path(__file__).resolve().parents[8] / "ciel_runtime_support" / "claude_mods" / "ciel-work" / "hooks" / "register.js"

BRIDGE_JS = r'''
export function register(on) {
  on('session.start', async ($, e, next) => {
    try {
      const names = (await $.command.list()).map(c => c.name)
      await $.fs.write('btw-probe-commands.json', JSON.stringify({ names, has_btw: names.includes('btw') }))
    } catch (err) { await $.fs.write('btw-probe-commands.json', JSON.stringify({ error: String(err && err.message || err) })) }
    return next(e)
  })
  on('prompt.submit', async ($, e, next) => {
    const origin = e.origin?.kind || ''
    if (origin !== 'composer' && /^\/btw(?:\s|$)/.test(e.text || '')) {
      const args = e.text.replace(/^\/btw\s*/, '')
      await $.fs.write('btw-probe-intercept.json', JSON.stringify({ origin, text: e.text, args, scheduled_at: Date.now() }))
      $.clock.after(50, async () => {
        await $.fs.write('btw-probe-timer-fired.json', JSON.stringify({ fired_at: Date.now() }))
        try {
          const result = await $.command.run({ command: 'btw', args })
          await $.fs.write('btw-probe-command-result.json', JSON.stringify({ ok: true, result }))
        } catch (err) {
          await $.fs.write('btw-probe-command-result.json', JSON.stringify({ ok: false, error: String(err && err.message || err) }))
        }
      })
      return { drop: 'btw bridged to native side question' }
    }
    return next(e)
  })
}
'''


def make_server():
    requests = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args): pass

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers.get("content-length", 0))))
            if "count_tokens" in self.path:
                raw = json.dumps({"input_tokens": 100}).encode()
                self.send_response(200); self.send_header("content-length", str(len(raw))); self.end_headers(); self.wfile.write(raw); return
            messages = body.get("messages", [])
            text = json.dumps(messages, ensure_ascii=False)
            whole = json.dumps(body, ensure_ascii=False)
            last_user = ""
            for m in reversed(messages):
                if m.get("role") == "user":
                    content = m.get("content")
                    if isinstance(content, str):
                        last_user = content
                    elif isinstance(content, list):
                        last_user = " ".join(
                            str(b.get("text") or "") for b in content if isinstance(b, dict))
                    break
            requests.append({
                "path": self.path,
                "tools": [t.get("name") for t in body.get("tools", [])],
                "has_btw_text": "/btw" in text,
                "has_probe_question": "BTW-PROBE-QUESTION" in text,
                "has_peer_frame": "Another Claude session sent a message" in text,
                "mentions_side_question": "side question" in whole.lower() or "by the way" in whole.lower(),
                "last_user_excerpt": last_user[:1500],
                "message_roles": [m.get("role") for m in messages],
            })
            msg = {"id": "msg_" + uuid.uuid4().hex[:10], "type": "message", "role": "assistant", "model": body.get("model"),
                   "content": [{"type": "text", "text": "BTW-PROBE-ANSWER"}], "stop_reason": "end_turn",
                   "usage": {"input_tokens": 100, "output_tokens": 10}}
            if not body.get("stream"):
                raw = json.dumps(msg).encode(); self.send_response(200); self.send_header("content-length", str(len(raw))); self.end_headers(); self.wfile.write(raw); return
            ev = [("message_start", {"type": "message_start", "message": {**msg, "content": [], "stop_reason": None}}),
                  ("content_block_start", {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}}),
                  ("content_block_delta", {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "BTW-PROBE-ANSWER"}}),
                  ("content_block_stop", {"type": "content_block_stop", "index": 0}),
                  ("message_delta", {"type": "message_delta", "delta": {"stop_reason": "end_turn", "stop_sequence": None}, "usage": {"output_tokens": 10}}),
                  ("message_stop", {"type": "message_stop"})]
            self.send_response(200); self.send_header("content-type", "text/event-stream"); self.send_header("connection", "close"); self.end_headers()
            for name, data in ev:
                self.wfile.write(("event: " + name + "\n" + "data: " + json.dumps(data) + "\n\n").encode()); self.wfile.flush()
            self.close_connection = True

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, requests


def base_env(port):
    env = {k: v for k, v in os.environ.items() if not k.upper().startswith(("CIEL_RUNTIME_", "CLAUDE", "ANTHROPIC", "CODEX", "OPENAI"))}
    env.update({"ANTHROPIC_BASE_URL": "http://127.0.0.1:" + str(port),
                "ANTHROPIC_AUTH_TOKEN": "fixture", "ANTHROPIC_API_KEY": "", "ANTHROPIC_MODEL": "fixture",
                "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1", "DISABLE_AUTOUPDATER": "1", "ENABLE_TOOL_SEARCH": "false"})
    return env


def arm_production_bridge():
    """Real production ciel-work register.js, loaded as an inline --plugin-dir.

    No router MCP is attached, so refresh() stays offline; the /btw bridge runs
    before any Work-status dependence, which is exactly the path under test.
    """
    run = ROOT / ("btw-prod-" + uuid.uuid4().hex[:8]); run.mkdir()
    cfg = run / "ccfg"; cfg.mkdir()
    ws = run / "workspace"; ws.mkdir()
    (cfg / ".claude.json").write_text(json.dumps({
        "hasCompletedOnboarding": True, "theme": "dark",
        "projects": {str(ws): {"hasTrustDialogAccepted": True},
                     str(ws).replace("\\", "/"): {"hasTrustDialogAccepted": True}},
    }), encoding="utf-8")
    plugin = run / "plugin"; (plugin / ".claude-plugin").mkdir(parents=True); (plugin / "hooks").mkdir()
    (plugin / ".claude-plugin/plugin.json").write_text(json.dumps({"name": "ciel-work", "version": "0.1.0"}), encoding="utf-8")
    (plugin / "hooks/hooks.json").write_text('{"modules":["./register.js"]}', encoding="utf-8")
    (plugin / "hooks/register.js").write_text(PROD_REGISTER.read_text(encoding="utf-8"), encoding="utf-8")
    server, requests = make_server()
    sock = rf"\\.\pipe\LOCAL\ciel-btw-{uuid.uuid4().hex[:16]}"
    env = base_env(server.server_port); env["CLAUDE_CONFIG_DIR"] = str(cfg)
    logs = []
    result = {"arm": "production-bridge", "run": str(run), "sent": False, "requests": requests}
    os.chdir(ws)
    session = WindowsConPtySession(
        [CLI, "--permission-mode", "default", "--messaging-socket-path", sock,
         "--plugin-dir", str(plugin), "--strict-mcp-config", "--mcp-config", '{"mcpServers":{}}'],
        env, log=lambda level, message: logs.append((level, message)), mirror_output=False, forward_stdin=False)
    try:
        deadline = time.time() + 45
        sessions = cfg / "sessions"
        while time.time() < deadline and session.poll() is None:
            candidates = list(sessions.glob("*.key")) if sessions.exists() else []
            if candidates:
                payload = json.loads(candidates[0].read_text(encoding="utf-8"))
                token = str(payload.get("peerToken") or "")
                if len(token) == 32:
                    frames = (json.dumps({"type": "auth", "token": token}) + "\n" + json.dumps({
                        "type": "user", "uuid": str(uuid.uuid4()),
                        "message": {"role": "user", "content": "/btw BTW-PROBE-QUESTION what is already known?"},
                        "priority": "next"}, ensure_ascii=False) + "\n").encode()
                    with open(sock, "wb", buffering=0) as pipe:
                        pipe.write(frames)
                    result["sent"] = True
                    break
            time.sleep(0.25)
        time.sleep(14)
        out = session.output_tail().decode("utf-8", errors="replace")
        (run / "stdout.txt").write_text(out, encoding="utf-8")
        result["request_count"] = len(requests)
        result["screen_contains_btw_answer"] = "BTW-PROBE-ANSWER" in out
        result["screen_clean_tail"] = clean(out)[-2500:]
        result["exit"] = session.poll()
    finally:
        try: session.close()
        finally: server.shutdown()
    (run / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result


def clean(text):
    text = re.sub(r"\x1b\[[0-9;?]*[A-Za-z]", " ", text)
    text = re.sub(r"\x1b\][^\x07]*\x07", " ", text)
    return re.sub(r"\s+", " ", text)


def arm_socket_bridge():
    run = ROOT / ("btw-bridge-" + uuid.uuid4().hex[:8]); run.mkdir()
    cfg = run / "ccfg"; cfg.mkdir()
    ws = run / "workspace"; ws.mkdir()
    (cfg / ".claude.json").write_text(json.dumps({
        "hasCompletedOnboarding": True, "theme": "dark",
        "projects": {str(ws): {"hasTrustDialogAccepted": True},
                     str(ws).replace("\\", "/"): {"hasTrustDialogAccepted": True}},
    }), encoding="utf-8")
    plugin = run / "plugin"; (plugin / ".claude-plugin").mkdir(parents=True); (plugin / "hooks").mkdir()
    (plugin / ".claude-plugin/plugin.json").write_text(json.dumps({"name": "btw-probe", "version": "0.2.0"}), encoding="utf-8")
    (plugin / "hooks/hooks.json").write_text('{"modules":["./register.js"]}', encoding="utf-8")
    (plugin / "hooks/register.js").write_text(BRIDGE_JS, encoding="utf-8")
    server, requests = make_server()
    sock = rf"\\.\pipe\LOCAL\ciel-btw-{uuid.uuid4().hex[:16]}"
    env = base_env(server.server_port); env["CLAUDE_CONFIG_DIR"] = str(cfg)
    logs = []
    result = {"arm": "socket-bridge", "run": str(run), "sent": False, "requests": requests}
    # The ConPty child inherits this process's cwd at spawn; it must start in
    # the workspace so the seeded trust entry matches and no dialog blocks.
    os.chdir(ws)
    session = WindowsConPtySession(
        [CLI, "--permission-mode", "default", "--messaging-socket-path", sock,
         "--plugin-dir", str(plugin), "--strict-mcp-config", "--mcp-config", '{"mcpServers":{}}'],
        env, log=lambda level, message: logs.append((level, message)), mirror_output=False, forward_stdin=False)
    try:
        deadline = time.time() + 45
        sessions = cfg / "sessions"
        while time.time() < deadline and session.poll() is None:
            candidates = list(sessions.glob("*.key")) if sessions.exists() else []
            if candidates:
                payload = json.loads(candidates[0].read_text(encoding="utf-8"))
                token = str(payload.get("peerToken") or "")
                if len(token) == 32:
                    frames = (json.dumps({"type": "auth", "token": token}) + "\n" + json.dumps({
                        "type": "user", "uuid": str(uuid.uuid4()),
                        "message": {"role": "user", "content": "/btw BTW-PROBE-QUESTION what is already known?"},
                        "priority": "next"}, ensure_ascii=False) + "\n").encode()
                    with open(sock, "wb", buffering=0) as pipe:
                        pipe.write(frames)
                    result["sent"] = True
                    break
            time.sleep(0.25)
        time.sleep(14)
        out = session.output_tail().decode("utf-8", errors="replace")
        (run / "stdout.txt").write_text(out, encoding="utf-8")
        for name in ("btw-probe-commands.json", "btw-probe-intercept.json", "btw-probe-timer-fired.json", "btw-probe-command-result.json"):
            f = ws / name
            result[name] = json.loads(f.read_text(encoding="utf-8")) if f.exists() else None
        result["request_count"] = len(requests)
        result["screen_contains_btw_answer"] = "BTW-PROBE-ANSWER" in out
        result["screen_clean_tail"] = clean(out)[-2500:]
        result["exit"] = session.poll()
    finally:
        try: session.close()
        finally: server.shutdown()
    (run / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result


def arm_tty():
    run = ROOT / ("btw-tty-" + uuid.uuid4().hex[:8]); run.mkdir()
    cfg = run / "ccfg"; cfg.mkdir()
    ws = run / "workspace"; ws.mkdir()
    (cfg / ".claude.json").write_text(json.dumps({
        "hasCompletedOnboarding": True, "theme": "dark",
        "projects": {str(ws): {"hasTrustDialogAccepted": True},
                     str(ws).replace("\\", "/"): {"hasTrustDialogAccepted": True}},
    }), encoding="utf-8")
    server, requests = make_server()
    env = base_env(server.server_port); env["CLAUDE_CONFIG_DIR"] = str(cfg)
    logs = []
    result = {"arm": "tty", "run": str(run), "typed": False, "requests": requests}
    os.chdir(ws)
    session = WindowsConPtySession(
        [CLI, "--permission-mode", "default",
         "--strict-mcp-config", "--mcp-config", '{"mcpServers":{}}'],
        env, log=lambda level, message: logs.append((level, message)), mirror_output=False, forward_stdin=False)
    try:
        # Wait for the interactive prompt to render, then type as the user would.
        deadline = time.time() + 45
        checkpoint = 0
        while time.time() < deadline and session.poll() is None:
            try:
                if session.wait_until_prompt_ready(timeout_seconds=2.0):
                    break
            except Exception:
                time.sleep(0.5)
        session.write("/btw BTW-PROBE-QUESTION what is already known?".encode())
        time.sleep(0.6)
        session.write(b"\r")
        result["typed"] = True
        time.sleep(14)
        out = session.output_tail().decode("utf-8", errors="replace")
        (run / "stdout.txt").write_text(out, encoding="utf-8")
        result["request_count"] = len(requests)
        result["screen_contains_btw_answer"] = "BTW-PROBE-ANSWER" in out
        result["screen_clean_tail"] = clean(out)[-2500:]
        result["exit"] = session.poll()
    finally:
        try: session.close()
        finally: server.shutdown()
    (run / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result


if __name__ == "__main__":
    which = sys.argv[1] if len(sys.argv) > 1 else "all"
    summary = {}
    if which in ("all", "bridge"):
        r = arm_socket_bridge()
        summary["bridge"] = {k: r.get(k) for k in ("run", "sent", "request_count", "screen_contains_btw_answer",
                                                   "btw-probe-commands.json", "btw-probe-intercept.json", "btw-probe-timer-fired.json", "btw-probe-command-result.json")}
        summary["bridge"]["requests"] = r["requests"]
    if which in ("all", "prod"):
        r = arm_production_bridge()
        summary["prod"] = {k: r.get(k) for k in ("run", "sent", "request_count", "screen_contains_btw_answer")}
        summary["prod"]["requests"] = r["requests"]
    if which in ("all", "tty"):
        r = arm_tty()
        summary["tty"] = {k: r.get(k) for k in ("run", "typed", "request_count", "screen_contains_btw_answer")}
    print(json.dumps(summary, ensure_ascii=False, indent=2))
