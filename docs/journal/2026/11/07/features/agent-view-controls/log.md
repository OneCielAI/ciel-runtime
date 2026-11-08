# Change log

## 2026-11-07

- Added the left-arrow and background-agents rows to Work & SSE Settings (72e59e2).
- Set `leftArrowOpensAgents=false` in this machine's `~/.claude.json`. `disableAgentView` was left unset.
- Fixed beta lint failures (9293d59).
- Real-console probe showed the setting applies to newly launched Claude sessions only. Both rows now say so (93be24c).
- 93be24c CI and Publish passed; published beta `0.2.51-beta.20261108-005232.93be24c`. Tarball integrity, gitHead, 508 runtime files and unchanged latest/nightly tags verified.
- Installed the verified beta into a new immutable 93be24c snapshot and globally; set User `CIEL_RUNTIME_HOME` to that beta snapshot. No live process was restarted.
- Installed smoke: 562 package files matched, six isolated CLI commands passed, and both agent-view settings projected with the intended status.
- Owned isolated real-console launch showed both rows on Settings page 1. Scratch removed; live routers 9505/9489/9524 stayed healthy.
