---
type: Release
title: Pre-output reason diagnostics nightly
description: Verified exact ac3b505 nightly, local installation and actual isolated CLI behavior.
tags: [release, retry, diagnostics]
timestamp: 2026-10-27
---
# Verified release

- Commit ac3b505d6ae8f22ed59b7a5f46a404ea0bd7e15d; version0.2.51-nightly.20261027-231234.ac3b505.
- [CI](evidence/ci.json) six jobs successful; [Publish](evidence/publish.json) successful.
- [Registry/tarball](evidence/published.json): exact nightly/gitHead; SHA1/SHA512 verified; tarball SHA2567cd2ceff59de2e0322484b57a74b31bb8b701d9a194d7fa145220a296438a672.
- [Package vs source](evidence/package-vs-commit.json):503 exact files,8 CRLF-only,2 version-stamped.
- [Fresh snapshot](evidence/snapshot-install.json):460 exact files; [global npm](evidence/npm-files.json):513 exact files.
- [Installed tests](evidence/installed-tests.txt):18 pass, including reason classification and no private payload/header leakage.
- [Actual isolated CLI](evidence/installed-ui-182429/result.json):Claude Code2.1.311, two upstream main requests, success on screen, retry logged, no CLI nonstreaming fallback. [Screenshot](evidence/installed-ui-182429/01-outcome.png).
- [Actual diagnostic logs](evidence/installed-ui-182429/diagnostic-lines.txt):guard armed, retry1/3, opens2. Fixed reasons/counts only; no request body or error message logged. Successful message_start is also reported as unrecognized_frame (non-error); this label does not imply a failing stream.
- Egress sentinel blocked attempts to downloads.claude.ai and github.com. Live9505 PID62984 and9524 PID8544 unchanged before/after.
- [Local switch](evidence/local-switch.json):HKCU snapshot home and statusline template verified at18:26:57-05. No install.ps1; no live session restart.

# Not a verified fix for the actual recurring 504

This release adds observability only, preserving strict replay policy. The actual screenshot8 still lacks the wire/commit branch evidence. Existing router9524 remains7792965 and does not acquire this logging without relaunch. Fixture recovery does not prove recovery for the unknown real error framing. Tarball retained locally; untrusted extraction kept in a separate fresh temporary directory outside the authored journal tree.
