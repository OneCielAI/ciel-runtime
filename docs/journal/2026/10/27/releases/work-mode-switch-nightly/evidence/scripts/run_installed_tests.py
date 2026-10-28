"""Run worktree test modules against an installed snapshot and print where each product module loaded from.

Usage: python -I run_installed_tests.py <snapshot_dir> <tests_dir> <module> [<module> ...]
The snapshot is first on sys.path, so ciel_runtime_support resolves there; the run fails if any
listed product module loads from anywhere else.
"""
import importlib
import sys
import unittest
from pathlib import Path

snapshot, tests = Path(sys.argv[1]).resolve(), Path(sys.argv[2]).resolve()
sys.path[:0] = [str(snapshot), str(tests)]
names = sys.argv[3:]
suite = unittest.TestSuite()
for name in names:
    print("MODULE_SOURCE", name)
    suite.addTests(unittest.defaultTestLoader.loadTestsFromModule(importlib.import_module(name)))
foreign = []
for product in ("ciel_runtime_support.work_mcp_tools", "ciel_runtime_support.work_mode_launch",
                "ciel_runtime_support.channel_mcp_tools"):
    path = Path(importlib.import_module(product).__file__).resolve()
    print("LOADED", path)
    if snapshot not in path.parents:
        foreign.append(str(path))
result = unittest.TextTestRunner(verbosity=2).run(suite)
if foreign:
    print("FOREIGN MODULES", foreign)
raise SystemExit(0 if result.wasSuccessful() and not foreign else 1)
