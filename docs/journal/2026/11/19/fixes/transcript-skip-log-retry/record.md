---
type: fix
title: Transcript skip WARN repeated on every failed-delivery retry
description: While transcript delivery failed, each retry re-read the same corrupt region from the unchanged cursor and logged the same transcript_record_skipped WARN again (Celly, 3012 times for one region in 3.3 hours). Each skip is now logged once per consumer and transcript until the cursor passes it or the transcript shrinks; the cursor and the delivered content are unchanged.
timestamp: 2026-11-19T22:00:00Z
tags: [transcript, delivery, logging, celly, hotfix]
---

# Problem

- Evidence from Celly's router log, collected by another agent (not re-measured here): the same `transcript_record_skipped` line (offset 190981688, length 4435, reason `nul_run`) appeared 3012 times in 3.3 hours. `transcript_delta_delivery_failed` appeared 345 times in that window.

# Cause (source, HEAD d216883, module blob f544324)

- `poll_once` saves the cursor only after `_post` succeeds. A failed post leaves the cursor where it was, so the next poll reads the same region again. This is intended: undelivered records must be retried. ✅
- `_read_complete_batch` calls `_skipped` for every NUL run or oversized line it meets. `_skipped` always logged. So every retry logged every skip in the batch again. ✅
- The only throttle in the module, `_report_error`, keeps a single last message for 30 s and is shared with delivery errors. It cannot remember several skip sites, and with a 30 s window it would still repeat about 400 times in 3.3 hours. It was not reused.
- The fail-before tests reproduce this: two skips and three failed polls give six WARNs. See [evidence/tests/fail-before-HEAD.txt](evidence/tests/fail-before-HEAD.txt).

# Fix (`ciel_runtime_support/transcript_delta_delivery.py`)

- `_skips_logged` keeps, per (consumer, transcript path), the offset and file size of the last read and the `(offset, length, reason)` of every skip already logged.
- `_skip_window` runs at the start of every `_read_complete_batch`.
  - It forgets skips before the read offset, because the cursor has passed them.
  - It forgets all of them when the read offset is earlier than the last read, or the file is smaller than the last read saw. This matches the module's own truncation rule (`size < offset`).
- `_skipped` logs a skip only if this consumer has not already logged that exact `(offset, length, reason)` for this transcript.

## Unchanged

- Cursor commit: still only after a successful post. The retried event has the same ID and content.
- What is skipped and where `end` lands: `_read_complete_batch` returns the same `_Batch` as before.
- A new skip, a skip with a different length or reason, another consumer, another transcript, and a skip found again after truncation are each still logged.
- `transcript_delta_delivery_failed` keeps its 30 s throttle.

# Verification

- Fail-before: the module at HEAD (blob f544324, same hash as `git rev-parse HEAD:…`) with the new test file: 12 tests, 3 failures. See [evidence/tests/fail-before-HEAD.txt](evidence/tests/fail-before-HEAD.txt).
- Pass-after: 6 transcript test files, 39 tests, 0 failures. See [evidence/tests/pass-after.txt](evidence/tests/pass-after.txt).
- Linux Docker, Python 3.11 and 3.10, network disabled and source read-only: the same 39 tests pass on each. See [evidence/tests/linux-pass-after.txt](evidence/tests/linux-pass-after.txt).
- `ruff check` on both changed files: clean.
- Not run: the full suite.

# Not covered

- Why Celly's delivery failed is not investigated here.
- If a transcript is truncated and grows past its old size between two reads, the change is not detected. The cursor rotation check has the same limit.
