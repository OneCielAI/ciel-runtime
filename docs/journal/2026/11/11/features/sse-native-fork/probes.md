---
type: Verification
title: Owned probes for MCP result size and named native forks
description: What Claude Code 2.1.321 returns to a plugin's $.mcp.call for large Walkie-shaped results, and whether a named native fork completes like an unnamed one.
timestamp: 2026-11-11T00:00:00Z
tags: [probe, mcp, native-fork, walkie, claude-code]
---

# Setup

All runs used a real Claude Code 2.1.321 with a temporary `CLAUDE_CONFIG_DIR`, a loopback model gateway, and a fixture MCP server named `walkie` that serves synthetic rows only (stdio, or the loopback streamable-HTTP variants below) ([walkie_size_mcp_server.py](evidence/walkie_size_mcp_server.py), driver [owned_side_probe.py](evidence/owned_side_probe.py)). Every inherited `CIEL_RUNTIME_*`, `CLAUDE*`, `ANTHROPIC*` and `MAX_MCP*` variable was removed. No live router, provider or user configuration was used.

# MCP result size (`$.mcp.call` in a plugin)

The fixture returned 60K, 200K and 600K characters from `walkie_read_messages` and `walkie_call_tool`. The plugin compared the received text with the SHA-256 that the server wrote. Summary: [mcp-size-summary.json](evidence/mcp-size-summary.json).

| Variant | 60K | 200K | 600K |
|---|---|---|---|
| no env (`mcp-size-none-ee41a3e2`) | full text, digest equal | stub, `isError: false` | stub, `isError: false` |
| `MAX_MCP_OUTPUT_TOKENS=500000` (`mcp-size-env-216bc6f5`) | full | full | full |
| server `_meta["anthropic/maxResultSizeChars"]=1000000` (`mcp-size-meta-7f59d22f`) | full | full | full |

## Non-ASCII over HTTP and stdio

Both fixtures served byte-identical synthetic rows mixing Hangul, BMP symbols (`★→€✓`), astral emoji (UTF-16 surrogate pairs) and a ZWJ sequence, as raw UTF-8 (`ensure_ascii=False`; shared generator [walkie_unicode_payload.py](evidence/walkie_unicode_payload.py)). Runs: streamable-HTTP MCP on loopback `mcp-size-unicode-http-fafc8267`, stdio MCP `mcp-size-unicode-stdio-40e5d046`. Every `CIEL_RUNTIME_*`, `CLAUDE*`, `ANTHROPIC*` and `MAX_MCP*` variable was unset (verified count 0). Summary per case: [mcp-size-unicode-summary.json](evidence/mcp-size-unicode-summary.json).

`walkie_read_messages`; `walkie_call_tool` was the same within 14 units:

| Size | Code points / UTF-16 units / UTF-8 bytes | `$.mcp.call` | Stub's stated count | `$.fs.read(saved)`: `.length` / code points / UTF-8 bytes | SHA-256 = server |
|---|---|---|---|---|---|
| ~60K | 60,685 / 70,525 / 144,325 | full text | — | — | yes (direct) |
| ~200K | 201,634 / 233,794 / 474,994 | stub | **233,794** | **233,794** / 201,634 / 474,994 | yes |
| ~600K | 600,755 / 694,595 / 1,398,395 | stub | **694,595** | **694,595** / 600,755 / 1,398,395 | yes |

HTTP and stdio gave identical numbers in all 12 cases. In every stub case the stated count equals the saved text's JavaScript `.length` (UTF-16 code units). It never equals the code-point count or the UTF-8 byte count. `mcpText`'s `full.length === stated` check is therefore in the unit Claude Code uses, so no code change was needed.

### SSE-framed streamable-HTTP responses

A streamable-HTTP server may answer a POST with `text/event-stream` instead of plain JSON. The variant `unicode-http-sse` uses the same loopback fixture and the same text, but frames every JSON-RPC response as one SSE event:

- the response is `Content-Type: text/event-stream`, with `event: message` and `data: {"jsonrpc":"2.0",...}` (raw UTF-8);
- it is sent with HTTP/1.1 chunked encoding in 4093-byte chunks;
- the fixture recorded where chunk boundaries fell inside a multi-byte UTF-8 sequence: 18, 56 and 182 times for `walkie_read_messages` at 60K, 200K and 600K, and 20, 64 and 192 times for `walkie_call_tool` (`http_framing` in each `result.json`).

