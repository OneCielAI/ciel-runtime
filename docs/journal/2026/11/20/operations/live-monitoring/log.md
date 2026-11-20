# Change log

## 2026-11-20

- 18:45–18:55Z: the user reported Andy and Celly frozen, both after the line "Prompt dropped by a hook: Owned Work notification retained outside main conversation". Both recovered by about 18:58Z. Read-only observations; the cause is not confirmed.
  - This PC: from about 18:47Z my own shell and PowerShell commands did not return (curl with a 20 s timeout, `Get-Date`). After I stopped my stalled checks and the f932da4 Linux Docker run, commands answered again at 18:55:03Z.
    - CPU load read 97% at about 18:56Z. At 18:59Z it was low: the top process used 1.35 of 24 cores.
    - My Windows bundle for 0be8eec (python 43224, started 17:59Z) is still running.
  - Celly router 9385: 569 log lines at 12:46 local, then none until 12:55. Andy router 9505: about 1,500 lines a minute fell to 65–300 between 12:47 and 12:53, and recovered at 12:54–12:55.
  - Celly main: the hook drop at 18:46:11Z, then a model request at 18:46:12Z with no answer until the user interrupted at 18:55:03Z. Then `work_host_page` timeouts and one ECONNREFUSED at 18:55:17Z (`/health` answered 200 at 18:55:15Z).
  - Andy main: the turn ended at 18:44:52Z with the upstream "blocked by our safety systems" API error. Only host notices follow until 18:55Z.
  - Upstream: at 12:45:01 local both routers logged `cielairouter_gateway_failure` (503), and Celly logged "no healthy gateway" with transport-disconnect retries.
  - The hook drop line is the designed handling of a Work fork's task notification (record.md, 11:25Z). In Celly it came one second before the stalled request. Neither this observation nor the source shows the drop causing the stall.

- 18:37Z tick (since 18:15Z):
  - Andy: 3 running, 1 reserved, 45 waiting, 22 submitted. No new failures or router ERROR lines.
    - n1174's recovery still holds its P3 slot (fix f932da4 waits for install). n2260 was refused again at 18:22:04Z on P3 capacity.
    - Finding `slot held, fork absent since 18:24: n2508 7a7cf0c4 P1 running`: not a defect. Native shows the fork `running` and fresh at 18:37:57Z; the row was briefly missing from one list.
  - Celly: 5 running, 7 waiting. Nothing new.
  - KOBE: ssh failed this tick.
  - Prody: 1 running, 5 waiting, 6 submitted. Main's own Python error only.
  - Andy_Main relayed five Andy observations (16:50–18:25Z). Read-only checks:
    1. The side's `understanding` led the posted reply. That is the 11-19 rule (0e0cb7e: one message per event, understanding then result). An `update` decision is always posted with its queue line, because `no_reply` applies only to `answer`. Not a defect against the stated requirements. The open Prody request (no thread reply for pure forward-to-Work) is waiting for the user's decision.
    2. Event b69915206b50 was processed twice.
       - Actor ade7618c (claimed 17:29:16Z) wrote its decision JSON at 17:33:13Z, but no understanding/resolve call followed. Renew calls stopped after 17:41 local+6.
       - Actor a8f1c6ea re-claimed it at 17:41:28Z and committed: understanding 17:43:58Z, resolve, reply. Only one thread post (154304).
       - Why the first actor's answer was not taken is not confirmed. Andy's main transcript has no rows between 16:49:13Z and 17:58:15Z, and the router log does not record the host's answer read.
    3. A fork Bash was refused "Work state unreadable (work_host_page was refused or failed)". That is the gate failing closed on a failed `work_host_page`. Andy's `work_host_page` failures (ECONNREFUSED, EADDRINUSE, ETIMEDOUT) are on record since 15:57Z; their cause is not confirmed.
    4. `work_side_handoff` and `work_parallel_status` were refused for side actors with "conversation identity is unavailable". These are main-session tools, and ciel-work hooks do not run inside forks it spawned ([[claude-plugin-own-fork-hook-skip]]). Not verified whether the side instruction leads the actor to them.
    5. Forks ended with an upstream "blocked by our safety systems" refusal; one side actor's first output line was Chinese and was not posted. Both are model or upstream output, not ciel-runtime.

- 18:15Z tick (since 18:00Z):
  - Andy: 45 waiting, 22 submitted, 1 reserved. No new failures or router ERROR lines.
    - Finding: `slot held by dead owner pid 14672: n1174 8b365b32 P3`. The sweep had released the reservation; the stranded recovery of n1174 (old lease d2e56870) re-reserved it at 18:01:46Z. The row is `deciding` and blocked: `stage_failed: workspace preparation/removal incomplete; retained`.
      - Read-only check of the recovery workspace. The manifest `status` is `preparing` and has been unchanged since 11-19 17:42:36Z (`git_dir` null). The worktree itself exists: it is registered, its toplevel is the target, HEAD is the manifest's own branch, and HEAD equals `initial_head` 8a1743e.
      - Cause, confirmed in source (0be8eec): `prepare` returns `_verify(manifest)` whenever a manifest exists, and `_verify` refuses any status other than ready/rebound. So a creation whose last manifest save was lost after `worktree add` can never complete. The recovery holds its P3 slot at every pump.
      - Effect: the parked P3 worker n2260 (lease 61e01208) was refused at 18:03:18Z with "active slot capacity reached for P3".
      - The interruption at 11-19 17:42Z itself is not recorded. The router log from that time has rotated, and the rotated file holds NUL bytes.
      - Fixed in f932da4. `prepare` now completes only an unbound fresh creation whose worktree verifies exactly and is still at its initial HEAD. Fail-first: the new test reproduces the same error on 0be8eec. The existing interrupted-before-add test still blocks.
  - Celly: 5 running, 7 waiting. No new failures or router ERROR lines. Main saw `work_host_page` connection-closed twice and one `no eligible candidate`.
  - KOBE: unchanged (2 running, 5 waiting, 1 accepted). n950 is still blocked and n964 still holds its slot (hotfix10 waits for a router restart).
  - Prody: unchanged (6 waiting, 6 submitted). No findings.

