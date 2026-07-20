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

Context mode calculation, localized setup projection, and adapter-owned context
mutation live in `context_setup.py`. Persistence remains outside this service,
while post-mutation caps and timeout recalculation are explicit ports.

Current-model metadata lookup, adapter-owned context projection, and refresh
failure isolation live in `provider_model_specs.py`. Cache identity matching,
configuration mutation, and remote refresh are separate dependency groups.

User-selected timeout profile lookup, localization, projection, mutation, and
LLM preset token overrides live in `timeout_profile.py`, separate from the
context-aware automatic timeout calculation policy.

LM Studio target-context discovery, load/unload lifecycle, and loaded-context
guard behavior live together in `lm_studio_runtime.py`; the facade retains
only compatibility wrappers and composition.

Model-identity context heuristics live in `model_context_hints.py`. Qwen, Kimi,
Z.ai, catalog, and preset hints form one pure ordered policy instead of leaking
string matching into provider orchestration.

Runtime LLM snapshot/restore, preset application, slider navigation, and
status projection live in `runtime_llm_options.py`. Configuration persistence,
presentation, and preset mutation are separate typed port groups; slash action
aliases dispatch through the same controller.

Live API-key status and mutation action dispatch live in
`live_api_key_controller.py`; the HTTP layer receives only masked projection
lines and a changed flag.

Compatibility probe request/response framing and HTTP error decoding live in
`compatibility_protocol.py`; orchestration remains in `compatibility_test.py`.

Compatibility runtime diagnostics and durable result-cache mutation live in
`compatibility_runtime.py`, separate from probe transport and protocol framing.

Claude Code model aliasing, context-limit projection, launch environment
construction, runtime settings, and shell rendering live in
`claude_environment.py`. These policies receive provider/catalog behavior via
small typed port groups; the composition root retains only compatibility
wrappers and dependency assembly.

Managed-router client leases, idle shutdown, health supervision, runner
lifetime, and routed-launch diagnostics live in `router_client_lifecycle.py`.
The composition root supplies health/start/stop callbacks while filesystem,
environment, and thread mechanics stay behind the lifecycle adapter.

Single-process termination, descendant traversal, wrapper-parent discovery,
and process-tree termination share `ProcessTreeController` in
`process_control.py`; they no longer duplicate ps/taskkill/signal mechanics in
the composition root.

Managed router reuse, active-client replacement, version/config mismatch
handling, process spawn, and readiness waiting live alongside shutdown policy
in `router_process_lifecycle.py`. State decisions and spawn effects use
separate typed ports.

Runtime-specific provider choices use immutable strategies in
`provider_choice.py`. Alias normalization and native/routed configuration no
longer branch across Anthropic, AGY, and Codex inside the composition root.

Explicit model selection is coordinated by `ModelSelectionController` in
`provider_model_selection.py`. Provider-specific follow-up updates are adapter
methods, while profile/context/preset/timeout and persistence effects arrive
through bounded typed ports.

Codex backend URL mapping, GET/POST forwarding, capacity retry, and streamed
response delivery live in `CodexBackendHttpAdapter` within `router_http.py`.
Channel mutation/request effects and retry/observability effects are separate
typed port groups.

The events dashboard, filtered recent-event response, and SSE long-poll stream
are projected by `EventHttpAdapter`; the facade supplies EventBus and response
writer ports without owning HTTP streaming loops.

Codex split-MCP HTTP forwarding is isolated in `McpSplitProxyHttpAdapter`.
Server resolution and router response effects are bounded ports, while the
adapter owns upstream HTTP, body/SSE streaming, error projection, and duplicate
native channel-notification suppression as one cohesive transport boundary.

The LLM configuration endpoint uses `LlmConfigHttpController` for presentation
projection and action dispatch. Identity, panel catalogs, mutation strategies,
and HTTP effects are separate bounded ports, keeping provider behavior in its
existing provider/configuration services and HTTP workflow out of the facade.

Router and context activity snapshots use `RuntimeActivityRepository` instead
of three facade-owned atomic-write implementations. Paths, clock generation,
and event/log effects are explicit ports, and snapshot failures are observable
rather than silently swallowed.

NVIDIA's nvd-claude-proxy lifecycle and model-ID translation live beside its
provider adapter in `providers/nvidia_runtime.py`. The provider runtime owns
pip/process/readiness mechanics; configuration and generic HTTP/executable
capabilities arrive through a bounded port instead of facade globals.

AGY's official manifest installation and update lifecycle lives in
`AgyInstaller`. Platform selection, download, checksum, archive extraction,
post-install, and native-update fallback form one installation boundary; the
facade injects executable/version/command effects through a bounded port.

Channel backlog inspection and clearing use `ChannelBacklogService`. Cursor
stores/caches and live session/notification state are separate typed ports, so
the facade only supplies controlled global-state setters and no longer owns the
multi-lock clear transaction.

Upstream tool visibility is projected by `ToolExposurePolicy`. Provider-owned
blocked-tool declarations and request workflow state are explicit inputs, and
the policy removes tools and matching forced choices without mutating the
original request body.

The runtime/provider ownership boundary is materialized by
`RuntimeCommandFactory`. It constructs normalized contracts and delegates argv
generation to the registered runtime adapter; API-key parsing and registry
creation are its only external ports.

Runtime model metadata discovery uses `ProviderRuntimeInfoService`. Compatibility
strategy selects specialized LM Studio discovery or a generic model catalog;
context-field normalization, current-model selection, fallback, and observable
HTTP failure handling no longer live in the facade.

Local Claude Code tool synthesis is split into `SyntheticTasklistPolicy` and
`ForcedPlanModeController`. Conversation continuation decisions and forced-tool
request handling remain separate, while both preserve the public facade entry
points used by router pipelines.

Managed Web, Z.AI, and channel MCP configuration is generated by
`ManagedMcpConfigService`. Executable selection, credential projection, JSON
persistence, and channel cursor initialization are explicit ports rather than
facade-owned filesystem and environment logic.

MCP proxy configuration is materialized by `McpProxyConfigService`. It owns
server precedence, stdio/forced-HTTP proxy selection, per-server artifacts,
notification-stream overrides, and proxy command projection behind bounded
reader/classification/persistence ports.

Chat attachment decoding, size validation, safe naming, storage, URL metadata,
and Markdown projection live in `ChatFileRepository`; only clocks are injected
and callers no longer own filesystem mechanics.

`ChannelMessageRepository` owns the JSONL append transaction as well as reads:
rotation, ID resynchronization, payload projection, duplicate return semantics,
and notification are coordinated under injected lock/condition ports. The
facade retains only its compatibility function and legacy next-ID mirror.

Incremental terminal mouse-report filtering lives in
`channel_terminal_input.py`, next to newline and wake-input policies, rather
than as a stateful parser class in the composition root.
The same platform boundary resolves and validates the Windows console input
handle, including its `CONIN$` fallback; the facade retains only a compatibility
delegate so terminal consumers can keep stable patch and call points.

Generic npm-backed runtime installation and update checks live in
`NpmPackageLifecycle`. Runtime-specific wrappers supply package names and
version readers while npm/prefix/version/output effects use one bounded port.
Self-update uses a separate `SelfUpdateLifecycle` because it must preserve the
current package root/prefix and restart through the freshly installed entrypoint.

## Rejected alternatives

- Moving the monolith unchanged into a differently named file
- Loading source fragments with `exec` into a shared global namespace
- Circular wildcard imports between numbered runtime fragments
- A global dictionary used as an untyped service locator
- Provider-specific branches inside runtime, protocol, or presentation layers

These alternatives reduce a filename's line count without reducing coupling.
