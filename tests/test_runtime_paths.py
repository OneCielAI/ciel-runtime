import unittest
from unittest import mock

from ciel_runtime_support import runtime_paths


class RuntimePathsTest(unittest.TestCase):
    def test_config_artifacts_share_config_root(self):
        self.assertEqual(runtime_paths.CONFIG_DIR, runtime_paths.CONFIG_PATH.parent)
        self.assertEqual(runtime_paths.CONFIG_DIR, runtime_paths.ROUTER_ACTIVITY_PATH.parent)
        self.assertEqual(runtime_paths.CONFIG_DIR, runtime_paths.CHANNEL_MCP_CONFIG.parent)

    def test_default_router_port_honors_valid_environment_override(self):
        with mock.patch.dict(runtime_paths.os.environ, {"CIEL_RUNTIME_ROUTER_PORT": "9876"}):
            self.assertEqual(9876, runtime_paths.default_router_port())

    def test_path_prefix_preserves_existing_path(self):
        projected = runtime_paths.path_with_ciel_runtime_user_dirs({"PATH": "existing-bin"})
        self.assertTrue(projected.endswith(runtime_paths.os.pathsep + "existing-bin"))


if __name__ == "__main__":
    unittest.main()