- 18:00Z tick (since 17:50Z):
  - This PC (Andy and Celly) booted at 17:54:38Z. I did not restart it, and the cause is not recorded here. My own background test runs for 0be8eec stopped with it and were started again.
    - After the boot, Celly's router 9385 was spawned at 17:55:34Z and Andy's router 9505 at 18:00:01Z, and new Claude processes started. Both routers load the hotfix9 and hotfix10 overlays from disk. `/health` still shows the package version (92ef95b and c955630), because the overlays do not change `package.json`.
  - Andy: 45 waiting, 22 submitted, nothing running. The startup sweep released reservation 8b365b32 (n1174), whose owner was gone. n1856 is still blocked. No new failures or router ERROR lines.
  - Celly: 4 running, 6 waiting.
    - n1078: new P0 lease c8a81bf5 is bound and running; the old lease 6f7a6a32 was orphaned by the startup sweep. The 5-minute "resume was refused" loop on 6f7a6a32 has ended.
    - n1141 (lease a90cf83f) was finalized (approved) after the restart. Before that it was `submitted` with a completion receipt from 15:59:12Z, but no review row for about two hours. Its native status stayed `waiting` from 15:59:36Z. The fork transcript ends at 15:59:26Z with its final text.
      - Native idle publication (`NATIVE_IDLE_STATUSES`) and the turn-end reconcile (`parallelTerminal`) both exclude `waiting`, so nothing could publish the review before a restart.
      - Why the engine reported `waiting` for a fork that had ended its turn is not confirmed. This is the only such case in the census: Celly n1141. Andy and KOBE have no submitted lease with a completion receipt and native `waiting`.
    - Router ERROR at 11:57:59 local, twice: `ChannelFileLockBusy` on `work-parallel.json.lock`, in `project` → `WorkParallelStore.read`. This was during router startup (`health_busy` keep-alive at 11:57:51). The lock holder is not established.
    - The tick could not read `work-parallel-coordination.json` once (OSError).
  - KOBE and Prody: ssh failed ("Connection timed out during banner exchange"). TCP 1004 is open on both, and ICMP gets no reply. No state was read.
  - Retried at 18:07Z for Prody and 18:14Z for KOBE (since 17:50Z and 17:35Z):
    - Prody: 6 waiting, 6 submitted. No findings, failures or router ERROR lines.
    - KOBE: 2 running, 5 waiting, 1 accepted. n950 is still blocked on unfinished children. No new failures or router ERROR lines.
      - n964 (1f7c4d9b) still holds its P1 slot, with run `terminal` and native `failed`. KOBE's router has not restarted since the hotfix10 overlay, so 0be8eec is not running there yet.
      - Main's tool errors are Walkie answers (404, 403, one unanswered send) and the goal-selection guard.

- 17:50Z: Prody added to the monitor (user order "지속적으로 celly, andy, kobe, prody 모니터링하면서 발견되는문제들을 수정해나가"). `run.sh` now runs `monitor_host.py` on .12 over `ssh -p 1004` too. The copy goes to `C:\Windows\Temp\monitor_host_prody.py` and is deleted after the run. Read-only.
  - First pass (last hour): scope claude:ef61c539…, 1 running, 5 waiting, 6 submitted. No failure journal and no stranded store yet (files absent). No router ERROR lines. Three side originals were forwarded to main.
  - Finding `slot held, fork absent since 16:38: n1164 118cdb2f P2 running`. Not a defect:
    - The fork's question report succeeded at 16:36:26Z (`waiting_input`, slot released).
    - At 17:49:20Z the host's parked resume sent "the wait on your Work item n1164 is cleared". That is the reacquire → running path, so the lease is legitimately running with a slot.
    - The 16:38:14Z `missing_since` is from before the resume. The fork has been running since 17:49 (reads only so far).
