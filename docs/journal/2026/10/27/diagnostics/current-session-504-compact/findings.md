---
type: Diagnostic
title: Current session 504 and compact ordering
description: Screenshot 8 is a pre-output SSE error after completed compaction, distinct from the earlier end_turn.
tags: [retry, compaction, evidence]
timestamp: 2026-10-27
---
# Observed ordering

All local timestamps UTC-05 on 2026-10-27; native transcript is UTC.

- 18:02:22.302: assistant explicitly ended the turn (`end_turn`).
- 18:03:48.933: user prompt; compaction followed. Boundary records 261501 ms duration, preTokens843628, postTokens25785.
- 18:08:11.276: native automatic compaction boundary. 18:08:13 router verified checkpoint and history shrink.
- 18:09:45: upstream request, 298517 estimated tokens; 18:09:47 downstream HTTP200.
- 18:10:31: pre-output SSE error, text_len0, message_startFalse, emitted_tool_useFalse. Recovery skipped with no_message_id and turn ended reason=error. Transcript23:10:31.047Z records screenshot8 codex/gpt-6.1-sol-high HTTP504.

Thus this latest 504 happened after compaction, not before it. The earlier explicit end_turn preceded compaction. This does not establish the cause of every reported stop, or why the provider timed out.

# Limits

No retry/stopped marker occurs for this failed request. Exact wire frame, error.type and guard commit branch were not retained by the running old router9524 (PID8544,7792965). No claim of a proven retry fix for the actual failure. Gateway ProgramData logs returned Permission denied; no elevation or ACL changes attempted. Existing live sessions were not restarted.

# Evidence

- [Router timeline](evidence/router-timeline.txt)
- [Native metadata](evidence/transcript-timeline.json)
