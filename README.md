<div align="center">

# Ciel Runtime

**Run the coding agent you want on the model provider you choose.**

Ciel Runtime is a cross-platform launcher, local protocol router, and workspace
control plane for Claude Code, Codex, Codex App Server, AGY, Grok Build, and
compatible AI coding-agent CLIs.

[![npm](https://img.shields.io/npm/v/@oneciel-ai/ciel-runtime?label=npm)](https://www.npmjs.com/package/@oneciel-ai/ciel-runtime)
[![CI](https://github.com/OneCielAI/ciel-runtime/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/OneCielAI/ciel-runtime/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![Node.js 18+](https://img.shields.io/badge/node-%3E%3D18-339933?logo=node.js&logoColor=white)](package.json)
[![Python 3.10+](https://img.shields.io/badge/python-%3E%3D3.10-3776AB?logo=python&logoColor=white)](docs/Installation.md)

[Install](#install) · [Quickstart](#quickstart) · [Capabilities](#what-ciel-runtime-does) · [Providers](docs/Providers.md) · [CLI reference](docs/CLI-Reference.md) · [Changelog](CHANGELOG.md)

</div>

## Why Ciel Runtime

Coding-agent CLIs and model APIs do not all speak the same protocol. Ciel
Runtime keeps the native CLI experience while handling provider selection,
protocol translation, model metadata, workspace context, message delivery, and
operational telemetry in one local runtime.

Use it when you need to:

- run multiple coding-agent CLIs without maintaining separate provider setups;
- connect Anthropic, OpenAI-compatible, Ollama, and provider-specific APIs;
- keep tools, thinking, streaming, and context behavior intact across protocol boundaries;
- synchronize project-scoped instructions and memory before every launch;
- deliver external messages to active sessions and export transcript deltas;
- audit routed token usage without storing prompts or provider credentials.

## Install

```sh
npm install -g @oneciel-ai/ciel-runtime
```

The package installs three commands:

| Command | Purpose |
|---|---|
| `ciel-runtime` / `cielrt` | Configure and launch an agent runtime |
| `ciel-runtimectl` | Inspect and change providers, models, memory, events, and router settings |
| `ciel-runtime-stop` | Stop the managed local router |

Requirements: Node.js 18 or newer, Python 3.10 or newer, and at least one
supported coding-agent CLI or provider credential. Set `CIEL_RUNTIME_PYTHON` to
select a specific Python executable. See the [installation guide](docs/Installation.md)
for shell installers, unattended setup, and diagnostics.

## Quickstart

Start the interactive setup and launch Claude Code:

```sh
ciel-runtime
```

Or configure a provider explicitly:

```sh
ciel-runtimectl provider openrouter
ciel-runtimectl set-api-key openrouter YOUR_API_KEY
ciel-runtimectl model MODEL_ID
ciel-runtime
```

Launch another runtime while preserving its remaining CLI arguments:

```sh
ciel-runtime codex
ciel-runtime agy
ciel-runtime grok
ciel-runtime --ca-runtime codex-app-server
```

Check the effective configuration and upstream compatibility:

```sh
ciel-runtimectl status
ciel-runtimectl models
ciel-runtimectl test
```

## What Ciel Runtime does

| Capability | What it provides |
|---|---|
| Runtime launch | Claude Code, Codex, Codex App Server, AGY, and Grok Build from one entrypoint |
| Provider routing | Native connections where supported; otherwise a loopback HTTP router with provider-owned endpoint and authentication rules |
| Protocol adaptation | Anthropic Messages, OpenAI Chat, OpenAI Responses, Ollama Chat, tool calls, thinking blocks, and SSE streams |
| Model control | Provider catalogs, context/output limits, reasoning effort, sampling options, API-key rotation, and rate-limit handling |
| Workspace context | Remote instructions plus atomic, workspace-scoped OKF/Markdown/JSON/YAML/TOML/text memory trees |
| External channels | Web Chat and explicit event delivery to live terminal sessions with runtime-specific submission policies |
| Transcript delivery | Incremental CloudEvent webhooks with durable cursors around normal turns and compaction boundaries |
| Usage observability | Workspace SQLite ledger, authenticated events/SSE/snapshots, outbound delivery, daily audits, and legacy JSONL backfill |
| Recovery | Invalid replay repair, repeated-tool protection, context compaction, Windows ConPTY Unicode handling, and launch-state isolation |

## Runtimes and providers

Ciel separates the **runtime** (the coding-agent CLI) from the **provider** (the
model API). A runtime/provider pair can use one of three launch modes:

```text
native  -> the CLI connects directly to its supported provider endpoint
routed  -> the CLI connects to Ciel's loopback router, which adapts the wire protocol
router  -> Ciel runs only the local router for an external client
```

Dedicated adapters cover Anthropic, Ollama and Ollama Cloud, DeepSeek, OpenCode
Zen/Go, Kimi, Z.AI, Alibaba Model Studio, xAI, OpenRouter, TaBiAI, Fireworks,
NVIDIA NIM, vLLM, and LM Studio. A declarative catalog adds other
OpenAI-compatible services and private gateways. The authoritative list,
endpoint rules, and model constraints are in [Providers](docs/Providers.md).

The router binds to `127.0.0.1` by default. External access is an explicit
debugging/operations feature and requires the configured administration token.

## Workspace context and memory

Remote Instructions can download the native instruction file used by the
selected CLI. Remote Memory can independently download a manifest-defined tree
into `<workspace>/.ciel/memory` before launch.

- Every file is staged and validated before the prior tree is replaced.
- Paths are constrained to the active workspace.
- Memory pointers are projected as workspace-relative paths so projects remain portable.
- The memory index guidance stays at the end of native and routed system context.

See [Remote Memory](docs/Remote-Memory.md) and [Configuration](docs/Configuration.md).

## Events, transcripts, and usage

Ciel's router exposes separate interfaces for inbound work and outbound
observability:

- external events and Web Chat messages can wake an active routed session;
- transcript webhooks emit only new deltas and retain a retry cursor;
- routed token usage is stored per workspace without prompt or credential bodies;
- authenticated consumers can read immutable usage events, resume an SSE stream,
  or request a time-range snapshot;
- outbound usage delivery retries with stable event IDs and emits periodic audit snapshots.

Native/direct CLI traffic that bypasses the Ciel router is outside the usage
collector's visibility. See [Usage observability](docs/usage-observability.md),
[MCP and channels](docs/MCP-Channels.md), and the [CLI reference](docs/CLI-Reference.md).

## Configuration

Configuration is stored under `~/.config/ciel-runtime/` on macOS/Linux and
`%APPDATA%\ciel-runtime\` on Windows. Workspace-owned state is isolated by the
resolved launch workspace. Override the global configuration directory with
`CIEL_RUNTIME_CONFIG_DIR`.

Common commands:

```sh
ciel-runtimectl provider [NAME]
ciel-runtimectl models [PROVIDER]
ciel-runtimectl model MODEL_ID
ciel-runtimectl zai-oauth login
ciel-runtimectl status
ciel-runtimectl remote-memory
ciel-runtimectl transcript-events
ciel-runtimectl usage-events
```

Use the [CLI reference](docs/CLI-Reference.md) for every command and
[Configuration](docs/Configuration.md) for files, environment variables, and
security boundaries.

## Releases

Stable releases are published from `main`:

```sh
npm install -g @oneciel-ai/ciel-runtime@latest
```

Nightly builds are published from `nightly` for pre-release validation:

```sh
npm install -g @oneciel-ai/ciel-runtime@nightly
```

See [CHANGELOG.md](CHANGELOG.md) for grouped release notes and the complete
commit ledger included in each stable release.

## Documentation

| Topic | Guide |
|---|---|
| Install and unattended setup | [Installation](docs/Installation.md) · [Setup modes](docs/install.md) |
| Commands and settings | [CLI reference](docs/CLI-Reference.md) · [Configuration](docs/Configuration.md) |
| Providers and model routing | [Providers](docs/Providers.md) · [Router](docs/Router.md) |
| Runtime design | [Architecture](docs/Architecture.md) · [Module map](docs/Module-Map.md) |
| Memory and messaging | [Remote Memory](docs/Remote-Memory.md) · [MCP and channels](docs/MCP-Channels.md) |
| Operations | [Observability](docs/Observability.md) · [Usage observability](docs/usage-observability.md) |
| Verification | [Test suite](docs/Test-Suite.md) |

## Development

```sh
npm test
npm run lint
npm run check:docs
npm pack --dry-run
```

`npm test` compiles the Python entrypoints and runs the unit, router, channel,
and runtime test groups. CI additionally checks Python 3.10 compatibility,
documentation links and release metadata, lint, and the npm package contents.

## Security

- Keep provider and consumer API keys out of source control and shell history.
- Keep the router on loopback unless authenticated external access is required.
- Treat traces, transcripts, event payloads, and usage metadata as sensitive.
- Review Tool Guard policy before granting an agent destructive capabilities.

Report security issues privately to the maintainers; do not open a public issue
containing credentials, private prompts, or request traces.

## License

MIT. See [LICENSE](LICENSE) and [NOTICE](NOTICE).
