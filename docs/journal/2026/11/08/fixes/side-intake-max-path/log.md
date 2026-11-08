# Change log

## 2026-11-08

- Fixed the side-intake failure on Betty and Prody:
  - Read-only log scan showed 3,360 and 3,141 priority-marker write failures on a 262-character temp path.
  - Shortened the durable temp name.
- Added AAP-depth regression tests. They fail on HEAD 2df49b7 and pass with the fix.
- Ran a real legacy-path test on Betty: the old code fails at 262 characters, and the fixed code completes receive, claim and finish.
- Added the Observatory control strip, so the keyboard works without clicking the dashboard. Verified in an owned real console, including a P1 fork that was processed and answered.
- Released beta 6933b8d:
  - CI and Publish succeeded and the registry check passed.
  - Installed locally as an immutable snapshot plus `npm install -g`.
  - Reran the console probe on the installed build.
- Betty u42 relaunched on 6933b8d on its own. Since then its log shows no path errors, no deferred events and no upstream errors.
- Before that relaunch, Betty's six side requests to `_API_KEYS-SSE-1` failed at the gateway ("Combo has no executable targets").
- Prody u41 is still on 2df49b7. Removed the owned Betty probe folder.
