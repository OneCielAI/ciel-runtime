---
type: Release
title: Compaction idle gates nightly release
description: CI, npm publish, and local install record for commit dc7711a, including a partial-install incident and its repair.
tags: [release, nightly, npm, install]
timestamp: 2026-10-23
---

# Release

- Commit: `dc7711ada162e848271ae0f1773a4bbb4314ae63` (fast-forward from 8b6114b)
- CI run 39915327934: success
- Publish to npm run 39915327931: `npm publish --tag nightly` 07:15:16Z,
  "Verify published tarball" success 07:20:24Z
- Registry: `0.2.51-nightly.20261023-071511.dc7711a`, gitHead matches the commit
- Global npm install: `@one-ciel-ai/ciel-runtime@0.2.51-nightly.20261023-071511.dc7711a`
- Snapshot `~/.local/share/ciel-runtime`: `runtime_compaction_control.py`,
  `runtime_compaction_binding.py` and `ciel_runtime.py` SHA-256 equal across the
  snapshot, the global install and `git show dc7711a:<file>`
- 109 focused tests pass with the modules loaded from the snapshot path
  ([output](evidence/installed-tests.txt))

# Install incident (local snapshot)

`install.ps1` from the published tarball ran `Remove-Item -Recurse -Force` on
the snapshot's `ciel_runtime_support`. It failed on five `__pycache__/*.cpython-314.pyc`
files that remained after the error, but only after deleting the `protocols/` and `providers/`
subpackages. The script stopped there, so `scripts/` and the bin shims were not
refreshed either.

Repair, within about two minutes: the published package's
`ciel_runtime_support/`, `scripts/`, `ciel_runtime.py` and the bin files listed
in `install.ps1` were copied over the snapshot. `diff -rq --exclude=__pycache__`
between the package and the snapshot then reported no differences, and
`providers.cielairouter` / `protocols.anthropic_thinking_policy` imported from
the snapshot.

Live routers 9489, 9505, 9524 (C:/Users/djlov) and 9385 (Celly) all run from
this snapshot under Python 3.14. Their `router.log` files show no
`ImportError`/`ModuleNotFoundError`/`Traceback` lines between 02:15 and 02:29
local time, and all four answered `/health` afterwards. No router or CLI was
restarted; each keeps its loaded build until its next launch.

Not determined: which process held the five `.pyc` files. `install.ps1`
deleting before it can finish the copy is a separate install-script defect,
not changed in this release.
