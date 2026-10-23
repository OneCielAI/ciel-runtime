import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from ciel_runtime_support import claude_stream_recovery_runtime as recovery
from ciel_runtime_support import claude_stream_recovery_settings as settings
from ciel_runtime_support.runtime_compaction_control import CompactionScope
from test_claude_stream_recovery_runtime import Harness, SCOPE, SESSION, assistant, user


def boundary(**changes):
    result = {"type": "system", "subtype": "compact_boundary", "uuid": "checkpoint-1",
              "sessionId": SESSION, "compactMetadata": {"trigger": "auto", "preTokens": 20000, "postTokens": 4000}}
    result.update(changes)
    return result


class CompactContinuationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.h = Harness(Path(self.temp.name))
        self.h.monitor.bound_epoch = lambda: "epoch-1"
        self.h.terminal["manual_input_known"] = True
        self.mode()
        self.task()

    def mode(self, value="default"):
        self.h.monitor.observe({"type": "permission-mode", "permissionMode": value, "sessionId": SESSION})

    def task(self):
        self.h.monitor.observe(user("human-1", "Finish the approved implementation and verify it."))
        self.h.monitor.observe(assistant("a-1", "human-1", "msg-1"))

    def decide(self, record=None):
        self.h.monitor.observe(boundary() if record is None else record)
        self.h.clock.value += recovery.QUIET_SECONDS + 0.1
        return self.h.monitor.tick()

    def test_positive_visible_scoped_notice_and_durable_dedupe(self):
        self.assertEqual("compact_resume", self.decide()["kind"])
        body = self.h.submitted[0]
        self.assertEqual("compact_resume", body["kind"])
        self.assertEqual("stream_recovery", body["meta"]["source_kind"])
        self.assertEqual("ciel-runtime-compact-resume", body["meta"]["source"])
        self.assertIn("Continue the previously authorized unfinished work", body["message"])
        self.assertNotIn("server error", body["message"])
        self.assertEqual(SCOPE.payload(), body["meta"]["recovery_scope"])
        self.assertEqual("", recovery.recovery_notice_skip_reason(body, SCOPE, self.h.clock()))
        self.assertEqual("stream_recovery_scope_changed", recovery.recovery_notice_skip_reason(body, None, self.h.clock()))
        self.assertEqual("stream_recovery_expired", recovery.recovery_notice_skip_reason(body, SCOPE, self.h.clock() + 121))
        self.assertIsNone(self.decide())
        self.h.monitor = self.h.new_monitor()
        self.h.monitor.bound_epoch = lambda: SCOPE.epoch
        self.mode()
        self.task()
        self.assertIsNone(self.decide())
        self.assertEqual(1, len(self.h.submitted))
        persisted = json.loads(self.h.ledger.repository.path.read_text(encoding="utf-8"))
        self.assertEqual({}, persisted["chains"])
        self.assertEqual(1, len(persisted["handled"]))

    def test_human_origin_and_native_summary_bookkeeping_do_not_cancel_resume(self):
        self.h.monitor.reset()
        self.mode()
        record = user("real-human", "Finish the authorized task")
        record["origin"] = {"kind": "human"}
        self.h.monitor.observe(record)
        self.h.monitor.observe(assistant("working", "real-human", "m-working"))
        self.h.monitor.observe(boundary())
        summary = user("summary", "This session is being continued from a previous conversation")
        summary["isCompactSummary"] = True
        self.h.monitor.observe(summary)
        for kind in ("attachment", "last-prompt", "mode", "atis-latch"):
            self.h.monitor.observe({"type": kind, "sessionId": SESSION})
        self.mode()
        self.h.clock.value += 5
        self.assertEqual("compact_resume", self.h.monitor.tick()["kind"])

    def test_posix_generation_acknowledges_human_submit_but_not_later_cancel(self):
        self.h.monitor.reset()
        self.mode()
        snapshot = {**self.h.terminal, "manual_input": True, "manual_input_known": True,
                    "ready": False, "input_ready": True, "operator_generation": 4,
                    "operator_input_at": self.h.clock() - 1, "at": self.h.clock()}
        self.h.monitor.set_terminal_admission(snapshot)
        record = user("human-posix", "Continue the approved implementation")
        from datetime import datetime, timezone
        record["timestamp"] = datetime.fromtimestamp(self.h.clock(), timezone.utc).isoformat()
        record["origin"] = {"kind": "human"}
        self.h.monitor.observe(record)
        self.h.monitor.observe(assistant("a-posix", "human-posix", "m-posix"))
        self.h.monitor.observe(boundary())
        self.h.clock.value += 5
        self.h.terminal = None
        self.h.monitor.set_terminal_admission({**snapshot, "at": self.h.clock()})
        self.assertEqual("compact_resume", self.h.monitor.tick()["kind"])
        self.h.monitor.observe(record)
        self.h.monitor.observe(boundary(uuid="second"))
        self.h.monitor.set_terminal_admission({**snapshot, "operator_generation": 5,
            "operator_input_at": self.h.clock() + 1, "at": self.h.clock()})
        self.h.clock.value += 5
        self.assertIsNone(self.h.monitor.tick())

    def test_owned_socket_and_http_fixture_receive_one_continuation(self):
        import socket
        import threading
        import urllib.request
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
        from ciel_runtime_support.claude_session_socket import ClaudeSessionSocketClient
        hits, frames = [], []
        class Upstream(BaseHTTPRequestHandler):
            def do_POST(handler):
                hits.append(json.loads(handler.rfile.read(int(handler.headers["Content-Length"]))))
                payload = b'{"ok":true}'
                handler.send_response(200)
                handler.send_header("Content-Length", str(len(payload)))
                handler.end_headers()
                handler.wfile.write(payload)
            def log_message(self, *_):
                pass
        http = ThreadingHTTPServer(("127.0.0.1", 0), Upstream)
        thread = threading.Thread(target=http.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(http.server_close)
        self.addCleanup(http.shutdown)
        if hasattr(socket, "AF_UNIX"):
            path = str(Path(self.temp.name) / "owned.sock")
            server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            server.bind(path)
            server.listen(1)
            server.settimeout(3)
            self.addCleanup(server.close)
            def read_frames():
                connection, _ = server.accept()
                with connection:
                    stream = connection.makefile("rb")
                    return [json.loads(stream.readline()) for _ in range(2)]
            platform_name = "posix"
        else:
            import ctypes
            from ctypes import wintypes
            import uuid
            path = chr(92) * 2 + "." + chr(92) + "pipe" + chr(92) + "ciel-owned-" + uuid.uuid4().hex
            kernel = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel.CreateNamedPipeW.restype = wintypes.HANDLE
            kernel.CreateNamedPipeW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                wintypes.DWORD, wintypes.DWORD, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID]
            kernel.ConnectNamedPipe.argtypes = [wintypes.HANDLE, wintypes.LPVOID]
            kernel.ReadFile.argtypes = [wintypes.HANDLE, wintypes.LPVOID, wintypes.DWORD,
                                      ctypes.POINTER(wintypes.DWORD), wintypes.LPVOID]
            kernel.CloseHandle.argtypes = [wintypes.HANDLE]
            handle = kernel.CreateNamedPipeW(path, 1, 0, 1, 65536, 65536, 0, None)
            self.assertNotEqual(ctypes.c_void_p(-1).value, handle)
            self.addCleanup(kernel.CloseHandle, handle)
            def read_frames():
                connected = kernel.ConnectNamedPipe(handle, None)
                if not connected and ctypes.get_last_error() != 535:
                    raise ctypes.WinError(ctypes.get_last_error())
                raw = bytearray()
                buffer = ctypes.create_string_buffer(65536)
                count = wintypes.DWORD()
                while raw.count(bytes([10])) < 2:
                    if not kernel.ReadFile(handle, buffer, len(buffer), ctypes.byref(count), None):
                        raise ctypes.WinError(ctypes.get_last_error())
                    raw.extend(buffer.raw[:count.value])
                return [json.loads(line) for line in raw.splitlines()]
            platform_name = "nt"
        def receive():
            frames.extend(read_frames())
            request = urllib.request.Request("http://127.0.0.1:" + str(http.server_address[1]) + "/v1/messages",
                data=json.dumps(frames[1]["message"]).encode(), headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(request, timeout=3) as result:
                self.assertEqual(200, result.status)
        socket_thread = threading.Thread(target=receive)
        socket_thread.start()
        client = ClaudeSessionSocketClient(Path(self.temp.name), self.h.log, platform_name=platform_name)
        client.configure(path)
        with mock.patch.object(client, "_read_peer_token", return_value="owned-test-token"):
            self.h.monitor.submit = lambda body: {"id": 1, "submitted": client.send(body["message"])}
            self.assertEqual("compact_resume", self.decide()["kind"])
            socket_thread.join(4)
        self.assertFalse(socket_thread.is_alive())
        self.assertEqual("auth", frames[0]["type"])
        self.assertEqual("next", frames[1]["priority"])
        self.assertEqual(1, len(hits))
        self.assertIn("Continue the previously authorized unfinished work", hits[0]["content"])
        self.assertIsNone(self.decide())
        print("compact_owned_transport checkpoint=1 socket_frames=2 http_requests=1 status=200 replay=0")

    def test_boundary_waits_for_compaction_gate_and_idle_report_to_refresh(self):
        self.h.monitor.observe(boundary())
        self.h.clock.value += 5
        self.h.terminal["compaction"] = True
        self.assertIsNone(self.h.monitor.tick())
        self.h.terminal["compaction"] = False
        self.assertEqual("compact_resume", self.h.monitor.tick()["kind"])

    def test_independent_toggle_and_fixed_one_attempt(self):
        self.h.config = {settings.CONFIG_KEY: {"enabled": False, "max_attempts": 2, "compact_resume": True}}
        self.assertEqual(1, self.decide()["limit"])
        self.h.config[settings.CONFIG_KEY]["compact_resume"] = False
        self.assertIsNone(self.decide(boundary(uuid="checkpoint-2")))

    def test_nonauto_and_invalid_shrink(self):
        for trigger in ("manual", "user", "tool", "", None):
            with self.subTest(trigger=trigger):
                self.assertIsNone(self.decide(boundary(compactMetadata={"trigger": trigger, "preTokens": 100, "postTokens": 20})))
        for pre, post in ((100, None), (100, 100), (100, 101), (100, 0), (True, 1), (100, True), ("100", 20), (100, 1.5)):
            with self.subTest(pre=pre, post=post):
                self.assertIsNone(self.decide(boundary(compactMetadata={"trigger": "auto", "preTokens": pre, "postTokens": post})))
        self.assertEqual([], self.h.submitted)

    def test_unknown_scope_epoch_or_session(self):
        original = self.h.scope
        for scope in (None, CompactionScope("codex", SESSION, "epoch-1"), CompactionScope("claude", SESSION, "epoch-2")):
            self.h.scope = scope
            self.assertIsNone(self.decide())
        self.h.scope = original
        self.assertIsNone(self.decide(boundary(sessionId="other")))
        self.h.monitor.bound_epoch = lambda: ""
        self.assertIsNone(self.decide())

    def test_scope_change_after_boundary(self):
        self.h.monitor.observe(boundary())
        self.h.scope = CompactionScope("claude", SESSION, "epoch-2")
        self.h.monitor.bound_epoch = lambda: "epoch-2"
        self.h.clock.value += 5
        self.assertIsNone(self.h.monitor.tick())

    def test_unknown_and_plan_mode(self):
        for mode in ("plan", "unknown", None):
            self.mode(mode)
            self.assertIsNone(self.decide())
        self.h.monitor.reset()
        self.task()
        self.assertIsNone(self.decide())

    def test_attachment_without_human_task_and_manual_commands(self):
        self.h.monitor.reset()
        self.mode()
        self.assertIsNone(self.decide())
        for changes in ({"isCompactSummary": True}, {"isMeta": True}, {"isSynthetic": True}, {"origin": {"kind": "peer"}}):
            record = user("not-human", "continue")
            record.update(changes)
            self.h.monitor.observe(record)
            self.assertIsNone(self.decide())
        for text in ("/compact", "<command-name>/compact</command-name>", "[ciel-wake] pending_ids=1", "[Request interrupted by user]", ""):
            self.h.monitor.observe(user("not-human", text))
            self.assertIsNone(self.decide())

    def test_completed_tasks_are_not_unfinished_authority(self):
        for stop in ("end_turn", "max_tokens", "refusal", "stop_sequence"):
            self.task()
            record = assistant("done", "a-1", "msg-done")
            record["message"]["stop_reason"] = stop
            self.h.monitor.observe(record)
            self.assertIsNone(self.decide())
        self.task()
        self.h.monitor.observe({"type": "system", "subtype": "turn_duration", "sessionId": SESSION})
        self.assertIsNone(self.decide())

    def test_unsettled_tool_blocks_observed_settlement_allows(self):
        record = assistant("tool", "a-1", "msg-tool")
        record["message"]["content"] = [{"type": "tool_use", "id": "t-1", "name": "Read", "input": {}}]
        self.h.monitor.observe(record)
        self.assertIsNone(self.decide())
        result = user("result", "")
        result["message"]["content"] = [{"type": "tool_result", "tool_use_id": "t-1", "content": "ok"}]
        self.h.monitor.observe(result)
        self.assertIsNotNone(self.decide())

    def test_unknown_tool_settlement_fails_closed(self):
        record = user("result", "")
        record["message"]["content"] = [{"type": "tool_result", "tool_use_id": "unobserved"}]
        self.h.monitor.observe(record)
        self.assertIsNone(self.decide())

    def test_native_progress_or_new_native_input_cancels_boundary(self):
        for record in (assistant("new", "a-1", "msg-new"), user("new-user", "new task"),
                       {"type": "queue-operation", "operation": "enqueue", "sessionId": SESSION},
                       {"type": "progress", "sessionId": SESSION}):
            self.task()
            self.h.monitor.observe(boundary())
            self.h.monitor.observe(record)
            self.h.clock.value += 5
            self.assertIsNone(self.h.monitor.tick())

    def test_new_runtime_input_before_or_after_boundary(self):
        self.h.monitor.input_watermark = lambda: 1
        self.assertIsNone(self.decide())
        self.task()
        self.h.monitor.observe(boundary())
        self.h.monitor.input_watermark = lambda: 2
        self.h.clock.value += 5
        self.assertIsNone(self.h.monitor.tick())

    def test_idle_does_not_erase_known_operator_input(self):
        self.h.monitor.set_terminal_admission({**self.h.terminal, "manual_input": True, "at": self.h.clock()})
        self.h.monitor.set_terminal_admission({**self.h.terminal, "at": self.h.clock()})
        self.assertIsNone(self.decide())
        self.task()
        self.h.monitor.observe(boundary())
        self.h.monitor.set_terminal_admission({**self.h.terminal, "manual_input": True, "at": self.h.clock()})
        self.h.clock.value += 5
        self.assertIsNone(self.h.monitor.tick())

    def test_terminal_fresh_idle_known_and_backlog_clear_required(self):
        for flag in ("manual_input_known", "pending", "busy", "compaction", "ready"):
            original = self.h.terminal[flag]
            self.h.terminal[flag] = not original
            self.assertIsNone(self.decide())
            self.h.terminal[flag] = original
            self.task()
        self.h.terminal = None
        self.assertIsNone(self.decide())

    def test_stale_snapshot_cannot_admit(self):
        self.h.monitor.set_terminal_admission({**self.h.terminal, "at": self.h.clock() - 4})
        self.h.terminal = None
        self.assertIsNone(self.decide())

    def test_submit_failure_spends_checkpoint_claim(self):
        self.h.monitor.submit = mock.Mock(side_effect=OSError("queue unavailable"))
        self.assertIsNone(self.decide())
        self.assertIsNone(self.decide())
        self.assertEqual(1, self.h.monitor.submit.call_count)

    def test_sidechain_boundary_does_not_admit(self):
        self.assertIsNone(self.decide(boundary(isSidechain=True)))

    def test_tool_requested_compact_is_not_resumed_after_result(self):
        record = assistant("tool", "a-1", "m-tool")
        record["message"]["content"] = [{"type": "tool_use", "id": "t-compact",
            "name": "mcp__ciel-runtime-router__compact_session", "input": {}}]
        self.h.monitor.observe(record)
        result = user("result", "")
        result["message"]["content"] = [{"type": "tool_result", "tool_use_id": "t-compact"}]
        self.h.monitor.observe(result)
        self.assertIsNone(self.decide())

    def test_poll_attachment_to_normal_gateway_path(self):
        from ciel_runtime_support.transcript_delta_delivery import TranscriptDeliveryPorts, TranscriptDeltaDeliveryService
        from ciel_runtime_support.runtime_input_gateway import RuntimeInputGateway

        root = Path(self.temp.name)
        transcript = root / "native.jsonl"
        transcript.write_text(json.dumps(user("historical", "old work")) + chr(10), encoding="utf-8")
        queue = []
        def append(payload):
            message = {**payload, "id": len(queue) + 1}
            queue.append(message)
            return message
        self.h.monitor.submit = RuntimeInputGateway(append=append).submit_notification
        service = TranscriptDeltaDeliveryService(root / "cursors.json", "owned-test",
            TranscriptDeliveryPorts(lambda: {}, lambda: transcript,
                lambda: {"runtime": "claude", "session_id": SESSION}, self.h.log,
                stream_recovery=lambda: self.h.monitor))
        self.assertEqual(0, service.poll_stream_recovery())
        with transcript.open("a", encoding="utf-8") as output:
            for record in ({"type": "permission-mode", "permissionMode": "default", "sessionId": SESSION},
                           user("human-new", "Finish the approved task"), assistant("a-new", "human-new", "m-new"), boundary()):
                output.write(json.dumps(record) + chr(10))
        self.assertEqual(4, service.poll_stream_recovery())
        self.assertEqual([], queue)
        self.h.clock.value += 5
        service.poll_stream_recovery()
        self.assertEqual(1, len(queue))
        self.assertEqual("private_runtime", queue[0]["visibility"])
        self.assertEqual(["llm"], queue[0]["delivery"])
        self.assertEqual("session_socket", queue[0]["meta"]["input_transport"])
        self.assertEqual("compact_resume", queue[0]["kind"])
        service.poll_stream_recovery()
        self.assertEqual(1, len(queue))


    def test_manual_boundary_withdraws_task_even_before_later_auto(self):
        self.assertIsNone(self.decide(boundary(compactMetadata={"trigger": "manual", "preTokens": 100, "postTokens": 20})))
        self.assertIsNone(self.decide(boundary(uuid="auto-after-manual")))

    def test_candidate_expires_before_admission(self):
        self.h.monitor.observe(boundary())
        self.h.clock.value += recovery.RECOVERY_NOTICE_TTL_SECONDS + 1
        self.assertIsNone(self.h.monitor.tick())



class CompactContinuationSettingsTests(unittest.TestCase):
    def test_default_and_invalid_setting(self):
        self.assertTrue(settings.default_config()["compact_resume"])
        for value in (None, 0, "false", [], {}):
            self.assertTrue(settings.compact_resume({settings.CONFIG_KEY: {"compact_resume": value}}))
        self.assertFalse(settings.compact_resume({settings.CONFIG_KEY: {"compact_resume": False}}))

    def test_panel_toggle_is_reachable_and_independent(self):
        config = {settings.CONFIG_KEY: {"enabled": False, "max_attempts": 2, "custom": "kept"}}
        rows, values = settings.with_panel_rows(["Back"], ["back"], config)
        index = values.index(settings.VALUE_COMPACT)
        self.assertIn("compact continuation", rows[index])
        self.assertTrue(settings.handles(values[index]))
        self.assertIn("off", settings.apply(config, values[index])[0])
        self.assertEqual({"enabled": False, "max_attempts": 2, "custom": "kept", "compact_resume": False}, config[settings.CONFIG_KEY])
        settings.toggle_enabled(config)
        settings.toggle_attempts(config)
        self.assertFalse(settings.compact_resume(config))

    def test_actual_menu_selection_saves_compact_toggle(self):
        import ciel_runtime
        from ciel_runtime_support.config_repository import build_default_config
        from ciel_runtime_support.prelaunch import MAIN_MENU_ACTIONS

        config = build_default_config({})
        rows, values = settings.with_panel_rows(["critical removal", "Back"], ["toggle", "back"], config)
        down = values.index(settings.VALUE_COMPACT)
        keys = iter(["enter", *(["down"] * down), "enter", "q", "q"])
        renders = []
        def render(_main, panel, index, rows, *_rest):
            renders.append((panel, index, list(rows)))
            return False
        with (mock.patch.object(ciel_runtime, "load_config", return_value=config),
              mock.patch.object(ciel_runtime, "get_current_provider", return_value=("anthropic", {})),
              mock.patch.object(ciel_runtime, "settings_ready_except_api_key", return_value=True),
              mock.patch.object(ciel_runtime, "prelaunch_action_index", return_value=MAIN_MENU_ACTIONS.index("dangerous-rm")),
              mock.patch.object(ciel_runtime, "preflight_lines", return_value=[]),
              mock.patch.object(ciel_runtime, "save_config") as save,
              mock.patch.object(ciel_runtime, "render_prelaunch_screen", side_effect=render),
              mock.patch.object(ciel_runtime, "read_menu_key", side_effect=lambda *_args: next(keys)),
              mock.patch.object(ciel_runtime, "enable_ansi")):
            self.assertEqual(ciel_runtime.PRELAUNCH_CANCEL, ciel_runtime.portable_prelaunch_menu([]))
        self.assertFalse(settings.compact_resume(config))
        self.assertTrue(settings.enabled(config))
        self.assertEqual(1, save.call_count)
        self.assertTrue(any("Claude compact continuation  [off]" in rows for _, _, rows in renders))
