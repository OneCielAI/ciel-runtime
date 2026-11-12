---
type: e2e-journal
title: Owned SSE native-fork E2E on the installed 57dbfb3 build
description: The SSE side-actor E2E (run A recording gateway, run B real provider) repeated against the installed npm package of beta 0.2.51-beta.20261112-102713.57dbfb3, using a separate installed-build driver that launches only a verified per-run byte copy. Both runs completed with every fact and every safety gate true; accepted by team-lead.
timestamp: 2026-11-12T08:52:00-06:00
acceptance: accepted by team-lead — run A run-a-20261112-083717 (31/31 facts) and run B run-b-20261112-084410 (25/25 facts)
tags: [sse, native-fork, e2e, installed-build, main-wake, exact-once, release, beta]
---

# Scope

This is a repetition of the source-tree SSE native-fork E2E, run against the installed release instead of a worktree snapshot. The accepted source runs are recorded in `docs/journal/2026/11/11/features/sse-native-fork/e2e-source/owned-e2e-eab3ad33.md`. The release and the local install are recorded in `docs/journal/2026/11/11/features/work-parallel-main-review/release-beta-57dbfb3.md`. Neither file was edited for this record.

Everything below was measured from the files linked in each section unless it is marked *not established*.

# Product under test

- **INSTALLED** (shared, read-only): `C:\Users\djlov\.local\share\ciel-runtime-57dbfb3ac9c8d9f1a42e89a038d27e16f4fab409`.
- **Commit:** `57dbfb3ac9c8d9f1a42e89a038d27e16f4fab409`.
- **Tree:** 573 non-bytecode files, manifest digest `22932603388589fa0f0d06b4219db2824622335b38d4ac724b20ac555bfac266`. It is the same before and after both runs.
- **Bytecode:** INSTALLED also holds 453 `__pycache__/*.pyc` files, present before run A and still 453 after run B. The driver reports them but never copies them and never puts them in the manifest.
- **Key pins:** SHA-256 over raw bytes, with no line-ending normalization. They are the committed LF blobs of 57dbfb3.

  | File | SHA-256 |
  |---|---|
  | `ciel_runtime_support/claude_mods/ciel-work/hooks/side-intake.js` | `dcf8fbbcf33cfeadd9e87890f2c62b790400e4aaca6105138ed55db03f7cac32` |
  | `ciel_runtime_support/claude_mods/ciel-work/hooks/register.js` | `ea7768f5348e61883cfe6833420abf0e98e1e001576c5868fb1b4005d526271a` |
  | `ciel_runtime_support/claude_mods/ciel-work/types/index.d.ts` | `4110db606785f32159b68bf2e77fdfb0e35ee46533853caab5917e9ce048f314` |
  | `ciel_runtime_support/work_parallel_mcp.py` | `ccf397c62c084241253723db5a9bbba09240bec09e2dba37ca14de83ebb7c03f` |
  | `ciel_runtime_support/work_parallel_finalization.py` | `79a573b67ac69a2649df9960841fbd026876ad7684d2584d6c9d63ba8000664d` |

  The pins matched 5/5 in four places: in INSTALLED before each run (`installed_preflight.key_pins`), in each run copy before launch (`product-pin.json`), and in both INSTALLED and the run copy after each run (`safety_gate.installed_key_pins`, `safety_gate.run_copy_key_pins`).

# Driver

The installed-build driver is a separate file. The source driver `walkie_e2e_driver_v4.py` (`0f8a977fb3f9e75542ce1dcf01e0900fca0857619c33b8ead338aafa9a3e9d7b`) is byte-identical to its accepted version. A test checks this: `test_installed_driver.test_v4_driver_is_untouched`.

A self-contained copy is in [driver-source](/e2e-source/driver-source/SHA256SUMS):

| File | SHA-256 |
|---|---|
| `walkie_e2e_driver_installed.py` | `7f9706efffa6b42cd3c945df1a4abf085e4b681050c9e686acbdf2ed37fe19cd` |
| `test_installed_driver.py` | `5edeae5f0b051f4748c3938ab8c454930c671576845f70bf485a5d055b118d75` |
| `start_detached_installed.py` | `58964085430f34118a5031190682145d77b01cd98f100214cc527fce7c68e89d` |
| `walkie_e2e_driver_installed-7f9706ef-vs-v4-0f8a977f.diff` | `24b0d07918a52a503d411f98a9c350090f464f73879ed421c39c9d146f791ce0` |

