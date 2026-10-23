---
type: Release
title: CielAiRouter timeout retry nightly release
description: Nightly publication and verified local deployment of dfcf41a.
tags: [release, nightly, npm, timeout, retry]
timestamp: 2026-10-23
---

# Release

- Code commit: `dfcf41a529c7dc1f24945c964b853b0146220830`.
- Branch: `nightly`, fast-forward from c741650.
- CI run `39981199804`: success.
- Publish run `39981199746`: success, including actual tarball verification.
- Registry version: `0.2.51-nightly.20261023-155417.dfcf41a`.
- Registry `gitHead` matches the code commit.

# Local deployment

- Global npm install and `~/.local/share/ciel-runtime` snapshot updated from
  the exact published tarball. Snapshot files were replaced atomically without
  first deleting the live support directory; install.ps1 was not run.
- 449 snapshot/bin files copied and compared byte-for-byte with the package.
- `transient_retry.py` SHA-256 matches git, package, snapshot and global npm:
  `3d6dae320c9f56c7edb86f0c446f39591d951bf9f4f3de2ed0b9d750bc97aeff`.
- Installed-path verification: 99 focused tests pass; the recorded import path
  is the snapshot, not the worktree.
- Real loopback Responses behavior: typed timeout then success = 2 upstream
  requests and HTTP200; persistent timeout with retries2 = 3 requests and one
  HTTP504 SSE error; retries0 = 1 request. No real model requests were made.
- Existing routers 9489/9505/9524/9385 remained healthy with unchanged PIDs
  34936/33444/51420/49232. No live CLI/router was restarted. Installed code is
  available for the next launch; no claim of live-session recovery is made.

# Evidence

- [Deployment record](evidence/deployment.json)
- [Deployment console](evidence/deploy-console.txt)
- [Installed-code tests](evidence/installed-tests.txt)
- [Pre-release tests](evidence/pre-release-tests.txt)
