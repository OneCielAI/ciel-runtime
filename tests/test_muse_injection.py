"""Muse injection paths: MSP client, option model, and capability rules."""

import json
import sys
import textwrap
import unittest

from ciel_runtime_support.muse_injection import (
    MuseInjectionError,
    MuseInjectionOptions,
    MuseInjectionPorts,
    MuseInjectionService,
    capability_for,
    exec_argv,
    if_busy_for_intent,
    options_from_config,
    options_from_payload,
    render_turn_parts,
    serve_host_argv,
    session_message_argv,
    session_message_body,
    session_message_state_from_probe,
)
from ciel_runtime_support.muse_msp import (
    MuseCommandIds,
    MuseMspConnection,
    MuseMspError,
    muse_approval_mode,
    muse_command_id,
)


FAKE_HOST = textwrap.dedent(
    """
    import json, sys

    initialized = False
    session_id = "01a0b800-0000-7000-8000-000000000001"

    def reply(request_id, result):
        sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": request_id, "result": result}) + "\\n")
        sys.stdout.flush()

    def fail(request_id, code, message, kind):
        sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message, "data": {"kind": kind}}}) + "\\n")
        sys.stdout.flush()

    def notify(method, params):
        sys.stdout.write(json.dumps({"jsonrpc": "2.0", "method": method, "params": params, "emittedAtMs": 1}) + "\\n")
        sys.stdout.flush()

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        frame = json.loads(line)
        method = frame.get("method")
        request_id = frame.get("id")
        if method == "initialize":
            if not frame["params"]["clientInfo"]["name"].replace("_", "").isalnum():
                fail(request_id, -32602, "invalid initialize params: clientInfo.name", "invalidParams")
                continue
            reply(request_id, {"schema": {"version": 1}, "serverInfo": {"name": "muse", "version": "fake"}})
            continue
        if method == "initialized":
            initialized = True
            continue
        if not initialized:
            fail(request_id, -32600, "Not initialized", "notInitialized")
            continue
        if method == "session/start":
            reply(request_id, {"session": {"sessionId": session_id, "providerId": frame["params"].get("providerId"), "status": "idle"}})
            notify("session/started", {"session": {"sessionId": session_id, "status": "idle"}})
            continue
        if method == "session/resume":
            reply(request_id, {"session": {"sessionId": frame["params"]["sessionId"], "status": "idle"}})
            continue
        if method == "turn/start":
            params = frame["params"]
            disposition = "started"
            if params.get("ifBusy") == "steer":
                disposition = "steered"
            elif params.get("ifBusy") == "replace":
                disposition = "replaced"
            reply(request_id, {"commandId": params["commandId"], "turnId": params["commandId"], "status": "accepted", "disposition": disposition, "startedNewTurn": disposition == "started"})
            notify("turn/started", {"turnId": params["commandId"], "sessionId": params["sessionId"]})
            notify("turn/completed", {"turnId": params["commandId"], "sessionId": params["sessionId"]})
            continue
        if method == "turn/steer":
            params = frame["params"]
            if not params.get("expectedTurnId"):
                fail(request_id, -32602, "turn/steer requires expectedTurnId", "invalidParams")
                continue
            reply(request_id, {"commandId": params["commandId"], "turnId": params["expectedTurnId"], "status": "accepted"})
            continue
        fail(request_id, -32601, "method not found", "methodNotFound")
    """
)


class MuseCommandIdTests(unittest.TestCase):
    def test_command_ids_are_uuid7(self):
        value = muse_command_id(seed="probe", now_ms=1_700_000_000_000)
        head, _, tail = value.partition("-")
        self.assertEqual(8, len(head))
        self.assertEqual("7", value[14])
        self.assertIn(value[19], "89ab")
        self.assertEqual(36, len(value))

    def test_command_ids_are_time_ordered(self):
        early = muse_command_id(seed="a", now_ms=1_000)
        late = muse_command_id(seed="a", now_ms=2_000)
        self.assertLess(early, late)

    def test_command_ids_reuse_one_id_per_key(self):
        ids = MuseCommandIds()

        first = ids.for_key("message-1")
        retry = ids.for_key("message-1")
        rotated = ids.for_key("message-1", fresh=True)

        self.assertEqual(first, retry)
        self.assertNotEqual(first, rotated)


