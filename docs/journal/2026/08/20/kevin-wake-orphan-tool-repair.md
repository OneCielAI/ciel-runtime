# Kevin wake and orphan tool-call repair

## Reported observation

- Kevin repeatedly displayed `[ciel-wake] pending_ids=3478`.
- After `ai-net.get_message` visibly returned data, the upstream emitted two
  `invalid_request_error` events:
  - `No tool output found for custom tool call
    call_TqE66SGyCQxJYqXPdlJBpM98.`
  - `No tool output found for custom tool call
    call_ablHbpwrYbaOvR9XBmbzaL9X.`
- AI-Net lists `agent_551vhozpcb4n` as Kevin, runtime Codex, status active.

## Source evidence

- `format_llm_delivery_wake_prompt` deliberately emits only the pending local
  message IDs; it does not contain the external message body.
- Channel delivery commits its cursor only after a successful routed turn.
  Therefore a rejected turn leaves message 3478 pending and eligible for
  another wake attempt.
- `repair_replayed_response_items` repaired mismatched item IDs but did not
  validate that `function_call`/`custom_tool_call` entries had matching output
  entries. The Codex backend forwarded such orphan calls unchanged.
- The pasted upstream verdict names the missing-output call IDs directly. The
  original Kevin transcript/router request dump was not available on the local
  or Wing Dave filesystem, so no claim is made about the earlier event that
  removed those outputs.

## Implementation

- Build call-ID and output-ID sets after existing Responses item-ID repair.
- Retain tool records only when the same non-empty `call_id` exists on both the
  call and output sides.
- Drop orphan calls because replay cannot execute them again, and drop orphan
  outputs because no upstream call exists to receive them.
- Preserve the original request object when no repair is required.

## Verification

- Focused compatibility tests cover complete function/custom pairs, orphan
  calls, orphan outputs, foreign IDs, and router-minted IDs.
- A backend-boundary test uses the first reported Kevin call ID and verifies
  that the orphan never reaches the scripted upstream while the following user
  message remains intact.
- Final test results:
  - unit: 1088 passed, 44 skipped
  - router: 902 passed
  - channel: 369 passed, 80 skipped
  - runtime: 242 passed, 12 skipped
  - total: 2601 passed, 136 skipped
  - Ruff: all checks passed
- The installed local package was imported from its deployment directory and
  exercised with `call_TqE66SGyCQxJYqXPdlJBpM98`. Its repaired input contained
  only the following user `message`; the original two-item body remained
  unchanged. A complete custom-call/output pair returned the original object.

## Deployment evidence

- Source, global npm installation, local shared installation, and the active
  snapshot file all have SHA-256
  `A0B38961262AC7870317F99C892C4A1535525979C93EF632A177571C3C8DCDC2`.
- The same file was copied to Wing's global npm installation and its hash was
  confirmed at deployment time. The backup stamps are `20260820-191611`
  locally and `20260820-191630` on Wing.
- A later Wing live-import check could not run because SSH port 22 timed out.
  No conclusion is drawn from that unavailable check.

## Remaining live verification boundary

- Multiple local Ciel Python processes using snapshot
  `ciel-runtime-57956d91be2d3fc922edb81d449b8808143d747e` were created between
  11:34 and 13:03, before the 19:16 deployment. Updating the module file does
  not replace code already imported in those processes.
- The process list does not identify which of those processes owns Kevin, so
  terminating one by guess would risk another active session.
- Consequently the package repair is implemented, tested, and deployed, but a
  Kevin-session restart followed by delivery of pending ID 3478 is still
  required for live end-to-end confirmation. The task is not recorded as live
  complete until that observation is obtained.

## 2026-08-20 queued-wake recurrence

- A Kevin-session screenshot shows Codex `Queued follow-up inputs` containing
  message IDs 3636 through 3639.
- IDs 3637, 3638, and 3639 appear more than once; ID 3638 appears repeatedly
  in consecutive queue entries. This is not a queue containing each pending
  message exactly once.
- Queueing a new follow-up while a Codex turn is active is expected client
  behavior. Repeated copies of the same `[ciel-wake] pending_ids=<id>` are not
  evidence of distinct messages and are not treated as normal delivery.
- Source inspection rules out `submit_retries=4` alone as the explanation on
  Windows ConPTY: confirmation retries require a tmux pane snapshot; without a
  snapshot the injector stops after its first submit attempt.
- The screenshot does not identify whether duplicate entries originated from
  repeated Ciel poll/claim injection or repeated input generation on Kevin's
  host. Kevin's router log for IDs 3636-3639 is required to distinguish those
  paths. No code change was made from this screenshot alone.

## 2026-08-20 live queued-wake root cause

### Kevin host evidence

- The Kevin host was reached as `kevin-codex@100.95.132.58`; hostname
  `aap-pool-hera`.
- The affected process used Ciel Runtime
  `0.2.21-nightly.20260821-003258.0ea4cce` and Codex CLI `0.149.0` on Linux.
- The persisted Codex session is
  `01a00b77-e165-75a3-8255-17f55de2ebcf`.
- Its transcript records a turn from `2026-08-21T02:31:13.240Z` through
  `2026-08-21T02:48:00.098Z`.
- Runtime input records 3636 through 3639 arrived while that turn was running
  (the runtime-input file stores host-local times `21:40:34` through
  `21:44:03`).
- The TUI screenshot shows those IDs in `Queued follow-up inputs`, including
  repeated copies. Neither the Codex transcript nor `~/.codex/history.jsonl`
  contains an executed/submitted record for IDs 3636 through 3639.
