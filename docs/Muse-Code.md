# Muse Code runtime

Ciel Runtime can launch Meta's official Muse Code CLI while preserving Muse
Code subscription authentication:

```sh
ciel-runtime muse
```

This is a native runtime integration. Muse Code sends its model traffic directly
with the browser-authenticated credential created by its own onboarding. The Ciel
Router remains the local control plane for Web Chat, external inputs, remote
instructions, and workspace memory; it does not proxy the subscription model
request through the pay-as-you-go Model API.

## Authentication and billing boundary

Native Muse launches remove `META_API_KEY` and `MODEL_API_KEY` from the child
environment. Meta documents that API-key authentication takes precedence over a
stored browser session and that additional API keys are billed pay-as-you-go.
Run `ciel-runtime muse`, then use Muse's `/login` command when sign-in is needed.

Use the existing Ciel `meta` provider with Claude or Codex when direct Model API
pay-as-you-go routing is desired. That is a separate path from Muse Code
subscription usage.

## Platforms and installation

On macOS and Linux, Ciel discovers `muse` on `PATH`; when it is absent, Ciel runs
Meta's official installer from `https://dev.meta.ai/install.sh` with Bash.

Meta currently documents native Muse Code installation for macOS and Linux. On
Windows, Ciel discovers or installs Muse Code inside the default WSL2
distribution and launches it through `wsl.exe`. The current Windows directory is
preserved by WSL path translation.

## Model, effort, and passthrough

The native runtime defaults to `muse-spark-1.3`. When the selected Ciel provider
is `meta`, its configured Muse model and supported reasoning effort are forwarded.
Ciel `max` maps to Muse's documented `ultra` tier. Explicit Muse flags win:

```sh
ciel-runtime muse --model muse-spark-1.3 --reasoning-effort high
ciel-runtime muse exec --json "Inspect this repository"
ciel-runtime --ca-runtime muse -- --version
```

Utility subcommands such as `login`, `auth`, `config`, `schema`, `serve`, and
`session-message` are passed through without injecting model flags.

Interactive sessions, `exec`, and `resume` include Muse's `--yolo` option by
default, matching Ciel's Codex launch policy. Muse defines this option as
disabling approval and sandboxing and trusting the workspace for that run.
An explicitly supplied `--yolo` is retained once rather than duplicated.
Utility commands do not receive it.

## Router input delivery

Interactive Muse sessions run through Ciel's channel-aware terminal proxy when
available. Web Chat and external inputs therefore use the standard
session-socket-first policy and safely fall back to terminal delivery because
Muse Code does not expose Claude's session socket. Because Muse does not publish
Claude/Codex-compatible transcript confirmation events, its terminal fallback
uses one write and commits the durable channel cursor immediately; it does not
replay the same accepted message while waiting for an unavailable confirmation.
Headless `muse exec` and
utility commands run directly and do not start an interactive input proxy.

Muse Code 1.0.2 also exposes `muse serve` for the Muse Session Protocol (MSP) over
stdio and `muse session-message` for peer sessions. Ciel preserves these commands
as native passthrough surfaces; it does not claim MSP lifecycle ownership for an
ordinary interactive TUI launch.
