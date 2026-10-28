---
type: Release
title: Late SSE retry and subagent PowerShell nightly (7792965)
description: Pre-output CielAiRouter SSE error retry, held-byte delivery fix and the Windows PowerShell tool default, published as 0.2.51-nightly.20261027-182520.7792965, installed-proof verified and switched locally.
tags: [release, nightly, cielairouter, retry, powershell, work]
timestamp: 2026-10-27
---
# Scope

Commits on `nightly` and `nightly-20261026`: f54104c (late SSE retry), 56a84ac (held bytes before a later read failure), 7792965 (Windows PowerShell tool default). The package also carries eedd8ee (Work focus), which had been published without an installed proof.

- [Late SSE diagnosis](/docs/journal/2026/10/27/diagnostics/late-sse-retry/findings.md)
- [Subagent PowerShell diagnosis](/docs/journal/2026/10/27/diagnostics/subagent-powershell/findings.md)
- [Work focus feature](/docs/journal/2026/10/27/features/work-focus/findings.md)

# Published

| Check | Result |
|---|---|
| f54104c | CI 40548121490 router group failed (2 Responses wire tests); Publish 40548121582 failed; nothing published |
| 56a84ac | CI 40554861421 success; Publish 40554861375 success |
| CI 40557841943 (7792965) | success: quality, python-minimum, unit, channel, router, runtime ([run](evidence/run-40557841943.json)) |
| Publish 40557841890 (7792965) | success ([run](evidence/run-40557841890.json)) |
| Registry | `0.2.51-nightly.20261027-182520.7792965`, gitHead 77929651290a8a0c4647eb47fe251fb5db4d1206, dist-tag nightly; SHA-1/SHA-512 verified, tarball sha256 `fd74e4f04f33c582686dcf1167b7a757a0f01b5ffd3b617112e3bd12cfef2fa2`, 513 files ([acquire](evidence/acquire.txt)) |
| Package vs commit | the five changed runtime files identical to 7792965 ([check](evidence/package-source-check.txt)) |

# Installed

- Snapshot `C:\Users\djlov\.local\share\ciel-runtime-77929651290a8a0c4647eb47fe251fb5db4d1206`: 460 files compared, no mismatch or extra ([snapshot-install.json](evidence/snapshot-install.json)).
- Installed real-CLI proofs (Claude Code 2.1.311/2.1.310, isolated HOME/config/ports, egress sentinel):
  - late SSE `-p`: 2 identical upstream attempts, success, `cielairouter_late_sse_retry` logged ([run](evidence/installed/run-installed-133154/result.json)); every attempt failing: 4 attempts, 3 retries, `budget_exhausted`, original error shown ([run](evidence/installed/run-installed-failall-133201/result.json)).
  - late SSE interactive: [screenshot](evidence/installed/ui-late-sse/01-outcome.png) shows `LATE-SSE-RETRY-SUCCEEDED`, no API error ([result](evidence/installed/ui-late-sse/result.json)).
  - subagent PowerShell through the real launcher: [screenshot](evidence/installed/launch-subagent-ps/01-outcome.png) shows `MAIN-DONE main-ps=[PS-MAIN-OK-42] child=CHILD-DONE [PS-CHILD-OK-42]`; both requests offered the tool ([result](evidence/installed/launch-subagent-ps/result.json)).
  - Work focus CLI: 9/9 checks ([result](evidence/installed/focus-cli/result.json), [screenshot](evidence/installed/focus-cli/01-focus-exhausted.png)); Inspector focus/clear UI 4/4 ([result](evidence/installed/focus-ui/result.json)).
- Local switch: HKCU `CIEL_RUNTIME_HOME` → the 7792965 snapshot; generated status line replaced with the snapshot template, backup `ciel-runtime-statusline.py.before-7792965-20261027b` ([local-switch.json](evidence/local-switch.json)).
- npm global `@one-ciel-ai/ciel-runtime@0.2.51-nightly.20261027-182520.7792965`; snapshot and shim both report that version ([npm-install.txt](evidence/npm-install.txt), [launch-paths.txt](evidence/launch-paths.txt)).

# Live sessions

Routers 9505 (pid 79472, ea59a61) and 9524 (pid 65156, eec7d69) were identical before and after ([before](evidence/live-before.json), [after](evidence/live-after.json)). They keep the code they were launched with: the late SSE retry and the PowerShell default take effect only in sessions started after this switch; no session was restarted.
