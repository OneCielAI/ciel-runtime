import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from ciel_runtime_support.workspace_state import migrate_workspace_state


class WorkspaceStateTests(unittest.TestCase):
    def test_router_port_changes_do_not_change_workspace_durable_state_path(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workspace = root / "project"
            workspace.mkdir()
            script = (
                "import json; from ciel_runtime_support.runtime_paths import "
                "ROUTER_INSTANCE_DIR, WORKSPACE_STATE_DIR; "
                "print(json.dumps([str(ROUTER_INSTANCE_DIR), str(WORKSPACE_STATE_DIR)]))"
            )

            def projected(port):
                env = dict(os.environ)
                for name in (
                    "CIEL_RUNTIME_STATE_DIR",
                    "CIEL_RUNTIME_TEST_ISOLATED",
                    "CIEL_RUNTIME_WORKSPACE_STATE_DIR",
                ):
                    env.pop(name, None)
                env.update(
                    {
                        "CIEL_RUNTIME_CONFIG_DIR": str(root / "config"),
                        "CIEL_RUNTIME_LAUNCH_CWD": str(workspace),
                        "CIEL_RUNTIME_ROUTER_PORT": str(port),
                    }
                )
                repository = str(Path(__file__).resolve().parents[1])
                env["PYTHONPATH"] = repository + os.pathsep + env.get("PYTHONPATH", "")
                result = subprocess.run(
                    [sys.executable, "-c", script],
                    cwd=workspace,
                    env=env,
                    check=True,
                    capture_output=True,
                    text=True,
                )
                return json.loads(result.stdout)

            first_instance, first_state = projected(18803)
            second_instance, second_state = projected(18804)

            self.assertNotEqual(first_instance, second_instance)
            self.assertEqual(first_state, second_state)

    def test_newest_legacy_instance_is_migrated_without_removing_source(self):
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory)
            old = config / "router-instances" / "8803-abc123"
            new = config / "router-instances" / "8804-abc123"
            old.mkdir(parents=True)
            new.mkdir(parents=True)
            (old / "runtime-inputs.jsonl").write_text("old\n", encoding="utf-8")
            (new / "runtime-inputs.jsonl").write_text("new\n", encoding="utf-8")
            (new / "channel-llm-cursor.json").write_text('{"last_id":1}', encoding="utf-8")
            target = config / "workspaces" / "abc123"

            migrated = migrate_workspace_state(config, "abc123", target)

            self.assertEqual("8804-abc123", migrated)
            self.assertEqual("new\n", (target / "runtime-inputs.jsonl").read_text(encoding="utf-8"))
            self.assertTrue((new / "runtime-inputs.jsonl").exists())

    def test_existing_workspace_state_is_never_overwritten_during_first_migration(self):
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory)
            legacy = config / "router-instances" / "8803-abc123"
            target = config / "workspaces" / "abc123"
            legacy.mkdir(parents=True)
            target.mkdir(parents=True)
            (legacy / "runtime-inputs.jsonl").write_text("legacy\n", encoding="utf-8")
            (target / "runtime-inputs.jsonl").write_text("current\n", encoding="utf-8")

            self.assertEqual("8803-abc123", migrate_workspace_state(config, "abc123", target))
            self.assertEqual("current\n", (target / "runtime-inputs.jsonl").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
