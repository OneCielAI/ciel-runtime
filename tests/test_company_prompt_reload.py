"""Walkie 53255: a new company prompt reloads the session at the end of the running turn."""

from __future__ import annotations

import io
import json
import tempfile
import time
import unittest
import unittest.mock
from pathlib import Path

from ciel_runtime_support import runtime_input_gateway
from ciel_runtime_support.company_prompt_reload import (
    COMPANY_PROMPT_RESTART_SOURCE,
    CompanyPromptReloader,
    announced_company_prompt,
    start_router_listener,
    stop_router_listener,
)
from ciel_runtime_support.remote_instructions import (
    RemoteInstructionSynchronizer,
    auto_reload_enabled,
    company_prompt_in,
    held_company_prompt,
    panel_rows,
)
from ciel_runtime_support.runtime_launch import prepare_restart
from ciel_runtime_support.runtime_session_restart import (
    RuntimeSessionRestartControl,
    RuntimeSessionRestartRequest,
    SessionRestartPorts,
)

V8 = "c344e8a65e5e432781fce56f7eebc46e4db86863fd13ec3979b93a1cd432bac1"
V7 = "8c657195" + "0" * 56


def _event(sha8: str = V8[:8], version: int = 8) -> str:
    return json.dumps({
        "specversion": "1.0", "id": "e1", "type": "com.ciel.walkie.support.message",
        "data": {"agent_context": {"company_prompt": {"version": version, "sha8": sha8, "source": "latest"}}, "text": "hi"},
    })


def _document(version: int, sha256: str) -> str:
    return f"<!-- company-prompt version={version} sha256={sha256} source=latest -->\n# Company prompt v{version}\n"


class _Response:
    def __init__(self, body: bytes, headers=None):
        self._body = io.BytesIO(body)
        self.headers = headers or {}

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self, size=-1):
        return self._body.read(size)

    def geturl(self):
        return "https://walkie.example/api/agents/instructions/key"


class AnnouncedPromptTests(unittest.TestCase):
    def test_reads_the_company_prompt_of_a_walkie_event(self):
        self.assertEqual({"version": "8", "sha8": V8[:8]}, announced_company_prompt(_event()))

    def test_ignores_events_without_a_usable_company_prompt(self):
        self.assertEqual({}, announced_company_prompt("not json"))
        self.assertEqual({}, announced_company_prompt(json.dumps({"data": {"text": "x"}})))
        self.assertEqual({}, announced_company_prompt(_event(sha8="c344")))


