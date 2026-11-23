"""ciel-runtimectl Work/SSE parity: one test group per measured gap.

Every test runs against an isolated temporary profile (the runner sets
CIEL_RUNTIME_TEST_ISOLATED=1 and a temp CIEL_RUNTIME_CONFIG_DIR); setUp refuses
to run otherwise.
"""
import argparse
import hashlib
import io
import json
import tempfile
import unittest
from contextlib import nullcontext, redirect_stderr
from pathlib import Path
from unittest.mock import patch

from ciel_runtime_support import runtime_paths
from ciel_runtime_support import work_plan_tree as tree
from ciel_runtime_support.work_parallel import WorkParallelStore

A = "claude:aaaaaaaa-1111-4111-8111-111111111111"
B = "claude:bbbbbbbb-2222-4222-8222-222222222222"
RID = "c" * 64


def ns(action, key=None, value=None, **extra):
    base = dict(action=action, key=key, value=value, json=True, session=None, confirm=False,
                expected_version=None, expected_finalization_version=None, preview_digest=None, attempt_ref=None,
                expected_settings_fingerprint=None,
                revision=None, clear_wait=[], activate=False, operation_id=None)
    base.update(extra)
    return argparse.Namespace(**base)


class Isolated(unittest.TestCase):
    def setUp(self):
        root = Path(tempfile.gettempdir()).resolve()
        config_dir = Path(runtime_paths.CONFIG_DIR).resolve()
        self.assertTrue(str(config_dir).lower().startswith(str(root).lower()), "not an isolated profile")
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.store = WorkParallelStore(self.root / "work-parallel.json")
        self.ledger = patch("ciel_runtime_support.work_parallel_mcp.ledger", return_value=self.store)
        self.ledger.start()
        self.config = {}
        self.saved = []

    def tearDown(self):
        self.ledger.stop()
        self.temp.cleanup()

    def save(self, config):
        self.saved.append(json.loads(json.dumps(config)))

    def cli(self, args):
        from ciel_runtime_support.work_settings_cli import command
        output, errors = [], io.StringIO()
        try:
            import ciel_runtime_support.work_ctl  # noqa: F401 - absent before the parity change
            scope = patch("ciel_runtime_support.work_ctl.state_dir", return_value=self.root)
        except ImportError:
            scope = nullcontext()
        with redirect_stderr(errors), scope:
            try:
                command(lambda: self.config, self.save, output.append)(args)
                code = 0
            except SystemExit as stop:
                code = stop.code
        return code, output, errors.getvalue()

    def board(self, identity, mutate=None):
        from ciel_runtime_support.work_plan_store import session_work_plan_store
        store = session_work_plan_store(self.root, identity)
        def change(doc):
            tree.add_subtree(doc, None, {"title": "fixture"})
            if mutate:
                mutate(doc)
        store.transaction(change, expected_version=store.read_snapshot()["version"])
        return store


class PriorityCtl(Isolated):
    def test_priority_action_uses_the_shared_rest_application_and_returns_exact_json_receipt(self):
        board = self.board(A)
        root = board.read()["active_root"]
        code, output, error = self.cli(ns("priority", root, "P0", session=A,
                                          expected_version=board.read()["version"],
                                          operation_id="a" * 32))
        self.assertEqual((0, ""), (code, error))
        result = json.loads(output[-1])
        self.assertEqual("a" * 32, result["priority_operation_id"])
        self.assertRegex(result["priority_operation_ref"], r"^[0-9a-f]{16}$")
        current = board.read()
        root_row = next(node for node in current["nodes"] if node["id"] == root)
        self.assertEqual("P1", root_row["priority"])
        from ciel_runtime_support.work_parallel_p0 import p0_store
        self.assertEqual("P0", p0_store(self.store).effective_priority(A, root_row))


class Gap3UnreadableLedger(Isolated):
    def unreadable(self):
        broken = patch.object(self.store, "read", side_effect=OSError("ledger unreadable"))
        broken.start()
        self.addCleanup(broken.stop)

    def test_every_key_stays_listed_with_status_and_no_info_row(self):
        from ciel_runtime_support.work_live_settings import settings
        from ciel_runtime_support.work_parallel_menu import KEYS
        ledger_keys = ("work_parallel_enabled", "work_parallel_paused",
                       *(f"work_parallel_p{n}_limit" for n in range(1, 6)), "work_parallel_launches_per_minute",
                       "work_parallel_max_rework", "work_parallel_orphans")
        self.unreadable()
        rows = {row["key"]: row for row in settings({}, {}, None)["settings"]}
        self.assertNotIn("__info__", rows)
        self.assertTrue(set(KEYS) <= set(rows))
        self.assertEqual({"unreadable"}, {rows[key].get("status") for key in ledger_keys})
        self.assertIs(True, rows["work_discovery_enabled"]["value"])

    def test_discovery_set_while_unreadable_reports_the_saved_key(self):
        self.unreadable()
        code, output, _ = self.cli(ns("set", "work_discovery_enabled", "off"))
        self.assertEqual(0, code, output)
        result = json.loads(output[0])
        self.assertEqual(["work_discovery_enabled"], [row["key"] for row in result["settings"]])
        self.assertIs(False, result["settings"][0]["value"])
        self.assertEqual(1, len(self.saved))

    def test_unknown_key_is_refused_before_anything_is_saved(self):
        code, output, _ = self.cli(ns("set", "work_parallel_p6_limit", "3"))
        self.assertEqual(1, code)
        self.assertEqual([], self.saved)
        self.assertEqual("invalid_request", json.loads(output[-1])["error"])


