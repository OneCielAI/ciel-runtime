---
type: feature
title: Work and SSE Observatory
description: "The existing /work-mode view inspector is replaced by a surface-aware Work/SSE/settings pane with a live side-model picker."
tags: [claude-code-mod, work-mode, sse, mobile, settings]
timestamp: 2026-11-07
---

# User surface

`/work-mode view` opens one **Work & SSE** pane:

- **Work (`w`)**: hierarchical detail, priority/status colors, focus actions, queue/completed tabs.
- **SSE (`s`)**: receipt state, source P1 filter, decisions, delivery, failures and linked Work.
- **Settings (`g`)**: every `work_live_settings` row, typed as boolean, enum, number, text, JSON or model.

`/work-mode settings` opens the same pane directly on Settings.

# Side model

The side-model rows use the current provider's cached model registry. The pane pages the full list and supports a custom id. A launch that started with a selected side model applies another selection to the next SSE. A launch that started with `inherit` retains the established `CLAUDE_CODE_SUBAGENT_MODEL_FORCE` safety policy: the selection is saved and the pane states that a relaunch is required.

# Surface adaptation

The render reads `e.surface` and `e.props.bodyColumns`.

- terminal/desktop/vscode when wide: denser pane with colored hierarchy/state rows;
- `mobile` or fewer than 58 body columns: single-column, touch-first Button pages; no `Input`, `Select`, `Client`, `Raster` or hover dependency.

Engine-backed plugin tests mount both the real `terminal` and `mobile` element tables. Both pass, so no unavailable element or invalid prop is present.

# Evidence

- 383 focused Python tests passed, 1 skipped, 259 subtests passed.
- New deterministic Observatory harness: 6 tests passed.
- Engine-backed plugin tests: the two new terminal/mobile tests pass. The pre-existing `work.test.ts` pass/fail set is byte-for-byte identical to the HEAD baseline (18 pass / 77 fail); those old failures are the existing fixture issue where `setup()` does not implement `clock.after`, not introduced by this feature.
- `claude plugin validate`: passed; existing missing-icon warning only.
- Local install after the settings-tab ordering fix: `0.2.51-local.20261107-cf76b0e` at `C:\Users\djlov\.local\share\ciel-runtime-cf76b0e7f85785e71f9ff4ce105805b5526d8de7`.
- Real fresh console: `/work-mode view` opened and focused the installed Work & SSE pane, showing its colored header, Work/SSE/Settings tabs, parallel capacity and mobile/narrow hint ([screenshot](evidence/real-console-work-pane.png), [screen text](evidence/real-console-work-pane.txt)).
