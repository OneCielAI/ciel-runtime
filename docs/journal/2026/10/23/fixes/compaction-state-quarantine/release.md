---
type: Release
title: Compaction state quarantine nightly release
description: CI, npm publish, and local install record for commit 0cd6c60.
tags: [release, nightly, npm]
timestamp: 2026-10-23
---

# Release

- Commit: `0cd6c609308bfa3c1933caf6b87a1233fcb931c0`
- CI run 39904483683: success
- Publish to npm run 39904483686: success
- Registry: `0.2.51-nightly.20261023-052413.0cd6c60`, gitHead matches the commit
- Local snapshot: `~/.local/share/ciel-runtime` version and changed files verified;
  global npm install updated to the same version
- No live router was restarted; running processes keep the previous build until relaunch

# Affected-session readback (peer report, 2026-10-23 06:06-06:17 UTC)

The Walkie session reported after its own relaunch: installed version
`0.2.51-nightly.20261023-052413.0cd6c60`, `admission_paused=false`, and a fresh
renotify event accepted at 06:06:35Z entered the conversation automatically.
Later `agent.pending_reminder` events (turn_ended, 06:12:48Z and 06:17:05Z) also
entered the model turn. `queued_input_stale` with partial coverage remains, so
this confirms fresh-event delivery, not a historical queue drain. The peer
reported no reset, queue deletion, config edit, or second restart.
