---
type: Verification
title: SSE native fork verification
description: Focused and grouped evidence for native fork inheritance, prompt policy, Walkie prefetch, and fail-closed handling.
timestamp: 2026-11-11T00:00:00Z
tags: [verification, sse, native-fork, walkie]
---

# Focused tests

Command environment:

- every inherited `CIEL_RUNTIME_*` variable unset;
- temporary `CIEL_RUNTIME_CONFIG_DIR` and `CIEL_RUNTIME_HOME`;
- repository root and `tests/` added to `PYTHONPATH`.

Latest isolated side/settings result, with `tests/test_work_mod_write_gate.py` included: `343 passed, 197 subtests passed in 97.82s`. Files: `tests/test_side_*.py`, `test_work_side_intake.py`, `test_work_http_side_receipts.py`, `test_work_mcp_tools.py`, `test_work_live_settings.py`, `test_work_settings_cli.py`, `test_work_settings_menu.py`, `test_launch_data_path.py`, `test_work_mod_write_gate.py`.

Covered:

- one native `fork` per event with no model/name override and no registered side type;
- no projected parent-context call or payload;
- no side-specific technical tool fence;
- every spawned event prompt carries all prohibited operations and decision-only output contract;
- canonical Walkie root plus paged `threads.read` (>200 replies fixture), exact text, target, null lineage, conflicting identities, exact 18-digit IDs above 2^53, and BigInt cursor/order;
- aggregate-only thread sizing: 531 threads, row p99/max 40/113, byte p99/max 111,564/341,233; selected bounds 8 pages and 384 KiB;
- truncated message-less event marker reaches both receipt and fork prompt without assuming missing content;
- quarantines are terminal across 8 categories × 3 fingerprints × typed/untyped (48 combinations);
- no spawn/no reply for read failure, missing target, malformed identity/data, lineage mismatch, duplicate target, foreign conversation, or oversize source;
- retired model/context settings omitted from UI/runtime and ignored when still stored;
- environment force flag no longer changes for a retired side-model value;
- P1 reserved slot, main wakes, wait release, host commit, and outbox recovery remain covered.

# Group tests

The groups ran under `python scripts/run_test_group.py unit|router`, with every inherited `CIEL_RUNTIME_*`, `CLAUDE*`, `ANTHROPIC*` and `MAX_MCP*` variable unset. The runner creates a temporary `CIEL_RUNTIME_CONFIG_DIR` and a free router port, and sets `CIEL_RUNTIME_TEST_ISOLATED=1`.

Other agents were editing the shared worktree during these runs. For the last run, a per-file manifest (SHA-256 and mtime of 1048 files) was taken at start, at unit end and at router end. All times are CST on 2026-11-11.

| Run | Window | Result |
|---|---|---|
| unit | 03:40–03:56 | 3705 tests, 1 error (`test_work_mod_write_gate` assertion on a fork name; that test was rewritten for the unnamed fork at 03:55, during the run) |
| router | 03:56–04:07 | 1530 tests, OK |
| unit | 04:08–04:21 | 3715 tests, OK (53 skipped); `register.js` 57801380… |
| unit | 04:54–05:05 | 3740 tests, 4 failures (53 skipped) |
| router | 05:05–05:17 | 1530 tests, OK |

In the last run, all 55 SSE-side files had identical hashes in all three manifests. That includes `side-intake.js` `aa5f7659…`, the side Python modules, `claude_router.py`, `claude_environment.py`, and every side test, harness and fixture.

Some files did change mid-run:

- during unit: `work_parallel_finalization.py`, `work_parallel_mcp.py` and `tests/test_work_parallel_finalization.py`;
- during router: `register.js` (72fd6693… → 7582542c…).

The 4 unit failures are all in `test_work_mod_real_backend`. They assert Work rework launch state, which comes from a backend subprocess. That subprocess imports `work_parallel_mcp` and `work_parallel_finalization` again for each scenario. No assertion involves side state. Whether the mid-run backend edits caused the failures was not determined. The final four-group run on a global freeze is coordinated by the team lead.

# Live runtime safety

Before grouped tests, live router `http://127.0.0.1:9524/health` returned HTTP JSON with `ok: true` and PID `82844`. After every group run, at 06:14 CST, it again returned `ok: true` with the same PID `82844`.
