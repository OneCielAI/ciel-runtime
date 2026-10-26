---
type: ReleaseVerification
title: Client-liveness recovery deployed locally and nightly
description: CI and publication succeeded and installed code matches the released source.
tags: [release, nightly, compaction]
timestamp: 2026-10-22
---

# Release

- Source commit: 063873ff1b2a8f187177d6dddb343b3c14c737c3.
- Registry version: 0.2.51-nightly.20261022-220706.063873f; gitHead matches.
- CI 39861087617: success.
- Publish to npm 39861087387: success.
- Global npm installed the exact nightly; package install.ps1 updated
  C:/Users/djlov/.local/share/ciel-runtime and the existing user runtime-home pin.
- Windows launcher `ciel-runtime.cmd version`: exit 0, same version.
  Existing unrelated codex_app_server_session.py:417 SyntaxWarning was printed.
- All three changed product files match source in global npm and runtime home.
- [Installed-code regression](evidence/installed-tests.txt): 61 tests, OK.

# Evidence boundaries

[Diagnosis](diagnosis.md) records 234 focused tests and the isolated native
Claude 2.1.308 loopback proof: delayed headers timed out, early SSE pings
returned usable output after 365 seconds. This is not a live Celly checkpoint.
No active session was restarted, history edited, or live queue modified.
The installed code takes effect on the next runtime launch.
