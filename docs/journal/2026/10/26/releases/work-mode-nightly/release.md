---
type: release-record
title: Work mode shipped to nightly and installed locally
description: Conversation-scoped Work mode published as 0.2.51-nightly.20261026-162537.2faae14 after one failed CI attempt, then installed into a new local snapshot.
tags: [release, nightly, npm, install, work]
timestamp: 2026-10-26T16:40:00Z
status: released-and-installed
---

# Request

User, 2026-10-26: "로컬 나이트리 푸시" (twice), then "계속". Feature details live in the [Work mode journal](../../features/work-mode/index.md).

# Commits on nightly

| Commit | Content |
| --- | --- |
| 0af4180 | feat(work): conversation-scoped hierarchical Work mode for Claude and Codex (28 files) |
| 12b8a12 | docs(journal): Work mode verification, isolation and timed evaluations (646 curated files) |
| 2faae14 | fix(work): keep --continue last on relaunch and skip TOML test on 3.10 |

Pushed fast-forward 179b9ed..12b8a12, then 12b8a12..2faae14, to `nightly` and `nightly-20261026`. No force push. Unrelated CRLF-only changes under docs/journal/2026/10/17 stayed uncommitted.

Journal evidence left out of the commit on purpose: isolated HOME/config trees, raw upstream request bodies, jsonl transcripts and per-poll console frames. Summaries, results, PNGs and assessment files are committed.

# Pre-push checks

- [Focused tests](evidence/focused-tests.txt): 458 tests OK. The first run found an import cycle (work_mode_launch -> work_mcp_tools -> work_mode_launch) through the architecture contract test; `WORK_TOOL_NAMES` moved into work_mode_launch before commit.
- [ruff](evidence/ruff.txt) `ruff check .` clean; project metadata check passed.
- [Mod](evidence/mod-validate-test.txt): `claude plugin validate --strict` passed; 18 mod tests passed.
- Secret scan of all 674 staged candidates against 66 locally stored credential values and token patterns: no credential value found. The one pattern hit is the existing `sk-ciel-router-` test placeholder.
- Live routers 9505 (pid 43372) and 9524 (pid 25276) unchanged across every test run.

# First CI attempt failed

CI 40354798049 and Publish 40354798047 on 12b8a12 failed; publish stopped at `npm test`, so nothing was published.

- python-minimum (Python 3.10): `tests/test_work_mode_launch.py` imported `tomllib`, which 3.10 lacks.
- unit and publish: four `test_runtime_session_restart` tests expected `--continue` as the last argument. The relaunch loop reinserted the Work `--plugin-dir` pair after the `--continue` a restart adds.

Fix 2faae14: the pair is inserted ahead of the first session selector or bare `--`; the tomllib import is guarded and the TOML test skips on 3.10; a launch test asserts `--plugin-dir` precedes `--continue`. [474 tests](evidence/ci-fix-tests.txt) passed locally on Python 3.14 and 3.10. Full test groups were not run locally because live routers are serving.

# Release

- CI 40357359092: success, all six jobs.
- Publish 40357359093: success, including Verify published tarball.
- Registry `nightly` = `0.2.51-nightly.20261026-162537.2faae14`, gitHead `2faae14453a948936e76cdfd3c1ee77b4d291371`; `latest` stays 0.2.51.
- Tarball sha256 `468b1204bca5bb235598e47f15e9099a79deb33e705db3e7d832957f1ebe421d`, 1,520,919 bytes, 507 files (previous nightly 497; +6 mod files, +4 Work modules). SHA-1 and SHA-512 checked against registry values before extraction ([published.json](evidence/published.json), [script](evidence/scripts/acquire_published.py)).

# Local install

install.ps1 was not used.

- New snapshot `~/.local/share/ciel-runtime-2faae14453a948936e76cdfd3c1ee77b4d291371` copied from the verified tarball: 454 files byte-identical, no extras, Work mod manifest present ([install-snapshot.json](evidence/install-snapshot.json), [script](evidence/scripts/install_snapshot.py)).
- Real Claude Code 2.1.310 E2E against that snapshot, owned loopback gateway/router, isolated environment: run-b0ef1f45 passed every step — vertical Work items, keyboard branch detail, `/work-mode view`, tree, ask deny and approve dialogs, executing phase, resume ([result](evidence/installed-e2e-result.json); PNGs under features/work-mode/evidence/navigation/run-b0ef1f45).
- One earlier attempt on the same snapshot, run-fe89ec44, ended when Claude showed "Press Ctrl-C again to exit" and exited after the keyboard-detail step. The harness sends Esc (0x1B) and Ctrl+X (chr 24), not Ctrl+C. The cause was not determined; it did not reproduce on the immediate rerun, and the earlier passing runs never showed that prompt.
- After the E2E, [switch](evidence/switch.json): User `CIEL_RUNTIME_HOME` moved from the breakdown snapshot to the new snapshot; generated `AppData/Local/ciel-runtime/bin/ciel-runtime-statusline.py` replaced atomically with the snapshot template (backup `.before-2faae14-20261026`).
- npm global `@one-ciel-ai/ciel-runtime` updated to the exact version; its `ciel_runtime_support` equals the snapshot. The only difference is the `.claude-plugin/types` stub directory, which Claude Code itself writes when it loads the plugin. The same directory exists in earlier snapshots and the worktree.
- [Launch paths](evidence/launch-paths.txt): `~/.local/bin` cmd, npm cmd and ps1 all print `0.2.51-nightly.20261026-162537.2faae14`.

# Limits

- Running sessions keep the code they were launched with. Routers 9505 and 9524 still run the breakdown snapshot (fingerprint f86ee3638ee760f3) until they are restarted; none were restarted here.
- The Walkie input-id reuse on router 9505 (investigation journal in the main checkout at docs/journal/2026/10/26/diagnostics/walkie-durable-replied) is not addressed by this release.
