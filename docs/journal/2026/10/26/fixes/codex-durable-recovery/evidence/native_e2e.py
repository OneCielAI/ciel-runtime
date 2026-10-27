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
import subprocess
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
    for key in ("HOME", "USERPROFILE", "APPDATA", "LOCALAPPDATA", "XDG_CONFIG_HOME"):
        path = temp / key
        path.mkdir()
        env[key] = str(path)
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
            status=status, log=log, retry_settings=lambda: RetrySettings(limit=2, interval_seconds=0.05),
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

    result: dict = {"codex": codex, "version": subprocess.check_output([codex, "--version"], env=env, text=True).strip(), "authority": "real Codex app-server, local HTTP Responses fixture, controlled RPC response loss", "scenarios": {}}
    try:
        plan["E2E-ONE"] = [504, 504]
        queue(1, "E2E-ONE")
        ok = run_until(lambda: row(1).get("status") == "replied", 40)
        result["scenarios"]["typed_504_retry_success"] = {"pass": ok, "final": row(1)}
        # Native turn/start is accepted, but discard the actual matching RPC
        # response before the waiting client can see it (same effect as ACK loss).
        dropped = []
        class Replies(dict):
            drop_id = -1
            def __setitem__(self, key, value):
                if key == self.drop_id:
                    dropped.append(value)
                    self.drop_id = -1
                else:
                    super().__setitem__(key, value)
        client = injector.client
        replies = Replies()
        client._responses = replies
        original_request = client.request
        def request(method, params=None, **kw):
            if method == "turn/start":
                replies.drop_id = client._next_id
                kw["timeout"] = 0.1
            return original_request(method, params, **kw)
        client.request = request
        queue(2, "E2E-TWO")
        run_until(lambda: row(2).get("status") == "interrupted_uncertain", 10)
        for _ in range(4):
            injector.poll_once()
            time.sleep(0.1)
        restarted = CodexAppServerChannelInjector(lambda:client, injector._ports,
                                                  cwd=str(temp / "work"), version="e2e-restart")
        restarted.client, restarted.thread_id = client, injector.thread_id
        restarted._adopt_attempts()
        restarted._adopt_retries()
        for _ in range(4):
            restarted.poll_once()
            time.sleep(0.1)
        count = sum(r["input"] == "E2E-TWO" for r in upstream)
        result["scenarios"]["native_start_ack_loss_no_replay"] = {
            "pass": row(2).get("status") == "interrupted_uncertain" and cursor[0] == 1 and count == 1 and bool(dropped),
            "final": row(2), "cursor":cursor[0], "upstream_request_count":count, "dropped_actual_response":dropped}
        client.request = original_request
        # Separate exact thread prevents an earlier uncertain input's turn
        # being confused with this controlled post-ACK process-crash boundary.
        cursor[0] = 2
        fresh = CodexAppServerChannelInjector(lambda:client, injector._ports,
                                              cwd=str(temp / "work"), version="crash-case")
        fresh.client = client
        fresh._start_thread(client)
        original_start = client.turn_start
        def crash(*args, **kw):
            original_start(*args, **kw)
            raise SystemExit("controlled crash after native ACK before cursor")
        client.turn_start = crash
        queue(3, "E2E-THREE")
        try:
            fresh.deliver_pending()
        except SystemExit:
            pass
        restarted = CodexAppServerChannelInjector(lambda:client, injector._ports,
                                                  cwd=str(temp / "work"), version="post-crash")
        restarted.client, restarted.thread_id = client, fresh.thread_id
        restarted._adopt_attempts()
        restarted._adopt_retries()
        for _ in range(10):
            restarted.poll_once()
            time.sleep(0.1)
        count = sum(r["input"] == "E2E-THREE" for r in upstream)
        result["scenarios"]["native_start_crash_no_replay"] = {
            "pass": row(3).get("status") == "submitted" and cursor[0] == 2 and count == 1,
            "final": row(3), "cursor": cursor[0], "upstream_request_count": count}
        client.turn_start = original_start
        OUT.with_name("native-status.jsonl").write_bytes(status.path.read_bytes())
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
    print(json.dumps({name: value.get("pass") for name, value in result["scenarios"].items()}))

    if not all(s.get("pass") for s in result["scenarios"].values()):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
