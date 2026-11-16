"""Owned probe: does claude.exe wait for a compaction response held longer than 10 minutes?

A loopback Anthropic stand-in answers every request at once, except the compaction
request (/compact), which gets HTTP 200 headers, a router-style keepalive comment
every 15 s for HOLD seconds, then a normal summary stream. One isolated claude.exe
(temp CLAUDE_CONFIG_DIR, stripped env, never the live router) first seeds a
conversation, then runs `-p --continue /compact`.

usage: python compact_hold_probe.py <out_dir> <hold_seconds> [env KEY=VALUE ...]
"""
from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

OUT = Path(sys.argv[1])
HOLD = float(sys.argv[2])
EXTRA = dict(arg.split("=", 1) for arg in sys.argv[3:])
OUT.mkdir(parents=True, exist_ok=True)
CLAUDE = Path(os.environ["USERPROFILE"]) / ".local" / "bin" / "claude.exe"
STRIP = ("CIEL_RUNTIME_", "CLAUDE", "ANTHROPIC", "CODEX", "OPENAI", "MAX_MCP")
LOG = OUT / "standin.jsonl"
lock = threading.Lock()
T0 = time.time()


def log(row):
    row = {"t": round(time.time() - T0, 1), **row}
    with lock, LOG.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(row, ensure_ascii=False) + "\n")


def frames(text):
    message = {"id": "msg_probe", "type": "message", "role": "assistant", "model": "claude-sonnet-5-5",
               "content": [], "stop_reason": None, "stop_sequence": None,
               "usage": {"input_tokens": 12, "output_tokens": 1}}
    yield "message_start", {"type": "message_start", "message": message}
    yield "content_block_start", {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}}
    yield "content_block_delta", {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": text}}
    yield "content_block_stop", {"type": "content_block_stop", "index": 0}
    yield "message_delta", {"type": "message_delta", "delta": {"stop_reason": "end_turn", "stop_sequence": None},
                            "usage": {"output_tokens": 20}}
    yield "message_stop", {"type": "message_stop"}


sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from ciel_runtime_support.context_summary_policy import compact_instruction_kind, native_compact_text  # noqa: E402


def is_compact(body):
    """The router's own native-compaction classifier: the newest user text block."""
    for message in reversed(body.get("messages") or []):
        if isinstance(message, dict) and message.get("role") == "user":
            text = native_compact_text(message.get("content"))
            if text.strip():
                return compact_instruction_kind(text) is not None
    return False


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):
        pass

    def do_HEAD(self):
        self.send_response(200); self.send_header("content-length", "0"); self.end_headers()

    def do_GET(self):
        log({"method": "GET", "path": self.path})
        body = b"{}"
        self.send_response(200); self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(body))); self.end_headers(); self.wfile.write(body)

    def do_POST(self):
        length = int(self.headers.get("content-length") or 0)
        raw = self.rfile.read(length)
        try:
            body = json.loads(raw or b"{}")
        except ValueError:
            body = {}
        if "count_tokens" in self.path:
            out = json.dumps({"input_tokens": 100}).encode()
            self.send_response(200); self.send_header("content-type", "application/json")
            self.send_header("content-length", str(len(out))); self.end_headers(); self.wfile.write(out)
            log({"path": self.path, "kind": "count_tokens"})
            return
        compact = is_compact(body)
        log({"path": self.path, "kind": "compact" if compact else "normal", "stream": body.get("stream"),
             "messages": len(body.get("messages") or []), "model": body.get("model"),
             "timeout_header": self.headers.get("x-stainless-timeout")})
        if not body.get("stream"):
            out = json.dumps({"id": "msg_probe", "type": "message", "role": "assistant", "model": "claude-sonnet-5-5",
                              "content": [{"type": "text", "text": "ok"}], "stop_reason": "end_turn",
                              "stop_sequence": None, "usage": {"input_tokens": 12, "output_tokens": 1}}).encode()
            self.send_response(200); self.send_header("content-type", "application/json")
            self.send_header("content-length", str(len(out))); self.end_headers(); self.wfile.write(out)
            return
        self.send_response(200)
        self.send_header("content-type", "text/event-stream")
        self.send_header("cache-control", "no-cache")
        self.send_header("transfer-encoding", "chunked")
        self.end_headers()

        def chunk(data: bytes):
            self.wfile.write(b"%x\r\n" % len(data) + data + b"\r\n"); self.wfile.flush()
        try:
            if compact:
                started = time.time()
                while time.time() - started < HOLD:
                    time.sleep(15)
                    chunk(b": ciel-runtime-keepalive\n\n")
                log({"event": "compact_hold_done", "held": round(time.time() - started, 1)})
                text = "<summary>Probe conversation summary: the user asked for one word and got it.</summary>"
            else:
                text = "pong"
            for name, data in frames(text):
                chunk(f"event: {name}\ndata: {json.dumps(data)}\n\n".encode())
            chunk(b"")
            log({"event": "responded", "kind": "compact" if compact else "normal"})
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError, OSError) as error:
            log({"event": "client_closed", "kind": "compact" if compact else "normal", "error": type(error).__name__})


def main():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0)); port = sock.getsockname()[1]
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    work = Path(tempfile.mkdtemp(prefix="compact-probe-"))
    config = work / "claude-config"; config.mkdir()
    cwd = work / "project"; cwd.mkdir()
    env = {k: v for k, v in os.environ.items() if not k.upper().startswith(STRIP)
           and not any(w in k.upper() for w in ("TOKEN", "SECRET", "PASSWORD", "API_KEY", "CREDENTIAL"))}
    env.update({"CLAUDE_CONFIG_DIR": str(config), "ANTHROPIC_BASE_URL": f"http://127.0.0.1:{port}",
                "ANTHROPIC_API_KEY": "probe-key", "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
                "DISABLE_AUTOUPDATER": "1", "CLAUDE_CODE_MAX_RETRIES": "0", **EXTRA})
    (OUT / "env.json").write_text(json.dumps({"hold_seconds": HOLD, "extra_env": EXTRA,
                                              "claude": str(CLAUDE)}, indent=1), encoding="utf-8")
    base = [str(CLAUDE), "-p", "--output-format", "stream-json", "--verbose", "--model", "claude-sonnet-5-5"]
    runs = [("seed", base + ["Reply with the single word: pong"]),
            ("compact", base + ["--continue", "/compact"])]
    summary = {}
    for name, cmd in runs:
        started = time.time()
        log({"event": "claude_start", "run": name})
        done = subprocess.run(cmd, cwd=cwd, env=env, capture_output=True, text=True, encoding="utf-8",
                              errors="replace", timeout=HOLD + 900)
        elapsed = round(time.time() - started, 1)
        (OUT / f"{name}.stdout.jsonl").write_text(done.stdout, encoding="utf-8")
        (OUT / f"{name}.stderr.txt").write_text(done.stderr, encoding="utf-8")
        result = None
        for line in done.stdout.splitlines():
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if row.get("type") == "result":
                result = {k: row.get(k) for k in ("subtype", "is_error", "result", "duration_ms", "num_turns")}
        summary[name] = {"exit": done.returncode, "seconds": elapsed, "result": result}
        log({"event": "claude_end", "run": name, "exit": done.returncode, "seconds": elapsed})
    transcripts = list((config / "projects").rglob("*.jsonl"))
    boundaries = 0
    for path in transcripts:
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            if '"compact_boundary"' in line:
                boundaries += 1
    summary["compact_boundaries_in_transcript"] = boundaries
    (OUT / "result.json").write_text(json.dumps(summary, indent=1, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False))
    server.shutdown()


if __name__ == "__main__":
    main()