class HeldPromptTests(unittest.TestCase):
    def test_headers_win_over_the_body_comment(self):
        found = company_prompt_in(_document(7, V7), {"X-Walkie-Company-Prompt-Version": "8", "X-Walkie-Company-Prompt-Sha256": V8.upper()})
        self.assertEqual({"company_prompt_version": "8", "company_prompt_sha256": V8}, found)
        self.assertEqual({"company_prompt_version": "7", "company_prompt_sha256": V7}, company_prompt_in(_document(7, V7)))
        self.assertEqual({}, company_prompt_in("# no marker"))

    def test_sync_records_the_company_prompt_and_the_newest_state_is_held(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            config = {"remote_instructions": {"enabled": True, "codex_url": "https://walkie.example/i"}}
            body = _document(8, V8).encode()
            service = RemoteInstructionSynchronizer(
                load_config=lambda: config, workspace=lambda: root / "ws", state_dir=root / "state",
                log=lambda *_a: None,
                urlopen=lambda *_a, **_k: _Response(body, {"X-Walkie-Company-Prompt-Version": "8", "X-Walkie-Company-Prompt-Sha256": V8}),
            )
            self.assertEqual("updated", service.sync("codex").status)
            state = json.loads((root / "state" / "remote-instructions-codex.json").read_text(encoding="utf-8"))
            self.assertEqual(("8", V8), (state["company_prompt_version"], state["company_prompt_sha256"]))
            # An older claude state without the fields falls back to its file's comment.
            (root / "ws" / "CLAUDE.md").write_text(_document(7, V7), encoding="utf-8")
            (root / "state" / "remote-instructions-claude.json").write_text(json.dumps({"updated_at": 1.0}), encoding="utf-8")
            self.assertEqual({"company_prompt_version": "8", "company_prompt_sha256": V8}, held_company_prompt(root / "state", root / "ws"))

    def test_a_state_written_before_the_fields_existed_reads_the_file(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "ws").mkdir()
            (root / "ws" / "AGENTS.md").write_text(_document(7, V7), encoding="utf-8")
            self.assertEqual("7", held_company_prompt(root / "state", root / "ws")["company_prompt_version"])
            self.assertEqual({}, held_company_prompt(root / "state", root / "missing"))


class ReloaderTests(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.state = Path(self.td.name)
        self.config = {"remote_instructions": {"enabled": True, "codex_url": "https://walkie.example/i"}}
        self.held = {"company_prompt_version": "7", "company_prompt_sha256": V7}
        self.queued: list[str] = []
        self.logs: list[str] = []
        self.now = 1000.0
        self.reloader = CompanyPromptReloader(
            lambda: self.config, lambda: self.held,
            lambda reason: self.queued.append(reason) or {"ok": True, "queued": True},
            self.state, lambda level, text: self.logs.append(f"{level} {text}"), clock=lambda: self.now,
        )

    def tearDown(self):
        self.td.cleanup()

    def test_a_new_prompt_queues_one_restart_per_retry_window(self):
        self.reloader.observe(_event())
        self.assertEqual(1, len(self.queued))
        self.assertIn("v8 c344e8a6 announced; held v7 8c657195", self.queued[0])
        self.assertIn("company_prompt_reload_requested announced=v8:c344e8a6 held=v7:8c657195 queued=true", self.logs[-1])
        self.now += 60
        self.reloader.observe(_event())
        self.assertEqual(1, len(self.queued))  # same prompt, still restarting: no second request
        self.now += 1800
        self.reloader.observe(_event())
        self.assertEqual(2, len(self.queued))  # the endpoint still served v7: try again after the window

    def test_the_held_prompt_and_turned_off_reloads_queue_nothing(self):
        self.held = {"company_prompt_version": "8", "company_prompt_sha256": V8}
        self.reloader.observe(_event())
        self.held = {"company_prompt_version": "7", "company_prompt_sha256": V7}
        self.config["remote_instructions"]["auto_reload"] = False
        self.reloader.observe(_event())
        self.config["remote_instructions"] = {"enabled": False}
        self.reloader.observe(_event())
        self.assertEqual([], self.queued)

    def test_auto_reload_is_on_by_default_only_with_remote_instructions(self):
        self.assertTrue(auto_reload_enabled({"remote_instructions": {"enabled": True}}))
        self.assertFalse(auto_reload_enabled({"remote_instructions": {"enabled": True, "auto_reload": False}}))
        self.assertFalse(auto_reload_enabled({}))

    def test_admitted_external_events_reach_the_router_listener(self):
        queued: list[str] = []
        start_router_listener(lambda: self.config, self.state, self.state / "ws",
                              lambda reason: queued.append(reason) or {"queued": True}, lambda *_a: None)
        try:
            appended: list[dict] = []
            gateway = runtime_input_gateway.RuntimeInputGateway(append=lambda record: appended.append(record) or record)
            gateway.submit_external_event(_event(), receiver_id="walkie", transport="sse", event_id="e1",
                                          event_type="com.ciel.walkie.support.message", event_source="walkie")
        finally:
            stop_router_listener()
        self.assertEqual(1, len(appended))
        self.assertEqual(1, len(queued))  # nothing held in an empty workspace: reload once
        self.assertEqual([], runtime_input_gateway.EXTERNAL_EVENT_LISTENERS)
        self.assertEqual("company-prompt", COMPANY_PROMPT_RESTART_SOURCE)


class RestartTests(unittest.TestCase):
    def _request(self, wait_turn: bool) -> RuntimeSessionRestartRequest:
        now = time.time()
        return RuntimeSessionRestartRequest(id="r1", source=COMPANY_PROMPT_RESTART_SOURCE, reason="v8", runtime="",
                                            target_pid=0, resume=True, requested_at=now, expires_at=now + 60, wait_turn=wait_turn)

    def test_wait_turn_survives_the_request_file(self):
        request = self._request(True)
        self.assertTrue(RuntimeSessionRestartRequest.from_mapping(request.to_mapping()).wait_turn)
        self.assertIn("wait_turn=true", request.summary())

    def test_a_company_prompt_restart_never_cuts_a_running_turn(self):
        clock = [0.0]
        pending = [self._request(True)]
        busy = [True]
        control = RuntimeSessionRestartControl(poll=lambda: pending.pop() if pending else None, clock=lambda: clock[0])
        self.assertIsNone(control.incoming(busy=lambda: busy[0]))
        clock[0] += 7200  # far past the 900 s turn-wait limit
        self.assertIsNone(control.incoming(busy=lambda: busy[0]))
        busy[0] = False
        clock[0] += 1
        self.assertEqual("r1", control.incoming(busy=lambda: busy[0]).id)

    def test_prepare_runs_before_a_relaunch_and_a_failure_does_not_stop_it(self):
        prepared: list[str] = []
        logs: list[str] = []
        prepare_restart(SessionRestartPorts(prepare=prepared.append), "codex", lambda *a: logs.append(" ".join(a)))
        self.assertEqual(["codex"], prepared)

        def broken(_runtime):
            raise OSError("offline")

        prepare_restart(SessionRestartPorts(prepare=broken), "claude", lambda *a: logs.append(" ".join(a)))
        self.assertIn("session_restart_prepare_failed runtime=claude error=OSError: offline", logs[-1])


class MenuTests(unittest.TestCase):
    def test_panel_shows_the_reload_toggle_and_the_held_prompt(self):
        rows, values = panel_rows({"remote_instructions": {"enabled": True}}, {"company_prompt_version": "8", "company_prompt_sha256": V8})
        index = values.index("auto_reload")
        self.assertEqual("Reload session when the company prompt changes  [on · held v8 c344e8a6]", rows[index])
        rows, _values = panel_rows({"remote_instructions": {"enabled": True, "auto_reload": False}})
        self.assertIn("[off · no company prompt held]", rows[index])

    def test_menu_toggle_turns_auto_reload_off_then_on(self):
        import ciel_runtime

        config = {"remote_instructions": {"enabled": True}}
        saved: list[dict] = []
        with unittest.mock.patch.object(ciel_runtime, "load_config", lambda: config), \
                unittest.mock.patch.object(ciel_runtime, "save_config", lambda cfg: saved.append(json.loads(json.dumps(cfg)))):
            first = ciel_runtime.set_remote_instruction_config("auto_reload", "toggle")
            second = ciel_runtime.set_remote_instruction_config("auto_reload", "toggle")
        self.assertFalse(saved[0]["remote_instructions"]["auto_reload"])
        self.assertTrue(saved[1]["remote_instructions"]["auto_reload"])
        self.assertIn("auto_reload=False", first[0])
        self.assertIn("auto_reload=True", second[0])


if __name__ == "__main__":
    unittest.main()
