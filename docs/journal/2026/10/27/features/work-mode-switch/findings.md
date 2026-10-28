---
type: Feature
title: Agent-controlled Work mode switch (work_mode_set)
description: The agent can turn Ciel Work mode off and back on by itself through the router tool work_mode_set, which writes the same setting as the menu.
tags: [work-mode, mcp, codex, claude-mod]
timestamp: 2026-10-27
---
# Request

2026-10-27: "에이전트가 스스로 끌 수있어야함." The user chose the menu setting (not a per-conversation switch) and allowed the agent to turn it back on as well.

# Scope correction

When asking, I described the menu setting as PC-wide. That was wrong. Verified on this PC: `work_mode` lives in the per-workspace config `workspaces/<digest>/config.json`, keyed by the launch folder. Router 9505 serves `g:\ciel-walkie` (`aae7db361c7f`, `work_mode: true`); router 9524 serves `c:\users\djlov\ciel-runtime` (`bd5f642b3583`, unset = on). Switching it affects every Ciel session launched from that folder and later launches from it, not other folders.

# Behaviour

- New router tool `work_mode_set(enabled: boolean)`. It writes `work_mode` through the router's `save_config`, which is the same store the menu option `LLM options -> Work mode` uses. Work plans are not touched.
- It is dispatched before the Work-off check, so it works while Work mode is off. While off, the router lists only this Work tool.
- Codex: with the router MCP attached and Work mode off, launch arguments pre-approve only `work_mode_set`, because `approval_policy=never` otherwise blocks it. With Work mode on, all Work tools are pre-approved as before.
- Claude mod: `work_mode_set` is in the Work tool set, so the call carries the conversation identity and the mod re-reads the board afterwards. The prompt context, edit gate and auto continue follow the new value at their next check. Notice, help and `/work-mode` text mention the tool.

# Limits

- A session launched while Work mode was off does not have the Claude mod or the Codex Work instructions loaded. Turning Work mode on from there takes full effect at the next launch; the tool reply says so.
- A Codex app-server launch attaches the router MCP only when Work mode applies or channel delivery needs it (`runtime_launch.py`). A Work-off desktop session without channel delivery therefore has no router tools and cannot turn Work mode on itself. This boundary was not changed.
- Codex keeps the Work instructions it was launched with. Turning Work mode off makes the Work tools answer "off"; it does not remove those instructions from the running conversation.
- Live routers 9505/9524 load new tools only after they run this build. Neither was restarted.

# Verification

- [Python tests](evidence/python-tests.txt): 93 focused tests OK. [Related modules](evidence/python-related-tests.txt): 635 OK (122 skipped). Live router PIDs were unchanged before and after.
- [Mod validate/test](evidence/mod-validate-and-test.txt): validate passed, 66 pass / 2 fail. The [same 2 fail on unmodified HEAD](evidence/mod-baseline-head-test.txt). They are host `ui.log`/toast expectations under Claude Code 2.1.312, not this change.
- [Real Claude Code E2E](evidence/run-source-211931/result.json): 16/16 checks. The model called `work_mode_set(false)`, after which the config was false, the router listed only the switch, and the next turn had no Work context. Edit BEFORE->AFTER-OFF then succeeded. `work_mode_set(true)` set the config back to true, the Work context returned, and Edit was denied with "Work mode is planning". Screenshots: [start](evidence/run-source-211931/01-work-on-start.png), [off](evidence/run-source-211931/02-agent-turned-off.png), [edit while off](evidence/run-source-211931/03-edit-allowed-while-off.png), [on](evidence/run-source-211931/04-agent-turned-on.png), [gated again](evidence/run-source-211931/05-edit-gated-again.png). The first run (`run-source-211825-harness-context-check-misplaced`) is kept: its harness looked for the hook context in the user message, but Claude Code places it in a later message of the turn. The harness was fixed and the product was not changed.
- [Real Codex app-server](evidence/codex-source-212133/result.json): 9/9 checks under `approvalPolicy: never` with Work mode off. With Ciel's launch args the call reached the router and the config went false->true. In the negative control without the pre-approval, Codex answered "MCP tool call requires approval, but approval policy is never" and the config was unchanged.
- Raw request dumps were deleted before commit and the harness no longer writes them. Claude Code had included the user's private global CLAUDE.md text in those requests even with HOME, USERPROFILE and CLAUDE_CONFIG_DIR isolated. Why it was loaded is not verified; the scratch workspace sits under the user's profile directory. `requests-summary.json` keeps only the derived fields the checks use.
- Release and installed verification: [work_mode_set nightly](../../releases/work-mode-switch-nightly/verification.md).