class Gap2TypedStatus(Isolated):
    def test_status_json_values_are_typed_and_operational_fields_present(self):
        code, output, _ = self.cli(ns("status"))
        self.assertEqual(0, code, output)
        result = json.loads(output[0])
        rows = {row["key"]: row for row in result["settings"]}
        self.assertIs(True, rows["work_parallel_enabled"]["value"])
        self.assertIs(False, rows["work_parallel_paused"]["value"])
        self.assertIs(5, rows["work_parallel_p1_limit"]["value"])
        self.assertIs(4, rows["work_parallel_launches_per_minute"]["value"])
        self.assertIs(2, rows["work_side_intake_max_active"]["value"])
        mode = rows["sse_injection_mode"]
        self.assertEqual(("enum", ["session", "btw_with_mcp"]), (mode["kind"], mode["choices"]))
        self.assertTrue(mode["label"])
        device = result["operational"]["device"]
        self.assertEqual("ok", device["status"])
        self.assertEqual(11, device["device_capacity"])
        self.assertEqual(0, device["active_slots"])
        self.assertIsInstance(device["ledger_version"], int)
        self.assertIsNone(result["operational"]["session"])

    def test_status_with_session_reports_counts_without_content(self):
        self.board(A, lambda doc: doc.setdefault("side_reviews", {}).update(
            {RID: {"state": "pending", "kind": "info", "summary": "secret summary", "recorded_at": 1.0}}))
        code, output, _ = self.cli(ns("status", session=A))
        self.assertEqual(0, code, output)
        session = json.loads(output[0])["operational"]["session"]
        for field in ("pending_reviews", "stranded", "superseded", "uncertain_wakes", "uncertain_notices",
                      "reply_outbox", "review_delivery"):
            self.assertIn(field, session)
        self.assertEqual({"unknown": 1}, session["reply_outbox"])
        self.assertNotIn("secret summary", output[0])

    def test_text_status_is_readable(self):
        self.config = {"work_mode": False}
        code, output, _ = self.cli(ns("status", json=False))
        self.assertEqual(0, code)
        self.assertIn("work_mode = off", output)
        self.assertIn("work_parallel_p1_limit = 5", output)


class SettingsCompareAndSet(Isolated):
    """settings_update pin: a stale fingerprint is refused, one of two racing writes wins."""

    def setUp(self):
        super().setUp()
        self.path = self.root / "workspace-config.json"
        self.path.write_text(json.dumps({"work_side_intake": {"max_active": 2}}), encoding="utf-8")
        from ciel_runtime_support import runtime_paths
        lock = patch.object(runtime_paths, "CONFIG_PATH", self.path)
        lock.start()
        self.addCleanup(lock.stop)

    def load(self):
        return json.loads(self.path.read_text(encoding="utf-8"))

    def write_file(self, config):
        self.path.write_text(json.dumps(config), encoding="utf-8")

    def test_fingerprint_is_stable_hex_over_values_only(self):
        from ciel_runtime_support.work_live_settings import settings, settings_fingerprint
        # An explicit discovery instruction: the language then changes labels
        # only (the default instruction is itself a localized value).
        self.write_file({**self.load(), "work_discovery": {"instruction": "find open work"}})
        first = settings_fingerprint(self.load())
        self.assertRegex(first, r"\A[0-9a-f]{64}\Z")
        self.assertEqual(first, settings_fingerprint({**self.load(), "language": "ko"}))
        self.assertNotIn("find open work", first)
        self.assertEqual(first, settings(self.load(), {}, None)["settings_fingerprint"])
        changed = self.load()
        changed["work_side_intake"]["max_active"] = 3
        self.assertNotEqual(first, settings_fingerprint(changed))

    def test_stale_fingerprint_is_refused_and_nothing_is_saved(self):
        from ciel_runtime_support.work_live_settings import FINGERPRINT_FIELD, SettingsConflict, settings
        from ciel_runtime_support.work_live_settings import settings_fingerprint
        seen = settings_fingerprint(self.load())
        # Another writer changes a different setting after we read.
        settings(self.load(), {"key": "work_side_intake_aging_seconds", "value": "17"}, self.write_file, load=self.load)
        before = self.path.read_bytes()
        with self.assertRaises(SettingsConflict):
            settings(self.load(), {"key": "work_side_intake_max_active", "value": "9", FINGERPRINT_FIELD: seen},
                     self.write_file, load=self.load)
        self.assertEqual(before, self.path.read_bytes())
        fresh = settings_fingerprint(self.load())
        settings(self.load(), {"key": "work_side_intake_max_active", "value": "9", FINGERPRINT_FIELD: fresh},
                 self.write_file, load=self.load)
        self.assertEqual({"max_active": 9, "aging_seconds": 17}, self.load()["work_side_intake"])

    def test_ctl_set_pins_and_reports_settings_conflict(self):
        from ciel_runtime_support.work_live_settings import settings_fingerprint
        seen = settings_fingerprint(self.load())
        self.config = self.load()
        self.write_file({"work_side_intake": {"max_active": 5}})  # changed elsewhere
        from ciel_runtime_support.work_settings_cli import command
        output = []
        args = ns("set", "work_side_intake_max_active", "7", expected_settings_fingerprint=seen)
        with patch("ciel_runtime_support.work_ctl.state_dir", return_value=self.root):
            with self.assertRaises(SystemExit) as stop:
                command(self.load, self.write_file, output.append)(args)
        self.assertEqual(1, stop.exception.code)
        self.assertEqual("settings_conflict", json.loads(output[-1])["error"])
        self.assertEqual(5, self.load()["work_side_intake"]["max_active"])

    def test_two_concurrent_pinned_writes_exactly_one_wins(self):
        import threading
        from ciel_runtime_support.work_live_settings import FINGERPRINT_FIELD, SettingsConflict, settings
        from ciel_runtime_support.work_live_settings import settings_fingerprint
        seen = settings_fingerprint(self.load())
        barrier, outcomes = threading.Barrier(2), {}

        def write(value):
            # The base config is read before the race: this fixture writes in
            # place (truncate, then write), so an unlocked read racing the other
            # thread's write could see an empty file (py310 shard 2, 2026-11-23:
            # JSONDecodeError at char 0). The product saves by atomic replace;
            # inside the race every read and write is under the settings lock.
            config = self.load()
            barrier.wait()
            try:
                settings(config, {"key": "work_side_intake_max_active", "value": value,
                                  FINGERPRINT_FIELD: seen}, self.write_file, load=self.load)
                outcomes[value] = "saved"
            except SettingsConflict:
                outcomes[value] = "conflict"
        threads = [threading.Thread(target=write, args=(value,)) for value in ("11", "12")]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(30)
        self.assertEqual(["conflict", "saved"], sorted(outcomes.values()))
        winner = next(value for value, outcome in outcomes.items() if outcome == "saved")
        self.assertEqual(int(winner), self.load()["work_side_intake"]["max_active"])

    def test_unpinned_write_with_reader_never_overwrites_a_concurrent_other_key(self):
        from ciel_runtime_support.work_live_settings import settings
        stale = self.load()
        settings(self.load(), {"key": "work_side_intake_aging_seconds", "value": "21"}, self.write_file, load=self.load)
        settings(stale, {"key": "work_side_intake_max_active", "value": "4"}, self.write_file, load=self.load)
        self.assertEqual({"max_active": 4, "aging_seconds": 21}, self.load()["work_side_intake"])


