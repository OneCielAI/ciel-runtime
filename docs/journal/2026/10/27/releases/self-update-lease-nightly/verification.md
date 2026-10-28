---
type: Release
title: Self-update lease handoff nightly
description: Publication and installed-package verification for the Windows self-update launch-lease fix.
tags: [windows, router, self-update, nightly]
timestamp: 2026-10-27
---
# Target

Commit `a92bcd901c68716337d1db1075ef8ed4cdb30c02`; source diagnosis and actual startup proof in [findings](/docs/journal/2026/10/27/diagnostics/self-update-lease-handoff/findings.md).

# Published and installed

| Check | Result |
|---|---|
| [CI](evidence/ci.json) | 40587002494, all six jobs success |
| [Publish](evidence/publish.json) | 40587003921 success including registry tarball verification |
| [Registry](evidence/published.json) | `0.2.51-nightly.20261027-214004.a92bcd9`, exact gitHead and nightly tag, SHA-1/SHA-512 verified; tarball SHA-256 `ee0d10c4071b992e6935320c54ddafac22b14acd9d1a3fad382010933c774e4c` |
| [Fresh snapshot](evidence/snapshot-install.json) | 460 files identical, no extra/mismatch |
| [npm global](evidence/npm-files.json) | digest-verified registry tarball installed; all 513 files identical |
| [Installed focused tests](evidence/installed-tests.json) | 43 pass against exact installed selector/restart modules |
| [Local switch](evidence/local-switch.json) | HKCU home and generated statusline point to verified a92bcd9 snapshot; previous statusline backup kept |
| Actual installed Codex | [explicit-port self-update result](../../diagnostics/self-update-lease-handoff/evidence/update-installed-explicit-codex-164632/result.json), 9/9 checks; [ready screenshot](../../diagnostics/self-update-lease-handoff/evidence/update-installed-explicit-codex-164632/01-after-self-update.png) |
| Actual installed Claude | [automatic-port self-update result](../../diagnostics/self-update-lease-handoff/evidence/update-installed-auto-164510/result.json), 9/9 checks; [ready screenshot](../../diagnostics/self-update-lease-handoff/evidence/update-installed-auto-164510/01-after-self-update.png) |

# Boundaries

No managed remote host or live router is restarted. Original screenshot process topology and upstream Base URL timeout remain unverified; the separate aggregate 504 incident is not claimed fixed by this change.

# Upgrade boundary

An updater already running old code does not emit the new handoff marker; installing a new package does not rewrite that process in memory. The direct-restart fix is verified when the updater itself runs a92bcd9. New launches use the installed code; no claim is made that this retroactively fixes an old updater already executing the previous restart method.

# Additional final-source proof

[Actual Codex explicit-port self-update](../../diagnostics/self-update-lease-handoff/evidence/update-source-explicit-codex-163128/result.json) passed 9/9 checks after the healthy-router handoff review correction. Work on, updater/restarted-child relationship and transferred lease verified, router and native CLI ready, `/exit` returned normally and TCP listener closed. [Ready screenshot](../../diagnostics/self-update-lease-handoff/evidence/update-source-explicit-codex-163128/01-after-self-update.png); live router PID/fingerprint unchanged.

# Evidence storage

Original registry tarball remains under `evidence/payload/download/published.tgz` (git-ignored); extraction moved after comparison to `C:/Users/djlov/AppData/Local/Temp/ciel-a92bcd9-verified-payload-z35pdt4w/unpack` so package Markdown is not mistaken for authored OKF. Initial validation including the extraction rejected 32 vendor Markdown files; authored release bundle validates with one tolerated cross-bundle link warning. Source/screens are preserved as captured, including terminal whitespace.
