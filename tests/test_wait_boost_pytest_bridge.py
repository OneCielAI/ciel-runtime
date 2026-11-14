"""Run the pytest-style Gate-5 wait-boost suites from the unittest release runner."""
from __future__ import annotations

import os
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SUITES = (
    "test_assignment_key_coordinator.py",
    "test_l3_cross_feature_race.py",
    "test_l3_p0_creator.py",
    "test_l3_wait_creator.py",
    "test_wait_boost_sidecar.py",
    "test_wait_cleanup_finalizer.py",
    "test_wait_cleanup_service.py",
    "test_wait_effect_service.py",
    "test_wait_orchestrator.py",
    "test_wait_parent_completion.py",
    "test_wait_phase_service.py",
    "test_wait_reacquire_history.py",
    "test_wait_reacquire_service.py",
    "test_wait_reserve_service.py",
    "test_work_wait_host_api.py",
    "test_work_wait_product_adapter.py",
    "test_work_wait_surfaces.py",
)


class WaitBoostPytestBridge(unittest.TestCase):
    def test_wait_boost_suites_pass(self):
        try:
            import pytest  # noqa: F401
        except ImportError:
            self.skipTest("pytest is not installed")
        env = dict(os.environ)
        env["PYTHONPATH"] = os.pathsep.join(
            [str(ROOT), str(ROOT / "tests"), env.get("PYTHONPATH", "")]
        )
        paths = [str(ROOT / "tests" / name) for name in SUITES]
        result = subprocess.run(
            [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", *paths],
            cwd=ROOT, env=env, capture_output=True, text=True, timeout=600,
        )
        self.assertEqual(result.returncode, 0, result.stdout[-4000:] + result.stderr[-2000:])


if __name__ == "__main__":
    unittest.main()