class Gap6SharedValidator(Isolated):
    WORDS = {"yes": True, "no": False, "1": True, "0": False, "enable": True, "disabled": False}

    def test_every_surface_accepts_the_same_words_and_reset(self):
        from ciel_runtime_support.config_value_codec import parse_bool
        from ciel_runtime_support.llm_option_config import set_work_mode_option
        from ciel_runtime_support.work_live_settings import settings
        for word, expected in self.WORDS.items():
            via_settings, via_options = {}, {}
            settings(via_settings, {"key": "work_auto_continue", "value": word}, lambda _: None)
            set_work_mode_option(via_options, "work_auto_continue", word, parse_bool, lambda _: None)
            self.assertEqual(via_options, via_settings, word)
            self.assertIs(expected, via_settings["work_auto_continue"])
        for surface in ("settings", "options"):
            config = {"work_mode": False}
            if surface == "settings":
                settings(config, {"key": "work_mode", "value": "default"}, lambda _: None)
            else:
                set_work_mode_option(config, "work_mode", "default", parse_bool, lambda _: None)
            self.assertNotIn("work_mode", config)

    def test_ctl_refuses_what_the_shared_validator_refuses(self):
        code, output, _ = self.cli(ns("set", "work_mode", "maybe"))
        self.assertEqual(1, code)
        self.assertEqual([], self.saved)

    def test_parallel_boolean_uses_the_same_words(self):
        from ciel_runtime_support.work_settings_menu import apply
        apply(self.config, "work_parallel_paused", "yes", self.save)
        self.assertIs(True, self.store.read()["policy"]["paused"])
        apply(self.config, "work_parallel_paused", "default", self.save)
        self.assertIs(False, self.store.read()["policy"]["paused"])


class Gap5InjectionMode(Isolated):
    # The web console's own REST path, exactly as the router mounts it. REST
    # settings-update is a compare-and-set: the client sends the
    # settings_fingerprint it read from GET /settings.
    def rest(self):
        from ciel_runtime_support.work_http_controller import WorkHttpController, WorkHttpServices
        reply = {}

        def write_json(_handler, payload, status=200):
            reply.clear()
            reply.update(status=status, payload=payload)
        controller = WorkHttpController(WorkHttpServices(
            self.root, lambda: self.config, lambda: A, lambda _identity: {}, write_json, "test",
            save_config=lambda config: (self.config.update(config), self.save(config))))

        def get_settings():
            path = "/ca/work/api/v1/settings"
            controller.get(type("Handler", (), {"headers": {}, "path": path + "?session_id=" + A})(), path)
            self.assertEqual(200, reply["status"], reply)
            return reply["payload"]["settings_fingerprint"]

        def update(body):
            path = "/ca/work/api/v1/commands/settings-update"
            controller.post(type("Handler", (), {"headers": {}, "path": path})(), path, {"session_id": A, **body})
            return dict(reply)
        return get_settings, update

    def test_rest_settings_update_accepts_injection_mode(self):
        get_settings, update = self.rest()
        seen = get_settings()
        reply = update({"key": "sse_injection_mode", "value": "btw_with_mcp", "expected_settings_fingerprint": seen})
        self.assertEqual(200, reply["status"], reply)
        self.assertEqual({"enabled": True, "type": "btw_with_mcp"}, self.saved[-1]["work_side_intake"])
        rows = {row["key"]: row for row in reply["payload"]["result"]["settings"]}
        self.assertEqual("btw_with_mcp", rows["sse_injection_mode"]["value"])

    def test_rest_settings_update_with_a_stale_fingerprint_is_a_conflict(self):
        get_settings, update = self.rest()
        seen = get_settings()
        self.config["work_side_intake"] = {"max_active": 5}     # another writer, after the read
        current = get_settings()
        saved = len(self.saved)
        reply = update({"key": "sse_injection_mode", "value": "btw_with_mcp", "expected_settings_fingerprint": seen})
        self.assertEqual((409, "settings_conflict"), (reply["status"], reply["payload"]["error"]), reply)
        self.assertEqual(current, reply["payload"]["settings_fingerprint"])
        self.assertEqual(saved, len(self.saved))                 # nothing written
        self.assertEqual({"max_active": 5}, self.config["work_side_intake"])

    def test_session_writes_the_same_bytes_as_console_off(self):
        # /btw_with_mcp off (work_mcp_tools work_side_settings) writes enabled=False, type=btw_with_mcp.
        from ciel_runtime_support.work_settings_menu import apply
        self.config = {"work_side_intake": {"enabled": True, "type": "btw_with_mcp", "max_active": 3}}
        apply(self.config, "sse_injection_mode", "session", self.save)
        self.assertEqual({"enabled": False, "type": "btw_with_mcp", "max_active": 3}, self.config["work_side_intake"])
        apply(self.config, "work_side_intake_enabled", "on", self.save)
        from ciel_runtime_support.work_setting_values import injection_mode
        self.assertEqual("btw_with_mcp", injection_mode(self.config))

    def test_menu_panel_lists_injection_mode_once(self):
        from ciel_runtime_support.work_settings_menu import panel_rows
        _, keys = panel_rows({})
        self.assertEqual(1, keys.count("sse_injection_mode"))


