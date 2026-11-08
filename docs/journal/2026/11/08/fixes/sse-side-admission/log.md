# Change log

## 2026-11-08

- Raised the shared side-intake contract from 16,000 to 32,000 characters and the pending-record budget to 256 KiB.
- Replaced external `/btw_with_mcp` command envelopes with exact side-only queue rows.
- Added unchanged raw-text delivery for identified permanent admission errors and an over-record-budget content-free notice.
- Added malformed-frame synthetic identity, operator-filter dead letters and rejected delivery ACKs.
- Added cursor, storage, deduplication, command-inertness and 32K capacity regression tests.
- Ran all four supported CI groups without changing any live-router PID.
