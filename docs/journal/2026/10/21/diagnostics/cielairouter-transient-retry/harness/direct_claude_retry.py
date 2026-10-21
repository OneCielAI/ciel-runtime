"""Measure Claude Code's own retry behaviour against an owned Anthropic fixture.

Runs the installed CLI with ``--bare`` (only ANTHROPIC_API_KEY is read) and a
private HOME/config, pointed at a loopback /v1/messages fixture that rejects the
first N attempts with the exact CielAiRouter 503/SSE body captured in the
diagnosis.  The fixture counts real HTTP hits; the CLI's own retry count is the
only thing observed.  No Ciel Runtime code runs in this path.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

CAPTURED_BODY = (
    'event: error\n'
    'data: {"type":"error","error":{"type":"overloaded_error",'
    '"message":"service_unavailable: Service temporarily unavailable: '
    'all targets were skipped by pre-dispatch filters"}}\n\n'
).encode()
MESSAGE = {
    "id": "msg_fixture", "type": "message", "role": "assistant",
    "model": "claude-sonnet-5-5",
    "content": [{"type": "text", "text": "ok"}],
    "stop_reason": "end_turn", "stop_sequence": None,
    "usage": {"input_tokens": 1, "output_tokens": 1},
}


class Fixture:
    def __init__(self, failures: int, mode: str) -> None:
        self.failures = failures
        self.mode = mode
        self.hits = 0
        self.lock = threading.Lock()

    def next_verdict(self) -> bool:
        with self.lock:
            self.hits += 1
            return self.hits <= self.failures

    def handler(self):
        fixture = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *_args):
                pass

            def _body(self) -> dict:
                raw = self.rfile.read(int(self.headers.get("Content-Length", "0") or 0))
                try:
                    value = json.loads(raw or b"{}")
                except ValueError:
                    value = {}
                return value if isinstance(value, dict) else {}

            def _send(self, status: int, ctype: str, raw: bytes) -> None:
                self.send_response(status)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(raw)))
                self.send_header("Connection", "close")
                self.close_connection = True
                self.end_headers()
                self.wfile.write(raw)

            def do_GET(self):
                if self.path.startswith("/v1/models"):
                    self._send(200, "application/json", b'{"data":[],"has_more":false}')
                    return
                self._send(404, "application/json", b'{"error":{"type":"not_found"}}')

            def do_POST(self):
                body = self._body()
                if self.path.startswith("/v1/messages/count_tokens"):
                    self._send(200, "application/json", b'{"input_tokens":1}')
                    return
                if not self.path.startswith("/v1/messages"):
                    self._send(404, "application/json", b'{"error":{"type":"not_found"}}')
                    return
                if fixture.next_verdict():
                    if fixture.mode == "sse":
                        self._send(503, "text/event-stream", CAPTURED_BODY)
                    else:
                        self._send(503, "application/json", json.dumps({
                            "type": "error", "error": {
                                "type": "overloaded_error", "message": "fixture overload"}}).encode())
                    return
                if body.get("stream"):
                    self._send(200, "text/event-stream", _stream_body())
                else:
                    self._send(200, "application/json", json.dumps(MESSAGE).encode())

        return Handler


def _event(name: str, payload: dict) -> bytes:
    return f"event: {name}\ndata: {json.dumps(payload)}\n\n".encode()


def _stream_body() -> bytes:
    return b"".join([
        _event("message_start", {"type": "message_start", "message": {**MESSAGE, "content": [], "usage": {"input_tokens": 1, "output_tokens": 0}}}),
        _event("content_block_start", {"type": "content_block_start", "index": 0,
                                       "content_block": {"type": "text", "text": ""}}),
        _event("content_block_delta", {"type": "content_block_delta", "index": 0,
                                       "delta": {"type": "text_delta", "text": "ok"}}),
        _event("content_block_stop", {"type": "content_block_stop", "index": 0}),
        _event("message_delta", {"type": "message_delta",
                                 "delta": {"stop_reason": "end_turn", "stop_sequence": None},
                                 "usage": {"output_tokens": 1}}),
        _event("message_stop", {"type": "message_stop"}),
    ])


def claude_executable() -> str:
    local = Path.home() / ".local" / "bin" / ("claude.exe" if os.name == "nt" else "claude")
    if local.exists():
        return str(local)
    found = __import__("shutil").which("claude")
    if not found:
        raise SystemExit("claude CLI not found")
    return found


def run_case(failures: int, mode: str, timeout: float, retries: int | None = None) -> dict:
    fixture = Fixture(failures, mode)
    server = ThreadingHTTPServer(("127.0.0.1", 0), fixture.handler())
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True)
    thread.start()
    try:
        with tempfile.TemporaryDirectory(prefix="claude-direct-retry-") as root:
            home = Path(root) / "home"
            config = Path(root) / "claude"
            cwd = Path(root) / "cwd"
            for path in (home, config, cwd):
                path.mkdir(parents=True, exist_ok=True)
            env = {
                key: value for key, value in os.environ.items()
                if not key.upper().startswith(("CIEL_RUNTIME_", "CLAUDE", "ANTHROPIC_"))
            }
            env.update(
                HOME=str(home), USERPROFILE=str(home),
                CLAUDE_CONFIG_DIR=str(config),
                ANTHROPIC_BASE_URL=f"http://127.0.0.1:{server.server_port}",
                ANTHROPIC_API_KEY="fixture-key",
                NO_COLOR="1",
            )
            retry_env = {}
            if retries is not None:
                # Documented CLI controls; do not let watchdog/timeout/fallback
                # paths silently add replays outside the selected retry count.
                retry_env = {
                    "CLAUDE_CODE_MAX_RETRIES": str(retries),
                    "CLAUDE_CODE_RETRY_WATCHDOG": "0",
                    "CLAUDE_CODE_NONSTREAMING_TIMEOUT_RETRIES": "0",
                    "CLAUDE_CODE_DISABLE_NONSTREAMING_FALLBACK": "1",
                }
                env.update(retry_env)
            executable = claude_executable()
            version = subprocess.run(
                [executable, "--version"], cwd=cwd, env=env,
                capture_output=True, text=True, timeout=30,
            ).stdout.strip()
            started = __import__("time").monotonic()
            try:
                completed = subprocess.run(
                    [claude_executable(), "--bare", "-p", "Reply with the word ok.",
                     "--model", "claude-sonnet-5-5", "--max-turns", "1"],
                    cwd=cwd, env=env, capture_output=True, text=True, timeout=timeout,
                )
                outcome = {"returncode": completed.returncode,
                           "stdout_tail": completed.stdout.strip()[-400:],
                           "stderr_tail": completed.stderr.strip()[-400:]}
            except subprocess.TimeoutExpired as exc:
                outcome = {"returncode": None, "timeout": True,
                           "stdout_tail": (exc.stdout or "")[-400:] if isinstance(exc.stdout, str) else "",
                           "stderr_tail": (exc.stderr or "")[-400:] if isinstance(exc.stderr, str) else ""}
            outcome["elapsed_seconds"] = round(__import__("time").monotonic() - started, 2)
        with fixture.lock:
            outcome.update(case={"failures": failures, "mode": mode}, message_hits=fixture.hits,
                           configured_retries=retries, cli_version=version, retry_env=retry_env)
        return outcome
    finally:
        server.shutdown()
        server.server_close()
        thread.join(2)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--failures", type=int, action="append", default=None)
    parser.add_argument("--modes", default="sse,json")
    parser.add_argument("--retries", type=int, default=None,
                        help="Set documented Claude CLI retry environment controls")
    parser.add_argument("--timeout", type=float, default=120.0)
    parser.add_argument("--out", default="")
    args = parser.parse_args()
    failures = args.failures or [0, 1, 2, 3, 4, 6]
    results = []
    for mode in args.modes.split(","):
        for count in failures:
            result = run_case(count, mode.strip(), args.timeout, args.retries)
            results.append(result)
            print(json.dumps(result, ensure_ascii=False), flush=True)
    if args.out:
        output = Path(args.out)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(results, indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
