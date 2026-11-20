# Log

## 2026-11-19

- Task from the team lead, based on Celly evidence from another agent: one skip line repeated 3012 times in 3.3 hours while delivery failed 345 times.
- Source read: a failed post keeps the cursor, the read is retried, and `_skipped` logs every time. `_report_error` is the only throttle in the module (one message, 30 s), so it does not fit several skip sites.
- Tests first, in `tests/test_transcript_corrupt_records.py`: failed-delivery retries, a new skip behind the same cursor, separate consumers and transcripts, a changed length or reason, and truncation. On the HEAD module, 3 of these fail with 6 WARNs where 2 or 3 are expected. The other two already pass and guard against over-suppression.
- First fix kept only the read offset. The truncation test failed with 1 WARN instead of 2: the cursor moved past the skip, the file was truncated before any read at the new offset, and the next read started at 0 again. Adding the file size seen at the last read fixed it.
- After: 39 tests in 6 transcript test files pass on Windows Python 3.11 and Linux Docker Python 3.11 and 3.10 (network disabled, source read-only), and ruff is clean. See [record.md](record.md) and [evidence/tests/](evidence/tests/).
