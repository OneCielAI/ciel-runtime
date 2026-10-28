---
type: Diagnostic
title: Subagent model inheritance recheck
description: Exact parent and failing-child request metadata show the same model; current CLI inheritance tests also pass.
tags: [claude, subagent, model, cielairouter]
timestamp: 2026-10-27
---
# Actual failing session

[49 request metadata rows](evidence/parent-child-models.json), read-only from local session `5bd157d5-5106-4f58-98ec-de5ecb8725d9` and failed child `af3b0b7f5480a5c86`, 19:15–19:24Z: parent and child both request `ciel-runtime-cielairouter-_walkie_opus55-sol61-fast`. The Agent invocation had no model override. Error messages listing claude and codex members therefore do not establish a child-model mismatch.

# Existing policy

`claude_environment.py:598-631` forces non-native provider subagents to inherit their parent at spawn time (`CLAUDE_CODE_SUBAGENT_MODEL=inherit`, `CLAUDE_CODE_SUBAGENT_MODEL_FORCE=1`). Native Anthropic behavior is preserved. No product change was needed for this report.

# Current installed proof

Actual Claude Code **2.1.311** using snapshot **273856d** against a loopback fixture:

- [Model argument case](evidence/current-cli-modelarg/result.json): explicit parent `--model fake-parent-b`, child also `fake-parent-b`.
- [Full case](evidence/current-cli-fixed/result.json): plain, `model=haiku`, `model=sonnet`, an agent definition with `model: haiku`, and a named teammate all use `fake-parent-a`.
- `--model fake-parent-b` and a subsequent interactive `/model fake-parent-b` make the next children use `fake-parent-b`.
- `inherit` never reaches the wire. Five print runs exit 0; live router PIDs unchanged. [Before switch](evidence/current-cli-fixed/named.png), [after switch](evidence/current-cli-fixed/named-switched.png).
- The verifier's `child_ok` field searches the final parent stdout for the child's marker; it is false even on success because final output contains only the parent's response. The actual child request models and successful Agent results are the evidence, not that field.

# Remaining failure

The reported upstream HTTP 504 remains separate Work n394/n436; matching model inheritance is not a fix for upstream member timeouts or missing provider credentials.
