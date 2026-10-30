---
type: Verification
title: Source SSE gate verification
description: Isolated focused regressions and owned real native CLI slash screenshots, with limitations retained.
tags: [verification, sse, compact, cli]
timestamp: 2026-10-29
---
# Confirmed

- Focused source regressions: **117 passed**. [Exact runner](evidence/scripts/focused.py), [result](evidence/focused-tests.json), [text](evidence/focused-tests.txt). Tests cover gate modes, latest-user command routing, local response without API/upstream calls, real compact pressure/request preservation, safety holds, config/status-write failure, menu setter/catalog, fresh/stale actual statusline subprocess output and existing compaction/Work regressions.
- Ruff passes for touched policy/binding/menu/shortcut/test modules; root file remains 4,980 lines.
- Owned actual Claude CLI [strict source run](evidence/source-cli/result.json): latest local response after off→status→on, persisted config and visible statusline mode verified; no additional upstream generation after one baseline request, live PIDs unchanged, foreground hits0/804samples. [Off](evidence/source-cli/gate-off.png), [on](evidence/source-cli/gate-on.png). Raw harness runs remain local; selected successful evidence is committed.

# Failed harness attempts retained

- `run-source-gate-203112`: slash unknown because test-isolated CLI assets were installed in cfg/.claude while harness forced home/.claude. Fixed harness to use cfg/.claude; no production asset-install change needed.
- `run-source-gate-final-203634`: stricter harness expected effective `open` forever, but actual statusline correctly displayed `unknown` after evidence aged 15s. Updated harness to assert persisted mode and newest local reply, without treating stale evidence as open.

# Boundary

This is source verification, not npm/install proof. Parent release task owns commit, CI/npm identity, installed snapshot validation and local switch. No live process restart or managed-host write was performed.

# Clarified command semantics

The local CLI proof harness was corrected to match the rendered command prompt rather than count viewport rows; the first run's failure was harness-only after the `on` response rendered, not a product failure. Focused source run118/118pass; exact result retained under the SAMA diagnostic evidence.
