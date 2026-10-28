---
type: Release
title: work_mode_set nightly and local install
description: Verified 679245f nightly publication, exact local snapshot and global install, and installed real Claude Code and Codex behaviour.
tags: [release, work-mode, codex, claude-mod]
timestamp: 2026-10-27
---
# Release proof

- Commit 679245fa9feaff3d72d7c948a53b5a7a92d5a2d4 ([feature findings](../../features/work-mode-switch/findings.md)). [CI](evidence/ci.json): six jobs succeeded. [Publish](evidence/publish.json): succeeded.
- [Registry](evidence/published.json): `0.2.51-nightly.20261028-023853.679245f`. The gitHead and nightly tag match exactly, and SHA1/SHA512 were verified. Tarball SHA256 is 600c292f5318c622c75048f3feb27ea515e5255be0fd13602c34c54555596ac5.
- [Package vs commit](evidence/package-vs-commit.json): 504 files identical, 8 differ only in line endings, and 2 are version stamps (`runtime_constants.py` VERSION and `package.json` version).
- [Snapshot](evidence/snapshot-install.json): 461 files exact. [Global npm](evidence/npm-files.json): 514 files exact, version 679245f.
- [Installed tests](evidence/installed-tests.txt): 50 OK. The changed modules loaded from the snapshot.
- [Local switch](evidence/local-switch.json): HKCU `CIEL_RUNTIME_HOME` and the generated statusline now point at the snapshot. The statusline diff is only the embedded snapshot path.

# Installed real behaviour

- Claude Code 2.1.312 with the installed mod and router: [result](../../features/work-mode-switch/evidence/run-installed-214815/result.json), 16/16 checks.
  - Screenshots: [off](../../features/work-mode-switch/evidence/run-installed-214815/02-agent-turned-off.png) and [gated again after on](../../features/work-mode-switch/evidence/run-installed-214815/05-edit-gated-again.png).
- Codex 0.158.0 app-server, `approvalPolicy: never`, Work mode off: [result](../../features/work-mode-switch/evidence/codex-installed-214851/result.json), 9/9 checks, including the negative control.

# Live routers

- 9524 (PID 68132, d2856f8) was not restarted. It, and this session, get `work_mode_set` only once a router runs this build.
- 9505 (`g:\ciel-walkie`) stopped by itself at 21:25:28 local. Its log ends with `router_client_stale_removed pid=43712` and then `router_managed_lifetime_shutdown reason=owner_dead_no_clients owner_pid=43712`.
  - Its owner was polling `/ca/tui/status` until 21:25:23.
  - My last test run had ended at 21:22:26 with 9505 alive. Between then and 21:25:28 I ran only `git commit`/`push` to the feature branch and `gh` reads.
  - Why launcher 43712 exited is unknown. The Ciel Walkie app processes are still running. Nothing was restarted.
- No remote or managed machine was touched.