class MuseApprovalModeTests(unittest.TestCase):
    def test_cli_spellings_map_onto_the_wire_enum(self):
        self.assertEqual("allowAll", muse_approval_mode("never"))
        self.assertEqual("onRequest", muse_approval_mode("on-request"))
        self.assertEqual("promptUnmatched", muse_approval_mode("untrusted"))
        self.assertEqual("denyUnmatched", muse_approval_mode("denyUnmatched"))

        with self.assertRaises(ValueError):
            muse_approval_mode("yolo")


class MuseMspConnectionTests(unittest.TestCase):
    """Drive the client against a scripted host over real stdio."""

    def connect(self):
        connection = MuseMspConnection.start(
            [sys.executable, "-c", FAKE_HOST],
            env={},
            request_timeout_seconds=10.0,
        )
        self.addCleanup(connection.close)
        return connection

    def test_handshake_gates_the_host(self):
        connection = self.connect()

        with self.assertRaises(MuseMspError) as early:
            connection.session_start(command_id=muse_command_id())
        self.assertEqual("notInitialized", early.exception.kind)

        result = connection.initialize(client_name="ciel_runtime", client_version="1.0")
        self.assertEqual("fake", result["serverInfo"]["version"])

    def test_invalid_client_name_is_reported(self):
        connection = self.connect()

        with self.assertRaises(MuseMspError) as error:
            connection.initialize(client_name="ciel-runtime", client_version="1.0")

        self.assertEqual("invalidParams", error.exception.kind)

    def test_turn_start_carries_the_requested_parameters(self):
        connection = self.connect()
        connection.initialize()
        session = connection.session_start(command_id=muse_command_id(), provider_id="echo")

        result = connection.turn_start(
            command_id=muse_command_id(),
            session_id=session["session"]["sessionId"],
            text="ping",
            display_text="ping (preview)",
            if_busy="replace",
            reasoning_effort="high",
        )

        self.assertEqual("replaced", result["disposition"])
        self.assertFalse(result["startedNewTurn"])

    def test_turn_steer_requires_the_expected_turn(self):
        connection = self.connect()
        connection.initialize()
        session = connection.session_start(command_id=muse_command_id())
        session_id = session["session"]["sessionId"]

        steered = connection.turn_steer(
            command_id=muse_command_id(),
            session_id=session_id,
            expected_turn_id="turn-1",
            text="more",
        )
        self.assertEqual("turn-1", steered["turnId"])

        with self.assertRaises(MuseMspError) as error:
            connection.turn_steer(
                command_id=muse_command_id(),
                session_id=session_id,
                expected_turn_id="",
                text="more",
            )
        self.assertEqual("invalidParams", error.exception.kind)

    def test_notifications_stream_and_wait(self):
        connection = self.connect()
        connection.initialize()
        session = connection.session_start(command_id=muse_command_id())
        session_id = session["session"]["sessionId"]

        connection.turn_start(
            command_id=muse_command_id(), session_id=session_id, text="ping"
        )

        completed = connection.wait_for("turn/completed", timeout=5.0)

        self.assertIsNotNone(completed)
        self.assertEqual(session_id, completed.params["sessionId"])


