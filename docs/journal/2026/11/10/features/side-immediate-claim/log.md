# Change log

## 2026-11-10
- Published beta `5c91ea8`: CI and publish succeeded, the registry SHA and integrity were verified, and latest/nightly were unchanged.
  - Installed it locally (snapshot = global, 573 files).
  - Re-ran the real-model E2E on the installed snapshot: idle 0.26 s and busy 0.44 s received→claim; both waits released; no handoff.
  - In one installed run the side model declined a wait that named a different source ("via Walkie").
- Real-model owned end-to-end run.
  - Idle main: the approval was claimed 0.77 s after receipt and the wait released; main was prompted 0.6 s after the resolve.
  - Busy main: the CI result was claimed 0.12 s after receipt and released while main ran a 75 s command; the notice was appended into the running turn.
  - No handoff or acknowledgment was needed. The live sessions were unchanged.
- Added `release_waits` to side decisions. The host releases only an unfinished wait of the same goal whose text is unchanged. A running fork is messaged; main gets an append when busy or a prompt when idle. Handoff wakes now use the same busy/idle choice (state `appended`).
- Fixed handoff activation for a child target (ciel-walkie n1235) and the completion-gate message.
- Cut the per-claim cost: settled handoffs are no longer resent, the pane refresh is single-flight and throttled, and quarantined events are indexed out of the claim window.
- Added the content-free session-socket wake frame and the draining claim pump.
- Measured Prody read-only: claims ran every ~30 s, because an empty claim was 361K characters in 46 pages. 231 events are quarantined.
- Owned engine probes ruled out socket, clock, MCP and spawn stalls.
