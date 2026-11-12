---
type: e2e-journal
title: Owned main-review E2E attempts after the reboot
description: Owned real-console main-review E2E attempts on 2026-11-11 and 2026-11-12. The early attempts were stopped or diagnostic. run-20261111-213818 on freeze eab3ad33 passed 30/30, with one product display defect observed. run-20261112-002502 on the final product bytes passed 31/31, including the hidden predecessor check. Records harness fixes, the deterministic gate design and what the durable state showed.
status: passed
timestamp: 2026-11-12T10:05:00Z
tags: [work-parallel, main-review, e2e, owned-run, restart-recovery]
---

# Outcome

Final 2026-11-12: `e2e-source/run-20261112-002502` (final product bytes, runner `345a7ebe`) passed 31/31 and supersedes 213818. The predecessor rework row no longer shows. See "Final run on the released product bytes" below.

Update 2026-11-12: `e2e-source/run-20261111-213818` (freeze eab3ad33, runner `9be390ea`) passed 30/30. A product display defect was observed: after finalization the predecessor rework row still shows in Main review. See "Released run on eab3ad33" below. The text that follows describes the 2026-11-11 attempts.

On 2026-11-11 there was no passing run. The team lead stopped the work at about 17:05Z. A confirmed defect in the frozen `side-intake.js` means the 12:26Z freeze, and every snapshot built from it, is no longer final. No product file was edited by this work.

| Attempt | Window (UTC) | Result |
|---|---|---|
| `e2e-source/run-20261111-071722` | 13:17 | Harness: Enter pressed while the trust dialog still selected "No, exit". Kept as evidence. |
| `e2e-source/run-20261111-104734` | 16:47-16:49 | Harness: seeded `priority_limits` P2-P5=0. `validate_policy` requires 1..16, so every Work call returned `corrupt parallel state; writes blocked`. Stopped by the agent (`STOPPED.json`). |
| `e2e-source/run-20261111-105012` | 16:50-16:52 | Checkpoint A reached deterministically, then the runner failed with `owned console did not start`. See its `STOPPED.json`. |

# Harness changes (runner `e2e-source/owned_main_review_probe.py`, evidence only)

- Trust dialog: Enter is pressed only after a re-read shows the marker on the "Yes" line. Moves are bounded at 4.
- Environment: every `CIEL_RUNTIME_*`/`CLAUDE*`/`ANTHROPIC*`/`CODEX*`/`OPENAI*` variable is removed from the child env and from the runner's own env. Only owned values are set: `CIEL_RUNTIME_TEST_ISOLATED=1`, temp `CIEL_RUNTIME_CONFIG_DIR`, `CLAUDE_CONFIG_DIR=<config>/.claude`, port and hosts. The earlier "6 after strip" were the probe's own six variables, not inherited ones. The result now lists names and checks that each value is owned.
- Isolated Claude config: Claude Code 2.1.322 works with `CLAUDE_CONFIG_DIR`. Its settings, hooks, statusline, transcripts and trust are all under the temp config. The provider config copies only the `cielairouter` entry plus non-secret behavior keys.
- Deterministic checkpoints: an owned TCP gate on 127.0.0.2 forwards to the owned router on 127.0.0.1 (`CIEL_RUNTIME_ROUTER_BIND_HOST`/`CLIENT_HOST`). An armed MCP `tools/call` is held and never forwarded. A: the first `review_wake prepare`. B: the first `bind` of the item's `resume` lease. The kill happens only after the finalization file confirms the state.
- Durable files are read under the store's own lock. An unlocked handle makes `os.replace` fail on Windows (measured `PermissionError 13`).
- The protected set is the 18 live launcher/router/claude PIDs plus their cmd parents.

# What run-20261111-105012 showed

