# MCP ownership and Ciel message delivery

## Ownership boundary

Claude Code, Codex, AGY, and other active CLI runtimes own external MCP configuration, transport negotiation, authentication, process startup, reconnects, subscriptions, and shutdown.

Ciel Runtime does **not**:

- scan user or project MCP configuration to discover SSE/Streamable HTTP servers;
- copy or rewrite external MCP server definitions;
- proxy stdio, SSE, or Streamable HTTP transports;
- create or resume external MCP sessions;
- turn external MCP notifications into chat messages;
- inject external MCP messages to wake a sleeping TUI.

Configure external MCP servers with the native CLI. Their protocol compliance and notification behavior are between that CLI and the MCP provider.

## What Ciel still delivers

Ciel retains only its own message paths:

- Web Chat messages submitted through `/ca/chat/messages` or `/ca/channel/messages`;
- explicit Ciel wake/compact operations required by the runtime;
- Web Chat observation through `/ca/chat/wait` and `/ca/chat/stream`.

These are Ciel bridge APIs. `/ca/chat/stream` is a Web Chat event stream, not an external MCP transport.

## Internal Ciel MCP tools

When Web Chat or an explicit Ciel integration needs response tools, the CLI may receive one generated server named `ciel-runtime-router` at:

```text
POST /ca/mcp
```

It exposes only Ciel-owned tools such as `send_message`, `send_file`, `compact_session`, and `llm_options`. The endpoint is stateless and has no MCP GET stream, `/ca/mcp/sse`, session registry, replay cursor, or external notification subscription.

## MCP 2026-07-28

The internal endpoint implements the July 2026 stateless core where applicable:

- `server/discover` instead of `initialize`;
- request `_meta` protocol version and client capabilities;
- `MCP-Protocol-Version`, `Mcp-Method`, and `Mcp-Name` header validation;
- `resultType` on results and cache metadata on discovery/list results;
- one POST endpoint, with no GET notification stream or `Mcp-Session-Id` lifecycle.

For currently installed clients that still use an official 2025 Streamable HTTP revision, the endpoint accepts stateless JSON `initialize`, `tools/list`, and `tools/call`. This compatibility does not restore Ciel-owned transport or lifecycle management.

## Upgrade behavior

On configuration migration, obsolete Ciel keys `claude_code.channels`, `claude_code.development_channels`, and `claude_code.channel_delivery` are removed. Existing user-owned Claude/Codex MCP files are not deleted or rewritten.
