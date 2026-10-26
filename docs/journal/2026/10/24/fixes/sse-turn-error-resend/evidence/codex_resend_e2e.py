"""E2E: the real Codex app-server (codex-cli on PATH) behind a fake Responses upstream.

The Ciel injector under test delivers runtime inputs as a second JSON-RPC
client, exactly as in a desktop/remote session.  The upstream answers by plan:

A. message 1: 504, 504, 504, then a normal reply  -> 3 marked resends, replied
B. message 2: 400 bad request                     -> failed, no resend, message 3 delivered
C. message 4: 504 on every request                -> 10 resends, then retry_exhausted; message 5 delivered

No live router, transcript or remote host is touched: every path is under a
temporary directory and inherited CIEL_RUNTIME_/CODEX_/OPENAI_ variables are
removed.  Usage: python codex_resend_e2e.py <worktree-root> <output-json>
"""
from __future__ import annotations

import json
import os
import shutil
import socket
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(sys.argv[1]).resolve()
OUT = Path(sys.argv[2]).resolve()
sys.path.insert(0, str(ROOT))

from ciel_runtime_support.codex_app_server import CodexAppServerClient  # noqa: E402
from ciel_runtime_support.codex_desktop_injection import (  # noqa: E402
    FULL_ACCESS, CodexAppServerChannelInjector, CodexDesktopChannelPorts)
from ciel_runtime_support.runtime_input_delivery_health import (  # noqa: E402
    delivery_health_text, runtime_input_delivery_health)
from ciel_runtime_support.runtime_input_retry import RetrySettings  # noqa: E402
from ciel_runtime_support.runtime_input_status import RuntimeInputStatusRepository  # noqa: E402

OK_EVENTS = [
    {"type": "response.created", "response": {"id": "resp-1"}},
    {"type": "response.output_item.done", "output_index": 0, "item": {
        "type": "message", "role": "assistant", "id": "msg-1",
        "content": [{"type": "output_text", "text": "done", "annotations": []}]}},
    {"type": "response.completed", "response": {"id": "resp-1", "usage": {
        "input_tokens": 10, "input_tokens_details": {"cached_tokens": 0}, "output_tokens": 1,
        "output_tokens_details": {"reasoning_tokens": 0}, "total_tokens": 11}}},
]
OK_BODY = "".join(f"event: {event['type']}\ndata: {json.dumps(event)}\n\n" for event in OK_EVENTS).encode()
upstream: list[dict] = []
plan: dict[str, list[int]] = {}
plan_lock = threading.Lock()


def marker_of(body: dict) -> str:
    """Which scenario input the request answers: the last user item decides."""
    users = [item for item in body.get("input") or [] if isinstance(item, dict) and item.get("role") == "user"]
    text = json.dumps(users[-1] if users else {}, ensure_ascii=False)
    for key in ("E2E-ONE", "E2E-TWO", "E2E-THREE", "E2E-FOUR", "E2E-FIVE"):
        if key in text:
            return key
    return "other"