- Checkpoint A: n2's review was `pending`, wake `pending` with 0 attempts, kill at 16:51:19.78Z. After the resume, main got exactly one plugin wake (16:51:42Z). It inspected the worktree, re-ran `test_pass.py`, and approved (one CAS retry). The approve saga then blocked at `archive` with `stage_failed: run is not terminal`.
- n2's coordination run stayed `active` while n4's became `terminal`. Both had completion receipts and native idle. n4 never got a review row. This is not root-caused.
- The runner failure: after the owned console it was attached to was killed, every PowerShell query from the runner returned no rows. That broke `console_for`, the cleanup and `live_after`. So `protected_pids_unchanged=false` is a measurement failure. An independent check matched all 18 protected rows exactly. The suspected cause is a destroyed attached console; it is not verified.
- Live profile: 49 hashed files unchanged. No owned path appears in live settings, bin or commands, and `~/.claude.json` has no owned entry. The orphaned owned tree (19 processes) was killed by the agent at 17:05:05Z.

# Run on the integrated freeze (2026-11-11 22:19Z, `e2e-source/run-20261111-161947`)

Runner: `e2e-source/owned_main_review_probe_v2.py`. It works from a per-run byte copy of the current worktree (1072 files, 0 mismatches; register.js `378f6d73`, side-intake.js `0aeb7d84`, work_parallel_mcp.py `35214869`). Two harness faults were fixed before this run: a detached runner's stdin became invalid after FreeConsole (WinError 6, `run-20261111-161636`), and queries returned nothing after the attached console was killed (105012). Outcome `failed`, 22 of 25 items true. The three false items:

- `wake_delivery_append_or_prompt` (product, measured): review `30353fd4` (P2) had `$.prompt.submit` called at 22:22:47.650Z. `working` was still false then: the previous plugin prompt was in its submit chain, and turn 1 started at 47.897Z. That prompt never reached a hook chain or the transcript. The wake record was never sent (4 prepares, 3 records), so the wake stayed `submitting`. Main found the review through its own `work_parallel_status` read at 22:23:11.8Z and approved it. There was no classic.Stop before the decision. Whether Claude Code held or dropped the second submit is not established.
- `snapshot_unchanged_after_run` (harness): Claude Code 2.1.322 rewrote the git-ignored, generated `ciel-work/.claude-plugin/types/claude-code-tools/index.d.ts` in the snapshot at plugin load (debug 22:20:30.140Z). No other file changed.
- `protected_pids_unchanged` (harness): `core_changed` is empty. The single overlap, 74172, is PID reuse: the protected conhost was created 21:15:46.850Z, while the owned conhost under uvx was created 22:24:17.416Z. The 21 gone protected descendants were all created before 22:19:57.1Z, none is in any owned set, and their exit cause is not established.
- Kills 2 and 3 returned rc 128 because `taskkill /T` reached processes that were not in the enumerated owned set (parents 74404, 74848, 60772). Their identity is not established: stdout was not kept, and no process-creation audit exists.

# Harness kill fix (2026-11-11 23:04Z, preflight `e2e-source/run-20261111-170346`)

Runner `owned_main_review_probe_v2.py` sha256 `290eca19`. Each run folder now holds the exact runner, console_driver and screenshot helper, with their SHA256 in `result.json`.

- Kills no longer use `taskkill /T`. The owned tree accepts a child only if it was created after its parent, so a reused PID cannot adopt an older process.
- Every identity is pinned with a process handle whose creation time matches CIM, at CIM's microsecond precision. While that handle is open the PID cannot be reused.
- The tree is suspended until a fresh enumeration finds nothing new, then killed leaves-first with `taskkill /PID n /F`. Any PID in the protected snapshot (the launchers, their ancestors and descendants) is refused. The full stdout/stderr of every kill is logged.
- Protected comparison uses (PID, CreationDate). Snapshot comparison excludes only the engine-generated, git-ignored `.claude-plugin/types/**` files and lists them.
- Detached drill result: 61 owned processes (a spawner adding a child every 70 ms) were pinned, suspended and killed in 4 rounds, all rc 0 and dead, deepest first, conhost last. None remained, no protected identity was hit, and the runner stayed visible.
- Earlier drill `run-20261111-164909` (no pinning, one leaf per round) killed 32 processes but left the spawner's 680 children orphaned until they exited on their own. That is why suspension and pinning were added.

