---
type: ReleaseVerification
title: Custom tool contract release
description: Confirmed runtime response restoration gaps fixed, published and installed locally.
tags: [release, nightly, codex]
timestamp: 2026-10-22
---

# Release

Source bb5a17f81bc07fe7a31f42b1161e5372778f174d. Registry 0.2.51-nightly.20261023-012415.bb5a17f, matching gitHead. CI39882687920 and Publish39882687922 succeeded.

Global npm and actual runtime home installed from registry; both changed module hashes match source. Windows launcher reports exact registry version, exit0. Installed-code regression:79 tests OK. Real loopback HTTP replay against installed code returns custom_tool_call/functions.exec, exact raw script and call_id without the outbound flag (evidence/loopback-installed.json).

# Boundaries

Remote Ara was not restarted or installed. Live tool recovery there is not verified. Celly automatic post-compact continuation is not implemented: missing scoped intent and monotonic new-input/cancel evidence; see ../../diagnostics/celly-postcompact-continuation/investigation.md. No unsafe automatic continuation injection added.
