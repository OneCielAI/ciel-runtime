---
type: implementation
title: Graphical Work and SSE Observatory
description: Correction of the beta plain-list pane into the reviewed graphical terminal dashboard.
tags: [work-mode, observatory, mods, sse, fork, terminal]
timestamp: 2026-11-07T21:00:00-06:00
---

# Correction

Beta 93be24c did **not** implement the reviewed mockup. It displayed a colored list with tabs. It had no
split Work/SSE overview, graphical progress, parallel lanes or visible active SSE fork. Reporting that as
the mockup implementation was incorrect.

# Implemented

The terminal Pane now returns a `Client` surface module (`hooks/observatory.client.js`) rather than the old
Box/Text/Button list for Work and SSE:

- Work hierarchy, P1–P5 color, status glyphs, progress bar and selected-item inspector.
- Parallel slots and P1 reserve in the header.
- Work tree and SSE flow shown together from a 48-column dock upward—the measured Claude dock is 51 cells.
- SSE state counts and receipt summaries.
- Active one-shot SSE fork projection: actor id, selected model and receipt. Active actors come directly from
  `side-intake.js`'s host-owned `actors` map; they are not inferred from receipt text.
- Keyboard: arrows/j/k, Page Up/Down, Home/End, W/S, Enter, P, F and R.
- Mouse: top thirds switch Work/SSE/Settings; rows select by pointer.
- Mobile, desktop, VS Code and narrow surfaces retain the cross-surface Button fallback.

# Real-SSE corrections (after beta 8bd9fe2)

A real SSE event delivered through the owned router's receiver exposed three defects that the engine
tests did not:

1. **`/work-mode view` silently did not open the pane.** `openDetails` gave up when its display refresh
   was superseded (an idle poll or turn start bumps the refresh generation, and `refresh()` then yields
   `null`). The command still printed "Work & SSE pane: …". Fix: the request falls back to its own
   snapshot read, and the command reports `off`/`unavailable` instead of claiming success. Regression:
   `tests/test_work_observatory.py::test_view_opens_even_when_a_display_refresh_overlaps` fails on
   8bd9fe2's `register.js` (`opened: False`) and passes now.
2. **SSE column rows vanished.** Rows were clipped to the column's outer width; inside the border and
   padding they wrapped, so "No active side fork" and receipt summaries were pushed out of the 51-cell
   dock. Rows are now one-row boxes clipped to the inner width with `wrap: 'truncate-end'`.
3. **A finished fork stayed painted.** When the actor list shrank, Claude Code 2.1.310 left the retired
   rows' cells on screen. Rows are now keyed by position, padded to the full inner width on the panel
   background, and the fork area keeps a fixed three-row slot (title, fork id, model), so the
   receipt rows never move between the active and finished states.

# Fullscreen boundary

Claude Code 2.1.319's Mods `$.ui.open()` accepts `rows` and `columns`, not a fullscreen flag. The binary's dock
calculation remains at most 45% (`min(floor(width*0.45), 90, width-70)`). `e.viewport.isFullscreen` only reports
whether the host docks a pane. The implementation requests 90 columns and 32 rows—the maximum useful API request—but
cannot take over the complete terminal.

# Verification

- Engine-mounted terminal Client, pointer/keyboard, settings fallback and mobile fallback pass.
- Active SSE test shows a P1 processing receipt and its summary while side work is active.
- Real conhost shows populated Work hierarchy, 1/4 progress and two SSE receipts side by side.
- [Local real-console overview](evidence/local-work-sse-overview.png)
- [Verification](verification.md)
