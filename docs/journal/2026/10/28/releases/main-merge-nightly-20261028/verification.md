---
type: Release
title: Merge nightly into main and start nightly-20261028
description: Verified merge of nightly 3d484c7 into main as 3aa9d90 and the new working branch nightly-20261028.
tags: [release, main, nightly, branch]
timestamp: 2026-10-28
---
# Request

2026-10-28: "지금까지 버전을 메인브랜치 병합 그리고 새로운 나이트리브랜치에서 시작". This follows the previous cycle's pattern: a merge commit on main (073e94f, "Merge nightly into main; start nightly-20261026 ...") and a new dated branch started from that merge.

# Before

Recorded in [precheck](evidence/precheck.txt):
- `origin/nightly` and `origin/nightly-20261026` were both at 3d484c7, and `origin/main` at 073e94f was an ancestor of nightly. Nightly had 51 commits that main lacked; main had none that nightly lacked.
- The last code commit was 679245f (CI 40620957822 and Publish 40620957819 succeeded). Commits after it changed only `docs/`.
- The [code commits integrated](evidence/integrated-code-commits.txt) are 28 non-docs commits.

# Merge

- [Merge commit](evidence/merge-commit.txt) 3aa9d904c41d393113dc4f581e1270766fdbbc36 has parents 073e94f (main) and 3d484c7 (nightly). Its tree is 657d99b, identical to nightly's tree.
  - It was built with `git commit-tree`, so no working tree was checked out or touched.
  - It was pushed to main as a fast-forward (073e94f..3aa9d90).
- [main CI](evidence/main-ci.json) 40649026483 succeeded, 6/6 jobs.
- [main Publish](evidence/main-publish.json) 40649026470 succeeded on the stable channel. Its [log](evidence/main-publish-log.txt) says `@one-ciel-ai/ciel-runtime@0.2.51 is already published; skipping npm publish`, so nothing new was released.
- [dist-tags after the main push](evidence/dist-tags-after.json): `latest` is still 0.2.51 and `nightly` is still 0.2.51-nightly.20261028-023853.679245f. The `nightly` tag moved later, through the unplanned push described below.

# New nightly branch

- [nightly-20261028](evidence/new-branch.txt) was created at 3aa9d90 and pushed.
- Worktree `C:\Users\djlov\ciel-runtime-wt-work` now tracks it. The two pre-existing, unrelated `tls-eof` modifications are preserved.
- The release branch `nightly` stays the publish trigger.

# Unplanned nightly push (my error)

The plan was to fast-forward `nightly` only with this `[skip ci]` journal commit. My command chained `git diff --cached --check && git commit ...`, then read `HEAD` as the journal commit and pushed it to `nightly`. The check failed on trailing whitespace in `new-branch.txt`, so no commit was made. `HEAD` was still the merge commit 3aa9d90, and that is what got pushed to `nightly` (fast-forward from 3d484c7).
- This started CI 40650058447 and Publish 40650058464 on `nightly` for 3aa9d90.
- The tree of 3aa9d90 is identical to 3d484c7, so the code is byte-identical to the already published 679245f nightly. Only the version stamp differs.
- I did not cancel the run. Cancelling mid-publish could have left the `nightly` dist-tag in a half-updated state.
- Result ([nightly-publish-3aa9d90](evidence/nightly-publish-3aa9d90.json)): CI and Publish both succeeded. The `nightly` tag is now `0.2.51-nightly.20261028-073420.3aa9d90`; its gitHead is 3aa9d90 and the tarball SHA1 matches the registry's 37e7587594d138cb459f295a37d0815bfc7054eb.
- [Package comparison](evidence/nightly-3aa9d90-vs-679245f-package.json) with the 679245f package: both have 514 files and the same paths. Only `package.json` version and `runtime_constants.py` VERSION differ.
- Installs that follow `nightly` therefore receive the same code under a new version number.
- The local snapshot and global install stay at 679245f. That is the same code, so it was not reinstalled.

# Not changed

- No live router, session, install or managed machine was touched.
- The primary worktree `C:\Users\djlov\ciel-runtime` (branch nightly-20261020, with its own uncommitted change) was left as is.
- The 32 public journal files that contain the user's private global CLAUDE.md text (commit dc9914c, `2026/10/26/fixes/work-hybrid-contract`) are part of nightly history. They are now reachable from main as well. How to handle them is still awaiting the user's decision.
