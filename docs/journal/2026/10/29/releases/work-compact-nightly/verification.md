---
type: Release Verification
title: Work compact view nightly deployment
description: Tracks the exact Work-only nightly package, local snapshot verification, and live-session preservation.
tags: [work, nightly, release, statusline]
timestamp: 2026-10-29
---
# Requested scope

User explicitly requested local and nightly deployment of compact Work display and verified REST/MCP management. Gateway incident was separately reported through an authenticated cross-session socket; that report does not authorize gateway mutation.

# Source

Commit `e3cb9a17214e5139eb0b34a9b166ed5a73dd3040`, pushed to `nightly` and `nightly-20261028`.

Only Work mod, generated statusline, relevant regression tests, and selected safe UI/API evidence were committed. Uncommitted Gemini/Kimi providers/config migrations and unrelated TLS evidence were excluded.

# Verification before publication

-34 statusline tests pass; source actual UI14 checks/six screenshots; actual backend REST/MCP6 HTTP checks.
- Full native mod70 tests:68 pass/2fail, same two non-UI failures on untouched HEAD baseline68 tests:66pass/2fail. This release does not claim those unrelated native-plan/toast failures fixed.
- All-repo Ruff and project metadata pass. Git staged diff check passes.
- CI40896131065 success6/6; Publish40896131015 success, including full npm test and published tarball verification.

# Protected live sessions

Before-install snapshot:9505PID86296 / fe71169;9524PID18536 /679245f. Existing live sessions must remain up; no install.ps1 or live restart used.

# Published and installed

- Version: `0.2.51-nightly.20261029-150640.e3cb9a1`.
- Exact registry gitHead: `e3cb9a17214e5139eb0b34a9b166ed5a73dd3040`; nightly tag points to it.
- Tarball SHA-256: `d0e8cdbcaf9ae3077cd96ecb2448dd872f965ba1910a7b05b1c5d7dcaeefb334`; registry SHA-1 and SHA-512 verified.
- All515 package files compared with commit:505 byte-identical,8 line-ending-only,2 publication version stamps. No missing or unexpected source files.
- Fresh local snapshot462 files exact, no extra files. Global npm515 files exact. `npm install -g --ignore-scripts` used the verified tarball; install.ps1 never run.
- Installed statusline regression34 pass, module paths pinned to new snapshot. Installed production backend actual HTTP6/6 checks (REST board/add/off; MCP tools/list/status/on; persisted config and plans preserved; REST on/help).
- Installed actual Claude2.1.312 UI14/14 checks; compact, expand, Completed tab, collapse and companion/native preservation. Screenshots and sanitized verification saved under `evidence/installed-ui/`.
- At2026-10-29T10:14:38-05:00, user `CIEL_RUNTIME_HOME` and generated statusline switched to the exact snapshot. Previous statusline backed up; generated script byte-identical to snapshot template.
- Before/after live9505PID86296 fe71169 and9524PID18536 679245f unchanged; no live router/CLI restart. Existing running mod instances keep their loaded code until their normal next launch/reload; no forced interruption was done.
- All8 digest-tracked unrelated provider/test/TLS files remain unchanged. Gemini/Kimi support is not included in this Work-only release.

# Gateway inquiry

User-requested authenticated cross-socket report sent09:49:51 local to gateway Claude peer `node gateway windows setup`, session `b43eb527-ba34-40d8-9ee5-c2fbc0add1fa`, cwd `F:\omini-router`. Peer transcript records queue/attachment receipt. No analysis reply received as of this release; no gateway mutation authorized by this report. See the local diagnostic receipt in `diagnostics/agent-stream-interruption/evidence/` (not added to this public release).
