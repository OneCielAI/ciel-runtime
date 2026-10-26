---
type: Release
title: Work hints, triage and queue - nightly and local release
description: Nightly publication and local installation of the Work usage hints, request triage and goal queue, including the start-retry fix found by the installed-snapshot run.
tags: [release, nightly, work, local-install]
timestamp: 2026-10-26T18:50:00Z
---
# Request

"빠진부분 보강해서 개선 테스트후 로컬 나이트리 배포" - change: [work-mode-hints](../../features/work-mode-hints/verification.md).

# First publication: e1826009

- CI 40370921168 and Publish 40370921328 succeeded on `e1826009db5e9bffa50355f7edb3e12709a50bdd` ([check](evidence/package-vs-commit.txt)).
- Registry: `nightly` = `0.2.51-nightly.20261026-175255.e182600`, gitHead matches, `latest` stays `0.2.51`; sha1 and sha512 of the downloaded tarball match ([published.json](evidence/published.json)); the five changed source files are byte-equal to the commit.
- New snapshot `C:\Users\djlov\.local\share\ciel-runtime-e1826009db5e9bffa50355f7edb3e12709a50bdd`: 454 files compared, no mismatch ([install-snapshot.json](evidence/install-snapshot.json)).
- Real CLI against that snapshot, run-111efba6: 8 of 10. The start notice and the empty-plan line were missing because the router was not yet connected on the mod's first load. Diagnosis and fix: [start on a mod's first load](../../features/work-mode-hints/verification.md). This build was not switched in locally.

# Second publication: 81f6a4b (start retry)

- Commit `81f6a4b` (mod hook and test, journal; [secret scan](evidence/secret-scan-fix.txt) 0 hits) pushed fast-forward to `nightly` and `nightly-20261026`.
- CI 40377622625 and Publish 40377622439 (npm test, publish, verify tarball) succeeded ([check](evidence/fix-81f6a4b/package-vs-commit.txt)).
- Registry: `nightly` = `0.2.51-nightly.20261026-183726.81f6a4b`, gitHead `81f6a4ba`, `latest` stays `0.2.51`; sha1 `07d6cda3` and sha512 match ([published.json](evidence/fix-81f6a4b/published.json)).
- New snapshot `C:\Users\djlov\.local\share\ciel-runtime-81f6a4ba5af240e6a3aa3e4437e8701a8c9bafa1`: 454 files, no mismatch. Its mod directory had no `.claude-plugin/types`, so the run below is a first load ([install-snapshot.json](evidence/fix-81f6a4b/install-snapshot.json)).
- Real Claude Code 2.1.310 against that snapshot, run-3fada75b ([script](evidence/scripts/run-installed-proof.py)): 10 of 10. `screen-001`/`002` show `Work unavailable`, `screen-003` shows the notice. The queued request shows while item 1 runs, and the edit succeeds ([01 PNG](../../features/work-mode-hints/evidence/run-3fada75b/01-start-notice-and-empty-hint.png), [07 PNG](../../features/work-mode-hints/evidence/run-3fada75b/07-queued-while-running.png)).

# Local switch

- [switch.json](evidence/fix-81f6a4b/switch.json) ([script](evidence/scripts/switch_local.py)): User `CIEL_RUNTIME_HOME` moved from the 2faae14 snapshot to the 81f6a4b snapshot. The generated statusline differed from the new template only in its two embedded snapshot paths. It was replaced with the template bytes; backup `ciel-runtime-statusline.py.before-81f6a4b-20261026` (sha256 `da5766c5`, the previous file).
  - The first attempt wrote the new statusline in text mode (CRLF), then failed on the PowerShell call before changing `CIEL_RUNTIME_HOME`. The script was corrected to write exact LF bytes and to reuse the backup it had kept; the second run completed. The installed statusline now has no CR and matches the template.
- npm global `@one-ciel-ai/ciel-runtime` = `0.2.51-nightly.20261026-183726.81f6a4b` ([npm view](evidence/fix-81f6a4b/npm-view.txt), [install](evidence/fix-81f6a4b/npm-install-global.txt)). Its `ciel_runtime_support` equals the snapshot except the `.claude-plugin/types` directory Claude Code writes at load ([compare](evidence/fix-81f6a4b/npm-vs-snapshot.txt)).
- [Launch paths](evidence/fix-81f6a4b/launch-paths.txt): `~/.local/bin` cmd, npm cmd and ps1 print `0.2.51-nightly.20261026-183726.81f6a4b`. The npm cmd path also printed a Python `SyntaxWarning: 'return' in a 'finally' block` from `codex_app_server_session.py:417`; it also appears in earlier router output and was not examined here.
- Live routers 9505 (pid 38368) and 9524 (pid 25276) kept their PIDs and code before and after the switch ([before](evidence/fix-81f6a4b/live-before-switch.txt), [after](evidence/fix-81f6a4b/launch-paths.txt)). Running sessions keep their launch code until they restart.
