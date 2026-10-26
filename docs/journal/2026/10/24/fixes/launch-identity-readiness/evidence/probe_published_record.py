"""Probe: what Claude Code 2.1.310 publishes while an idle --continue launch starts.

A real interactive ``claude --continue --messaging-socket-path <pipe>`` runs in
its own console with a temporary CLAUDE_CONFIG_DIR, a seeded prior session and a
fake Anthropic upstream. Nothing is typed. Every change of
``<config>/sessions/<pid>.json`` (sessionId, status) and every upstream request
is recorded with its time, plus console screens. Inherited CIEL_RUNTIME_,
CLAUDE*, ANTHROPIC*, CODEX*, OPENAI* variables are removed; no router runs.
Usage: python probe_published_record.py <output-dir> [seconds] [continue|resume|fresh]
"""
from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import sys
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[8]
sys.path.insert(0, str(ROOT))
from types import SimpleNamespace
from ciel_runtime_support.runtime_compaction_binding import RuntimeCompactionBinding
from ciel_runtime_support.claude_session_socket import ClaudeSessionSocketClient

OUT = Path(sys.argv[1]).resolve()
SECONDS = float(sys.argv[2]) if len(sys.argv) > 2 else 60.0
MODE = sys.argv[3] if len(sys.argv) > 3 else "continue"
OUT.mkdir(parents=True, exist_ok=True)
CLAUDE = shutil.which("claude.exe") or shutil.which("claude")
T0 = time.time()
upstream: list[dict] = []


def rel() -> float:
    return round(time.time() - T0, 2)


def sse_ok(model: str) -> bytes:
    events = [
        ("message_start", {"type": "message_start", "message": {
            "id": "msg_probe", "type": "message", "role": "assistant", "model": model, "content": [],
            "stop_reason": None, "stop_sequence": None, "usage": {"input_tokens": 10, "output_tokens": 1}}}),
        ("content_block_start", {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}}),
        ("content_block_delta", {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "ok"}}),
        ("content_block_stop", {"type": "content_block_stop", "index": 0}),
        ("message_delta", {"type": "message_delta", "delta": {"stop_reason": "end_turn", "stop_sequence": None},
                           "usage": {"output_tokens": 1}}),
        ("message_stop", {"type": "message_stop"}),
    ]
    return "".join(f"event: {n}\ndata: {json.dumps(d)}\n\n" for n, d in events).encode()