class Gap4PinnedOrphanRelease(Isolated):
    def lease(self, name, owner):
        version = self.store.read()["version"]
        d, ident = self.store.reserve([dict(item_id=name, priority="P1", created_at=1, approved=True, focused=True,
                                            dependencies_done=True, plan_version=1, usage=.05, role="worker",
                                            mode="fork")], scope=A, expected_version=version, owner=owner)
        self.store.bind(ident, "host-" + name, expected_version=d["version"])
        return ident

    def test_menu_has_no_bulk_release_and_reports_released_leases(self):
        from ciel_runtime_support.work_parallel_menu import apply
        dead = self.lease("dead", {"session_pid": 11, "identity": "birth-11"})
        with patch("ciel_runtime_support.work_parallel_mcp.process_identity", side_effect={11: ""}.get):
            before = self.store.read()
            try:
                apply({}, "work_parallel_orphans", "apply", lambda _: None)
            except ValueError:
                pass
            # A bulk release of every dead lease, with no pinned preview, is gone.
            self.assertEqual(before, self.store.read())
            from ciel_runtime_support.work_parallel_menu import orphan_choices
            preview, choices = orphan_choices()
            message = apply({}, "work_parallel_orphans", choices[0][1], lambda _: None)
        self.assertIn("  released " + dead + " (orphaned)", message)
        self.assertNotEqual(preview["digest"], orphan_choices()[0]["digest"])

    def test_ctl_release_goes_through_the_rest_path_and_needs_confirm(self):
        self.board(A)
        dead = self.lease("dead", {"session_pid": 11, "identity": "birth-11"})
        with patch("ciel_runtime_support.work_parallel_mcp.process_identity", side_effect={11: ""}.get):
            code, output, _ = self.cli(ns("orphan-release", dead[:10], session=A))
            self.assertEqual(3, code)
            self.assertTrue(self.store.read()["leases"][dead]["slot"])
            self.assertEqual("confirmation_required", json.loads(output[-1])["error"])
            code, output, _ = self.cli(ns("orphan-release", dead[:10], session=A, confirm=True,
                                          preview_digest="0" * 64))
            self.assertEqual(1, code)
            self.assertIn("stale", json.loads(output[-1])["message"])
            code, output, _ = self.cli(ns("orphan-release", dead[:10], session=A, confirm=True))
        self.assertEqual(0, code, output)
        self.assertEqual(dead, json.loads(output[0])["result"]["lease_id"])
        self.assertEqual("orphaned", self.store.read()["leases"][dead]["state"])

    def leaked_run(self):
        """A coordination run left reserved after its lease was released (Celly)."""
        from ciel_runtime_support import work_parallel_mcp as m
        ident = self.lease("leaked", None)
        coord = m.coordination(self.store)
        state = coord.read()
        state["runs"][ident] = {
            "scope": A, "lease_id": ident, "item_id": "leaked", "root_id": "leaked", "priority": "P1",
            "role": "worker", "generation": 1, "approval_generation": 1, "assigned_todos": ["leaked"],
            "fingerprint": "f" * 64, "workspace": None, "agent_id": None, "agent_name": None, "state": "reserved",
            "reserved_at": 1.0, "bound_at": None, "state_changed_at": 1.0, "native_status": None,
            "native_seen_at": None, "exit": None, "exit_attempts": 0,
            "recovery": {"state": "none", "attempts": 0, "error_code": None}, "replaced_by": None}
        state["version"] += 1
        Path(coord.path).write_text(json.dumps(state), encoding="utf-8")
        doc = self.store.read()
        del doc["leases"][ident]
        doc["version"] += 1
        self.store.path.write_text(json.dumps(doc), encoding="utf-8")
        return ident

    def test_menu_and_ctl_retire_never_launched_runs_of_the_shown_preview(self):
        from ciel_runtime_support import work_parallel_mcp as m
        from ciel_runtime_support.work_parallel_menu import apply, orphan_choices, rows
        self.board(A)
        self.leaked_run()
        shown = {key: value for _, key, value in rows({})}["work_parallel_orphans"]
        self.assertIn("never-launched runs=1", shown)
        _, choices = orphan_choices()
        label, value = choices[0]
        self.assertTrue(value.startswith("reconcile "), choices)
        message = apply({}, "work_parallel_orphans", value, lambda _: None)
        self.assertTrue(any("retired runs=1" in line for line in message), message)
        self.assertEqual({}, m.coordination(self.store).read()["runs"])
        # ctl: the same function through REST, gated by --confirm.
        self.leaked_run()
        code, output, _ = self.cli(ns("orphan-reconcile", session=A))
        self.assertEqual(3, code)
        self.assertEqual(1, len(m.coordination(self.store).read()["runs"]))
        code, output, _ = self.cli(ns("orphan-reconcile", session=A, confirm=True))
        self.assertEqual(0, code, output)
        self.assertEqual(1, json.loads(output[0])["result"]["cancelled_runs"])
        self.assertEqual({}, m.coordination(self.store).read()["runs"])


class Gap8NumericInput(Isolated):
    def test_numeric_settings_offer_other_and_accept_any_valid_integer(self):
        from ciel_runtime_support.work_settings_menu import apply, choices
        for key in ("work_parallel_p1_limit", "work_side_intake_aging_seconds", "work_parallel_max_rework"):
            self.assertIn("__edit__", choices({}, key)[1], key)
        apply(self.config, "work_parallel_p1_limit", "6", self.save)
        apply(self.config, "work_side_intake_aging_seconds", "17", self.save)
        self.assertEqual(6, self.store.read()["policy"]["priority_limits"]["P1"])
        self.assertEqual(17, self.config["work_side_intake"]["aging_seconds"])
        for key, value in (("work_parallel_p1_limit", "17"), ("work_parallel_max_rework", "11"),
                           ("work_side_intake_aging_seconds", "3601")):
            with self.assertRaises(ValueError):
                apply(self.config, key, value, self.save)

    def test_menu_ranges_match_the_store_validator(self):
        from ciel_runtime_support.work_settings_menu import NUMBER_RANGES
        for key in ("work_parallel_p1_limit", "work_parallel_launches_per_minute", "work_parallel_max_rework"):
            low, high = NUMBER_RANGES[key]
            field = key[len("work_parallel_"):]
            for number, accepted in ((low, True), (high, True), (low - 1, False), (high + 1, False)):
                changes = {"priority_limits": {"P1": number}} if field == "p1_limit" else {field: number}
                version = self.store.read()["version"]
                try:
                    self.store.configure(changes, capability_ready=True, expected_version=version)
                    ok = True
                except ValueError:
                    ok = False
                self.assertEqual(accepted, ok, (key, number))


class Gap7Usage(unittest.TestCase):
    def test_usage_and_reference_document_work(self):
        from ciel_runtime_support.cli_usage import cli_usage_text
        text = cli_usage_text()
        for fragment in ("ciel-runtimectl work status", "ciel-runtimectl work sessions", "--session",
                         "--confirm"):
            self.assertIn(fragment, text)
        reference = (Path(__file__).resolve().parents[1] / "docs" / "CLI-Reference.md").read_text(encoding="utf-8")
        self.assertIn("ciel-runtimectl work review-approve", reference)
        self.assertIn("SSE 사이드 포크", reference)


