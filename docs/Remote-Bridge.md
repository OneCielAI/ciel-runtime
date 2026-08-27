# Remote Runtime Bridge

Remote Bridge mode separates the machine running Ciel Router from the machine
running an agent CLI. The remote client uses one authenticated Ciel endpoint;
each request can select a configured bridge-compatible provider and supply an
upstream model ID.

## Start the bridge host

Enable the mode and persist the listening address:

```sh
ciel-runtimectl bridge enable --host 0.0.0.0
```

Run the router in the foreground:

```sh
ciel-runtimectl bridge serve --host 0.0.0.0
```

The same operation is available as `ciel-runtime bridge ...`. Both `enable` and
`serve` print the token so the client can be configured. `status` reports only
whether a token exists; `token` explicitly prints its value:

```sh
ciel-runtimectl bridge status
ciel-runtimectl bridge token
```

Every non-loopback request to a bridge endpoint must authenticate with the
dedicated bridge token:

```http
Authorization: Bearer BRIDGE_TOKEN
```

Anthropic clients that use API-key authentication can put the same bridge token
in `x-api-key`. `X-Ciel-Runtime-Token` is also accepted. These client
authentication headers are removed before any provider request is built.

The bridge token is scoped to the LLM endpoints, model discovery, and bridge
status documented on this page. It cannot call `/health`, router configuration,
or other `/ca/*` control APIs, even when Router debug/Web external access is
also enabled. That administrative mode uses its own separate token.

By default, the bridge token is generated in the active router instance's
`remote-bridge-token` file. `CIEL_RUNTIME_REMOTE_BRIDGE_TOKEN` overrides that
value. The administrative token remains separately stored in
`router-external-token` or supplied by `CIEL_RUNTIME_ROUTER_EXTERNAL_TOKEN`.
If both environment overrides are set to the same value, administrative
authentication fails closed until distinct values are configured.

## Compatible endpoints

The bridge does not have a separate fixed port. It uses Ciel's effective
`ROUTER_PORT`. Run `ciel-runtimectl bridge status` on the Router host and use
the port shown on its `Listen:` line. In the examples below,
`ROUTER_HOST:ROUTER_PORT` is a placeholder for that reported address.

Use `http://ROUTER_HOST:ROUTER_PORT/v1` as an OpenAI base URL and
`http://ROUTER_HOST:ROUTER_PORT` as an Anthropic base URL.

| Protocol | Endpoint |
|---|---|
| OpenAI Chat Completions | `POST /v1/chat/completions` |
| OpenAI Responses | `POST /v1/responses` |
| OpenAI Responses compaction | `POST /v1/responses/compact` |
| Anthropic Messages | `POST /v1/messages` |
| Anthropic token count | `POST /v1/messages/count_tokens` |
| OpenAI model list | `GET /v1/models` |
| OpenAI model detail | `GET /v1/models/{provider}/{model}` |
| Bridge status | `GET /ca/bridge` |

`GET /v1/models` preserves the OpenAI-compatible `object`, `data`, and
`has_more` fields. It also includes an additive `models` array for Codex model
catalog discovery. Ciel leaves that Codex array empty instead of fabricating
partial Codex `ModelInfo` records, so the client retains its bundled/fallback
capability metadata while the OpenAI `data` catalog remains authoritative for
remote provider/model discovery.

### Cross-protocol behavior

The route is selected from the requested provider and model before Ciel chooses
the provider's upstream wire protocol:

| Client request | Selected upstream protocol | Bridge behavior |
|---|---|---|
| Chat Completions | OpenAI Chat | Native passthrough to the provider-owned Chat path |
| Chat Completions | OpenAI Responses | Chat is projected to Responses, the completed response is projected back to Chat |
| Chat Completions | Anthropic Messages | Chat is projected to Messages, collected, and projected back to Chat |
| Chat Completions | Ollama Chat | Chat is projected to Ollama, collected, and projected back to Chat |
| Anthropic Messages | OpenAI Responses | Messages is projected to Responses and returned as Anthropic JSON or Anthropic SSE |