**The diff against v4 `0f8a977f`**
- It is a Python `difflib` unified diff: 12 hunks, +183/−79 lines.
- Applying it to v4 with `patch --binary` in a temporary directory reproduces `7f9706ef…` exactly.
- An earlier diff, `.local/sse-true-fork/e2e/installed-driver/walkie_e2e_driver_installed-vs-v4-0f8a977f.diff` (`ca681661…`), was made before the move to commit 57dbfb3 and before the types rule below. It does not describe `7f9706ef` and is superseded by the file above.

**Shared harness modules**

The installed driver shares these modules with the source runs, unchanged. The hashes are copied in each run's `harness/SHA256SUMS`.

| File | SHA-256 |
|---|---|
| `owned_process_control.py` | `02d111f8a5bf6bf610b20c5fd55376cf60512763b8fea6b429119394aa0f9b80` |
| `test_owned_process_control.py` | `eea1a96f9f8b934a2527cc83193585f8c185eba680f85f7c6c2ff47b37bddf5f` |
| `raw_text_inventory.py` | `33b0d0bb66eb8087787f65ef91c795968ad579e2374c398959f15390dbbc6c94` |
| `test_raw_text_inventory.py` | `3993c33d0b48fc0f8b16cdafde21ae243362eb10804dc3fddf3d6cc63b3a31ce` |
| `test_driver_assertions.py` | `980985f2647100c2a5528c4cb305c0aaae4b0adad99243eb4c080f660cd8e792` |
| `console_driver.py` | `67e94ec2064978fa6bbd6d63826f36b2fb3ff656345fbebcc477e1f7de834c49` |
| `check_freeze_injection.py` | `b2832803b7c1f86e54f0731e7af431a56a559a10aca1829f3a428a2bf7458e5f` |
| `shot_hwnd.ps1` | `513c859a9d883cb0464a896c0193811d9097029eeac1052e0556201e1ccda64b` |

## What the installed driver changes

Each point below is a hunk in the diff.

- **Argument.** `--installed <npm package root>` is required and resolved, and the tree is only read.
- **Preflight (`verify_installed`)** runs before anything else and stops the run if any check fails:
  - shape check: runtime files must be present, and `.git`, `.local` and `tests` must be absent at the top level;
  - INSTALLED must not be inside the dev worktree;
  - the 5 key pins on raw bytes;
  - the product bound expressions;
  - the full non-bytecode manifest is hashed.
- **Per-run copy (`make_run_copy`).**
  - It is made under `.local/sse-true-fork/e2e/run-copies/<run>` and refuses a destination that already exists.
  - It is verified file by file against the manifest.
  - INSTALLED is hashed again after the copy, so a change made after preflight fails the run.
  - `SNAP` is set to the copy before `seed_board`, any product import, the path probe and the launch.
  - Before launch, the copy must equal the manifest exactly, and no seed write is allowed.
- **Owned markers.** Leftover matching uses only the scratch dir and the run's own copy. Processes whose command line names INSTALLED are listed in `installed_path_processes_observed_not_owned` and are never matched or killed.
- **After the run:**
  - INSTALLED is hashed again (`installed_tree_unchanged`);
  - the key pins are checked in INSTALLED and in the copy;
  - the run-copy changes are classified (types rule below);
  - the INSTALLED bytecode list is reported without being gated.

## Types rule

Claude Code writes `ciel-work/.claude-plugin/types/` into the launched home when it loads the mod. That covers `.gitignore`, `tsconfig.json`, `claude-code/index.d.ts`, `claude-code-mcp/index.d.ts` and `claude-code-tools/index.d.ts`. None of these is in the npm package.

`classify_copy_path(rel, installed)` marks a run-copy path as `engine_written_types` only when both conditions hold:
- the path is under `.claude-plugin/types/`;
- the path is absent from the INSTALLED manifest.

A path that exists in INSTALLED and changed or was deleted is `other`, and therefore a failure, wherever it is. Offline tests cover this:
- `test_engine_written_types_absent_from_installed_are_allowed`;
- `test_changed_installed_file_under_types_still_fails`;
- `test_other_extras_and_changes_fail`;
- `test_installed_bytecode_does_not_change_the_manifest`.

## Offline gate