class MuseInjectionOptionsTests(unittest.TestCase):
    def test_payload_parsing_carries_parameters(self):
        options = options_from_payload(
            {
                "transport": "msp",
                "intent": "steer",
                "wake": "now",
                "sandbox": "disable_shell, disable_write",
                "display_text": "preview",
                "session": "01a0b8",
                "unknown_key": 1,
            }
        )

        self.assertEqual(("disable_shell", "disable_write"), options.sandbox)
        self.assertEqual("steer", options.intent)
        self.assertEqual({"unknown_key": 1}, dict(options.extras))

    def test_config_declares_defaults_and_overlays_win(self):
        config = {
            "muse": {
                "injection": {
                    "transport": "msp",
                    "intent": "steer",
                    "sandbox": ["disable_shell"],
                }
            },
            "providers": {
                "meta": {"current_model": "muse-spark-1.3", "base_url": "https://api.meta.ai/v1"}
            },
        }

        declared = options_from_config(config)
        overlay = options_from_config(config, overlay={"intent": "queue", "session": "api-review"})

        self.assertEqual("msp", declared.transport)
        self.assertEqual("steer", declared.intent)
        self.assertEqual(("disable_shell",), declared.sandbox)
        self.assertEqual("muse-spark-1.3", declared.model)
        self.assertEqual("queue", overlay.intent)
        self.assertEqual("api-review", overlay.session)
        self.assertEqual("steer", declared.intent)

    def test_validation_rejects_unknown_values(self):
        for payload in (
            {"transport": "carrier-pigeon"},
            {"intent": "shout"},
            {"wake": "eventually"},
            {"sandbox": "disable_everything"},
        ):
            with self.subTest(payload=payload):
                with self.assertRaises(MuseInjectionError):
                    options_from_payload(payload)

    def test_intent_maps_onto_the_busy_disposition(self):
        self.assertEqual("queue", if_busy_for_intent("queue"))
        self.assertEqual("steer", if_busy_for_intent("steer"))
        self.assertEqual("replace", if_busy_for_intent("replace"))
        self.assertIsNone(if_busy_for_intent("notify"))

    def test_serve_host_argv_carries_sandbox_and_durability(self):
        argv = serve_host_argv(
            "muse",
            MuseInjectionOptions(transport="msp", sandbox=("disable_shell", "disable_write")),
        )
        self.assertEqual(["muse", "serve", "--no-session-log", "--disable-shell", "--disable-write"], argv)

        durable = serve_host_argv(
            ["wsl", "-e", "muse"], MuseInjectionOptions(transport="msp", durable=True)
        )
        self.assertEqual(["wsl", "-e", "muse", "serve"], durable)

    def test_exec_argv_carries_headless_parameters(self):
        argv = exec_argv(
            "muse",
            MuseInjectionOptions(
                transport="exec",
                provider="echo",
                model="muse-spark-1.3",
                base_url="http://127.0.0.1:9611/v1",
                permission_profile="read-only",
                reasoning_effort="low",
            ),
            prompt_file="/tmp/prompt.txt",
        )

        self.assertEqual(
            [
                "muse",
                "exec",
                "--prompt-file",
                "/tmp/prompt.txt",
                "--json",
                "--provider",
                "echo",
                "--model",
                "muse-spark-1.3",
                "--base-url",
                "http://127.0.0.1:9611/v1",
                "--reasoning-effort",
                "low",
                "--permission-profile",
                "read-only",
                "--no-session-log",
            ],
            argv,
        )

    def test_session_message_argv_requires_a_target(self):
        options = MuseInjectionOptions(transport="session-message", session="api-review-482731")
        argv = session_message_argv("muse", options, reply_to="reply-token")

        self.assertEqual(
            [
                "muse",
                "session-message",
                "send",
                "--target",
                "api-review-482731",
                "--json",
                "--in-reply-to",
                "reply-token",
            ],
            argv,
        )
        with self.assertRaises(MuseInjectionError):
            session_message_argv("muse", MuseInjectionOptions(transport="session-message"))

    def test_session_message_body_states_the_delivery_behaviour(self):
        steer = session_message_body("check the build", MuseInjectionOptions(intent="steer"))
        queued = session_message_body("check the build", MuseInjectionOptions(intent="queue"))
        notify = session_message_body("build passed", MuseInjectionOptions(intent="notify"))

        self.assertTrue(steer.startswith("Steer the active turn"))
        self.assertTrue(queued.startswith("Queue this for your next turn"))
        self.assertTrue(notify.startswith("Notify only"))

    def test_ingress_state_parses_the_probe_output(self):
        self.assertEqual(
            "external_agent_ingress_closed",
            session_message_state_from_probe(
                json.dumps({"schema_version": 1, "status": "unavailable", "error_code": "external_agent_ingress_closed"})
            ),
        )
        self.assertEqual("available", session_message_state_from_probe('{"status": "available"}'))
        self.assertEqual("unknown", session_message_state_from_probe("not json"))

    def test_render_turn_parts_keeps_attachments(self):
        parts = render_turn_parts(
            "look",
            MuseInjectionOptions(extras={"images": [{"type": "image", "mediaType": "image/png", "base64Data": "AA=="}]}),
        )

        self.assertEqual(
            [
                {"type": "text", "text": "look"},
                {"type": "image", "mediaType": "image/png", "base64Data": "AA=="},
            ],
            parts,
        )


