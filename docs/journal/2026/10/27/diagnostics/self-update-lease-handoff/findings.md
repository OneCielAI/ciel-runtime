---
type: Diagnostic
title: Windows self-update launch lease handoff
description: A waiting updater retained the launch lease and blocked the restarted launcher's helper and managed-router children.
tags: [router, startup, windows, self-update, lease]
timestamp: 2026-10-27
---
# Confirmed defect

Windows `RuntimeRestartService.restart` starts a fresh Python process and waits for it. Port selection treated that restarted process like an ordinary helper: it shared the live updater's lease without taking ownership. The restarted launcher's own helper and managed-router children were not direct children of the lease holder and failed with `workspace router launch already reserved by another or unknown process`.

The [real subprocess regression baseline](evidence/baseline-3160858-real-chain.txt) runs the actual Windows restart service with a waiting updater, restarted launcher and two grandchildren. Both automatic and explicit reservations fail on installed-source baseline 3160858. No broad expiry/deletion of live reservations is used in the fix.

# Change

- Only the Windows direct-Python self-update restart sets `CIEL_RUNTIME_LAUNCH_LEASE_HANDOFF` to the updater PID, in its child environment (not the updater environment).
- Selection transfers ownership under the existing registry lock only when the marker names the exact direct parent and its live creation identity matches the recorded lease. Automatic/explicit reservation type is preserved.
- Normal helper children, including grandchildren inheriting an older marker, still share their parent's lease without taking it. Unrelated, reused or unknown owner identities remain protected.
- POSIX exec and indirect launcher fallback do not offer a new handoff marker.

# Verification

| Check | Evidence / result |
|---|---|
| Selector + restart focused tests | [38 selector + 5 restart tests](evidence/fixed-source-tests.txt), all pass; includes actual subprocess tree, normal helpers, unrelated/reused/unknown owner denial |
| Adjacent lifecycle tests | [10 client lifetime + 63 session restart](evidence/adjacent-tests.txt), pass; architecture 261 tests (42 skips), maintenance assembly 1, router workspace 4, npm 7 also pass |
| Actual installed 3160858 npm-bin launch | [baseline result](evidence/update-installed-auto-161906/result.json): self-update announced, managed router exits 1 after 30.6 s with exact lease error in [router log](evidence/update-installed-auto-161906/router-log-cfg.txt); [crash screenshot](evidence/update-installed-auto-161906/01-after-self-update.png) |
| Fixed source, automatic port | [result](evidence/update-source-auto-161716/result.json), 9/9: updater 66368 -> restarted launcher 55564 owns lease -> router and CLI ready -> normal exit and closed TCP port; [ready screenshot](evidence/update-source-auto-161716/01-after-self-update.png), [exit screenshot](evidence/update-source-auto-161716/02-exit.png) |
| Fixed source, explicit port | [result](evidence/update-source-explicit-161929/result.json), 9/9: updater 66940 -> restarted launcher 67076 owns lease -> router and CLI ready -> normal exit and closed TCP port; [ready screenshot](evidence/update-source-explicit-161929/01-after-self-update.png), [exit screenshot](evidence/update-source-explicit-161929/02-exit.png) |
| Actual Codex TUI, Work on | [result](evidence/update-source-auto-codex-162447/result.json), 9/9: real npm-bin self-update -> updater 63532 -> restarted launcher 67384 -> router/CLI ready -> normal exit/TCP closed; [ready screenshot](evidence/update-source-auto-codex-162447/01-after-self-update.png) |
| Live sessions | 9505 PID 62984 / 9524 PID 8544 and their fingerprints unchanged before/after all runs |

# Review follow-up

Independent read-only review found that matching healthy-router selection returned before handoff. That path now transfers the verified parent lease too, with a regression proving a later offline helper can still launch. Added explicit-port unrelated/reused/unknown-owner refusals and foreign/unidentified/occupied-listener tests; all assert registry immutability on rejection.

# Fixture and limits

[Actual launcher verifier](evidence/scripts/e2e_self_update.py) starts the npm bin shim (`node -> py -3 -> Python 3.14 -> updater's Python child`) in an owned non-activating Windows Terminal; native Claude Code is 2.1.310, Work on. HOME/config/workspace/ports are isolated, inherited runtime/provider variables stripped. A [stand-in npm](evidence/scripts/fake_npm.py) reports a newer version and accepts the install without changing any package. Therefore this verifies the real self-update decision/restart/launch path, **not a registry download or npm overwrite**. Actual published package verification is a separate release step.

The sentinel blocked 11 external CONNECT attempts in each successful source run. No upstream model request was needed; external downloads were not allowed. Screenshots and process-tree/registry JSON are retained. The first installed attempt `update-installed-auto-161301` used a snapshot outside node_modules and fell through to the global launcher shim; it is retained but is not the decisive direct restart-chain fixture. The corrected 161906 run uses the real installed npm-bin entrypoint.

The user's screenshot has the same lease exception; its original PID topology is unavailable. This reproduction establishes a product defect, not proof that all historical startup/Base URL timeouts share this cause. Upstream aggregate 504 remains a separate unresolved incident.
