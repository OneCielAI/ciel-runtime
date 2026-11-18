# Log

## 2026-11-17

- User requested a safe restart: non-destructive worktree backup and takeover, no folder-trust stall, SSE/channel inputs held from the request, and `/restart-session`.
- Measured Claude Code 2.1.326 and 2.1.327 with an owned isolated console probe:
  - the dialog appears on the home folder and a subfolder;
  - `--continue` does not skip it;
  - the default is `No, exit`;
  - Yes is not persisted on the project folder key;
  - `hasTrustDialogAccepted=true` on the forward-slash folder key suppresses it.
- Implemented `trust_restart_folder`, `restart_hold`, `restart_takeover`, the `SessionRestartPorts.relaunch` boundary and `/restart-session`.
- Unit tests discovered a test-side effect: the restart loop had called the real trust/snapshot helpers. Deleted 64 stray refs. The live worktree trust key is true, but the available backups do not prove whether the test changed it. Added the no-op relaunch port so harnesses have no such effect.
- Safe-restart unit group: 305 tests, 0 failures/errors (before the operator surfaces and final grouped sweep).
- E2E run after-3 exposed four test/product facts:
  1. the upstream stub needed Anthropic `/v1/messages`, not only Chat Completions;
  2. marker checks on a 1500-character prefix missed prompts after hook context;
  3. scratch isolation installed slash commands under its config folder while Claude Code read the scratch HOME;
  4. `~/.local/share/claude` in the home repository was 759 MB, so it must be excluded from snapshots;
  5. a Bash `cd` does not change the transcript cwd, so the notice must name every changed worktree.
- Fixed each of those. E2E after-4 on real Claude Code 2.1.327 passed every check, with the same conversation id and zero surviving owned processes. Screenshots captured both MCP and slash restarts.
- The before E2E on 71f5243 failed:
  - the trust dialog reappeared;
  - the old CLI took the held inputs;
  - no takeover line or snapshot was produced;
  - `/restart-session` was unknown.
- E2E after-5 (scratch under `D:\Temp`) passed 12/12 again; its screenshots carry no account path.
- Operator surfaces: REST GET/POST, web Runtime tab, menu section; ctl parity tested. 21 tests pass after; before had 3 failures and 12 errors.
- Moved the slash binding and the hold release into support modules, so `ciel_runtime.py` stays at its 4980-line budget.
- Ruff passes. `claude plugin test ciel-work`: 104 pass.

See [record.md](record.md) and [evidence/e2e-after/result.json](evidence/e2e-after/result.json).
