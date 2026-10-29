---
type: Release Verification
title: Work retention nightly release
description: Records exact24hcompleted Work retention publication and installed verification without restarting live sessions.
tags: [work, retention, nightly, release]
timestamp: 2026-10-29
---
# Scope

User requested completed Work older than one day be automatically deleted, then explicitly requested implementation and deployment. Only Work completion retention, menu/API metadata, runnable-current display correction and relevant tests/evidence are included. Dirty Gemini/Kimi/provider migrations and unrelated TLS files remain excluded.

# Source

- Feature commit6d07b179a579969de5a4db457d9a314d603d1420.
- Final replan-boundary fixf48c810752c4f788375e3f404820b037f3c3683d: replacing a goal clears retired progress/completion state.69targeted tests pass after this correction.
- Both pushed to nightly and nightly-20261028.

# Source proofs

19new retention cases initially pass; replan regression adds one. Release-focused132 tests pass before that addition. Fullfocused154:153pass/1baselineWindowsreplaceerror, exactHEAD reproduction saved; native mod69pass/2samebaselineerrors. Actual owned backend6checks pass, actualCLI17checks/screenshots pass. These are source proofs, not installed proofs.

# Execution boundaries

No install.ps1, no live router/CLI restart, no source/project/transcript/artifact deletion by retention. Only completed Work rows under the configured workspace are expired. Focused items stay protected; counts preserved; unknownlegacytimestamps receive24hgrace. Live-before-install identities9385PID89420/9505PID70956/9524PID18536 recorded.

# CI ratchet correction

Initial f48c810 CI/Publish failed: main facade4984 lines exceeded existing4980 budget. a54d98eea0fcfd5c46ee5f0d2cbb2bb84b654154 moves lifecycle wrappers into WorkRetentionService.bind_lifecycle, retaining start/stop order and the existing4980 limit.96targeted tests pass, including architecture budget, facade binding and21retention cases. The six final pinned regression modules also pass134/134 on source. No test disabled or budget increased. This is the final release candidate; f48c810 was not installed. Actual owned source backend recheck6/6passes after lifecycle extraction (`evidence/source-lifecycle/backend-probe.json`). Protected9385/9505/9524 identities and8unrelated file digests remain unchanged before package acquisition.

# Published package

- Final commit `a54d98eea0fcfd5c46ee5f0d2cbb2bb84b654154`; nightly and nightly-20261028 pushed.
- Version `0.2.51-nightly.20261029-220515.a54d98e`, exact registry gitHead and nightly tag match.
- [CI40961722822](https://github.com/OneCielAI/ciel-runtime/actions/runs/40961722822): six jobs successful. [Publish40961722766](https://github.com/OneCielAI/ciel-runtime/actions/runs/40961722766): successful, including published tarball verification.
- Publish suite: unit3000(skipped59), router1486, channel582(skipped80), runtime455(skipped19); all groups pass on isolated GitHub runner. Full groups were not run on the protected live Windows account.
- SHA256 `6e4a858377bddf95fd0eb14d6124ef32a64417511cb67c4182d084ae8c88b800`; registry SHA1 and SHA512 verified.
- All516files compared with exact commit:506identical,8line-endings only,2verified version stamps; no unexplained differences.

# Installation

- Fresh snapshot `C:/Users/djlov/.local/share/ciel-runtime-a54d98eea0fcfd5c46ee5f0d2cbb2bb84b654154`:463files exact, no extra or mismatch.
- Global npm install used the verified local tarball with `--ignore-scripts`;516files match exactly.
- Installed134/134tests pass with imports asserted under the new snapshot, including21retention cases.

# Installed backend verification

Actual installed production router6/6checks pass: menu toggle and realHTTP MCP policy; periodic60s expiry (no direct sweep), recent/unfinished preservation,2/3counts, phase/version persistence, stale CAS409, owned-router restart persistence. Evidence: `evidence/installed-backend/backend-probe.json`. Only the fixture-owned router was restarted.

# Installed CLI and local switch

- Actual Claude Code2.1.312 against installed snapshot:17/17checks pass. The real60sworker removed10old done leaves, retaining10recent done and20unfinished leaves with20/40counts unchanged. Compact, expand, Inspector, Queue, Completed and collapse pass; native/companion content remains visible.
- Selected decisive screenshots: [before expiry](evidence/installed-ui/00-before-expiry.png), [after expiry compact](evidence/installed-ui/01-progress-bounded.png), [Completed tab](evidence/installed-ui/05-completed-tab.png), [compact again](evidence/installed-ui/06-compact-again.png). See [verification](evidence/installed-ui/verification.json).
- Owned proxy denied updater CONNECT requests to downloads.claude.ai and github.com; no real provider generation used. Only owned fixture descendants were terminated.
- Local HKCU CIEL_RUNTIME_HOME switched to the verified snapshot at2026-10-29T17:14:34-05:00; generated statusline matches that snapshot template exactly, previous script backed up. No settings.json permission change.
- Protected routers9385PID89420,9505PID70956,9524PID18536 retain exact PID/version/fingerprint. All8unrelated source/evidence digests unchanged.

# Application boundary

Local install and nightly publication are verified. Existing live processes were not restarted and still execute their prior imported code: automatic retention starts when a new router normally launches from this installation. No claim that old live instances already run the worker. Focus-protected nodes remain exempt from expiry until the user clears their focus; there is no forced focus clearing.
