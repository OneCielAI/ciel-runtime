---
type: fix
title: Safe restart — no folder-trust stall, worktree snapshots with a takeover line, inputs held from the request, /restart-session
description: A CLI restart (MCP restart_session, ciel-runtime restart-session, and the new /restart-session) now marks the folder trusted before the relaunch, snapshots every changed worktree to non-destructive refs, names them in the resumed session's notice, and holds SSE/channel inputs from the moment the request is claimed until the relaunched session picks them up in order. Real E2E on Claude Code 2.1.327 passes 12/12 checks; the 71f5243 tree fails the same run.
timestamp: 2026-11-18T01:45:00Z
tags: [restart, trust-dialog, worktree, snapshot, takeover, sse, hold, slash-command, celly, claude-code]
---

# Requirements (user, 2026-11-17)

1. **R3.** A restart command backs up and organizes the worktrees being worked on, and the relaunched session takes them over. Verify that the restart is safe.
   - User decision: a non-destructive snapshot ref.
2. **R1.** A restart never stops at the folder-trust question ("y/n"). Celly stalls there; `--continue` does not skip it.
3. **R2.** SSE messages that arrive during a restart are kept, not consumed, and picked up after the restart.
   - User decision: the hold starts the moment the request is received, including the wait for the running turn.
4. **R4.** Add a slash entry. User decision: `/restart-session`, the same operation as MCP `restart_session` and `ciel-runtime restart-session`.

Plan: `~/.claude/plans/starry-brewing-candle.md` (approved).

# Measured before the change

## Trust dialog (owned probe, isolated HOME, real claude.exe)

Script: [evidence/trust_probe.py](evidence/trust_probe.py).

| Probe | Claude Code | Case | Dialog shown | Folder key after |
|---|---|---|---|---|
| 1 | 2.1.326 | home folder, untrusted | yes | unset |
| 1 | 2.1.326 | home folder, `hasTrustDialogAccepted=true` pre-written | **no** | true |
| 1 | 2.1.326 | subfolder, untrusted / pre-written | yes / **no** | unset / true |
| 1, 2 | 2.1.326, 2.1.327 | home and subfolder, answered "Yes, I trust this folder" | yes | **unset** (Yes is not stored on the folder key) |
| 3 | 2.1.327 | home and subfolder after the product's `trust_restart_folder` | **no** | true (forward-slash key) |

- The home-folder dialog says "This is your home folder, so Yes lasts for this session only".
- Its options are "❯ No, exit" (selected by default) and "Yes, I trust this folder" ([evidence/trust-probe-1/home-untrusted.screen.txt](evidence/trust-probe-1/home-untrusted.screen.txt)). An Enter from an auto-answerer therefore exits Claude Code.
- Celly's `G:\ciel-bridge-service\.claude.json` holds `'G:/ciel-bridge-service': hasTrustDialogAccepted=false`. Its home folder is the repository.

## Restart path (code reading)

The launcher waits for the turn (up to 900 s), kills the tree, optionally backs up, relaunches with `--continue`, and types `계속-세션재시작됨`. Before this change it did nothing with git or folder trust.

Pending channel rows could be lost in three ways:
- the launch fast-forward skipped rows older than 600 s;
- the pending poll kept delivering to the dying CLI (`interrupted_uncertain`, never replayed);
- whether `--continue` keeps the session id (side inbox key) was unknown.

# Change

## R1 — trust before the relaunch

- `claude_user_settings.trust_restart_folder(folder)` writes `projects[<forward-slash key>].hasTrustDialogAccepted = true`.
  - It writes under Claude Code's own `<config>.lock`.
  - An unreadable file or a non-object `projects` leaves the file unchanged.
- `runtime_launch._trust_restart_folder` calls it before every Claude relaunch and logs `claude_restart_folder_trusted key=… changed=…`. A first launch is never changed.

## R2 — hold from the claim

`restart_hold.py`:
- `RuntimeSessionRestartService.claim` / `claim_after_exit` write `restart-hold-<launcher pid>.json` (`request_id`, `started_at`) and log `runtime_session_restart_hold_started`.
- While the transport's restart state holds a request (claimed or deferred for the turn), the pending poll delivers nothing. This covers both console proxies (Windows and POSIX, `external_admission_paused`) and the direct poll in `_await_child`. Rows stay queued behind the cursor; already submitted inputs keep blocking the restart until confirmed, as before.
- At the relaunch, `prepare_for_launch` never fast-forwards past `started_at` (`restart_hold_since=` in its log line). It then removes the marker. A marker older than 3600 s is ignored and removed.

## R3 — snapshots and takeover

`restart_takeover.py`. It runs after the old CLI exited, before the relaunch, so nothing writes the files meanwhile.
- Every worktree of the launch folder's repository is listed with `git worktree list --porcelain`.
- Each changed worktree gets a snapshot commit built with a temporary `GIT_INDEX_FILE`: `read-tree HEAD` → `add -A` → `write-tree` → `commit-tree -p HEAD`. The commit is stored at `refs/ciel/restart/<request id>/<slug>`.
  - The real index, the working files and every branch are untouched. Ignored files are not captured.
  - Trust uses exact `safe.directory` roots, never a wildcard.
