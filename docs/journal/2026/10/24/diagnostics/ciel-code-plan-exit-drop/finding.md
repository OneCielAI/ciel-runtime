---
type: Diagnostic Finding
title: Router drops ExitPlanMode after native compaction, so plan-mode turns end empty
description: In the ciel-code session every empty or reasoning-only glm-5.3-flash turn since 14:43 was an ExitPlanMode call that the router dropped because it no longer recognised plan mode after Claude Code's native compaction.
tags: [plan-mode, exit-plan-mode, compaction, ollama, glm, empty-end-turn]
timestamp: 2026-10-24T21:45:00Z
---
# Report

User screenshot, local `ciel-code` session, `glm-5.3-flash (ollama-cloud)`, status line `plan mode on`. Each "계속" ends with `[ciel-runtime] Upstream model returned reasoning without a final answer or tool call` or `... an empty end_turn with no text or tool call`, plus a `[ciel-runtime-plan-guard]` stop-hook message saying Claude Code is still in plan mode.

# Observations

Router 9464 (`0.2.51-nightly.20261024-102326.a7e28f9`, PID 54764), local time ([router-9464-drops.txt](evidence/router-9464-drops.txt)):

- Every `ollama_empty_stream` and `ollama_reasoning_only_stream` today is immediately preceded by `dropped ExitPlanMode while plan mode is not active`: 9 of 9 (14:43:41, 14:45:04, 14:45:47, 14:46:03, 15:11:51, 15:26:11, 15:34:55, 15:35:00, 16:30:20).
- The log line is written by `ConversationTurnPolicy.plan_mode_tool_name_for_emit` (`protocols/conversation_turn_policy.py:242-244`) when the upstream stream contains an `ExitPlanMode` tool call. The model did call a tool; the router removed it, and the empty/reasoning-only notice followed.

Claude Code transcript `d56cd7ef` (UTC, version 2.1.310; [transcript-plan-timeline.txt](evidence/transcript-plan-timeline.txt)):

- 19:38:05Z (14:38 local): `EnterPlanMode` tool call, `plan_mode` attachment.
- 19:43:38Z: `compact_boundary`, `trigger: auto` (968,507 → 22,383 tokens). Router: `native_compact_started` at 14:41:19, first post-compaction request at 14:43:39 with `msgs=5`.
- After the boundary: `plan_mode` attachments at 19:43:37Z and 20:34:52Z, `permissionMode: plan` up to 21:30:16Z. No `EnterPlanMode` call after the boundary.

# Mechanism

1. The router infers plan mode only from the request body (`plan_mode_active`, `conversation_turn_policy.py:57-94`): an `EnterPlanMode`/`ExitPlanMode` tool result in history, a message-level `attachment` dict, or a content block typed `plan_mode`.
2. Real Claude Code 2.1.310 does not send attachments as such. Isolated capture ([plan_wire_probe.py](evidence/plan_wire_probe.py), [plan-wire-result.json](evidence/plan-wire-result.json), [captured-main-messages.json](evidence/captured-main-messages.json)): with `--permission-mode plan`, the request messages have only `role`/`content` keys, and the plan state is plain text, "Plan mode is active. The user indicated that they do not want you to execute yet ...", inside a `system`-role message. On that body the router's `plan_mode_active` returns `false`, and `plan_mode_tool_name_for_emit(..., "ExitPlanMode")` returns `None` (dropped). The stored capture is redacted to message structure and the plan-mode reminder ([redact_capture.py](evidence/redact_capture.py)). Claude Code also includes CLAUDE.md files found above the workspace, and those are not kept.
3. Control: the same captured body with an `EnterPlanMode` tool call and result prepended → `true`, and `ExitPlanMode` passes through.
4. Before compaction, the `EnterPlanMode` result was in history, so plan mode was recognised. The native compaction removed it from the request, and the router lost the state while Claude Code stayed in plan mode.
5. The stop hook (`ciel-runtime-tool-guard.py`, `transcript_plan_mode_active` at 379, message at 524) reads the transcript file, where the `plan_mode` attachments are present. It sees plan mode active and tells the model to call `ExitPlanMode`, which the router then drops again.

`conversation_turn_policy.py` is identical between a7e28f9 and the current worktree (`git diff --stat a7e28f9` is empty), so the probe ran the same logic as the live router.

# Relation to the uncommitted Ollama semantic retry

The retry in [/2026/10/24/fixes/ollama-semantic-retry/verification.md](/2026/10/24/fixes/ollama-semantic-retry/verification.md) does not change these turns. It marks raw `tool_calls` as tool intent (`streaming_anthropic.py:2001`) before the plan-mode drop (`:2059`), so a dropped `ExitPlanMode` turn is not retried and still ends with the empty notice.

# Status

- ✅ The observed empty and reasoning-only turns in ciel-code since 14:43 were dropped `ExitPlanMode` calls, not empty model output.
- ✅ The router's plan-mode inference does not recognise Claude Code 2.1.310's on-wire plan-mode text, reproduced in isolation.
- 🟡 The exact post-compaction request body of the live session was not captured; the router does not log bodies. The conclusion rests on the log correlation, the transcript and the isolated capture.
- Separate, not analysed: every Ollama stream in a7e28f9 and in this worktree also logs `ollama_stream_trace_finalize_failed ... unexpected keyword argument 'text_so_far'`.
- No code, configuration, live router or session was changed. Live router PIDs were the same before and after the probe ([before](evidence/live-health-before.txt), [after](evidence/live-health-after.txt)).

# Fix

Fixed by reading Claude Code's plan-mode reminder text, see [/2026/10/24/fixes/plan-mode-reminder-text/verification.md](/2026/10/24/fixes/plan-mode-reminder-text/verification.md).
