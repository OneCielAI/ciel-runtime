"""Evidence-gated SSE self-recovery tool."""
import json
import os
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import unittest
from unittest.mock import patch

from ciel_runtime_support.channel_mcp_tools import (
    ChannelMcpRuntimeServices,
    ChannelMcpToolServices,
    dispatch_channel_mcp_tool,
)
from ciel_runtime_support.runtime_input_status import RuntimeInputStatusRepository
from ciel_runtime_support.runtime_session_restart import (
    RESTART_REQUEST_FILE_NAME,
    RuntimeSessionRestartService,
    RuntimeSessionRestartServicePorts,
)
from ciel_runtime_support.runtime_sse_recovery import (
    DEFAULT_COOLDOWN_SECONDS,
    DEFAULT_MIN_QUEUE_AGE_SECONDS,
    recover_sse_delivery,
    router_recovery,
    _admission_diagnostics,
)


def mcp_services(recover):
    return ChannelMcpToolServices(
        queue_compact=lambda *_args, **_kwargs: {}, append_message=lambda value: value,
        read_messages=lambda *_args, **_kwargs: [], store_file_path=lambda *_args: {},
        store_file_upload=lambda *_args: {}, file_message_text=lambda *_args: "",
        handle_llm_options=lambda *_args: ([], False),
        runtime=ChannelMcpRuntimeServices(recover_sse_delivery=recover),
    )


