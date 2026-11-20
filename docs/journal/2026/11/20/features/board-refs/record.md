---
type: feature
title: Every related source id is registered on the Work board (refs)
description: The side actor must register every id of the request it queues, the board has a refs field for all of them, and main, the side actor and REST read and edit it. Includes the rollout fixes found before install and the hotfix11 install on Andy, Celly, KOBE, Prody and Betty.
timestamp: 2026-11-20T22:40:00Z
tags: [work, side, refs, rest, prompt, rollout]
---

# Request

User, 2026-11-20 (Korean, paraphrased): on the Celly, Andy, KOBE, Prody and Betty sessions, change the side actor's default prompt and apply an emergency REST patch. When the side actor queues a request on the Work board, it must register the related ids it received (there can be several). Otherwise main cannot find the exact point it has to answer. The board must also get a field holding all related ids, so the side actor can read them.

# Change

Commits on feature/priority-models: 6720d3d (feature), 75e604b and 351fa9a (defects found before install).

Board (`work_plan_tree.py`):
- A node carries `refs`: ordered, deduplicated external ids, at most 32, each at most 64 characters.
- `ref` stays: older readers (status line, fork label, approval scope) read it. A board written before refs loads its `ref` as the first of `refs`.
- refs given without ref give the node ref = the first id (75e604b). An explicit empty ref stays empty.
- `append_refs` adds ids to an existing item without returning its goal to planning and without touching ref, so a side update never changes the approved scope.

Side actor:
- Decision field `refs`. A create, update or prioritize is refused with "decision refs required" when the event names a concrete message. Gate re-checks are exempt; they carry the node's refs into the check text.
- create stores the refs (first as ref); update and prioritize accumulate them with `append_refs`.
- The side projection (`side_workload`) includes refs.
- Default prompt (`side-intake.js` PROMPT = `DEFAULT_SIDE_INSTRUCTION`): new section "큐 등록 시 관련 id (필수 규칙)" after the classification list. It names the ids to give (target message first, thread root, parent, conversation, and any confirmed related message), forbids invented ids, and says ids written only in the body are not registered.
- 75e604b: the section first sat between "정확히 분류하십시오." and its list; it now follows the list.

Main and operators:
- MCP `work_plan_set`, `work_node_add`, `work_node_update` accept and store refs (75e604b: they dropped or ignored them before).
- REST: `/ca/work/api/v1/commands/plan-set|node-add|node-update` and the legacy `/ca/work/nodes/add|update` (75e604b) accept refs as a list.
- Web console: node editor and add form have a References field (one per line or comma-separated); the board shows all refs.

Rollout safety (75e604b, 351fa9a):
- The side mod reloads at a turn end, the router only at restart. A router from before refs refuses an unknown decision field. The claim now lists `decision_fields`; the mod sends a new answer with only those fields, and with no list it drops refs. A saved proposal is the router's own and is committed unchanged.
- "decision refs required" is a correctable refusal: the actor gets one correction naming refs, as for other shape errors.
- A proposal saved by an older router without refs is final and commits after the upgrade (understanding step and resolve skip the refs check for it).

Model-facing text touched in code: the side PROMPT literal and its Python copy (existing registry pair, kept identical by `test_side_instruction_view`), the `refs` schema description, and one sentence added to the existing correction literal in `side-intake.js`.

# Verification

[Evidence](/docs/journal/2026/11/20/features/board-refs/evidence/).

Fail-first, each on the code before its fix:
- `fail-first-tree.txt`, `fail-first-mcp.txt` (6720d3d tests on f932da4).
- `fail-first-rollout.txt` (6720d3d): REST plan-set refs gave ref '' (2 fails), claim had no `decision_fields` (KeyError), a legacy saved proposal was refused "decision refs required", the refs refusal got no correction, and refs reached a router that does not list them.
- `fail-first-mcp-tree.txt` (6720d3d): MCP work_plan_set stored `[]`; tree left ref ''.
- The Linux bundle on 75e604b failed 2 tests that became the fail-first for 351fa9a: the resumed proposal lost refs, and the ref schema contract changed.

