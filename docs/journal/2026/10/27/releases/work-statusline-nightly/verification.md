---
type: Release
title: Work status line attention nightly (54cfce3)
description: Waiting/blocked emphasis in the status line, published as 0.2.51-nightly.20261027-144102.54cfce3, installed-proof verified and switched locally.
tags: [release, nightly, work, statusline]
timestamp: 2026-10-27
---
# Scope

Work n367: [feature record](/docs/journal/2026/10/27/features/work-statusline-attention/findings.md). Commit `54cfce39e031d6392a4a12c5640fb0dea9c44d2e` on `nightly` and `nightly-20261026`.

# Published

| Check | Result |
|---|---|
| CI 40521719359 | success: quality, python-minimum, unit, channel, router, runtime ([ci.json](evidence/ci.json)) |
| Publish to npm 40521719662 | success ([publish.json](evidence/publish.json)) |
| Registry | `0.2.51-nightly.20261027-144102.54cfce3`, gitHead 54cfce3, dist-tag nightly; SHA-1 and SHA-512 verified; tarball sha256 `c9f7417f1e2022edd41cde228b94b491142a3f5243fa7e0d86745870c4931bb1`, 513 files ([published.json](evidence/published.json)) |
| Package vs git | 460 runtime files: identical except the VERSION stamp in `runtime_constants.py` and CRLF in `scripts/deploy_colab_speech.ps1`; the 14 absent scripts are outside `package.json` `files` ([package-vs-git.txt](evidence/package-vs-git.txt)) |

# Installed

- Snapshot `C:\Users\djlov\.local\share\ciel-runtime-54cfce39e031d6392a4a12c5640fb0dea9c44d2e`: 460 files compared, no mismatch or extra ([snapshot-install.json](evidence/snapshot-install.json)).
- Installed proof: real Claude Code 2.1.311 with the snapshot's status line and Work mod, isolated HOME/config/ports and egress sentinel: checks 9/9, plan unchanged ([installed/01-statusline.png](evidence/installed/01-statusline.png), [installed/result.json](evidence/installed/result.json)).
- Local switch: HKCU `CIEL_RUNTIME_HOME` ea59a61 → 54cfce3; generated status line replaced with the snapshot template (backup `ciel-runtime-statusline.py.before-54cfce3-20261027b`) ([local-switch.json](evidence/local-switch.json)).
- npm global `@one-ciel-ai/ciel-runtime@0.2.51-nightly.20261027-144102.54cfce3`; snapshot and shim both report that version ([npm-install.txt](evidence/npm-install.txt), [launch-paths.txt](evidence/launch-paths.txt)).

# Live sessions

Routers 9505 (pid 79472, ea59a61) and 9524 (pid 65156, eec7d69) were identical before and after ([live-before.json](evidence/live-before.json), [live-after.json](evidence/live-after.json)); they keep the code they were launched with. Running Claude sessions read the replaced status line file on their next refresh; their Work mod and router change only when they are restarted, which was not done.
