---
type: Verification
title: Busy side intake and pending-delivery preservation
description: Native busy-session queue deduplication, failure retention and release evidence.
tags: [work, side-intake, verification]
timestamp: 2026-11-05
status: release-verification-in-progress
---

# Verified behavior

Owned native Claude Code 2.1.318 with loopback Messages stream, explicit fixture MCP tool allowlist and isolated state. No production session restart or permissions mutation.

- `.local/side-busy-85bfb881/result.json`: while one main stream remains active, RuntimeInputGateway produces an admitted side envelope; exact live-session callback accepts it twice, but Work node count increases only from one to two. Main model request count stays one and side sentinel is absent.
- `.local/side-busy-9fa5024f/result.json`: native typed `/work-mode settings` invokes real live-settings projection during active main stream. Actual native VT output includes Korean field labels and navigation. `.local/side-busy-9fa5024f/settings-ko.png` is a screenshot of xterm replay of captured native ConPTY bytes, not a desktop capture or hand-built mock.
- Regression reproduced: false side callback marked delivery terminal failed, and a later scan skipped it. Commit `9977504` defers the proven unsubmitted side attempt instead; retry then registers one Work goal and advances cursor only after durable queue receipt. Ordinary transport failures are unchanged.
- Focused side/socket/channel/architecture suite: 72 tests, one platform skip, all remaining tests pass. Ruff passes.
- Discovery instruction file path now available through live settings, translated in four locales; empty assignment clears configuration without writing/deleting a file or enabling discovery.
- CI `41986927815` failure: PTY test stopped reading on partial CHILD_DONE prefix before full SHA256 arrived. Full-line read correction passed eight actual Linux/WSL PTY tests.

# Release state

Commits `4195598`, merge `79ed345`, and `9977504` pushed to feature/work-parallel-20261103, nightly-20261020 and actual publishing branch nightly. Concurrent nightly merge history retained, no force push. Latest CI41989144202 and Publish41989144049 pending at writing. Guarded background installer requires both success and exact npm gitHead99775046660bda13cca89c35366702eeae2ecd75 before replacing global npm installation.

# Unverified boundary

External conversational ACK/recap is not implemented. event-ack/1 is existing transport status only, not a user-facing task receipt. Existing authenticated Walkie connector contract requested from peer; no endpoint guessed and no arbitrary event-provided URL used.