class MuseCapabilityTests(unittest.TestCase):
    def test_each_path_reports_its_parameters(self):
        msp = capability_for("msp")
        console = capability_for("console")
        exec_report = capability_for("exec", has_live_session=False)

        self.assertTrue(msp["supported"])
        self.assertIn("ifBusy=queue|steer|replace", msp["parameters"])
        self.assertTrue(console["supported"])
        self.assertIn("bracketed paste", console["parameters"])
        self.assertTrue(exec_report["supported"])
        self.assertIn("prompt file", exec_report["parameters"])

    def test_unavailable_paths_explain_themselves(self):
        self.assertFalse(capability_for("msp", has_host_connection=False)["supported"])
        self.assertFalse(
            capability_for("session-message", platform="nt")["supported"]
        )
        closed = capability_for(
            "session-message", platform="linux", session_message_state="external_agent_ingress_closed"
        )
        self.assertFalse(closed["supported"])
        self.assertEqual("msp or console", closed["alternative"])
        self.assertFalse(
            capability_for("exec", has_live_session=True)["supported"]
        )


class MuseInjectionServiceTests(unittest.TestCase):
    def build_service(self, **ports):
        recorded = {"calls": [], "logs": []}
        connection = FakeConnection(recorded)
        base = dict(
            connection=lambda _options: connection,
            command_ids=MuseCommandIds(),
            session_message_state=lambda: "available",
            platform_name=lambda: "linux",
        )
        base.update(ports)
        return MuseInjectionService(MuseInjectionPorts(**base)), recorded, connection

    def test_msp_queue_delivery_uses_the_busy_disposition(self):
        service, recorded, _connection = self.build_service()

        receipt = service.deliver(
            "check the build",
            MuseInjectionOptions(transport="msp", intent="queue"),
            session_id="session-1",
            command_key="message-1",
        )

        call = recorded["calls"][0]
        self.assertEqual("turn/start", call["method"])
        self.assertEqual("queue", call["params"]["ifBusy"])
        self.assertEqual([{"type": "text", "text": "check the build"}], call["params"]["input"])
        self.assertEqual("accepted", receipt["status"])

    def test_msp_steer_uses_the_live_turn_or_the_disposition(self):
        service, recorded, _connection = self.build_service()

        steered = service.deliver(
            "urgent",
            MuseInjectionOptions(transport="msp", intent="steer"),
            session_id="session-1",
            active_turn_id="turn-9",
        )
        self.assertEqual("turn/steer", recorded["calls"][-1]["method"])
        self.assertEqual("turn-9", recorded["calls"][-1]["params"]["expectedTurnId"])
        self.assertEqual("turn-9", steered["turn_id"])

        service.deliver(
            "urgent again",
            MuseInjectionOptions(transport="msp", intent="steer"),
            session_id="session-1",
        )
        self.assertEqual("turn/start", recorded["calls"][-1]["method"])
        self.assertEqual("steer", recorded["calls"][-1]["params"]["ifBusy"])

    def test_msp_replace_carries_display_text(self):
        service, recorded, _connection = self.build_service()

        service.deliver(
            "new direction",
            MuseInjectionOptions(transport="msp", intent="replace", display_text="new direction"),
            session_id="session-1",
        )

        params = recorded["calls"][-1]["params"]
        self.assertEqual("replace", params["ifBusy"])
        self.assertEqual("new direction", params["displayText"])

    def test_repeat_delivery_of_an_applied_key_rotates_the_command_id(self):
        # The live host answers -32030 command_id_conflict when an applied id
        # is sent again for a new command (2026-09-19), so the second delivery
        # must carry a fresh id.
        service, recorded, _connection = self.build_service()
        options = MuseInjectionOptions(transport="msp")

        first = service.deliver("first", options, session_id="session-1", command_key="message-7")
        second = service.deliver("first", options, session_id="session-1", command_key="message-7")

        self.assertNotEqual(
            recorded["calls"][0]["params"]["commandId"],
            recorded["calls"][1]["params"]["commandId"],
        )
        self.assertFalse(first["command_reused"])
        self.assertTrue(second["command_reused"])

    def test_an_explicit_command_id_is_never_rotated(self):
        service, recorded, _connection = self.build_service()
        options = MuseInjectionOptions(transport="msp", command_id="01a0b800-0000-7000-8000-000000000009")

        service.deliver("first", options, session_id="session-1", command_key="message-9")
        service.deliver("first", options, session_id="session-1", command_key="message-9")

        self.assertEqual(
            recorded["calls"][0]["params"]["commandId"],
            recorded["calls"][1]["params"]["commandId"],
        )

    def test_notify_only_is_refused_on_the_msp_path(self):
        service, _recorded, _connection = self.build_service()

        with self.assertRaises(MuseInjectionError) as error:
            service.deliver(
                "status",
                MuseInjectionOptions(transport="msp", intent="notify"),
                session_id="session-1",
            )

        self.assertIn("display-only", str(error.exception))

    def test_console_path_passes_parameters_to_the_port(self):
        seen = {}

        def console_deliver(message, options):
            seen["message"] = message
            seen["options"] = options
            return True

        service, _recorded, _connection = self.build_service(console_deliver=console_deliver)

        receipt = service.deliver(
            "paste me",
            MuseInjectionOptions(transport="console", intent="steer", wake="now"),
        )

        self.assertEqual("paste me", seen["message"])
        self.assertTrue(receipt["delivered"])

    def test_session_message_path_transports_the_body(self):
        seen = {}

        def runner(argv, body, options):
            seen["argv"] = list(argv)
            seen["body"] = body
            return 0

        service, _recorded, _connection = self.build_service(session_message_runner=runner)

        receipt = service.deliver(
            "handoff",
            MuseInjectionOptions(
                transport="session-message",
                intent="notify",
                session="api-review-482731",
            ),
        )

        self.assertIn("--target", seen["argv"])
        self.assertTrue(seen["body"].startswith("Notify only"))
        self.assertEqual(0, receipt["returncode"])

    def test_exec_path_writes_the_prompt_file(self):
        written = {}

        def writer(message):
            written["message"] = message
            return "/tmp/prompt.txt"

        service, _recorded, _connection = self.build_service(
            exec_runner=lambda argv, message, options: 0,
            write_prompt_file=writer,
        )

        receipt = service.deliver(
            "one shot",
            MuseInjectionOptions(transport="exec", provider="echo"),
            has_live_session=False,
        )

        self.assertEqual("one shot", written["message"])
        self.assertIn("--prompt-file", receipt["argv"])