Adapted Chat requests preserve system/developer messages, user and assistant
history, image inputs, tool calls and results, tool choice, stop sequences,
sampling fields, and reasoning effort where the selected upstream protocol can
represent them. Ciel returns HTTP 400 instead of silently dropping a lossy Chat
option such as multiple choices (`n` other than `1`), audio, legacy functions,
log probabilities, non-zero penalties, response formats, prediction, or web
search options. A non-object `function.parameters` value and any non-null
`message.name` are also rejected on an adapted Chat route instead of being
silently widened or discarded.

For converted Chat routes, Ciel first obtains one complete upstream result.
`stream: true` therefore produces valid Chat Completions SSE synthesized from
that completed result; it is not token-by-token passthrough. Native Chat routes
retain their provider stream passthrough. Remote stream collectors require the
upstream protocol's terminal marker and do not emit a normal successful
terminator for a truncated upstream stream.

For the three generation endpoints, an omitted `stream` field is projected as
`false`, matching the non-streaming default expected by compatible API clients.
An explicit `stream: true` remains unchanged.
Providers that require upstream streaming reject non-streaming Chat or
Anthropic Messages requests with HTTP 501; use `/v1/responses` when Ciel must
collect that stream into a non-streaming compatible response.

## Select a provider, model, and provider credential

The portable route syntax is `provider/model` in the standard `model` field:

```json
{
  "model": "openrouter/anthropic/claude-sonnet-4",
  "messages": [{"role": "user", "content": "Hello"}]
}
```

The first segment selects the Ciel provider. The remaining value is forwarded
as the upstream model ID, so model IDs containing `/` remain supported.

Control headers are also accepted:

```http
X-Ciel-Runtime-Provider: openrouter
X-Ciel-Runtime-Model: anthropic/claude-sonnet-4
X-Ciel-Runtime-API-Key: REQUEST_SCOPED_PROVIDER_KEY
```

For raw HTTP clients, the same values can be placed in a private `ciel` object:

```json
{
  "model": "ignored-when-ciel-model-is-set",
  "ciel": {
    "provider": "openrouter",
    "model": "anthropic/claude-sonnet-4",
    "api_key": "REQUEST_SCOPED_PROVIDER_KEY"
  },
  "messages": [{"role": "user", "content": "Hello"}]
}
```

The bridge removes the private object and all `X-Ciel-Runtime-*` control
headers before forwarding upstream. A request-scoped provider key is applied to
an in-memory copy of that provider configuration and is not saved to Ciel's
configuration. This override is accepted only by providers that allow remote
request keys. If no request key is supplied, the provider credential stored on
the bridge host is used.

Request-scoped keys are also isolated from Router-host rate-limit state. Their
usage, learned rate headers, backoff penalties, and per-key cooldowns are not
read from or written to the shared repository. A retry delay can still be
honored within the current request, but it does not penalize later requests
that use the Router host's configured credential.

### GitHub Copilot OAuth

GitHub Copilot is a router-host-managed OAuth route. Complete the device login
on the machine running Ciel Router:

```sh
ciel-runtimectl copilot-oauth login
ciel-runtimectl copilot-oauth status
```

The remote client selects only the provider and model:

```json
{"model":"github-copilot-oauth/gpt-5.6-sol","input":"Hello"}
```

Ciel reads, refreshes, and applies the Copilot token from the router host's
credential store. The client's bridge bearer token is removed before upstream
forwarding. A request that tries to supply `X-Ciel-Runtime-API-Key` or
`ciel.api_key` for `github-copilot-oauth` is rejected; Copilot OAuth credentials
are never accepted from the remote CLI.

