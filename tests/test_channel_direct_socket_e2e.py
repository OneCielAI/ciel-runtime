"""Real SSE and production delivery to an owned protocol fixture, not native CLI."""

import hashlib
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from ciel_runtime_support.channel_inflight import ChannelInflightEffects
from ciel_runtime_support.channel_message_policy import string_list
from ciel_runtime_support.channel_message_repository import ChannelMessageAppendPorts, ChannelMessageRepository, exclusive_file_lock
from ciel_runtime_support.channel_pending_injection import (
    ChannelInjectionIO, ChannelInjectionPolicy, ChannelInjectionPrompts,
    ChannelInjectionServices, ChannelInjectionState, ChannelInjectionWakeStore,
    inject_pending_channel_messages,
)
from ciel_runtime_support.channel_terminal_context import (
    ChannelTerminalCompatibilityApi, ChannelTerminalContext, ChannelTerminalDispatchPorts, ChannelTerminalIoPorts,
    ChannelTerminalPolicyPorts, ChannelTerminalPollingPorts,
    ChannelTerminalProcessPorts, ChannelTerminalWindowsPorts,
)
from ciel_runtime_support.channel_wake_claim_repository import prompt_message_ids, prompt_references_message_id
from ciel_runtime_support.channel_transcript import ChannelWakeTranscriptServices, wake_state_evidence_from_text
from ciel_runtime_support.channel_wake_delivery_repository import ChannelWakeDeliveryRepository
from ciel_runtime_support.claude_session_socket import ClaudeSessionSocketClient, generated_socket_path, session_key_hash
from ciel_runtime_support.external_event_receiver import EventReceiverSecretVault, ExternalEventReceiverService
from ciel_runtime_support.runtime_input_gateway import RuntimeInputGateway
from ciel_runtime_support.runtime_input_status import RuntimeInputStatusRepository


CHILD_CODE = """
import ctypes,json,os,socket,sys,threading,time
from pathlib import Path
pipe,key,receipt,release,transcript=sys.argv[1:]
threading.Timer(20,lambda:os._exit(4)).start()
if Path(receipt).exists():
    while not Path(release).exists():time.sleep(0.02)
    os._exit(0)
prompts=[]
for batch_index in range(int(os.environ.get('CIEL_FIXTURE_BATCHES','1'))):
    if os.name == 'nt':
        from ctypes import wintypes
        k=ctypes.WinDLL('kernel32',use_last_error=True)
        k.CreateNamedPipeW.argtypes=[wintypes.LPCWSTR,wintypes.DWORD,wintypes.DWORD,wintypes.DWORD,wintypes.DWORD,wintypes.DWORD,wintypes.DWORD,ctypes.c_void_p]
        k.CreateNamedPipeW.restype=wintypes.HANDLE
        k.ConnectNamedPipe.argtypes=[wintypes.HANDLE,ctypes.c_void_p]
        k.ReadFile.argtypes=[wintypes.HANDLE,ctypes.c_void_p,wintypes.DWORD,ctypes.POINTER(wintypes.DWORD),ctypes.c_void_p]
        k.CloseHandle.argtypes=[wintypes.HANDLE]
        handle=k.CreateNamedPipeW(pipe,1,8,1,65536,65536,0,None)
        if handle == ctypes.c_void_p(-1).value:raise ctypes.WinError(ctypes.get_last_error())
        Path(key).write_text(json.dumps({'peerToken':'ab'*16}),encoding='utf-8')
        if not k.ConnectNamedPipe(handle,None) and ctypes.get_last_error()!=535:raise ctypes.WinError(ctypes.get_last_error())
        data=bytearray()
        while data.count(bytes([10]))<2:
            buf=ctypes.create_string_buffer(65536);n=wintypes.DWORD()
            if not k.ReadFile(handle,buf,len(buf),ctypes.byref(n),None):break
            data.extend(buf.raw[:n.value])
        k.CloseHandle(handle)
    else:
        server=socket.socket(socket.AF_UNIX,socket.SOCK_STREAM)
        server.bind(pipe);server.listen(1)
        Path(key).write_text(json.dumps({'peerToken':'ab'*16}),encoding='utf-8')
        c,_=server.accept();data=bytearray()
        with c:
            while data.count(bytes([10]))<2:
                block=c.recv(65536)
                if not block:break
                data.extend(block)
        server.close()
        Path(pipe).unlink(missing_ok=True)
    frames=[json.loads(row) for row in bytes(data).splitlines()]
    assert frames[0]=={'type':'auth','token':'ab'*16}
    assert frames[1]['type']=='user'
    prompts.append(frames[1]['message']['content'])
    Path(receipt).write_text(json.dumps({'accepted':True,'prompt':prompts[-1],'prompts':prompts,'user_frames':len(prompts)}),encoding='utf-8')
    from datetime import datetime,timezone
    stamp=datetime.now(timezone.utc).isoformat()
    rows=[{'type':'user','sessionId':'owned-fixture','timestamp':stamp,'message':frames[1]['message']},
          {'type':'assistant','sessionId':'owned-fixture','timestamp':stamp,'message':{'role':'assistant','content':[{'type':'text','text':'synthetic fixture reply'}]}}]
    with Path(transcript).open('a',encoding='utf-8') as out:out.write(''.join(json.dumps(row)+chr(10) for row in rows))
while not Path(release).exists():time.sleep(0.02)
os._exit(0)
"""