- 17:46Z: no-workspace host salvage fix 0be8eec installed (hash-checked, backups `*.backup-hotfix10`, every target held 2cb27cc's bytes first): local (Andy home, default home, global npm), Celly (elevated) and KOBE (ssh). Two files, all verified. No process restarted. The Windows and Linux bundles are still running, so there is no beta push yet.

- 17:35Z tick (since 17:15Z):
  - Andy: 2 running (was 3), 1 reserved, 45 waiting, 22 submitted. n1856 is still blocked. No new failures or router ERROR lines. `work_host_page` failed twice more (ECONNREFUSED, EADDRINUSE).
  - Celly: 3 running, 6 waiting, 1 submitted. n1141 is still unpublished.
    - One router `channel_stdin_proxy_blocked ... stale_unconfirmed` (11:21 local).
    - The parked lease 6f7a6a32 (n1078) repeats "a resume was refused within the last 5 minutes". Same P0 open-child shape; 2cb27cc is installed and waits for a restart.
  - KOBE: 2 running, 5 waiting, 1 accepted. Two more `channel_stdin_proxy_blocked` lines.
    - New defect, n964 (lease 1f7c4d9b, no workspace): the lease is `running` with a P1 slot, but its run is `terminal` and native is `failed` since 17:19:55Z.
      - The fork transcript shows every turn since 17:16:11Z ending in `API Error: This request was blocked by our safety systems`. That covers the mod's four exit-contract prompts and main's two resumes.
      - Main tried `work_parallel_event` `help`/`native_exit`/`stopped`/`cancel`, all refused "unsupported parallel event". TaskStop answered "not running (status: failed)".
      - Cause, confirmed in source: `host_salvage` refused any lease without a workspace ("unknown salvage workspace"). `parallelEnsureExitContract` swallows that refusal. So a fork reserved without a workspace that ends without a report keeps its slot forever. KOBE reserves every fork without a workspace (session folder is not a git repo).
      - 1446629 (11-19) allowed no-workspace leases on the review path only.
      - Fixed in 0be8eec: fail-first reproduced on bd21399; the bundle is running.

- 17:35Z: beta 0cb598b published as npm `0.2.51-beta.20261120-172521.0cb598b`. Its two files match the hotfix9 installs on local, Celly and KOBE. No session restarted.
- 17:22Z: Andy's Work prompts reset to defaults (user order "andy 세션 프롬프트 업데이트해줘 기본값으로"). Done through Andy's live router 9505 (c955630), with compare-and-set on the fingerprints just read:
  - `workflow-prompt-reset` for `gate_actor` (374721ea… → default df8a6cc3…) and `gate_recheck` (its override already equalled the default). Both 200.
  - `settings-update` `work_side_intake_instruction` = "" (it was 15104 bytes, the c955630 default text stored with `instruction_replace=true`). 200.
  - Read back: 0 non-default prompts, stored instruction 0 bytes, settings fingerprint e395ed07… → 74643763…. The workspace config no longer has `instruction`/`instruction_replace`, and `workflow-prompts.json` holds no prompts (its history records both resets, source `rest`).
  - Nothing else changed. `work_side_intake_session` stays `fresh`. The router was not restarted. The managed prompts and the side instruction are read per request and per side event.
- 17:20Z: Betty (.12 u42, read-only, user request). Version `0.2.51-beta.20261120-051824.6e0818e` (router 9564 `/health`).
  - Prompts: 30, all default, no history. 6e0818e predates the three prompts added in 03e064d. Its 30 fingerprints equal Prody's.
  - Settings fingerprint 9df9eb78…. Side instruction empty (built-in default); `work_side_intake_session` `fork`.
  - Differences from Prody:
    - SSE injection mode and side intake type: `session` (Prody `btw_with_mcp`); side intake disabled.
    - Reply connectors empty.
    - Every model `inherit`.
    - P1/P2 limits 5/3 (Prody 2/1); orphans `live=1`.
    - `work_completed_auto_delete` false; `claude_background_agents_enabled` false.
- 17:19Z: P0 reacquire completion fix 2cb27cc installed (hash-checked, backups `*.backup-hotfix9`, every target held 95cf25c's bytes first):
  - Local: the Andy home, the default home and the global npm package.
  - Celly: elevated overlay. KOBE: overlay over ssh.
  - Two files each, all verified. No process was restarted.
- 17:15Z tick (since 17:00Z):
  - Andy: unchanged (3 running, 1 reserved, 45 waiting, 22 submitted). No new failures or router ERROR lines. Main called host-only `work_side_texts` and was refused (by design). Two side originals were forwarded.
  - Celly: 3 running, 6 waiting, 1 submitted.
    - n1078's resume was refused with `no active slot` (17:05:49Z). This is the third lease with the P0 open-child shape (with n1143 and n1156), covered by 2cb27cc.
    - Two `ChannelFileLockBusy` on a side-inbox lock (11:13 local).
    - n1141 is still unpublished. Two `git.exe` processes started at 15:48:56Z are still alive under an account this monitor cannot read. Their start time is 4 s after n1141's fork ran its `git fetch` command at 15:48:52Z, which went to the background. That matches a background task the gate stopped the fork from ending. Ownership is not confirmed.
  - KOBE: unchanged (1 running, 5 waiting, 1 accepted; n950 blocked). Seven side-original forwards, two of them `already` duplicates.

- 17:15Z: Prody Work prompts and settings compared with Andy (request relayed by Andy_Main, read-only). Method: loopback GETs to Prody router 9592, `/ca/work/api/v1/workflow/prompts` and `/settings`. Only fingerprints and sizes were printed.
  - Prompts: all 33 use defaults, with no history. Andy differs on `gate_actor` (override 374721ea… vs Prody default df8a6cc3…). Andy's `gate_recheck` override is identical to the default.
  - Settings fingerprints differ (Prody 20de7c6c…, Andy e395ed07…).
    - Prody's stored side instruction is empty, so it uses the built-in default.
    - Andy's workspace config stores `instruction` (15104 bytes, fc5146173c08c4ee) with `instruction_replace=true`. That text is byte-identical to c955630's built-in default.
    - `work_side_intake_session` is `fork` on Prody and `fresh` on Andy.
  - Defaults by source:
    - 79673d9 adds `side_complaint_and_urgency`, `side_complaint_notice` and `mod_side_ask_main_wake`. The other 30 default fingerprints are unchanged.
    - The built-in side instruction went from 15104 to 15115 bytes (6096ccfa…), adding `, complaint` to the decision fields.
  - Consequence for Andy after a restart (from source, not yet observed): the stored replace-mode text still lists the fields ending in "no_reply. 다른 필드는 없습니다." The appended `side_complaint_and_urgency` asks for `"complaint": true`. The two instructions conflict. Nothing was changed.

- 17:00Z tick (since 16:40Z):
  - Andy: unchanged (3 running, 1 reserved, 45 waiting, 22 submitted); n1856 is still blocked. No new failures or router ERROR lines. Main's errors are its own (Python script errors, Walkie 403, "n538 is not a goal").
  - Celly: 2 running, 6 waiting, 1 submitted. No new failures or router ERROR lines.
    - n1156 (lease c171a2d4, P0 lane): main's resume was refused twice with `no active slot` (16:52:46Z, 16:53:43Z). Same shape as n1143: P0 send child `slot_acquired` 16:46:22Z, worker question 16:50:12Z. Covered by 2cb27cc (not installed yet).
    - n1141 (lease a90cf83f) is still submitted with no review row, native `waiting`. Read-only evidence from the fork transcript:
      - Two Bash calls (15:48:52Z `git fetch`+`git show`, 15:54:54Z a recursive `grep -rl` under `.local`) did not finish within the tool timeout and were moved to the background (b7c810nhs, bz6e3m0uc).
      - At 15:57:09Z the fork called TaskStop on both. `ciel-work-gate` denied each: `TaskStop is not available to this Work fork` (design 11-12 row 11: a worker must not stop other agents).
      - No completion notice for either task is in the transcript, which ends with the fork's final text at 15:59:26Z.
      - n1156's fork has the same pattern: TaskStop and TaskList denied at 16:32Z, native `waiting` since 16:51:21Z after its last answer.
      - The engine type doc defines `waiting` as held on background work the agent owns. `_publish_native_idle_review` and the turn-end reconcile accept only idle/completed/failed/killed. So the review is not published while a fork's own background tasks run.
      - Whether those background processes are still alive is not confirmed: Celly's processes belong to another account and their command lines are not readable here.
  - KOBE: 1 running, 5 waiting, 1 accepted; n950 is still blocked. Two router `[ERROR] channel_stdin_proxy_blocked ... prior_submission_failed detail=stale_unconfirmed action=skipped` (08:54, 08:59 local). Same class as Celly's 11:18Z line (not yet investigated). Five side originals forwarded (two `already` duplicates of the same event).
  - 2cb27cc: the Windows bundle (81 files) and the Linux 3.11 bundle (90 files) are still running.

- 16:40Z tick (since 15:40Z):
  - Andy: 3 running, 1 reserved, 45 waiting, 22 submitted. n1856 is still blocked. New `decision_refused_stop` 487db395 (n2471); that review is now finalized/approve.
    - One router `PermissionError [WinError 5]` at 10:08:30 local, on the `work-parallel-stranded-v1.json` replace. Its trace has no `replace_with_retry` frame, so the running router predates the 0dc57b0 overlay (same as the 13:49Z entry).
    - Main: n2472 got a new lease 6c99a6a9 (15:59Z refusal names it, so the 15:55Z orphan path worked).
    - `work_host_page` failed four times with ECONNREFUSED/EADDRINUSE/ETIMEDOUT (15:57–16:26Z). Cause not measured.
  - Celly: 1 running, 6 waiting, 1 submitted. No new failure-journal entries or router ERROR lines.
    - New finding n1141 (lease a90cf83f): completion receipt 15:59:11Z, lease submitted, no review row. The fork's native status has been `waiting` since 15:59:36Z, and its transcript ends at 15:59:26Z with a final text answer. `_publish_native_idle_review` publishes only for idle/completed/failed/killed, and the turn-end reconcile treats `waiting` as not terminal. So no path has published it yet. Why the engine lists a fork that has ended its turn as `waiting` is not confirmed (the type doc says `waiting` is held on background work, plan approval or an Agent call).
    - 15:47:22Z `work_parallel_run` n1141 was refused with `coordination persistence failed; reservation released: ... assignment already exists with a different manifest`. The same item was reserved and bound at 15:47:14–21Z by the P0 lane, so this was a second dispatch of the same item, refused by the existing-run guard.
  - KOBE: 1 running, 5 waiting, 1 accepted. n950 is still blocked on unfinished children. Six side originals were forwarded to main (`invalid_answer` path); one was `already`. Main's other errors are Walkie flow/QA refusals and a GitHub token failure (not Ciel). 16:36:14Z "auto continue could not read the Work status" (seen before).
  - P0 reacquire completion fix 2cb27cc: fail-first recorded; Python 3.10 changed modules 228 tests, 0 failures. Windows bundle and Linux 3.11 bundle still running.
  - .12 (user question): Betty runs `0.2.51-beta.20261120-051824.6e0818e` (package.json 06:20:19Z, launcher 06:26:03Z, router 9564 `/health` same). Prody runs `0.2.51-beta.20261120-152815.79673d9` (package.json 16:25:12Z, launcher 16:27:03Z, Claude 16:32:27Z, router 9592 `/health` same).
    - Prody's npm debug log at 16:25:07Z records `npm install --force --global @one-ciel-ai/ciel-runtime@beta`, exit 0. Ciel's own self-update and upgrade build `npm install -g [--prefix] [--allow-scripts] @one-ciel-ai/ciel-runtime@<exact version>` (`npm_global_install_command`). So this install did not come from Ciel's updater. Who ran it is not established from the host.
    - Betty's npm logs before 10:56Z have rotated out, so how 6e0818e was installed at 06:20Z is not recorded.

- 15:40Z tick (since 15:16Z):
  - Andy: 2 running, 1 reserved, 46 waiting, 23 submitted (one fewer running and one more submitted). n1856 review is still blocked on `unknown Work item`; n1174 reservation still has no matching run or native identity. No new failure-journal entries or router ERROR/uncaught lines. At 15:21:16Z the hooks worker crashed, `ciel-work` unloaded, and `ciel-work-gate` fell back three times. The next two board updates failed because there was no conversation identity. `ciel-work` reloaded at 15:22:02Z. A parked P3 worker was refused on slot capacity. The mod directory in the running process's launch arguments is the install target updated at 15:20:49Z. The transcript proves a crash and later reload, not that the overlay caused the crash; the same crash was recorded earlier that day.
  - Celly: 2 running, 5 waiting (unchanged). No new failure-journal entries. One new `/ca/mcp` router ERROR, `ChannelFileLockBusy` on a side-inbox lock at 15:23:33Z. Lock holder not established. Main's three attempts to resume n1143 (15:20:43Z, 15:30:37Z, 15:39:59Z) were refused with `no active slot`. The lease is `waiting_input`, slot false, with an older P0 send still `slot_acquired` since 14:49:16Z. Router reacquire returns that same open send without taking a slot; neither mod resume path calls the router's `p0_reacquire_complete`. The send census is 12 open on Celly (3 still waiting), 1 on Andy (orphaned) and 2 on KOBE (one still waiting); all 15 were `slot_acquired`. `ciel-work` reloaded at 15:22:00Z.
  - KOBE: reachable again; 2 running, 5 waiting, 1 accepted. n955 now waits without a slot; lease prefixes previously reported for n953/n957 are no longer present. New finding n950: approval committed to the review, lease accepted, but board finalization blocked on unfinished children. Two new `decision_refused_stop` entries; main saw a stale finalization version, a conflicting review decision, two existing-lease guards and two goal-not-approved guards. Three distinct side originals were forwarded, with one additional `already` notice for the same event. No new router ERROR/uncaught lines. `ciel-work` reloaded at 15:26:35Z.
  - All three transcripts confirm hot reloads after the previous overlay; this does not establish that their running Python routers picked up the new code or that the end-to-end fix is active. No live session or router was restarted by the check.
  - Follow-up at 15:55Z: Andy n2472 dispatch was refused at 15:53:40Z while its old lease was open. Startup orphan sweep recorded `orphaned`, slot false, at 15:54:27Z; the attempted SendMessage to the previous fork was refused at 15:54:44Z. Relayed the observed transition and the existing dispatch path to Andy's session. No lease was manually released.

- 15:40Z: side complaint and P0 inheritance fix (03e064d) is installed on local, Celly and KOBE (hash-checked, backups `*.backup-hotfix8`). Beta 79673d9 is published as npm `0.2.51-beta.20261120-152815.79673d9`, and its ten files match the installs. No session was restarted.
- 15:16Z tick (since 15:00Z):
  - Andy: unchanged (3 running, 1 reserved, 46 waiting, 22 submitted); n1856 still blocked. No new failures or router ERROR/uncaught lines.
    - 15:01–15:02Z: five `work_node_update status=done` calls (n2304–n2308) were refused with "Work completion waits for this item's parallel validation, review and cleanup". This is the existing completion gate; nothing was applied.
  - Celly: 2 running, 5 waiting. No new failures or router ERROR/uncaught lines. One side-failure forward.
    - 15:03:36Z: `work_parallel_run` n1152 (P1 worker) was refused with `unknown P0 assignment`. That text is raised by `p0_launch_assignment` (`work_parallel_mcp.py`) when the intent/lease/dispatch-name pin does not match a reserved P0 launch. Main's `work_parallel_run` surfaced it, so the dispatch went through the P0 lane. Not confirmed which pin failed.
  - KOBE: unreachable this tick. ssh first got "lost connection", then "Connection timed out during banner exchange" on two retries, and no state was read. The previous tick's KOBE findings (n955, n953, n957 leases running with slot while their runs are terminal or waiting; artifact-format report refusals) stay open.
  - Linux Docker run of 03e064d is still in progress.

- 15:00Z tick (since 14:35Z):
  - Andy: unchanged (3 running, 1 reserved, 46 waiting, 22 submitted); n1856 still blocked. One side-failure forward. Main's own regex error. No router ERROR or uncaught lines.
  - Celly: 2 running, 4 waiting, 1 submitted. No new failures or router ERROR/uncaught lines.
    - Main got refusals on its own calls:
      - `P0 reacquire parent authority` on SendMessage to n1128's parked worker (14:36Z, 14:41Z).
      - `P0 intent transition` on `work_parallel_run` n1151 (14:47Z).
      - `plan version`, `stale plan version`, `no active slot`, `no eligible candidate`, `unsupported parallel event`.
      - Walkie `Conversation access denied`.
    - The two P0 refusals are new refusal texts on this host. Their cause is not investigated yet.
  - KOBE: running leases rose to 7 at 14:36–14:45Z. Main's P0 goals for incident 153865 started six P0 forks (n950, n953, n955, n956, n957, n18, n20).
    - Finding `slot held, fork absent since 14:46: n955 a1e68f4b P1 running`: the run holds a `question` exit at 14:44:16Z, and the lease is still `running` with slot. The fork's question report returned `waiting_input` with slot released. The ledger row read later still shows `running` / slot true. Not confirmed why.
    - Fork n953 (af55b265):
      - 14:42:09Z: API error "blocked by our safety systems".
      - Then 6 completion reports refused with `completion artifact SHA256 required`. It passed paths or `sha256:`/`#sha256=` forms. `digest()` accepts only a bare 64-char lowercase hex string.
      - Then a handoff without `workspace`/`next_step`/`blockers`, answered `handoff done` as an error. That is the KeyError text of the missing `blockers` field.
      - Run `terminal`/native `failed`, lease still `running`/slot.
    - Fork n957 (ae94b273): API errors "circuit breaker is open (HTTP 503)" and "all targets were skipped by pre-dispatch filters". Two exit-contract prompts. Run terminal/failed, lease running/slot.
    - Report refusals since 11-18 (read-only fork transcript census):
      - KOBE: 72 accepted. Refused 48 times in 25 forks with `completion artifact SHA256 required`, and 28 times with `worker may only report question, completion or handoff`.
      - Andy: 81 accepted. 61 refusals in 34 forks with `completion artifact SHA256 required`, 44 with the event-name refusal, and 31 with `completion text`.
      - Both hosts have one `handoff done`.
    - Main: `TaskStop`/`No task found` for two P0 forks; `mod_stop_unavailable` at 14:37Z; `work_host_page: ECONNREFUSED` at 14:49Z.
  - Side complaint/P0 change 03e064d:
    - Windows bundle 847 tests, 0 failures, 55 skipped.
    - Real claude.exe 2.1.329 load passed.
    - All three installs hold the 3d42d1d bytes for the 10 changed files.
    - The Linux run is in progress.

- 14:35Z tick (since 14:16Z):
  - Andy: unchanged (3 running, 1 reserved, 46 waiting, 22 submitted); n1856 still blocked. Nothing new.
  - Celly: 3 running, 4 waiting.
    - No new failures and no router ERROR or uncaught lines.
    - Main's tool errors were its own commands: shell exits, cp1252 encoding, git ignore, ripgrep timeout. There were also two correct host refusals: `work_side_status` is host-only, and n1149 already has a lease.
  - KOBE: 2 running, 3 waiting.
    - One side-failure forward.
    - 14:32:19Z `mod_stop_unavailable`: the Stop hook's board read missed its 10 s deadline (`STOP_READ_MS`, register.js). Celly showed the same notice at 14:01Z. These are single occurrences. Why the read was slow is not measured.
    - Main's own Python cp1252 print errors.
  - Reported to Andy_Main for Betty 153842, from 6e0818e source:
    - A claimed restart waits only for (a) no unconfirmed submitted channel input (`restart_protected`, no time limit) and (b) turn end or 900 s. Work leases and forks are not part of the condition.
    - `/exit` releases a deferred request (`after_child_exit`).
    - An in-place restart keeps launcher pid 760, so the unbound reservation ad958b5c stays live-owned and is not released. This is open as a product gap. Andy 8b365b32 has the same shape.

- 14:16Z tick (since 13:55Z):
  - Andy: unchanged (3 running, 1 reserved, 46 waiting, 22 submitted); n1856 still blocked. No new failures, router ERROR/uncaught lines or main tool errors.
  - Celly: 2 running, 4 waiting. No new failures or router ERROR/uncaught lines.
    - 14:01:54Z notice "auto continue could not read the Work status". The router logged the `work_status` call at 08:01:23 local with no error after it. The reason for the read failure is not confirmed.
    - 14:08:38Z: a turn-end event was forwarded to main as `superseded` (existing quarantine path).
  - KOBE: 2 running, 3 waiting; nothing new.
  - The affected-bundle rerun for the side complaint/P0 change was still running at this tick.

- 13:55Z tick (since 13:36:29Z):
  - Andy: 3 running, 1 reserved, 46 waiting, 22 submitted (unchanged); n1856 remains blocked. No new entries in the Work failure journal or main tool errors. At 13:49:24Z, `/ca/mcp` logged one uncaught `PermissionError [WinError 5]` writing `work-parallel-stranded-v1.json` via `_stranded_roll_forward` → `block` → `_change`. This is the same write path fixed in 0dc57b0. The fix is on disk; the running process predates the overlay, and this trace has no `replace_with_retry` frame.
  - Celly: 2 running, 3 waiting (unchanged). The previous n1143 missing-fork finding cleared after main resumed the worker. No new Work failure entries or router ERROR/uncaught lines. Main had two tool errors: Walkie rejected operation arguments (13:49:09Z) and PowerShell rejected a redirection without a target (13:49:32Z).
  - KOBE: 1 running, 3 waiting, 1 submitted (previously 2 running and 4 waiting). No new lease finding, Work failure-journal entry, router ERROR/uncaught, or actual main tool error. Four side notices represent three distinct originals forwarded and one already-delivered notice. A read-only backup-mode copy of recent receipts (removed after inspection) showed 24 of 26 `invalid_answer` failures over three hours had `side answer carries no decision JSON`. For one newly forwarded gate event, a decision JSON appears before another tool call but the final answer is prose; in other inspected events the final answer is prose without a decision. The sampled transcripts show no received corrective message; why the correction was not delivered is unconfirmed.
  - No live session was restarted or changed by this check.

- 13:36Z tick (since 12:15Z):
  - Andy: 3 running, 1 reserved, 46 waiting, 22 submitted (unchanged); n1856 still blocked.
    - New router lines are WARN only: SSE disconnects, and an upstream 503 "all targets were skipped by pre-dispatch filters" at 07:19 and 07:24 local.
    - Tool error 12:50:24Z: `work_side_handoff: invalid side receipt identity`. Main passed the 12-character event ref from the notice `Ciel side asks main (event 132ea9c9a6a9)` as `event_id`. The notice offers no full id and needs no handoff, so this is a wording gap, not a lost event. It is covered by the complaint/ask_main change under test, whose wake says "act on it now" instead of "released recorded wait".
    - Reservation 8b365b32 (n1174 P3) is still held. The owner PID is alive, and there is no dispatch name or coordination run. Reported to Andy_Main for Betty 153726; no state changed.
  - Celly: 2 running, 3 waiting.
    - Finding: n1143 (lease e5b6cdd7, P0 lane) was absent from the list from 13:25:53Z. Its fork transcript ends at 13:24:13Z with `API Error: 503 ... all targets were skipped by pre-dispatch filters` (stop_sequence). That is the same upstream 503 Andy logged.
      - Main resumed the worker with SendMessage at 13:38:32Z, and the fork continued at 13:38:45Z.
      - The native list showed no terminal status, so no exit-contract message was sent. Main noticed only through the dispatch check.
      - Not confirmed: whether the host should treat this as terminal by itself.
    - New `[ERROR] router_post_uncaught ChannelFileLockBusy` ×5 at 06:39–06:40 local, all on `runtime-inputs.jsonl.lock`. The path is `/v1/messages` → `body_with_pending_channel_messages` → `ChannelWakeClaimRepository.prompt`, which waits on the runtime-input lock. Earlier ticks saw the same class on other locks; the holder is still not identified.
  - KOBE: 2 running, 4 waiting; no lease findings.
    - 13 side-failure forwards since 12:15Z, all `invalid_answer · 1회 시도`: 11 gate re-check or turn-end events and 2 Walkie messages.
    - Not confirmed: the refusal reasons. The forward text carries only the category, and the receipts under `workspaces\651d879f6c68` are ACL-denied to the monitor account.
  - The vanished-fork review gap (0dc57b0) shows no new case. KOBE n878 and n880 still wait for KOBE's restart.

- 12:30Z: vanished-fork review and sidecar replace-retry fixes (0dc57b0) installed on local, Celly and KOBE (hash-checked, backups `*.backup-hotfix7`). Beta eab2a18 published as npm `0.2.51-beta.20261120-122502.eab2a18`; its five files match the installs. No session restarted.
- 12:15Z tick:
  - Andy relaunched 12:12Z (c955630); 3 running, 1 reserved, 46 waiting, 22 submitted. The launch sweep orphaned 4 leases (3 P1 running, 1 P3 reserved).
  - Celly: 4 running.
    - New: 4 × `router_post_uncaught ChannelFileLockBusy` at 12:03:16Z–12:08:31Z. One was on `work-plan.json.lock` (work session 909aa6ee…), three on side-inbox event locks.
    - A `work_parallel_run` was refused at 12:10:13Z with `workspace_prepare_retained ... channel file lock acquisition busy`.
    - Confirmed: `msvcrt.locking(LK_LOCK)` gives up after 10 one-second attempts, so some holder kept these locks for more than 10 s.
    - Not confirmed: which code path held them.
  - KOBE: unchanged; n878 and n880 are still without reviews.
- 12:14Z: user question "어떻게 앤디 세션이 워크모드가 저렇게 그냥 멈춰버릴수있지?". Read-only check of Andy:
  - Main transcript: 11:56:22Z `ciel-work: ciel-work was unloaded: it crashed the hooks worker`, then 12:04:44Z `Unknown command: /work-mode`. With the mod unloaded, none of its hooks run: no pump, no native observation, no review wake, no fork gate.
  - Same notice earlier: Andy 04:08:09Z; Celly 11-19 08:59:03Z and 11-20 06:42:51Z; KOBE 11-18 12:34:33Z, 17:26:43Z and 17:43:06Z.
  - Andy's ciel-runtime-router MCP log: 515–685 tool calls per minute 11:53–11:55Z, of which 461–621 were `work_host_page`. The previous Andy process logged 288–358 per minute before its 10:13Z exit. Celly's current session: 64–119 per minute.
  - Andy's 11:52:34Z notice `Parallel read failed: EADDRINUSE` came 4 minutes before the unload.
  - Router timeline (local -6):
    - 06:11:49 `router_lifetime_stopped reason=claude_exit`.
    - Andy relaunched at 12:12:13Z under the same conversation. The router restarted at 06:12:15 and 06:12:23 local, and the launch orphan sweep released 4 leases.
    - The `/health` miss was during that relaunch. At 12:13:56Z the router answered with c955630, pid 99752.
  - No Windows Application Error or WER event 1000/1001 for claude.exe between 11:55 and 11:58Z.
  - Not confirmed: what the hooks worker died of. The engine's notice carries no reason, and no other log was found. The `work_host_page` volume is the measured anomaly, and the claim that it caused the crash is unproven.
- 11:55Z tick:
  - Andy: unchanged lease counts.
    - New notice at 11:52:34Z: "Work reconciliation: Parallel read failed: EADDRINUSE: Unable to connect".
    - The router log shows no error then.
    - The router answered 500–700 `/ca/mcp` POSTs per minute (05:48–05:55 local), mostly `work_host_page`. Ephemeral port exhaustion on the client side is a hypothesis, not confirmed.
  - Celly: 3 running, no findings.
  - KOBE: n878 and n880 are still without reviews, waiting for the vanished-fork fix. KOBE has no `work-parallel-stranded-v1.json` yet, which is expected: the store is created on first write.
- 11:40Z tick:
  - Andy: 46 waiting, 22 submitted, 3 running, 1 reserved; n1856 still blocked.
    - New: `router_post_uncaught PermissionError [WinError 5]` at 11:38:32Z, raised by `os.replace` of `work-parallel-stranded-v1.json` in `_stranded_roll_forward`. That call was writing `stage_failed` after `prepare` raised `workspace preparation/removal incomplete; retained`.
    - The stranded and finalization stores have no `PermissionError` retry around `os.replace`; the ledger, native, P0 and receipts stores retry 20 times.
  - Celly: restarted again (scope b86a350f); 2 running, 1 waiting; no findings.
  - KOBE: n878 is still without a review, and n880 (lease edf577f8) has the same shape: native `failed` 11:19:59Z, receipt 11:27:35Z, listed idle 11:28:39Z, no review. Covered by the vanished-fork fix under test.
- 11:25Z: First passes of the read-only monitor over Andy, Celly and KOBE. Confirmed a vanished-fork review gap on Celly (n1070, n1135; published only by Celly's 11:14Z restart) and KOBE (n878, open). Periodic monitoring scheduled.

(Fill-in note: the ticks below were written during the day above this line in the same file; this closing entry was added last.)
- 22:40Z note (not a monitor run; the session-only cron is still to be recreated after the restarts):
  - Board refs change (6720d3d, 75e604b, 351fa9a) installed by hash-checked overlay (hotfix11) on Andy (3 roots), Celly, KOBE, Prody and Betty. Record: features/board-refs. Router parts take effect at each router's next restart; mod parts reload at a turn end.
  - Prody and Betty had auto-updated at 18:20Z to beta 0.2.51-beta.20261120-172521.0cb598b before this overlay.
  - Andy_Main reported at 22:3xZ three observations after the ~21:32Z PC restart: n2453 (goal n1445) resumed, its worker's last line is an upstream "all targets were skipped by pre-dispatch filters" error, and the goal still shows "workspace preparation failed and its folder was kept"; n1962's resumed worker was refused "invalid or revoked worker capability" and the host assigned the same item a new P0 fork; n1953 P0 still has an open lease of an ended process. Read-only investigation pending.
  - Docker on this PC: 9 containers. All test containers came from Andy's Walkie Work forks (`docker run -d --rm --name <item>-...`). The 4 exited ones (walkie-n2453-bd49-pg/redis, walkie-n2453b-2043-pg/redis) were removed on the user's order. The 4 running ones (n2453-base-pg/redis, n2508-e2e-final-pg/rd) belong to two still-working forks and were left alone. Andy applied a fixed-instance rule (walkie-test-pg, walkie-test-redis, per-item database and redis db number) and told the running forks. ciel-runtime's Linux tests now reuse two fixed containers (ciel-runtime-test-py311/py310) instead of one container per run. Unused volumes: 363 (about 16.9 GB); not deleted, pending the user's decision.

- 22:57Z note (not a monitor run):
  - Prody 154103 decided by the user: forward-only side decisions post nothing. fc9ada9, installed as hotfix12 on Andy, Celly, KOBE, Prody and Betty (record: fixes/forward-no-reply). Router restart still pending per host.
  - User asked about "Prompt dropped by a hook: Owned Work notification retained outside main conversation" on Celly: the designed drop of a host-owned fork's native task notification (`ownedWorkNotification`, register.js:1931-1936); the fork's result reaches main through its durable receipt. Census of Celly transcripts (read-only): session b86a350f has 101 such lines for 39 task ids, 20 of them repeated, one 6 times; the current session 9288dece has 62, two task ids resolved, last at 22:40Z. The repeated drops of one notification are recorded here; why the engine redelivers is not confirmed.
  - Andy_Main relayed a Walkie request (154648/154643): accept forwarded loopback Work API requests carrying a valid admin token while the router stays on 127.0.0.1, and confirm how the token reaches the runtime. This changes an external access surface, so it is waiting for the user's decision; nothing changed.