When the Router host has Copilot model-card metadata, Remote Bridge publishes
only entries for which Copilot explicitly reports `model_picker_enabled: true`.
Utility, embedding, search, execution-agent, trajectory, legacy, and other
picker-disabled entries are omitted from model list/detail responses and cannot
be selected directly. `mai-code-1-flash` is the stable public ID; Ciel maps it
to Copilot's current `mai-code-1-flash-picker` wire ID only while forwarding the
request. If model-card metadata has not been cached yet, the bundled public
fallback catalog remains available rather than producing an empty model list.

The special `codex` provider's `route_through_router=true` mode is not exposed
through Remote Bridge because that local mode depends on client-local ChatGPT
OAuth header passthrough. The native `codex` and `agy` provider entries are not
bridge routes because both depend on authentication owned by their local CLI
runtimes. `zai-start-plan` is also excluded because its request flow can require
host-side browser/CAPTCHA and shared runtime-interaction state. These providers
are omitted from bridge status/model discovery and direct bridge selection is
rejected. Loopback requests that do not authenticate with the bridge token
retain the existing local routes. Remote Codex clients should select a provider
whose credential is stored on the Router host, such as
`github-copilot-oauth`, or a provider configured with a host/request-scoped API
key.

## Reasoning effort fields

Use the field native to the client protocol. Ciel projects it across the
compatible bridge conversions:

| Client protocol | Request field |
|---|---|
| OpenAI Chat Completions | `reasoning_effort` |
| OpenAI Responses | `reasoning.effort` |
| Anthropic Messages | `output_config.effort` |

The selected provider/model remains authoritative for supported effort values;
the presence of a bridge field does not make every effort level valid for every
model.

## OpenAI-compatible request

```sh
curl http://ROUTER_HOST:ROUTER_PORT/v1/chat/completions \
  -H "Authorization: Bearer BRIDGE_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"model":"vllm/my-model","messages":[{"role":"user","content":"Hello"}]}'
```

## Anthropic-compatible request

```sh
curl http://ROUTER_HOST:ROUTER_PORT/v1/messages \
  -H "Authorization: Bearer BRIDGE_TOKEN" \
  -H "anthropic-version: 2023-06-01" \
  -H "Content-Type: application/json" \
  -d '{"model":"anthropic/claude-sonnet-4-6","max_tokens":256,"messages":[{"role":"user","content":"Hello"}]}'
```

## Codex on the remote machine

Codex custom providers use the Responses wire API. Add this user-level
configuration on the client machine:

```toml
model_provider = "ciel_bridge"
model = "vllm/my-model"
model_reasoning_effort = "low"

[model_providers.ciel_bridge]
name = "Ciel Bridge"
base_url = "http://ROUTER_HOST:ROUTER_PORT/v1"
env_key = "CIEL_BRIDGE_TOKEN"
wire_api = "responses"
env_http_headers = { "x-ciel-runtime-api-key" = "CIEL_PROVIDER_API_KEY" }
```

Set `CIEL_BRIDGE_TOKEN` to the bridge token. Set
`CIEL_PROVIDER_API_KEY` only when this client must override the provider key
stored on the bridge host and the selected provider allows request-key
overrides. The effort can also be overridden for one run:

```sh
codex -c 'model_reasoning_effort="high"' --model vllm/my-model
```

When a Responses request is translated to a provider without a native Responses
wire, the bridge validates Codex's `client_metadata`, `prompt_cache_key`, and
`text.verbosity` fields but does not forward them: the target wire has no exact
equivalent, and client/session metadata must not be repurposed as model input.

Codex CLI 0.150.1 also sends its hosted `web_search` tool when web search is
enabled. If the selected provider uses a non-Responses upstream wire (for
example, `vllm/my-model` over OpenAI Chat), that hosted tool has no lossless
representation on the target wire, so the bridge rejects the request instead
of silently dropping the tool. Disable it for that route:

```sh
codex -c 'web_search="disabled"' --model vllm/my-model
```

This setting is specific to non-native translation targets. A provider that
uses a native Responses upstream can retain the hosted `web_search` tool.

