"""Harness-only launcher: run ciel_runtime.py unchanged, optionally widening one Windows probe timeout.

Why this exists (measured, see windows-refused-connect-latency.txt): on this Windows host a refused
TCP connect to a closed loopback port takes ~2.05 s, but the uncommitted
codex_desktop_runtime.listener_probe() gives up after 0.5 s, gets TimeoutError (an OSError), returns
"unknown", and listener_is_bound() treats unknown as bound.  Every Codex remote launch on a free port is
then refused with codex_remote_listener_already_bound.  Without --listener-probe-timeout this shim is a
pure pass-through, so the unpatched behaviour can be reproduced on demand.

Usage: launch_shim.py [--listener-probe-timeout SECONDS] -- <ciel_runtime.py arguments>

Copied unchanged on 2026-10-26 from docs/journal/2026/10/20/diagnostics/restart-delivery/evidence/.
The Walkie id-reuse E2E always sets OWNED_RESTART_RUNTIME_ROOT, so the default below is unused.
"""
from __future__ import annotations

import os
import runpy
import sys
from pathlib import Path

REPO = Path(os.environ.get("OWNED_RESTART_RUNTIME_ROOT") or Path(__file__).resolve().parents[8]).resolve()
args = sys.argv[1:]
timeout = None
if args and args[0] == "--listener-probe-timeout":
    timeout = float(args[1])
    args = args[2:]
if args and args[0] == "--":
    args = args[1:]

sys.path.insert(0, str(REPO))
if timeout is not None:
    from ciel_runtime_support import codex_desktop_runtime

    # listener_is_bound passes its own timeout default down to listener_probe, so patch that one.
    codex_desktop_runtime.listener_is_bound.__kwdefaults__["timeout"] = timeout
    codex_desktop_runtime.listener_probe.__kwdefaults__["timeout"] = timeout
    print(f"launch_shim: listener probe timeout widened to {timeout}s (harness only)", flush=True)

sys.argv = [str(REPO / "ciel_runtime.py"), *args]
runpy.run_path(str(REPO / "ciel_runtime.py"), run_name="__main__")