class DirectSocketDeliveryE2ETests(unittest.TestCase):
    def test_real_sse_queue_and_socket_survive_proxy_fallback(self):
        self._run_pipeline(fail_after_submit=False)

    def test_inflight_socket_delivery_is_confirmed_after_real_child_replacement(self):
        self._run_pipeline(fail_after_submit=True)

    def test_damaged_history_admits_new_sse_once(self):
        self._run_pipeline(fail_after_submit=False, damaged_history=True)

    def test_damaged_history_replacement_preserves_old_owners(self):
        self._run_pipeline(fail_after_submit=True, damaged_history=True)

    def test_paused_recovery_settles_submitted_batch_then_releases_next_sse_batch_once(self):
        self._run_pipeline(fail_after_submit=False, recovery_batches=True)

    def _run_pipeline(self, *, fail_after_submit, damaged_history=False, recovery_batches=False):
        with tempfile.TemporaryDirectory(prefix="ciel-direct-e2e-") as raw:
            root = Path(raw)
            environment = mock.patch.dict(os.environ, {"CLAUDE_CONFIG_DIR": str(root / ".claude")})
            environment.start()
            self.addCleanup(environment.stop)
            logs = []
            def log(*entry):
                logs.append(entry)
            cursor = [0]
            receipt = root / "received.json"
            transcript = root / "transcript.jsonl"
            evidence_states = []
            socket_sends = []
            repeat_ids = []
            recovery_paused = [False]
            settled_while_paused = []
            held_checks = [0]
            gate_releases = []
            fresh_id = 7 if damaged_history else 1
            release = root / "release"
            target = generated_socket_path("nt") if os.name == "nt" else str(root / "session.sock")
            keys = root / ".claude" / "sessions"
            keys.mkdir(parents=True)
            key = keys / f"fixture.{session_key_hash(target, os.name)}.key"
            client = ClaudeSessionSocketClient(root, log, platform_name=os.name)
            client.configure(target)
            def publish_status(**event):
                row = event["data"]
                evidence_states.append((row["request_id"], row["status"]))
            status = RuntimeInputStatusRepository(root / "status.jsonl", publish_status, log, threading.RLock())
            queue = ChannelMessageRepository(root / "inputs.jsonl", log)
            append_ports = ChannelMessageAppendPorts(
                condition=threading.Condition(), file_lock=lambda: exclusive_file_lock(queue.path),
                duplicate=lambda row: None, normalize_recipients=string_list,
            )
            # Old inputs whose readable durable owners must never be resent:
            # 1-3 are plain rows, 4-6 survive a leading-NUL writer prefix.
            original_states = {1: "submitted", 2: "replied", 3: "interrupted_uncertain",
                               4: "submitted", 5: "replied", 6: "interrupted_uncertain"} if damaged_history else {}
            history_prefix = b""
            if damaged_history:
                owner_rows = []
                for message_id, old_state in original_states.items():
                    queue.append({"message": f"synthetic old input {message_id}", "channel": "fixture-old",
                                  "meta": {"input_transport": "session_socket"}}, append_ports)
                    row = {"request_id": message_id, "status": old_state,
                           "updated_at": "2026-10-20T00:00:00+00:00", "updated_at_epoch": 1792454400.0,
                           "data": {"attempt_id": f"old-attempt-{message_id}", "child_epoch": "old-child"}}
                    nul_prefix = bytes(19) if message_id > 3 else b""
                    owner_rows.append(nul_prefix + json.dumps(row).encode("utf-8") + bytes([10]))
                damaged_lines = [b"{malformed" + bytes([10]), b"{broken" + bytes([0, 10]), bytes([255, 10])]
                history_prefix = (damaged_lines[0] + b"".join(owner_rows[:3]) + damaged_lines[1]
                                  + b"".join(owner_rows[3:]) + damaged_lines[2] + b"{" + b'"unfinished"' + b":")
                status.path.write_bytes(history_prefix)
                for message_id, old_state in original_states.items():
                    self.assertEqual(old_state, status.get(message_id)["status"])
                self.assertFalse(status.history_health()["complete"])
                self.assertEqual(history_prefix, status.path.read_bytes(), "history read changed source bytes")
            gateway = RuntimeInputGateway(lambda body: queue.append(body, append_ports), status=status)
            lifecycle = ChannelWakeDeliveryRepository(
                lock=threading.RLock(), delivered=set(), prompts={}, batches={},
                clear_claim=lambda _: None, commit_cursor=lambda n: cursor.__setitem__(0, n), status=status,
            )
            lifecycle.bind_child_epoch("owned-protocol-child")
            transcript_services = ChannelWakeTranscriptServices(
                claim_prompt=lifecycle.prompt, prompt_references_message_id=prompt_references_message_id,
                prompt_message_ids=prompt_message_ids, now=time.time,
            )
            def wake_state(message_id, prompt=None):
                text = transcript.read_text(encoding="utf-8") if transcript.exists() else ""
                return wake_state_evidence_from_text(
                    message_id, text, [prompt] if prompt else (), transcript_services,
                ).state
            def send_socket(prompt, messages):
                socket_sends.append([row["id"] for row in messages])
                sent = client.send(prompt, messages)
                if recovery_batches and len(socket_sends) == 1:
                    recovery_paused[0] = True
                return sent
            def no_tty(*args, **kwargs):
                raise AssertionError("direct fallback tried terminal input")
            def format_prompt(messages):
                return " ".join(row["message"] for row in messages)
            def compact(writer, *args, **kwargs):
                self.assertIsNotNone(writer, "direct fallback polled terminal compaction")
                return ""
            injection = ChannelInjectionServices(
                state=ChannelInjectionState(
                    active_tool_call=lambda: False, active_turn=lambda: False,
                    recover_cursor=lambda n: n, pending_scan_limit=lambda: 50,
                    superseded_ids=lambda rows: set(), message_is_web_chat=lambda row: False,
                    message_skip_reason=lambda row: "", event_identity_key=lambda row: (),
                    wake_state_for_message=lambda row, prompt: wake_state(row["id"], prompt),
                    queued_wake_is_stale=lambda *a: False,
                ),
                prompts=ChannelInjectionPrompts(
                    llm_delivery=format_prompt, visible_llm_delivery=format_prompt,
                    web_chat=format_prompt, standard=format_prompt,
                    enter_bytes=lambda _: bytes([13]), enter_label=lambda _: "cr",
                ),
                wake_store=ChannelInjectionWakeStore(
                    claim_for_nonblocking_scan=lambda _: False, claim_prompt=lambda *a: True,
                    clear_claim=lambda _: None, release_stale=lambda *a: None,
                    lifecycle=lifecycle, commit_cursor=lambda n: cursor.__setitem__(0, n),
                ),
                io=ChannelInjectionIO(
                    inject_lock=threading.Lock(), read_messages=lambda n,*a: queue.read(n),
                    write_prompt=no_tty, log=log, write_session_socket=send_socket,
                    session_socket_available=client.available,
                ),
                policy=ChannelInjectionPolicy(wake_batch_limit=lambda: 3 if recovery_batches else 1, replay_skip_reason=lambda row: ""),
            )
            def inject(writer, last_id, enter, **options):
                return inject_pending_channel_messages(writer, last_id, enter, services=injection, **options)
            def complete(message_id):
                lifecycle.complete(message_id)
                if recovery_batches:
                    if message_id == 3:
                        self.assertTrue(recovery_paused[0])
                        self.assertEqual(["replied"] * 3, [status.get(n)["status"] for n in (1, 2, 3)])
                        self.assertEqual(["queued"] * 3, [status.get(n)["status"] for n in (4, 5, 6)])
                        settled_while_paused.extend((1, 2, 3))
                    elif message_id == 6:
                        release.touch()
                else:
                    release.touch()
            def admission_paused():
                if settled_while_paused and recovery_paused[0]:
                    self.assertEqual([[1, 2, 3]], socket_sends)
                    self.assertEqual(["queued"] * 3, [status.get(n)["status"] for n in (4, 5, 6)])
                    held_checks[0] += 1
                    if held_checks[0] >= 6:
                        recovery_paused[0] = False
                        gate_releases.append("released_after_exact_settlement")
                return recovery_paused[0]
            processes = []
            def popen(cmd, env):
                proc = subprocess.Popen(cmd, env=env, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
                processes.append(proc)
                return proc
            def terminate(proc, label):
                if proc.poll() is None:
                    proc.terminate()
                proc.wait(timeout=5)
            def fail_proxy(cmd, env, **options):
                if not fail_after_submit:
                    raise OSError(36, "fixture proxy failure")
                from dataclasses import replace
                from types import SimpleNamespace
                from ciel_runtime_support.channel_terminal_proxy import run_windows_channel_terminal_proxy
                proc = popen(cmd, env)
                deadline = time.monotonic() + 3
                while not key.exists() and proc.poll() is None and time.monotonic() < deadline:
                    time.sleep(0.01)
                self.assertTrue(key.exists(), "original child never published its socket key")
                class ConsoleChild:
                    pid = proc.pid
                    poll = staticmethod(proc.poll)
                    wait = staticmethod(proc.wait)
                    terminate = staticmethod(proc.terminate)
                    def resize_if_needed(self):
                        if lifecycle.is_delivered(fresh_id):
                            deadline = time.monotonic() + 3
                            while not receipt.exists() and time.monotonic() < deadline:
                                time.sleep(0.01)
                            if not receipt.exists():
                                raise AssertionError("original socket submission was not received")
                            raise OSError(36, "fixture post-submit display failure")
                    def close(self):
                        pass
                services = context.windows_services()
                services = replace(services, console=SimpleNamespace(
                    open_conpty=lambda *a: ConsoleChild(), startup_grace_seconds=lambda: 0,
                    reset_interval_seconds=lambda n: n, sleep=time.sleep, active_turn=lambda: False,
                ))
                options.pop("normalize_bare_cr_for_synthetic_enter", None)
                return run_windows_channel_terminal_proxy(cmd, env, services, **options)
            api = ChannelTerminalCompatibilityApi(lambda: context)
            from types import SimpleNamespace
            context = ChannelTerminalContext(
                process=ChannelTerminalProcessPorts(
                    popen=popen, write_record=lambda *a: None, terminate=terminate,
                    release_record=lambda *a: None,
                ),
                policy=ChannelTerminalPolicyPorts(
                    initial_cursor=lambda: cursor[0], enter_bytes=lambda _: bytes([13]),
                    enter_label=lambda _: "cr", enter_is_fixed=lambda: True,
                    unseen_retry_seconds=lambda: 30, inflight_is_stale=lambda *a: False,
                    log=log,
                    compaction=SimpleNamespace(external_admission_paused=admission_paused),
                ),
                polling=ChannelTerminalPollingPorts(
                    inject_compact=compact,
                    file_marker=lambda: (queue.path.stat().st_mtime, queue.path.stat().st_size) if queue.path.exists() else (0.0, 0),
                    should_check=lambda *a: True, active_tool_call=lambda: False,
                    active_turn=lambda: False, inject_pending=inject, wake_state=wake_state,
                    inflight_effects=lambda: ChannelInflightEffects(
                        commit_cursor=lambda n: cursor.__setitem__(0, n), complete_wake=complete,
                        release_wake=no_tty, ensure_cursor=lambda: cursor[0], log=log,
                    ),
                    session_socket_ready=client.available,
                ),
                io=ChannelTerminalIoPorts(
                    terminal_size=no_tty, apply_terminal_size=no_tty, write_all=no_tty,
                    mouse_filter=no_tty, observed_enter=no_tty, reset_input_mode=no_tty,
                ),
                windows=ChannelTerminalWindowsPorts(
                    run_proxy=lambda cmd, env, services, **options: fail_proxy(cmd, env, **options),
                    reset_input_mode=no_tty, mouse_guard=no_tty,
                    input_writer=no_tty, startup_grace_seconds=lambda: 0,
                    reset_interval_seconds=lambda n: n, active_turn=lambda: False,
                    sleep=time.sleep, open_conpty=no_tty,
                ),
                dispatch_ports=ChannelTerminalDispatchPorts(
                    platform_name="nt", stdin_isatty=lambda: True, stdout_isatty=lambda: True,
                    direct_call=no_tty, run_windows=api.run_windows, run_posix=no_tty,
                    prepare_delivery=lambda: None, windows_supported=lambda: True,
                ),
            )
            ready = threading.Event()
            class Inbox(BaseHTTPRequestHandler):
                def do_GET(self):
                    self.send_response(200)
                    self.send_header("content-type", "text/event-stream")
                    self.end_headers()
                    for index in range(1, 7) if recovery_batches else (1,):
                        event_id = f"fixture-event-{index}"
                        event = {"specversion": "1.0", "id": event_id, "source": "urn:ciel:fixture",
                                 "type": "fixture.message", "data": {"text": f"socket-fallback-nonce-{index}"}}
                        frame = "id: " + event_id + chr(10) + "data: " + json.dumps(event) + chr(10) * 2
                        self.wfile.write(frame.encode())
                        self.wfile.flush()
                    ready.set()
                def log_message(self, *args):
                    pass
            server = ThreadingHTTPServer(("127.0.0.1", 0), Inbox)
            server_thread = threading.Thread(target=server.serve_forever, daemon=True)
            server_thread.start()
            config = {"external_event_receivers": {"fixture": {"default": {
                "enabled": True, "transport": "sse", "input_transport": "session_socket",
                "url": f"http://127.0.0.1:{server.server_port}/events",
            }}}}
            receiver = ExternalEventReceiverService(
                load_config=lambda: config, save_config=lambda value: None,
                write_json=lambda *a: None, submit_event=gateway.submit_external_event,
                vault=EventReceiverSecretVault(root / "fixture.vault.json"),
                workspace_key="fixture", log=log, cursor_path=root / "sse-cursor.json",
            )
            child = root / "fixture-child.py"
            child.write_text(CHILD_CODE, encoding="utf-8")
            purge = ("CIEL_RUNTIME_", "CLAUDE", "ANTHROPIC_", "CODEX_", "OPENAI_", "OPENROUTER_", "GEMINI_", "GOOGLE_")
            env = {k: v for k, v in os.environ.items() if not k.upper().startswith(purge)
                   and not any(word in k.upper() for word in ("TOKEN", "SECRET", "PASSWORD", "API_KEY", "CREDENTIAL"))}
            env.update(HOME=str(root), USERPROFILE=str(root), CLAUDE_CONFIG_DIR=str(root / ".claude"))
            env["CIEL_FIXTURE_BATCHES"] = "2" if recovery_batches else "1"
            env["CIEL_RUNTIME_TEST_ISOLATED"] = "1"
            env["CIEL_RUNTIME_CONFIG_DIR"] = str(root / "config")
            try:
                receiver.start()
                self.assertTrue(ready.wait(3))
                deadline = time.monotonic() + 3
                # Wait for gateway admission itself: its queued status row is
                # appended after the queue row, and old rows already exist.
                while ((6 if recovery_batches else fresh_id), "queued") not in evidence_states and time.monotonic() < deadline:
                    time.sleep(0.01)
                self.assertEqual(6 if recovery_batches else fresh_id, queue.max_id())
                self.assertEqual([(n, "queued") for n in range(1, 7)] if recovery_batches else [(fresh_id, "queued")], evidence_states)
                self.assertEqual("queued", status.get(fresh_id)["status"])
                self.assertEqual([], socket_sends, "transport preceded queued admission")
                receiver.stop()
                result = context.dispatch(
                    [sys.executable, "-I", str(child), target, str(key), str(receipt), str(release), str(transcript)],
                    env, channel_wake_confirm_submit=True,
                )
                errors = [proc.stderr.read().decode() for proc in processes]
                self.assertEqual(0, result, f"stderr={errors}; logs={logs}")
                record = json.loads(receipt.read_text(encoding="utf-8"))
                self.assertTrue(record["accepted"])
                self.assertIn("socket-fallback-nonce", record["prompt"])
                self.assertEqual(2 if recovery_batches else 1, record["user_frames"])
                self.assertEqual(6 if recovery_batches else fresh_id, len(queue.read()))
                # A readable old submitted owner holds the frontier without
                # blocking or resending; later independent inputs still go.
                self.assertEqual(6 if recovery_batches else 0 if damaged_history else fresh_id, cursor[0])
                self.assertEqual("replied", status.get(fresh_id)["status"])
                fresh_body = queue.read(fresh_id - 1)[0]["message"]
                self.assertEqual("completed", wake_state(fresh_id, fresh_body))
                expected_states = ([(n, "queued") for n in range(1, 7)] +
                    [(n, stage) for first in (1, 4) for stage in ("submitted", "replied") for n in range(first, first + 3)]
                    if recovery_batches else [(fresh_id, "queued"), (fresh_id, "submitted"), (fresh_id, "replied")])
                self.assertEqual(expected_states, evidence_states)
                expected_sends = [[1, 2, 3], [4, 5, 6]] if recovery_batches else [[fresh_id]]
                self.assertEqual(expected_sends, socket_sends)
                self.assertEqual(2 if recovery_batches else 1, sum("claude_session_socket_injected" in message for _, message in logs))
                if recovery_batches:
                    self.assertEqual([1, 2, 3], settled_while_paused)
                    self.assertEqual(["released_after_exact_settlement"], gate_releases)
                    self.assertEqual(["replied"] * 6, [status.get(n)["status"] for n in range(1, 7)])
                appended_before_repeat = status.path.read_bytes()
                # Full rescan from zero, not just an empty tail after the cursor.
                inject(None, 0, bytes([13]), terminal_available=False, injected_message_ids=repeat_ids)
                self.assertEqual([], repeat_ids)
                self.assertEqual(expected_sends, socket_sends)
                self.assertEqual(appended_before_repeat, status.path.read_bytes(), "repeat scan wrote status")
                for message_id, old_state in original_states.items():
                    self.assertEqual(old_state, status.get(message_id)["status"])
                    self.assertEqual(f"old-attempt-{message_id}", status.get(message_id)["data"]["attempt_id"])
                final_history = status.path.read_bytes()
                self.assertTrue(final_history.startswith(history_prefix), "history prefix rewritten")
                self.assertEqual([], [p.name for p in root.rglob("*") if "archive" in p.name or "quarantine" in p.name])
                evidence_dir = os.environ.get("E2E_EVIDENCE_DIR")
                if evidence_dir:
                    label = ("recovery-batches" if recovery_batches else "damaged" if damaged_history else "baseline") + ("-replacement" if fail_after_submit else "")
                    appended = final_history[len(history_prefix):]
                    evidence = {
                        "fixture": "owned python protocol child (not native Claude Code)",
                        "platform": os.name, "message_id": fresh_id, "cursor": cursor[0],
                        "socket_sends": socket_sends, "repeat_scan_injected_ids": repeat_ids,
                        "settled_while_paused": settled_while_paused, "held_admission_checks_after_settlement": held_checks[0],
                        "gate_releases": gate_releases,
                        "status_events": evidence_states,
                        "old_states_before": original_states,
                        "old_states_after": {i: status.get(i)["status"] for i in original_states},
                        "history_health_after": status.history_health(),
                        "history_prefix_bytes": len(history_prefix),
                        "history_prefix_sha256": hashlib.sha256(history_prefix).hexdigest(),
                        "history_prefix_unchanged": final_history.startswith(history_prefix),
                        "appended_rows": [{k: json.loads(line).get(k) for k in ("request_id", "status")}
                                          for line in appended.splitlines() if line.strip()],
                        "transcript_record_types": [json.loads(line)["type"]
                                                    for line in transcript.read_text(encoding="utf-8").splitlines()],
                        "transcript_wake_state": wake_state(fresh_id, fresh_body),
                        "receipt_user_frames": record["user_frames"], "process_count": len(processes),
                        "logs": logs,
                    }
                    Path(evidence_dir, label + ".json").write_text(json.dumps(evidence, indent=2), encoding="utf-8")
                self.assertTrue(any("channel_direct_socket_poller_started" in message for _, message in logs))
                self.assertTrue(any("channel_direct_socket_poller_stopped" in message for _, message in logs))
                if fail_after_submit:
                    self.assertEqual(2, len(processes))
                    self.assertIsNotNone(processes[0].poll())
                    self.assertTrue(any("stage=terminal_io" in message for _, message in logs))
                    self.assertTrue(any("channel_direct_socket_confirmed" in message for _, message in logs))
                else:
                    self.assertEqual(1, len(processes))
            finally:
                receiver.stop()
                server.shutdown()
                server.server_close()
                server_thread.join(timeout=2)
                for proc in processes:
                    terminate(proc, "fixture cleanup")
                    if proc.stderr is not None:
                        proc.stderr.close()