# Freeze 0d1b4afc runs (diagnostic: freeze has known blockers)

- `run-20261111-172722`: errored at checkpoint A on a false protected refusal. The runner refused PID 83768 by a PID-only check: the protected snapshot's 83768 (a pytest created 23:26:13Z) had exited, and an owned process reused the PID at 23:30:26Z. Fixed in runner `f4874aef`: protected roots are fixed by (PID, CreationDate), the protected tree uses creation-fenced descent, a refusal happens only on an exact identity in both trees and logs both chains, an aborted kill resumes what it suspended, and pins are logged. Preflight drill `run-20261111-173653`: 77 processes pinned and killed, 0 remaining, PID-reuse replay passed.
- `run-20261111-173855`: diagnostic-stopped at 23:42:09Z by the team lead's do-not-relaunch message; the launch had crossed that message. The fill and workers-running frames were captured before the stop; checkpoint A was not reached. The cleanup pinned and killed 28 owned identities with no `/T` and 0 remaining. A pre/post protected identity check shows no protected identity was hit, and the live profile is unchanged. Freeze 0d1b4afc has two FIFO release blockers, so neither run is release evidence.

# Prepared for freeze eab3ad33 (not launched; waits for the team lead's release)

Runner `owned_main_review_probe_v2.py` sha256 `7a7481a4664ba5e59c3115a6f85a92109b6869b5b648e46bca2c89fe376301b0`, from harness `f4874aef`. Diff, patch scripts, replay and synthetic checks are in `e2e-source/prepared-eab3ad33/` with `SHA256SUMS`. No product file was edited and nothing was launched.

- Key hashes now match `.local/tmp/freeze-sse-eab3ad33-final/SHA256SUMS` and the worktree: `register.js eab3ad33...`, `side-intake.js 9b955068...`, `types/index.d.ts 542ff492...` (new), `work_parallel_mcp.py 35214869...`.
- `at_most_one_prompt_submit_in_flight` was removed. `no_same_identity_raw_submit_twice` groups each raw review `$.prompt.submit` by `["parallel:<lease>", sha256(attempt_id)[:16]]`, the identity `side-intake.js promptIdentity` uses. The attempt id comes from two sources: the guard's durable observation, which now keeps `wake.attempt_id` because the id is cleared on decision, and the gate's passive log of every `review_wake` MCP call. That log keeps only operation, lease, attempt and state, never `_host`. If a submit cannot be attributed, the check fails.
- `no_wake_left_submitting` and `wake_delivery_terminal` count `queued|appended|uncertain` as terminal.
- `no_stop_data_for_pending_prompt` flags a Stop presentation of a lease inside a window that contains its registry pending interval. The window opens at the lease's last prepare request before the raw submit and closes at that attempt's record request. It can only over-report. The old "Stop before own wake" list is kept as a diagnostic only.
- `each_pending_review_reached_main_and_decision_called` requires all 5 reviews to show in main's transcript (wake or Stop) and their leases in `work_parallel_item_review` calls. `checkpoint_a_converged_no_duplicate_review_rows` was added.
- Offline replay on run 161947 (measured, `replay-161947.txt`): leases `11f824dc` and `30353fd4` had concurrent raw submits with distinct identities, which is allowed. No duplicate, `no_wake_left_submitting` = false (`30353fd4` stuck submitting), `wake_delivery_terminal` = false. That matches the recorded defect.
- Synthetic checks (`synthetic-checks.txt`): 6/6 pass. They cover a same-identity duplicate, concurrent distinct identities, two attempts of one lease, a duplicate on the second attempt, an unattributable submit, and Stop inside or after the pending window.
- Lint: `py_compile` passes. ruff E/F/W shows the same 3 pre-existing style findings as `f4874aef` (2 E402, 1 E501) and no new ones.

