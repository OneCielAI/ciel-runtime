---
type: research
title: Do the Stop hooks duplicate the side actor's main-idle check? No — they do different things
description: The "Ran N stop hooks" line counts command hooks in Claude settings (Ciel Runtime's tool-guard, plus another app's hook on this machine). The tool-guard Stop hook does no idle detection and nothing reads its Stop records. The side actor's idle state comes from the ciel-work plugin's turn.start/turn.complete. ciel-work's own Stop handler is not in that count and does Work/side jobs the idle wake does not.
timestamp: 2026-11-17T15:00:00Z
tags: [stop-hook, tool-guard, side-actor, idle, btw_with_mcp, work-mode, research]
---

# Question (user, 2026-11-17)

> 이제는 싸이트 액터가 메인세션의 유휴 상태를 체크해주기때문에 ran 1 stop hook. 2 stop hook은 싸이드액터가 동작하는 옵션 btw_with mcp 혹은 work mode 상태에서는 동작을 정지시켜도 될거같은데.

> 같은 동작인지 확인후 ..리서치먼저

In English: "Now that the side actor checks main's idle state, the 'Ran 1 stop hook' / '2 stop hooks' could be disabled while the side actor runs (btw_with_mcp or Work mode)." And: "Research first whether it is the same behavior."

# Measured

**What "Ran N stop hooks" counts.** Claude Code writes one `stop_hook_summary` record per Stop. Its `hookInfos` names each hook that ran.

| Session | `hookCount` | Hooks in `hookInfos` |
|---|---|---|
| This session (5674b8f5) | 2, in all 1,794 records | `ciel-runtime-tool-guard.py`, plus another app's command hook registered in the same settings file |
| Sessions of 2026-10-19, 10-20, 10-24 | 1 | `ciel-runtime-tool-guard.py` only |

The plugin hooks (`ciel-work`'s `classic.Stop`) are not in this count.

**What the tool-guard Stop hook does** (`ciel-runtime-tool-guard.py` `handle_stop`).
- It writes one log line and one JSON record.
- Its only blocking path: in native Plan Mode, when the last answer was a short acknowledgement without a tool call, it blocks once so the model continues (plan-mode stall guard).
- `SubagentStop`: log only.
- Nothing in `ciel_runtime_support` or `ciel_runtime.py` reads those Stop records (search for the log files and "Stop guard": no consumer).
- In the retained tool-guard log, "Stop guard blocked plan idle" appears 0 times.
- Run time per Stop in this session: 127–147 ms.

**Turn effect in this session's transcript.** Of 384 main turns, 345 had exactly one Stop pass. 0 turns carried a "Stop hook feedback" continuation. So no Stop hook kept a turn going in this session.

**Where the side actor's idle state comes from.** `ciel-work` `register.js`:
- `sideMainBusy(() => working)`;
- `turn.start` sets `working = true`;
- `workTurnComplete` sets `working = false` and calls `sidePromptTurnComplete`, which wakes an idle main for any appended row no request carried yet (`wakeUnread`).

No Stop hook is involved.

**What `ciel-work`'s own Stop handler does** (`workStop`, not in the "Ran N" count). It runs the Work pump, then presents at most one of the following and continues the same turn:
1. a pending high-priority side event (same-turn delivery);
2. a reminder for an appended, unacknowledged side event;
3. delivery-uncertain notice;
4. ACK-retry notice;
5. side answers to review;
6. the Work result review gate;
7. Work auto-continue for actionable items.

A Stop that is itself a continuation (`stop_hook_active`) skips 3 and 4.

# Answer

| Stop path | Same as the side actor's idle check? |
|---|---|
| tool-guard `Stop` (counted in "Ran N") | No. It never detected idle; its one job is the Plan Mode stall guard. |
| The other app's hook (counted in "Ran 2" on this machine) | No. Not Ciel Runtime's. |
| `ciel-work` `classic.Stop` (not counted) | No. The idle wake covers only delivery to an idle main, in a new turn. Stop delivers in the same turn, and items 3–7 have no idle-wake counterpart. |

Not changed: nothing was edited or disabled.
