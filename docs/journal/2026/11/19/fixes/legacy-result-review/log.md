# Change log

## 2026-11-19

- Reproduced the real authenticated legacy worker completion path into `submitted` with no coordination receipt, followed by the exact durable-receipt review refusal.
- Checked historical commit `57dbfb3` and corrected the native-idle hypothesis: result evidence itself was copied to `idle_proof`, so that field alone is not independent native idle evidence.
- Added `work_parallel_legacy_source.py`, a read-only source/provenance inspector with source-digest revalidation and explicit unknown historical facts. It never writes a run, receipt or verification claim, approves a result, or reruns work.
- Added 14 isolated tests covering unknown facts, immutable source pins, missing evidence, missing/stale/mismatched native history, fresh active contradictions, exact idle/terminal observations, archived/partial coordination refusal, retained bindings and the real report-path reproduction.
- Final helper-only command: `python .local/tmp/run_tests.py . tests/test_work_parallel_legacy_source.py`, with all inherited `CIEL_RUNTIME_*` removed, `CIEL_RUNTIME_TEST_ISOLATED=1` and a temporary config directory under TEMP. Result: `Ran 14 tests in 0.134s`, `OK`, `RAN 14 failures=0 errors=0 skipped=0`.
- Regression command: `python .local/tmp/run_tests.py . tests/test_work_parallel_legacy_source.py tests/test_work_parallel_receipts.py tests/test_work_parallel_finalization.py`, under the same isolation. Result: `Ran 124 tests in 237.570s`, `OK`, `RAN 124 failures=0 errors=0 skipped=0`. This run contained 13 helper tests and preceded addition of the real-report reproduction.
- `python -I -m py_compile` of both new Python files passed. Targeted `git diff --check` passed.
- Production integration deliberately remains unclaimed: schema-5 requires historical receipt/fingerprint/generation that do not exist. Main was sent precise finalization/publication/revalidation/approve/rework/restart integration boundaries; no fabricated receipt compatibility adapter was written.
