# MCP ownership and Ciel message delivery

## Ownership boundary

Claude Code, Codex, AGY, and other active CLI runtimes own external MCP configuration, transport negotiation, authentication, process startup, reconnects, subscriptions, and shutdown.

Ciel Runtime does **not**:

- scan user or project MCP configuration to discover SSE/Streamable HTTP servers;
- scan or rewrite a runtime's native user/project MCP files;
- proxy stdio, SSE, or Streamable HTTP transports;
- create or resume external MCP sessions;
- turn external MCP notifications into chat messages;
- inject external MCP messages to wake a sleeping TUI.

Configure external MCP servers with the native CLI. Their protocol compliance and notification behavior are between that CLI and the MCP provider.

## Workspace-scoped prelaunch MCP modules

The prelaunch **Workspace MCP modules** menu stores an explicit desired-state list
in the current workspace configuration. It does not modify `.mcp.json`,
`.codex/config.toml`, or either runtime's global configuration. At launch, Ciel
projects only the enabled definitions for the selected runtime:

- Claude receives one private generated `--mcp-config` JSON file;
- Codex and Codex App Server receive equivalent `mcp_servers.*` startup overrides;
- unsupported runtimes receive no silently converted configuration.

Definitions support stdio and Streamable HTTP, a Claude/Codex runtime allow-list,
and an `auto` protocol preference. Transport negotiation, tool calls, reconnects,
and MCP process shutdown remain owned by the CLI.

Every projection is recorded under the stable workspace state directory as an
atomic launch lease. A normal exit removes the generated config and lease. On the
next launch, an abandoned lease whose owner is dead is recovered; a recorded
runtime child is terminated only when its live command identity still matches,
so a reused PID cannot cause an unrelated process to be killed. Invalid and stale
generated artifacts are removed only from that workspace's `mcp-launches`
directory. Secrets should be supplied through environment-variable references,
not literal workspace configuration values.

## What Ciel still delivers

Ciel retains only its own message paths:

- Web Chat messages submitted through `/ca/chat/messages` or `/ca/channel/messages`;
- provider-neutral application events admitted as CloudEvents 1.0 over a Standard Webhooks endpoint or an SSE subscription;
- explicit Ciel wake/compact operations required by the runtime;
- Web Chat observation through `/ca/chat/wait` and `/ca/chat/stream`.

These are Ciel bridge APIs. `/ca/chat/stream` is a Web Chat event stream, not an external MCP transport.

`POST /ca/chat/messages` and its `/ca/channel/messages` alias independently select
how a request enters the active TUI and how the answer should leave it:

- `input_mode=structured` (default) keeps the current Web Chat envelope, attachment
  projection, voice/text hint, and request metadata. `input_mode=tty` puts the
  caller's message text directly on the private terminal-input path without the
  Web Chat input envelope.
- `input_transport=session_socket` is selected automatically while an interactive
  Claude Code session launched by Ciel has its authenticated messaging socket
  active. It sends the selected input without typing into the terminal and can
  deliver while Claude is processing another turn. Other runtimes default to
  `input_transport=tty`, which submits through the active CLI terminal. An
  explicit `input_transport=tty` always retains terminal delivery.
- `input_transport=router` keeps the full message in the
  Runtime Input Gateway: while a model turn is already active, its next request
  consumes the message without a console wake; while the CLI is idle, Ciel types
  a correlated wake turn and replaces that entire turn with the full pending
  message in the next request body. Codex shows the wake marker followed by the
  original projected message in its TUI; Claude and other CLI adapters retain the
  short marker-only wake because their terminal turn behavior is different. In
  both cases the model request receives the queued message exactly once. Router
  transport therefore requires the active model session to use Ciel Router.
  Aliases `llm`, `context`, and `inband` are accepted. Socket aliases `socket`,
  `claude_socket`, and `messaging_socket` select `session_socket`.
- `response_mode=web_chat` (alias `ai_net`, default) uses the normal correlated Web
  Chat reply contract. `response_mode=tty` leaves the model's ordinary terminal
  output as-is and does not require a Web Chat tool reply. `response_mode=mcp`
  supplies a one-request MCP routing hint from `response_mcp`.

The MCP hint is declarative; Ciel does not call the named server itself:

```json
{
  "input_mode": "structured",
  "input_transport": "router",
  "response_mode": "mcp",
  "response_mcp": {
    "server": "ai-net",
    "tool": "send_message",
    "hint": "Reply to the room that originated this request"
  },
  "message": "Summarize the result"
}
```

Input format, input transport, and response mode can be combined independently.
For example, `input_mode=tty` with
`response_mode=web_chat` publishes a correlation record in the browser transcript,
injects the user text without the structured input envelope, and adds only the
one-shot reply routing contract. The legacy `injection_mode=web_chat|tty` parameter
remains supported and maps to the old paired behavior. Unknown modes and an MCP
response without `response_mcp.server` return HTTP 400; they never silently fall
back to another route.

### Claude Code cross-session socket boundary

Claude Code 2.1.239 and later publishes its cross-session messaging socket on
Windows as well as macOS and Linux. For an interactive Claude session, Ciel now
creates an explicit per-launch socket path, passes it with
`--messaging-socket-path`, and enables `crossSessionInbound=accept` in Ciel's
generated runtime settings. Windows uses a local named pipe; Unix platforms use
an AF_UNIX socket.

Each connection authenticates first with the `peerToken` from Claude's
user-private `~/.claude/sessions/<pid>.<socket-hash>.key` file and then submits a
newline-delimited `user` frame. Ciel deliberately omits a peer `from` address:
the existing private Web Chat/MCP response contract remains authoritative, so a
socket input does not invent a second Claude peer as its reply destination.
If the key or socket is not ready, the durable Runtime Input record is retained
and retried; it is not acknowledged or cursor-committed as delivered.

Set `claude_code.session_socket_input=false` to disable automatic socket setup
for a launch, or choose `input_transport=tty|router` on an individual input.

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

The configuration POST accepts `enabled`, `transport` (`webhook` or `sse`), `url`
for SSE, an optional `event_types` allow-list, and either `webhook_secret` or
`authorization`. `input_transport=auto|session_socket|tty|router` controls how
admitted events reach the active runtime; `auto` selects the Claude socket only
while it is active and otherwise selects TTY. Secrets are stored in the local
encrypted workspace vault and are never returned by the GET response.

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

It exposes only Ciel-owned tools such as `submit_input`, `send_message`,
`send_file`, `compact_session`, and `llm_options`. External Streamable HTTP MCP
clients can call `submit_input`; its default transport follows the same active
Claude socket policy and accepts an explicit `session_socket|tty|router`
override. The endpoint is stateless and has no MCP GET stream, `/ca/mcp/sse`,
session registry, replay cursor, or external notification subscription.

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