class Upstream(BaseHTTPRequestHandler):
    def log_message(self, *_args):
        pass

    def do_GET(self):
        upstream.append({"t": rel(), "method": "GET", "path": self.path})
        self.send_response(404)
        self.send_header("content-length", "0")
        self.end_headers()

    def do_POST(self):
        raw = self.rfile.read(int(self.headers.get("content-length") or 0))
        try:
            body = json.loads(raw or b"{}")
        except ValueError:
            body = {}
        upstream.append({"t": rel(), "method": "POST", "path": self.path,
                         "session_header": self.headers.get("x-claude-code-session-id", ""),
                         "messages": len(body.get("messages") or []), "stream": bool(body.get("stream")), "external_marker": b"READINESS-EXTERNAL-TEST" in raw})
        if body.get("stream"):
            payload, kind = sse_ok(str(body.get("model") or "m")), "text/event-stream"
        else:
            payload = json.dumps({"id": "msg_probe", "type": "message", "role": "assistant",
                                  "model": str(body.get("model") or "m"), "content": [{"type": "text", "text": "ok"}],
                                  "stop_reason": "end_turn", "usage": {"input_tokens": 1, "output_tokens": 1},
                                  "input_tokens": 1}).encode()
            kind = "application/json"
        self.send_response(200)
        self.send_header("content-type", kind)
        self.send_header("content-length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def main() -> None:
    for key in list(os.environ):
        if key.startswith(("CIEL_RUNTIME_", "CLAUDE", "ANTHROPIC", "CODEX", "OPENAI")):
            os.environ.pop(key)
    scratch = Path(os.environ["LOCALAPPDATA"]) / "Temp" / f"ciel-idle-probe-{int(T0)}"
    work, config = scratch / "ws", scratch / ".claude"
    work.mkdir(parents=True)
    config.mkdir()
    (config / ".claude.json").write_text(json.dumps({
        "hasCompletedOnboarding": True, "lastOnboardingVersion": "2.1.310", "numStartups": 3,
        "projects": {key: {"hasTrustDialogAccepted": True, "allowedTools": []}
                     for key in {str(work), work.as_posix(), str(work).lower(), work.as_posix().lower()}}}),
        encoding="utf-8")
    port = free_port()
    server = ThreadingHTTPServer(("127.0.0.1", port), Upstream)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    env = {k: v for k, v in os.environ.items()
           if not k.startswith(("CIEL_RUNTIME_", "CLAUDE", "ANTHROPIC", "CODEX", "OPENAI"))}
    env.update({"CLAUDE_CONFIG_DIR": str(config), "ANTHROPIC_BASE_URL": f"http://127.0.0.1:{port}",
                "ANTHROPIC_AUTH_TOKEN": "probe-token", "DISABLE_AUTOUPDATER": "1"})
    if MODE == "continue":
        # --continue does not select ``-p`` sessions; seed an interactive one.
        seeder = subprocess.Popen([CLAUDE], cwd=work, env=env, creationflags=subprocess.CREATE_NEW_CONSOLE)
        typed = OUT / "seed-steps.txt"
        helper = subprocess.Popen([sys.executable, "-I", str(Path(__file__).with_name("probe_seed.py")),
                                   str(seeder.pid), "seed turn", str(typed)], creationflags=0x00000008)
        helper.wait(timeout=180)
        subprocess.run(["taskkill", "/T", "/F", "/PID", str(seeder.pid)], capture_output=True)
        time.sleep(2)
        exit_note = typed.read_text(encoding="utf-8") if typed.exists() else "no-steps"
    elif MODE == "fresh":
        exit_note = "none"
    else:
        seed = subprocess.run([CLAUDE, "-p", "seed turn"], cwd=work, env=env, capture_output=True,
                              text=True, encoding="utf-8", errors="replace", timeout=180)
        exit_note = f"exit={seed.returncode}"
    seeded = sorted(p.stem for p in (config / "projects").rglob("*.jsonl"))
    upstream.append({"t": rel(), "method": "-", "path": f"seed {exit_note} transcripts={seeded}"})
    pipe = rf"\\.\pipe\LOCAL\cc-msg-probe{uuid.uuid4().hex[:12]}"
    launched_at = rel()
    resume = (["--resume", seeded[-1]] if MODE == "resume" and seeded
              else [] if MODE == "fresh" else ["--continue"])
    os.environ["CLAUDE_CONFIG_DIR"] = str(config)
    logs = []
    def log(level, text):
        logs.append({"t": rel(), "level": level, "text": text})
    budget = SimpleNamespace(compact_kind=lambda body: None, context_limit=lambda *a: 20000,
                             configured_output=lambda *a: 1000, cap_output_ratio=lambda *a: 1000,
                             reserve=lambda *a: 1000, positive_int=lambda v: int(v or 0))
    requests = SimpleNamespace(queue_automatic=lambda *a: None, clear=lambda *a: None)
    binding = RuntimeCompactionBinding(scratch / "binding", scratch, requests,
                lambda: SimpleNamespace(budget=budget), lambda body: 100,
                lambda: ("other", {}), log)
    binding.bind("claude", cwd=work, messaging_socket_path=pipe)
    sock = ClaudeSessionSocketClient(scratch, log)
    sock.configure(pipe)
    delivery = None
    child = subprocess.Popen([CLAUDE, *resume, "--messaging-socket-path", pipe], cwd=work, env=env,
                             creationflags=subprocess.CREATE_NEW_CONSOLE)
    watcher = subprocess.Popen([sys.executable, "-I", str(Path(__file__).with_name("probe_console.py")),
                                str(child.pid), str(OUT)], creationflags=0x00000008)
    sessions = config / "sessions"
    seen: dict[str, tuple] = {}
    changes = []
    end = time.time() + SECONDS
    while time.time() < end and child.poll() is None:
        for path in sorted(sessions.glob("*.json")) if sessions.exists() else []:
            try:
                record = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            key = (record.get("sessionId"), record.get("status"), record.get("statusUpdatedAt"),
                   bool(record.get("messagingSocketPath")))
            if seen.get(path.name) != key:
                seen[path.name] = key
                changes.append({"t": rel(), "file": path.name, "sessionId": record.get("sessionId"),
                                "status": record.get("status"), "statusUpdatedAt": record.get("statusUpdatedAt"),
                                "socket_matches": record.get("messagingSocketPath") == pipe,
                                "peerFeatures": record.get("peerFeatures")})
        if delivery is None and not binding.external_admission_paused():
            scope = binding.scope()
            sent = sock.send("READINESS-EXTERNAL-TEST: reply ok without tools")
            delivery = {"t": rel(), "sent": sent, "session": scope.session_id if scope else None,
                        "request_observed_before_send": binding.repository.load("probe").get("request_observed", False)}
        if delivery and any(u.get("external_marker") for u in upstream) and rel() - delivery["t"] > 4:
            break
        time.sleep(0.05)
    (OUT / "stop").write_text("stop", encoding="utf-8")
    subprocess.run(["taskkill", "/T", "/F", "/PID", str(child.pid)], capture_output=True)
    watcher.wait(timeout=30)
    server.shutdown()
    result = {"claude": CLAUDE, "mode": MODE, "child_pid": child.pid, "seeded_sessions": seeded, "launch_t": launched_at, "seconds": SECONDS,
              "record_changes": changes, "upstream": upstream, "delivery": delivery, "logs": logs,
              "scratch": str(scratch)}
    (OUT / "probe-result.json").write_text(json.dumps(result, indent=1), encoding="utf-8")
    print(json.dumps({"seeded": seeded, "changes": changes, "delivery": delivery,
                      "upstream_after_launch": [u for u in upstream if u["t"] >= launched_at]}, indent=1))


main()
