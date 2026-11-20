# Change log

## 2026-11-19

- Fixed null-workspace close-resolved crash with a failing regression test.
- Applied the user's decision: main reviews scope-changed results; approval_changed is read-only. Reports are retained; rework/resume need an approved goal.
- Reproduced P0 reservation failure on a temporary Andy state copy, then closed stale intents of ended leases and preserved exact unbound reservation replays. Both reported items reserved afterward.
- Broader Windows Work run: 687 tests, 2 failures and 2 errors, 10 skipped. Two errors were test mock signatures after _revalidate_review gained its decision argument; one failure expected the old scope-change report refusal. All three are updated and the MCP/review-pin modules reran with 49 tests, zero failures/errors. The remaining workflow test asserted an English phrase while the managed prompt is Korean; tracked with side fixture maintenance.
- Linux Python 3.11 and 3.10: seven changed Work modules, 216 tests each, zero failures/errors, 8 skipped each. The skips cover unavailable historical-beta source in the copied tree; mixed-version tests ran on Windows.
- New KOBE evidence also exposed the separate host recovery cap: see [host recovery projection](../host-recovery-projection/record.md).
- No installation or live post-install verification yet.