## Released run on eab3ad33: superseded at preflight

- `e2e-source/run-20261111-213536` (runner `7a7481a4`, released by the team lead after all four groups passed) crashed in preflight. The first `protected_rows()` call failed with JSONDecodeError "Invalid control character".
- Measured cause: Windows PowerShell 5.1 `ConvertTo-Json` left a U+001A unescaped. It sat in the command lines of two foreign `bash.exe` processes (56344, 64716) that started at 01:36:10Z and are not related to this run. Python's strict JSON parser rejects raw control characters.
- The run stopped before the protected baseline, the temp config and any owned launch. Afterwards no owned process remained, and `~/.claude.json` did not contain the run id or an owned temp path. The run is marked superseded in `SUPERSEDED.json`.
- Harness fix: `ps_rows` now parses with `json.loads(raw, strict=False)`. New runner `9be390ea`. The extracted runner code parsed the live list: 735 rows, including the two U+001A rows. Relaunch waits for the team lead's release of `9be390ea`.

## Released run on eab3ad33: run-20261111-213818 passed (30/30)

`e2e-source/run-20261111-213818` used runner `9be390ea` and supersedes 213536 (`SUPERSEDES.json`). It ran from 03:38:18Z to about 03:46Z on 2026-11-12. Outcome: `passed`, 30/30 acceptance true. Driver hashes in `result.json` match `driver-source/SHA256SUMS`. The snapshot key hashes match eab3ad33 / 9b955068 / 542ff492 / 35214869 (`snapshot-manifest.json`).

Measured facts (all from the run folder):
- Fill was P1=2, P2=1, P3=1 (`02-workers-running-state.json`, `02-workers-running*.png`). There were 0 reviewer leases and 0 reviewer forks. All six native forks are `work-auto-*` or `work-rework-*`.
- Checkpoint A: all 4 leases were `submitted` and no wake had been sent when the gate held the `review_wake prepare` for 2802f996 (`03-completion-before-review.json`, `response-gate.jsonl`). After the restart each of the 5 reviews had exactly 1 wake attempt. Three were `appended` (n2, n4 first review, n6). Two were `queued` through a raw prompt (n8 `84d340ca`, and replacement `ab47409e`). Nothing was left `submitting`.
- Raw submits: 2, with distinct identities (`84d340ca`/`a6606efc7f46302a`, `ab47409e`/`500bdc042dfa7273`). No duplicate, none unattributed, no Stop during a pending prompt. Each review showed in main's transcript once, as a wake (`result.json.fifo`).
- Decisions: 7 unique `work_parallel_item_review` calls. Two got `Work finalization changed; reread` (84d340ca, ab47409e), and main retried both, which then succeeded. The final state is n2, n6 and n8 approve → `finalized`. n4 first review → `rework_ready`, and replacement `ab47409e` approve → `finalized`. Every board item is `done` (`07-final-state.json`).
- Checkpoint B: the gate held the replacement's `bind` while launch attempt 1 was `spawn_observed` and the ledger had no host id. After the kill and restart, a1 resolved `absent` once. Attempt 2 bound host `a7154aab4c3d3cc9b` (`work-rework-…-a2`), the only rework fork in the third process. The token hash rotated (`f8c12cf7…` → `406816ff…`), and the retained workspace path was the same (`05-rework-before-bind.json`, `result.json.final.launch`).
- Safety: 136 kill rows, all rc 0 and dead, with no `/T`. Protected `core_changed` [], overlap [], refusals []. The live profile is unchanged. The snapshot is unchanged after the run. No owned process remained. `~/.claude.json` contains neither the run id, the owned temp dir, nor the session id (`claude-json-metadata-post.json`; its hash is metadata only).
- Decisive frames:
  - Review wake: `04-pending-review.png`.
  - Decisions: `05-main-decisions.png`.
  - rework_ready: `05-main-decisions-desktop.png` / `-mobile.png` and `05-main-decisions-state.json`.
  - Restart convergence: `04-pending-review.png` (after A) and `06-rework-running.png` (after B).
  - Approve finalized: `07-finalized.png`, `07-finalized-desktop.png` / `-mobile.png`.