def call_mcp_tool(services, arguments, *, name="recover_sse_delivery"):
    """One real MCP tools/call over loopback HTTP; returns (payload, is_error)."""

    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from urllib.request import Request, urlopen

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            request = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            response = dispatch_channel_mcp_tool(request["id"], request["params"], services)
            raw = json.dumps(response).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def log_message(self, *_args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    worker = threading.Thread(target=server.serve_forever)
    worker.start()
    try:
        payload = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                              "params": {"name": name, "arguments": arguments}}).encode()
        request = Request(f"http://127.0.0.1:{server.server_port}/mcp", data=payload,
                          headers={"Content-Type": "application/json"})
        with urlopen(request, timeout=5) as response:
            result = json.loads(response.read())
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=5)
    return json.loads(result["result"]["content"][0]["text"]), bool(result["result"]["isError"])


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.TemporaryDirectory()
        self.state = Path(self.root.name) / "sse-recovery.json"
        self.resets = []
        self.clock = [1000.0]

    def tearDown(self):
        self.root.cleanup()

    def run_tool(self, health, args=None, paused=False, restart=None):
        return recover_sse_delivery(
            args or {},
            delivery_health=lambda: health,
            admission_paused=lambda: paused,
            restart=restart or self.restart,
            state_path=self.state,
            now=lambda: self.clock[0],
        )

    def restart(self, **kwargs):
        self.resets.append(kwargs)
        return {"ok": True, "action": "restart", "client_pid": kwargs.get("client_pid")}

    def healthy(self):
        return {"state": "not_blocked", "admission_blocked": False,
                "known_queued_count": 0, "oldest_queued_age_seconds": None,
                "queued_coverage": "complete", "history": {"coverage": "complete"}}

    def held(self):
        # The shape the read-only delivery observer emits for a real stale
        # queue.  Complete queue coverage is what makes the observed age
        # restart evidence; a launcher hold is only reported, never required.
        return {"state": "not_blocked", "reason": "", "admission_blocked": False,
                "launcher_hold_observed": False, "blocker_scope": None,
                "known_queued_count": 1, "oldest_queued_age_seconds": 400,
                "queued_coverage": "complete", "history": {"coverage": "complete"}}

    def test_defaults_and_status_are_read_only(self):
        self.assertEqual(120.0, DEFAULT_MIN_QUEUE_AGE_SECONDS)
        self.assertEqual(900.0, DEFAULT_COOLDOWN_SECONDS)
        result = self.run_tool(
            {"state": "not_blocked", "admission_blocked": False,
             "known_queued_count": 2, "oldest_queued_age_seconds": 30,
             "queued_coverage": "complete", "history": {"coverage": "complete"}},
        )
        self.assertFalse(result["reset"])
        self.assertFalse(result["problem"])
        self.assertFalse(result["evidence"]["restart_eligible"])
        self.assertEqual(2, result["evidence"]["known_queued_count"])
        self.assertEqual([], self.resets)
        self.assertFalse(self.state.exists())

    def test_reused_input_ids_are_a_problem_but_never_restart_authority(self):
        health = {**self.healthy(), "reused_id_count": 13, "reused_id_newest": 14907}
        status = self.run_tool(health)
        self.assertTrue(status["problem"])
        self.assertEqual(["input_id_reuse"], status["evidence"]["reasons"])
        self.assertEqual((13, 14907), (status["evidence"]["reused_id_count"], status["evidence"]["reused_id_newest"]))
        auto = self.run_tool(health, {"action": "auto"})
        self.assertFalse(auto["reset"])
        self.assertEqual("restart_evidence_insufficient", auto["reason"])
        self.assertEqual([], self.resets)

    def test_complete_stale_queue_queues_one_resumed_restart(self):
        result = self.run_tool(self.held(), {"action": "auto"})
        self.assertTrue(result["reset"])
        self.assertEqual("sse_delivery_recovery_queued", result["reason"])
        self.assertEqual(["queued_input_stale"], result["evidence"]["reasons"])
        self.assertEqual(1, len(self.resets))
        self.assertTrue(self.resets[0]["resume"])
        self.assertEqual("sse_delivery_recovery", self.resets[0]["reason"])
        self.assertEqual(0, self.resets[0]["client_pid"])
        record = json.loads(self.state.read_text(encoding="utf-8"))
        self.assertEqual(1, record["count"])
        self.assertEqual(["queued_input_stale"], record["reasons"])

    def test_compaction_pause_does_not_authorize_a_restart(self):
        result = self.run_tool(self.held(), {"action": "auto"}, paused=True)
        self.assertTrue(result["problem"])
        self.assertFalse(result["reset"])
        self.assertEqual("compaction_admission_paused", result["reason"])
        self.assertFalse(result["evidence"]["restart_eligible"])
        self.assertTrue(result["evidence"]["pause_observable"])
        self.assertIs(True, result["evidence"]["admission_paused"])
        self.assertEqual([], self.resets)
        self.assertFalse(self.state.exists())

    def test_stale_queue_without_complete_coverage_is_not_restart_evidence(self):
        for coverage in (None, "partial", "unknown"):
            with self.subTest(coverage=coverage):
                health = {"state": "not_blocked", "admission_blocked": False,
                          "known_queued_count": 4, "oldest_queued_age_seconds": 400}
                if coverage is not None:
                    health["queued_coverage"] = coverage
                result = self.run_tool(health, {"action": "reset"})
                self.assertTrue(result["problem"])
                self.assertFalse(result["reset"])
                self.assertEqual("restart_evidence_insufficient", result["reason"])
                self.assertFalse(result["evidence"]["restart_eligible"])
        self.assertEqual([], self.resets)
        self.assertFalse(self.state.exists())

    def test_admission_blocked_without_stale_queue_is_not_restart_authority(self):
        health = {"state": "not_blocked", "admission_blocked": True,
                  "known_queued_count": 0, "oldest_queued_age_seconds": None,
                  "queued_coverage": "complete", "history": {"coverage": "complete"}}
        result = self.run_tool(health, {"action": "auto"})
        self.assertTrue(result["problem"])
        self.assertIn("admission_blocked", result["evidence"]["reasons"])
        self.assertFalse(result["reset"])
        self.assertEqual("restart_evidence_insufficient", result["reason"])
        self.assertEqual([], self.resets)

    def test_recent_queue_is_not_reset(self):
        health = {"state": "not_blocked", "admission_blocked": False,
                  "known_queued_count": 4, "oldest_queued_age_seconds": 5,
                  "queued_coverage": "complete", "history": {"coverage": "complete"}}
        result = self.run_tool(health, {"action": "auto"})
        self.assertFalse(result["reset"])
        self.assertEqual("no_sse_delivery_problem", result["reason"])

    def test_cooldown_suppresses_second_reset_and_force_cannot_bypass(self):
        health = self.held()
        self.assertTrue(self.run_tool(health, {"action": "auto"})["reset"])
        second = self.run_tool(health, {"action": "auto"})
        self.assertFalse(second["reset"])
        self.assertEqual("cooldown_active", second["reason"])
        self.assertGreater(second["cooldown_remaining_seconds"], 0)
        forced = self.run_tool(health, {"action": "reset", "force": True})
        self.assertFalse(forced["ok"])
        self.assertIn("cooldown", forced["error"])
        self.assertEqual(1, len(self.resets))
        self.assertEqual(1, json.loads(self.state.read_text(encoding="utf-8"))["count"])

    def test_cooldown_expires(self):
        health = self.held()
        self.run_tool(health, {"action": "auto"})
        self.clock[0] += 901
        self.assertTrue(self.run_tool(health, {"action": "auto"})["reset"])

    def test_refused_restart_keeps_cooldown_reservation(self):
        result = self.run_tool(self.held(), {"action": "auto"},
                               restart=lambda **_kwargs: {"ok": False})
        self.assertFalse(result["reset"])
        self.assertEqual("restart_refused", result["reason"])
        self.assertTrue(self.state.exists())
        self.assertEqual("cooldown_active", self.run_tool(self.held(), {"action": "auto"})["reason"])

    def test_unobservable_health_does_not_reset(self):
        def broken():
            raise OSError("unreadable")
        result = recover_sse_delivery(
            {"action": "auto"}, delivery_health=broken, admission_paused=None,
            restart=self.restart, state_path=self.state, now=lambda: 1.0,
        )
        self.assertFalse(result["problem"])
        self.assertFalse(result["evidence"]["observable"])
        self.assertEqual([], self.resets)

    def test_invalid_action_and_pid_are_rejected(self):
        self.assertFalse(self.run_tool(self.healthy(), {"action": "now"})["ok"])
        result = self.run_tool(self.healthy(), {"action": "reset", "client_pid": "x"},
                               paused=True)
        self.assertFalse(result["ok"])

    def test_initial_clock_below_cooldown_is_not_a_previous_reset(self):
        self.clock[0] = 1.0
        self.assertTrue(self.run_tool(self.held(), {"action": "auto"})["reset"])

    def test_zero_timestamp_is_not_a_previous_reset(self):
        self.state.write_text(json.dumps({"last_reset_at": 0, "count": 0}), encoding="utf-8")
        self.clock[0] = 1.0
        self.assertTrue(self.run_tool(self.held(), {"action": "auto"})["reset"])

    def test_enforced_minimums_and_force_rejection(self):
        args = {"action": "auto", "cooldown_seconds": 0, "min_queue_age_seconds": 1}
        recent = {"state": "not_blocked", "admission_blocked": False,
                  "known_queued_count": 1, "oldest_queued_age_seconds": 100,
                  "queued_coverage": "complete", "history": {"coverage": "complete"}}
        self.assertFalse(self.run_tool(recent, args)["problem"])
        self.assertFalse(self.state.exists())
        self.assertTrue(self.run_tool(self.held(), args)["reset"])
        second = self.run_tool(self.held(), args)
        self.assertFalse(second["reset"])
        self.assertEqual(900, second["cooldown_seconds"])
        self.assertFalse(self.run_tool(self.held(), {"action": "auto", "force": True})["ok"])
        self.assertEqual(1, len(self.resets))

    def test_invalid_arguments_rejected_even_without_evidence(self):
        for key in ("cooldown_seconds", "min_queue_age_seconds"):
            for value in (float("nan"), float("inf"), float("-inf"), -1, True, "invalid", None):
                with self.subTest(key=key, value=value):
                    self.assertFalse(self.run_tool(self.healthy(), {key: value})["ok"])
        for value in (True, 1.5, "123", -1, float("inf")):
            self.assertFalse(self.run_tool(self.healthy(), {"client_pid": value})["ok"])
        self.assertFalse(self.run_tool(self.healthy(), {"force": "true"})["ok"])
        self.assertFalse(self.run_tool(self.healthy(), {"action": []})["ok"])

    def test_nonfinite_health_is_not_restart_authority(self):
        for value in (float("inf"), float("nan")):
            result = self.run_tool({"known_queued_count": value,
                                    "oldest_queued_age_seconds": value,
                                    "queued_coverage": "complete"}, {"action": "auto"})
            self.assertFalse(result["reset"])
            json.dumps(result, allow_nan=False)

    def test_corrupt_or_unreadable_record_fails_closed(self):
        for raw in ("not-json", "[]", "{}", json.dumps({"last_reset_at": float("nan"), "count": 1})):
            self.state.write_text(raw, encoding="utf-8")
            result = self.run_tool(self.held(), {"action": "auto", "force": False})
            self.assertFalse(result["ok"])
            self.assertEqual([], self.resets)
        with patch("ciel_runtime_support.runtime_sse_recovery._read_reset_record", side_effect=PermissionError):
            self.assertFalse(self.run_tool(self.held(), {"action": "reset", "force": True})["ok"])
        self.assertEqual([], self.resets)

    def test_failed_reservation_never_calls_restart(self):
        with patch("ciel_runtime_support.runtime_sse_recovery._write_reset_record", side_effect=PermissionError):
            result = self.run_tool(self.held(), {"action": "auto"})
        self.assertFalse(result["ok"])
        self.assertEqual([], self.resets)

    def test_simultaneous_calls_queue_one_restart(self):
        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(lambda _: self.run_tool(self.held(), {"action": "auto"}), range(24)))
        self.assertEqual(1, len(self.resets))
        self.assertEqual(1, sum(result["reset"] for result in results))
        self.assertEqual(1, json.loads(self.state.read_text())["count"])

    def test_clock_reversal_remains_in_cooldown(self):
        self.assertTrue(self.run_tool(self.held(), {"action": "auto"})["reset"])
        self.clock[0] -= 100
        self.assertFalse(self.run_tool(self.held(), {"action": "auto"})["reset"])

    def test_pause_observation_failure_does_not_authorize_reset(self):
        def unavailable():
            raise OSError("unavailable")
        result = recover_sse_delivery({"action": "auto"}, delivery_health=self.held,
                                      admission_paused=unavailable, restart=self.restart,
                                      state_path=self.state, now=lambda: 1000)
        self.assertFalse(result["reset"])
        self.assertEqual("restart_evidence_insufficient", result["reason"])
        self.assertFalse(result["evidence"]["pause_observable"])

    def test_restart_exception_is_reserved_not_retried(self):
        def unavailable(**_kwargs):
            raise OSError("unavailable")
        first = self.run_tool(self.held(), {"action": "auto"}, restart=unavailable)
        self.assertFalse(first["reset"])
        self.assertEqual("restart_failed:OSError", first["reason"])
        self.assertFalse(self.run_tool(self.held(), {"action": "auto"})["reset"])
        self.assertEqual([], self.resets)

    def test_router_only_targets_one_registered_instance_client(self):
        outer = self
        class Service:
            def __init__(self, clients):
                self.registered = clients
            def clients(self):
                return self.registered
            def queue_tool(self, **kwargs):
                return outer.restart(**kwargs)
        class Compaction:
            def external_admission_paused(self):
                return False

        class Status:
            def __init__(self, path):
                self.path = path

        # A patched health observation still needs the paired private queue
        # to hold a real record before a reset can be verified.
        status = Status(Path(self.root.name) / "runtime-input-status.jsonl")
        (Path(self.root.name) / "runtime-inputs.jsonl").write_text(
            json.dumps({"id": 1, "created_at_epoch": time.time()}) + "\n", encoding="utf-8")
        with patch("ciel_runtime_support.runtime_input_delivery_health.runtime_input_delivery_health", return_value=self.held()):
            for clients, pid, allowed in (([], 99, False), ([{"pid": 1}, {"pid": 2}], 0, False),
                                         ([{"pid": 1}], 99, False), ([{"pid": 1}], 0, True)):
                with self.subTest(clients=clients, pid=pid):
                    self.state.unlink(missing_ok=True)
                    result = router_recovery({"action": "reset", "client_pid": pid}, status_repository=status,
                                             compaction=Compaction(), restart=Service(clients).queue_tool,
                                             root=Path(self.root.name))
                    self.assertEqual(allowed, result["reset"])
        self.assertEqual(1, len(self.resets))
        self.assertEqual(1, self.resets[0]["client_pid"])

    def test_actual_mcp_http_fixture_dispatches_recovery(self):
        services = mcp_services(lambda args: self.run_tool(self.held(), args))
        tool, is_error = call_mcp_tool(services, {"action": "auto"})
        self.assertFalse(is_error)
        self.assertTrue(tool["reset"])
        self.assertEqual(1, len(self.resets))
        self.assertTrue(self.resets[0]["resume"])

    def test_actual_status_queue_and_restart_service_over_mcp(self):
        instance = Path(self.root.name) / "instance"
        clients = instance / "router-clients"
        clients.mkdir(parents=True)
        pid = os.getpid()
        (clients / f"{pid}.json").write_text(json.dumps({
            "pid": pid, "workspace": "C:/work",
            "started_at": "2026-10-21T00:00:00", "router_port": 9611,
        }), encoding="utf-8")
        logs = []
        restart_service = RuntimeSessionRestartService(RuntimeSessionRestartServicePorts(
            instance_dir=instance,
            instances_root=Path(self.root.name),
            is_running=lambda candidate: candidate == pid,
            log=lambda level, message: logs.append(f"{level} {message}"),
            workspace_digest=lambda _path: "recovery",
            unisolated_test=lambda: False,
        ))
        status_path = instance / "runtime-input-status.jsonl"
        inputs_path = instance / "runtime-inputs.jsonl"
        cursor_path = instance / "channel-llm-cursor.json"
        status = RuntimeInputStatusRepository(status_path, lambda **_kwargs: None,
                                              lambda *_args: None, threading.RLock())
        queued_at = time.time() - 400.0
        status_path.write_bytes(b"".join(json.dumps(row).encode() + b"\n" for row in (
            {"request_id": 11, "status": "queued", "updated_at_epoch": queued_at},
            {"request_id": 12, "status": "queued", "updated_at_epoch": queued_at},
            {"request_id": 12, "status": "replied", "updated_at_epoch": queued_at + 10.0},
        )))
        inputs_path.write_bytes(b"".join(json.dumps(row).encode() + b"\n" for row in (
            {"id": 11, "created_at_epoch": queued_at, "channel": "external:default"},
            {"id": 12, "created_at_epoch": queued_at, "channel": "external:default"},
        )))
        cursor_path.write_text(json.dumps({"last_id": 9}) + "\n", encoding="utf-8")

        class Compaction:
            paused = True
            def external_admission_paused(self):
                return self.paused

        compaction = Compaction()
        services = mcp_services(lambda args: router_recovery(
            args, status_repository=status, compaction=compaction,
            restart=restart_service.queue_tool, root=instance))
        untouched = {path: (path.read_bytes(), path.stat().st_mtime_ns)
                     for path in (status_path, inputs_path, cursor_path)}
        request_path = instance / RESTART_REQUEST_FILE_NAME

        paused, is_error = call_mcp_tool(services, {"action": "auto"})
        self.assertFalse(is_error)
        self.assertFalse(paused["reset"])
        self.assertEqual("compaction_admission_paused", paused["reason"])
        self.assertIs(True, paused["evidence"]["admission_paused"])
        self.assertTrue(paused["evidence"]["pause_observable"])
        self.assertEqual(1, paused["evidence"]["known_queued_count"])
        self.assertFalse(request_path.exists())

        compaction.paused = False
        queued, is_error = call_mcp_tool(services, {"action": "auto", "reason": "stale queue"})
        self.assertFalse(is_error)
        self.assertTrue(queued["reset"])
        self.assertTrue(queued["restart"]["queued"])
        self.assertIs(True, queued["restart"]["request"]["resume"])
        self.assertEqual(pid, queued["restart"]["request"]["target_pid"])
        request = json.loads(request_path.read_text(encoding="utf-8"))
        self.assertEqual(pid, request["target_pid"])
        self.assertIs(True, request["resume"])
        self.assertEqual("ciel-runtime-router-tool", request["source"])
        self.assertEqual("stale queue", request["reason"])
        queued_request = request_path.read_bytes()

        second, is_error = call_mcp_tool(services, {"action": "auto"})
        self.assertFalse(is_error)
        self.assertFalse(second["reset"])
        self.assertEqual("cooldown_active", second["reason"])
        self.assertGreater(second["cooldown_remaining_seconds"], 0)
        self.assertEqual(queued_request, request_path.read_bytes())

        for path, (raw, modified) in untouched.items():
            self.assertEqual(raw, path.read_bytes(), path.name)
            self.assertEqual(modified, path.stat().st_mtime_ns, path.name)
        self.assertEqual(1, sum("runtime_session_restart_queued" in line for line in logs))

    def test_partial_private_history_never_authorizes_restart(self):
        instance = Path(self.root.name) / "partial"
        instance.mkdir()
        status_path = instance / "runtime-input-status.jsonl"
        inputs_path = instance / "runtime-inputs.jsonl"
        status = RuntimeInputStatusRepository(status_path, lambda **_kwargs: None,
                                              lambda *_args: None, threading.RLock())
        queued_at = time.time() - 400.0
        status_path.write_bytes(
            json.dumps({"request_id": 1, "status": "queued",
                        "updated_at_epoch": queued_at}).encode() + b"\n"
            + b"{not json}" + b"\n")
        inputs_path.write_bytes(json.dumps(
            {"id": 1, "created_at_epoch": queued_at}).encode() + b"\n")
        class Compaction:
            def external_admission_paused(self):
                return False
        result = router_recovery({"action": "auto"}, status_repository=status,
                                 compaction=Compaction(), restart=self.restart, root=instance)
        self.assertEqual(1, result["evidence"]["known_queued_count"])
        self.assertEqual("partial", result["evidence"]["queued_coverage"])
        self.assertTrue(result["problem"])
        self.assertFalse(result["reset"])
        self.assertEqual("restart_evidence_insufficient", result["reason"])
        self.assertEqual([], self.resets)

    def test_status_only_history_without_a_real_queue_never_resets(self):
        # The delivery observer treats a missing queue file as a complete
        # empty queue, so historical queued status rows alone must not
        # authorize a restart.  Only a real, non-empty paired queue can.
        class Compaction:
            def external_admission_paused(self):
                return False
        for case in ("missing", "empty", "directory", "damaged"):
            with self.subTest(case=case):
                instance = Path(self.root.name) / case
                instance.mkdir()
                status_path = instance / "runtime-input-status.jsonl"
                inputs_path = instance / "runtime-inputs.jsonl"
                queued_at = time.time() - 400.0
                status_path.write_text(json.dumps({
                    "request_id": 1, "status": "queued", "updated_at_epoch": queued_at,
                }) + "\n", encoding="utf-8")
                if case == "empty":
                    inputs_path.write_bytes(b"")
                elif case == "directory":
                    inputs_path.mkdir()
                elif case == "damaged":
                    inputs_path.write_bytes(b"{not json}\n")
                status = RuntimeInputStatusRepository(status_path, lambda **_kwargs: None,
                                                      lambda *_args: None, threading.RLock())

                def snapshot():
                    return {path.name: (path.stat().st_mtime_ns, path.stat().st_size)
                            for path in instance.iterdir()}

                before = snapshot()
                # Status is strictly read-only: no locks, files or directories.
                observed = router_recovery({"action": "status"}, status_repository=status,
                                           compaction=Compaction(), restart=self.restart,
                                           root=instance)
                self.assertEqual(0, observed["evidence"]["known_queued_count"])
                self.assertIsNone(observed["evidence"]["oldest_queued_age_seconds"])
                self.assertFalse(observed["evidence"]["restart_eligible"])
                self.assertEqual(before, snapshot())

                result = router_recovery({"action": "auto"}, status_repository=status,
                                         compaction=Compaction(), restart=self.restart,
                                         root=instance)
                self.assertFalse(result["problem"])
                self.assertEqual(0, result["evidence"]["known_queued_count"])
                self.assertFalse(result["evidence"]["restart_eligible"])
                self.assertFalse(result["reset"])
                self.assertEqual("no_sse_delivery_problem", result["reason"])
                self.assertEqual([], self.resets)
                self.assertFalse((instance / "sse-recovery.json").exists())
                after = {name: value for name, value in snapshot().items()
                         if not name.startswith("sse-recovery.json")}
                self.assertEqual(before, after)

    def test_owned_pipeline_exact_retained_nonterminal_id_matrix(self):
        """Real history/queue -> router recovery -> sole local restart fixture."""
        from ciel_runtime_support.runtime_input_delivery_health import runtime_input_delivery_health
        outer = self
        class Service:
            def clients(self):
                return [{"pid": os.getpid()}]
            def queue_tool(self, **kwargs):
                return outer.restart(**kwargs)
            def uncertain(self, **kwargs):
                raise OSError("uncertain fixture restart")
        class Compaction:
            def external_admission_paused(self):
                return False
        for case in ("mismatch", "recent_B", "terminal_B", "uncertain_B", "removed",
                     "corrupt_queue", "torn_queue", "partial_history", "changed_history",
                     "reuse", "retained_stale", "cooldown", "uncertain_restart"):
            with self.subTest(case=case):
                instance = Path(self.root.name) / case
                instance.mkdir()
                status_path = instance / "runtime-input-status.jsonl"
                queue_path = instance / "runtime-inputs.jsonl"
                cursor_path = instance / "channel-llm-cursor.json"
                stamp = time.time() - 400
                rows = [{"request_id": 1, "status": "queued", "updated_at_epoch": stamp}]
                queue = [{"id": 2, "created_at_epoch": stamp}]
                if case == "recent_B":
                    rows.append({"request_id": 2, "status": "queued", "updated_at_epoch": time.time()})
                    queue[0]["created_at_epoch"] = time.time()
                elif case in ("terminal_B", "uncertain_B", "reuse"):
                    rows.append({"request_id": 2, "status": "interrupted_uncertain" if case == "uncertain_B"
                                 else "replied", "updated_at_epoch": stamp - 10})
                elif case != "mismatch":
                    queue[0]["id"] = 1
                status_path.write_bytes(b"".join(json.dumps(row).encode() + b"\n" for row in rows))
                queue_path.write_bytes(b"".join(json.dumps(row).encode() + b"\n" for row in queue))
                cursor_path.write_text('{"last_id": 0}\n', encoding="utf-8")
                status = RuntimeInputStatusRepository(status_path, lambda **_kw: None,
                                                     lambda *_a: None, threading.RLock())
                if case == "removed":
                    self.assertEqual(1, runtime_input_delivery_health(status)["known_queued_count"])
                    queue_path.write_bytes(b"")
                elif case in ("corrupt_queue", "torn_queue"):
                    with queue_path.open("ab") as stream:
                        stream.write(b"{broken}\n" if case == "corrupt_queue" else b'{"id":')
                elif case == "partial_history":
                    with status_path.open("ab") as stream:
                        stream.write(b"{broken}\n")
                before = {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in
                          (status_path, queue_path, cursor_path)}
                before_calls = len(self.resets)
                original_scan = status._scan_history
                def changing_scan():
                    result = original_scan()
                    with status_path.open("ab") as stream:
                        stream.write(json.dumps({"request_id": 1, "status": "replied",
                                                 "updated_at_epoch": time.time()}).encode() + b"\n")
                    return result
                service = Service()
                restart = service.uncertain if case == "uncertain_restart" else service.queue_tool
                with patch.object(RuntimeInputStatusRepository, "_scan_history",
                                  side_effect=changing_scan if case == "changed_history" else original_scan):
                    result = router_recovery({"action": "auto"}, status_repository=status,
                                             compaction=Compaction(), restart=restart, root=instance)
                allowed = case in ("retained_stale", "cooldown")
                self.assertEqual(allowed, result["reset"], result)
                self.assertEqual(before_calls + int(allowed), len(self.resets))
                if case in ("cooldown", "uncertain_restart"):
                    again = router_recovery({"action": "auto"}, status_repository=status,
                                            compaction=Compaction(), restart=service.queue_tool, root=instance)
                    self.assertEqual("cooldown_active", again["reason"])
                    self.assertFalse(again["reset"])
                    self.assertEqual(before_calls + int(allowed), len(self.resets))
                if case != "changed_history":
                    for p, identity in before.items():
                        self.assertEqual(identity, (p.read_bytes(), p.stat().st_mtime_ns), p.name)
                print("owned-pipeline", case, "reset=", result["reset"], "reason=", result["reason"],
                      "queued=", result["evidence"]["known_queued_count"],
                      "coverage=", result["evidence"]["queued_coverage"])

    def test_oversized_record_fails_closed(self):
        self.state.write_text(" " * (64 * 1024 + 1), encoding="utf-8")
        self.assertFalse(self.run_tool(self.held(), {"action": "auto"})["ok"])
        self.assertEqual([], self.resets)


