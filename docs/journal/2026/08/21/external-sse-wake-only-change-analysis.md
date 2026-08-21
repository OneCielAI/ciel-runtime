# External SSE wake-only TTY change analysis

## Observation

- External SSE messages previously appeared with their full body in the active
  CLI terminal.
- Current routed sessions display only `[ciel-wake] pending_ids=...` in the
  terminal.

## Confirmed history

- Commit `66eb29c3d1ee1b90fbca84f27d18cb590b46ff4c` on 2026-08-18
  (`feat: restore router-backed chat injection`) changed
  `format_channel_llm_delivery_wake_prompt` from the full-body formatter to a
  dedicated wake-only formatter.
- That commit's tests explicitly changed from asserting that the external body
  is present in TTY bytes to asserting that it is absent and only message IDs
  are present.
- Commit `0208728356504baf432e85b442a646b2e9ff7359` later on 2026-08-18
  (`fix: prevent router wake loss behind tty entries`) shortened the marker to
  the current `[ciel-wake] pending_ids=...` form.

## Current delivery path

- External SSE receivers submit the exact decoded event through
  `RuntimeInputGateway.submit_external_event` without an `input_transport`
  override.
- `message_input_transport` treats a missing persisted transport as `router`.
- Routed CLI launches enable `wake_for_llm_delivery` whenever router mode is
  active.
- The terminal injection path therefore writes only the wake marker. On the
  subsequent routed model request, `inject_pending_channel_context` removes the
  marker and appends the full formatted external-event body to the LLM request.
- The exact event remains model input, but it is no longer rendered as full TTY
  input in this path.

## Conclusion

The wake-only terminal display is an implemented behavior change introduced on
2026-08-18, not an unexplained SSE decoding failure. No change to this delivery
policy was made during this analysis.
