---
type: implementation-journal
title: SSE/runtime input injection as native Claude /btw side questions
status: verified-in-isolated-real-cli-probes; not committed, not deployed
date: 2026-11-03
description: How external SSE/runtime input is admitted as a native Claude /btw side question, and why /btw cannot answer through MCP.
tags: [btw, sse-injection, session-socket, claude-mods]
timestamp: 2026-11-03T00:00:00Z
---

# Request and user decisions
- Provide a way to inject SSE/runtime messages with `/btw`, and determine whether
  `/btw` can answer through MCP (user, 2026-11-03).
- User selected: native `/btw` passthrough; automatic prefix detection plus an
  explicit `btw=true` option.

# Confirmed facts (evidence-backed)
1. Native `/btw` (official Claude Code docs, interactive mode): the question and
   answer never enter the main conversation history, it can be asked while
   Claude is working, it answers only from what is already in context, and it
   has NO tool access — no file reads, no commands, no search, no MCP. Pressing
   `f` forks the question into a tool-enabled background subagent; that is no
   longer native `/btw`.
2. A session-socket `type=user` frame whose content starts with `/btw` does NOT
   invoke the native command. Real Claude 2.1.317 probe
   `evidence/btw-native-aa189a7e` (and `btw-native-6ce13edf`): Claude rewrote it
   as an ordinary peer turn ("Another Claude session sent a message: /btw ...").
3. Calling `$.command.run` synchronously inside a `prompt.submit` hook is
   rejected by the host ("called from a prompt.submit hook, it would wait on
   the turn this hook is holding") — probe `btw-native-6ce13edf`.
4. Working bridge (probe `evidence/btw-bridge-4ca4f030`, minimal plugin; and
   `evidence/btw-prod-60822e87`, the production ciel-work register.js built by
   this change): a non-composer `prompt.submit` whose text starts with `/btw`
   is dropped, and `$.command.run({command:'btw', args})` is scheduled through
   `$.clock.after(50, ...)`. Claude then opened the native side-question panel
   ("/btw ... · Answering… Esc to close", answer with "↑/↓ scroll · c to copy ·
   f to fork · Esc to close" footer) and made exactly one provider request with
   no peer frame and no `/btw` command text in the user content.
5. `$.command.list()` on Claude 2.1.317 includes the built-in `btw`
   (probe `btw-bridge-1b32f4cd`).

# Answer to the MCP question
Native `/btw` cannot respond using MCP. It has no tool access at all, so the
answer cannot be returned through Ciel's `send_message` MCP tool either; it is
shown only in the native `/btw` panel (and `f` forks it into a tool-enabled
subagent). If an MCP-carried reply is required, the request must be a normal
tool-enabled turn, not `/btw`.

# Implementation (this branch, uncommitted)
Admission (Python):
- `runtime_input_gateway.py`: exact `/btw` prefix detection or explicit boolean
  `btw` stamps `meta.btw=True` and `meta.raw_injection=True` on web-chat, tty,
  notification and stream inputs; malformed flags raise; a quoted `/btw` stays
  ordinary prose. Generic external CloudEvents keep their exact body and are
  not auto-detected.
- `chat_http_controller.py`: `btw` body/query option (boolean and
  1/true/yes/on / 0/false/no/off forms), invalid values return 400
  `invalid_btw` before admission; `btw` is reported in responses.
- `channel_mcp_tools.py`: `submit_input` accepts `btw` (boolean, validated).
- `channel_message_policy.py` / `channel_message_prompt.py`: `message_btw`;
  web-chat and MCP reply contracts are skipped for raw/`btw` messages because
  the side question cannot call the reply tool.
- `channel_pending_injection.py`: `btw` messages never use the router body
  transport; without a whole-message transport they defer with
  `btw_requires_session_socket_or_tty` instead of being mistyped or claimed
  delivered. TTY delivery also works because typing `/btw ...` at the composer
  is the native gesture.

Native bridge (Claude mod):
- `claude_mods/ciel-work/hooks/register.js`: `btwBridge` runs first in
  `prompt.submit`. Composer-typed prompts and command submissions pass through
  untouched; any other origin whose text starts with `/btw` is dropped with a
  visible reason while the native command runs from `$.clock.after`.

# Scope boundary (confirmed, not an oversight)
The bridge ships inside the bundled `ciel-work` mod, which is attached when
Work mode applies (router MCP attached, not a native launch). Outside that
scope an injected `/btw` remains an ordinary raw peer prompt — it is not
silently claimed to be a native side question. Raw socket delivery by itself
never invokes the command (fact 2).

# Related

Verification evidence for every claim above: [verification](./verification.md).

# Citations

[1] Claude Code interactive mode documentation — side questions with /btw (official docs, consulted 2026-11-03).
[2] Isolated probe runs under `evidence/` in this directory (Claude 2.1.317, loopback Anthropic gateway).
