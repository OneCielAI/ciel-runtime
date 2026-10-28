---
type: Diagnostic
title: Gateway errors bypass strict replay recognition
description: Current-session SSE failure was committed as unrecognized with zero retries; official gateway metadata format is not accepted by the parser.
timestamp: 2026-10-28
tags: [sse, retry, gateway]
---
# Confirmed live evidence

9524 at 04:33:34 local: operation `eaaeee065d324043b322c9e279f87445`, `unrecognized_frame`, attempts=0, opens=1. No message_start, text or tool output. The 777-character error JSON has `error` plus two unknown top-level fields. Error fields: string code/message/type, type=server_error, message length=170. The strict parser rejects ANY additional top-level key beyond type/error/request_id, sufficient to reject this frame irrespective of the other values.

The watcher ended at 04:35:36 with reason=sse_error_captured, 24 retained lines, 0 rotations. [Receipt](evidence/watch-status.json), [sanitized lines](evidence/kept-lines.txt). It is no longer running.

# Official source contract (not deployed-version proof)

Read-only official repository `OneCiel/ciel-ai-router`, main on 2026-10-28:
- `open-sse/utils/error.ts`, blob 9d80dc159fd6ab07ccd79f38bf1b6b62a05c817a, lines 578-590: errorResponseWithComboDiagnostics adds diagnostics and optionally recovery_hint.
- Same file lines 437-470 and 542-563: closed recovery actions and structured diagnostics projection.
- `open-sse/utils/earlyStreamKeepalive.ts`, blob 063ea0618b7bc069a0d9566f6a85c7021c26e7f7, lines 374-390: delayed non-SSE error body becomes event:error, preserving the full JSON including these siblings.

These source facts establish a supported gateway format. They do NOT establish that the two fields in the captured live frame have these exact names, nor that the live gateway runs this commit. No raw live error body was retained. The service package directory is access-denied; no privilege escalation or access changes attempted. Accessible user database has zero call_logs and zero request_detail_logs.

# Separate connection reset

9505 at 04:26:30: two URLError/WinError10054 errors; the log reports endpoint http://127.0.0.1:20128/v1/messages. This field is computed before gateway reselection and may be stale on a reopen, so the failing socket destination is not proven. upstream_retry wraps them in RuntimeError, then the local route responds HTTP500. This is not evidence that an HTTP500 response came from CielAiRouter. The reset origin and whether the upstream accepted work remain unknown. Nearby 503 retry operations cannot be conclusively paired to these transport lines (no operation_id on transport line).

# Safety boundary

No live router/session restart, gateway config change, privilege change or real generation probe. Do not blanket-retry connection resets or unknown/mixed/output-bearing streams. Compatibility tests must identify themselves as official-contract synthetic fixtures, not exact live replay.

# Transport investigation correction

Read-only CPython 3.14.2 source inspection: urllib/request.py lines 1319-1328 wraps OSError from h.request as URLError; h.getresponse failures are not wrapped. This locates the failure in connect/header/body sending, but socket.sendall explicitly cannot report how many bytes were sent on failure. No evidence establishes zero submission. No reset-retry policy change is justified by these logs.

The CLI's 5xx suffix takes the displayed inference-gateway host from its own ANTHROPIC_BASE_URL. Thus screenshot #9's port 8966 is a CLI-to-runtime address, not evidence of the runtime-to-gateway socket destination. This host currently has no 8966 listener/instance, so the local 9505 records match the signature/time but are NOT proven to be the exact screenshot request. The earlier direct identification of #9 as 9505 was too strong and is withdrawn.

# Compatibility change under verification

Known metadata is accepted only after validating the published nested schemas; unknown siblings, generation fields, malformed shapes, duplicate keys and nonfinite values remain rejected. Retry hints do not override terminal auth/quota classification or output/finite-budget boundaries. Diagnostics add only the two published field names/types, never metadata contents. This is a fix for the official contract, not proof of the unidentified live siblings.

Installed baseline contract suite: 8 tests, 3 failures and 1 error. Source contract suite: 8 tests passed. Initial fixture syntax errors are retained separately as harness failures, not product results.

# Real CLI comparison

Owned fake gateway, actual Claude Code 2.1.312, native retries disabled, fresh HOME/config/free ports, egress sentinel:
- Installed baseline 679245f: [result](evidence/scripts/ui-installed-045651/result.json), [screenshot](evidence/scripts/ui-installed-045651/01-outcome.png): one main request, visible API error, no retry.
- Source fix: [result](evidence/scripts/ui-source-045757/result.json), [screenshot](evidence/scripts/ui-source-045757/01-outcome.png): two main requests of equal payload length, late SSE retry recorded, LATE-SSE-RETRY-SUCCEEDED on screen, no native fallback.
- Both runs 7/7 checks. Protected live routers retained PID18536/PID30608 and fingerprint122e12595997f09d.
- Final focused retry/diagnostic suite: 57 tests passed (final-focused-tests.txt). OKF strict validator conformant, no issues.

# Scope of result

This proves official-contract compatibility and synthetic CLI recovery. Exact live sibling names remain unverified. The live reset and actual upstream504 source are not fixed by this change. No claim that recurring production errors are resolved.