Observation (product display defect, found by the team lead and confirmed in the frames): after the replacement was finalized, `07-finalized.txt` and `07-finalized-desktop.png` / `-mobile.png` still show `Main review (1) · Rework · n4` (lease `26c5461a`). `07-final-state.json` keeps that predecessor row at `rework_ready` with every stage true. No acceptance check covered this display, so it did not fail the run. A fix is in progress elsewhere (fix-restart-archive).

Prepared for the rerun after that fix. Not launched. Runner `c67719187ad3c29a6793296253536fe0183b7de3f4071e271291bd7c47346d20` adds `reworked_item_absent_from_final_main_review`. The reworked item must have 0 rows in the final terminal status block and in the web Main review at 1440 and 390. The terminal side fails unless that status block's header and Policy line are visible. Replaying it on 213818 returns false: one n4 row in the terminal, desktop and mobile. Synthetic fixed-state checks pass 5/5. Evidence is in `e2e-source/prepared-main-review-hidden/`.

The review-projection fix landed: `work_parallel_finalization.py` is now `c7610d77…`. The runner now pins that hash too, along with eab3ad33 / 9b955068 / 542ff492 / 35214869. New runner: `3c0cab1a357dec75bce76e2a99e303800682bf137b81a69c42f46b0ac91f04a9`. The diff is `probe_v2-c6771918-to-3c0cab1a.diff`, and the hidden-row assertion is kept. The runner's KEY_HASHES gate matches all 5 worktree files. Offline check (static, not E2E): the fixed `projection()` applied to 213818's recorded final finalization state returns 0 pending rows, so the n4 predecessor is hidden (`offline-projection-213818-c7610d77.txt`). Whether the terminal status and the web page show that projection is left to the rerun to confirm. Not launched; waiting for the team lead's release after the four groups pass.

Hold (team lead): the rerun waits for one more backend fix. The fix targets the measured rework bind/`launch_bound` conflict that strands a replacement before review. The final product hashes will change, and the team lead will send them with the release. Runner `3c0cab1a` stays ready: c6771918 changes plus the finalization pin. Before any launch, KEY_HASHES must be updated to the released hashes and the new runner hash reported.

Final product hashes pinned (read from the worktree):
- `register.js ea7768f5…`
- `side-intake.js 9b955068…`
- `types/index.d.ts 542ff492…`
- `work_parallel_mcp.py 704cb811…`
- `work_parallel_finalization.py 5c9afdc5…`

New runner `345a7ebe5e772e3f1ab31aa8e974efb5cc2bd4a780e3fe63b1171614c7d7eaf0`. The diff `probe_v2-3c0cab1a-to-345a7ebe.diff` has one hunk, at lines 29-49, inside the KEY_HASHES block only. All 5 pins match the worktree files. The hidden-row and raw-submit assertions are kept. review-restart-terminal measured that the checkpoint_b record block is byte-identical between 9be390ea and 3c0cab1a (block hash 2403a88a). This change does not touch it. Not launched: the four isolated groups run on these bytes from about 07:45Z, and launch waits for the team lead's release.

## Final run on the released product bytes: run-20261112-002502 passed (31/31)