if __name__ == "__main__":
    unittest.main()


class AdmissionDiagnosticsTests(unittest.TestCase):
    def test_missing_and_invalid_do_not_write(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "absent"
            self.assertFalse(_admission_diagnostics(root)["identity"]["observable"])
            self.assertFalse(root.exists())
            root.mkdir()
            path = root / "compaction-scope.json"
            for raw in (b"bad", b"[]", b"x" * (64 * 1024 + 1)):
                path.write_bytes(raw)
                self.assertEqual("unreadable", _admission_diagnostics(root)["identity"]["reason"])
                self.assertEqual(raw, path.read_bytes())

    def test_payload_free_identity_and_gate(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            scope = root / "compaction-scope.json"
            gate = root / "sse-gate-status.json"
            scope.write_text(json.dumps({"epoch": "private-epoch", "session_id": "private-session",
                "identity_since": 950, "request_observed": False, "launcher_pid": 42,
                "messaging_socket_path": "private-socket", "cwd": "private-cwd"}))
            gate.write_text(json.dumps({"enabled": True, "paused": True, "state": "held",
                "reason": "identity_pending", "updated_at": 999, "observer_pid": 43}))
            before = {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in (scope, gate)}
            with patch("ciel_runtime_support.runtime_sse_recovery.time.time", return_value=1000):
                value = _admission_diagnostics(root)
            self.assertEqual(50, value["identity"]["identity_age_seconds"])
            self.assertEqual(64, len(value["identity"]["session_id_sha256"]))
            self.assertEqual(43, value["gate_snapshot"]["observer_pid"])
            self.assertEqual(1, value["gate_snapshot"]["age_seconds"])
            self.assertNotIn("private-", json.dumps(value))
            self.assertEqual(before, {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in (scope, gate)})