class FakeConnection:
    """Records MSP calls the way a live host would answer them.

    It renames the service's keyword arguments into the wire parameter names,
    which is exactly the mapping ``MuseMspConnection`` performs, so assertions
    read the contract the host actually receives.
    """

    def __init__(self, recorded):
        self.recorded = recorded

    def _record(self, method, wire):
        self.recorded["calls"].append({"method": method, "params": dict(wire)})

    def turn_start(self, **params):
        wire = {
            "commandId": params["command_id"],
            "sessionId": params["session_id"],
            "input": params["parts"],
        }
        if params.get("display_text"):
            wire["displayText"] = params["display_text"]
        if params.get("if_busy"):
            wire["ifBusy"] = params["if_busy"]
        if params.get("reasoning_effort"):
            wire["reasoningEffort"] = params["reasoning_effort"]
        self._record("turn/start", wire)
        disposition = {"steer": "steered", "replace": "replaced"}.get(
            params.get("if_busy"), "started"
        )
        return {
            "commandId": params["command_id"],
            "turnId": params["command_id"],
            "status": "accepted",
            "disposition": disposition,
            "startedNewTurn": disposition == "started",
        }

    def turn_steer(self, **params):
        wire = {
            "commandId": params["command_id"],
            "sessionId": params["session_id"],
            "expectedTurnId": params["expected_turn_id"],
            "input": params["parts"],
        }
        self._record("turn/steer", wire)
        return {
            "commandId": params["command_id"],
            "turnId": params["expected_turn_id"],
            "status": "accepted",
        }


if __name__ == "__main__":
    unittest.main()