`e2e-source/run-20261112-002502` used runner `345a7ebe` and supersedes 213818 (`SUPERSEDES.json`; 213818 is kept as evidence). It ran from 08:25:06Z (`SUPERSEDES.json` written) to 08:37:06Z (`result.json` written) on 2026-11-12. Outcome: `passed`, all 31 acceptance items are true and none are false. The snapshot key hashes are register.js `ea7768f5`, side-intake.js `9b955068`, types `542ff492`, work_parallel_mcp.py `704cb811` and work_parallel_finalization.py `5c9afdc5`. They are the same bytes that are committed.

Measured facts (all from `result.json` and the frames):
- Fill was P1=2, P2=1, P3=1 (`fill_use`). The ledger has no reviewer leases (`final.reviewers` []), and `no_reviewer_forks` is true.
- Checkpoint A (completion before review) and checkpoint B (rework launch durable before bind) held. At B, the gate held `bind` for replacement lease `70318b41…` while attempt 1 was `spawn_observed` with no ledger host id. After the restart attempt 1 resolved absent once. Attempt 2 (`work-rework-70318b41…-a2`) bound host `a7362e4d6c459571c` in the same retained workspace, and the token rotated once.
- Wakes: each of the 5 reviews went `pending` → `submitting` → `appended`/`queued` → `acknowledged` once (`wake_observations`). There were 2 raw prompt submits, each with its own identity and none repeated (`fifo.raw_prompt_submits`).
- Final board: all 4 items are `done`. The approved items were finalized. The failing item was reworked, and the replacement's second review was approved and finalized.
- Display fix confirmed: `reworked_item_absent_from_final_main_review` is true. `07-finalized.txt` shows the status block with ENDED (5) and no Main review row. `07-finalized-desktop.png` (1440) and `07-finalized-mobile.png` (390) show `Main review (0)` and "No worker result is awaiting main review". Ended lists n2, n4, n4 (rework a2), n6 and n8.
- Safety: `kills.jsonl` has 169 rows, and none contains `/T`. `protected_pids_unchanged` and `live_profile_unchanged` are true, and the snapshot is unchanged after the run. After the run the protected launchers 23728/39984/21912 kept the same creation times (15:49:31Z, 15:49:59Z and 15:50:47Z on 2026-11-11), and no owned run process remained.

## Installed-build runner prepared (not launched)

`e2e-source/owned_main_review_probe_installed.py` sha256 `a2faa191ba9afc49a9882e7a178fc780aebbdc4d32e9d7a00ddc67b4d3098dfb` is derived from v2 `345a7ebe`; v2 is byte-identical (re-hashed after generation). The diff and evidence are in `e2e-source/prepared-installed-runner/` with `SHA256SUMS`.

- Source: the installed npm package root is the only argv. It may not be the worktree or inside it. `build_snapshot()` copies it into the per-run owned SNAP under the worktree `.local` and verifies the copy file by file against the installed manifest. It writes `installed-manifest.json` and fails if the installed tree changes during the copy. Harness tools (console driver, screenshot helper) and `.local` still come from the worktree.
- `tests` is dropped from SNAP_DIRS only when the installed tree has no `tests/`. `result.json.installed_source` and `snapshot` record the path, snap_dirs, tests_dropped, installed file count and manifest digest.
- The installed path is never owned. Any command line that names it, in any slash form or case, is skipped in final owned-command discovery and refused in owned-root registration, tree pinning and explicit kill. A startup check refuses BASE, SNAP or scratch overlapping it.
- KEY_HASHES are the committed LF blob hashes the team lead named: finalization `79a573b6`, register `ea7768f5`, side-intake `dcf8fbbc`, types `4110db60`, mcp `ccf397c6`. The acceptance set is unchanged: 28 in the dict plus 3 set afterwards = 31, including `reworked_item_absent_from_final_main_review`. Protected-PID logic, env stripping and live-profile hashing are unchanged.
- Offline gate (`offline-gate-aafbe1c.txt`, measured):
  - Plain `git archive aafbe1c` extracted on this Windows checkout fails the gate on `types/index.d.ts`. That file came out with 22 CR bytes, sha `542ff492`. `* text=auto` has no eol rule for `.d.ts`, so the archive wrote CRLF.
  - `git archive` with `-c core.autocrlf=false -c core.eol=lf` writes the blob bytes: `index.d.ts` 0 CR, sha `4110db60`. The gate, copy and file-by-file check pass with 1062 files and 0 mismatches.
  - With `tests/` removed (the npm root shape) they pass with 560 files, `tests_dropped` true.
