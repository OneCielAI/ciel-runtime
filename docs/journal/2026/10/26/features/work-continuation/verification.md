---
type: Verification
title: Work continuation verification
description: Focused tests and owned real Claude CLI exercise normal Stop continuation, priority, urgent holds, waits, linked ids and completion.
tags: [work, continuation, verification]
timestamp: 2026-10-26T23:40:00Z
---
# Verified

- Python focused tests: 92 passed ([output](evidence/01-python-tests.txt)). Includes tree/store/MCP, exact-session isolation, version rollback, labels, nested urgent resume, queue priority, statusline, Codex instructions and menu configuration.
- Claude Code 2.1.310 mod: strict validation passed; 34 tests passed ([output](evidence/02-mod-tests.txt)). Existing missing plugin listing icon warning is non-blocking.
- Ruff whole repository: passed ([output](evidence/03-ruff.txt)).
- Owned real Claude CLI against a loopback fixture and production router: all 11 assertions passed with one user model prompt. Normal end_turn resumed without a dot, urgent end_turn resumed while holding other goals, prior active resumed, all four goals completed, and model requests stopped after completion ([result](evidence/run-29886202/result.json), [driver](evidence/04-real-cli.txt)).
- Screenshots: [three levels and labels](evidence/run-29886202/01-normal-stop-three-levels.png), [urgent holds](evidence/run-29886202/02-urgent-holds.png), [done and wait](evidence/run-29886202/03-done-check-and-wait.png), [all done](evidence/run-29886202/04-all-done.png). PrintWindow succeeded for all captures.
- Live router PIDs and fingerprints at 9505/9524 stayed unchanged before/after the CLI run. The egress sentinel blocked downloads.claude.ai and github.com connection attempts; all fixture inference was loopback.

# Corrections during verification

- Label clipping at the length boundary retained a trailing space, making normalized roundtrips differ. Added rstrip and reran the regression tests.
- Updated prior count-only statusline test expectations to explicit third-level rows and check marks. Codex tool approval counts now derive from the tool inventory, including the two new tools.
- First menu capture failed its harness assertion that a default key must remain absent after a save. Config merging writes default values; the assertion now checks semantic on. Retry failed because stale menu.pid referred to the prior exited console. A fresh directory is used for the next attempt. These are recorded as failed attempts, not feature passes.

# Additional verification

- Python 3.10: same 92 focused tests passed (two tomllib-dependent tests skipped); Python 3.14 ran those tests as well.
- Real Claude CLI root priority pane: native Tab/Enter pressed the queued goal's Move up button; the production router made it active. Both assertions passed, screenshots captured ([result](evidence/run-efa3e3dd/result.json), [after click](evidence/run-efa3e3dd/03-priority-order-after-click.png)).
- Deterministic OKF strict validation passed with no issues; documentation metadata check passed (30 Markdown files).

# Actual menu

Fresh owned console run passed all nine checks, including default auto continue on and Enter off/on with persisted config. All nine PNG captures succeeded with PrintWindow; owned menu and host exited ([result](evidence/menu-clean/result.json), [off](evidence/menu-clean/08-auto-continue-off.png), [on](evidence/menu-clean/09-auto-continue-on.png)).

# Not yet verified

- Installed-package verification and local/nightly release are pending.
- Codex automatic resumption is not implemented: only instructions are provided.
