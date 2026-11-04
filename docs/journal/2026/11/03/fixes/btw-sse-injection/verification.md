---
type: verification-journal
title: /btw SSE injection — verification evidence
status: verified; not committed, not deployed
date: 2026-11-03
description: Test and isolated real-CLI evidence for the /btw injection change, including invalid probe runs that must not be cited.
tags: [btw, verification, e2e]
timestamp: 2026-11-03T00:00:00Z
---

# Unit and hook tests
- Python, focused modules only (no loose sweep near live sessions):
  `python -m pytest tests/test_runtime_input_gateway.py
  tests/test_channel_mcp_tools.py tests/test_chat_http_injection_mode.py
  tests/test_channel_message_prompt.py tests/test_channel_pending_injection.py -q`
  → 115 passed, 40 subtests passed (2026-11-03, after the bridge change; the
  admission layer did not change in this step).
- Claude mod: `claude plugin test .` in
  `ciel_runtime_support/claude_mods/ciel-work` → 84 pass / 7 fail. The 7
  failures are pre-existing on this branch: the identical failure set
  (same 7 test names) reproduces on a HEAD-baseline copy of register.js
  (`/tmp/ciel-work-baseline`). The new test
  "external /btw is bridged to the native command and never enters as a prompt"
  passes: peer `/btw` is dropped, `command.run({command:'btw'})` fires after
  `clock.advance(100)`, composer-typed `/btw` and quoted prose are untouched.
- `claude plugin validate .` → Validation passed.

# Real Claude CLI E2E (isolated, loopback Anthropic gateway, Claude 2.1.317)
Probe harness: `evidence/btw_async_bridge_probe.py` (owned ConPty sessions,
isolated config/workspace, no live router or provider).

- `evidence/btw-prod-60822e87` — production ciel-work register.js built by this
  change, socket frame `/btw BTW-PROBE-QUESTION ...`:
  - screen: "● Prompt dropped by a hook: Ciel: /btw delivered as a native side
    question (see the /btw panel)." then "› Prompt from the ciel-work plugin
    ❯ /btw BTW-PROBE-QUESTION what is already known?" and the native panel
    "· Answering… Esc to close" with the probe answer and the
    "↑/↓ scroll · c to copy · f to fork · Esc to close" footer;
  - exactly 1 provider request; its user content held no `/btw` command text
    and no "Another Claude session sent a message" peer frame.
- `evidence/btw-bridge-4ca4f030` — same result with the minimal bridge plugin;
  intercept record shows `origin: "peer"`; the `$.clock.after` timer fired
  (71 ms after intercept). `$.command.run`'s own result file is not written
  because the native panel command does not resolve while its panel is open;
  panel evidence above is the observable outcome.
- Negative controls (earlier probes, unchanged code paths):
  - `evidence/btw-native-aa189a7e` / `btw-native-6ce13edf`: socket frame
    without the bridge became an ordinary peer turn; a synchronous
    `$.command.run` inside `prompt.submit` was rejected by the host.
  - Invalid runs (do not cite as behavior evidence): `btw-native-2a904a1a`
    (non-interactive `-p` startup error), `btw-native-b96321a7` (onboarding),
    `btw-native-3682303f` and `btw-bridge-ced93f8f` (workspace-trust dialog:
    the ConPty child inherits the spawner's cwd, so the seeded trust entry did
    not match until the probe chdir'd into the workspace before spawn),
    `btw-native-8d0d86cc` (bypass-permissions warning).

# MCP answer (the user's second question)
Confirmed NOT possible for native `/btw`: official docs state side questions
have no tool access, and the bridge probes show the side question answered
entirely inside the native panel. An injected `/btw` therefore cannot reply
through Ciel's `send_message` MCP tool. Use a normal turn (or the panel's `f`
fork) when a tool/MCP-carried response is required.

# Not done here
- No commit, push, nightly publish, or local install was performed for this
  change. Deployment is a separate user-approved step.
- The bridge covers Claude sessions where the bundled ciel-work mod is
  attached (Work mode scope). Other launch scopes keep the raw prompt as an
  ordinary peer prompt; TTY-typed `/btw` remains native everywhere.

# Related

Design and confirmed facts: [implementation](./implementation.md).