- Installed-path guard checks: 6/6 (`installed-guard-checks.txt`).

Target update: CI on aafbe1c failed only because a shallow checkout made `test_side_wake_protocol`'s `git show` return 128; nothing was published. Target commit/path is now `57dbfb3ac9c8d9f1a42e89a038d27e16f4fab409` / `C:/Users/djlov/.local/share/ciel-runtime-57dbfb3ac9c8d9f1a42e89a038d27e16f4fab409`. It changes only that test, so package bytes and the five KEY_HASHES are unchanged. Runner hash remains `a2faa191…`. Repeated offline gate on 57dbfb3 has the same result: plain Windows `git archive` fails only on CRLF `index.d.ts`; blob/LF archive passes (1062 files), and its tests-removed npm shape passes (560 files). New evidence is beside the old evidence: `offline-gate-57dbfb3.txt`, `installed-guard-checks-57dbfb3.txt`, `SHA256SUMS-57dbfb3`. No earlier evidence file was edited in this update.

# Before any rerun on a new freeze

FreeConsole before killing the attached console, and record each subprocess return code and stderr. Take a full pre-launch hash baseline that includes node.exe descendants. Root-cause the n2 `run is not terminal` block before treating it as a product defect.

# Installed beta 57dbfb3

The product came from the installed beta npm package `0.2.51-beta.20261112-102713.57dbfb3`, installed at `C:/Users/djlov/.local/share/ciel-runtime-57dbfb3ac9c8d9f1a42e89a038d27e16f4fab409` (INSTALLED). Each run copied INSTALLED into its own owned SNAP and verified the copy file by file against INSTALLED's manifest: 542 files, no `tests/` dir, `tests_dropped` true, manifest `35700ea7…`. The committed LF key hashes all matched: `79a573b6` / `ea7768f5` / `dcf8fbbc` / `4110db60` / `ccf397c6`.

Relationship to 002502: these runs repeat the main-review E2E on the installed build. They do not replace `run-20261112-002502`, which stays the accepted source-tree run. Each run's `SUPERSEDES.json` points at the previous installed or source run and says why.

`__pycache__` origin: INSTALLED held 1026 files before both runs, 573 package files plus 453 `__pycache__` files. The team lead's post-install smoke test created the `__pycache__` files (`py -3 <snapshot>/ciel_runtime.py version` and a module import). Before that smoke test, `evidence/beta-57dbfb3/snapshot.sha256` measured 573 package files. The runner's manifest excludes `__pycache__`.

INSTALLED was unchanged by both runs (measured): a read-only full manifest of all 1026 files was taken before and after each run (`installed-tree-before.json` / `installed-tree-after.json` in each run folder). The digest stayed `e64484d3…`, with 0 files changed.

## run-20261112-044325: failed 30/31, harness classification (product_defect false)

- Runner `a2faa191`. The only false item was `snapshot_unchanged_after_run`.
- `snapshot_changes.product` listed only `.claude-plugin/types/.gitignore` (content `*`) and `.claude-plugin/types/tsconfig.json`. Claude Code wrote both into the run's SNAP at plugin load, around 10:45Z, together with the three `claude-code*` type dirs. Neither file is in git or in the npm package, and no ciel_runtime_support code writes them. In the worktree both files already existed, which is why v2 treated them as product; in the installed copy they are new.
- The run is kept as evidence and marked superseded by 075943 (`run-20261112-044325/SUPERSEDED.json`, `product_defect` false).