class Gap1ParserAndErrors(Isolated):
    def parser(self):
        from ciel_runtime_support import cli_parser
        noop = lambda _args: None  # noqa: E731
        services = cli_parser.CliParserServices(
            launch=cli_parser.CliParserLaunch(*(noop,) * 7), runtime=cli_parser.CliParserRuntime(*(noop,) * 5),
            settings=cli_parser.CliParserSettings(*(noop,) * 10),
            provider=cli_parser.CliParserProvider(*(noop,) * 9, {"copilot": noop, "zai": noop}),
            models=cli_parser.CliParserModels(noop, noop, noop), work=noop)
        return cli_parser.build_cli_parser(services)

    def test_parser_accepts_every_action_and_option(self):
        from ciel_runtime_support.work_ctl import ACTIONS
        parser = self.parser()
        args = parser.parse_args(["work", "review-approve", "lease-1", "--session", A, "--confirm", "--json",
                                  "--expected-finalization-version", "7", "--operation-id", "op-1"])
        self.assertEqual(("review-approve", "lease-1", A, True, True, 7, "op-1"),
                         (args.action, args.key, args.session, args.confirm, args.json,
                          args.expected_finalization_version, args.operation_id))
        args = parser.parse_args(["work", "handoff-ack", "ev", "answer text", "--session", A, "--clear-wait", "w1",
                                  "--clear-wait", "w2", "--activate"])
        self.assertEqual((["w1", "w2"], True, "answer text"), (args.clear_wait, args.activate, args.value))
        self.assertEqual("status", parser.parse_args(["work"]).action)
        for action in ("status", "list", "get", "set", *ACTIONS):
            self.assertEqual(action, parser.parse_args(["work", action]).action)

    def test_errors_are_one_line_and_never_tracebacks(self):
        code, output, errors = self.cli(ns("get", "nope", json=False))
        self.assertEqual(1, code)
        self.assertEqual(["work: unknown Work setting: nope"], errors.splitlines())
        self.assertNotIn("Traceback", errors)
        code, output, _ = self.cli(ns("reviews"))
        self.assertEqual(1, code)
        self.assertEqual("session_required", json.loads(output[-1])["error"])
        code, output, _ = self.cli(ns("reviews", session="claude:not-a-known-session"))
        self.assertEqual("unknown_session", json.loads(output[-1])["error"])


class Gap1Actions(Isolated):
    def test_sessions_list_identities_and_counts_only(self):
        self.board(A, lambda doc: doc.setdefault("side_reviews", {}).update(
            {RID: {"state": "pending", "kind": "info", "summary": "private text", "recorded_at": 1.0}}))
        self.board(B)
        instance = Path(runtime_paths.CONFIG_DIR) / "router-instances" / "1-x"
        instance.mkdir(parents=True, exist_ok=True)
        (instance / "compaction-scope.json").write_text(json.dumps({"runtime": "claude", "session_id": A[7:]}))
        self.addCleanup(lambda: (instance / "compaction-scope.json").unlink(missing_ok=True))
        code, output, _ = self.cli(ns("sessions"))
        self.assertEqual(0, code, output)
        rows = {row["digest"]: row for row in json.loads(output[0])["sessions"]}
        a = rows[hashlib.sha256(A.encode()).hexdigest()[:16]]
        self.assertEqual((A, 1, 1), (a["session"], a["nodes"], a["unfinished"]))
        b = rows[hashlib.sha256(B.encode()).hexdigest()[:16]]
        self.assertIsNone(b["session"])
        self.assertNotIn("private text", output[0])
        self.assertNotIn("fixture", output[0])

    def test_side_review_ok_runs_the_rest_action_for_the_named_session(self):
        store = self.board(A, lambda doc: doc.setdefault("side_reviews", {}).update(
            {RID: {"state": "pending", "kind": "info", "summary": "s", "recorded_at": 1.0}}))
        code, output, _ = self.cli(ns("side-review-ok", RID[:12], session=A))
        self.assertEqual(0, code, output)
        self.assertEqual("reviewed", store.read_snapshot()["side_reviews"][RID]["state"])

    def test_unlisted_exact_id_is_decided_by_the_rest_validator(self):
        # A decided review (or a replay of a committed --operation-id) is no
        # longer listed; ctl sends the exact id instead of refusing on its own.
        self.board(A)
        code, output, _ = self.cli(ns("review-approve", "f" * 32, session=A, confirm=True))
        self.assertEqual(1, code)
        body = json.loads(output[-1])
        self.assertEqual("invalid_request", body["error"])
        self.assertIn("unknown conversation lease", body["message"])
        code, output, _ = self.cli(ns("review-approve", "f" * 9, session=A, confirm=True))
        self.assertEqual("unknown_target", json.loads(output[-1])["error"])

    def test_duplicate_risk_actions_need_confirm_and_change_nothing(self):
        # A released-wait notice whose outcome is unknown, built by the real side stores.
        from ciel_runtime_support import side_notice
        from ciel_runtime_support.side_intake_inbox import SideIntakeInbox
        from ciel_runtime_support.side_intake_processing import claim, finish
        self.board(A)
        inbox = SideIntakeInbox(self.root, A)
        receipt = inbox.receive("event that released a wait", "parity-notice")["receipt_id"]
        leased = claim(self.root, A, "actor")
        finish(self.root, A, receipt, leased["generation"], {"reply": "", "delivery": "not_applicable",
                                                              "released_waits": [{"id": "n1", "waiting_on": "x"}]})
        granted = side_notice.prepare(inbox, receipt, "owner", "notice text")
        side_notice.record(inbox, receipt, "uncertain", granted.get("attempt_id"), owner_nonce="owner")
        before = inbox._load(receipt)
        code, output, _ = self.cli(ns("notice-retry", receipt[:12], session=A))
        self.assertEqual(3, code)
        body = json.loads(output[-1])
        self.assertEqual("confirmation_required", body["error"])
        self.assertIn("may already have", body["message"])
        pin = side_notice.retry_pin(before["release_notice"])
        self.assertEqual({"receipt_id": receipt, "attempt_ref": pin["notice_attempt_ref"],
                          "revision": pin["notice_revision"], "confirmed": True}, body["would"])
        self.assertEqual(before, inbox._load(receipt))
        stale = ns("notice-retry", receipt[:12], session=A, confirm=True, attempt_ref="0" * 16)
        code, output, _ = self.cli(stale)
        self.assertEqual(1, code)  # a stale pin is refused by the REST validator; nothing changes
        self.assertEqual(before, inbox._load(receipt))
        code, output, _ = self.cli(ns("notice-retry", receipt[:12], session=A, confirm=True))
        self.assertEqual(0, code, output)
        self.assertEqual("released", json.loads(output[0])["result"]["state"])


