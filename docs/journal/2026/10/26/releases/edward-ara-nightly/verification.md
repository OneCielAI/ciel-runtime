---
type: Release Verification
title: Edward SSE and ARA Stop nightly
description: Exact CI publication and installed native CLI proof for 0555b6c.
tags: [nightly, install, verification]
timestamp: 2026-10-27T03:25:00Z
---
# Source

Commit 0555b6c follows explicit parked Claude background ownership and prevents internal Work status refreshes from superseding Stop. Source actual-CLI checks, screenshots and focused regressions are in the Edward and ARA diagnostic bundles.

# Publication

Pushed to nightly and nightly-20261026. CI 40442513726 and Publish 40442513748 succeeded. Published 0.2.51-nightly.20261027-031627.0555b6c; gitHead matches 0555b6c1e080d3466d44437d7eb01b7f61367615. Registry tarball SHA1/SHA512 verified. Fresh snapshot compares 460 files without mismatch. Installed native CLI checks pass: handoff 13/13, Work Stop 8/8. Both installed native proofs have decisive PNGs and durable JSON status evidence. The compaction-timeout retry request is separate ongoing work and is not claimed to be in this release.

# Safety

No .14 deployment/restart. Local live routers and native sessions stay on their existing snapshots. Install uses a fresh snapshot and verified published package; never install.ps1 over live code.

# Installed result

- Exact npm version: `0.2.51-nightly.20261027-031627.0555b6c`.
- Fresh snapshot: `C:/Users/djlov/.local/share/ciel-runtime-0555b6c1e080d3466d44437d7eb01b7f61367615`; 460 package files compared, no mismatch/extra.
- [Installed handoff](evidence/installed-handoff/result.json), [after background PNG](evidence/installed-handoff/02-background-injected.png): 13/13 checks pass through the actual production launcher, HTTP-configured SSE receiver, queue, exact bg peer and transcript.
- [Installed Work Stop](evidence/installed-work-stop/result.json), [wait/blocked PNG](evidence/installed-work-stop/01-waiting-blocked-notice.png), [push then pause PNG](evidence/installed-work-stop/02-actionable-push-then-bounded-pause.png): 8/8 checks pass, once-only person notice, no model replay for blocked Work, actionable push and bounded no-progress pause.
- [Local switch](evidence/local-switch.json) updated HKCU CIEL_RUNTIME_HOME and atomically installed the exact snapshot statusline (backup retained). `npm install -g` exact nightly succeeded.
- [Launch paths](evidence/launch-paths.txt): both npm and .local cmd shims report the exact nightly with inherited runtime/provider environment removed. [Live post-install](evidence/live-post-install.json) equals pre-install: router 9505 pid77432 fp72c6bfb456e30cd7, 9524 pid25276 fpf86ee3638ee760f3 unchanged.
- Registry payload was moved outside the OKF tree to an owned Temp directory after byte comparison; unpacked third-party Markdown is not part of this knowledge bundle. Its verified digests remain in published.json.