After the fixes:
- New tests pass (`after-fix-new.txt`).
- Plugin: `claude plugin validate` passed; `claude plugin test` 89 pass, 0 fail.
- Windows, isolated config, affected bundle of 93 test files: 1616 tests, 0 failures, 1 error, 65 skipped ([bundle-windows.txt](/docs/journal/2026/11/20/features/board-refs/evidence/bundle-windows.txt)).
  - The one error is the test's own cleanup: `PermissionError [WinError 32]` deleting its temporary `work-parallel-p0.json.lock`, held by another process at that moment. The test's own assertions had passed. It passed on re-run with the new REST test: 4 tests, 0 failures ([bundle-windows-rerun.txt](/docs/journal/2026/11/20/features/board-refs/evidence/bundle-windows-rerun.txt)).
- Linux Docker, python:3.11-slim with pytest 9.1.1, archive of 351fa9a, 102 files: 1616 tests, 0 failures, 0 errors, 124 skipped ([bundle-linux.txt](/docs/journal/2026/11/20/features/board-refs/evidence/bundle-linux.txt)). This Docker run predates the shared test containers.
- Python 3.10.22 in Docker, the ten test files that own the changed modules: 295 tests, 0 failures, 0 errors, 13 skipped ([py310.txt](/docs/journal/2026/11/20/features/board-refs/evidence/py310.txt)).
- ruff on the 12 changed Python files and test files: all checks passed. `ciel_runtime.py` is unchanged at 4980 lines.

# Installed

Hash-checked overlay with backups (hotfix11), no process restarted. Each target was pre-checked: every file held its base bytes before anything was written
([precheck-local.txt](/docs/journal/2026/11/20/features/board-refs/evidence/precheck-local.txt),
[precheck-aap12.txt](/docs/journal/2026/11/20/features/board-refs/evidence/precheck-aap12.txt)).

- Local: Andy home, default home and global npm — 14 files each, 56 verified ([install-local.txt](/docs/journal/2026/11/20/features/board-refs/evidence/install-local.txt)).
- Celly: same stage, 14 files (included in install-local.txt).
- KOBE: same stage, 14 files ([install-kobe.txt](/docs/journal/2026/11/20/features/board-refs/evidence/install-kobe.txt)).
  - Bases: 0cb598b for 12 files; `work_parallel_mcp.py` and `work_parallel_receipts.py` already held 0be8eec's bytes (hotfix10) and are unchanged by this stage, so hotfix10 stays installed.
- Prody and Betty (.12): both packages auto-updated at 18:20Z to beta `0.2.51-beta.20261120-172521.0cb598b` (routers 9592 and 9564 report that version). The stage for them is a plain 0cb598b base, 16 files each, which also brings hotfix10's two files (`work_parallel_mcp.py`, `work_parallel_receipts.py`) they did not have ([install-aap12.txt](/docs/journal/2026/11/20/features/board-refs/evidence/install-aap12.txt)).

The 14 files: register.js, side-intake.js, side_intake_mcp.py, side_intake_settings.py, side_understanding_outbox.py, side_work_decision.py, side_workload.py, work_application.py, work_gate_check.py, work_http_controller.py, work_mcp_tools.py, work_parallel_workspace.py, work_plan_tree.py, work_web_ui.py. Hash of the installed side-intake.js: CF979CC8FEB04126 (HEAD 351fa9a).

Side prompt check on all five hosts: every stored side instruction is empty and none replaces the default (local and Celly configs: instruction_bytes 0, replace False; KOBE has no such config; .12 configs carry no work_side_intake section). So the new default instruction applies as written once the mod reloads.

# Open

- Router changes (claim `decision_fields`, refs validation, REST, MCP) take effect at each router's next restart. Until then a hot-reloaded mod drops refs for that router, so the side actor's queue items carry no refs yet on that session.
- Whether a live side event after the mod's reload actually lands refs on the board is not yet observed. Needs a Check on the sessions after reload/restart.
- Taken over from the salvage record (that record ended with an empty # Bundles section): the three bundle results are recorded in its [Verification](/docs/journal/2026/11/20/fixes/salvage-no-workspace/record.md#verification).