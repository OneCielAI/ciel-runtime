---
type: Verification
title: Ollama semantic retry - real runtime and Claude CLI E2E
description: Isolated fake-Ollama endpoint, this worktree's runtime serve router and the installed Claude Code 2.1.310, one typed prompt per run.
tags: [ollama, semantic-retry, e2e, claude, sse]
timestamp: 2026-10-24T15:45:31-05:00
---
# What ran

`python -I e2e_semantic_retry.py recover|exhaust [retries]` (driver) with
`e2e_console_agent.py` (detached console reader that types one prompt + Enter).
Reused helpers: `../../../../16/features/cielairouter-direct-path/evidence/e2e/console_driver.py`
and `shot_title.ps1`. Each run: new temp HOME/USERPROFILE/APPDATA/LOCALAPPDATA/TEMP,
runtime config (`CIEL_RUNTIME_TEST_ISOLATED=1`), `CLAUDE_CONFIG_DIR`, `CODEX_HOME`,
workspace; new loopback ports; `launch.cmd` clears every CIEL_RUNTIME_/CLAUDE/ANTHROPIC/CODEX/OPENAI
variable first ([launch-env](run-recover-r1/launch-env.txt)). Provider `ollama`, base URL = fake
Ollama, `empty_response_retries` written explicitly. HTTP(S)_PROXY = local sentinel that refuses
every non-loopback request. `ciel_runtime.py cli --ca-runtime claude` in its own Windows Terminal
window spawned the `serve` router and `claude.exe`. Nothing typed after the first prompt.

# Results (all checks true: [summary](e2e-summary.json))

| run | fake main-turn POSTs | router log | SSE to Claude | transcript |
|---|---|---|---|---|
| [recover r1](run-recover-r1/result.json) | 2: reasoning_only, then answer (replay carries runtime instruction, same base digest) | `ollama_semantic_retry ... reason=reasoning_only`, `ollama_semantic_recovered attempts=2` | 1 message_start / 1 message_stop, outcome success, end_turn | one assistant message: thinking + `OSR-ANSWER-6a23064bbc recovered after the semantic replay.` |
| [exhaust r1](run-exhaust-r1/result.json) | 2: empty, empty | `reason=empty_response`, `ollama_semantic_exhausted attempts=2` | 1/1, outcome exhausted, end_turn | one text: "... after 2 attempts; automatic retries are exhausted. ..." |
| [exhaust r2](run-exhaust-r2/result.json) | 3: empty x3 | retry 1/2, 2/2, `exhausted attempts=3` | 1/1, outcome exhausted, end_turn | "... after 3 attempts; automatic retries are exhausted. ..." |

Each run: one `POST /v1/messages` from Claude, zero aux POSTs, POST count unchanged
through a 25 s quiet window after the response. Screens: `run-*/screens/`, PNG:
`run-*/shots/` (ready, typed, response, after-quiet). Live routers 9385/9464/9489/9505/9524:
same PIDs before and after every run (GET /health only). Teardown killed only the launch.cmd
tree (cmd, launcher, router, claude, conhost); none left alive; E2E windows closed by title.

# Limitations

- SSE evidence is the router's own outgoing trace (`CIEL_RUNTIME_SSE_TRACE=1`) plus the Claude
  transcript and screen; the router-to-Claude bytes were not captured by an independent proxy.
- The sentinel recorded refused CONNECTs to downloads.claude.ai, pypi.org, github.com and
  registry.npmjs.org (source process not attributed). A process ignoring proxy variables would
  not be seen by the sentinel.
- Claude ran with the runtime's bypass-permissions mode; no tool call was exercised.
