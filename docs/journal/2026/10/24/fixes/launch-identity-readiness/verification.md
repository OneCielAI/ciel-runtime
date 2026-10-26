---
type: Fix
title: Deliver on native launch readiness
description: Exact Claude published idle status opens initial SSE admission without waiting for a user turn or the 120-second compatibility deadline.
tags: [sse, readiness, claude, launch, compaction]
timestamp: 2026-10-24T18:40:00Z
---
# Request

User: start receiving external inputs as soon as the session is usable, rather than waiting for a user/model request.

# Confirmed observations

- Real Claude Code 2.1.310 publishes its process/socket/session before startup finishes. `--continue` first publishes a temporary session with no status, then the resumed session with `status=idle` (see [initial probe](evidence/run3/probe-result.json)).
- Fresh startup can publish idle without creating a transcript. Transcript presence cannot be a readiness requirement.
- The existing identity gate ignored these statuses and waited for a routed request or 120 seconds. Celly's measured 11:03:41–11:05:41 delay was that gate.

# Change

`runtime_compaction_binding.py` uses the same unique socket/cwd/PID filename/procStart/start-time-matched record for identity and readiness. Idle with a valid status timestamp establishes persisted `identity_ready`, separately from `request_observed`. Provisional records are reread on each admission check; established ready identities retain the existing 2-second publication cache.

- Atomic scope update compares epoch, session, socket, identity time and prior readiness/request-observed state before committing. New launches and retargets cannot inherit stale proof.
- Same peer becoming busy does not restart initial identity gating. A different peer or session clears readiness.
- Missing/malformed/old/future timestamps, busy/waiting, foreign or ambiguous records do not establish early readiness. The legacy 120-second fallback and first-request path remain.
- This only releases initial identity holding. Real compaction admission is still checked and remains fail-closed.
- No new toggle: the existing external-event input operation becomes readiness-driven automatically.

# Execution verification

[Summary](evidence/e2e-summary.json): actual CLI, fake loopback upstream, production binding + authenticated socket client, no post-launch user input; all `request_observed_before_send=false`.

| Run | Launch to socket send | Launch to upstream request | Transcript result |
| --- | --- | --- | --- |
| Fresh | 1.20 s | 1.49 s | external user input then assistant end_turn |
| Explicit resume | 1.13 s | 1.37 s | same seeded session, assistant end_turn |
| Continue | 1.12 s | 1.37 s | resumed actual session, assistant end_turn |

Console text captures are under [fresh](evidence/e2e-fresh/), [resume](evidence/e2e-resume-fixed/), and [continue](evidence/e2e-continue/). Timings are isolated-run observations, not a live latency guarantee. Poll scheduling still applies.

Exact-file isolated runner clears CIEL_RUNTIME/CLAUDE/ANTHROPIC/CODEX/OPENAI inheritance and creates temp homes/configs. Python 3.13 and Python 3.14: 449 tests each, 42 skips ([3.13](evidence/tests-py313.txt), [3.14](evidence/tests-py314.txt)). Readiness, identity, binding, contention, pending poll/injection, control/checkpoint and architecture contracts included. Ruff and metadata checks passed.

# Probe corrections

- Initial `-p` seed was not eligible for interactive `--continue`; probe_seed.py now makes an interactive seed before testing --continue.
- Initial socket E2E set a non-default config dir but gave the socket client the default-home path. That run returned auth_key_unavailable. Successful runs use the same isolated `.claude` directory for both; production code was not changed for that fixture mistake.
- A regression fixture's millisecond startedAt was rounded after statusUpdatedAt, requiring the same 1-second rounding tolerance already used by the launch identity validator.

# Boundaries

No Celly or .14 files, sessions, queue or cursor were modified. Existing live processes are not reloaded. Packaging and local installation results are recorded separately when available.

# Release

- Code commit `4af30c7995732ec6c0bbf8b7afe90c965f79bc87` pushed to nightly; CI 40121737737 and Publish 40121737726 both succeeded.
- Registry nightly `0.2.51-nightly.20261024-184918.4af30c7` has the exact code gitHead.
- Local deployment used the exact tarball, `npm install -g`, and 454 per-file atomic replacements; no `install.ps1` and no live restart. See [deployment](evidence/deployment.json) and [output](evidence/deploy-output.txt).
- `runtime_compaction_binding.py` hashes match git, registry tarball, local snapshot and global npm.
- Installed-path Python 3.14 focused run: 188 tests OK ([output](evidence/installed-tests.txt)).
- Router PIDs unchanged: 9385=6032, 9464=54764, 9489=67368, 9505=49288, 9524=6196. Celly remains separate and was not installed/restarted by this task.
- New behavior requires a new launch; old running Python processes keep their loaded code.
