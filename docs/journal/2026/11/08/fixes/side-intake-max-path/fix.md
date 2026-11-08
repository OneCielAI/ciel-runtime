---
type: fix
title: SSE side intake fails under the legacy Windows path limit
description: "On AAP hosts without long-path support every SSE event failed to index: the side-inbox priority-marker temp path was 262 characters. The temp name no longer repeats the 64-hex target stem. The graphical Observatory also gains a keyboard path that needs no click."
tags: [sse, side-intake, windows, max-path, aap, observatory, keyboard]
timestamp: 2026-11-08
---

# Observed on .12 (read-only)

Neither Betty (u42) nor Prody (u41) ACKed or handled any SSE message in the side panel. Their router logs show the cause:

| Host | Build | Router | Priority-marker write failures | `channel_side_intake_deferred` | Side-model requests |
| --- | --- | --- | --- | --- | --- |
| Betty u42 | 93be24c | 9564 | 3,360 | 2,875 | 10 (Haiku, 17:18–17:54 only) |
| Prody u41 | 2df49b7 | 9592 | 3,141 | 2,522 | 0 |

Each failure is the same `FileNotFoundError: [Errno 2]`, at a path like this one:

```text
C:\Users\<aap-user>\AppData\Roaming\ciel-runtime\workspaces\<12>\side-inboxes\<64>\priority\P3\<2>\.<64>.<pid>.<time_ns>.tmp
```

- That path is 262 characters.
- Betty has `LongPathsEnabled=0`.
- The final marker path is 236 characters and would fit. Only the temp name is too long.

# Cause

`durable_json_file.save` named its temp file `.{stem}.{pid}.{time_ns}.tmp` in the target directory.

- For a priority marker (`priority/<P>/<key[:2]>/<key>.json`, a 64-hex key) that basename is 95 characters, longer than the 69-character target.
- `open('xb')` fails with FileNotFoundError above 259 characters.
- `receive` had already saved the pending record. It then raised, so the event stayed deferred.
- Every redelivery, and every claim-time re-index, failed on the same path. Nothing reached a side fork and nothing was ACKed.

# Fix

- `durable_json_file.save` now writes `.{pid}.{time_ns}.{8 hex}.tmp`, 30–38 characters, in the same directory.
  - The name no longer depends on the target's length.
  - The 8 random hex characters keep concurrent writers in one directory apart.
- Exclusive create, fsync, `_io_lock`, the bounded Windows replace retry, POSIX directory fsync and cleanup are unchanged.
- The side-inbox layout is unchanged.

# Observatory keyboard

**Confirmed:** the graphical dashboard is a Mods `Client`. A `Client` receives keys only after a click inside it, and Escape releases them.
- **Source:** Mods types (`ClientSurface.onKey`) and the Claude Code 2.1.319 binary.
- **Ruled out:** `open({ focus })`, Ctrl+X Tab, `autoFocus` and `$.ui.focus` cannot reach a `Client`.

What changed:
- A control strip of pane-drawn Buttons now sits above the dashboard. These Buttons are the pane's own focus ring.
  - `w` Work (autoFocus), `s` SSE, `g` Settings.
  - `k` ▲Prev, `j` ▼Next.
  - `i` Inspect, `p` P1, `f` Focus, `r` Refresh, `q` Close.
- Tab and the arrows move the ring, and Enter presses the focused control.
- The cursor and the P1 filter now live in plugin state and reach the `Client` through its props. The strip and the click-focused keys therefore move one shared cursor.
- `/work-mode view` and the dashboard footer now describe both routes, replacing the incorrect "Tab/arrows navigate".

# Open

On both hosts, a read-only check over the SYSTEM SSH found no `side-inboxes` directory. The isolated old-code probe on Betty did write the pending record before the marker failed (see verification). Why the live users' records were not visible to SYSTEM has not been determined.
