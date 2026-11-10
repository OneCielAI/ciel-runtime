# Change log

## 2026-11-10
- Published and registry-verified beta `0.2.51-beta.20261110-042134.0f1e47b`; installed snapshot and global package match on 573 files.
- Reran all 50 Work mod gate tests against the installed snapshot; all pass.
- Added a deferred-spawn regression scenario; all 50 Work mod gate tests pass.
- Changed restart recovery authorization from a call-scoped set to an exact one-shot lease/name map.

## 2026-11-09
- Read Prody's session transcript, canonical Work board, and parallel ledger without modifying the machine.
- Correlated three exact SendMessage refusals with their persisted running leases and agent names.
