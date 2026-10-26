---
type: Verification
title: Work hierarchy status display
description: Statusline now shows phase, branch progress and current hierarchical step instead of a bare total.
tags: [work, statusline, hierarchy, deployment]
timestamp: 2026-10-26T12:30:00Z
---
# Change

Both displays are updated: the mod footer shows phase and total progress, compact per-branch counts, and current parent > step; the command statusline adds a separate Work summary line plus the current hierarchical path/status/node id. Long titles are bounded and terminal control characters sanitized. The existing external /work board remains a session-scoped fallback only when no runtime Work plan exists. The statusline reads only the current conversation's hashed plan; no network calls or plan writes.

# Verified

- [48 Python tests](evidence/breakdown-tests.txt) passed, including branch counts, path, active leaf, other-session/missing identity rejection and existing status telemetry regression tests.
- [Mod validation and 15 tests](evidence/mod/breakdown-validation.txt) passed. Catalogue icon warning remains nonblocking.
- Actual installed snapshot CLI [run-ab574cd3 result](evidence/claude/run-ab574cd3/result.json) passed planning gates, auto execution, persisted resume and ask deny/approve.
- [Actual console screenshot](evidence/claude/run-ab574cd3/05-approved-executing.png) and matching [console text](evidence/claude/run-ab574cd3/screen-05-approved-executing.txt) show `work: run 1/2 [Exercise guar… 1/2] · Edit and verify subject > Continue persisted plan`.
- [Installed command output](evidence/breakdown-installed-output.txt) demonstrates the multibranch/path rendering from a synthetic copy of the completed coding evaluation plan. This is explicitly not a claim that the completed benchmark remains in progress.

# Deployment

New independent snapshot selected through User CIEL_RUNTIME_HOME; [manifest](evidence/deployment/breakdown-snapshot.json). Matching generated AppData/Local/ciel-runtime/bin/ciel-runtime-statusline.py atomically replaced after reading/backing up its previous bytes. No live session/router restart. Existing loaded mods do not hot-reload this new snapshot automatically; new launches load it. Generated statusline updates apply at its next invocation when the current process has compatible Work modules.
