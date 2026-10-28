---
type: Verification
title: Experimental Work mode local deployment
description: Actual Claude planning gates, approval UI, conversation resume and installed snapshot proof.
tags: [work, claude, codex, deployment, verification]
timestamp: 2026-10-26T11:35:00Z
---
# Implemented

Default-on, non-native Ciel launches attach the bundled Claude mod or Codex developer instructions when router MCP is attached. The prelaunch LLM options expose Work mode and Work plan approval. Auto approval is default. Planning decomposes goal/phase/task/step trees with eight levels below root and 600 nodes maximum. Every completed leaf requires evidence. State is per exact runtime conversation, never shared across unrelated sessions in one folder. Native clients are excluded. `/work-mode status|tree` is distinct from the separately repaired external `/work` command.

# Actual Claude CLI proof

Source run [run-5bbcdc3b result](evidence/claude/run-5bbcdc3b/result.json), then installed snapshot run [run-532f0d94 result](evidence/claude/run-532f0d94/result.json). Both used the real installed Claude Code 2.1.310, an owned loopback fake Messages model, owned router, empty home/config and blocked external proxy. No real provider API calls. ENABLE_TOOL_SEARCH=false is fixture-only to expose Work schemas to the deterministic fake model; production supports normal deferred MCP discovery.

Observed under bypass permissions:
- Read subject succeeds while planning; Edit is denied.
- Bash attempting to write shell-planning.txt is denied; the file does not exist afterward. This installed version runs tool.check under bypass.
- work_plan_set builds goal -> phase -> task -> steps; work_execute auto switches to execution.
- Edit succeeds only after execute; actual subject content is AFTER, and read-only Bash verifies it.
- One leaf marked done with evidence; progress 1/2.
- A separate process resumes the exact conversation and sees identical version 4 / phase executing / progress 1/2.
- Real interactive /work-mode tree renders the persisted hierarchy and footer.
- Real Keep planning selection leaves phase planning; real Approve selection changes phase executing. Approval is pinned to both conversation id and plan version.

Installed-run captures:
- [Persisted tree](evidence/claude/run-532f0d94/01-persisted-tree.png)
- [Approval dialog](evidence/claude/run-532f0d94/02-approval-dialog-deny.png)
- [Rejected](evidence/claude/run-532f0d94/03-denied-planning.png)
- [Approve dialog](evidence/claude/run-532f0d94/04-approval-dialog-approve.png)
- [Executing footer](evidence/claude/run-532f0d94/05-approved-executing.png)

Screenshots were captured with the existing owned-window helper; matching screen text and successful tool/router results are the behavior evidence. Earlier runs are retained as failed fixture attempts: missing deferred MCP schema discovery, and an API-key confirmation prompt. They are not passing feature evidence.

# Codex

Real Codex 0.158.0 app-server proof captured native MCP `_meta.sessionId` in [wire calls](evidence/codex/native-metadata-isolation/mcp-wire-calls.json), with no model-supplied session_id. Work tools completed under approvalPolicy=never, native ids separated plans, missing identity was rejected. [Summary](evidence/codex/native-metadata-isolation/summary.json).

Installed snapshot was also verified with the real Codex app-server: [installed summary](evidence/codex/installed-native-metadata/summary.json), native metadata/isolation passed.

Codex planning/ask approval remains model-mediated: there is no pre-tool enforcement on Codex edits. TUI and remote-TUI argument materialization is covered by launch tests, not a real interactive Codex TUI screenshot.

# Automated and menu checks

- [146 exact-file Python tests](evidence/focused-final-tests.txt), including launch/restart on/off toggles, isolation, MCP dispatch, corruption, concurrent writes and packaging.
- [14 mod tests](evidence/mod/final-validation.txt); strict validation exit 0, one nonblocking missing catalogue icon warning.
- [Actual prelaunch menu result](evidence/menu/result.json), six screenshots: defaults on/auto; off/on and ask/auto persist.
- [npm pack dry run](evidence/packaging/npm-pack-dry-run-summary.txt): required mod manifest and hooks shipped.

# Local deployment

Deployment manifest: [snapshot](evidence/deployment/snapshot.json). A new standalone snapshot was copied (no install.ps1 and no in-place partial overwrite), then tested with its own isolated router and real Claude process before selecting it as User CIEL_RUNTIME_HOME. The separately fixed external /work statusline template was preserved. The previous registered path is retained in the manifest. Existing live router processes were not restarted. New launches use the selected snapshot; currently running sessions keep their prior code.

The final installed Claude run compared live router ports 9385/9464/9489/9505/9524 before and after: PIDs 6032/51556/67368/93416/81772 unchanged. No inference is made about PID changes between earlier independent runs. No branch push or npm publish was requested in this continuation.