class Condition3AuditAndIdentity(Isolated):
    def files(self, root):
        return sorted(str(path.relative_to(root)) for path in Path(root).rglob("*"))

    def test_session_of_another_workspace_is_refused_before_any_write(self):
        from ciel_runtime_support.work_ctl import CtlError, WorkCtl
        self.board(A)  # A's board lives in this workspace's state dir (self.root)
        with tempfile.TemporaryDirectory() as other:
            before = self.files(other)
            ctl = WorkCtl(lambda: self.config, self.save, Path(other))
            with self.assertRaises(CtlError) as refused:
                ctl.require_session(A)
            self.assertEqual("unknown_session", refused.exception.code)
            self.assertEqual(before, self.files(other))

    def test_ctl_receipt_names_ctl_user_and_pid_and_keeps_no_text(self):
        import os
        secret_text = "PRIVATE-REVIEW-REPLY-TEXT"
        store = self.board(A, lambda doc: doc.setdefault("side_reviews", {}).update(
            {RID: {"state": "pending", "kind": "info", "summary": "s", "reply": secret_text, "recorded_at": 1.0}}))
        code, output, _ = self.cli(ns("side-review-ok", RID, session=A))
        self.assertEqual(0, code, output)
        receipts = store.read_snapshot()["rest_operations"]
        self.assertEqual(1, len(receipts))
        receipt = next(iter(receipts.values()))
        self.assertEqual("ctl", receipt["source"])
        self.assertEqual(os.getpid(), receipt["origin"]["pid"])
        self.assertTrue(receipt["origin"]["os_user"])
        self.assertNotIn(secret_text, json.dumps(receipts))
        code, output, _ = self.cli(ns("audit", session=A))
        event = json.loads(output[0])["events"][0]
        self.assertEqual(("ctl", os.getpid()), (event["source"], event["origin"]["pid"]))


class OperationResolution(Isolated):
    """The web audit tab's Reconcile and Release unverified, through ciel-runtimectl."""

    KEY = "u-settings-000000001"

    def setUp(self):
        super().setUp()
        self.config = {"work_mode": True}
        self.board_store = self.board(A)

        def prepared(doc):  # an operator command whose outcome is unknown
            doc.setdefault("rest_operations", {})[self.KEY] = {
                "digest": "0" * 64, "command": "settings_update", "source": "rest", "origin": {},
                "state": "prepared", "created_at": 1.0, "request": {"key": "work_mode"},
                "response": {"ok": False, "state": "prepared"}}
        self.board_store.transaction(prepared, audit_only=True)

    def receipt(self):
        return self.board_store.read_snapshot()["rest_operations"][self.KEY]

    def test_reconcile_and_release_need_confirm_and_use_the_audit_row(self):
        code, output, _ = self.cli(ns("operation-reconcile", self.KEY[:10], session=A))
        refused = json.loads(output[-1])
        self.assertEqual((3, "confirmation_required"), (code, refused["error"]))
        self.assertIn("Reconcile “settings_update” (u-settings-0)? This checks the actual store", refused["message"])
        self.assertNotIn("last_reconcile", self.receipt())       # nothing ran without --confirm
        code, output, _ = self.cli(ns("operation-reconcile", self.KEY[:10], session=A, confirm=True))
        self.assertEqual(0, code, output)
        result = json.loads(output[-1])["result"]
        self.assertEqual(("unknown", "no_stored_proof", 2),
                         (result["outcome"], result["observed"], result["receipt_version"]))
        code, output, _ = self.cli(ns("operation-release", self.KEY, session=A))
        refused = json.loads(output[-1])
        self.assertEqual((3, 2), (code, refused["would"]["expected_receipt_version"]))
        self.assertIn("the effect may already have happened; retrying may duplicate it", refused["message"])
        code, output, _ = self.cli(ns("operation-release", self.KEY, session=A, confirm=True, expected_version=1))
        self.assertEqual((1, "receipt_changed"), (code, json.loads(output[-1])["error"]))
        code, output, _ = self.cli(ns("operation-release", self.KEY, session=A, confirm=True))
        self.assertEqual(0, code, output)
        self.assertEqual(("released_unverified", 3), (json.loads(output[-1])["result"]["outcome"],
                                                      json.loads(output[-1])["result"]["receipt_version"]))
        self.assertEqual(("released_unverified", "operation_release", "ctl"),
                         (self.receipt()["state"], self.receipt()["resolution"]["command"],
                          self.receipt()["resolution"]["source"]))
        code, output, _ = self.cli(ns("audit", session=A))
        row = next(event for event in json.loads(output[0])["events"] if event["operation_id"] == self.KEY)
        self.assertEqual("released_unverified", row["outcome"])

    def test_release_before_a_reconcile_is_refused(self):
        code, output, _ = self.cli(ns("operation-release", self.KEY, session=A, confirm=True))
        self.assertEqual((1, "reconcile_required"), (code, json.loads(output[-1])["error"]))
        self.assertEqual("prepared", self.receipt()["state"])

    def test_unknown_operation_is_one_line(self):
        code, output, errors = self.cli(ns("operation-reconcile", "x" * 20, session=A, confirm=True, json=False))
        self.assertEqual(1, code)
        self.assertEqual(1, len(errors.splitlines()), errors)
        self.assertNotIn("Traceback", errors)


class QueuedReworkDisplay(unittest.TestCase):
    def test_owner_offline_rework_reads_as_queued_not_failed(self):
        from ciel_runtime_support.work_ctl import text_lines
        from ciel_runtime_support.work_parallel_mcp import OWNER_OFFLINE_TEXT
        payload = {"ok": True, "operation_id": "ctl-1", "result": {
            "decision": "rework", "lease_id": "a" * 32, "item_id": "n3", "state": "awaiting_capacity",
            "finalization_version": 9, "retry": "host_pump", "reason": OWNER_OFFLINE_TEXT}}
        lines = text_lines("review-rework", payload)
        self.assertEqual("queued: rework queued: waiting for the conversation to resume "
                         "(retried by the conversation's own Work host)", lines[0])
        self.assertFalse(any("error" in line.lower() or "fail" in line.lower() for line in lines), lines)


