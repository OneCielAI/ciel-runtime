---
type: Investigation
title: Edward unfinished Work and terminated API error
description: Two distinct termination paths confirmed in Edward u22 on .14; neither has been fixed by this investigation.
tags: [work, end-turn, recovery, edward, diagnostics]
timestamp: 2026-10-26T20:40:00Z
---
# Target and scope

User approved investigation of Edward only on 100.64.0.14:1004. Sandbox: `u22/home/edward-ai`. Running router pid18768, port9397, instance `9397-4d48ef23cb8e`, Claude pid12436, Claude2.1.310, runtime `0.2.51-nightly.20261026-183726.81f6a4b`. Session `354281f4-a695-46c2-a0db-450d681953b8`. Remote local time is UTC-7.

No session, queue, config, process or service was changed. Two diagnostic scripts were initially copied to Windows Temp, contrary to the intended no-write scope; no further remote files were created. The scripts only read transcript bytes. Later read-only probes used Python over SSH directly. No elevation or ACL changes were attempted when access to the Work directory was denied; Work status was verified from recorded MCP results in the transcript instead.

# 1. Ordinary turn completion with unfinished work — confirmed

- 13:16:02.285 local: assistant text with `stop_reason=end_turn`, message1793045752651. Work results immediately before and after show executing1/9. User typed `.` at13:16:19.062 and a new request followed.
- 13:18:21.389: another text response with `end_turn` while Work remained1/9.
- 13:23:26.660: text response with `end_turn`. Last recorded Work progress4/11 at13:22:42.332.
- 13:24:48.344: the user's pasted response about waiting for a corrected commit SHA and QA permissions is text with `end_turn`. No intervening recorded Work update had completed those items.
- Stop hook summaries after these responses have no errors, no output and `preventedContinuation=false`. The installed Work mod contains no turn.complete hook. Its source hash matches the local81f6a4b snapshot.

These observed completions are not empty responses or an API transport failure. The model ended its turn while the Work board was unfinished. The transcript does not establish why the model chose to stop. The existing Work board and prompt instructions do not enforce continued execution after end_turn. A person-facing unfinished-work notice and an execution-enforcement policy are different features; a notice alone does not resume work.

# 2. API Error terminated — separately confirmed

At13:20:04.547, Claude recorded `API Error: terminated`, `isApiErrorMessage=true`, synthetic message77d4bc28-12de-4906-8d9d-c10cdcaff794, `stop_reason=stop_sequence`. `truncatedAfterOutput` is absent. Its parent is a user/tool_result record, not an assistant record. The user typed `.` at13:20:26.427.

The upstream failure ledger recorded message1793046003178 at13:20:03.864: `anthropic_stream_upstream_error_event`, `upstream_error_type=api_error`, visible_text=false, tool_uncertain=true, saw_tool_use=true, emitted_tool_use=true.

The installed recovery `_observe_error` requires `truncatedAfterOutput is True` and an assistant-id lookup for the error's parent. This record fails the flag requirement and has a tool-result parent. Consequently this error shape does not create a pending recovery in that code. Even independently evaluating the observed upstream failure returns `tool_use_seen`, not an eligible automatic continuation. Replaying after uncertain tool effects could duplicate operations; no replay was attempted.

The ledger has no handled recovery entry for this error. The router log level is ERROR, so INFO-level recovery decisions were unavailable; log level was not changed. The exact upstream reason for the terminated API error is not established by these records.

# Limits and status

- Edward's task code, QA access and deployment work were not acted on by this session.
- Existing11-step work was still incomplete at the reported stops. Later recorded updates reached6/11; this does not retroactively make the earlier stops complete.
- No automatic restart was requested. The approved scope was diagnosis, not restarting the managed session.
- Any remediation needs to distinguish safe continuation after ordinary end_turn from recovery of errors with uncertain tools; no unlimited retry or blind restart is justified.

# Citations

[1] [Recorded transcript facts](evidence/01-transcript-facts.txt)
[2] [Source guard and hash verification](evidence/02-installed-code-guards.txt)