| Run | Tool | 200K: stated / saved `.length` / SHA | 600K: stated / saved `.length` / SHA |
|---|---|---|---|
| `mcp-size-unicode-http-sse-498dd81b` | `walkie_read_messages` | 233,794 / 233,794 / equal | 694,595 / 694,595 / equal |
| | `walkie_call_tool` | 233,780 / 233,780 / equal | 694,581 / 694,581 / equal |
| `mcp-size-unicode-http-sse-8ea312cd` (repeat) | `walkie_read_messages` | 233,794 / 233,794 / equal | 694,595 / 694,595 / equal |
| | `walkie_call_tool` | 233,780 / 233,780 / equal | 694,581 / 694,581 / equal |

The 60K cases arrived as full text with an equal SHA. In the saved files, code points (201,634 / 600,755 and 201,620 / 600,741) and UTF-8 bytes (474,994 / 1,398,395 and 474,980 / 1,398,381) again differ from the stated count. The stub wording, with the path normalized, is identical to the plain-JSON HTTP run.

Across all four runs (24 cases) the SHA-256 equals the server's every time. In all 16 stub cases the stated count equals JS `.length` and nothing else. SSE framing changes nothing that `mcpText` depends on.

The fixture's server thread logged one `ConnectionResetError` (WinError 10054) per run. The repeat run timed it: the error came at +1.74 s, when the CLI was closed (`cli_close_at` +1.74 s). That was after the last response (+1.57 s) and after the probe finished. It is the keep-alive socket closing at shutdown and touched no measured call.

The harness `tests/side_persisted_unicode_harness.mjs` uses the captured non-ASCII stub ([persisted-stub-unicode-fixture.json](evidence/persisted-stub-unicode-fixture.json)). It accepts a saved text whose UTF-16 length matches, and the fork receives that text exactly. It refuses equal-code-point and equal-UTF-8 lengths.

The stub text is `Error: result (N characters across 1 line) exceeds maximum allowed tokens. Output has been saved to <CLAUDE_CONFIG_DIR>/projects/<project>/<session id>/tool-results/mcp-<server>-<tool>-<ms>.txt.` followed by model instructions. In each of the four stub cases, `$.fs.read` of that exact path returned text whose length and SHA-256 equal the server's.

## Consequence

The host must not trust a bare `$.mcp.call` result for one Walkie read: the Walkie MCP has no `maxResultSizeChars`, and a 200-row page can exceed the limit. `side-intake.js` (`mcpText`) therefore:

- recognizes the exact stub;
- accepts the saved file only when it is `<CLAUDE_CONFIG_DIR>/projects/<project>/<this session id>/tool-results/mcp-<server>-<tool>-<digits>.txt`;
- reads it with `$.fs.read`, and requires its length to equal the count the stub states;
- otherwise fails closed (`source_context`) without spawning.

An environment-variable fix was not adopted. `MAX_MCP_OUTPUT_TOKENS` also changes what every model-visible MCP call returns in main, and the persisted-file path already returns the full text.

# Named versus unnamed native fork (`named-fork-d48887c3`)

The plugin spawned `subagentType: 'fork'` once with `name: 'side-intake-0123456789abcdef'` and once without a name.

| | named | unnamed |
|---|---|---|
| `agent.spawn` hook input | `fork: true`, `subagentType: fork`, origin plugin | same |
| request | main history inherited, same 28 tools, same model | same |
| `$.agent.list()` after turn | `type: fork`, **status `idle`** | `type: fork`, status `completed` |
| `TaskStop` | "not running (status: completed)" | same |
| after `TaskStop` | still listed, **`idle`** | `completed` |
| team files | none | none |

A named fork is a native fork, not a team member. However, it stays `idle` and listed after its turn and after `TaskStop`. Side forks are therefore spawned **unnamed** and bound to their pending row by the exact unique description `Side SSE <12 hex>`. `sideSpawnInput` denies a named side spawn.

# Citations

[1] [Claude Code MCP output limits](https://code.claude.com/docs/en/mcp)
[2] [Earlier stub observation (2026-11-07)](/docs/journal/2026/11/07/fixes/side-large-board/fix.md)
