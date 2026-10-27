---
type: Verification
title: Work API Verification
description: Focused evidence for Work HTTP board operations, exact-session metadata, authentication and SSE invalidation.
tags: [work, verification]
timestamp: 2026-10-26T00:00:00Z
---

# Verified

- 62 focused tree/store/controller tests passed, including all-depth sibling ordering, CAS, no-op persistence, cross-process writes, read-only corrupt snapshots and SSE reconnect.
- 13 owned loopback HTTP tests passed against a production-style RouterHttpHandler/auth policy: valid admin token, wrong/bridge token 401, disabled external access 403, no mutation on rejection, complete CRUD, stale CAS and SSE.
- 360 isolated related tests passed (42 skipped), including architecture contracts and the final observer/controller code ([output](evidence/focused-tests-after-sse-race.txt)). An earlier 360-test run exposed an error-SSE initial-snapshot race; the fix captures the initial error cursor before publishing the first frame.
- 4 runtime observer tests passed: configured alias versus response model, exact scope/epoch correlation, 504 timeout and 404 HTTP error classification without credential body retention.

- A follow-up integrated run reached the HTTP idle-band refresh and kept live routers unchanged; initial native plan fixtures failed: first harness missed the path, then Windows slash/backslash spelling differed, and a diagnostic run proved the exact host path matched but a second Work status refresh was unavailable. Diagnostic run 4e1d8f54 proved exact path matching and the unavailable Work requery denial. The native plan-only exemption now bypasses that Work network requery while native host permissions still apply; all other writes continue to fail closed. Run 98dd03b1 then created the real native plan and displayed it in the approval dialog; that harness did not recognize the populated-plan dialog, so full-lifecycle proof is being rerun.

- Mod tests: 44 passed, including native Windows plan path slash/backslash equivalence without permitting other paths or parent traversal. Strict plugin validation passed (existing missing-icon warning only).

# Limits

No live router was restarted. The current user-provided error strings were not treated as correlated runtime observations; only an exact request/session/epoch observation can populate an error row.

# Final actual CLI verification

Owned Claude Code 2.1.310 run [424a4f4d](evidence/run-424a4f4d/result.json) passed all seven checks with one model prompt: native plan file was written while repository writes were denied; native approval and Work execution reached idle; HTTP mutation advanced the board; idle band reflected it; build metadata was returned. Both live-router PID/fingerprint records remained unchanged.

- [Native plan and original band PNG](evidence/run-424a4f4d/01-native-plan-and-before-api.png)
- [HTTP idle refresh PNG](evidence/run-424a4f4d/02-http-idle-refreshed.png)
- [Screenshot capture log](evidence/run-424a4f4d/screenshot-log.txt)

Earlier failures are retained, not counted as passes: fixture path extraction, Windows path spelling, unavailable Work requery, recognizing the populated-plan approval dialog, and one screenshot subprocess timeout. The final implementation permits only the exact engine-pinned native plan file independently of Work network availability; ordinary edits remain guarded. Diagnostic WORKPROBE output was removed.

Final focused suite: 360 tests run, 42 skipped, no failures ([output](evidence/final-focused-tests.txt)). Mod suite: 44 passed, zero failed ([output](evidence/mod-tests-final.txt)). No commit, Publish, installation or live-session restart performed.
