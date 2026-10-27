---
type: Release
title: Router exit race nightly
description: Verified supervisor shutdown fix published and locally installed as 0.2.51-nightly.20261027-205033.3160858.
tags: [router, lifecycle, release, nightly]
timestamp: 2026-10-27
---
# Release

Commit `31608580bb380cd982dddf8d2dcadac92171c3d6`; [diagnosis](/docs/journal/2026/10/27/diagnostics/router-exit-race/findings.md).

| Check | Result |
|---|---|
| [CI 40579823552](evidence/ci.json) | all jobs success after retrying Python-minimum |
| Initial Python-minimum failure | 2935 tests passed, then tempfile cleanup failed with `Directory not empty`; [original log](evidence/ci-attempt1-cleanup-failure.txt). Same commit rerun passed. |
| [Publish 40579823581](evidence/publish.json) | success including tarball verification |
| [Registry](evidence/published.json) | `0.2.51-nightly.20261027-205033.3160858`, exact gitHead, SHA-1/SHA-512 verified; tarball SHA-256 `ebfc5e95254c920c9f061195b818093e55fcef53ff4821654e214c4b423c5dd1`, 513 files |
| [Snapshot install](evidence/snapshot-install.json) | fresh snapshot 3160858, 460 files identical, no extras |
| Installed actual CLI | [launch-installed-155420 result](/docs/journal/2026/10/27/diagnostics/router-exit-race/evidence/launch-installed-155420/result.json): 7/7 checks, two launches/two normal exits/TCP port closes both times |
| [Local switch](evidence/local-switch.json) | HKCU CIEL_RUNTIME_HOME and statusline match 3160858 snapshot |
| [npm global](evidence/npm-install.txt) | exact verified registry tarball installed; global package version read back as 3160858 |
| First npm attempt | registry version lookup temporarily returned ETARGET despite metadata/tarball availability; [failure retained](evidence/npm-install-registry-failed.txt), installed the already digest-verified exact registry tarball instead |

# Live sessions and remaining issues

Live routers 9505 PID 62984 (273856d) and 9524 PID 8544 (7792965) were not restarted or terminated. New launches use the new install; existing processes retain their loaded code. This release fixes the reproduced supervisor-after-exit spawn, not the separate self-update lease-grandchild defect or the unexplained actual pre-output 504 case. The user's original timeout is not causally attributed to the reproduced race without matching process evidence.