- Before launch, the full harness gate ran 72 tests: 0 failures, rc 0, 1.314 s. The log is `.local/sse-true-fork/e2e/installed-driver/offline-unit-gate-57dbfb3-types-rule.txt`.
- Each run repeats the same 72-test gate inside the driver: [run A](/e2e-source/run-a-20261112-083717/harness-unittest.txt), [run B](/e2e-source/run-b-20261112-084410/harness-unittest.txt). Both returned rc 0.
- A `git -c core.autocrlf=false -c core.eol=lf archive 57dbfb3` fixture passes the 5/5 key gate (`test_key_hash_gate_passes_on_committed_archive`).
- A plain `git archive` on this machine exports `types/index.d.ts` with CRLF line endings (`542ff492…`). The runtime gate does not normalize line endings, so that export would correctly fail the pin.

# Runs

Both runs used the same driver `7f9706ef…`, the same INSTALLED tree and the launcher `start_detached_installed.py`.

| | Run A | Run B |
|---|---|---|
| Folder | [run-a-20261112-083717](/e2e-source/run-a-20261112-083717/result.json) | [run-b-20261112-084410](/e2e-source/run-b-20261112-084410/result.json) |
| Runner | PID 25520, created 08:37:17 local | PID 85996, created 08:44:10.166 local |
| Run window (`run_start`–`run_end`) | 08:37:20–08:39:32 | 08:44:13–08:46:46 |
| Model | recording gateway (`claude-opus-5-5` label) | `_Walkie_Opus55-Sol61-Fast` (real provider) |
| Run copy | `run-copies/run-a-20261112-083717`, 573 files, digest `22932603…` | `run-copies/run-b-20261112-084410`, 573 files, digest `22932603…` |
| Outcome | `completed`, error null | `completed`, error null |
| Facts | 31/31 true | 25/25 true |
| `safety_gate` | 11/11 true | 11/11 true |
| Driver exit | PID 25520 absent from the process table, checked after the monitor reported exit | PID 85996 absent from the process table, checked after the monitor reported exit |

## Measured facts

Paths are relative to each run folder.

| Assertion | Run A | Run B | Evidence |
|---|---|---|---|
| Main wakes for the time-limit event | 1 transcript wake row, 0 Stop-block rows, 0 uncertain rows, 1 `$.prompt.submit`; gateway max wake blocks 1, Stop blocks 0 | 1 transcript wake row, 0 Stop-block rows, 0 uncertain rows, 1 `$.prompt.submit` | `result.json` `wake_count` |
| Wake timeline | intent → started → queued → acknowledged, channel `prompt` | same, ending `acknowledged` on channel `prompt` | `wake-timeline.jsonl` |
| Stop path | not used (`happened: false`) | not used | `result.json` `stop_path` |
| Related Work | event `e4bf4154…`, item n1, root n1, in the seeded tree {n1, n2} | event `0df469de…`, item n1, root n1, in the seeded tree {n1, n2} | `result.json` `related_work_check`, `seed-board.json` |
| Priority | Work root P4, event thread task priority 5 | same | `result.json` `priority` |
| Delay | handoff commit to wake start 0.378 s; to the gateway's first main request 0.922 s; to ACK 1.048 s | handoff commit to wake start 0.895 s; to ACK 23.319 s (no gateway on the real provider) | `result.json` `wake_timing` |
| ACK | 1 attempt, `expected_version` 2, `is_error` false | 1 attempt, `expected_version` 2, `is_error` false | `result.json` `ack_attempts` |
| Side actors | 2 native forks (`agentType: fork`, `isFork: true`, unnamed), one each for events `7e1f782f` and `e4bf4154` | 1 native fork for event `0df469de` | `result.json` `side_actors` |
| Fork wire (A only) | model `claude-opus-5-5`; front, middle and end canaries and the Claude marker present; thread messages 1000–1004 exact; target 650 characters in the payload; last payload key `instruction`, ending with the contract | not recorded (no gateway) | `result.json` `fork_wire` |
| Answer (B) | — | uses the thread (1002–1004 constraints) and main context (Work n1, change ticket CHG-58213); facts `answer_uses_thread`, `answer_uses_main_context` and `answer_not_event_only` true | `result.json` `answer` |
| Message-less event | `not_applicable`, no send, no main instruction | — | `facts.none_*` |
| Fetch failure | no spawn, no reply, durable reason | — | `facts.fetch_*` |
| Prompt delivery | 1 prompt submit for the event, 0 duplicate texts; no wake left `started` after resolve; no Stop presentation while the identity was pending | same | `result.json` `design_assertions`, `facts` |