Codex `namespace` tool definitions remain usable on non-Responses wires. The
bridge flattens each namespace/member pair into a collision-resistant portable
tool name, rejecting the request if projected names would still collide. When
the target returns a tool call, the bridge matches it against the source tool
definitions and restores the original Responses `namespace` and member `name`.
An upstream `toolset_name`, when present, must exactly match that source
namespace declaration; it cannot create a namespace for a plain tool.
Strict bridge projection also requires exact lowercase tool discriminators and
rejects leading or trailing whitespace in top-level, namespace, or member names
instead of normalizing an identity that could not be restored losslessly.
The same fail-closed identity rule applies to adapted Anthropic client tools,
tool choice names/types, tool-use/result IDs, and Responses upstream tool-call
IDs/names returned through the Anthropic endpoint.

On a non-native Anthropic route, Codex 0.150.1 freeform grammar support is
limited to its exact official `apply_patch.lark` definitions (base and the
official optional Environment-ID variant) and code-mode `exec` grammar carried in
`format = { type = "grammar", syntax = "lark", definition = "..." }`. Ciel
normalizes only LF/CRLF source line endings when identifying either definition,
projects the original JSON-encoded contract into the tool description, accepts
exactly one string field named `input`, validates the returned raw value against
the matching grammar, and then restores it as a Responses `custom_tool_call`.
Other Lark definitions, unsupported formats, invalid or empty payloads, and
malformed custom-tool returns are rejected rather than coerced.

Responses Lite requests are also handled explicitly. A leading developer
`additional_tools` input item becomes the effective tool list,
`reasoning.context = "all_turns"` is validated and consumed, and
`reasoning_summary_delivery = "sequential_cutoff"` is accepted only for a
streaming request with reasoning summaries enabled. Codex internal turn
metadata is validated against the 0.150.1 fields and is not forwarded to the
selected provider.

Some bundled Codex models advertise native search-tool support. With Codex
0.150.1, `gpt-5.5` plus a deferred tool causes the client to send the hosted
`tool_search` tool even when `web_search` is disabled. That hosted operation
also has no lossless Anthropic or Chat representation, so a non-native route
rejects it. In a client with no other deferred MCP, plugin, app, or dynamic
tools, disabling the default deferred multi-agent tools is the verified minimum
for this route:

```sh
codex -c 'web_search="disabled"' \
  -c 'features.multi_agent=false' \
  -c 'model_reasoning_effort="low"' \
  --model vllm/gpt-5.5
```

`features.tool_search=false` is not an alternative in Codex 0.150.1: that
compatibility flag is removed and ignored. If another deferred tool source is
enabled, Codex can still add `tool_search`; the bridge will continue to reject
that request instead of dropping the hosted tool.

## Claude Code on the remote machine

Claude Code supports an Anthropic-format gateway through its base URL and
authorization-token environment variables:

```sh
export ANTHROPIC_BASE_URL=http://ROUTER_HOST:ROUTER_PORT
export ANTHROPIC_AUTH_TOKEN=BRIDGE_TOKEN
claude --effort high --model anthropic/claude-sonnet-4-6
```

## Security boundary

The bridge token authenticates only the bridge endpoints listed above and never
grants Router administration access. When administrative external access is
enabled, its separate administrator token is the broader credential and can
also reach bridge endpoints. Do not distribute that administrator token to LLM
clients. Plain HTTP does not encrypt either token, prompts, responses, or a
request-scoped provider key; use an encrypted network path whenever traffic
crosses an untrusted network.

Ciel Router trusts direct loopback peers. Consequently, a TLS reverse proxy on
the Router host **must validate the bridge token itself** before forwarding a
request, must forward that token to Ciel so bridge routing is selected, and the
unencrypted Router port must remain unreachable from the external network.
Passing an unauthenticated request through an on-host proxy would otherwise
inherit loopback trust. A network path that preserves the remote peer address
can leave bridge-token validation to Ciel Router.

`/ca/bridge` exposes credential-presence booleans, while model discovery exposes
provider/model metadata; neither response contains credential values.
