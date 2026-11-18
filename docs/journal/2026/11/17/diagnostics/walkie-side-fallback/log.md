# Log

## 2026-11-17

Fix:
- Fixed the bounded-thread gate in `side-intake.js`. The durable `compaction_waits` from `work_side_fail` receipts now opens `bound()` once 12 waits are spent.
- Fixed the terminal Client stage (`register.js` `sideFailureState`, `observatory.client.js` `stage()`): only a quarantine reads `failed`; retries read `retrying` and compaction waits read `waiting`.
- Tests:
  - New sequences in `tests/side_persisted_harness.mjs`.
  - `test_side_intake_mod`: 2 new tests.
  - `test_side_intake_mcp`: 1 new test.
  - New `tests/test_observatory_stage.py` with `observatory_stage_harness.mjs`.
  - New case in `observatory.test.ts`.
  - Fail-before on `git archive HEAD`: 4 Python failures and 1 mod failure. Pass-after: plugin test 104/0; Python 112/0 for the changed files and 610/0 for all 18 files that load the Work mod.
- Not committed; the main session commits.

Diagnosis (earlier the same day):
- Read-only diagnosis of the G:\ciel-walkie side failures, run in a separate fork as the user asked.
- Identified the session: install 71db761, router 9505, workspace `aae7db361c7f`, launched at 17:28:51.
- Scanned the side inbox (`pending/`, `receipts/`, `sources/`), the main transcript and the fork transcripts. Scripts and outputs are in `evidence/`.
- Findings: see [record.md](record.md) F1–F5.
  - The "failed" rows are context-budget failures, not model failures.
  - `turn_error` rows were retried on main's model today.
  - The bounded-thread fallback is gated on a mod compaction in the last 10 minutes, so oversized threads are quarantined after 12+6 tries and only then reach main (median 17.5 h).
  - 194 newer events have not been claimed.
- No code or state changed. Nothing committed; the main session commits.
