# ADR 0001: Runtime bounded contexts and file-size budget

## Status

Accepted. Migration is in progress on `architecture/runtime-evolution-2026-07-19`.

## Decision

`ciel_runtime.py` becomes a compatibility facade and composition root. It may
select adapters and assemble typed services, but it must not own provider wire
logic, persistence, UI rendering, channel transport, retry state machines, or
runtime process implementations.

Production code is partitioned into these inward-facing contexts:

1. `architecture`, registries, policies, and immutable domain values
2. provider and protocol adapters
3. channel application services and infrastructure adapters
4. router application services and HTTP adapters
5. runtime launch/process application services
6. configuration repositories and migrations
7. presentation policies and terminal/web adapters
8. the top-level composition root and compatibility facade

Dependencies point from adapters and composition toward domain/application
contracts. Support modules never import `ciel_runtime`. Cross-context behavior
uses typed ports or immutable values, not provider-name conditionals or a shared
service-locator dictionary.

Every production Python file must remain below 5,000 physical lines. The final
facade budget is 4,999 lines. During migration, `MAIN_FILE_LINE_BUDGET` is a
monotonically decreasing ratchet and may never be raised.

## Current channel boundaries

- `channel_mcp_transport.py` owns MCP transport configuration and state.
- `channel_notification_projection.py` maps chat records to MCP notifications.
- `channel_compact_request_repository.py` owns the durable compact-request slot,
  expiration, and atomic persistence boundary.
- `channel_cursor_repository.py` owns cursor file I/O, while
  `channel_cursor_service.py` owns cursor advancement and client-resume policy.

The facade only composes these objects and retains narrow compatibility
functions for callers that still import historical private names.

Codex configuration parsing and MCP server discovery live in
`codex_config.py`; runtime launch orchestration consumes their projections and
does not parse TOML itself.

Codex launch argument decisions live in `codex_launch_policy.py`, the local
resume index is isolated behind `codex_session_repository.py`, and bundled
model projection plus atomic catalog writes live in `codex_model_catalog.py`.

Provider-emitted pseudo tool envelopes are parsed by
`pseudo_tool_parser.py`, and transport-independent word buffering lives in
`stream_chunk_policy.py`. Provider adapters compose these policies instead of
embedding their grammars.

Upstream failure classification and localized retry presentation live in
`upstream_error_policy.py`. Cross-runtime transcript discovery, bounded JSONL
reading, and response projection live behind the Repository and Application
Service in `session_import.py`.

Locally generated Anthropic JSON/SSE responses are serialized by the HTTP
Adapter in `anthropic_response_writer.py`. Its single block serializer handles
text, thinking, redacted-thinking, and tool-use projections without facade
duplication.

Provider wire request construction lives in `provider_request_builder.py`.
Separate typed ports own budgeting, Ollama projection, OpenAI projection, and
provider option policy; each port is capped at ten dependencies.

Upstream socket timeout, downstream disconnect detection, cancellable line
iteration, and retry sleep live in the Infrastructure Adapter
`upstream_stream_io.py`.

Provider-specific Advisor message projection, input budgeting, request/response
mapping, and endpoint selection live in `advisor_request_builder.py`. Network
and rate-limit side effects remain outside this pure Builder boundary.

Advisor feedback injection and the optional second-pass response decoration
live in `advisor_refinement.py`; typed text, policy, and I/O ports keep the
refinement workflow independent of provider transport implementations.

Advisor network execution and refinement provider calls live in
`advisor_client.py`. `AdvisorClient` owns review I/O while
`ProviderChatExecutor` selects the Ollama or OpenAI-compatible transport
strategy through typed policy and I/O ports.

MCP channel capability probing, durable cache persistence, record
classification, capable-server projection, and source-path lookup live behind
`ChannelProbeCacheRepository` and `ChannelProbeService` in
`channel_probe_cache.py`.

The same service owns launch candidate projection, cache-refresh decisions,
and refresh failure isolation so launch orchestration does not depend on the
probe cache schema.

HTTP MCP configuration projection, environment-backed authentication headers,
external server discovery, allow-list filtering, and automatic connection
startup live in `channel_mcp_discovery.py`.

Notification-stream ownership is read by `ChannelProxyOwnershipRepository`,
while `ChannelRouterLifecycle` filters proxy-owned servers and starts only the
router-owned workers. Both live in `channel_mcp_ownership.py`.

Persisted channel lists, passthrough imports, delivery-mode normalization, and
add/remove/clear mutations live in `channel_config_service.py`; CLI handlers
only translate command arguments and render returned messages.

Channel CLI parsing and presentation live in `channel_cli.py`. Its controller
uses separate view and command ports and never reads or writes configuration
files directly.

Provider and LLM option status projection lives in
`provider_option_status.py`. It consumes adapter-declared context and
presentation policies through an explicit ten-field port, keeping
provider-specific display rules outside the compatibility facade without
reintroducing provider-name dispatch.

Provider option command orchestration lives in `provider_option_cli.py`.
The controller coordinates provider selection, persistence, model-cache
invalidation, recommended limits, and presentation through three ports of at
most five dependencies; mutation rules remain in provider policies.

Context-relative output caps are pure policies in `provider_context.py`.
Context-aware request timeout calculation and mutation live in
`provider_timeout_policy.py`, which composes adapter context policy with model,
catalog, preset, and output information through explicit settings and ports.

## Rejected alternatives

- Moving the monolith unchanged into a differently named file
- Loading source fragments with `exec` into a shared global namespace
- Circular wildcard imports between numbered runtime fragments
- A global dictionary used as an untyped service locator
- Provider-specific branches inside runtime, protocol, or presentation layers

These alternatives reduce a filename's line count without reducing coupling.
