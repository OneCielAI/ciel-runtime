---
type: Release
title: SSE error shape nightly and live router 9524
description: Verified d2856f8 nightly, local install, and approved router-only replacement of live 9524.
tags: [release, retry, diagnostics, router]
timestamp: 2026-10-27
---
# Release proof

- Commit d2856f828a9f48cf2122c438aa2b698b4cd72150; [CI](evidence/ci.json) six jobs success; [Publish](evidence/publish.json) success.
- [Registry](evidence/published.json): 0.2.51-nightly.20261028-005129.d2856f8, exact gitHead/nightly tag, SHA1/SHA512 verified; tarball SHA256 634b3818399208bccc0837fc1333119ab75967de2a391dcea691d7b976419b64.
- [Package vs commit](evidence/package-vs-commit.json): 504 exact, 8 CRLF-only, 2 version stamps.
- [Snapshot](evidence/snapshot-install.json) 461 exact; [global npm](evidence/npm-files.json) 514 exact.
- [Installed tests](evidence/installed-tests.txt): 45 pass with modules loaded from the snapshot.
- [Installed actual Claude Code](evidence/installed-ui/result.json): integer error.code 504 stays non-retryable; screen shows the error ([screenshot](evidence/installed-ui/01-outcome.png)); logs record guard reason `unrecognized_frame` and `code:number`, `type_token:api_error`.
- [Local switch](evidence/local-switch.json): HKCU home and statusline.

# Live router 9524 replacement (user-approved, 9524 only)

Existing restart paths were unsuitable: session restart also restarts the CLI, the launcher supervisor respawns its own old script, and `stop` also stops NCP. [router_handover_launch.py](evidence/scripts/router_handover_launch.py) copies the old router environment (values not printed), swaps only home/script paths, imports the new snapshot while the old endpoint is healthy (registry unchanged), terminates only the health-verified old PID, then serves the same port ([serve script](evidence/scripts/router_handover_serve.py)).

- Rehearsed on isolated owned routers with the old and both new snapshots: [ac3b505](evidence/rehearsal-1/result.json), [d2856f8](evidence/rehearsal-d2856f8/result.json); all six checks, no egress.
- [Live result](evidence/live-handover.json): PID 8544 (7792965) -> 68132 (d2856f8); active client 29548 kept; CLI 20056 and launcher 29548 unchanged; registry lease unchanged; one 9524 listener; 9505 PID 62984 unchanged.
- [Live diagnostics](evidence/live-diagnostic-lines.txt): real requests now log `cielairouter_preoutput_guard state=armed` and commit shape. The old launcher logs `router_lifetime_keep_alive reason=reachable_health_mismatch_active_client` every 5 s; by source it keeps the endpoint.

# Side effect

The new router rotated the 1.41 MB `router.log` on start (size rule > 1 MB; the old process had held the file open). The previous `router.log.1` (before 13:42 local, including the 08:52-12:26 504 events) was overwritten. Those events were analysed earlier ([late-sse-retry findings](../../diagnostics/late-sse-retry/findings.md)), but their raw lines no longer exist locally. `router.log.1` now holds 13:45-20:05, including 17:20 and 18:10.

# Boundary

Observability only; retry acceptance unchanged. The actual recurring 504 cause is still unknown until it recurs on this router.