- Paths left out of every snapshot:
  - nested worktrees;
  - the CLIs' own state: `~/.claude`, `~/.claude.json`, `~/.codex`, `CODEX_HOME`, the ciel-runtime config folder;
  - Claude Code's native install (`~/.local/bin/claude(.exe)`, `~/.local/share/claude`). The E2E home held 759 MB of it, which made the home repository "too large to snapshot".
- A worktree with more than 5000 untracked files or 512 MB is recorded as skipped.
- Snapshot refs of the newest 10 restarts are kept.
- `restart-takeover.json` in the router instance folder lists every worktree and the session's `cwd` (from the newest transcript).
- The notice line keeps `계속-세션재시작됨` first and appends:
  - where the session was;
  - every changed worktree by path, branch, HEAD and snapshot ref (up to 5, then "외 N곳");
  - "목록: restart-takeover.json";
  - the hold note.
- Measured, two E2E runs with the same steps:
  - Scratch under `D:\Temp` (run after-5): the transcript `cwd` followed the session's Bash `cd` into the worktree (13 rows), and the notice names that worktree as the work location.
  - Scratch under the account's `%TEMP%` (runs after-3/after-4, inside a folder tree whose drive root `C:\` is itself a git repository on this machine): it stayed at the launch folder. Why the two differ is not established.
  - Either way, after the relaunch the `cwd` is the launch folder again: `--continue` does not restore the location.
  - So the notice names every changed worktree as well, not only the recorded location.
- Wiring: `SessionRestartPorts.relaunch`. The Claude launch assembly binds `runtime_launch.claude_restart_takeover` (trust → snapshot → hold note); the default is a no-op, so harnesses that drive the loop never touch `~/.claude.json` or repository refs. An exception never blocks the restart (`session_restart_relaunch_prepare_failed`).

## R4 — `/restart-session`

- `slash_command_assets.RESTART_SESSION_SLASH_COMMAND` defines the command; its marker is `CIEL_RUNTIME_RESTART_SESSION`.
- `restart-session.md` is installed with the other Ciel commands.
- The router intercepts it (`router_shortcuts.RestartSessionShortcut`) and queues the restart with `source=slash`. It answers without calling the model, so the turn ends at once and the restart proceeds.

## Operator surfaces (menu, web, REST, ciel-runtimectl)

Every surface goes through the same `RuntimeSessionRestartService` (`queue` / `instances` / `clients`). New helper module: `runtime_session_restart_menu.py`.

- **REST, status.** `GET /ca/work/api/v1/runtime` (and `/ca/work/runtime`) now includes `runtime.restart` with:
  - the live clients;
  - the pending request;
  - the holds: `started_at`, request id, pid, and `inputs_since_hold` (counted from input ids; `null` when they cannot be read, never a made-up 0).
- **REST, restart.** `POST /ca/work/api/v1/runtime/restart` queues a restart.
  - It requires `confirmed=true`, otherwise 422.
  - It validates `reason`, `runtime` (claude, codex or empty), `resume` and `client_pid`.
  - The source is derived by the server: `web` for a signed-in browser, `rest` for a token or loopback.
  - Only this router instance's active clients can be targeted; any other pid is refused with 409 `restart_not_queued`.
  - The current-session fail-closed rule applies, as on every Work route.
- **Web console.** A new Runtime tab shows clients, the pending request and the holds (an unreadable count shows "unknown"). It queues a restart with session, reason, runtime and resume (`--continue`) after a danger confirmation.
- **Prelaunch menu.** The "Unattended restart" panel gains a "Safe restart of a live session" section. It lists live sessions, the pending request and the holds. Choosing a pid asks yes/no (default no), then the reason and resume, and queues with source `menu`.
- **ciel-runtimectl.** Not changed. The existing `restart-session --reason/--runtime/--pid/--workspace/--no-resume` already reaches `RuntimeSessionRestartService.command`; parity is tested. Its source stays `cli`, as before.
- **Tests.** `tests/test_runtime_session_restart_surfaces.py` (21) uses fake pid records and a fake `is_running` only. Before: 3 failures, 12 errors; after: all pass. Evidence: [evidence/restart-surfaces-before.txt](evidence/restart-surfaces-before.txt), [evidence/restart-surfaces-after.txt](evidence/restart-surfaces-after.txt), [evidence/restart-surfaces-regression.txt](evidence/restart-surfaces-regression.txt).

Architecture budget: `ciel_runtime.py` must stay at 4980 lines or fewer (`test_runtime_compaction_binding`). The slash binding is `RestartSessionShortcut.for_slash`, and the hold release is `channel_delivery_context.restart_hold_release`. Both live in support modules, so the file stays at 4980 lines.

