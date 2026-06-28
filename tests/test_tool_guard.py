import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
GUARD = ROOT / "ciel-runtime-tool-guard.py"


class ToolGuardTests(unittest.TestCase):
    def run_guard(self, event: dict, env_extra: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
        with tempfile.TemporaryDirectory() as tmp:
            env = os.environ.copy()
            # Drop Ciel Runtime vars that may be exported on a dev host (where
            # ciel-runtime itself runs) so the guard's behavior is decided only by
            # what each test injects via env_extra -- otherwise a leaked
            # CIEL_RUNTIME_BYPASS_PERMISSIONS=1 would make "without bypass" tests
            # see bypass and fail non-deterministically by host.
            for leaked in ("CIEL_RUNTIME_PROVIDER", "CIEL_RUNTIME_BYPASS_PERMISSIONS", "CIEL_RUNTIME_MODEL_ALIAS"):
                env.pop(leaked, None)
            env["HOME"] = tmp
            env["USERPROFILE"] = tmp
            if env_extra:
                env.update(env_extra)
            return subprocess.run(
                [sys.executable, str(GUARD)],
                input=json.dumps(event),
                text=True,
                capture_output=True,
                env=env,
                check=True,
            )

    def test_guard_is_silent_for_native_worktree_hook(self):
        proc = self.run_guard({"hook_event_name": "WorktreeCreate", "cwd": "/tmp/project"})

        self.assertEqual("", proc.stdout.strip())
        self.assertEqual("", proc.stderr.strip())

    def test_guard_handles_worktree_hook_for_ciel_runtime_provider(self):
        proc = self.run_guard(
            {"hook_event_name": "WorktreeCreate", "cwd": "/tmp/project"},
            {"CIEL_RUNTIME_PROVIDER": "ollama-cloud"},
        )

        payload = json.loads(proc.stdout)
        self.assertEqual("/tmp/project", payload["hookSpecificOutput"]["worktreePath"])

    def test_exit_plan_permission_is_auto_allowed_when_bypass_permissions_is_active(self):
        proc = self.run_guard(
            {
                "hook_event_name": "PermissionRequest",
                "tool_name": "ExitPlanMode",
                "tool_input": {"plan": "Implement the requested fix."},
            },
            {
                "CIEL_RUNTIME_PROVIDER": "ollama-cloud",
                "CIEL_RUNTIME_BYPASS_PERMISSIONS": "1",
            },
        )

        payload = json.loads(proc.stdout)
        output = payload["hookSpecificOutput"]
        self.assertEqual("PermissionRequest", output["hookEventName"])
        self.assertEqual("allow", output["decision"]["behavior"])
        self.assertEqual({"plan": "Implement the requested fix."}, output["decision"]["updatedInput"])

    def test_exit_plan_permission_accepts_camel_case_hook_fields(self):
        proc = self.run_guard(
            {
                "hook_event_name": "PermissionRequest",
                "toolName": "ExitPlanMode",
                "toolInput": {"plan": "Leave plan mode."},
            },
            {
                "CIEL_RUNTIME_PROVIDER": "deepseek",
                "CIEL_RUNTIME_BYPASS_PERMISSIONS": "true",
            },
        )

        payload = json.loads(proc.stdout)
        self.assertEqual("allow", payload["hookSpecificOutput"]["decision"]["behavior"])
        self.assertEqual({"plan": "Leave plan mode."}, payload["hookSpecificOutput"]["decision"]["updatedInput"])

    def test_exit_plan_permission_is_observed_without_bypass_permissions(self):
        proc = self.run_guard(
            {
                "hook_event_name": "PermissionRequest",
                "tool_name": "ExitPlanMode",
                "tool_input": {"plan": "Implement the requested fix."},
            },
            {"CIEL_RUNTIME_PROVIDER": "ollama-cloud"},
        )

        self.assertEqual("", proc.stdout.strip())
        self.assertEqual("", proc.stderr.strip())

    def test_non_plan_permission_is_not_auto_allowed_by_bypass_permissions(self):
        proc = self.run_guard(
            {
                "hook_event_name": "PermissionRequest",
                "tool_name": "Bash",
                "tool_input": {"command": "echo hello"},
            },
            {
                "CIEL_RUNTIME_PROVIDER": "ollama-cloud",
                "CIEL_RUNTIME_BYPASS_PERMISSIONS": "1",
            },
        )

        self.assertEqual("", proc.stdout.strip())
        self.assertEqual("", proc.stderr.strip())

    def test_exit_plan_pretooluse_is_auto_allowed_under_bypass(self):
        # PermissionRequest does not fire in headless -p mode, so the guard must
        # also auto-allow ExitPlanMode on PreToolUse (which fires in every mode).
        proc = self.run_guard(
            {
                "hook_event_name": "PreToolUse",
                "tool_name": "ExitPlanMode",
                "tool_input": {"plan": "Ship it."},
            },
            {
                "CIEL_RUNTIME_PROVIDER": "ollama-cloud",
                "CIEL_RUNTIME_BYPASS_PERMISSIONS": "1",
            },
        )
        payload = json.loads(proc.stdout)
        output = payload["hookSpecificOutput"]
        self.assertEqual("PreToolUse", output["hookEventName"])
        self.assertEqual("allow", output["permissionDecision"])
        self.assertEqual({"plan": "Ship it."}, output["updatedInput"])

    def test_exit_plan_pretooluse_auto_allow_ignores_missing_transcript(self):
        # A truncated/missing transcript must not be able to deny the exit a
        # bypass session needs. With no transcript_path the old stale-detection
        # would skip; the bypass auto-allow must still fire before that check.
        proc = self.run_guard(
            {
                "hook_event_name": "PreToolUse",
                "tool_name": "ExitPlanMode",
                "tool_input": {"plan": "Continue."},
            },
            {
                "CIEL_RUNTIME_PROVIDER": "deepseek",
                "CIEL_RUNTIME_BYPASS_PERMISSIONS": "1",
            },
        )
        payload = json.loads(proc.stdout)
        self.assertEqual("allow", payload["hookSpecificOutput"]["permissionDecision"])

    def test_anthropic_routed_bypass_auto_allows_exit_plan(self):
        # GAP 1: anthropic-routed bypass sessions run with provider="anthropic",
        # which is NOT in NON_NATIVE_PROVIDERS, yet still launch bypassPermissions.
        # The guard must stay active for them so plan approval is auto-resolved.
        proc = self.run_guard(
            {
                "hook_event_name": "PreToolUse",
                "tool_name": "ExitPlanMode",
                "tool_input": {"plan": "Proceed."},
            },
            {
                "CIEL_RUNTIME_PROVIDER": "anthropic",
                "CIEL_RUNTIME_BYPASS_PERMISSIONS": "1",
            },
        )
        payload = json.loads(proc.stdout)
        self.assertEqual("allow", payload["hookSpecificOutput"]["permissionDecision"])

    def test_anthropic_routed_bypass_permission_request_auto_allows_exit_plan(self):
        # Interactive anthropic-routed bypass: the PermissionRequest path must
        # also work now that the guard activates for provider="anthropic"+bypass.
        proc = self.run_guard(
            {
                "hook_event_name": "PermissionRequest",
                "tool_name": "ExitPlanMode",
                "tool_input": {"plan": "Go."},
            },
            {
                "CIEL_RUNTIME_PROVIDER": "anthropic",
                "CIEL_RUNTIME_BYPASS_PERMISSIONS": "1",
            },
        )
        payload = json.loads(proc.stdout)
        self.assertEqual("allow", payload["hookSpecificOutput"]["decision"]["behavior"])

    def test_anthropic_routed_bypass_does_not_touch_other_tools(self):
        # When active only via bypass (native provider), the guard must NOT
        # normalize or deny non-plan tools -- a native Anthropic model emits
        # correct schemas and rewriting its input would be a regression.
        proc = self.run_guard(
            {
                "hook_event_name": "PreToolUse",
                "tool_name": "TaskUpdate",
                "tool_input": {"task_id": "1", "status": "done"},
            },
            {
                "CIEL_RUNTIME_PROVIDER": "anthropic",
                "CIEL_RUNTIME_BYPASS_PERMISSIONS": "1",
            },
        )
        self.assertEqual("", proc.stdout.strip())
        self.assertEqual("", proc.stderr.strip())

    def test_native_session_without_bypass_stays_silent(self):
        # No bypass, native provider not in NON_NATIVE_PROVIDERS -> fully silent.
        proc = self.run_guard(
            {
                "hook_event_name": "PreToolUse",
                "tool_name": "ExitPlanMode",
                "tool_input": {"plan": "x"},
            },
            {"CIEL_RUNTIME_PROVIDER": "anthropic"},
        )
        self.assertEqual("", proc.stdout.strip())
        self.assertEqual("", proc.stderr.strip())

    def test_enter_plan_pretooluse_denies_external_channel_prompt(self):
        with tempfile.TemporaryDirectory() as tmp:
            transcript = Path(tmp) / "session.jsonl"
            transcript.write_text(
                json.dumps(
                    {
                        "type": "user",
                        "message": {
                            "role": "user",
                            "content": [
                                {
                                    "type": "text",
                                    "text": (
                                        "[ciel-runtime external channel message] "
                                        "channel=ai-net-http room=room1 from=agent id=42 text=\"hello\"."
                                    ),
                                }
                            ],
                        },
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            proc = self.run_guard(
                {
                    "hook_event_name": "PreToolUse",
                    "tool_name": "EnterPlanMode",
                    "tool_input": {},
                    "transcript_path": str(transcript),
                },
                {"CIEL_RUNTIME_PROVIDER": "ollama-cloud"},
            )

        payload = json.loads(proc.stdout)
        output = payload["hookSpecificOutput"]
        self.assertEqual("deny", output["permissionDecision"])
        self.assertIn("External channel messages", output["permissionDecisionReason"])

    def test_enter_plan_pretooluse_denies_external_channel_input_prompt(self):
        with tempfile.TemporaryDirectory() as tmp:
            transcript = Path(tmp) / "session.jsonl"
            transcript.write_text(
                json.dumps(
                    {
                        "type": "user",
                        "message": {
                            "role": "user",
                            "content": "[external channel input]\n<< ai-net-http >>\nid=42 channel=ai-net-http\nhello",
                        },
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            proc = self.run_guard(
                {
                    "hook_event_name": "PreToolUse",
                    "tool_name": "EnterPlanMode",
                    "tool_input": {},
                    "transcript_path": str(transcript),
                },
                {"CIEL_RUNTIME_PROVIDER": "deepseek"},
            )

        payload = json.loads(proc.stdout)
        output = payload["hookSpecificOutput"]
        self.assertEqual("deny", output["permissionDecision"])
        self.assertIn("External channel messages", output["permissionDecisionReason"])


if __name__ == "__main__":
    unittest.main()
