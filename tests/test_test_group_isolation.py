import os
import unittest
from unittest import mock

from scripts.run_test_group import isolate_runtime_state


class TestGroupIsolationTests(unittest.TestCase):
    def test_isolation_uses_temporary_state_and_non_live_port(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            state = isolate_runtime_state()
            try:
                self.assertEqual(state.name, os.environ["CIEL_RUNTIME_CONFIG_DIR"])
                self.assertNotEqual("9464", os.environ["CIEL_RUNTIME_ROUTER_PORT"])
                self.assertEqual("1", os.environ["CIEL_RUNTIME_TEST_ISOLATED"])
            finally:
                state.cleanup()


if __name__ == "__main__":
    unittest.main()
