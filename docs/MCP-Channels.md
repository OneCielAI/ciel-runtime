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
- provider-neutral application events admitted as CloudEvents 1.0 over a Standard Webhooks endpoint or an SSE subscription;
- explicit Ciel wake/compact operations required by the runtime;
- Web Chat observation through `/ca/chat/wait` and `/ca/chat/stream`.

These are Ciel bridge APIs. `/ca/chat/stream` is a Web Chat event stream, not an external MCP transport.

`POST /ca/chat/messages` and its `/ca/channel/messages` alias accept an optional
JSON field or query parameter named `injection_mode`:

- `web_chat` (the default) publishes the request to the Web Chat transcript and
  supplies the Web Chat reply contract to the active model.
- `tty` sends the request directly to the private runtime input queue through
  the same terminal wake/injection path used by generic events. It is not
  published to Web Chat and does not ask the model to reply through Web Chat.
  Objects and arrays supplied in `message` retain their JSON structure.

Unknown values return HTTP 400; they never silently fall back to Web Chat.

Web Chat attachments are uploaded through `POST /ca/channel/files`. The public
chat transcript contains only download metadata; the private Runtime Input
projection adds a validated workspace-local path so the active Claude or Codex
session can inspect image attachments with its native image-reading tool before
replying. Agent replies can return a local or inline file with the
`ciel-runtime-router` `send_file` tool. The browser renders same-runtime PNG,
JPEG, GIF, WebP, AVIF, and BMP images inline, embeds bounded PDF previews, and
offers bounded text previews. HTML and SVG are shown only as inert source text,
never executed in the chat origin.

## Standard application event inputs

External application events and Web Chat requests enter the same private Runtime Input Gateway. The browser transcript remains a separate repository, so an external event is never published by `/ca/chat/messages`, `/ca/chat/wait`, or `/ca/chat/stream`.

Receiver management is workspace-scoped:

```text
GET  /ca/events/receivers
POST /ca/events/receivers/<receiver-id>
POST /ca/events/webhooks/<receiver-id>
```

The configuration POST accepts `enabled`, `transport` (`webhook` or `sse`), `url` for SSE, an optional `event_types` allow-list, and either `webhook_secret` or `authorization`. Secrets are stored in the local encrypted workspace vault and are never returned by the GET response.

Webhook bodies use CloudEvents 1.0 structured JSON and Standard Webhooks `webhook-id`, `webhook-timestamp`, and `webhook-signature` headers. SSE requests negotiate the same structured representation with `Accept: text/event-stream, application/cloudevents+json`; each `data` frame must contain one CloudEvent 1.0 structured JSON object. Ciel validates framing, signatures, replay windows, type filters, and duplicate identities, but preserves the admitted event text exactly for the LLM.

Reconnects use the SSE `id` field and `Last-Event-ID` by default. Streams that carry a cursor inside the CloudEvent can set `cursor_json_pointer` to an RFC 6901 pointer such as `/data/stream_id`. If the producer expects its cursor in the reconnect URL instead of `Last-Event-ID`, set the provider-neutral `cursor_query_parameter` to that query parameter's name. Ciel persists the projected cursor per workspace and receiver; no product-specific event schema is built into the runtime.

The workspace router is the sole owner of each outbound SSE subscription. The prelaunch settings process only persists receiver configuration, preventing duplicate connections and duplicate deliveries. The terminal bridge also performs a bounded periodic safety rescan of its durable private input queue so a missed filesystem notification cannot strand an admitted event.

Receiver configuration, encrypted credentials, SSE reconnect cursors, private runtime inputs, and terminal delivery cursors are owned by the workspace rather than by a router port. Changing or reallocating the router port therefore changes only process-local router artifacts; durable input state is retained. On the first launch after upgrading, Ciel copies the newest matching legacy `router-instances/<port>-<workspace>` state into the stable workspace state directory without deleting the legacy files.

This feature is an application input bridge, not MCP transport support. Ciel does not inspect MCP configuration or assume ownership of an MCP server's lifecycle.

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
