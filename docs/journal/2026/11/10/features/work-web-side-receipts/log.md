# Change log

## 2026-11-10
- Added the owned Playwright probe `evidence/e2e_work_side_receipts.py`.
  - Real `WorkHttpController`, web login and file-backed `SideIntakeInbox`; no product code is patched or mocked.
  - Ran it against the source tree after the backend conformed to the top-level receipt contract; every browser assertion passed and the screenshots/readback were recorded.
- Added server-redacted receipt list/detail projections, `side.changed` invalidation, paged/filterable receipt-first Side UI, REST handoff parity and focused tests.
- Added duplicate-processing guards verified from the 121619 incident: decisions must be grounded in the current source message, ordinary committed P1 updates no longer wake main for duplicate work, and only actual side questions/advice receive coordinator follow-up events.
- Released commit `09f0c4c` to beta `0.2.51-beta.20261110-215246.09f0c4c`; CI, npm publish and registry/integrity verification passed while latest/nightly stayed unchanged.
- Installed the registry-verified snapshot locally, matched it to the global package on 573 files, and reran the owned browser proof against that snapshot: 38/38 checks and all seven screenshots passed. Running user sessions were not restarted.
