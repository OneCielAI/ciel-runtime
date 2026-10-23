---
type: Fix
title: Recover from an unreadable compaction admission state
description: Quarantine an unreadable state file once so the next measured request can start a new cycle instead of blocking external input forever.
tags: [compaction, channel, sse, recovery]
timestamp: 2026-10-23
---

# Symptom

`compaction-admission.json` is read before every external delivery. Any read
error became a permanent pause: `runtime_compaction_control admission=paused
reason=state_unavailable error=OSError`, and no client request could repair it
because `observe_request` also raised `OSError: compaction admission state is
unreadable`.

Measured on a local nightly instance (9505, workspace G:/ciel-Walkie):
712-byte file with all 712 bytes zero, last written 22:57:54; after the
23:07:21 reboot, 56,426 pause lines, 139 `context_recovery` deferrals, zero
channel injections, and five manual turns that did not recover delivery.

# Change

- `ciel_runtime_support/secure_json_repository.py`: new `quarantine(purpose)`
  moves an unreadable file to `<name>.corrupt-<time_ns>`, preserving bytes for
  diagnosis. A failed move leaves the file in place, so callers stay
  fail-closed.
- `ciel_runtime_support/runtime_compaction_control.py`: the composed loader
  quarantines on `None` and returns an empty state. The next observed request
  then starts a fresh cycle from its own measured tokens; a full or native
  request still pauses admission until shrink is proven.

# Safety boundary

Liveness now rests on measured evidence rather than an unreadable file, but the
gate still closes for pressure and native compactions. The exact cause of the
zero-filled file is not established; an unclean shutdown is only correlated.

# Verification

- [Focused tests](evidence/focused-tests.txt): 65 tests, OK, including the new
  `test_unreadable_state_is_quarantined_and_a_new_cycle_can_verify`.
- [Change patch](evidence/change.patch)
