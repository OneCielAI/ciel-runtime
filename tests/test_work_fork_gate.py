"""Unit harness for the ciel-work-gate plugin (blocker 3).

The gate enforces Work rules inside forks ciel-work started with $.agent.spawn,
because ciel-work's own hooks never run there (measured on claude.exe 2.1.323).
These tests drive the packaged hooks module with a fake host; the real-console
acceptance (gate E1) is a separate owned run.
"""
import json
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GATE_DIR = ROOT / "ciel_runtime_support" / "claude_mods" / "ciel-work-gate"
HARNESS = Path(__file__).resolve().parent / "work_fork_gate_harness.mjs"
ALIAS_MAIN = "ciel-runtime-p-main"


@unittest.skipUnless(shutil.which("node"), "node required")
class WorkForkGateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # The hooks module is an ES module named .js; node loads it as one under .mjs.
        with tempfile.TemporaryDirectory() as directory:
            module = Path(directory) / "register.mjs"
            shutil.copyfile(GATE_DIR / "hooks" / "register.js", module)
            result = subprocess.run(["node", str(HARNESS), str(module)], capture_output=True, text=True,
                                    encoding="utf-8", timeout=120, cwd=str(ROOT))
        if result.returncode != 0:
            raise AssertionError("harness failed: " + result.stderr[-2000:])
        cls.out = json.loads(result.stdout)

    def scenario(self, name):
        value = self.out[name]
        self.assertNotIn("error", value, value.get("error"))
        return value

    def test_fork_model_follows_lease_priority_from_the_first_request(self):
        value = self.scenario("fork_model_by_host_id")
        self.assertEqual("model-p2", value["first"])
        self.assertEqual("model-p2", value["second"])
        # Cached per agent: one status read for two steps.
        self.assertEqual(1, value["statusReads"])

    def test_fork_model_before_bind_uses_dispatch_name_and_effective_p0(self):
        self.assertEqual("model-p0", self.scenario("fork_model_pre_bind_by_dispatch_name")["first"])

    def test_fork_model_without_lease_is_retried_next_step(self):
        value = self.scenario("fork_model_unknown_lease_not_cached")
        self.assertEqual(ALIAS_MAIN, value["first"])
        self.assertEqual("model-p2", value["second"])

    def test_rework_fork_before_bind_uses_its_replacement_lease_priority(self):
        self.assertEqual("model-p2", self.scenario("fork_model_rework_name_before_bind")["first"])

    def test_side_fork_uses_side_model(self):
        self.assertEqual("model-side", self.scenario("fork_model_side")["first"])

    def test_inherit_main_and_user_agents_keep_their_model(self):
        value = self.scenario("fork_model_inherit_and_others")
        self.assertEqual(ALIAS_MAIN, value["inherit"])
        self.assertEqual(ALIAS_MAIN, value["main"])
        self.assertEqual(ALIAS_MAIN, value["user"])
        self.assertEqual(ALIAS_MAIN, value["absent"])

    def test_direct_gateway_requests_get_the_upstream_id(self):
        self.assertEqual("up-p2", self.scenario("fork_model_direct_path_uses_upstream_id")["first"])

    def test_router_failure_keeps_engine_model_and_logs_no_text(self):
        value = self.scenario("fork_model_router_failure_keeps_model")
        self.assertEqual(ALIAS_MAIN, value["first"])
        self.assertTrue(value["logged"])
        self.assertFalse(value["leaked"])

    def test_main_model_applies_from_a_turns_first_step_and_holds_for_the_turn(self):
        value = self.scenario("main_model_turn_pinned")
        self.assertEqual(("ciel-runtime-p-chosen", "ciel-runtime-p-chosen"), (value["a0"], value["a1"]))
        # The next turn reads again; with no main model configured it keeps the engine's.
        self.assertEqual("ciel-runtime-p-main", value["b0"])
        self.assertEqual("up-chosen", value["direct"])
        self.assertEqual(3, value["statusReads"])

    def test_main_model_never_switches_mid_turn_and_failure_keeps_the_engine_model(self):
        value = self.scenario("main_model_midturn_and_failure")
        main = "ciel-runtime-p-main"
        self.assertEqual((main, main, 0), (value["mid"], value["mid2"], value["midReads"]))
        self.assertEqual((main, main, 1), (value["failed"], value["failedNext"], value["failedReads"]))
        self.assertTrue(value["logged"])
        self.assertFalse(value["leaked"])
        self.assertEqual("ciel-runtime-p-main", value["forkStep"])

    def test_package_has_exactly_one_hooks_module(self):
        manifest = json.loads((GATE_DIR / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))
        self.assertEqual("ciel-work-gate", manifest["name"])
        hooks = json.loads((GATE_DIR / "hooks" / "hooks.json").read_text(encoding="utf-8"))
        self.assertEqual(["./register.js"], hooks["modules"])

    def test_gate_version_literal(self):
        source = (GATE_DIR / "hooks" / "register.js").read_text(encoding="utf-8")
        self.assertEqual(["1"], re.findall(r"^const GATE_VERSION = '([^']+)'$", source, re.M))

    def test_only_one_place_reads_a_router_credential(self):
        source = (GATE_DIR / "hooks" / "register.js").read_text(encoding="utf-8")
        self.assertEqual(1, source.count("$.env.get("))
        self.assertEqual(1, source.count("CIEL_RUNTIME_WORK_PARALLEL_HOST_TOKEN"))
        self.assertNotIn("$.env.set(", source)
        self.assertNotIn("$.fs.", source)

    def test_agents_outside_scope_are_untouched(self):
        main = self.scenario("main_write_passes")
        self.assertTrue(main["nextCalled"])
        self.assertIsNone(main["deny"])
        self.assertEqual(0, main["routerCalls"])
        user = self.scenario("user_subagent_passes")
        self.assertTrue(user["callNext"])
        self.assertTrue(user["checkNext"])
        self.assertEqual(0, user["routerCalls"])
        model_fork = self.scenario("model_dispatched_fork_passes")
        self.assertTrue(model_fork["nextCalled"])
        self.assertEqual(0, model_fork["routerCalls"])

    def test_exact_side_actor_shape_is_untouched(self):
        side = self.scenario("side_actor_passes")
        self.assertTrue(side["bashNext"])
        self.assertTrue(side["resolveNext"])
        self.assertEqual(0, side["routerCalls"])
        self.assertTrue(self.scenario("side_actor_by_row_after_reload")["bashNext"])
        named = self.scenario("named_side_shape_is_work_fork")
        self.assertFalse(named["nextCalled"])
        self.assertIn("unrecognized ciel-work fork", named["deny"])

    def test_prebind_mutation_is_denied_after_one_reread(self):
        value = self.scenario("prebind_write_and_bash_denied_after_one_reread")
        self.assertFalse(value["writeNext"])
        self.assertFalse(value["bashNext"])
        self.assertIn("lease binding yet", value["writeDeny"])
        self.assertIn("retry", value["writeDeny"])
        self.assertTrue(value["writeDeny"].startswith("Ciel Work fork gate: "))
        self.assertEqual(2, value["routerAfterWrite"])
        self.assertIn("lease binding yet", value["bashDeny"])
        self.assertTrue(value["hostArgs"])
        self.assertEqual(2, value["decisionLogs"])
        self.assertFalse(value["logLeaksToken"])

    def test_bind_seen_on_the_reread_allows(self):
        value = self.scenario("bind_seen_on_reread_allows")
        self.assertIsNone(value["deny"])
        self.assertTrue(value["nextCalled"])
        self.assertEqual(["work_parallel_status", "work_parallel_status"], value["routerCalls"])

    def test_reads_and_worker_report_pass_and_host_field_is_dropped(self):
        value = self.scenario("reads_and_worker_tools")
        self.assertTrue(value["readNext"])
        self.assertEqual(0, value["routerAfterRead"])
        self.assertTrue(value["reportNext"])
        self.assertTrue(value["reportHostDropped"])
        self.assertTrue(value["reportKept"])

    def test_board_router_and_orchestration_tools_are_denied(self):
        value = self.scenario("denied_router_and_orchestration_tools")
        for tool, row in value["rows"].items():
            self.assertTrue(row["denied"], tool)
            self.assertFalse(row["nextCalled"], tool)
        self.assertEqual(0, value["routerCalls"])

    def test_bound_file_writes_are_confined_to_the_lease_worktree(self):
        value = self.scenario("bound_writes_confined")
        for key in ("inside", "insideBackslash", "notebook", "patchInside", "bash", "otherMcp"):
            self.assertEqual("allowed", value[key], key)
        for key in ("outside", "sibling", "escape", "patchOutside"):
            self.assertIn("outside lease lease-1", value[key], key)
        self.assertIn("absolute path", value["relative"])
        self.assertIn("names no file path", value["patchNoPath"])

    def test_worktree_projection_cases(self):
        value = self.scenario("worktree_projection_cases")
        self.assertEqual("allowed", value["unconfined"])
        self.assertIn("does not name lease lease-1's worktree", value["unprojected"])
        self.assertIn("not absolute", value["invalid"])
        self.assertIn("started outside lease lease-1", self.scenario("spawn_cwd_cross_check")["deny"])

    def test_board_phase_gates_file_writes_only(self):
        value = self.scenario("board_phase")
        self.assertIn("Work is planning", value["planningWrite"])
        self.assertEqual("allowed", value["planningBash"])
        # P0 is dispatched without approval; its host-marked lease may write in planning.
        self.assertEqual("allowed", value["p0PlanningWrite"])
        self.assertEqual("allowed", value["disabledWrite"])
        self.assertIn("not valid", value["invalidWrite"])

    def test_lease_that_moved_on_is_not_bound(self):
        value = self.scenario("lease_moved_on")
        self.assertIn("lease lease-1 is submitted", value["deny"])
        self.assertEqual(2, value["routerCalls"])

    def test_unreadable_state_fails_closed_for_work_forks_only(self):
        value = self.scenario("state_unreadable")
        self.assertIn("Work state unreadable", value["bashDeny"])
        self.assertFalse(value["leaksRouterText"])
        self.assertTrue(value["readNext"])
        self.assertIn("credential is not provisioned", value["noTokenDeny"])
        self.assertEqual(0, value["noTokenRouterCalls"])
        unreadable = self.scenario("classification_unreadable_leaves_alone")
        self.assertTrue(unreadable["nextCalled"])
        self.assertTrue(unreadable["logged"])

    def test_every_name_ciel_work_gives_is_recognized(self):
        value = self.scenario("recognized_names")
        self.assertEqual(4, len(value))
        for name, deny in value.items():
            self.assertIn("this Work fork's lease binding", deny, name)
            self.assertNotIn("unrecognized", deny, name)

    def test_unrecognized_ciel_work_fork_is_a_work_fork(self):
        value = self.scenario("unrecognized_name")
        self.assertIn("unrecognized ciel-work fork (mystery-fork)", value["deny"])
        self.assertIn("unrecognized ciel-work fork", value["unnamedDeny"])

    def test_agent_spawn_from_a_work_fork_is_denied(self):
        value = self.scenario("agent_spawn_rules")
        self.assertIn("cannot start agents", value["deny"])
        self.assertFalse(value["forkNext"])
        self.assertEqual("k2", value["mainAgent"])
        self.assertTrue(value["mainNext"])
        self.assertTrue(value["engineSpawnNotRecorded"])

    def test_presence_probe_answers_only_ciel_work_queries(self):
        value = self.scenario("presence_probe")
        self.assertEqual({"decision": "ask", "reason": "ciel-work-gate 1 " + "ab" * 16}, value["ask"])
        self.assertFalse(value["askNext"])
        self.assertTrue(value["engineNext"])
        self.assertTrue(value["realCallNext"])
        self.assertTrue(value["badNonceNext"])
        self.assertEqual(0, value["routerCalls"])

    def test_tool_check_reuses_the_tool_call_decision(self):
        value = self.scenario("check_reuses_call_decision")
        self.assertEqual(value["afterCall"], value["afterCheck"])
        self.assertTrue(value["checkNext"])
        self.assertEqual("deny", value["directDecision"]["decision"])
        self.assertIn("lease binding yet", value["directDecision"]["reason"])

    def test_paged_state_is_verified(self):
        value = self.scenario("paged_state")
        self.assertIsNone(value["deny"])
        self.assertTrue(value["nextCalled"])
        self.assertEqual(4, value["pageCalls"])
        self.assertIn("did not match their digest", value["tamperedDeny"])

    def test_catch_handlers_deny_only_known_work_forks(self):
        value = self.scenario("catch_handlers")
        self.assertIn("gate check failed", value["forFork"])
        self.assertTrue(value["forOtherNext"])
        self.assertEqual("deny", value["checkFork"]["decision"])
        self.assertTrue(value["checkMainNext"])
        self.assertTrue(value["spawnCatch"])


if __name__ == "__main__":
    unittest.main()
