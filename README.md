# Ciel Runtime

Ciel Runtime is a cross-platform launcher and local model-routing layer for AI coding agents. It can launch Claude Code, Codex, Codex App Server, or AGY and connect them to native or OpenAI-compatible model providers through one configuration.

## Features

- Launch Claude Code, Codex, Codex App Server, and AGY from one CLI.
- Route Anthropic Messages, OpenAI Chat/Responses, and Ollama-compatible traffic.
- Select models and providers without rewriting each agent's configuration.
- Rotate API keys and observe provider rate limits.
- Normalize tool calls, thinking blocks, streaming events, and context limits.
- Receive external MCP channel messages and inject them into active agent sessions.
- Run on Windows, macOS, and Linux.

## Requirements

- Node.js 18 or newer when installed from npm.
- Python 3.10 or newer.
- At least one supported coding-agent CLI or the credentials for the provider you intend to use.

The npm launcher searches for `py -3`, `python`, or `python3`. Set `CIEL_RUNTIME_PYTHON` to use a specific Python executable.

## Install

```sh
npm install -g @oneciel-ai/ciel-runtime
```

The package installs these commands:

- `ciel-runtime` and `cielrt`: configure and launch an agent.
- `ciel-runtimectl`: inspect or change runtime configuration.
- `ciel-runtime-stop`: stop the managed local router.

Manual installation instructions are available in [docs/Installation.md](docs/Installation.md).

## Quick start

Select a provider and model interactively:

```sh
ciel-runtime
```

Or configure them explicitly:

```sh
ciel-runtimectl provider openrouter
ciel-runtimectl set-api-key openrouter YOUR_API_KEY
ciel-runtimectl model MODEL_ID
```

Launch a specific runtime:

```sh
ciel-runtime                 # Claude Code by default
ciel-runtime codex
ciel-runtime agy
ciel-runtime codex-app-server
```

Inspect the active configuration and router status:

```sh
ciel-runtimectl status
ciel-runtimectl models
ciel-runtimectl test
```

See [docs/CLI-Reference.md](docs/CLI-Reference.md) for the complete command reference and [docs/Providers.md](docs/Providers.md) for provider-specific options.

## Native and routed modes

Ciel Runtime uses a provider's native endpoint when the selected agent and provider support a direct connection. Other combinations use a local HTTP router that translates request, response, streaming, thinking, and tool-call formats.

The router binds to `127.0.0.1` by default. External router access is a debugging feature and should only be enabled on a trusted network with the external access token configured. Never expose an unauthenticated development router to the public internet.

Configuration is stored under `~/.config/ciel-runtime/` on macOS and Linux or `%APPDATA%\ciel-runtime\` on Windows. Override the location with `CIEL_RUNTIME_CONFIG_DIR`. Files containing credentials are written with restricted permissions where the platform supports them.

## Channels and MCP

Ciel Runtime can discover channel-capable MCP servers and deliver channel messages to active Claude, Codex, or AGY sessions. Channel collection, delivery acknowledgement, and terminal injection are separate layers so a failed terminal submission does not incorrectly acknowledge a message.

See [docs/MCP-Channels.md](docs/MCP-Channels.md) for configuration and transport details.

## Stable and nightly releases

Install the stable npm release:

```sh
npm install -g @oneciel-ai/ciel-runtime@latest
```

Install the latest nightly build:

```sh
npm install -g @oneciel-ai/ciel-runtime@nightly
```

Nightly builds are intended for early validation and may change behavior before the next stable release.

## Development

```sh
npm test
npm run lint
npm pack --dry-run
```

`npm test` compiles the Python entry points and runs the complete unittest suite. The full suite includes subprocess and channel integration tests and can take several minutes.

Architecture and maintenance references:

- [Architecture](docs/Architecture.md)
- [Module map](docs/Module-Map.md)
- [Router](docs/Router.md)
- [Configuration](docs/Configuration.md)
- [Observability](docs/Observability.md)
- [Test suite](docs/Test-Suite.md)

## Security

- Keep provider API keys out of source control and shell history.
- Keep the router bound to loopback unless remote debugging is explicitly required.
- Treat request traces, response traces, transcripts, and event logs as potentially sensitive.
- Review Tool Guard configuration before granting agents access to destructive tools.

Report security issues privately to the project maintainers rather than opening a public issue with credentials or request traces.

## License

MIT. See [LICENSE](LICENSE) and [NOTICE](NOTICE).