# Verification

## E2E (real ciel-runtime launcher, real claude.exe 2.1.327, isolated)

- Source: [e2e-source/e2e_safe_restart.py](e2e-source/e2e_safe_restart.py).
- Isolation: a stub upstream, scratch config and home under %TEMP%, every inherited `CIEL_RUNTIME_*`/`ANTHROPIC_*`/`CLAUDE_*` variable cleared.
- The home folder is a git repository (Celly's shape).
- The session's own Bash tool creates `.claude/worktrees/feat`, edits a tracked file and adds an untracked one.
- During a 45 s turn, MCP `restart_session` is called, then three channel inputs are sent; after the relaunch, `/restart-session e2e slash` is typed.

| Check | after (working tree) | before (71f5243) |
|---|---|---|
| trust dialog only on the first launch | ✅ | ❌ shown again at the relaunch |
| restart waited for the running turn | ✅ (request 19:08:37, turn replied 19:09:20, CLI stopped 19:09:22) | ✅ |
| held inputs sent during the wait | ✅ | ✅ |
| held inputs delivered after the relaunch, once each, in order | ✅ (request 8 carries HELD-1, 2, 3 in order; each appears once in the final history) | ❌ |
| held inputs not given to the old session | ✅ | ❌ the old CLI took all three (19:13:44, CLI stopped 19:13:47) |
| held inputs precede the notice | ✅ (inputs request 8, notice request 10) | ✅ |
| notice names the worktree | ✅ | ❌ notice is `계속-세션재시작됨` only |
| snapshot holds the work (tracked edit and untracked file) | ✅ | ❌ no snapshot, no manifest |
| worktree files, index and branches unchanged | ✅ | ✅ |
| same conversation after `--continue` | ✅ | ✅ |
| `/restart-session` restarts with `source=slash` | ✅ | ❌ `Unknown command: /restart-session` |
| E2E processes left | 0 | 0 |

- Session id: the transcript file is the same before and after `--continue` (`session_id_kept: true`), so the side inbox key does not change and no migration is needed.
- The table's "after" column is run after-4. Run after-5 (scratch under `D:\Temp`, so screenshots carry no account path) passed the same 12 checks.
- Evidence:
  - [evidence/e2e-after/](evidence/e2e-after/): after-5 with screenshots. `shots/03-after-restart-1.png` and `shots/04-after-restart-2.png` show the notice with the work location, the snapshot ref and the hold note.
  - [evidence/e2e-after-root-cwd/](evidence/e2e-after-root-cwd/): after-4, text only. The session location stayed the launch folder, and the notice lists the changed worktree.
  - Paths in text files are redacted.

### Before (71f5243)

Evidence: [evidence/e2e-before/](evidence/e2e-before/). The text is redacted; screenshots are left out because they show the account's temp path.

- **Trust dialog.** The dialog appeared again at the relaunch. In this run the existing startup auto-answerer chose "Yes, I trust this folder" about 3 s later (`startup_prompt_answered prompt=claude_trust option=yes,itrustthisfolder key=downx1+enter`), so the run did not stall.
  - This E2E therefore does **not** reproduce Celly's stall. Why the auto-answer did not get Celly past it is unknown: Celly's router log is not readable from this account.
  - The fix removes the dialog itself instead of relying on answering it.
- **Held inputs.** The old CLI submitted all three as one turn right after the slow turn ended, 3 s before it was stopped. They reached the model through the dying session, not the relaunched one.
  - Here they stayed in the conversation because `--continue` resumed it. A row submitted while the CLI is killed ends `interrupted_uncertain` and is not replayed (code reading; not forced in this run).

## Unit tests

| Module | Covers |
|---|---|
| `test_restart_takeover` (10) | non-destructive snapshot; foreign owner; session cwd; notice naming every changed worktree; skipped/failed marking and cap; prune; CLI state exclusion; size guard; non-git |
| `test_restart_hold` | marker semantics; stale/broken marker; poll gate; launch fast-forward stops at the hold |
| `test_runtime_session_restart` | claim starts the hold; the relaunch port runs once before the notice; a failing port never blocks; trust → snapshot → hold note order; the assembly wires the port; the home folder's CLI state and native install are excluded |
| `test_claude_user_settings` | trust key, lock, unreadable file |
| `test_slash_command_assets` | asset and shortcut |

No test run writes `~/.claude.json` or creates repository refs. Measured after the run: `refs/ciel/` count 0, and `~/.claude.json` mtime/size unchanged.

# Incident during development (disclosed)

- An earlier unit run drove the real restart loop before the relaunch port existed. It created 64 `refs/ciel/restart/*` refs in this repository; they were deleted (count 0).
- It may also have written `projects["…/.local/tmp/work-parallel"].hasTrustDialogAccepted = true` into the live `~/.claude.json`. That key is true in every available backup, so whether the run wrote it cannot be proven. It was left as is.
- The relaunch port (default no-op) now prevents both effects.
