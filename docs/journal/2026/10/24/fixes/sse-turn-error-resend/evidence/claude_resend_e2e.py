"""E2E: real Claude Code (claude on PATH) transcripts drive the Claude resend path.

1. ``claude -p`` sends the real Ciel wake prompt for input 7 to a fake
   Anthropic upstream that answers 503, so Claude Code itself writes the
   ``isApiErrorMessage`` record.
2. That transcript is read by the production wake-state reader, the inflight
   state machine and the delivery repository (real status file): the input is
   queued for a resend, not recorded replied.
3. The production injector holds the resend until due, then builds it with the
   marker; ``claude -p --resume`` sends exactly that prompt and the upstream
   answers 200, so the transcript now shows the latest attempt completed.
4. A 400 run shows a non-transient error failing at once.

Everything lives in a temporary CLAUDE_CONFIG_DIR; inherited CIEL_RUNTIME_,
CLAUDE*, ANTHROPIC*, CODEX_ variables are removed and no router is started.
Usage: python claude_resend_e2e.py <worktree-root> <output-json>
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
from unittest.mock import patch

ROOT = Path(sys.argv[1]).resolve()
OUT = Path(sys.argv[2]).resolve()
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from ciel_runtime_support import runtime_input_retry as retry_policy  # noqa: E402
from ciel_runtime_support.channel_inflight import (  # noqa: E402
    ChannelInflightEffects, ChannelInflightPolicy, ChannelInflightSnapshot, advance_channel_inflight)
from ciel_runtime_support.channel_message_prompt import format_wake_batch_prompt  # noqa: E402
from ciel_runtime_support.channel_pending_injection import inject_pending_channel_messages  # noqa: E402
from ciel_runtime_support.channel_transcript import (  # noqa: E402
    ChannelWakeTranscriptServices, wake_state_evidence_from_text)
from ciel_runtime_support.channel_wake_claim_repository import (  # noqa: E402
    prompt_message_ids, prompt_references_message_id)
from ciel_runtime_support.channel_wake_delivery_repository import (  # noqa: E402
    ChannelWakeDeliveryRepository, DeliveryAttempts)
from ciel_runtime_support.runtime_input_status import RuntimeInputStatusRepository  # noqa: E402
from test_channel_pending_injection import _InjectionHarness  # noqa: E402

KEY = "E2E-CLAUDE"
plan: list[int] = []
upstream: list[dict] = []


def sse_ok(model: str) -> bytes:
    events = [
        ("message_start", {"type": "message_start", "message": {
            "id": "msg_e2e", "type": "message", "role": "assistant", "model": model, "content": [],
            "stop_reason": None, "stop_sequence": None, "usage": {"input_tokens": 10, "output_tokens": 1}}}),
        ("content_block_start", {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}}),
        ("content_block_delta", {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "done"}}),
        ("content_block_stop", {"type": "content_block_stop", "index": 0}),
        ("message_delta", {"type": "message_delta", "delta": {"stop_reason": "end_turn", "stop_sequence": None},
                           "usage": {"output_tokens": 1}}),
        ("message_stop", {"type": "message_stop"}),
    ]
    return "".join(f"event: {name}\ndata: {json.dumps(data)}\n\n" for name, data in events).encode()


class Upstream(BaseHTTPRequestHandler):
    def log_message(self, *_args):
        pass

    def do_GET(self):
        self.send_response(404)
        self.send_header("content-length", "0")
        self.end_headers()

    def do_POST(self):
        raw = self.rfile.read(int(self.headers.get("content-length") or 0))
        body = json.loads(raw or b"{}")
        main = KEY in raw.decode("utf-8", "replace")
        status = plan.pop(0) if main and plan else 200
        upstream.append({"at": round(time.time(), 1), "path": self.path, "main": main, "status": status,
                         "model": body.get("model"), "tools": len(body.get("tools") or []),
                         "messages": len(body.get("messages") or []), "stream": bool(body.get("stream")),
                         "max_tokens": body.get("max_tokens")})
        if status == 200:
            if body.get("stream"):
                payload, kind = sse_ok(str(body.get("model") or "m")), "text/event-stream"
            else:
                payload = json.dumps({"id": "msg_e2e", "type": "message", "role": "assistant",
                                      "model": str(body.get("model") or "m"), "content": [{"type": "text", "text": "ok"}],
                                      "stop_reason": "end_turn", "usage": {"input_tokens": 1, "output_tokens": 1}}).encode()
                kind = "application/json"
        else:
            kind = "application/json"
            error_type = "overloaded_error" if status == 529 else "api_error" if status >= 500 else "invalid_request_error"
            payload = json.dumps({"type": "error", "error": {"type": error_type, "message": f"fake upstream {status}"}}).encode()
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
    temp = Path(tempfile.mkdtemp(prefix="ciel-claude-resend-e2e-"))
    port = free_port()
    server = ThreadingHTTPServer(("127.0.0.1", port), Upstream)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    work = temp / "work"
    work.mkdir()
    env = {k: v for k, v in os.environ.items()
           if not k.startswith(("CIEL_RUNTIME_", "CLAUDE", "ANTHROPIC", "CODEX_", "OPENAI_"))}
    env.update(CLAUDE_CONFIG_DIR=str(temp / "claude"), ANTHROPIC_BASE_URL=f"http://127.0.0.1:{port}",
               ANTHROPIC_API_KEY="local-test", CLAUDE_CODE_MAX_RETRIES="0", DISABLE_AUTOUPDATER="1",
               CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC="1")
    claude = shutil.which("claude")

    def run_claude(prompt: str, resume: str = "") -> dict:
        args = [claude, "-p", prompt, "--output-format", "json", "--model", "claude-sonnet-4-5"]
        if resume:
            args += ["--resume", resume]
        done = subprocess.run(args, cwd=work, env=env, capture_output=True, text=True, encoding="utf-8",
                              errors="replace", timeout=180)
        try:
            return json.loads(done.stdout.strip().splitlines()[-1])
        except (ValueError, IndexError):
            return {"exit": done.returncode, "stdout": done.stdout[-400:], "stderr": done.stderr[-400:]}

    def transcript(session_id: str) -> str:
        found = list((temp / "claude" / "projects").rglob(f"{session_id}.jsonl"))
        return found[0].read_text(encoding="utf-8") if found else ""

    services = ChannelWakeTranscriptServices(
        claim_prompt=lambda _message_id: "", prompt_references_message_id=prompt_references_message_id,
        prompt_message_ids=prompt_message_ids, now=time.time)
    message = {"id": 7, "channel": "external:default", "kind": "external_event", "sender_id": "e2e",
               "message": f"{KEY} please answer", "meta": {"input_transport": "session_socket"},
               "delivery": ["llm"], "visibility": "private_runtime"}
    result: dict = {"claude": claude}
    try:
        # 1. First attempt: the upstream answers 503.
        plan[:] = [503]
        first = run_claude(format_wake_batch_prompt([message]))
        session_id = str(first.get("session_id") or "")
        text = transcript(session_id)
        records = [json.loads(line) for line in text.splitlines() if line.strip()]
        errors = [{key: record.get(key) for key in ("error", "apiErrorStatus", "apiErrorIsTransient",
                                                    "resendCannotCure", "truncatedAfterOutput")}
                  for record in records if record.get("isApiErrorMessage") is True]
        state = wake_state_evidence_from_text(7, text, None, services).state
        result["attempt_1"] = {"cli_result": {k: first.get(k) for k in ("is_error", "subtype", "result", "session_id")},
                               "api_error_records": errors, "wake_state": state}

        # 2. Production settlement: status file, delivery repository, inflight machine.
        status = RuntimeInputStatusRepository(temp / "runtime-input-status.jsonl", lambda **_kw: None,
                                              lambda *_a: None, threading.RLock())
        status.transition(7, "queued")
        delivery = ChannelWakeDeliveryRepository(
            lock=threading.RLock(), delivered=set(), prompts={}, batches={},
            clear_claim=lambda _message_id: None, commit_cursor=lambda _message_id: None,
            status=status, attempts=DeliveryAttempts(child_epoch="e2e"))
        delivery.begin_attempt([message])
        started = time.time() - 60
        commits: list[int] = []
        update = advance_channel_inflight(
            ChannelInflightSnapshot(7, 7, state, started_at=started, logged_at=started, now=time.time(), last_id=7),
            ChannelInflightPolicy(unseen_retry_seconds=20.0, waiting_log_interval=30.0,
                                  is_stale=lambda *_a: False, commit_cursor_on_stale=False,
                                  log_namespace="e2e", stale_event="stale"),
            ChannelInflightEffects(commit_cursor=commits.append, complete_wake=lambda _m: None,
                                   release_wake=lambda _m: None, ensure_cursor=lambda: 6, log=lambda *_a: None,
                                   turn_failed=lambda m, s, t: delivery.turn_failed(m, s, t)))
        row = status.get(7)
        result["settlement"] = {"inflight_action": update.action, "rescan_from": update.last_id,
                                "cursor_commits": commits, "status": row.get("status"), "reason": row.get("reason"),
                                "retry": row.get("data", {}).get("retry")}

        # 3. Production injector: held until due, then the marked resend.
        harness = _InjectionHarness([message])
        harness.delivery.status = status
        services_injection = harness.services()
        due = float(row["data"]["retry"]["due_epoch"])
        with patch("ciel_runtime_support.channel_pending_injection.time.time", return_value=due - 1):
            inject_pending_channel_messages(99, 6, None, services=services_injection)
        held = list(harness.written)
        with patch("ciel_runtime_support.channel_pending_injection.time.time", return_value=due + 0.1):
            inject_pending_channel_messages(99, 6, None, services=services_injection)
        resend_prompt = format_wake_batch_prompt([retry_policy.resend_message(message, row["data"]["retry"])])
        result["injector"] = {"written_before_due": held, "written_when_due": harness.written,
                              "status_after_send": status.get(7).get("status")}

        # 4. The resend goes to the same Claude session; the upstream answers 200.
        second = run_claude(resend_prompt, resume=session_id)
        text = transcript(session_id)
        result["attempt_2"] = {"cli_result": {k: second.get(k) for k in ("is_error", "subtype", "result")},
                               "prompt_first_line": resend_prompt.splitlines()[0][:200],
                               "wake_state": wake_state_evidence_from_text(7, text, None, services).state}

        # 5. A 400 is not resent.  Claude Code 2.1.310 itself sent a 400'd
        #    request once more in the first run (and got a 200), so every
        #    attempt of this turn answers 400 here.
        plan[:] = [400] * 12
        bad = dict(message, id=8)
        third = run_claude(format_wake_batch_prompt([bad]))
        text = transcript(str(third.get("session_id") or ""))
        state = wake_state_evidence_from_text(8, text, None, services).state
        sequence = []
        for line in text.splitlines():
            if line.strip():
                record = json.loads(line)
                content = (record.get("message") or {}).get("content")
                sequence.append({"type": record.get("type"), "api_error": record.get("isApiErrorMessage"),
                                 "error": record.get("error"), "status": record.get("apiErrorStatus"),
                                 "text": (content if isinstance(content, str) else json.dumps(content, ensure_ascii=False))[:90]
                                 if content is not None else None})
        result["bad_request"] = {"wake_state": state, "cli_result": {k: third.get(k) for k in ("is_error", "result")},
                                 "records": sequence}
        if retry_policy.is_failed_state(state):
            status.transition(8, "queued")
            delivery.begin_attempt([bad])
            result["bad_request"]["outcome"] = delivery.turn_failed(8, state, time.time() - 60)
            result["bad_request"]["status"] = status.get(8).get("status")
            result["bad_request"]["reason"] = status.get(8).get("reason")
    finally:
        server.shutdown()
    result["upstream_requests"] = upstream
    OUT.write_text(json.dumps(result, indent=1, ensure_ascii=False), encoding="utf-8")
    shutil.rmtree(temp, ignore_errors=True)
    print(json.dumps({key: result.get(key, {}).get("wake_state") for key in ("attempt_1", "attempt_2", "bad_request")},
                     ensure_ascii=False))


if __name__ == "__main__":
    main()
