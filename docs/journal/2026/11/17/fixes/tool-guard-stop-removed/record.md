---
type: fix-record
title: The tool-guard Stop hook is removed
description: The user decided to delete the tool-guard Stop hook. Its plan-mode continuation added unrelated instructions to main's context, and the Work side actor watches main's idle state. Launches no longer register it, an older settings file loses it on the next launch (other Stop hooks are kept), and the script makes no Stop decision.
timestamp: 2026-11-17T15:00:00Z
tags: [stop-hook, tool-guard, plan-mode, context, side-actor]
---

# Request (user, 2026-11-17)

> tool-guard Stop 은 오히려 메인 컨텍스트를 오염만시키는거같고 .. 집중을 못하게 컨텍스트를 인지 하지못한상태에서 이거저것 집중해야하는일외에 다른일을줘서 문제야.. 일단삭제

> work 모드의 사이드액터가 메인세션이 유휴상태를 충분히 더 이성적으로 체크해준다보곰

In English: "The tool-guard Stop hook only pollutes main's context. Without knowing the context, it hands main things other than what it must focus on. Delete it for now." And: "The Work mode side actor checks main's idle state well enough, and more sensibly."

The research that preceded this is [../../research/stop-hooks-vs-side-idle/record.md](../../research/stop-hooks-vs-side-idle/record.md).

# Change

1. **The launch no longer registers the guard on `Stop`.** `tool_guard_hooks.py` drops `Stop` from `TOOL_GUARD_EVENTS_WITHOUT_MATCHER`.
2. **An existing settings file loses it on the next launch.**
   - `ToolGuardHookPolicy.retired_events = ("Stop",)`.
   - `install_tool_guard_hooks` removes the tool-guard handler, and legacy claude-any handlers, from `Stop`.
   - Every other `Stop` hook is kept.
   - A `Stop` event left empty is deleted.
3. **The script makes no Stop decision.** `ciel-runtime-tool-guard.py` `handle_stop` returns nothing for `Stop`. This covers a settings file that still calls it before the next launch. The plan-stop helpers are deleted.
   - Deleted: `should_block_plan_stop`, `transcript_latest_turn`, `short_resume_prompt`, `non_actionable_stop_text`, `message_text`, `message_has_tool_use`, and the script's `PLAN_GUARD_MARKER`.
   - `SubagentStop` stays log-only.
4. **Unchanged.**
   - ExitPlanMode auto-approval under bypass (`PermissionRequest` and `PreToolUse`).
   - The EnterPlanMode denials.
   - The router's `PLAN_GUARD_MARKER` filter, which still drops old guard text from earlier transcripts.
5. **Docs.** `docs/Plan-Mode.md` and `CHANGELOG.md`.

# Verification

- **Fail-before** ([evidence/fail-before.txt](evidence/fail-before.txt)). The new tests run against HEAD `42b1ff8`: 5 failures and 1 error.
  - HEAD registers `Stop`.
  - It keeps the guard in an existing `Stop` entry.
  - It emits `{"decision":"block","reason":"[ciel-runtime-plan-guard] …"}` for a plan-mode short acknowledgement, for both routed and anthropic providers under bypass.
- **Pass-after on Windows** ([evidence/pass-after.txt](evidence/pass-after.txt)). Tool guard, architecture contracts, launch contracts and deepseek provider: 315 run, 0 failures.
- **Linux** ([evidence/linux-tests.txt](evidence/linux-tests.txt)). python:3.11-slim, the same 4 modules: 273 passed.
- **Real Claude Code 2.1.326** ([evidence/e2e-real-claude-code.txt](evidence/e2e-real-claude-code.txt)). The settings file was rewritten by each tree's own installer.
  - HEAD: the Stop summary reads `hookCount 2` with tool-guard.
  - Fix: `hookCount 1`, holding only the other hook.
  - That run did not reproduce the plan-mode block itself, because no `plan_mode` attachment was written. The block is shown by the unit fail-before.

# Takes effect

- The installer rewrites `~/.claude/settings.json` at the next Ciel Runtime launch, so the change takes effect then.
- Until then the `Stop` entry keeps the path it was written with: a snapshot directory of the build that last launched (on this machine `ciel-runtime-5d65674…`). Sessions running now therefore still run that older script on `Stop`.
- Whether Claude Code re-reads hooks from settings mid-session was not checked here.