class ReviewPin(unittest.TestCase):
    """R1: ctl sends the review_pin of the row it rendered (lead ruling)."""

    PIN = "rp1:" + "a" * 64

    def sent(self, row, **extra):
        from ciel_runtime_support.work_ctl import WorkCtl
        ctl = WorkCtl(lambda: {}, lambda _config: None, Path(tempfile.gettempdir()))
        view = {"review_version": 7, "parallel_reviews": [{"lease_id": "b" * 32, "item_title": "Item", **row}]}
        posts = []
        with patch.object(WorkCtl, "get", return_value=view), \
                patch.object(WorkCtl, "post", side_effect=lambda _s, command, body, _op: posts.append((command, body))):
            ctl.act("review-rework", A, ns("review-rework", "b" * 12, "add a test", **extra))
        return posts[0]

    def test_the_rendered_rows_pin_is_sent_with_the_version(self):
        command, body = self.sent({"review_pin": self.PIN})
        self.assertEqual("parallel-item-review", command)
        self.assertEqual((self.PIN, 7), (body["expected_review_pin"], body["expected_finalization_version"]))

    def test_an_earlier_reads_pin_overrides_and_no_pin_keeps_the_version_rule(self):
        other = "rp1:" + "c" * 64
        self.assertEqual(other, self.sent({"review_pin": self.PIN}, expected_review_pin=other)[1]["expected_review_pin"])
        self.assertNotIn("expected_review_pin", self.sent({})[1])

    def test_parser_accepts_the_pin_option(self):
        from ciel_runtime_support import cli_parser
        noop = lambda _args: None  # noqa: E731
        parser = cli_parser.build_cli_parser(cli_parser.CliParserServices(
            launch=cli_parser.CliParserLaunch(*(noop,) * 7), runtime=cli_parser.CliParserRuntime(*(noop,) * 5),
            settings=cli_parser.CliParserSettings(*(noop,) * 10),
            provider=cli_parser.CliParserProvider(*(noop,) * 9, {"copilot": noop, "zai": noop}),
            models=cli_parser.CliParserModels(noop, noop, noop), work=noop))
        args = parser.parse_args(["work", "review-approve", "x", "--session", A, "--expected-review-pin", self.PIN])
        self.assertEqual(self.PIN, args.expected_review_pin)


class ForkGateDisplay(Isolated):
    """G5: ctl shows the Work fork gate state from backend's presence record."""

    NOT_LOADED = "Work fork gate not loaded: parallel execution disabled"
    NOT_REPORTED = "Work fork gate presence not reported by this conversation's Work host"

    def setUp(self):
        super().setUp()
        self.config = {"work_mode": True}
        self.board(A)

    def record(self, state, version="1.0.0"):
        from ciel_runtime_support.work_parallel_gate_presence import WorkParallelGatePresence
        WorkParallelGatePresence.beside(self.store.path).record(A, state, gate_version=None if state == "missing"
                                                                  else version, probe_ref="0" * 32)

    def status(self, *, text=False):
        code, output, _ = self.cli(ns("status", session=A, json=not text))
        self.assertEqual(0, code, output)
        return output if text else json.loads(output[-1])["operational"]

    def test_not_loaded_is_shown_verbatim_once(self):
        self.record("missing")
        operational = self.status()
        self.assertEqual(("not_loaded", "missing", self.NOT_LOADED),
                         tuple(operational["session"]["fork_gate"][key] for key in ("state", "reason", "message")))
        self.assertEqual([self.NOT_LOADED], operational["warnings"])
        lines = self.status(text=True)
        # One warning line; the operational JSON line carries the server object as is.
        self.assertEqual(["warning: " + self.NOT_LOADED], [line for line in lines if line.startswith("warning:")])
        code, output, _ = self.cli(ns("capacity", session=A))
        capacity = json.loads(output[-1])
        self.assertEqual((0, "not_loaded", [self.NOT_LOADED]),
                         (code, capacity["fork_gate"]["state"], capacity["warnings"]))
        code, output, _ = self.cli(ns("capacity", session=A, json=False))
        self.assertEqual(["warning: " + self.NOT_LOADED], [line for line in output if line.startswith("warning:")])
        self.assertEqual("warning: " + self.NOT_LOADED, output[0])

    def test_capacity_shows_the_per_priority_diagnosis(self):
        # Stage 3, 2026-11-17: per priority limit, use, candidates and blockers in ctl too.
        code, output, _ = self.cli(ns("capacity", session=A))
        self.assertEqual(0, code, output)
        diagnosis = json.loads(output[-1])["priority_diagnosis"]
        self.assertEqual(["P0", "P1", "P2", "P3", "P4", "P5"], [row["priority"] for row in diagnosis["rows"]])
        self.assertTrue(all({"limit", "active", "free", "candidates", "blocked", "state", "reason"} <= set(row)
                            for row in diagnosis["rows"]))
        self.assertIsInstance(diagnosis["summary"], str)

    def test_unknown_has_its_own_text_and_loaded_has_none(self):
        operational = self.status()
        self.assertEqual(("unknown", "not_reported", [self.NOT_REPORTED]),
                         (operational["session"]["fork_gate"]["state"], operational["session"]["fork_gate"]["reason"],
                          operational["warnings"]))
        self.record("loaded")
        operational = self.status()
        self.assertEqual(("loaded", None, []), (operational["session"]["fork_gate"]["state"],
                                                operational["session"]["fork_gate"]["message"], operational["warnings"]))
        self.assertFalse(any(line.startswith("warning:") for line in self.status(text=True)))


class ReceiptsFullFromCtl(Isolated):
    """R3: with 128 outcome-unknown operator intents, ctl shows the server's 409 text verbatim,
    `work audit` lists every prepared intent, and reconcile/release still run."""

    def setUp(self):
        super().setUp()
        from ciel_runtime_support.work_application import WorkApplication
        self.config = {"work_mode": True}
        self.plan = self.board(A)
        app = WorkApplication(self.plan)
        for index in range(128):
            app.operator_prepare("settings_update", {"key": "work_mode", "value": "on", "n": index},
                                 f"full-{index:03d}-operation")

    def test_new_command_gets_the_server_text_and_audit_still_resolves(self):
        from ciel_runtime_support.work_application import RECEIPTS_FULL
        store = self.plan
        RID2 = "d" * 64
        store.transaction(lambda doc: doc.setdefault("side_reviews", {}).update(
            {RID2: {"state": "pending", "kind": "info", "summary": "s", "recorded_at": 1.0}}),
            expected_version=store.read_snapshot()["version"])
        code, output, _ = self.cli(ns("side-review-ok", RID2, session=A))
        body = json.loads(output[-1])
        self.assertEqual((1, "operator_receipts_full", RECEIPTS_FULL), (code, body["error"], body["message"]))
        code, output, errors = self.cli(ns("side-review-ok", RID2, session=A, json=False))
        self.assertEqual(["work: " + RECEIPTS_FULL], errors.splitlines())
        code, output, _ = self.cli(ns("audit", session=A))
        events = json.loads(output[0])["events"]
        self.assertEqual(128, sum(1 for row in events if row["outcome"] == "unknown"))
        # Resolutions are not receipts: they still run when every receipt is prepared.
        code, output, _ = self.cli(ns("operation-reconcile", "full-000-operation", session=A, confirm=True))
        self.assertEqual(0, code, output)
        self.assertEqual("unknown", json.loads(output[-1])["result"]["outcome"])
        code, output, _ = self.cli(ns("operation-release", "full-000-operation", session=A, confirm=True))
        self.assertEqual((0, "released_unverified"), (code, json.loads(output[-1])["result"]["outcome"]))
        # One slot is free again: the same ctl command now runs.
        code, output, _ = self.cli(ns("side-review-ok", RID2, session=A))
        self.assertEqual(0, code, output)


