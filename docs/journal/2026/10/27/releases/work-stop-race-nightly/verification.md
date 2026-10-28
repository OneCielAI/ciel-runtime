---
type: Release
title: Work Stop race nightly
description: Verified def252d nightly and local installed queued-Work handoff.
tags: [work, stop, release]
timestamp: 2026-10-27
---
# Release proof

| Check | Result |
|---|---|
| Commit | def252dcb269859a7109e595b168412c0b3c6be9 |
| [CI](evidence/ci.json) / [Publish](evidence/publish.json) | 40595484719 all six jobs success;40595484529 success |
| [Registry](evidence/published.json) | 0.2.51-nightly.20261027-224345.def252d, exact gitHead/nightly tag, SHA1/SHA512 verified; tarball SHA256 d70a8bca50044354b4a65f8c77b43dbe8f1515f24a86c76f2bf373d557046508 |
| [Snapshot](evidence/snapshot-install.json) | 460 exact files, no extras/mismatch |
| [Global npm](evidence/npm-files.json) | 513 exact files, same version |
| [Installed race harness](evidence/installed-hook-tests.json) | display refresh preserves queued continuation; new main turn/session end cancel old Stop |
| [Actual installed CLI](evidence/installed-queue-24ae7eb3/result.json) | 10/10: one user prompt -> waiting active goal -> queued selection -> Write -> done -> original waiting goal restored; [screenshot](evidence/installed-queue-24ae7eb3/01-queue-yield-completed.png) |
| [Local switch](evidence/local-switch.json) | HKCU home/statusline exact verified snapshot |

# Boundaries

[Diagnosis](../../diagnostics/work-stop-read-race/findings.md): deterministic hook harness proves the read race; actual CLI run verifies ordinary queued handoff, not forced concurrent refresh timing. No claim this reproduces the exact historic overlap in this session or Prody. Live9505 PID62984 /9524 PID8544 unchanged. Existing processes keep their loaded code; no live restart/hook reload was performed. Upstream504 is separate and not fixed here.

Original tarball retained locally; temporary extraction moved outside the authored OKF tree after package/snapshot comparison. No install.ps1 used.
