# Change log

## 2026-11-12
- [Duplicate side wake](/duplicate-side-wake.md): rejected `c5c992ae` product restore (the two entries below are kept as rejected history); final bytes remain `eab3ad33`/`9b955068`/`542ff492`; adversarial evidence is the byte-identical rerun on `9b955068` (`results.jsonl` `0c9607f8…`). Corrected final-engine-run attribution, bounded notice tombstone residuals (`/clear`+reload and 200-newer eviction), terminalize-throw qualification, and archived exact reviewer probe `1efd9ab8…` with three final modes.
- [Duplicate side wake](/duplicate-side-wake.md): freeze `eab3ad33` superseded by `c5c992ae` (terminal-record restore at `/clear`, journal corrections, notice cell); adversarial evidence hashes corrected to the final-draft run (`results.jsonl` `124f64a9…`).
- [Duplicate side wake](/duplicate-side-wake.md): `/clear` re-assert also restores local terminal records; the notice residual is now exactly four conditions (labelled test); legacy-exhaustion wording corrected for fail-closed (`exhaust` probe: `submits: []`); per-group `~/.claude.json` hashes.
- [Duplicate side wake](/duplicate-side-wake.md): freeze `cb2f0172` rejected (fail-closed, terminal expiry, `/clear` re-claim) and `92a1347a` superseded. Final: delivery identity is key + exact `attempt_ref`; bounded terminal records in the module and in `$.state` (200 pending, 200 terminal, oldest terminal evicted first); the same identity never re-issues automatically, while a person-confirmed new attempt may; the timer callback checks its own timer; a refused `$.clock.after` settles `uncertain`. Review-side-wake's adversarial probes pass on the final draft.
- [Duplicate side wake](/duplicate-side-wake.md): an unclaimable prompt key now fails closed (no raw submit; `uncertain` at the idle window); each idle-window callback checks its own arming, entry and generation; `/clear` (measured as `classic.SessionStart` with `$.state` reset) re-claims unresolved raw keys before a reload can see the registry empty (harness-only for the re-claim).
- [Duplicate side wake](/duplicate-side-wake.md): per-key idle prompts replace the FIFO. Each key is issued at once; a `$.state` `promptPending` registry (CAS claim, generation-fenced clear, 200 rows) stops a duplicate key from any module instance and survives a hot reload; a 5 s idle-anchored window releases an unresolved key as `uncertain`. Liveness table (review, side wake, retry, notice × finite, anomaly, restart, reload) is tested cell by cell. Owned probes `prompt-registry-35a040a8` and `prompt-registry-e2bed500`; the `prompt-fifo-371c36de` "distinct-promise" label is corrected.

## 2026-11-11
- [Duplicate side wake](/duplicate-side-wake.md):
  - **Stop.** Stop joins the wake election, records `queued` on the same turn's next `turn.step` (owned probes `stopblock-838268bd`/`d9595ffa`), and adds no side-wake block while `stop_hook_active`.
  - **Notices.** The uncertainty and ACK-retry notices fire once each, and the pane retry asks for confirmation.
  - **Old routers.** Their attempts are fenced across module instances by a `$.state` compare-and-set (owned probe `state-fence-df88e27b`).
  - **Reconcile.** The host's transcript reconcile was removed, because `$.session.messages()` rows carry no uuid.
  - **Prompt FIFO (superseded).** One shared FIFO serialized every idle-path plugin prompt. Reverted after review: a never-settling submit hid every later wake.
  - **Review fixes.** A superseded Stop presents nothing (A). A record that lands inside a Stop is not called uncertain (B). Knowledge of another attempt never hides uncertainty (C). `turn.complete` drops unproven presentations. `stop_hook_active` gates only the two notices. Fence exhaustion delivers (at-least-once).
- Group runs on the moving shared tree, with per-file manifests. SSE-side files were hash-stable. Router: 1530 OK. Unit: 3740 tests, 4 failures, all in `test_work_mod_real_backend`, whose backend files changed during the run. The live router stayed healthy (PID 82844).
- Non-ASCII owned probes over stdio, plain-JSON HTTP and SSE-framed (`text/event-stream`, chunked) HTTP: the stub's stated count is the saved text's UTF-16 `.length` in all 16 stub cases; SHA-256 equal in 24/24; no `mcpText` change.
- Owned probes (Claude Code 2.1.321): large MCP results reach `$.mcp.call` as a persisted-file stub; named native forks stay idle, so side forks stay unnamed.
- Host reads follow only this session's exact persisted tool-result file; retained thread bounded by measured caps and main's remaining context.
- Message-less events deliver `not_applicable`; quarantines are terminal; a failed fork proof is denied.
- Replaced the registered, filtered side actor with one unnamed native `fork` per SSE event.
- Retired active side model/context settings; old stored values are ignored.
- Made the side restriction explicitly prompt-only while keeping host-only Work commit and outbox send.
- Changed Walkie prefetch to a canonical root read plus paged `threads.read`, with exact target and lineage validation.
- Added focused fail-closed and inheritance tests.
