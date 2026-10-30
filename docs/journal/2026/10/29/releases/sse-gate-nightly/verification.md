---
type: Release Verification
title: SSE gate control nightly
description: Publication and installed validation of explicit SSE admission gate controls.
tags: [sse, gate, statusline, nightly]
timestamp: 2026-10-29
---
# Scope

User explicitly requested SSE gate slash on/off and visible status line, followed by local/nightly deployment. This release record is separate from the preceding compact/Work037c55f release. Publication and installed functionality verified. Protected-health verification limit is recorded below.

# Boundaries

Preserve live sessions, input ownership, uncertain-write handling and unrelated dirty files. No install.ps1 or remote deployment.

# Source acceptance

- Feature commit `907726516449764c26169a54c5d441cb0dd9be94` pushed to nightly and nightly-20261028.
-117focused tests pass on exact clean exported commit, independently of dirty provider changes.
- Main facade4980lines, no architecture budget violations. Ruff and staged whitespace checks pass.
- Owned actualCLI verifies latest local off→status→on responses, persistent mode and visible statusline, with one baseline upstream request only and0foregroundactivations.
- CI40987470637and Publish40987470633passed. Exact registry package verified; installed actualCLI verification passed.

# Control boundary

Default on. Off bypasses known compact recovery admission only; identity/handoff/unreadable state/unknown safety holds, inflight ownership and uncertain-write protections remain. Queue/cursors/compact evidence are not cleared or forged. Statusline shows mode plus held/bypassed/open/unknown and reason; stale observations become unknown. Routed commands are local; direct-native sessions that bypass routing do not gain this handler.

# Exact default-on recovery proof

Owned actualCLI against the clean9077265source retains the previous compact recovery behavior: terminal failure→boundedretry→queued SSE delivered exactlyonce→newcompactverified,4compact requests including native follow-up,0foregroundactivations/1318samples, live identities unchanged. [Result](evidence/default-on-cli/result.json); [verified screenshot](evidence/default-on-cli/03-sse-retriggered-compact-success.png). No completion/evidence/queue reset was used.

# Published package

- Version `0.2.51-nightly.20261030-015514.9077265`; exact gitHead/nightly tag verified.
- SHA256 `fdd88e1b0c87232845416670a99ebfa32901a6406ca203f122b959740bc80418`; registry SHA1/SHA512verified.
-517files compared:507identical,8CRLF-only,2explicitly checked version stamps; none missing from commit and no unexplained changes.
- Publish suite: unit3017(skipped59), router1486, channel582(skipped80), runtime455(skipped19); all pass on isolated GitHub runner. No full groups run on protected live Windows account.

# Installed harness correction

Initial snapshot-pinned run executed117tests with2failures, both `ModuleNotFoundError` from fixtures launching `python -c` in a separately exported test ROOT without installed module search path. The parent process imports were correctly installed. Original failures retained under `evidence/harness-initial-installed-tests.*`. Runner pins `PYTHONPATH` for those subprocesses to the same installed snapshot. The next run exposed an unrelated `Temp/org.py` imported from fixture ROOT by Python; `PYTHONSAFEPATH=1` excludes that foreign cwd. Both intermediate outputs retained; no product/test assertion changed. Final installed117/117pass.

Installed owned real-pressure cycle separately verifies off preserves the original request and unverified phase; re-enabling restores the unresolved hold. [Proof](evidence/installed-gate-contract.json).

# Installed proof and local deployment

- Fresh snapshot464files exact, no extras/mismatch; global npm install517files exact, scripts disabled.
- Installed117/117focused tests pass with module imports/subprocesses pinned to snapshot, safe cwd policy.
- Actual installedCLI off→status→on verifies newest local replies, persistent config and visible mode, with one upstream baseline request only and0foregroundhits/821samples. [Result](evidence/installed-cli/result.json); [off](evidence/installed-cli/gate-off.png), [status](evidence/installed-cli/gate-status.png), [on](evidence/installed-cli/gate-on.png).
- HKCU CIEL_RUNTIME_HOME switched at2026-10-29T21:03:40-05:00to `C:/Users/djlov/.local/share/ciel-runtime-907726516449764c26169a54c5d441cb0dd9be94`; generated statusline matches installed template exactly; backup kept. Normal local launcher reports `0.2.51-nightly.20261030-015514.9077265`.

# Preservation verification limit

The blanket three-router unchanged claim is NOT established for the whole SSE release interval:9505was already connection-refused before installedCLI began and stayed unavailable, with originalPID70956absent at post-switch. Cause/time of exit not established. No restart attempted.9385PID89420and9524PID18536retain exact identities;4unrelated dirty file digests unchanged. Owned teardown PID list does not include any protected PID. [Observation](evidence/post-switch-health-observation.json), [limit](evidence/preservation-limit.json). This does not prove what caused9505to exit.

# Activation boundary

No live session was restarted to apply this build. New launches use the installed code and receive `/sse-gate`; existing live processes retain old imported routing code. Off is a compact-admission override, not removal of identity/uncertain-delivery guards or Work approval.