- The turn completed and the TUI then displayed
  `No tool output found for custom tool call call_KjPlhUWIbFkQDJD6ywRc8ZCJ`;
  the queued follow-ups remained stranded until the session was restarted.
- After restart, IDs 3648 and 3649 were submitted once and entered a new turn,
  confirming that AI-Net receipt and Ciel runtime-input persistence were not
  the blocked stage.

### Confirmed code defect

- The Windows terminal poller blocks injection when either a tool call or the
  whole model turn is active.
- The POSIX terminal poller used only `active_tool_call`. It therefore injected
  wake text during model execution between tool calls. Codex placed that text
  in its internal follow-up queue instead of starting a turn.
- OpenAI Codex source defines queued follow-ups as input held until the current
  turn finishes and drains them through `maybe_send_next_queued_input()`.
  OpenAI issue 37974 documents a Codex failure path in which the TUI is ready
  but queued follow-ups remain permanently stranded. The Kevin transcript does
  not contain a prompt-edit failure record, so that particular Codex trigger is
  not attributed to Kevin; only the observed stranded-queue state is used.

### Repair

- `ChannelTerminalPolling` now receives both `active_tool_call` and
  `active_turn` signals and exposes `input_busy()` as their logical OR.
- The POSIX pending-message poller uses `input_busy()`, matching the existing
  Windows safety boundary.
- This is transport-wide behavior for supported CLI runtimes; it contains no
  Kevin, agent-ID, model, or provider exception.

### Verification

- Focused channel/transcript/architecture tests: 485 passed, 122 skipped.
- Full repository tests:
  - unit: 1,105 passed, 44 skipped
  - router: 902 passed
  - channel: 370 passed, 80 skipped
  - runtime: 244 passed, 12 skipped
  - total: 2,621 passed, 136 skipped

## 2026-08-20 live validation correction

### Contradicting live result

- Commit `81fd842` and nightly
  `0.2.21-nightly.20260821-031340.81fd842` were installed on Kevin.
- During an active turn, a local-only verification notification was admitted
  as runtime input 3667. The input file advanced to 3667 while the delivery
  cursor remained 3666.
- Contrary to the expected deferral, the Codex TUI displayed input 3667 under
  `Messages to be submitted after next tool call`. The first repair was
  therefore insufficient and was not treated as complete.
- After the turn ended, the verification input did run and produced the exact
  requested marker once as an assistant response, but the active-turn
  injection itself remained a confirmed defect.

### Confirmed second defect

- Kevin's resumed Codex transcript is 276,346,204 bytes.
- For the tested turn, `task_started` was at byte offset 275,665,220 and the
  first verification-marker record was at offset 276,344,102: a distance of
  678,882 bytes.
- `ChannelTranscriptRepository.read_tail_text` reads at most 524,288 bytes.
  Thus the active-turn parser could no longer see the opening lifecycle event
  once one turn emitted more than the diagnostic-tail limit, and it returned
  inactive before the turn completed.

### Second repair

- Transcript scope initialization now records the exact end offset of an
  existing resumed-session transcript before the CLI process starts.
- Active-turn detection incrementally reads complete JSONL records appended
  after that boundary and persists the last lifecycle state between polls.
- The state closes only when a later lifecycle/assistant record closes the
  turn. Output volume no longer determines whether the opening event remains
  visible.
- The implementation is runtime/session scoped and contains no Kevin, agent,
  provider, or model exception.

### Second-repair verification

- A regression test appends `task_started`, more than 600 KiB of reasoning,
  and `task_complete` to a resumed transcript. The incremental reader retains
  the start state beyond the old 512 KiB boundary and later observes the
  completion.
- Focused transcript/channel/architecture tests: 497 passed, 122 skipped.
- Full repository tests:
  - unit: 1,105 passed, 44 skipped
  - router: 902 passed
  - channel: 372 passed, 80 skipped
  - runtime: 244 passed, 12 skipped
  - total: 2,623 passed, 136 skipped
- Ruff and diff checks passed.

### Second-repair deployment and live result

- Commit `a6b8c58` was pushed to `nightly`.
- GitHub CI run `32443885914` succeeded.
- npm publish/tarball verification run `32443885966` succeeded.
- Published and installed Kevin version:
  `0.2.21-nightly.20260821-033715.a6b8c58`; registry `gitHead` is
  `a6b8c58c6a83f3208158bcac27f853797cf61df7`.
- After restart, runtime input 3674 was admitted during the active turn that
  ran from `2026-08-21T03:43:32.213Z` to `03:43:56.729Z`.
- Six seconds after admission, the input file contained 3674, the delivery
  cursor still read 3673, and the TUI contained zero matches for the marker,
  `pending_ids=3674`, or `Messages to be submitted`.
- After the active turn completed, a new turn ran from `03:43:57.378Z` to
  `03:44:01.320Z`. The assistant emitted the exact verification marker once,
  the delivery cursor advanced to 3674, and the visible TUI contained zero
  internal queue labels.

### Kevin MCP credential repair observed during deployment

- The restarted process contained `AINET_API_KEY`, and the same credential
  returned HTTP 200 from `/api/v1/agents/me`; environment projection was not
  missing.
- Kevin's pre-existing `ai-net-http` Codex entry contained a different static
  bearer token, while the managed `ai-net` entry matched the current
  credential. That static mismatch produced the observed 401.
- `ai-net-http` was replaced through `codex mcp` with
  `bearer_token_env_var = "AINET_API_KEY"` and no static authorization header.
- After the final restart, the captured TUI contained zero `ai-net-http`
  startup failures and zero `invalid agent API key` messages.
