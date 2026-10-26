---
type: Verification
title: Work mode usage hints, request triage and queue integrity
description: User-facing Work hints for Claude and Codex, triage guidance for new requests, and a router queue that no longer lets a new request take over the running goal.
tags: [work, hints, queue, triage, claude, codex, e2e]
timestamp: 2026-10-26T12:45:00Z
---
# Requests

User, 2026-10-26, in order:

1. "빠진부분 보강해서 개선 테스트후 로컬 나이트리 배포" - add the missing Work usage hints, test, deploy locally and to nightly.
2. "가능하면 힌트로 첫번째 레벨은 아이템의 번호를 포함해서 저장하는게 유리 하다고 힌트할것" - hint that top-level items should carry their item number.
3. "새로운 요청이올때마다 파악을해서 ... 루트레벨에 아이템을 만들고, 종속되어있으면 ... 차일드레벨에 ... 작업큐가 안꼬이게 관리할수있어야해" - triage each new request into a root item or a child, and keep the queue from tangling.

A Walkie problem report arrived in between; it is queued as its own Work item (2) and is not part of this change.

# Changes

| Area | File | Change |
|---|---|---|
| Claude mod | `ciel_runtime_support/claude_mods/ciel-work/hooks/register.js` | Transcript notice at interactive session start (`$.ui.log`, not sent to the model); `Work mode: no plan yet … /work-mode help` above the prompt; band header `Work planning/running d/t`; up to three `Queued:` rows; planning deny text names `work_plan_set`/`work_execute`/`/work-mode help`; one toast per conversation and plan version; `/work-mode help`; bare `/work-mode` is a readable summary with the queue; `status` keeps JSON |
| Model guidance | `register.js` INSTRUCTIONS, `work_mode_launch.WORK_MODE_INSTRUCTIONS`, `work_mcp_tools` descriptions | Triage every new request: independent = own goal (root item) titled with its item number where possible; dependent = child under the related item; new goals queue |
| Router queue | `ciel_runtime_support/work_plan_tree.py` | `add_node` without a parent queues behind an unfinished active goal; `set_plan` on a queued or finished goal keeps the active slot; finishing (or emptying) the active goal activates the oldest queued goal in planning; `queued_roots()`; snapshot `queue`; text render `Queued goals (next first)` / `Finished goals` |
| Router messages | `ciel_runtime_support/work_mcp_tools.py` | Results say `queued behind the active goal X` and `the next queued goal Y is now active` |
| Codex launch | `work_mode_launch.work_mode_launch_notice`, `runtime_launch.py` | One console line before Codex TUI and app-server launches when Work applies |
| Docs | `docs/Configuration.md` | Hints table, `/work-mode` arguments, triage and queue table |

# Live reproduction of the tangle

On this session's own plan (router 9524, code before this change) adding the Walkie request as a new root while item 1 was executing moved the active goal to the new item and switched the phase to planning, which blocks Claude edits. Record: [queue-tangle-repro.txt](evidence/queue-tangle-repro.txt).

# Verification

- [Mod checks](evidence/mod-check-3.txt): `claude plugin validate --strict` exit 0; `claude plugin test` 23 pass, 0 fail (isolated temp HOME and config).
- [Focused Python](evidence/focused-tests.txt): 576 tests OK on Python 3.14.2 and 3.10.6 (Work tree/tools/store/launch/option/packaging, launch data path, architecture, session restart, Codex runtime, statusline, workspace MCP). [ruff](evidence/ruff.txt) 0.15.22 clean; `git diff --check` clean. Live router PIDs unchanged ([before](evidence/tests-live-before.txt), [after](evidence/tests-live-after.txt)).
- Real Claude Code 2.1.310, owned loopback gateway and router, isolated environment, owned window kept minimized: [harness](evidence/e2e_hints.py), run-6a0d4cec, 10 of 10 checks.
  - `01`: start notice and `no plan yet` line. `02`: `/work-mode help`. `03`: bare `/work-mode` summary.
  - `04`: planning toast at the top right; `05`: deny text on the blocked Edit; the file never received the planning edit.
  - `06`: `Work planning 0/3 — 1. Hint check`.
  - `07`: `Work running 0/3 — 1. Hint check` with `Queued: [ ] 2. Second request 0/1` while the Edit `BEFORE -> AFTER` succeeded.
  - `08`/`09`: after item 1's steps were marked done, `Work planning 0/1 — 2. Second request` and the matching summary.
  - The model's requests carried the triage text and the `work_plan_set` description; the session notice never reached the model; the model received the `queued behind` and `next queued goal` results.
