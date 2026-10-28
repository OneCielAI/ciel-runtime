---
type: Release Verification
title: Work queue panel nightly
description: Exact publication and installed acceptance for waiting-goal handoff and compact Work tabs.
tags: [work, queue, panel, nightly]
timestamp: 2026-10-27
---
# Status

Source commit `ea59a6105838f611d1c4168ab150f01c6f4e6fd2` pushed nightly and nightly-20261026. CI 40469752581 and Publish 40469752579 succeeded. Published `0.2.51-nightly.20261027-080148.ea59a61`, exact gitHead and SHA1/SHA512 verified. Fresh snapshot: 460 files compared, no mismatch or extra. Local HKCU CIEL_RUNTIME_HOME, statusline and npm global switched to this version; both launch shims report it.

# Source verification

144 focused Python Work tests; 63 isolated native mod tests; repository Ruff, compile and docs metadata checks pass. Actual Claude Code 2.1.311 owned fixtures: queue 10/10, panel 12/12. Full test groups run in CI, not under the serving Windows account.

Source evidence: [diagnostic bundle](../../diagnostics/eric14-work-stall/findings.md). Initial remote agent mapping was incorrect and is explicitly corrected there; no Eric-specific remote cause is claimed. Managed host .14 was read-only; no remote installation or restart performed.

# Safety

Observed local baseline before acceptance: 9505 pid33384 and 9524 pid65156, both eec7d69 fp11b9fec1d2441894. Owned fixtures recorded equal before/after health. Keep live processes and their launch snapshots untouched. Use a fresh snapshot, not install.ps1.

# Installed acceptance

[installed-results.json](evidence/installed-results.json): actual Claude Code 2.1.311, owned isolated fixtures against the exact published snapshot.

- Queue handoff: **10/10**, one user prompt. All active steps waiting -> first runnable queue selected without inventing user priority -> plan/execute -> file Write -> verified completion -> original waiting goal resumes. Wait and approval state retained.
- Tabbed Work panel: **12/12**, 40 active leaves plus six queue goals and completed history. Progress hides done rows; Queue/Completed and next-page keyboard actions work; Inspector reachable; Work rows bounded <=10; registered synthetic companion stays visible; display operations do not mutate Work state. The synthetic companion verifies composition, not placement of real native teammates.
- PNGs: [progress](evidence/installed-panel/01-progress-bounded.png), [queue](evidence/installed-panel/03-queue-tab.png), [completed](evidence/installed-panel/04-completed-tab.png), [next page](evidence/installed-panel/05-completed-next-page.png), [queue completion](evidence/installed-queue/01-queue-yield-completed.png).
- [Snapshot comparison](evidence/snapshot-install.json), [published identity/digests](evidence/published.json), [local switch](evidence/local-switch.json), [launch paths](evidence/launch-paths.txt).
- [live-after.json](evidence/live-after.json): 9505 pid33384, 9524 pid65156 still on eec7d69 with fp11b9fec1d2441894. No restart or modification of their snapshots. Existing sessions keep launch code until their owners restart them.

# Limits

Screenshot Eric identity remains unresolved: `.14` currently maps agent21 to Ara and agent23 to Eric, while the screenshot says Eric agent21. Do not claim that these source fixes diagnose that exact remote incident. .14 was read-only throughout; local/nightly release did not authorize a remote restart/install. Screenshot #16 also shows an in-progress PostToolUse hook, not proof that every active item was blocked. Work only advances to the queue when no active leaf can run, retaining explicit priority protection otherwise.