## Raw-text inventory

Allow-list: `wake.summary` ≤ 1000, `wake.time_limit.text` ≤ 1000, `wake.time_limit.evidence` ≤ 2000, `review.summary` ≤ 300, `review.reply` ≤ 1200. The minimum excerpt is 24 characters.

- **Run A:** passed with no failures.
  - Wake row 63 has 1 excerpt, in `wake.summary` (25).
  - Review row 72 has 1 excerpt, in `review.summary` (25).
  - The request contexts (`attachment:UserPromptSubmit`, `request:user`) passed.
  - No non-side host rows.
  - Observation, not a failure: 1 match inside main's own `work_side_handoff` tool_result.
- **Run B:** passed with no failures.
  - Wake row 61 has excerpts in `wake.summary` (26, 31, 24), `wake.time_limit.text` (56) and `wake.time_limit.evidence` (86, 71).
  - Review row 96 has excerpts in `review.summary` (26, 31) and `review.reply` (29, 36, 32, 35).
  - The request contexts passed.
  - Observation: main read the Walkie thread itself, with 3 `mcp__walkie__walkie_read_messages` calls at rows 75, 80 and 100.
- Evidence: `raw-text-inventory.json` in each run folder.

## Safety

The same `safety_gate` checks were true in both runs:
- `core_launcher_roots_intact_and_no_protected_owned_or_killed`
- `installed_tree_unchanged`
- `installed_key_pins`
- `run_copy_key_pins`
- `run_copy_expected_mutations_only`
- `exclusive_run`
- `owned_tree_disjoint_from_protected`
- `cleanup_completed`
- `no_unexpected_leftover`
- `live_profile_equal`
- `claude_json_no_owned_refs`

**Protected processes**
- The core launcher roots 21912, 23728 and 39984 kept their exact `CreationDate`.
- `ended_not_killed` and `ended_owned_or_killed` are empty in both runs.
- The protected gate held 41 long-lived PIDs, with no lead-listed PID missing at start.

**Cleanup** (`cleanup.jsonl`)
- Each run killed 2 exact `(PID, CreationDate)` identities, leaf first, each with `taskkill /PID <pid> /F` and no `/T`:
  - A: cmd 7124, then conhost 13960, in 13.2 s;
  - B: cmd 97824, then conhost 88476, in 12.5 s.
- No identity was rejected and none intersected a protected process.
- Nothing was left live, there was no owned leftover and no unexpected leftover.

**Installed path**
- The only process whose command line named INSTALLED was each run's own driver (25520 in A, 85996 in B).
- It is listed under `installed_path_processes_observed_not_owned` and was not matched or killed.

**Run-copy changes**
- 0 files changed in either run.
- The 5 extras are the 5 `.claude-plugin/types/` files named in the types rule. All 5 are absent from INSTALLED and classified `engine_written_types`, with 0 unexpected.
- `claude-code-tools/index.d.ts` went from 0 to 78 tool keys. `claude-code-mcp/index.d.ts` has 0 MCP tool keys.

**Isolation**
- The only inherited variable left was `CIEL_RUNTIME_TEST_ISOLATED`, which the driver sets.
- The live profile (53 files) and the live Ciel state were unchanged.
- `~/.claude.json` has no owned references.
- The secret scan found 0 hits (A: 98 files, B: 76 files).

**Provider key (run B)**
- `scratch_key_scrubbed: true`.
- The scratch `config/config.json` has 68 provider rows, none with `api_key` or `api_keys`.
- The scratch dirs `ciel-sse-true-fork-a-lzcair34` and `ciel-sse-true-fork-b-_ji6g9o2` remain under `%TEMP%`.

# Frames