class Upstream(BaseHTTPRequestHandler):
    def log_message(self, *_args):
        pass

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get("content-length") or 0)) or b"{}")
        key = marker_of(body)
        with plan_lock:
            queue = plan.get(key, [])
            status = queue.pop(0) if queue else 200
        users = [item for item in body.get("input") or [] if isinstance(item, dict) and item.get("role") == "user"]
        last = json.dumps(users[-1] if users else {}, ensure_ascii=False)
        marker = last.split("[ciel-runtime 재전송", 1)[1].split("]", 1)[0] if "[ciel-runtime 재전송" in last else ""
        upstream.append({"at": round(time.time(), 1), "path": self.path, "input": key, "status": status,
                         "resend_marker": marker.strip(), "first_line_kept": "[ciel-runtime external channel message]" in last})
        if status == 200:
            payload, kind = OK_BODY, "text/event-stream"
        else:
            payload = json.dumps({"error": {"message": f"fake upstream {status}", "type": "server_error"}}).encode()
            kind = "application/json"
        self.send_response(status)
        self.send_header("content-type", kind)
        self.send_header("content-length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def main() -> None:
    temp = Path(tempfile.mkdtemp(prefix="ciel-resend-e2e-"))
    port = free_port()
    server = ThreadingHTTPServer(("127.0.0.1", port), Upstream)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    env = {k: v for k, v in os.environ.items() if not k.startswith(("CIEL_RUNTIME_", "CODEX_", "OPENAI_", "CLAUDE", "ANTHROPIC"))}
    env.update(CODEX_HOME=str(temp / "codex-home"), FAKE_UPSTREAM_KEY="local-test")
    (temp / "codex-home").mkdir()
    (temp / "work").mkdir()
    args = ["-c", "model_provider=\"fake\"", "-c", "model_providers.fake.name=\"fake\"",
            "-c", f"model_providers.fake.base_url=\"http://127.0.0.1:{port}/v1\"",
            "-c", "model_providers.fake.wire_api=\"responses\"",
            "-c", "model_providers.fake.env_key=\"FAKE_UPSTREAM_KEY\"",
            "-c", "model_providers.fake.request_max_retries=0",
            "-c", "model_providers.fake.stream_max_retries=0",
            "-c", "model=\"gpt-5-codex\""]
    codex = shutil.which("codex")
    status = RuntimeInputStatusRepository(temp / "runtime-input-status.jsonl", lambda **_kw: None,
                                          lambda *_a: None, threading.RLock())
    messages: list[dict] = []
    cursor = [0]
    logs: list[str] = []
    notes: list[dict] = []
    clients: list[CodexAppServerClient] = []

    def connect() -> CodexAppServerClient:
        client = CodexAppServerClient.spawn(codex, args, env=env, cwd=temp / "work")
        clients.append(client)
        original = client.next_notification

        def tee(*, timeout=0.0):
            message = original(timeout=timeout)
            if message and message.get("method") in {"error", "turn/completed"}:
                params = message.get("params") or {}
                turn = params.get("turn") or {}
                notes.append({"at": round(time.time(), 1), "method": message["method"],
                              "status": turn.get("status"), "willRetry": params.get("willRetry"),
                              "error": params.get("error") or turn.get("error")})
            return message
        client.next_notification = tee  # type: ignore[method-assign]
        return client

    def log(level: str, text: str) -> None:
        logs.append(f"{time.strftime('%H:%M:%S')} {level} {text}")

    injector = CodexAppServerChannelInjector(
        connect,
        CodexDesktopChannelPorts(
            read_messages=lambda last_id, limit: [m for m in messages if m["id"] > last_id][:limit],
            read_cursor=lambda: cursor[0],
            commit_cursor=lambda message_id: cursor.__setitem__(0, max(cursor[0], message_id)),
            status=status, log=log, retry_settings=lambda: RetrySettings(),
        ),
        cwd=str(temp / "work"), version="e2e", permissions=FULL_ACCESS,
    )
    injector.open()

    def queue(message_id: int, key: str) -> None:
        messages.append({"id": message_id, "channel": "external:default", "kind": "external_event",
                         "message": f"{key} please answer", "sender_id": "e2e", "recipients": ["all"],
                         "meta": {"input_transport": "session_socket", "source": "ciel-runtime-external-event"},
                         "delivery": ["llm"], "visibility": "private_runtime"})
        status.transition(message_id, "queued")

    def run_until(predicate, seconds: float) -> bool:
        deadline = time.time() + seconds
        while time.time() < deadline:
            injector.poll_once()
            if predicate():
                return True
            time.sleep(0.25)
        return False

    def row(message_id: int) -> dict:
        return status.get(message_id) or {}

    result: dict = {"codex": codex, "scenarios": {}}
    try:
        plan["E2E-ONE"] = [504, 504, 504]
        started = time.time()
        queue(1, "E2E-ONE")
        ok = run_until(lambda: row(1).get("status") in {"replied", "failed"}, 120)
        result["scenarios"]["A_504x3_then_success"] = {"settled": ok, "seconds": round(time.time() - started, 1),
                                                       "final": row(1)}
        plan["E2E-TWO"] = [400]
        queue(2, "E2E-TWO")
        # Queued only after message 2 settled: two inputs queued together go
        # into one Codex turn and share its outcome (observed in the first run).
        run_until(lambda: row(2).get("status") == "failed", 60)
        queue(3, "E2E-THREE")
        ok = run_until(lambda: row(2).get("status") == "failed" and row(3).get("status") == "replied", 90)
        result["scenarios"]["B_400_then_next"] = {"settled": ok, "message_2": row(2), "message_3": row(3)}
        plan["E2E-FOUR"] = [504] * 20
        started = time.time()
        queue(4, "E2E-FOUR")
        queue(5, "E2E-FIVE")
        ok = run_until(lambda: row(4).get("status") == "failed" and row(5).get("status") == "replied", 240)
        result["scenarios"]["C_504_forever_then_next"] = {"settled": ok, "seconds": round(time.time() - started, 1),
                                                          "message_4": row(4), "message_5": row(5)}
        health = runtime_input_delivery_health(status, inputs_path=temp / "none.jsonl")
        result["health_text"] = delivery_health_text(health)
    finally:
        injector.stop()
        for client in clients:
            try:
                client.close()
            except Exception:
                pass
        server.shutdown()
    result["upstream_requests"] = upstream
    result["notifications"] = notes
    result["injector_log"] = [line for line in logs if "codex_desktop_channel" in line]
    OUT.write_text(json.dumps(result, indent=1, ensure_ascii=False), encoding="utf-8")
    shutil.rmtree(temp, ignore_errors=True)
    print(json.dumps({name: value.get("settled") for name, value in result["scenarios"].items()}))


if __name__ == "__main__":
    main()
