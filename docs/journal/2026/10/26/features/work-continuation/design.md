---
type: Design
title: Work continuation and priority
description: Resume actionable unfinished Work after a normal turn ending, order goals by position, mark waits, link ids, hold everything for an urgent request, and show three levels above the prompt.
tags: [work, continuation, priority, urgent, ui]
timestamp: 2026-10-26T22:10:00Z
---
# Requests (2026-10-26, from the user)

1. When a turn ends normally and Work items remain, continue without the person typing a dot. Register pending requests as items without duplicates, work until all items finish, and while an item waits take up other items in parallel.
2. Adjust priority by changing the order of root items: by default among queued items; the active item moves only on the user's explicit request.
3. Mark an item that waits for something outside the conversation with a clock and a short note at the end naming what it waits for.
4. Show items with a linked id as `[ id ] description`.
5. Show three levels above the prompt (goal, branch, sub-items) instead of `3/8` counts.
6. Show finished items with a green check mark instead of `[x]`.
7. On the user's request, hold all items, put the ad-hoc request on top as urgent, break it down and execute it.

# Decisions

## Continuation (Claude, `classic.Stop` in the ciel-work mod)

- Evidence: the installed mod types define `classic.Stop` with `stop_hook_active`, `background_tasks` and `agent_id` (absent on the main thread), and `{ block: reason }` re-prompts. The hooks reference says Stop does not run after a user interrupt, API errors fire `StopFailure` instead, and Claude Code caps eight consecutive continuations, resetting the count whenever Claude calls a tool [2].
- The mod reads the exact-session router snapshot. It continues only when Work and the `work_auto_continue` setting are on, the hook is on the main thread, no background task is in flight (a background task wakes the session itself), and the router reports at least one actionable item.
- Actionable: an unfinished leaf whose path has no `blocked` status and no `waiting_on` label, in the active goal; or, when the active goal has nothing actionable and is not urgent, in a queued goal. A goal without steps is actionable as "plan it".
- No progress: if the plan version did not change since the previous continuation in this conversation, the next one is the last; after that the mod stops continuing and shows a toast until the plan changes or the person writes. Claude Code's own cap still applies.
- The re-prompt states facts: what remains, the next item, what other items wait on, and how to mark a wait when the agent needs the person. It does not ask for scope beyond the user's requests.

## Order and urgent

- Root order in the document is the priority order. `work_reorder` moves a root to a 1-based position among unfinished goals; position 1 is the active slot. Taking or leaving the active slot needs `user_requested: true`. Ties keep insertion order by construction.
- `work_urgent` creates an urgent goal in the active slot on the user's request; every other unfinished goal is held (shown as held, not actionable). When the urgent goal is done, the queue advances to the goal that was active before.
- The Work details pane offers Move up / Move down for root items; a person's press counts as the explicit request.

## Labels

- `waiting_on` (at most 120 characters) and `ref` (at most 64) are node fields set by `work_node_add`, `work_plan_set` children and `work_node_update` (empty text clears). Titles stay unchanged.
- Display: `[ ref ] title`, a clock and `waiting_on` at the end, a green `✓` (theme color `success`) for done.

## Codex

Codex receives the same rules as instructions only. Nothing in Ciel resumes a Codex turn; this is reported separately.

# Implementation boundary

No managed .14 restart or installation is authorized by the local/nightly release request.

# Citations

[1] Anthropic official Stop continuation example: https://github.com/anthropics/claude-code/blob/main/plugins/ralph-wiggum/hooks/stop-hook.sh
[2] Hook reference, sections Stop and Stop input: https://code.claude.com/docs/en/hooks
