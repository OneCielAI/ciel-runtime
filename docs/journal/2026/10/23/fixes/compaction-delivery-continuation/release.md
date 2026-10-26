---
type: Release
title: Compaction recovery nightly deployment
description: Local installed verification and nightly publication for 879c96d plus ed7ab55.
tags: [release, nightly, recovery]
timestamp: 2026-10-23
---

# Code

- Main recovery commit879c96d9184d92dbe66de11f899d68ff5cdca114.
- Final admission-refresh fix ed7ab55659a9e5ffe39d1509982552acafe80b7a.
- CI40017201433 and40017419438 both success.
- Publish40017201412 and40017419621 both success, including tarball reads.
- Final712 exact-file tests pass (42 existing architecture skips).

# Local install

- Exact registry version0.2.51-nightly.20261023-203900.ed7ab55 installed globally
 and into ~/.local/share/ciel-runtime using per-file atomic replacement.
-450 files compared against the tarball; changed transient_retry.py SHA256
 16de8775219480e75bee6d90e9ca288e10416c3cbf37cacea0afb6e1b1401365
 matches git/package/snapshot/npm.
- Installed-path400 tests pass, including real owned named-pipe/HTTP fixture.
- Live9489/9505/9524/9385 health responses retain unchanged PIDs during install;
 no CLI/router restart performed. Existing sessions retain old loaded code.
- See evidence/deployment.json and installed-tests.txt.

# Registry tag race

Both feature commits were pushed while CI was active. Earlier879c96d completed
version computation7seconds after newered7ab55 and published last, leaving
nightly=879c96d although ed7ab55 tarball exists. Deployment refused the tag mismatch
before changing files; exacted7ab55 version was then installed.

Local npm dist-tag correction failed E401 (invalid local npm authentication),
so no credentials were changed and no alternate account was used. The existing
Publish to npm workflow was dispatched once for the currentnightly tip via
run40019663823 to restore latest code through authorized CI publication.
Run40019663823 succeeded; nightly now names0.2.51-nightly.20261023-203900.ed7ab55 with matching gitHead. Local global/npm snapshot were refreshed to that exact tag version and400 installed-code tests passed again.

# Runtime boundary

Actual real300-second preheader timeout now retries and succeeds in the fixture.
Automatic compaction continuation verifies normal queue/owned transport delivery,
not a live Claude model executing the original task; live REGINA recovery remains
unclaimed. No asyncRewake enabled and no remote queue/history/cursor mutation.
