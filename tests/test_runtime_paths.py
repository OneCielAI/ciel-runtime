import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from ciel_runtime_support import runtime_paths


class RuntimePathsTest(unittest.TestCase):
    def test_configuration_and_router_state_are_workspace_isolated(self):
        self.assertEqual(runtime_paths.ROUTER_INSTANCE_DIR, runtime_paths.ROUTER_ACTIVITY_PATH.parent)
        # The input queue reserves the ids its status history recorded beside it.
        self.assertEqual(runtime_paths.RUNTIME_INPUTS_PATH.parent, runtime_paths.RUNTIME_INPUT_STATUS_PATH.parent)
        if runtime_paths.os.environ.get("CIEL_RUNTIME_TEST_ISOLATED"):
            self.assertEqual(runtime_paths.CONFIG_DIR, runtime_paths.ROUTER_INSTANCE_DIR)
            self.assertEqual(runtime_paths.CONFIG_DIR, runtime_paths.CONFIG_PATH.parent)
        else:
            self.assertIn(runtime_paths.CONFIG_DIR / "router-instances", runtime_paths.ROUTER_INSTANCE_DIR.parents)
            self.assertIn(runtime_paths.CONFIG_DIR / "workspaces", runtime_paths.WORKSPACE_STATE_DIR.parents)
            self.assertEqual(runtime_paths.WORKSPACE_STATE_DIR, runtime_paths.RUNTIME_INPUTS_PATH.parent)
            self.assertEqual(runtime_paths.WORKSPACE_STATE_DIR, runtime_paths.CHANNEL_LLM_CURSOR_PATH.parent)
            self.assertEqual(runtime_paths.WORKSPACE_STATE_DIR, runtime_paths.CONFIG_PATH.parent)
            self.assertEqual(runtime_paths.CONFIG_DIR, runtime_paths.LEGACY_CONFIG_PATH.parent)

    PROBE = (
        "import json, os, sys\n"
        "sys.path.insert(0, {repo!r})\n"
        "from ciel_runtime_support import runtime_paths as p\n"
        "print(json.dumps({{'config': str(p.CONFIG_DIR), 'legacy': str(p.LEGACY_CONFIG_PATH),\n"
        "    'isolated': p._TEST_STATE_ISOLATED, 'child_config': os.environ.get('CIEL_RUNTIME_CONFIG_DIR'),\n"
        "    'child_flag': os.environ.get('CIEL_RUNTIME_TEST_ISOLATED')}}))\n"
    )

    def probe(self, name, env_updates, drop=()):
        repo = str(Path(__file__).resolve().parents[1])
        with tempfile.TemporaryDirectory() as scripts:
            script = Path(scripts) / name
            script.write_text(self.PROBE.format(repo=repo), encoding="utf-8")
            env = {key: value for key, value in os.environ.items() if not key.startswith("CIEL_RUNTIME_")}
            env.update(env_updates)
            for key in drop:
                env.pop(key, None)
            completed = subprocess.run(
                [sys.executable, str(script)], check=True, capture_output=True,
                text=True, encoding="utf-8", env=env, cwd=scripts,
            )
        return json.loads(completed.stdout.strip().splitlines()[-1])

    def live_profile(self):
        # A directory outside the temp root stands for an inherited live profile.
        return str(Path.home() / ("ciel-runtime-probe-live-" + str(os.getpid())))

    def test_isolated_flag_without_config_override_uses_process_temp_profile(self):
        with tempfile.TemporaryDirectory() as appdata:
            result = self.probe("probe.py", {"APPDATA": appdata, "CIEL_RUNTIME_TEST_ISOLATED": "1"})

        self.assertTrue(result["isolated"])
        self.assertNotEqual(str(Path(appdata) / "ciel-runtime"), result["config"])
        self.assertTrue(Path(result["config"]).name.startswith("ciel-runtime-test-"))
        self.assertEqual(str(Path(result["config"]) / "config.json"), result["legacy"])
        self.assertEqual(result["config"], result["child_config"])

    def test_isolated_flag_rejects_inherited_live_profile(self):
        live = self.live_profile()
        result = self.probe("probe.py", {"CIEL_RUNTIME_TEST_ISOLATED": "1", "CIEL_RUNTIME_CONFIG_DIR": live})

        self.assertNotEqual(live, result["config"])
        self.assertTrue(Path(result["config"]).name.startswith("ciel-runtime-test-"))
        self.assertFalse(Path(live).exists())

    def test_loose_test_entrypoint_isolates_even_without_the_flag(self):
        # The 2026-11-09 incident command: no flag, live CONFIG_DIR inherited.
        live = self.live_profile()
        result = self.probe("test_probe_entry.py", {"CIEL_RUNTIME_CONFIG_DIR": live})

        self.assertTrue(result["isolated"])
        self.assertNotEqual(live, result["config"])
        self.assertEqual("1", result["child_flag"])
        self.assertFalse(Path(live).exists())

    def test_production_entrypoint_keeps_its_configured_profile(self):
        with tempfile.TemporaryDirectory() as root:
            configured = str(Path(root) / "profile")
            result = self.probe("ciel_runtime_probe.py", {"CIEL_RUNTIME_CONFIG_DIR": configured})

        self.assertFalse(result["isolated"])
        self.assertEqual(configured, result["config"])

    def test_default_router_port_honors_valid_environment_override(self):
        with mock.patch.dict(runtime_paths.os.environ, {"CIEL_RUNTIME_ROUTER_PORT": "9876"}):
            self.assertEqual(9876, runtime_paths.default_router_port())

    def test_path_prefix_preserves_existing_path(self):
        projected = runtime_paths.path_with_ciel_runtime_user_dirs({"PATH": "existing-bin"})
        self.assertTrue(projected.endswith(runtime_paths.os.pathsep + "existing-bin"))


if __name__ == "__main__":
    unittest.main()
