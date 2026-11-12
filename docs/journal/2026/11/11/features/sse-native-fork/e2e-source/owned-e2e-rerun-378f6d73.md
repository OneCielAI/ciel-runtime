---
type: Verification
title: Owned SSE native-fork E2E rerun on freeze 378f6d73
description: Run A (recording gateway) and run B (real provider) on the duplicate-wake fix; exactly one main wake, first-try ACK, native fork with inherited context. Provisional; superseded by a rerun on the next freeze.
timestamp: 2026-11-11T17:35:00-06:00
acceptance: provisional — both runs must be rerun on the wake-delivery fix freeze; run B also overlapped another owned E2E
tags: [sse, native-fork, e2e, main-wake, exact-once]
---

# Status (2026-11-11, team-lead rulings A–D)

These runs are **provisional**. A product fix for wake delivery (concurrent plugin prompts) is in progress and will change `register.js` and `side-intake.js`, so run A and run B must both be rerun on the new freeze. Independent review found no product defect in these runs.

- **Raw-text evidence corrected.** The old raw-text gate is invalid evidence; see [Raw-text evidence](#raw-text-evidence-corrected). The `main_no_raw_sse_or_thread` fact in run A and run B, and `host_injected_no_raw_sse_or_thread` in run B's post-ruling `result.json`, were all computed from eight whole-message needles. That proves only that no *whole* source message appeared. It does not prove that no raw thread text appeared. The result files are kept unchanged, and the gate is marked invalid in [evidence status](recheck-378f6d73/evidence-status.json).
- **Run B is not isolated from another E2E.** The overlap is measured; its effect is not established. Run B overlapped the owned main-review E2E (snapshot `161947`); see [Overlap](#overlap-with-the-main-review-e2e).
- **Cleanup used `taskkill /T`.** Both runs did. The disappearance analysis from the before/after snapshots is in [Process disappearance](#process-disappearance-during-161820-and-162135). The corrected driver is described in [Driver v4](#driver-v4-and-preflight-drill).

# Source under test

- SSE freeze `freeze-sse-378f6d73`:
  - `register.js` `378f6d73…`;
  - `side-intake.js` `0aeb7d84…`;
  - plus tests and types.
- Restart freeze `09255642`.
- The snapshot is `.local/sse-true-fork/e2e/snapshot-378f6d73`: 1,075 files, whole-tree digest `ac9468eb58b2…`, HEAD `ba8b13c5`.
- [Snapshot verification](snapshot-378f6d73/verify.json):
  - 0 source changes during the copy and 0 copy mismatches;
  - all 8 runtime/test SSE-freeze lines match;
  - the restart freeze's `register.js`, `test_work_mod_write_gate.py` and `work_mod_harness.mjs` are superseded by the SSE-freeze bytes;
  - its other 4 runtime/test lines match.
- Every launch ran from a fresh per-run copy whose digest equalled `ac9468eb…` before launch.

The fix itself is described in [the duplicate side wake defect](../duplicate-side-wake.md). The defect it replaces is in [the first owned E2E](owned-e2e-2026-11-11.md).

# Isolation and safety (both runs)

Isolation:
- `CIEL_RUNTIME_TEST_ISOLATED=1` was set.
- A temporary `CIEL_RUNTIME_CONFIG_DIR` held `CLAUDE_CONFIG_DIR`.
- Every inherited `CIEL_RUNTIME_*`, `CLAUDE*`, `ANTHROPIC*`, `CODEX*`, `OPENAI*` and `MAX_MCP*` variable was removed.
- A pre-launch path probe placed every CLI asset, state and router path inside the owned scratch directory.

Checks after each run:

| Check | Run A | Run B |
|---|---|---|
| 53 live-profile hashes equal; no change during the run | yes | yes |
| 40 long-lived protected descendants of 23728/39984/21912 (64168 included): same PID and CreationDate | yes | yes |
| Owned tree intersects protected tree (checked only against the 4-row tree captured after `/exit`; insufficient, see [Process disappearance](#process-disappearance-during-161820-and-162135)) | no | no |
| Protected respawns | none | none |
| Walkie `presence_*` log events in window (read-only) | none | none |
| Snapshot digest after | `ac9468eb…` | `ac9468eb…` |
| Freeze rows mismatching (snapshot / worktree) | 0 / 0 | 0 / 0 |
| Run-copy diff | engine-written `claude-code-tools/index.d.ts` only (79→78 keys, `ClaudeDesign` removed, no `mcp__*`) | same |
| Secret hits in evidence and scratch | 0 | 0 |

# Run A — recording gateway (`run-a-20261111-161820`)

All 26 durable facts in `result.json` are true.

| Requirement | Evidence |
|---|---|
| Native fork, not registered | Two actors, one per spawned event: `Side SSE 8a077b439c54` (thread) and `Side SSE 7a3fc83011b3` (none_referenced). Each is `agentType: fork`, `isFork: true`, `model: inherit`, unnamed. There is no actor for the failed prefetch `7895d78835ea`. |
| Inherits main model and context | The fork request (`gateway-request-004`) has all three canaries, `CHG-58213` and the CLAUDE.md marker. It uses the same model as main, the same 75 tools, and a system prompt equal to main's. |
| Full thread before answering | Messages `1000`–`1004` are exact; target length is 650/650 with no clipping; `source_context: verified_thread`. |
| Contract forbids code and command execution | `instruction` is the payload's last key and ends with `The host validates and commits every field.`. It includes the file-write, shell, Work-mutation and send prohibitions. Nothing follows the payload. |
| Low-priority time limit reaches main immediately with its Work number | Work `n1` is P4; the Walkie task priority is 5. Commit to wake start took 0.455 s, and commit to the main request carrying the wake 1.07 s. |
| Exactly one main wake | 1 `$.prompt.submit` (22:19:01.307Z). 1 main user wake row. The gateway's largest wake-block count in one request is 1. 0 Stop blocks. The ACK came 25 s before the observation window ended. |
| ACK on first try | The `work_side_handoff` tool result (`gateway-request-006`) is not an error. The handoff is `acknowledged`, wake `acknowledged`, `needs_ack_retry: false`, revision 3. |
| Host commit and one source reply | The receipt is `processed`; one `walkie_send_message` to 1002. |
| Whole-message scan of main (diagnostic only; see [Raw-text evidence](#raw-text-evidence-corrected)) | 0 of 8 whole-message needles. The exact-substring recheck found no source excerpt of 24 or more characters in any host-injected field: the gateway fixture's decision text has no long verbatim excerpt. |
| none_referenced | `delivery: not_applicable`; 0 sends, 0 Walkie reads, 0 extra main requests. |
| Unreadable or unverifiable thread gives no answer | `Source root read failed` (`source_context`); no spawn, no reply. The row stays in `pending/` with `side_failure`. |
| Stop path | Not used in this run: the wake channel was `prompt`. |

# Run B — real provider (`run-b-20261111-162135`)

- Model: `_Walkie_Opus55-Sol61-Fast` through the owned router.
- The provider key was copied into the scratch config for the launch only. It was removed afterwards; `api_key` is now empty, and 0 files contain the key.
- `result.json` lists 20 of 20 facts true after the first ruling below, and 19 of 20 before it. Its raw-text fact is invalid evidence, so the run is provisional and must be rerun (see Status).

The fork's committed decision (synthetic fixture):

> reply: "Readiness review request for the synthetic Walkie feature recorded on the existing readiness-review Work item n1, linked to change ticket CHG-58213. Within 30 minutes, report whether the thread can proceed. Constraints: use only target environment sandbox-east, do not deploy, do not edit any file, and do not run commands."
>
> time_limit.evidence: `TARGET_1002: "Within 30 minutes, report whether the thread can proceed and preserve every constraint"; REPLY_1004: "acknowledge the 30-minute time limit to main before any ordinary reply."`

`CHG-58213` appears only in main's own END turn, never in the thread or in the Work projection. So the answer used inherited main context. The sandbox, 30-minute and REPLY_1004 content shows it used the full thread.

Wake and acknowledgment:
- 1 prompt wake, commit to start 0.47 s.
- The commit-to-main-request time was not measured. Run B uses the real provider and has no recording gateway, so `gateway_first_wake_request_at` is null.
- Main's first `work_side_handoff` succeeded.
- Handoff and wake are both `acknowledged`.
- 0 Stop wake blocks.

# Raw-text evidence corrected

The independent reviewer confirmed an **evidence defect** in the old predicate. The eight needles were whole messages. A 0/8 result cannot prove that main received no exact source substring.

The product contract permits two bounded verbatim destinations:

- `time_limit.evidence` in the main wake;
- the fork-composed sent `reply` shown in the Stop review.

It forbids the raw SSE event/body, any complete raw thread message, and any other exact source excerpt above the selected minimum length. The corrected driver uses a 24-character minimum and inventories every exact match by source message id, source offset, destination offset, length and destination field. Any whole source message and raw event payload is always rejected.

Read-only recheck: [exact-substring inventory](recheck-378f6d73/raw-text-inventory-recheck.json).

| Host row | Exact source matches (message id, source offset, length → destination) | Result |
|---|---|---|
| Run B wake row 61 | `1002@72`, 29 → `wake.summary`; `1002@194`, 56 → `wake.time_limit.text`; `1002@194`, 86 → `wake.time_limit.evidence`; `1004@38`, 71 → `wake.time_limit.evidence` | The two evidence excerpts are allowed. The summary and `time_limit.text` fragments are outside the v4 allow-list. |
| Run B Stop review row 81 | `1002@72`, 29 → `review.summary`; four excerpts of 31/33/58/71 characters → `review.reply` | The reply excerpts are allowed. The summary fragment is outside the v4 allow-list. |
| Run A host-injected rows | no exact source match of 24+ characters | Pass |

No host-injected row contained a whole source message. This is **not a product-defect finding for the frozen run**: the team-lead ruled that the bounded evidence quote and the fork-composed reply are permitted, and the independent reviewer found no product defect. It is an evidence-harness correction. The two-field allow-list above is superseded by the five-field ruling in [Allow-list ruling and inventory v2](#allow-list-ruling-and-inventory-v2).

Main's own source read remains separate. Run B row 91 is `mcp__walkie__walkie_read_messages`, chosen by main after its ACK and side review. The product does not restrict it.

The pre-ruling and post-ruling result files are unchanged and retained. Their old raw-text gate is marked invalid in [evidence status](recheck-378f6d73/evidence-status.json).

## Allow-list ruling and inventory v2

The team-lead ruled from the authoritative contract:

- The main handoff carries a bounded summary, deadline text and evidence, the Work number, the goal or title, and the queue position.
- The Stop review carries the side summary and the fork-composed sent reply.

Bounded verbatim excerpts are allowed in five fields. Each bound is the product's existing clip, verified in the frozen source:

| Field | Bound (JS UTF-16 units) | Product expression |
|---|---|---|
| `wake.summary` | 1000 | `summary:line(wake.summary,1000)` (side-intake.js `wakeText`); `summary:clipped(h.summary, 1000)` (register.js `handoffView`) |
| `wake.time_limit.text` | 1000 | `text:line(wake.time_limit.text,1000)`; `text:clipped(h.time_limit.text, 1000)` |
| `wake.time_limit.evidence` | 2000 | `evidence:line(wake.time_limit.evidence,2000)`; `evidence:clipped(h.time_limit.evidence, 2000)` |
| `review.summary` | 300 | `summary:clipped(r.summary,300)` (register.js `sideReviewText`) |
| `review.reply` | 1200 | `reply:clipped(r.reply,1200)` |

`raw_text_inventory.py` (sha `be80b286…`) fails a run on any of these:

1. A whole source message, the raw CloudEvent, or its `data` payload appears anywhere in a host-injected block. This covers every field (allowed ones included) and the whole block text. A whitespace-normalised comparison is applied as well.
2. An excerpt of 24 or more characters appears in any other field. Every JSON leaf is inventoried (title, question, waits, queue, source, kind, note and so on), and so is the text around the payload (`wake.prose`, `review.prose`).
3. An allowed field exceeds its bound.
4. A host-injected user row is not identified as a wake or review row, or does not parse as one. A host-injected row is a ciel-work plugin prompt, Stop-hook feedback, or any row that carries a side marker. A non-human row without a marker that still carries source text also fails.

The sources are thread messages `1000`–`1004`, plus each sent CloudEvent's `data.text`.

Request-only host context is inventoried too: the system-reminder blocks in `api-request-blob` rows, and the system prompt. register.js `:897-902` (snapshot) shows the prompt.submit hook context appends `handoffView` lists ("Side advice pending") and `sideReviewText`. Their views get the same allow-list, and all other text is `request_context.prose`.

Main's own tool reads, and its own assistant text, are reported under `observations`. They are never a failure.

The driver now does four new things:

- It calls `inventory_main_rows` with every sent CloudEvent.
- It writes the full report to `raw-text-inventory.json`.
- It refuses to start when `verify_product_bounds(run copy)` is missing any of the bound expressions (`product-bounds-check.json`).
- It runs `test_raw_text_inventory` in the pre-launch unit gate.

Recheck with v2: [`raw-text-inventory-recheck-v2.json`](recheck-378f6d73/raw-text-inventory-recheck-v2.json). The old recheck file is unchanged.

| Run | Host rows | Excerpts | Result |
|---|---|---|---|
| B | wake 61, review 81 | Same 9 rows as the old recheck (source ids, offsets, lengths, sha256). `1002@72/29` → `wake.summary` and `review.summary`; `1002@194/56` → `wake.time_limit.text`; the others go to evidence or reply. All allowed. | pass |
| A | wake 65, review 72, request context 62 (1 handoff view, 1 review view) | `event.text@34/25` (`ynthetic readiness review`, shared by the fixture summary and the none_referenced event text) → `wake.summary` / `review.summary` only | pass |

Observations, not failures:

- Run B row 91 is main's own `walkie_read_messages`. Its result, row 92, holds whole messages `1000`/`1001`/`1002`.
- Main's own Work tool results also carry excerpts: `work_status` (67), `work_side_handoff` (73) and `work_side_review` (87).

### Unit test and fail-before

`test_raw_text_inventory.py` (sha `1e25bdf1…`) has 22 tests.

Positive tests on the recorded data:

- run B passes and reproduces the old recheck's 9 match rows exactly;
- run B's own read is only an observation;
- run A passes;
- the 3 previously disallowed recorded rows are allowed under the ruling;
- the snapshot's product bounds match.

Negative tests mutate run B's real wake and review rows. The run fails for:

- an excerpt in `wake.title`, `wake.question`, `wake.waits[0].waiting_on`, `wake.queue.phase` or an added `wake.note`;
- an excerpt in `review.source`, `review.kind` or `review.note`;
- an excerpt outside the payload (`wake.prose`);
- the 24-character boundary: 23 passes and 24 fails;
- a whole message in `wake.summary`, in `wake.time_limit.evidence`, in `review.reply`, or anywhere in the block;
- the raw event, and the `data` payload;
- each allowed field at its bound passes and at bound+1 fails, with UTF-16 counting (150 emoji pass, 151 fail);
- unidentified plugin and Stop rows, and a review row with no payload;
- a truncated wake payload;
- an unmarked meta row carrying source text;
- request-context prose, and `wake.question` inside request context;
- the second review section's `kind`;
- a changed product bound.

A human prompt is not host injection.

[`rti-mutation-check.json`](preflight-drill-20261111-172723/rti-mutation-check.json) is the output of `mutation_check_rti.py` (sha `05aea2fa…`).

- The previous module `7412cc06…` errors all 22 tests.
- 11 single mutations of the current module, each applied to a temp copy, all fail at least one test, and the real module is unchanged. The mutations are:
  - restoring the two-field allow-list (8 tests fail, including the run B positive);
  - removing the bounds check;
  - counting code points instead of UTF-16 units;
  - removing the whole-message check;
  - removing the raw-event check;
  - not inventorying the prose;
  - treating plugin rows as not host-injected;
  - dropping unidentified rows;
  - skipping request context;
  - parsing only the first review section;
  - not stripping the `#N` suffix.
- One candidate mutation was discarded as equivalent. Removing the kind check from `passed` changes nothing, because an unidentified kind always sets `parse_error`.

[`preflight-drill-20261111-172723`](preflight-drill-20261111-172723/drill-result.json) is the detached drill, with no product launch:

- The unit gate ran 29 tests (OPC 7 and RTI 22) from the harness copy, all OK.
- 10 explicit leaf-first kills; no `/T`.
- Protected and live-profile state unchanged.
- `passed: true`.

# Overlap with the main-review E2E

Measured overlap is in [overlap-main-review-161947.json](overlap-main-review-161947.json). Its effect is not established.

- Run A: 16:18:20–16:20:17. Its side event and ACK were 16:18:59–16:19:02, before the main-review E2E began at 16:19:47. The main-review E2E started while run A was still running its later cases/screenshots.
- Run B: 16:21:35–16:23:56. The main-review launcher, router, Claude and workers were already live before run B began, and some ended while run B was active.

Run B therefore is not final isolation evidence. The v4 preflight refuses to launch while another owned E2E command line exists.

# Process disappearance during 161820 and 162135

The old driver used `taskkill /PID <root> /T /F` and discarded taskkill stdout/stderr. A parent PID reused between discovery and taskkill could therefore have killed a foreign tree without leaving taskkill evidence. The before/after snapshots cannot prove cause. The inventory is [process-disappearance-161820-162135.json](process-disappearance-161820-162135.json).

| Run | Snapshot-visible identities that ended | Under protected launchers | Other | Parent PID in this run's 4-row tree at kill time |
|---|---:|---:|---:|---:|
| A `161820` | 7 (3 `python.exe`, 4 `conhost.exe`) | 0 | 7 | 0 |
| B `162135` | 45 | 13 (7 bash, 4 conhost, 1 python, 1 tasklist) | 32 (12 conhost, 11 python, 4 cmd, 3 node, 1 Claude, 1 py) | 0 |

For run B, seven of the non-protected ended rows have an explicit main-review path in their command line. The others include short-lived shell/tool processes and processes whose command line was blank in the snapshot. None of the 45 ended identities had its recorded parent PID in this SSE run's four-row tree at kill time. These facts do not establish why any process ended.

The 4-row tree is insufficient evidence by the new requirement: it was captured only after `/exit` and consists of conhost → cmd → py → runtime python. The full ready tree was not retained as exact `(PID, CreationDate)` identities. The runs are provisional.

# Driver v4 and preflight drill

The next freeze will use driver v4. The driver and every helper are copied into each run folder before launch, with `SHA256SUMS`; `result.json` records `driver_sha256` and the whole helper map.

Cleanup (`owned_process_control.py`):

1. Owns exact `(PID, CreationDate)` identities, never bare PIDs.
2. Accepts a child only while its exact parent identity is present and the child's creation time is strictly later.
3. Keeps the full ready ledger and accepts dynamic children under those exact identities.
4. Immediately before every kill, re-reads CIM and verifies `(PID, CreationDate)` again.
5. Re-discovers children, kills explicit leaves first with `taskkill /PID <pid> /F` (never `/T`), and repeats until no owned identity remains.
6. Logs argv, return code, stdout and stderr for every explicit kill.
7. Checks the ready ledger, dynamic identities and every explicit kill against the protected identity set.
8. Refuses to run until `start_detached.py` has orphaned the driver out of the protected launcher's tree.

Preflight-only drill: [`preflight-drill-20261111-165606`](preflight-drill-20261111-165606/drill-result.json) (no product/Claude/Ciel launch).

An earlier drill, `preflight-drill-20261111-165329`, was started directly from the agent shell under live `claude.exe` 12240. Its owned tree was therefore a descendant of protected launcher 21912, and v4 refused to kill it: "owned process ledger intersects protected identities". That abort left six owned sleepers alive. Their starter, PID 69700, had exited. They were then removed by exact identity with the same no-`/T` routine (`leftover-cleanup.json`). The fix is the detach guard (rule 8 above).

- Synthetic rule checks: child-after-parent, reused-PID identity mismatch and reused-parent rejection all passed.
- Real tree: 10 owned identities (root, children, grandchildren and console hosts) were killed leaf-first; exactly 10 explicit kills succeeded.
- A non-owned decoy remained alive until it was separately cleaned by exact identity.
- No `/T`; no rejected kill; no protected intersection; no owned identity left.
- The 40 protected long-lived identities and the 53 live-profile hashes were unchanged.
- Full taskkill output is in `cleanup.jsonl`; `drill-result.json` says `passed: true`.

## Unit test with a fake process table

`test_owned_process_control.py` runs `owned_process_control.py` unchanged against an in-memory process table.

- `snapshot_all`, `subprocess.run` (taskkill) and `time.sleep` are patched.
- The fake taskkill removes exactly one PID and asserts `/T` is absent.
- No real process is created or killed.

There are seven tests:

| Test | What it proves |
|---|---|
| `test_child_older_than_parent_is_refused` | A row naming the root as parent but created before it (PID reuse) gets `reject_child_time`, is never killed, and survives. Only `20`, then `10`, are killed. |
| `test_leaf_first_order_and_no_tree_flag` | Grandchildren die before their parent and the root dies last. Every argv is exactly `taskkill /PID <pid> /F`. The unrelated row survives, and the taskkill stdout is in the JSONL log. |
| `test_protected_identity_refuses_before_any_kill` | An owned identity equal to a protected `(PID, CreationDate)` raises `RuntimeError` with zero taskkill calls. The log records `protected_intersection`. |
| `test_same_pid_with_other_creation_date_is_not_protected_match` | Protection matches the exact identity, not a bare PID. |
| `test_pid_reused_before_kill_is_not_killed` | An owned leaf exits and its PID is reused by a foreign process before the kill loop. The reused PID is never passed to taskkill. |
| `test_child_spawned_during_cleanup_is_killed_before_parent` | A child that appears mid-cleanup is admitted as a dynamic identity and killed before the root. |
| `test_absent_root_is_refused` | A root PID with a different CreationDate is refused. |

Result: `py -3.11 -B -m unittest test_owned_process_control -v` ran 7 tests, OK.

Fail-before: `mutation_check_opc.py` applied five safety-removing mutations, each to a temp copy of the module. The real module hash was unchanged afterwards. Every mutant failed at least one test ([`opc-mutation-check.json`](preflight-drill-20261111-170652/opc-mutation-check.json)):

| Mutation | Failing tests |
|---|---|
| child-time check removed | `test_child_older_than_parent_is_refused` |
| leaf filter and not-leaf skip removed | leaf-first, child spawned mid-cleanup |
| `/T` added to taskkill | 5 tests |
| protected checks removed | `test_protected_identity_refuses_before_any_kill` |
| PID-only identity match | `test_pid_reused_before_kill_is_not_killed` |

Wiring (driver `73bb73cd…`; since `b0dfa7b1…` the gate is `harness_unit_gate`, which also runs `test_raw_text_inventory` and writes `harness-unittest.txt`): the test is in `HARNESS_FILES`. Driver v4 calls `opc_unit_gate(out)` right after `copy_harness`. The gate:

- runs the unit test from the per-run `harness/` copy, before anything is launched;
- writes `opc-unittest.txt`;
- records `opc_unit_gate` (return code, number of tests run, module and test hashes) in `result.json`;
- exits on any failure, or when zero tests ran.

`cleanup_drill.py` runs the same gate first.

## Preflight drill rerun after wiring

[`preflight-drill-20261111-170652`](preflight-drill-20261111-170652/drill-result.json) was started detached. There was no product, Claude or Ciel launch.

- Unit gate: return code 0, 7 tests run, module `797bba5c…`, test `e9eb56f9…` ([`opc-unittest.txt`](preflight-drill-20261111-170652/opc-unittest.txt)).
- Synthetic rule checks: all three true.
- Real tree: 10 owned identities (5 python, 5 conhost), exactly 10 explicit kills, all `rc 0` with `SUCCESS`.
  - In `cleanup.jsonl`, no owned child was killed after its parent, and the root was killed last.
  - No `/T`, no rejected kill, no protected intersection, no owned identity left.
- The decoy stayed alive and was then cleaned by exact identity.
- 40 protected long-lived identities and the live-profile hashes were unchanged.
- No `time.sleep(300)` process was left after the drill.
- `passed: true`.

Current v4 hashes (`SHA256SUMS` in `preflight-drill-20261111-172723/harness/`):

| File | SHA-256 |
|---|---|
| `walkie_e2e_driver_v4.py` | `b0dfa7b1b4d4fea9013a8211d8ea7fe9e824f59951db03f3b76fbd85e6803454` |
| `owned_process_control.py` | `797bba5ccb5e25bd2041b5366f238fb3b0d69f14536e4a5d4d4ec01221a91fb0` (unchanged) |
| `test_owned_process_control.py` | `e9eb56f955bbefc0f58948c0a82d714249c0b0cf3a6dba5b0562c1ab34f6ab2e` (unchanged) |
| `raw_text_inventory.py` | `be80b286363d9f36e78ef9aa7181bf4caabc348d9fdd3691c23454657e6dfce6` |
| `test_raw_text_inventory.py` | `1e25bdf1f5fc5e3d4340f0d233df76a69ac3b7115e19f130e41ef7243585263e` |
| `cleanup_drill.py` | `29c665123395664a2ec59645f8634f20de36bbb77ffcc7ab4642a33e30b78bf4` |
| `mutation_check_opc.py` | `61f4fe94151f55a41133d32a3840ddc7cc475ea8f8bfc59844c6b03a97b6b7bd` |
| `mutation_check_rti.py` | `05aea2fa868287a33c986c8e3c91c699446e46280ad3cfb5b4d64c1815718bfe` |
| `start_detached.py` | `027b0cb59f0582e0a2a74071bb3985fdfaf8d3350022a09276bacad9186b6b5c` |

Driver history:

| Driver | Change | Used by |
|---|---|---|
| `dd3f08cd…` | first v4 | drill 165606 (cleanup_drill `b2afa9fa…`) |
| `73bb73cd…` | adds the OPC unit gate | drill 170652 |
| `b0dfa7b1…` | inventory v2, product-bound preflight, combined OPC+RTI unit gate | drill 172723 |

No product launch used driver v4. Both A and B wait for the next product freeze.

# Harness corrections during this round (not product defects)

| Run | Stop cause | Correction |
|---|---|---|
| `run-a-20261111-160035` | The fetch-failure event named the same message 1002. The host's source-identity dedupe answered it as already handled, so no read happened. | The fetch-failure event names message 1006. |
| `run-a-20261111-160639` | The failed row stays in `pending/` with `side_failure`, but the driver read only `receipts/`. It made 6 root reads at 0.6/7.8/19.4/40.1/80.6/161 s, then quarantined, as designed. | The driver reads `receipts/` and `pending/`. |
| `run-a-20261111-161230` | Only the rule requiring exactly one actor per run failed. Two forks (thread and none_referenced) are correct. | The rule is now one fork per spawned event, none for the failed prefetch. |

Each run folder holds an `attempt-note.json`. In all three, the wake was already exactly one, with a first-try ACK.

# Evidence

- [Run A result](run-a-20261111-161820/result.json): deciding frames are `03-wake-and-ack.png`, `side-desktop.png` and `side-mobile.png`.
- [Run B result](run-b-20261111-162135/result.json): deciding frames are `03-wake-and-ack.png` (the single wake prompt), `04-after-events.png` (the Stop review block), `side-desktop.png` and `side-mobile.png`.
- [Snapshot verification](snapshot-378f6d73/verify.json)
