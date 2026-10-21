---
type: Diagnostic
title: Native compaction checkpoint admission reproduction
description: Isolated fixture evidence for four checkpoint admission failure classes on nightly-20261020, with controls.
tags: [compaction, checkpoint, codex, claude, admission, reproduction]
timestamp: 2026-10-21T00:00:00Z
---

# Question

Which checkpoint admission failure classes exist on the current tree, reproduced
with the existing `test_runtime_compaction_control` harness (confirmation
timeout 10 s) and tmp-only transcripts? This is research/test evidence for a
pending sequential fix, not a verified fix and not a diagnosis of the actual
Ara host.

# Scope

* Tree: `nightly-20261020` working tree at commit `ed80c84`.
* Production code read, not modified:
  `ciel_runtime_support/runtime_compaction_control.py`,
  `ciel_runtime_support/runtime_compaction_binding.py`,
  `ciel_runtime_support/channel_transcript_repository.py`.
* New file only: `tests/test_runtime_compaction_checkpoint_evidence.py`.
* No live Runtime state, transcript, cursor, queue or remote host was read or
  written. Every fixture lives under a temporary directory.

# Confirmed by source

| Location | Behavior |
|---|---|
| `runtime_compaction_control.py:79-99` | `native_checkpoint_from_transcript` seeks to `size - 512 KiB`, reads that window, and when `size > max_bytes` discards everything before the first newline. |
| `runtime_compaction_control.py:202-206` | Only `dispatching`, `awaiting_checkpoint` and `compacting` have a deadline; expiry sets `blocked` / `checkpoint_unconfirmed`. |
| `runtime_compaction_control.py:286-298` | `shrunk` requires `raw_tokens < context_limit` **and** `raw_tokens < baseline_tokens`; the early return at `:251-252` skips the shrink check when token counts are zero. |
| `runtime_compaction_control.py:306-312` | `_not_shrunk` replaces the baseline and retries, but `awaiting_shrink` itself has no deadline. |
| `runtime_compaction_binding.py:311` | Codex `_transcript_session` parses `stream.readline(64 * 1024)`. |
| `channel_transcript_repository.py:428` | Codex `_codex_session_identity` also parses `readline(64 * 1024)`. |
| `runtime_compaction_binding.py:407-410` | When an active cycle has no capacity bound, the observation limit becomes the stored `baseline_tokens`. |

# Observed failure classes

Exact output: [observations.txt](evidence/observations.txt); exact-file test
output: [focused-tests.txt](evidence/focused-tests.txt).

## 1. A ~600 KiB compacted line is not checkpoint proof

* Parser: a 614646-byte `compact_boundary` line at EOF -> `found = False`;
  the identical record without padding -> `found = True`.
* Controller (codex): a 614610-byte `compacted` record while
  `awaiting_checkpoint` -> after the 10 s deadline, `phase = blocked`,
  `failure = checkpoint_unconfirmed`, admission paused.
* Cause observed in code: the 512 KiB window starts inside the record, and
  `partition(b"\n")[2]` discards the partial first line.

## 2. A checkpoint pushed past the tail window is not checkpoint proof

* Parser: a small valid `compacted` record followed by 600 KiB of later
  records -> `found = False` (614695-byte file). The same shape with a 400 KiB
  tail still parses in the control.
* Controller: after the same fixtures, the 10 s deadline yields `phase =
  blocked`, `failure = checkpoint_unconfirmed`.

## 3. A persisted `awaiting_shrink` phase does not auto-release

* Codex fixture: baseline 1200, limit 1000; a newer small checkpoint is
  persisted and observed, followed by a native `token_count` record with
  `last_token_usage.input_tokens = 120`. `native_input_tokens` confirms 120.
  After reconstructing the controller, admission polls at +1 s, +11 s, and
  +100000 s remain `awaiting_shrink` / paused. A real raw request of the same
  size (120) immediately verifies. Thus persisted native usage is not consumed
  as shrink proof by polling; this is confirmed current policy, not proof that
  accepting native usage would be safe in an arbitrary session.