class FailureMessages(Isolated):
    """B2: `work reviews` prints a failure row's server message verbatim once, under its JSON row."""

    UNKNOWN = "Owner of this launch attempt is unknown; resolve it from the Work pane."
    LIVE = "Another live Work host owns this launch attempt; waiting for that host."

    def setUp(self):
        super().setUp()
        from ciel_runtime_support.work_parallel_failures import WorkParallelFailureLog
        self.config = {"work_mode": True}
        self.board(A)
        log = WorkParallelFailureLog.beside(self.store.path)
        for index, code in enumerate(("owner_unknown", "owner_live", "revival_refused")):
            log.record(A, code, lease_id="lease-" + code, dedup_key="fixture-" + code, now=1.8e9 + index)

    def test_text_prints_each_server_message_once_and_keeps_the_rows(self):
        code, output, _ = self.cli(ns("reviews", session=A, json=False))
        self.assertEqual(0, code, output)
        for message in (self.UNKNOWN, self.LIVE):
            self.assertEqual(1, output.count("    " + message), output)
        rows = [line for line in output if line.startswith("  {")]
        self.assertEqual(["owner_unknown", "owner_live", "revival_refused"][::-1],
                         [json.loads(line)["code"] for line in rows])
        # A code without a server message gets no extra line, and ctl adds no wording of its own.
        self.assertEqual(2, sum(1 for line in output if line.startswith("    ")))

    def test_json_mode_is_the_view_unchanged(self):
        code, output, _ = self.cli(ns("reviews", session=A))
        self.assertEqual(0, code, output)
        body = json.loads(output[-1])
        self.assertEqual({self.UNKNOWN, self.LIVE},
                         {row["message"] for row in body["parallel_failures"] if "message" in row})
        # The digest is compared across the trees before and after this change.
        print("JSON-SHA256", hashlib.sha256(output[-1].encode("utf-8")).hexdigest())


class WorkExecute(Isolated):
    """The web console's Execute (board command `execute`), from ciel-runtimectl.

    Under approval=ask a Codex conversation has no Work dialog, so a person
    approves plan execution from an operator surface.
    """

    def setUp(self):
        super().setUp()
        from ciel_runtime_support.work_plan_store import session_work_plan_store
        self.config = {"work_mode": True, "work_plan_approval_ask": True}
        self.plan = session_work_plan_store(self.root, A)
        doc = self.plan.read_snapshot()
        self.plan.transaction(lambda d: tree.set_plan(d, {
            "title": "Ship the release", "note": "", "priority": "P1", "children": [
                {"title": "Phase", "children": [{"title": "Task", "children": []}]}]}, None),
            expected_version=doc["version"])
        self.root_id = self.plan.read_snapshot()["active_root"]

    def test_execute_shows_the_plan_and_needs_confirm(self):
        code, output, _ = self.cli(ns("execute", session=A))
        refused = json.loads(output[-1])
        self.assertEqual((3, "confirmation_required"), (code, refused["error"]))
        for shown in ("Start execution for the active plan?", self.root_id, "Ship the release", "phase planning",
                      "approval ask"):
            self.assertIn(shown, refused["message"])
        self.assertEqual((self.root_id, "Ship the release", "planning"),
                         (refused["would"]["root_id"], refused["would"]["title"], refused["would"]["phase"]))
        self.assertEqual("planning", self.plan.read_snapshot()["phase"])     # nothing ran

    def test_execute_with_confirm_starts_the_plan_through_the_web_path(self):
        code, output, _ = self.cli(ns("execute", session=A, confirm=True, operation_id="ctl-execute-1"))
        self.assertEqual(0, code, output)
        reply = json.loads(output[-1])
        self.assertEqual(("execute", "started", "executing", self.root_id),
                         (reply["operation"], reply["result"]["outcome"], reply["result"]["phase"],
                          reply["result"]["root_id"]))
        self.assertEqual("executing", self.plan.read_snapshot()["phase"])
        # A stale pin is refused like the console's version conflict.
        code, output, _ = self.cli(ns("execute", session=A, confirm=True, expected_version=0))
        self.assertEqual((1, "version_conflict"), (code, json.loads(output[-1])["error"]))

    def test_execute_target_approves_one_queued_goal_and_keeps_the_active_one(self):
        doc = self.plan.read_snapshot()
        self.plan.transaction(lambda d: tree.set_plan(d, {
            "title": "Queued work", "note": "", "priority": "P1", "children": [
                {"title": "Phase", "children": [{"title": "Task", "children": []}]}]}, None),
            expected_version=doc["version"])
        queued = next(n["id"] for n in self.plan.read_snapshot()["nodes"]
                      if n["parent_id"] is None and n["id"] != self.root_id)
        code, output, _ = self.cli(ns("execute", queued, session=A))
        refused = json.loads(output[-1])
        self.assertEqual((3, "confirmation_required"), (code, refused["error"]))
        for shown in ("queued goal " + queued, "Queued work", "phase planning", "The active goal stays active"):
            self.assertIn(shown, refused["message"])
        self.assertEqual((queued, "planning"), (refused["would"]["root_id"], refused["would"]["phase"]))
        code, output, _ = self.cli(ns("execute", queued, session=A, confirm=True, operation_id="ctl-execute-3"))
        self.assertEqual(0, code, output)
        reply = json.loads(output[-1])
        self.assertEqual(("started", "executing", queued),
                         (reply["result"]["outcome"], reply["result"]["phase"], reply["result"]["root_id"]))
        after = self.plan.read_snapshot()
        self.assertEqual((self.root_id, "planning", "executing"),
                         (after["active_root"], tree.root_phase(after, self.root_id), tree.root_phase(after, queued)))

    def test_execute_is_audited_as_ctl(self):
        import os
        code, output, _ = self.cli(ns("execute", session=A, confirm=True, operation_id="ctl-execute-2"))
        self.assertEqual(0, code, output)
        code, output, _ = self.cli(ns("audit", session=A))
        row = next(event for event in json.loads(output[0])["events"] if event["operation_id"] == "ctl-execute-2")
        self.assertEqual(("execute", "ctl", os.getpid()),
                         (row["command"], row["source"], (row.get("origin") or {}).get("pid")))


if __name__ == "__main__":
    unittest.main()
