#!/usr/bin/env python3
"""Run deterministic, non-overlapping unittest groups for parallel CI."""

from __future__ import annotations

import argparse
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TEST_DIR = ROOT / "tests"
GROUPS = ("unit", "router", "channel", "runtime")
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def group_for(filename: str) -> str:
    stem = filename.removeprefix("test_").removesuffix(".py")
    if "channel" in stem or stem in {"cron_tools"}:
        return "channel"
    if stem.startswith(("codex", "agy", "advisor")) or stem in {
        "headless_update_checks",
        "install_diagnostics",
        "menu_key_debug",
        "review_command_passthrough",
        "statusline",
        "version_sync",
        "web_chat_ui",
    }:
        return "runtime"
    if any(
        marker in stem
        for marker in (
            "provider",
            "router",
            "upstream",
            "api_key",
            "rate_limit",
            "anthropic",
            "claude_native",
            "deepseek",
            "fireworks",
            "kimi",
            "lm_studio",
            "ollama",
            "opencode",
            "openrouter",
            "vllm",
            "zai",
        )
    ):
        return "router"
    return "unit"


def files_for(group: str) -> list[Path]:
    return [path for path in sorted(TEST_DIR.glob("test_*.py")) if group_for(path.name) == group]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("group", choices=GROUPS)
    parser.add_argument("--list", action="store_true")
    args = parser.parse_args()
    files = files_for(args.group)
    if args.list:
        print("\n".join(path.name for path in files))
        return 0
    suite = unittest.TestSuite()
    loader = unittest.defaultTestLoader
    for path in files:
        suite.addTests(loader.discover(str(TEST_DIR), pattern=path.name))
    print(f"Running {args.group} test group ({len(files)} files)", flush=True)
    return 0 if unittest.TextTestRunner(verbosity=1).run(suite).wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main())
