# Change log

## 2026-11-06

- Frozen release export passed unit3319/50skip, router1529, channel604/80skip and runtime458/19skip. Actual native run5369022f used that exported product tree. Final profile labels,24menu rows and26ctl settings verified.
- Independently verified safeguard fixture collision: old in-process poller shares the replacement global scanner; deterministic real-poller10/10fail before,100/100pass after fixture cleanup. The frozen gate already includes this test-only correction. Original failure's exact interleaving remains unknown.
- Actual Python3.10 found two encoding-test compatibility defects; version-compatible mocks and a valid child environment fixed them,17focused tests passed on3.10/3.13/3.14. Full3.10unit gateb0naq1gmq passed3319/52skip on the final test-only corrected export; no publish yet.

- Final native production-router afaf7460 verifies full/recent × main/custom, exact frozen25item Work in all four arms, recent41.5233percent at a safe boundary, one primary send and no shadow side effects. Recorded compact assertions and inspected actual screenshots.
- Reproduced stale original handoff receipt (`requested` after board answer), synchronized durable display state without overwriting delivery, and verified native/browser round trip122334d3 shows `answered`.
- Retained failed unit gatebroo9p7wb (one safeguard assertion); separate router1528/channel604/runtime458 gates passed. Investigating the failure rather than declaring a clean release. Removed unused prototypes from package while preserving them in scratch.

- Revisited native-only context limitation: wired existing router-side parent API projection without editing native history. Full exact prefix, provider-counted recent tail, complete Work board and read-only four-profile comparison now implemented on routed path; direct gateway is explicitly unsupported. See routed-context.md.
- Native full/custom repeated30cc02b9 and recent/full-boardf5a10f49 passed; shadow tool-attack3a6ef010 made no external side effect. Routed production Claude handler full probe passed; recent full-handler probe running. New code under independent review and full gates, not released.

- Added heartbeat renewal, operator-owned source/receiver MCP reply bindings, explicit uncertain-reply retry and per-attempt send fencing. Actual native125second delayed side response passed with4heartbeats;23menu rows and25ctl settings verified.
- Added fail-closed single read reconnect for disconnected MCP; historical router-call cause remains unconfirmed.
- Authorized API KEYS - SSE read-only measurement attempted; remote404/400provided no usage/cache data. Full/recent modes remain unconnected, not falsely marked complete.

- Added priority-index crash repair, board-to-receipt reconciliation and acknowledgment-to-followup recovery tests.
- Verified bounded side advice reminder and goal-scoped child priority handling.
- Fixed stale native side pane and verified original/decision/action/reply/submitted fields in actual CLI capture27fefc5c.
- Verified all22Work menu descriptions and catalog-backed side model picker2486f38c.
- Actual packaged advice round trip62489e9f completed with one root, zero pending, two submitted fixture replies. No live recipient/cache claim.
- Full gatebozubhlwh passed on its snapshot; later deltas invalidated it as final-source proof.
- Reproduced and fixed Python3.13CP949capability-probe reader failure; browser advice now uses correlated status before one final reply.
- Final-source unit rerun exposed a Windows lock-byte initialization PermissionError (1/30reproduction). Removed the pre-lock write per documented lock-beyond-EOF behavior;60/60cross-process repetitions then passed. Full gatebh0yp4ix8 passed (unit3308, router1516, channel604, runtime458).
- Fixed pane receipt label and single-line log rendering; native rerun44ad8bbf verified.
- Native advice screenshot86c7cfd2 exposed two UI defects (receipt label, multiline log glyphs). The fixture also misclassified a post-handoff main request as side by message words; harness classification now uses the side system prompt.
