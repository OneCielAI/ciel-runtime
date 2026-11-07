---
type: design-mockup
title: Ciel Work Observatory
description: "Terminal-faithful interactive mockup for a keyboard/mouse/color Work tree and SSE side-intake observatory, including live side-model selection."
tags: [claude-code-mod, work-mode, sse, side-intake, terminal-ui, mockup]
timestamp: 2026-11-07
---

# Scope

The mockup visualizes one live Walkie session:

- hierarchical Work tree, priorities, progress, waiting and blocked counts;
- active and pending goals plus parallel capacity and the P1 reservation;
- SSE receipt flow through retained/processing/answered/Work-queued lanes;
- selected Work/SSE detail without raw payloads;
- side model picker that applies from the next SSE event.

# Verified Mod capabilities

Claude Code 2.1.310's generated `claude-code.d.ts` confirms:

- `PaneOpenArgs`: focused panes, Escape close, requested rows and dock columns;
- `Button`: mouse click, focus, Enter and one-letter/digit hotkeys;
- `Box`: theme/raw colors, borders and hover restyling/reveals;
- terminal `Client`: `onKey` (arrows, tab, home, pageup) and `onPointer` (down/move/up/enter/leave with cell coordinates);
- terminal `Raster`: 24-bit foreground/background per glyph cell, repaintable with `$.ui.blit`;
- terminal `Image`: PNG/RGBA only in terminals with an image protocol, so it is not the baseline;
- desktop/vscode `Svg`: richer non-terminal rendering for a later surface-specific layer.

The proposed baseline therefore uses text, box drawing, bars and color cells. It does not depend on terminal images.

# Current-data facts shown

The snapshot was taken at Work version 1740. It contained 13 queued goals, 23 side receipts and 11 P1 side receipts. The configured side model was `_Walkie_Haiku_Terra`; the router log showed that combo and `_API_KEYS-SSE-1` returning 404 `Combo has no executable targets`. `_Walkie_Opus55-Sol61-Fast` was the functioning main/fallback route.

These observations are time-specific evidence, not permanent claims about the provider.

# Data boundary

`evidence/snapshot.py` copies live files into scratch before reading them. `evidence/mockup-data.json` contains only these allow-listed fields:

- Work: node id/parent/title/status/priority, progress counts, attention counts, active/queue/focus;
- side: short receipt id, event type/message id, source priority, derived state, timestamps, decision action/item/delivery/summary, failure category/model/attempt count and parent retry;
- model: configured/main model, combo ids and router-observed working/failing model ids;
- parallel: policy and this session's active leases.

It excludes raw event text, routes, credentials, reply tokens, transcript previews and reply bodies. A word such as `preview` can still occur in a Work title; validation is by JSON keys, not substring.

# Interaction map

| Action | Mockup | Real Mod primitive |
|---|---|---|
| Move selection | ↑ / ↓ | `Client.onKey` or focus ring |
| Collapse tree | ← / → | `Client.onKey` + local state |
| Switch Work/SSE | Tab | `Client.onKey` |
| Inspect | Enter / click | `Button.onPress` / `Client.onPointer` |
| P1 filter | P | Button hotkey/local state |
| Focus-first | F | existing `work_focus` mutation |
| Model picker | M / click | `Select` with `work_live_settings` |
| Hover details | pointer | keyed `Box.hover` |

# Evidence

- [Interactive mockup](mockup.html) (open with `evidence/mockup-data.js` beside it in the original scratch run; screenshots are canonical evidence)
- [Overview](evidence/screens/01-overview.png)
- [Work selection](evidence/screens/02-work-selected.png)
- [SSE selection](evidence/screens/03-sse-selected.png)
- [P1 filter](evidence/screens/04-p1-filter.png)
- [Live model picker](evidence/screens/05-model-picker.png)