## Runner fix a4de6539

- `owned_main_review_probe_installed.py` changed `a2faa191` → `a4de65392b51b5ddac14b301f7fab9c1a4f7f97c8d9a10fb748afadb3e0cbb71`. The diff is `e2e-source/prepared-installed-runner-engine-types/installed-runner-a2faa191-to-a4de6539.diff`, 16 changed lines.
- A path under `ENGINE_TYPES`, including `.gitignore` and `tsconfig.json`, is engine-generated only when it is absent from INSTALLED's manifest (`installed-manifest.json`). A path that exists in INSTALLED and changes is still a product change.
- Offline replay (`replay-044325-engine-types.txt`): 044325's recorded changes, and its SNAP recomputed now, both give product [] with 5 engine-generated files. Synthetic cases pass 4/4.

## run-20261112-075943: passed 31/31

- Runner `a4de6539`. Outcome `passed`, 31/31 acceptance, `snapshot_changes.product` []. The 5 engine-written types files are listed as engine-generated.
- Fill was P1=2, P2=1, P3=1, with 0 reviewer forks. Each of the 5 reviews was woken exactly once. There were 2 raw prompt submits, each with a distinct identity, no duplicates, and no Stop during a pending prompt.
- Decisions: n2, n6 and n8 approve → finalized. n4 first review → `rework_ready`, and the replacement approve → finalized. Launch attempt 1 resolved `absent`, and attempt 2 bound.
- `reworked_item_absent_from_final_main_review` is true: the terminal status and the web Main review at 1440 and 390 show 0 n4 rows (`Main review (0)`).
- Safety: 147 kill rows, none using `/T`. `protected_pids_unchanged` true (`core_changed` [], no overlap, no refusals). `live_profile_unchanged` true. INSTALLED unchanged. The runner process exited and 0 owned processes remained (process table).
- Decisive frames, all in `run-20261112-075943/`:
  - pending review: `04-pending-review.png`
  - decisions: `05-main-decisions.png`, `05-main-decisions-desktop.png` / `-mobile.png`
  - rework: `05-rework-before-bind-terminal.png`, `06-rework-running.png`, `06-rework-running-desktop.png` / `-mobile.png`
  - finalized: terminal `07-finalized.png`, 1440 `07-finalized-desktop.png`, 390 `07-finalized-mobile.png`

## Observation from 044325 checkpoint B (not a defect)

Rework attempt a1 (agent `a70ec164d860b5f7b`, `work-rework-0208d5bc…-a1`) was held at bind but was already running. Times are from a1's subagent transcript:
- 10:49:11.2Z: a1 started. At 10:49:13.8Z the gate held its `bind`.
- 10:49:14.8Z: a1 read `fail_solution.py`. 10:49:17.670Z: it edited the file. 10:49:26.9Z: it ran the tests.
- 10:49:32.430Z: a1 called `work_parallel_report` with completion. The router answered at 10:49:32.713Z with `worker lease is not active`.
- 10:49:38.7Z: last a1 row.
- 10:49:42.85Z: the owned processes were killed (`checkpoints.b_kill.killed_at`). This measured time replaces an earlier rough 10:50:04Z, which was the time of a later capture's kill summary.

a2 (agent `acb57b27f1e502bea`) then found the fix already in the retained worktree. It read the file and ran the tests, but made no edit. Main reviewed the worktree as it was and approved, noting in its evidence that what changed the file is not identified. 002502's final note shows the same kind of approval.

Evidence: `run-20261112-044325/rework-a1-transcript/` holds the redacted transcript, the meta, a note and SHA256SUMS. The raw transcript was withheld from docs/ because it contains the dead owned router's worker_token. It is at `.local/tmp/work-parallel/.local/evidence-private/run-20261112-044325/rework-a1-transcript/`, relative to the ciel-runtime root.
