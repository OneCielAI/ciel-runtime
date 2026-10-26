---
type: Release
title: Work continuation nightly
description: Verified publication and local installation record for Work continuation, root priority, urgent holds and labels.
tags: [work, release, nightly]
timestamp: 2026-10-26T23:10:00Z
---
# Commits

- 040b9ee1d673dc1830d773dd20c12b8f7d684760: Work continuation. Failed CI unit/python-minimum on the OptionPanelRuntime ten-field architecture contract; Publish stopped at npm test, so nothing was published ([CI](https://github.com/OneCielAI/ciel-runtime/actions/runs/40413807363), [Publish](https://github.com/OneCielAI/ciel-runtime/actions/runs/40413807401)).
- 5054af2e3f706ca9f66ce68535f3e71a8287100a: contract fix. Includes the prior Walkie id-reuse and CielAiRouter native-model alias safeguard fixes.

# Publication

- [CI 40414938303](https://github.com/OneCielAI/ciel-runtime/actions/runs/40414938303): success, all jobs.
- [Publish 40414938042](https://github.com/OneCielAI/ciel-runtime/actions/runs/40414938042): npm publish done; nightly dist-tag points at `0.2.51-nightly.20261026-225621.5054af2`, gitHead 5054af2.
- Registry tarball sha1 and sha512 integrity matched the registry metadata; tarball sha256 9d31db1d8bf7fed21877470b6a76407947c52947c95097bca0efaef116e36f41, 507 files ([record](evidence/01-published.json)).
- Fifteen Work, statusline and compaction source files in the package are byte-identical (line endings normalized) to commit 5054af2.

# Local installation

- New snapshot `~/.local/share/ciel-runtime-5054af2e3f706ca9f66ce68535f3e71a8287100a`: 454 files compared with the package, no mismatch or extra file, Work mod manifest present.
- Installed snapshot real CLI verification passed: Work continuation 11/11 checks, CielAiRouter alias safeguard `compact_dispatched_once`, and Walkie `injected`; all owned runs reported live routers unchanged ([continuation](evidence/05-installed-continuation.txt), [compact](../../fixes/cielairouter-alias-safeguard/evidence/e2e/run-installed-5054af2-180641/result.json), [Walkie](../../diagnostics/walkie-durable-replied/evidence/e2e/run-installed-5054af2-180739/result.json)).
- Local switch completed: `CIEL_RUNTIME_HOME` now points to the new snapshot, statusline template matches it, and the previous statusline is backed up ([switch](evidence/02-switch.json)).
- Exact npm global install completed for `0.2.51-nightly.20261026-225621.5054af2`; npm and `.local` launch shims both report that version ([install](evidence/03-npm-install.txt), [launch paths](evidence/04-launch-paths.txt)). No live session was restarted.

# Source verification

[Work continuation verification](/docs/journal/2026/10/26/features/work-continuation/verification.md).
