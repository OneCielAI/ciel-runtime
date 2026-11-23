"""Real claude.exe check of the "can't rule out a dangerous removal" prompt (2026-11-23).

usage: python e2e_waiver.py <source tree> <out dir> <variant>
  baseline  guard hooks as installed today (PermissionRequest included), option env on
  fixed     hooks and env produced by the fixed launcher code for the option on
  hookwaive waiver env on but the guard's PermissionRequest hook still installed

Isolated: own CLAUDE_CONFIG_DIR and CIEL_RUNTIME_CONFIG_DIR under D:/Temp, a stub
Anthropic API on 127.0.0.1 (no real credentials), every inherited CIEL_RUNTIME_*,
CLAUDE*, ANTHROPIC*, CODEX*, OPENAI* variable dropped. The stub answers the first
prompt with one Bash tool call carrying the command Celly's fork sent at
17:28:28Z (its cd path pointed at the scratch workspace; the script it runs only
reads files that do not exist there, so it fails before writing anything).
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

SOURCE = Path(sys.argv[1]).resolve()
OUT = Path(sys.argv[2]).resolve()
VARIANT = sys.argv[3]
OUT.mkdir(parents=True, exist_ok=True)
HERE = Path(__file__).resolve().parent
CLI = str(Path.home() / ".local/bin/claude.exe")
PREFIXES = ("CIEL_RUNTIME_", "CLAUDE", "ANTHROPIC", "CODEX", "OPENAI", "MAX_MCP")
sys.path[:0] = [str(SOURCE), str(HERE)]
from ciel_runtime_support import dangerous_rm_auto_allow as option  # noqa: E402
from ciel_runtime_support.settings_repository import JsonSettingsRepository, SettingsFileEffects  # noqa: E402
from ciel_runtime_support.tool_guard_hooks import (  # noqa: E402
    DEFAULT_TOOL_GUARD_HOOK_POLICY, ToolGuardHookServices, install_tool_guard_hook_settings)
from ciel_runtime_support.windows_conpty import WindowsConPtySession  # noqa: E402
from tinyterm import Screen  # noqa: E402

OWNED = Path(tempfile.mkdtemp(prefix="rm-waiver-", dir="D:/Temp"))
CFG, CIEL_CFG, WS = (OWNED / name for name in ("claude", "ciel", "workspace"))
for path in (CFG, CIEL_CFG, WS):
    path.mkdir(parents=True, exist_ok=True)
COMMAND = (HERE / "celly-command.txt").read_text(encoding="utf-8").replace(
    "G:/ciel-bridge-service/.claude/jobs/4ee83c21/tmp/n1389/base", str(WS).replace("\\", "/"), 1)
TOOL_ID = "toolu_e2e_waiver_1"
SEEN: list[dict] = []
LOCK = threading.Lock()


class Messages(BaseHTTPRequestHandler):
    def log_message(self, *_args):
        pass

    def _json(self, obj, code=200):
        data = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        self._json({"data": [], "has_more": False})

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get("content-length") or 0)) or b"{}")
        path = self.path.split("?")[0]
        if path.endswith("/count_tokens"):
            self._json({"input_tokens": 100})
            return
        if not path.endswith("/v1/messages"):
            self._json({})
            return
        msgs = body.get("messages") or []
        results = [b for m in msgs if isinstance(m.get("content"), list) for b in m["content"]
                   if isinstance(b, dict) and b.get("type") == "tool_result" and b.get("tool_use_id") == TOOL_ID]
        texts = " ".join(b.get("text", "") for m in msgs if m.get("role") == "user" and isinstance(m.get("content"), list)
                         for b in m["content"] if isinstance(b, dict) and b.get("type") == "text")
        texts += " ".join(m["content"] for m in msgs if m.get("role") == "user" and isinstance(m.get("content"), str))
        tools = [t.get("name") for t in body.get("tools") or [] if isinstance(t, dict)]
        with LOCK:
            SEEN.append({"t": round(time.time(), 1), "tools": len(tools), "has_bash": "Bash" in tools,
                         "run": "RUN-CMD" in texts, "tool_results": [
                             {"is_error": b.get("is_error"), "content": json.dumps(b.get("content"), ensure_ascii=False)[:600]}
                             for b in results]})
        if "RUN-CMD" in texts and not results and "Bash" in tools:
            block = {"type": "tool_use", "id": TOOL_ID, "name": "Bash", "input": {}}
            delta = {"type": "input_json_delta", "partial_json": json.dumps({"command": COMMAND, "description": "apply n1389 patch"})}
            stop = "tool_use"
        else:
            block = {"type": "text", "text": ""}
            delta = {"type": "text_delta", "text": "STUB-DONE"}
            stop = "end_turn"
        message = {"id": "msg_%d" % len(SEEN), "type": "message", "role": "assistant", "model": body.get("model") or "stub",
                   "content": [], "stop_reason": None, "stop_sequence": None,
                   "usage": {"input_tokens": 10, "output_tokens": 2}}
        if not body.get("stream"):
            final = dict(block)
            if final["type"] == "text":
                final["text"] = delta["text"]
            else:
                final["input"] = json.loads(delta["partial_json"])
            self._json({**message, "content": [final], "stop_reason": stop})
            return
        self.send_response(200)
        self.send_header("content-type", "text/event-stream")
        self.end_headers()
        for event in ({"type": "message_start", "message": message},
                      {"type": "content_block_start", "index": 0, "content_block": block},
                      {"type": "content_block_delta", "index": 0, "delta": delta},
                      {"type": "content_block_stop", "index": 0},
                      {"type": "message_delta", "delta": {"stop_reason": stop, "stop_sequence": None},
                       "usage": {"output_tokens": 2}},
                      {"type": "message_stop"}):
            self.wfile.write(f"event: {event['type']}\ndata: {json.dumps(event)}\n\n".encode())
            self.wfile.flush()


def sha(path: Path) -> str | None:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return None


def live_profile() -> dict:
    home = Path.home()
    return {name: sha(home / name) for name in (".claude/settings.json", ".claude.json")}


PROFILE_BEFORE = live_profile()
api = ThreadingHTTPServer(("127.0.0.1", 0), Messages)
threading.Thread(target=api.serve_forever, daemon=True).start()

env = {k: v for k, v in os.environ.items() if not k.upper().startswith(PREFIXES)}
env.update(CIEL_RUNTIME_TEST_ISOLATED="1", CIEL_RUNTIME_CONFIG_DIR=str(CIEL_CFG), CLAUDE_CONFIG_DIR=str(CFG),
           ANTHROPIC_BASE_URL=f"http://127.0.0.1:{api.server_port}", ANTHROPIC_AUTH_TOKEN="fixture",
           ANTHROPIC_API_KEY="", ANTHROPIC_MODEL="fixture-main", CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC="1",
           DISABLE_AUTOUPDATER="1", PYTHONIOENCODING="utf-8",
           # What Ciel's launch sets for a routed, bypass Claude session (claude_environment.py).
           CIEL_RUNTIME_PROVIDER="cielairouter", CIEL_RUNTIME_BYPASS_PERMISSIONS="1")
config = {option.CONFIG_KEY: True}
guard = SOURCE / "ciel-runtime-tool-guard.py"
guard_command = f'"{sys.executable.replace(chr(92), "/")}" "{str(guard).replace(chr(92), "/")}"'
repo = JsonSettingsRepository(path=CFG / "settings.json", effects=SettingsFileEffects(log=lambda *_a: None))
(CFG / "settings.json").write_text("{}", encoding="utf-8")
if VARIANT == "baseline":
    # Before the fix: the option exported only the guard switch and the guard
    # held PermissionRequest.
    env[option.ENV_NAME] = "1"
    without = False
elif VARIANT == "fixed":
    without = option.apply_launch_env(config, env)
elif VARIANT == "otherhook":
    # Fixed launcher output plus a third-party PermissionRequest hook that
    # answers nothing (Andy's profile carries one: orca-agent-hook.exe claude).
    without = option.apply_launch_env(config, env)
elif VARIANT == "hookwaive":
    option.apply_launch_env(config, env)
    without = False
else:
    raise SystemExit("unknown variant " + VARIANT)
install_tool_guard_hook_settings(guard_command, DEFAULT_TOOL_GUARD_HOOK_POLICY,
                                 ToolGuardHookServices(repository=repo, install_legacy_shim=lambda: None, warn=print),
                                 without_permission_request=without)
settings = json.loads((CFG / "settings.json").read_text(encoding="utf-8"))
if VARIANT == "otherhook":
    other = OWNED / "other_hook.py"
    other.write_text("import sys\nsys.stdin.read()\n", encoding="utf-8")
    settings.setdefault("hooks", {}).setdefault("PermissionRequest", []).append(
        {"matcher": "*", "hooks": [{"type": "command", "command": f'"{sys.executable.replace(chr(92), "/")}" "{str(other).replace(chr(92), "/")}"'}]})
    (CFG / "settings.json").write_text(json.dumps(settings), encoding="utf-8")
(CFG / ".claude.json").write_text(json.dumps({
    "hasCompletedOnboarding": True, "theme": "dark", "bypassPermissionsModeAccepted": True,
    "projects": {str(WS): {"hasTrustDialogAccepted": True}, str(WS).replace("\\", "/"): {"hasTrustDialogAccepted": True}}}),
    encoding="utf-8")
result = {"variant": VARIANT, "claude_version": subprocess.run([CLI, "--version"], capture_output=True, text=True,
                                                                timeout=60).stdout.strip(),
          "hook_events": sorted(settings.get("hooks", {})),
          "permission_request_hook": "PermissionRequest" in settings.get("hooks", {}),
          "env": {k: env.get(k) for k in (option.ENV_NAME, option.WAIVER_ENV_NAME, "CIEL_RUNTIME_BYPASS_PERMISSIONS")}}
DEBUG = OWNED / "claude-debug.txt"
screen = Screen(rows=50, cols=160)
proc = None
t0 = time.time()


def screen_text() -> str:
    raw = proc.output_tail().decode("utf-8", errors="replace") if proc else ""
    view = Screen(rows=50, cols=160)
    view.feed(raw)
    return view.text()


def wait_screen(pattern: str, timeout: float) -> bool:
    end = time.time() + timeout
    while time.time() < end:
        if re.search(pattern, screen_text()):
            return True
        time.sleep(0.5)
    return False


def save_png(name: str, text: str) -> None:
    from PIL import Image, ImageDraw, ImageFont
    lines = text.splitlines()[-48:] or [""]
    try:
        font = ImageFont.truetype("consola.ttf", 15)
    except OSError:
        font = ImageFont.load_default()
    image = Image.new("RGB", (160 * 9 + 20, len(lines) * 19 + 20), (18, 18, 18))
    draw = ImageDraw.Draw(image)
    for index, line in enumerate(lines):
        draw.text((10, 10 + index * 19), line, fill=(220, 220, 220), font=font)
    image.save(OUT / name)


try:
    os.chdir(WS)
    proc = WindowsConPtySession([CLI, "--dangerously-skip-permissions", "--permission-mode", "bypassPermissions",
                                 "--strict-mcp-config", "--debug-file", str(DEBUG)],
                                env, log=lambda *_: None, mirror_output=False, forward_stdin=False)
    result["ready"] = wait_screen(r"bypass permissions|⏵⏵|>\s*$", 90)
    time.sleep(4)
    (OUT / "screen-01-start.txt").write_text(screen_text(), encoding="utf-8")
    proc.write("RUN-CMD apply the patch".encode("utf-8"))
    time.sleep(1.0)
    proc.write(b"\r")
    end = time.time() + 120
    prompt_seen = False
    ran = False
    while time.time() < end:
        text = screen_text()
        if re.search(r"rule out a dangerous removal|Do you want to proceed\?", text):
            prompt_seen = True
            break
        with LOCK:
            if any(row["tool_results"] for row in SEEN):
                ran = True
                break
        time.sleep(0.5)
    time.sleep(3)
    final = screen_text()
    (OUT / "screen-02-after.txt").write_text(final, encoding="utf-8")
    save_png("screen-02-after.png", final)
    with LOCK:
        results = [r for row in SEEN for r in row["tool_results"]]
    result.update(prompt_seen=prompt_seen, tool_result_seen=bool(results) or ran,
                  tool_results=results[:2], requests=len(SEEN), elapsed_s=round(time.time() - t0, 1))
except Exception as error:
    result["failure"] = type(error).__name__ + ": " + str(error)[:400]
finally:
    if proc is not None:
        result["kill"] = subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"], capture_output=True,
                                        text=True).stdout.strip()[:200] if proc.poll() is None else "exited"
        try:
            proc.close()
        except Exception:
            pass
    api.shutdown()
    api.server_close()
    text = DEBUG.read_text(encoding="utf-8", errors="replace") if DEBUG.exists() else ""
    result["debug_lines"] = [line[:300] for line in text.splitlines()
                             if re.search(r"EVEN_MORE|removal|PermissionRequest|dangerous|safetyCheck", line)][-25:]
    result["profile_unchanged"] = PROFILE_BEFORE == live_profile()
    (OUT / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    shutil.rmtree(OWNED, ignore_errors=True)
    print(json.dumps({k: result.get(k) for k in ("variant", "claude_version", "permission_request_hook", "prompt_seen",
                                                  "tool_result_seen", "requests", "failure", "profile_unchanged")},
                     ensure_ascii=False))