* After a client-started compaction with persisted `baseline_tokens = 100`, a
  small checkpoint is observed and the phase is persisted as `awaiting_shrink`.
* A new controller instance reads the same state. A post-checkpoint request of
  120 tokens (below the 1000-token context limit, above the stored baseline)
  leaves `phase = awaiting_shrink`, admission paused; after `+100000` s it is
  still `awaiting_shrink` and paused.
* Control: the same persisted state releases to `verified` when the request is
  90 tokens, below the baseline.
* Second shape: a queued manual compact starts with `baseline_tokens = 0` and
  `context_limit = 0`. The binding substitutes the zero baseline when capacity
  is unknown, and `observe_request` returns before the shrink check, so a
  120-token request is ignored and the persisted `awaiting_shrink` remains
  paused after `+100000` s.
* Cause observed in code: no deadline covers `awaiting_shrink`, and the shrink
  gate depends on the stored baseline, not on the observed conversation size.

## 4. A codex `session_meta` line over 64 KiB is unresolved

* Fixture: 71847-byte `session_meta` header naming thread `thread`; the full
  line parses to that id.
* `RuntimeCompactionBinding._transcript_session` -> `''`;
  `ChannelTranscriptRepository._codex_session_identity` -> `(None, '', '')`.
* Bound scope still names `thread`, but `transcript_path() = None`, so the
  checkpoint reader is never given a path. A native compact request leaves the
  phase `compacting`; after the confirmation deadline it is `blocked` /
  `checkpoint_unconfirmed`.
* Control: the identical rollout with a small `session_meta` resolves the path
  and yields a checkpoint.

# Desired behavior (not verified here)

For every class above, the desired outcome after the sequential minimal fix is
an observed checkpoint followed by `verified` with the smaller request's
`next_raw_tokens` (120 in these fixtures), never `blocked` /
`checkpoint_unconfirmed` and never an indefinite `awaiting_shrink`. These tests
assert the current failure so the fix can be observed as a deliberate test
change; they do not mark any fix verified.

# Evidence

* [focused-tests.txt](evidence/focused-tests.txt) — exact-file isolated run of
  `test_runtime_compaction_checkpoint_evidence.py` plus
  `test_runtime_compaction_control.py`: 32 tests, OK, 1.077 s (final rerun,
  including the persisted-native-usage case).
* [observations.txt](evidence/observations.txt) — printed values for all four
  classes and controls.
* [focused.py](evidence/scripts/focused.py) — runner adapted from the current
  compaction helper (scrubs inherited runtime/provider env, temp config root,
  free loopback port, exact file list).
* [observe_failures.py](evidence/scripts/observe_failures.py) — tmp-only probe
  that prints the observation values.
* [source-sha256.txt](evidence/source-sha256.txt) — digests of the new test and
  the three production files under observation.
* [okf-validate.txt](evidence/okf-validate.txt) — strict OKF v0.1 conformance
  result (conformant, no issues).

# Limitations

* Fixture-level reproduction with synthetic transcripts; no Claude Code or
  Codex CLI was executed and no upstream provider was contacted.
* The controller fixtures use the existing 10 s confirmation timeout to reach
  the deadline quickly; production policy stays at 1200 s.
* No claim is made that any one class is the actual cause on the Ara host.
  Class 4 is additionally dependent on the launcher binding an explicit codex
  session id; its scope-level effect is shown only through the binding.
* The second `awaiting_shrink` shape models the binding's own substitution
  (`active and not limit -> baseline_tokens`); it does not exercise the router
  or a live config.

# Citations

[1] `ciel_runtime_support/runtime_compaction_control.py`
[2] `ciel_runtime_support/runtime_compaction_binding.py`
[3] `ciel_runtime_support/channel_transcript_repository.py`
[4] `tests/test_runtime_compaction_checkpoint_evidence.py`