| Frame | Run A | Run B |
|---|---|---|
| Terminal wake | [03-wake-and-ack.png](/e2e-source/run-a-20261112-083717/03-wake-and-ack.png): main ACKs, and the review is presented at the safe boundary through the Stop hook ("Review side answers at this safe boundary"). This is the review notice, not a wake; `transcript_stop_block_rows` is 0. | [03-wake-and-ack.png](/e2e-source/run-b-20261112-084410/03-wake-and-ack.png): "Prompt from the ciel-work plugin" carries the side event for n1/n1, P4 time_limit "Within 30 minutes", and main reads Walkie. |
| Side desktop 1440 | [side-desktop.png](/e2e-source/run-a-20261112-083717/side-desktop.png): processed 2, failed 1, 2 pending reviews, 0 main handoffs; scroll width 1440, no overflow, no page errors | [side-desktop.png](/e2e-source/run-b-20261112-084410/side-desktop.png): processed 1, 1 pending review, 0 main handoffs; scroll width 1440, no overflow, no page errors |
| Side mobile 390 | [side-mobile.png](/e2e-source/run-a-20261112-083717/side-mobile.png): same content stacked; scroll width 390, no overflow, no page errors | [side-mobile.png](/e2e-source/run-b-20261112-084410/side-mobile.png): same content stacked; scroll width 390, no overflow, no page errors |

The frames were viewed directly. The overflow and page-error values come from `result.json` `web`.

# Observations

## WinError 10054 traceback in the runner logs

- Each runner log has exactly one stdlib `socketserver` "Exception occurred during processing of request from ('127.0.0.1', …)" traceback, ending in `ConnectionResetError: [WinError 10054]` inside `http.server.handle_one_request` → `rfile.readline`:
  - `.local/sse-true-fork/e2e/run-a-installed-57dbfb3.log` (client port 58963);
  - `.local/sse-true-fork/e2e/run-b-installed-57dbfb3.log` (client port 56198).
- The accepted source runs' logs have the same single traceback: `run-a-eab3ad33-3.log` and `run-b-eab3ad33-2.log`.
- Neither run's outcome is affected (`error: null`).
- The driver creates two `ThreadingHTTPServer`s, `SyntheticWalkie` and `RecordingGateway`, and the gateway exists only in run A (`RecordingGateway(out) if kind == 'A'`). In run B, the traceback can therefore only have come from the `SyntheticWalkie` server.
- *Not established:* which server logged it in run A, and which client process closed the connection.

## Orphan `tail -f` from the run A monitor

- After run A, a `tail.exe -n +1 -f …/run-a-20261112-083717/driver-steps.log` was still alive:
  - PID 2932, created 08:37:56.731 local;
  - its parent, PID 76864, was gone.
- It was the leftover of this worker's own run A Monitor, which had exited.
- Before killing it, the exact identity was checked: name, `CreationDate` and the command line naming the run A log. Then `taskkill /PID 2932 /F` (no `/T`). The process table then no longer listed it.
- The run B monitor polled the file in a loop instead of using `tail -f`. After run B, no process named the run B folder or the driver.
- Two other processes matched the driver name during run A: bash 71764 and 101016, created 08:32:05. They were the team-lead's monitor and were left untouched.

## Launch timing

Run A started at 08:37:17. The team-lead's "launch now" message arrived after that, and so did the later "launch B now". Both runs had already been started by the earlier release.

# Citations

- Driver sources and diff: [driver-source/SHA256SUMS](/e2e-source/driver-source/SHA256SUMS).
- Run A: [result.json](/e2e-source/run-a-20261112-083717/result.json), [installed-copy.json](/e2e-source/run-a-20261112-083717/installed-copy.json), [product-pin.json](/e2e-source/run-a-20261112-083717/product-pin.json), [cleanup.jsonl](/e2e-source/run-a-20261112-083717/cleanup.jsonl), [raw-text-inventory.json](/e2e-source/run-a-20261112-083717/raw-text-inventory.json), [wake-timeline.jsonl](/e2e-source/run-a-20261112-083717/wake-timeline.jsonl), [driver-steps.log](/e2e-source/run-a-20261112-083717/driver-steps.log).
- Run B: [result.json](/e2e-source/run-b-20261112-084410/result.json), [installed-copy.json](/e2e-source/run-b-20261112-084410/installed-copy.json), [product-pin.json](/e2e-source/run-b-20261112-084410/product-pin.json), [cleanup.jsonl](/e2e-source/run-b-20261112-084410/cleanup.jsonl), [raw-text-inventory.json](/e2e-source/run-b-20261112-084410/raw-text-inventory.json), [wake-timeline.jsonl](/e2e-source/run-b-20261112-084410/wake-timeline.jsonl), [driver-steps.log](/e2e-source/run-b-20261112-084410/driver-steps.log).
