# Change log

## 2026-11-10
- Replaced session-local best-effort fill with a serialized event-driven host pump and per-priority refill.
- Removed the device-wide reviewer singleton and added reviewer retirement after receipt consumption.
- Added host-owned assignment, receipt, handoff, recovery, approval-generation and bounded-archive coordination state outside the legacy lease schema.
- Added mandatory completion/handoff exit checks and host-to-fork recovery without main prompt injection.
- Added bounded model status projections, legacy orphan repair, mixed-build ledger compatibility and workspace salvage/rebind contracts.
- Added the phase-preserving Work `progress` field and retained `note` as approved scope.
- Focused source verification reached 350 tests plus 144 subtests; isolated repository groups also passed: unit 3,641 (52 skipped), router 1,552, channel 607 (80 skipped), runtime 458 (19 skipped).
- Owned source scheduler proof passed: four distinct native forks filled P1=2/P2=1/P3=1, marker overlap 46.4 s, a new P1 lease/agent started automatically after slot release, main-model rows held no fork canary, and desktop/mobile captures showed four confirmed cards.
- Owned restart run source-7 proved the same session, lease, generation, native agent ID and transcript resumed after terminating only the probe-owned tree; the post-restart marker appeared and main-model rows held no recovery/canary content. The probe's screenshot-only tail could not attach to the detached resumed console, so it exited nonzero after recording all restart acceptance facts.
- Released beta `0.2.51-beta.20261111-055322.8e08b97`: CI/publish passed; registry gitHead, SHA-512 and 521 source files matched; latest/nightly stayed unchanged. Extracted a 575-file immutable snapshot and installed the exact archive locally; global and snapshot files all matched, and no live process was restarted.
