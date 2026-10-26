---
type: Release
title: Model compact safeguard nightly
description: Published and locally installed model-aware token window and measured90percent compact safeguard.
tags: [release, nightly, compaction]
timestamp: 2026-10-23
---

# Publication

- Commit1c48c8e3b28a60bcb1fcc81ea11c9cc93892a51a.
- Version0.2.51-nightly.20261023-220155.1c48c8e; registry gitHead matches.
- CI40029775472 success; npm publish40029775427 success.
- Worktree509 tests OK with42 existing retired architecture skips.

# Local deployment

Global npm and ~/.local/share/ciel-runtime installed from exact published
tarball without deleting live support directory.451files byte-compared with
package; runtime_compaction_binding.py hash matches package/snapshot/npm/git.
Installed-path499 tests OK with1 source-layout-only skip: the source fixture
expects ciel-runtime-menu.py beside ciel_runtime.py, whereas installed menu
lives in ~/.local/bin. Initial installed run failed on that path assertion;
its source run passed and installed menu was independently byte-compared.
Actual menu-selection/persistence tests still ran, not skipped.

All four live routers9489/9505/9524/9385 remained healthy with unchanged PIDs
during install. None was restarted; new launch required to load changes.
No live Emily automatic compaction or remote deployment is claimed.

Evidence: evidence/deployment.json, evidence/installed-tests.txt,
evidence/child-env.txt and evidence/dispatch-tests.txt.
