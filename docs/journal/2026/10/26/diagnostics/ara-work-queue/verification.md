---
type: Verification
title: ARA unfinished Work Stop verification
description: Real CLI Stop notices, runnable continuation and bounded no-progress behavior.
tags: [work, hooks, regression]
timestamp: 2026-10-27T03:15:00Z
---
# Confirmed

- Actual Claude Code 2.1.310 with isolated HOME/config/ports and loopback fixture: [all eight checks](evidence/run-baa8d455/result.json) pass. Waiting and blocked Work ends with one person-only notice, no model retry; releasing the wait produces two continuation requests, then a visible bounded no-progress pause.
- [Waiting/blocked screenshot](evidence/run-baa8d455/01-waiting-blocked-notice.png), [continuation/pause screenshot](evidence/run-baa8d455/02-actionable-push-then-bounded-pause.png).
- Real CLI exposed a separate defect absent from the mock kit: an internal work_status call re-enters tool.call, whose pre/post refresh increments generation and supersedes the original Stop refresh. The original returns null while cache is populated, so Stop silently returns. The own-mod work_status origin now bypasses recursive refresh; model work_status remains identity-pinned and refreshed.
- Initial actual runs failed; explicit auto_continue=true did not fix it (the default was already true). The next run fixed continuation but one screen assertion failed due to a wrapped line; normalizing screen whitespace resolved that harness assertion. Final run passes.
- 48 mod tests passed. A proposed direct $.mcp test was removed because the test handle does not expose $.mcp; real CLI is the re-entry regression evidence. It was not reported as passing.
- Sentinel blocked two external CONNECT attempts; no external model received the fixture. Live routers 9505/9524 PID/fingerprint remained unchanged. No managed host changes.

# Release

Installed-package verification and nightly/local switch passed; exact version and proof are recorded below.

# Published installation

Installed exact nightly 0.2.51-nightly.20261027-031627.0555b6c; native CLI installed checks pass. See release proof at `docs/journal/2026/10/26/releases/edward-ara-nightly/verification.md`. Local live sessions unchanged; .14 was not installed/restarted.