- [Codex prompt check](evidence/codex-prompt-check.json) ([script](evidence/codex_prompt_check.py)): codex-cli 0.158.0 `debug prompt-input` in an isolated `CODEX_HOME`; the developer prompt has the triage, numbering, child, queue and how-to-turn-off texts.

# Start on a mod's first load

The installed-snapshot run of the published e1826009 build (run-111efba6) passed 8 of 10 checks: no start notice, and the band said `Work unavailable — /work-mode status` until the first command ([01 PNG](evidence/run-111efba6/01-start-notice-and-empty-hint.png)).

- Instrumented temporary copies (not shipped; [diff](/docs/journal/2026/10/26/releases/work-hints-nightly/evidence/start-diag/instrumentation.diff)) recorded the start read.
  - With an existing `.claude-plugin/types` directory, 4 of 4 starts read Work at 0 ms (run-21d3e101, run-374aa408, run-4393287d, run-1b68d59b).
  - Fresh copies without it, as a new install is: 2 of 2 failed at 0 ms with `$.mcp.call: no connected MCP tool "work_status" on a server named "ciel-runtime-router"` and 0 router tools, then read Work at 301 and 296 ms (run-1f2dd9a8, run-acc6e897; [records](/docs/journal/2026/10/26/releases/work-hints-nightly/evidence/start-diag/)).
  - Claude Code writes `.claude-plugin/types` into the mod directory at its first load (13:06:47.76 in run-111efba6). Why the router connects later on that load was not determined.
- The mod read Work once at start and never again until the next prompt or command. It now reads again with `$.clock.after` at 250, 500, 1000, 2000, 4000 and 8000 ms when the interactive start read fails, logs the notice once for that start, and stops on success, a new start, or Work off.
- Tests: two new mod tests (retry, bounds/new start/off); [25 pass](/docs/journal/2026/10/26/releases/work-hints-nightly/evidence/mod-check-start-retry-1.txt), and the retry test [fails on the previous code](/docs/journal/2026/10/26/releases/work-hints-nightly/evidence/mod-check-start-retry-old-code.txt).
- Real CLI on fresh first-load copies with the fix: run-b96f7a86 and run-00cb229e, 10 of 10 each. `screen-002` shows `Work unavailable`, `screen-003` (about 200 ms later) shows the notice and `no plan yet` ([01 PNG](evidence/run-b96f7a86/01-start-notice-and-empty-hint.png)).

# Failed or invalid runs

- run-c6b21f5b: keystrokes typed by the person landed in the newly opened E2E window (`분석할것` prefixed the help command). The harness now minimizes its owned window right away and shows it without activation only for screenshots; it also refuses to type into a non-empty prompt.
- run-ef0277ad: toast check matched text that wraps inside the toast box; fixed to match its first line.
- run-a7a6ccd0: the shared console agent exited with `PermissionError` before the first command (harness file race); rerun passed.
- run-1b68d59b: the harness refused to type `/work-mode help` because the prompt already held `cccccccc`, typed into the owned window from outside the harness; nothing was sent.
- run-711742ad passed the hint checks before the queue work; run-9612574c failed one stale harness predicate after the help text changed; run-a5ecb4e8 passed but its queued screenshot was taken after the queue had already advanced, so the gateway now holds item 1 until the screenshot.

# Limits

- The full `ciel-runtime` launcher was not run against real Codex here: launch-time client termination near the live routers was not risked. The console line is covered by `run_codex_app_server` with stubbed services (printed exactly once).
- Item numbers are guidance; the router neither assigns nor enforces them. Whether a request is independent or dependent is the model's judgment.
- Running sessions keep the code they were launched with until they restart.
