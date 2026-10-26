---
type: Verification
title: Work conversation isolation
description: Distinguishes the installed global board leak from the session-scoped experimental Work correction.
tags: [work, sessions, isolation, verification]
timestamp: 2026-10-26T00:00:00Z
---

# Reported existing /work bug

The installed global `/work` command at `C:/Users/djlov/.claude/commands/work.md` invokes `C:/Users/djlov/ciel-code/dist/cli/main.js work`. The source `ciel-code/src/sdk/work/items.ts:38-39` resolves `CONFIG_DIR()/boards/active.json` with no conversation identity. Its header explicitly says sessions share the single file. This is distinct from the experimental runtime `/work-mode` mod. No external project files, global commands or existing board records were changed; the standing working-directory rule prohibits editing that other project.

# Experimental runtime correction

Previously `workspace_work_plan_store` used `WORKSPACE_STATE_DIR/work-plan.json`, which also shared plans among conversations in one workspace. Replaced it with `session_work_plan_store`, storing at `work-sessions/<sha256(runtime:conversation-id)>/work-plan.json` beneath the workspace. Missing or malformed identities fail closed; the old shared plan is not loaded, copied, migrated or deleted. Claude session_id is an optional schema field stamped by the mod; Codex identity comes from the native MCP call metadata, not model arguments. Missing identity is rejected at dispatch.

Claude mod reads the engine's `$.session.id()` each time, including after clear/resume; it overwrites any model-supplied session_id and resets display cache on session.end. Subagents in one conversation use the parent conversation's plan. Codex `_meta.sessionId` takes precedence over any model argument, and `_meta.threadId` is deliberately not used so child threads share the root conversation. Raw Codex-prefixed arguments without native metadata are rejected. The real Codex request capture confirms both native metadata fields. HTTP callers can forge metadata, so this is namespace isolation, not authentication against malicious local clients.

# Verified evidence

- [Python test output](evidence/session-isolation-python-tests.txt): 118 tests passed, including two conversations in one directory, same-id resume, runtime namespace separation, missing/unsafe id rejection and leaving legacy data unchanged.
- [Mod retest](evidence/mod/session-isolation-retest.txt): 13 passed. Initial mock returned the wrong result shape; corrected to `{value: sessionId}`. Initial failures retained in [first run](evidence/mod/session-isolation-tests.txt).
- [Real Codex summary](evidence/codex/native-metadata-isolation/summary.json): actual Codex 0.158.0 app-server, isolated router and fake Responses gateway. No session_id in model tool arguments; all three MCP calls carried native `_meta.sessionId` equal to the actual conversation id. `native_mcp_metadata_verified=true`; current conversation executing, other conversation empty/planning, missing identity rejected.
- [MCP wire calls](evidence/codex/native-metadata-isolation/mcp-wire-calls.json), [current response](evidence/codex/native-metadata-isolation/router-session-state.json), [other response](evidence/codex/native-metadata-isolation/other-session-state.json), [missing identity](evidence/codex/native-metadata-isolation/missing-session-state.json).

# Deployment status update

The existing external `/work` bug was subsequently fixed and locally deployed under explicit user authorization; see `C:/Users/djlov/ciel-code/docs/journal/2026/10/26/fixes/work-session-isolation/verification.md`. Its old shared data was preserved. The initial investigation above describes the state before that separate fix.

The experimental Work mode now has actual Claude CLI auto/ask/resume evidence and local snapshot deployment, recorded in [verification](verification.md). Earlier workspace-shared and model-supplied-id Codex evidence is superseded by native-metadata evidence. Interactive CLI screenshots are saved with matching console text; no live user session was restarted. Local router PIDs were compared before/after each actual Claude run and remained unchanged within the final installed run.
