---
type: feature-record
title: Side actor instruction shows the built-in default and edits in a Markdown dialog
description: The web Settings row for the SSE side instruction now shows the built-in default instead of an empty box, and editing opens a popup with a Markdown editor and a live preview.
timestamp: 2026-11-15T21:30:00Z
tags: [work, side-intake, settings, web, markdown]
---

# Requirement (user, 2026-11-15)

- **Problem:** "싸이트 액터에게 주는 추가적인 프롬프트들이 지금은 웹대시보디에셔는 기능 기본값사용이면 빈텍스트박스로나오는데" (with the default in use, the web dashboard shows an empty text box).
- **Show the default:** "기본값도 볼수있게해주고" (let me see the default too).
- **Edit in a popup:** "수정하는것은 팝업 창을열어서 MD형태를 수정하고 프리뷰하게해줘" (edit in a popup, as Markdown, with a preview).

# Design

- **Built-in default:**
  - `side_intake_settings.DEFAULT_SIDE_INSTRUCTION` is the exact `PROMPT` of `ciel-work/hooks/side-intake.js`. That prompt is what the native side fork receives.
  - It is regenerated from the JS source, and a unit test pins the two equal.
- **Operator instruction:**
  - The stored `work_side_intake_instruction` stays the operator part only.
  - It is appended after the default under `## Operator-configured instruction`, as before.
  - An empty value removes the key, so the side fork runs on the built-in default only.
- **Settings row:** `work_live_settings` gives this row `editor: markdown`, plus `stored_value`, `effective_value`, `default_value` and `using_default`. It is the same shared path for the menu, web, REST, MCP and ctl.
- **Web Settings tab:**
  - **State line:** "Built-in default in use (no operator instruction)" or "Built-in default + operator instruction".
  - **Preview:** the rendered effective instruction.
  - **Button:** "View / edit…".
  - **Dialog (`#md-dialog`):** a Markdown textarea and a live preview. The default is shown on top and cannot be removed; the operator text is appended under a separator. The buttons are Cancel, "Use built-in default only" (writes an empty value) and Apply.
  - **Rendering:** builds DOM nodes with `textContent` only and never uses `innerHTML`. It covers headings, lists, paragraphs, fenced code, inline code and bold.

# Verification

## Unit

`tests/test_side_instruction_view.py` (5 tests) checks:
- the default equals the JS `PROMPT`;
- the effective value is the default plus the appended operator text;
- an empty write removes the key;
- the row fields;
- the web markers.

## Owned browser E2E

- **Source:** `e2e-source/side_instruction_web.py`, run against a fresh isolated config with the real Work HTTP routes and system Chrome.
- **Result:** 6/6 PASS (`evidence/result.json`):
  - the row shows the built-in default when nothing is set;
  - the editor preview renders the default plus the operator Markdown (h3, 16 list items, bold, code, separator);
  - Apply stores only the operator instruction;
  - the row then says "Built-in default + operator instruction";
  - the REST row carries the default, stored and effective values;
  - at 390 px the editor opens with no horizontal scroll.
- **Screenshots:**
  - desktop: `evidence/web-desktop-1-row-default.png`, `web-desktop-2-editor-preview.png`, `web-desktop-3-row-custom.png`;
  - 390 px: the same three as `web-390-*`.

# Notes

The default text is the side prompt as of this commit. It includes the live-item relay and gate re-check paragraphs added the same day (see [gate re-check](/docs/journal/2026/11/15/features/gate-recheck/record.md)).
