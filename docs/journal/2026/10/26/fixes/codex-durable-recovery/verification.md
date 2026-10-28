---
type: Verification Record
title: Codex durable recovery verification
description: Focused unit tests and a real isolated Codex app-server run verify the recovery boundaries.
tags: [codex, tests, evidence]
timestamp: 2026-10-26T00:00:00Z
---

# Results

* Focused isolated runner: `103 tests`, `OK`.
* Native harness: `typed_504_retry_success`, `native_start_ack_loss_no_replay`, and `native_start_crash_no_replay` all passed.
* The native harness used installed `codex-cli 0.158.0`, a fresh temporary environment, and a local Responses HTTP fixture. It did not contact a live router, provider, remote host, or managed process.

# Evidence

See `evidence/focused-tests.txt`, `evidence/native-e2e.json`, `evidence/native-e2e-log.txt`, and `evidence/native_e2e.py`.

# Limits

This is real native app-server execution with a controlled local upstream and injected ACK loss, not a live provider/network failure or desktop UI E2E. Unknown acceptance is intentionally not declared failed/replied and is never automatically resent. No install, nightly publication, commit, or push was performed by this work slice.
