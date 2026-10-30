---
type: Release Verification
title: Grant automatic restart and headless startup repair
description: Three targeted safety fixes with isolated regressions and an actual native headless CLI witness; release and installation follow exact commit verification.
tags: [release, nightly, grant, restart]
timestamp: 2026-10-30T19:25:00Z
---

# Scope

- Unknown company-prompt identity does not authorize a restart; legacy instruction state refreshes response metadata unconditionally once, then resumes normal validators. Event announcements are not promoted to installed prompt proof.
- Pending submitted channel inputs protect their child from restart until their inflight/backlog tracking settles, including when transcript busy detection has not caught up. Unknown protection fails closed. Existing verified-child-exit handling is retained.
- Unattended Windows ConPTY launches read the owned child console through a separate helper. The launcher never detaches its own console. Current rendered frames, not append-only output or timeouts, establish startup readiness. Missing screen still holds.

# Source verification

291 targeted tests passed (company prompt, instructions, restart control, unattended startup, pending injection, terminal dispatch, Windows ConPTY, screen reader). Four new restart-protection regressions failed against baseline; prompt-unknown and legacy metadata regressions reproduced baseline defects. Old screen reader after FreeConsole returns None; new helper reads actual owned child viewport.

Actual isolated native CLI + loopback model fixture: startup dialog answered from rendered screen, composer readiness proven, OWNED_GRANT_REPLY_TEST submitted and OWNED_GRANT_REPLY_OK displayed. CLI text frames and exact JSON witness are retained locally. No live session restarted, input replayed, or ACL altered.

# Release status

Pending exact commit CI, npm tarball, installed proof and local selection checks. Raw incident captures contain private material and are deliberately not committed or packaged.
