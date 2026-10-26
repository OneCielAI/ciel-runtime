---
type: Architecture Finding
title: ciel-code hierarchical work board
description: The inspected ciel-code implementation stores multiple work items and per-item steps in one shared JSON board.
tags: [work, board, hierarchy, ciel-code]
timestamp: 2026-10-26T00:00:00Z
---

# Observed implementation

Canonical source: `C:/Users/djlov/ciel-code/src/sdk/work/items.ts`.

- `Board` contains `version`, `activeItem`, `items`, and `steps` (`items.ts:31-36`).
- `WorkItem` has id, title, lifecycle status (`planning|running|paused|done`), timestamps, and optional `sessionId` (`items.ts:21-29`). Current mutators do not populate `sessionId`; its resume mapping is a declared field, not proven wiring here.
- `WorkStep` has id, text, and `pending|in_progress|done` state (`items.ts:15-19`). The relationship is board -> item -> steps, although JSON stores steps in an item-indexed map.
- `addItem` initializes its step list, makes the item active, and persists (`items.ts:73-80`). `setActive` makes a planning item running (`items.ts:121-129`). Completing all steps marks the item done; reopening one changes it back to running (`items.ts:96-118`).
- Source-of-truth path: `CONFIG_DIR()/boards/active.json` (`items.ts:38-49`). Windows `CONFIG_DIR` is `%APPDATA%\ciel-code` unless `CIEL_CODE_CONFIG_DIR` overrides it (`src/sdk/configdir.ts:9-15`).

# Hierarchy limit

The current source implements a fixed three-level grouping: Board -> WorkItem -> WorkStep. Neither `WorkItem` nor `WorkStep` has a parent id or child array. It does **not** demonstrate arbitrary recursive Epic -> Item -> Subitem -> Step nesting. A deeper task tree would require an explicit schema/API extension with migration and cycle validation, rather than describing the existing board as an arbitrary-depth hierarchy.

# CLI and slash-command surface

`C:/Users/djlov/ciel-code/src/cli/workCmd.ts:10-70` implements `list|status|add|todo|done|state|active|remove`; `src/cli/main.ts:31-34` dispatches it and `:50-53` advertises it as the `/work` source.

`C:/Users/djlov/ciel-code/.claude/commands/work.md` requires `work list` first (`:19-21`), then an `AskUserQuestion` item selector with existing/new/keep options (`:23-28`), then item switch or creation and TodoWrite alignment (`:30-34`). Each completed step is immediately persisted with `work done`, and switching items requires confirmation (`:36-38`).

This is not a flat TodoWrite replacement: the design note explicitly distinguishes TodoWrite as one item's steps from Board/WorkItem as cross-item hierarchy and aggregate state (`C:/Users/djlov/ciel-code/docs/okf/ciel-code/architecture/concepts/work-items.md:51-54`).

# Persistence, concurrency, resume

`saveBoard` writes `${file}.tmp` then `renameSync` (`items.ts:52-58`), an atomic replacement pattern for readers. No lock, revision check, merge, or compare-and-swap is present in the inspected code. Each mutation loads then saves, so concurrent writers could lose updates; this is an implementation gap, not a measured race.

`nextId` uses a module-level sequence and only checks the currently loaded board (`items.ts:64-71`); no cross-process reservation is present.

`sessionId?` is documented as a resume mapping (`items.ts:27-29`), but this CRUD source does not read or write it. The design note proposes per-item sessions and resume, while its verification list still marks remote `/work` exposure unfinished (`work-items.md:56-59`). Treat end-to-end resume as partial until separately proven.
