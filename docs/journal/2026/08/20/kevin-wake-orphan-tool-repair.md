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
