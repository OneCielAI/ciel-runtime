---
type: Feature
title: User-directed existing item focus
description: Existing root/subtree priorities and exclusive continuation persist until explicit release.
tags: [work, focus, priorities]
timestamp: 2026-10-27
---
# Request and baseline

User asks for “AAAA related items first and only those.” Existing root reorder/select work, but urgent only creates a new goal; no persisted subset focus existed. Actual CLI baseline8ebacfa has no work_focus tool and cannot establish the hold. Strict baseline fixture does not proceed to Write/done without focus (initial fixture unconditionally advanced; corrected and rerun).

# Contract

work_focus(node_ids=[exact existing IDs in priority order], user_requested=true, expected_version=...). Roots or subtrees; [] releases. No fuzzy automatic title matching: the model resolves requested items to IDs. Ancestor waits/blocks and approval remain. Other statuses/notes/waits are preserved. Focus remains after completion: unrelated work stays held until explicit clear. New goals do not break exhaustion hold; deleting/replacing focused nodes fails closed. Urgent holds cannot be overridden by unrelated focus. This is scheduling, not a shell side-effect security boundary.

Inspector: f Focus only this item; a Append to focus (if focus exists and another item inspected); c Clear focus / resume all. Prompt summary, header, queue held flags and bottom status line reflect focus.

# Verification

- tests/test_work_focus.py9 regressions: subtree order vs unrelated in_progress, persistence, CAS, exhaustion, clear, ancestor wait/block, urgent conflict, deletion/replacement, new-goal/reorder safety.
- Existing Work Python210 passed. Mod66 passed before Append button addition; final run recorded separately.
- Actual CLI source9/9: first AAAA completed, end_turn, model receives Stop block for second AAAA, second completed, unrelated leaf untouched, exhausted focus stops. Single user prompt. source/01-focus-exhausted.png and request capture.
- Actual Inspector source-ui4/4: f sets focus, c clears, node data unchanged. source-ui/02-focus-control.png,03-focused.png,04-cleared.png. Hotkey driver extended to send a letter without Enter (original key-only path supports named virtual keys, not letters).
- No live or remote restart/install during fixture runs.
