# AGY CLI Support Research

Date: 2026-06-28

## Official Sources Checked

- Google Antigravity CLI repo: https://github.com/google-antigravity/antigravity-cli
- Google Antigravity SDK Python repo: https://github.com/google-antigravity/antigravity-sdk-python
- AGY install scripts: https://antigravity.google/cli/install.sh and https://antigravity.google/cli/install.ps1
- AGY updater manifests: `https://antigravity-cli-auto-updater-974169037036.us-central1.run.app/manifests/<platform>_<arch>.json`
- Google Agent2Agent/A2A protocol: https://github.com/a2aproject/A2A

## CLI Surface

The public AGY CLI command is `agy`. The inspected `agy --help` surface includes:

- `--continue`
- `--conversation`
- `--dangerously-skip-permissions`
- `--model`
- `--print`
- `--project`
- `--new-project`
- `--sandbox`
- subcommands including `install`, `models`, `plugin`, and `update`

AGY uses native Google Antigravity sign-in and local settings. The official docs and CLI help did not expose a supported model upstream base URL override equivalent to Codex `model_providers` or Claude `ANTHROPIC_BASE_URL`.

## Install And Update

AGY is not an npm package. The official installer downloads a platform manifest, verifies SHA512, installs the native binary, then runs `agy install`.

Ciel Runtime follows that model:

- Windows: `%LOCALAPPDATA%\agy\bin\agy.exe`
- POSIX: `~/.local/bin/agy`
- Missing binary: download official manifest, verify SHA512, install, run `agy install`
- Existing binary: run `agy update` with forced `y\n`/CI yes environment; if native update fails and a newer manifest exists, reinstall from the manifest

## Routed Mode Scope

`AGY` means native Antigravity CLI launch with Ciel Runtime disabled except for prelaunch/update behavior.

`AGY Routed` currently means native Antigravity CLI launch plus Ciel Runtime channel/PTY wake support. It does not claim to proxy AGY model upstream traffic, because no official CLI/API source showed a supported AGY model endpoint override.

## Message Injection Options

Short term:

- Use the existing Ciel Runtime PTY wake proxy.
- Submit external channel messages with bracketed paste and submit confirmation, matching the safer Codex path.

Medium term:

- Use AGY's MCP support where a channel backend can be exposed as MCP.
- Use AGY hooks/statusline only for observation, not for instructing the model.

Long term:

- Evaluate the Antigravity SDK `Conversation.send()` and trigger APIs for a direct bridge.
- A2A is a candidate for agent-to-agent bridge work, but it is not a drop-in TUI input injection mechanism.

