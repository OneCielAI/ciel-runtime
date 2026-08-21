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

## WING evidence

- The active WING launch state identifies the affected process as
  `mode=codex-router`, model `deepseek-v4-pro:0813`.
- WING's workspace-local `runtime-inputs.jsonl` contains both screenshot IDs:
  - `1217`: the exact `net.ai-net.connected` event from Dave.
  - `1218`: the exact `net.ai-net.room.activity` event, including room,
    message, author, and instruction data.
- This proves SSE receive and durable storage succeeded for those two events;
  the missing terminal body was the wake formatter's presentation policy.

## Runtime-specific correction

- Kept `format_llm_delivery_wake_prompt` unchanged as the short, shared
  router marker.
- Added a visible wake formatter whose first line remains
  `[ciel-wake] pending_ids=...` and whose following text is the existing exact
  LLM batch projection.
- Enabled that visible formatter only from the Codex launch path. Claude and
  AGY retain the existing marker-only behavior; this preserves Claude's
  separately verified terminal/turn semantics instead of assuming every CLI
  handles wake input identically.
- Codex still submits the combined text as one bracketed-paste turn. The router
  recognizes the leading marker, removes the entire terminal-originated turn,
  and injects the durable queued body once. The model therefore does not
  receive a duplicate event.
- For visible wakes, correlation IDs are now read only from the trusted first
  marker line. Untrusted event text such as `id=999` cannot claim another
  queued message.

## Memory-path cross-check

- WING's workspace state records:
  - workspace: `C:\Users\daniel.yun.WING`
  - root: `.ciel/memory`
  - index: `index.md`
  - index address: `C:\Users\daniel.yun.WING\.ciel\memory\index.md`
- That exact file exists. The malformed
  `C:\Users\daniel.yun.WING.ciel\memory\index.md` does not exist.
- The latest Codex transcript contains the correctly escaped wire value
  `C:\\Users\\daniel.yun.WING\\.ciel\\memory\\index.md`. Therefore the
  malformed address was not produced by runtime path composition or prompt
  injection.

## Verification before deployment

- Focused suites:
  - channel message prompt: 15 passed.
  - wake claim repository: 5 passed.
  - pending poll: 6 passed.
  - channel bridge: 206 passed, 80 skipped.
  - Codex runtime: 69 passed, 12 skipped.
- Full `npm test`:
  - unit: 1,112 passed, 44 skipped.
  - router: 902 passed.
  - channel: 377 passed, 80 skipped.
  - runtime: 247 passed, 12 skipped.
- `npm run lint`: passed.
- `npm run check:docs`: passed.
- `npm pack --dry-run --json`: package generated, 385 entries.

## Deployment

- Code commit: `72a2474dedbc9048f4605486d29840d5000f7ca3`.
- Pushed to `origin/nightly`.
- GitHub CI run `32470113497`: passed all quality, minimum-Python,
  unit, router, channel, and runtime jobs.
- npm publish run `32470113566`: passed package tests, publish, and tarball
  verification.
- Published and installed on WING:
  `0.2.22-nightly.20260821-095648.72a2474`.
- npm registry `gitHead`:
  `72a2474dedbc9048f4605486d29840d5000f7ca3`.

## Installed-package verification

The WING-installed package was executed against its actual durable record
`1218`:

- record kind: `external_event`.
- raw SHA-256:
  `3f98b234c39a32373eb310dff8f022efef8ce789056ade3cb47455452bf406b5`.
- visible first line: `[ciel-wake] pending_ids=1218`.
- exact raw occurrence in visible prompt: one.
- raw occurrence in shared short prompt: zero.
- parsed correlation IDs after appending hostile `id=999` text: `{1218}`.
- wake recognized: true.
- complete terminal wake turn removed before router injection: true.
- installed Codex launch enables body display: true.
- installed Claude launch enables body display: false.

The already-running WING TUI process loaded the previous package before the
global npm replacement. It was deliberately not killed over SSH because that
would terminate the user's attached interactive session. A newly launched
Codex session loads the verified installed package.
