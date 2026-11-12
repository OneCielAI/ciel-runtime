---
type: release-record
title: Beta release and local install of 57dbfb3
description: The main-review, review-projection, bind-retry and true native-fork SSE changes were published as beta 0.2.51-beta.20261112-102713.57dbfb3 and installed locally. Records the two failed workflow attempts, the registry checks, the immutable snapshot and the global install.
status: published
timestamp: 2026-11-12T10:45:00Z
tags: [release, beta, npm, local-install, work-parallel, sse]
---

# Commits

| Commit | Content |
|---|---|
| `34966b6` | Code and tests. `tests/test_side_intake_inbox.py` is excluded; it carries an older uncommitted change. |
| `aafbe1c` | Journals and owned E2E evidence (1394 files). |
| `57dbfb3` | CI fix: `test_side_wake_protocol` skips its old-reader round trip when git history is unavailable. |

Before committing, staged blobs were compared with the working files after CRLF→LF normalization: 0 mismatches. Ruff 0.15.22 (the CI version) passed with `.local` excluded.

# Workflow attempts

1. **`aafbe1c`: CI and publish failed.** The unit group had one error: `test_side_wake_protocol` ran `git show 8e08b97:…`, and the shallow CI checkout returned exit 128. Nothing was published.
2. **`57dbfb3`: CI passed, publish attempt 1 failed.** `npm test` in the publish job failed one PTY test, `test_terminal_modal_guard … trust_dialog`, with `CHILD_EXIT=1 HELD=True`.
   - The same test passed in that commit's CI run.
   - Neither the test nor its modules (`terminal_modal_guard`, `channel_terminal_proxy`, `channel_wake_context`) changed in `ba8b13c..57dbfb3`.
   - The 2026-09-27 journal (`/docs/journal/2026/09/27/fixes/channel/trust-dialog-injection.okf`) already records this exit-status race, which reproduced in 2-3 of 5 runs and was left unchanged.
   - The race is still open.
3. **`57dbfb3`: publish attempt 2 (same commit, failed job rerun) succeeded.**
   - `npm test` passed.
   - `npm publish --tag beta` ran at 10:27:18Z.
   - The "Verify published tarball" step passed after registry propagation.
   - Evidence: [publish run](evidence/beta-57dbfb3/publish-run-43196834134.json), [CI run](evidence/beta-57dbfb3/ci-run-43196833992.json).

# Registry checks (measured)

- **beta:** `0.2.51-beta.20261112-102713.57dbfb3`, gitHead `57dbfb3ac9c8d9f1a42e89a038d27e16f4fab409`.
- **latest and nightly:** latest `0.2.51` and nightly `0.2.51-nightly.20261106-020333.c00c8b6` did not change ([before](evidence/beta-57dbfb3/dist-tags.json), [after](evidence/beta-57dbfb3/dist-tags-after.json)).
- **Integrity:** the SHA-512 of the downloaded tarball equals the registry `dist.integrity`, `sha512-Y/fCKXXu0fa80LO3sCvfY6s7W6ADu1zU9rpYQ89k5EjgFlvT9pmUv9sE1nKIWIoQic51kp3PZTHrNhMD0pkpjw==`.
- **Package vs commit:** the 573 packaged files were compared with the committed blobs from `git archive` with `core.autocrlf=false core.eol=lf`.
  - 571 are byte-identical.
  - 2 differ, only by the CI version stamp: `runtime_constants.py` `VERSION` and `package.json` `version`.
  - Details are in [source-compare](evidence/beta-57dbfb3/source-compare.txt).
- **Key files in the package:**
  - `work_parallel_finalization.py` `79a573b6`
  - `register.js` `ea7768f5`
  - `side-intake.js` `dcf8fbbc`
  - `types/index.d.ts` `4110db60` (LF)
  - `work_parallel_mcp.py` `ccf397c6`

  These are the committed LF blobs. The E2E runs used the CRLF working copies (`9b955068` / `542ff492` / `704cb811` / `5c9afdc5`), which have the same content after normalization.

# Local install (measured)

- **Immutable snapshot:** `C:/Users/djlov/.local/share/ciel-runtime-57dbfb3ac9c8d9f1a42e89a038d27e16f4fab409`. It is a copy of the verified tarball's `package/` folder: 573 files, all matching the package hashes.
- **Global package:** before the install, no process referenced the global package directory. `npm install -g <verified tgz>` reported "changed 1 package". All 573 global files match the snapshot ([global](evidence/beta-57dbfb3/global.sha256), [snapshot](evidence/beta-57dbfb3/snapshot.sha256)).
- **Channel:** User `CIEL_RUNTIME_HOME` was changed from `ciel-runtime-8e08b97…` to the new snapshot. The machine stays on beta, which the user chose on 2026-11-06.
- **Smoke test:** run with stripped env and a temp config, both `py -3 <snapshot>/ciel_runtime.py version` and `ciel-runtime.cmd version` print `ciel-runtime 0.2.51-beta.20261112-102713.57dbfb3`.
- **Pre-existing warning:** `codex_app_server_session.py:417` raises a SyntaxWarning (`return` in `finally`). That file last changed in `9ac82f0`, not in this release.
- **Live sessions:** they were not restarted. Launchers 23728 / 39984 / 21912 keep their creation times and still run from the `8e08b97…` snapshot.

# Installed E2E

The owned E2E against the installed snapshot is recorded in [owned E2E attempts](owned-e2e-attempts.md) (main review) and in the SSE bundle.
