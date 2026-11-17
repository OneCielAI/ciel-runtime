---
type: fix-record
title: KOBE P3 side events failed because Claude Code 2.1.326 renamed saved MCP results; the forwarded original woke main with the wrong instruction
description: Claude Code 2.1.326 adds a random suffix to the file it saves a large MCP result in. The side actor's host read of the Walkie thread refused that name, so every large-thread event failed before any fork or model call and was quarantined after 6 attempts. Its original did reach main, but an idle main was told it was a released wait to continue. Both are fixed.
timestamp: 2026-11-17T09:00:00Z
tags: [side-intake, kobe, claude-code-2.1.326, persisted-mcp-result, main-forward, wake-prompt]
---

# Questions (user, 2026-11-17)

> Kobe 가 p3 sse 사이드액터 실패인이유 확인해줘

> 또 사이드액터가 실패하면 모델에 실패하면 폴백모델로 가고 또실패하며 메인에 알림해야하는데 둘다 실패

In English: "Find out why KOBE's P3 SSE side actor fails." And: "When the side actor fails on a model, it should go to the fallback model; if that fails too, main should be notified. Both failed."

# Measured on KOBE (read-only)

Details are in [evidence/kobe-measurements.txt](evidence/kobe-measurements.txt). Nothing on KOBE was written or restarted.

- **Versions.** KOBE runs ciel-runtime `5d65674`. Claude Code 2.1.326 was installed at 02:03Z and first appears in the transcript at 02:25Z.
- **Saved-result file names changed.** The last name without a suffix is at 02:15Z; the first `-<ms>-<6 chars>` name is at 03:19Z.
- **18 P3 receipts failed for one reason.** They were received between 03:18Z and 06:59Z, and each was quarantined with `source_context` after 6 attempts. The reason was "Source persisted result is not this session's exact file", with `use_parent_model=false`.
- **No side fork and no model request was made for any of them.**
- **Earlier quarantines are different.** The 11-10 to 11-16 P3 quarantines are thread-size and context-budget refusals, decision validation failures, and six 11-11 `turn_error`s. None of them has this reason.

# Cause (confirmed)

**The name format changed.** Template strings in the two local Claude Code binaries:

| Version | Saved-result name |
|---|---|
| 2.1.325 | `` `mcp-${Dn(r)}-${Dn(n)}-${_e}` `` (`_e = Date.now()`) |
| 2.1.326 | `` `mcp-${In(r)}-${In(n)}-${Date.now()}-${Math.random().toString(36).slice(2,8)}` `` |

**Anthropic changed it on purpose.** The official CHANGELOG for 2.1.326 says: "Fixed parallel MCP tool calls reading another call's saved result when two long results arrived in the same millisecond (anthropics/claude-code#105138)".

**The side reader rejected the new name.**
- The side actor reads the Walkie thread on the host before it spawns a fork (`side-intake.js` `fetchSourceMessage` → `mcpText`).
- KOBE's root reads were 154K–585K characters, so Claude Code saves them to a file and returns a stub.
- `mcpText` follows the stub only to the session's own `tool-results/mcp-<server>-<tool>-\d{1,20}.txt`.
- The suffixed name does not match that pattern, so the read throws `SourcePrefetchError` (`source_context`).

# Why there was no fallback model, and what main received

**No model retry was possible.**
- The model fallback (retry on main's model, `cadd1a5`) applies when the side fork's turn ends on an API error or the model is unavailable.
- These events never reached a fork.
- A failed source read is retried 5 times with backoff and quarantined on the 6th failure. Then its original is forwarded to main ([../../../16/fixes/side-failed-forward/record.md](../../../16/fixes/side-failed-forward/record.md)).

**The forward to main worked.**
- All 18 have `notice_state=appended`.
- Each event appears in main's transcript as a plugin note holding the original, plus the line "original of failed event … → main (appended)".
- Each appears in exactly one main model request.
- Main acted on several: it replied on Walkie to two of them, and wrote "No action needed" for the GitHub notices.

**Defect found: the idle wake described the forward wrongly.**
- When main was idle, the wake prompt called the forward a "Ciel side released recorded wait … Read it there and continue … needs no acknowledgment" (seen in KOBE's transcript at 03:29–06:00Z).
- `noticeLead` knew only the relay and release leads, so a forward fell back to the release text.

# Fix (`ab6edbe`)

1. **`mcpText` accepts both name forms.** The pattern is now `-\d{1,20}(?:-[0-9a-z]{1,6})?\.txt`; the suffix is the lowercase base-36 that `Math.random().toString(36).slice(2,8)` yields.
   - Unchanged checks: config directory, four path segments, this session's id, `tool-results`, exact server and tool, length equal to the stated count, 4 MiB cap.
   - New check: the `projects/<project>` segment may no longer be `.` or `..`. Before this, those values were accepted and resolved outside `projects/`.
2. **The forward has its own wake lead.**
   - `FORWARD_LEAD` is the first words of `side_forward.text`.
   - The wake now says the side could not process the event, that its original is in the message, and that main decides what reply or work it needs.
   - It names `main_forwards` as the durable source.

# Verification

- **Fail-before** ([evidence/fail-before.txt](evidence/fail-before.txt)). The new tests run against HEAD `334f4c1` give 6 failures:
  - 3 suffixed names refused with the KOBE reason;
  - `.` and `..` project segments accepted;
  - the forward wake says "released recorded wait".
- **Pass-after** ([evidence/pass-after.txt](evidence/pass-after.txt)):
  - changed modules 72/72;
  - side forward, MCP, release, gate and real-backend modules 104/104;
  - `claude plugin test ciel-work` 103/103.
- **Linux** ([evidence/linux-tests.txt](evidence/linux-tests.txt)): python:3.11-slim with Node, 9 modules, 176 passed. The node-based mod tests ran; none were skipped.
- **Real Claude Code E2E** ([evidence/e2e-real-claude-code.txt](evidence/e2e-real-claude-code.txt)). Setup: an owned process using a temp Claude config, a stdio Walkie fixture, a router stand-in and a loopback model gateway. No live session or router was involved.
  - HEAD code on 2.1.326: saved `…-k8eakd.txt`, then `work_side_fail` with the KOBE reason. No fork and no model request.
  - Fixed code on 2.1.326: saved `…-rjylyu.txt` and `…-7s1bzq.txt`. One side-fork model request carried the event, followed by `work_side_resolve` and `work_side_reply`.
  - Fixed code on 2.1.321: unsuffixed names, with the same completed flow.
- **Windows CI groups and deployment:** see [log.md](log.md).

# Not changed (reported to the user)

- **The 18 quarantined receipts stay quarantined.** Their originals already reached main, and main acted on them. A side re-run would post late duplicate Walkie replies. If wanted, an explicit operator retry command would have to be added.
- **The rest of the delay is unchanged.** A deterministic `source_context` failure is still retried 5 times before the forward, a delay of about 5–12 minutes per event on KOBE. This fix removes the cause for this reason, but not that retry policy.
- **KOBE gets the fix only after an update and a session restart.** The mod is loaded from the installed package, and KOBE is a managed machine, so this needs the user's decision.
