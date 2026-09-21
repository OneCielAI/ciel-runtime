# Muse Code runtime

Ciel Runtime can launch Meta's official Muse Code CLI while preserving Muse
Code subscription authentication:

```sh
ciel-runtime muse
```

Muse Code runs in one of two modes, chosen like Claude's and Codex's:

- **Muse Native** - `ciel-runtime muse`, or select *Muse Native* (`meta:native`)
  in the provider menu. Muse talks to Meta directly. When the workspace's `meta`
  provider has a Model API key (`ciel-runtime api-key meta`), the launcher hands
  it to Muse as `META_API_KEY`; Meta documents that an API key takes priority
  over the stored account login and bills those calls pay-as-you-go. Without a
  key, Muse keeps using its own account login. A native launch also resets the
  launcher's routerspin in Muse's settings (see below) so Muse starts from its
  factory transport again.
- **Muse Routed** - `ciel-runtime muse --ca-router`, select *Muse Routed*
  (`meta:routed`) in the provider menu, or use the launch menu's routed row.
  Muse's model traffic goes through the Ciel Router, which holds the Model API
  key and serves the `/v1/responses` route Muse speaks.

The Ciel Router remains the local control plane for Web Chat, external inputs,
remote instructions, and workspace memory in both modes.

## Routed mode (Meta Model API through the Ciel Router)

```sh
ciel-runtime muse --ca-router
```

Routed mode needs the `meta` provider selected (Muse Native or Muse Routed); a
launch with another provider stops with that instruction, because the router
sends Muse's requests to the selected provider and Muse only speaks Meta's wire.

Routed mode launches the same Muse Code CLI but points its Meta provider at the
Ciel Router instead of `https://api.meta.ai/v1`. Muse was captured sending
`POST <base>/responses` (OpenAI Responses, streaming) with
`Authorization: Bearer <META_API_KEY>` and a `GET /muse-code/models` catalog
probe, so the router receives its familiar `/v1/responses` route and relays to
the configured `meta` provider - the same path a routed Codex launch uses.

What changes:

- `--base-url {ROUTER_BASE}/v1` and an explicit `--provider meta` are passed to
  Muse; a `--provider echo` request is refused because it would bypass the
  router.
- Muse advertises the router's token; the router holds the Meta Model API key,
  so this is the pay-as-you-go Model API path, not the subscription. Configure
  the key with `ciel-runtime api-key meta`.
- The launcher pins the router base URL in Muse's settings as
  `endpoint_transport = {"base_url": "<router>/v1", "auth": "bearer"}`. Muse
  1.3.0 withholds its Meta bearer from a base URL that is "off the sanctioned
  front door" and only accepts the pin (its own message: a settings pin does
  not vouch for a `--base-url` flag, so the pin is written even though the flag
  is passed). A native launch removes the pin again; a pin the launcher did not
  write is left alone.
- The launch owns the router for the whole session, including headless
  `muse exec` runs, and records the launch mode as `muse-router`.
- Everything the router adds applies: channel delivery, Web Chat, remote
  instructions, telemetry, live LLM options and the advisor.

Platform note (Windows): Muse Code runs inside WSL while the router runs on
Windows, and WSL cannot reach the Windows loopback. A Muse launch therefore
binds this launch's router to the address the distribution sees as its host
(its default gateway, printed by `wsl -e sh -lc "ip route show default"`)
automatically; `--ca-web-address <host>` still overrides it. That address is
outside loopback, so the router needs external access (the automatic bind
enables it for the launch) and Muse receives the router's external-access
token. Without a WSL host address the launch stops with the instructions
instead of letting every model call fail.

Credentials travel into the distribution through `WSLENV`: a Windows process
variable is invisible to a `wsl -e` process unless WSLENV lists its name, so
the launcher adds `META_API_KEY`/`MODEL_API_KEY` to it and drops the
`env -u META_API_KEY -u MODEL_API_KEY` wrapper it uses for subscription
launches (that wrapper would delete the injected value again).

## Authentication and billing boundary

Meta documents that API-key authentication takes precedence over a stored
browser session and that additional API keys are billed pay-as-you-go, so the
boundary is explicit:

- The workspace's `meta` provider key (`ciel-runtime api-key meta`) is injected
  into a **native** launch; remove it (`ciel-runtime api-key meta --clear`) to
  fall back to the account login.
- Muse's own account token lives in `~/.config/muse/auth.json` (inside WSL on
  Windows). The API-key menu for the Meta provider now offers
  `Login with Muse OAuth (device code)`, a status line and a logout entry, so
  the token can be stored before the first session instead of during it. The
  login runs `muse login` through the same launcher prefix; it never sees the
  injected key.

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

## Router MCP attach (restart and channel tools)

Muse Code reads MCP servers from `mcpServers` in `~/.config/muse/settings.json`
and offers no per-launch flag for them, so a Ciel launch merges one entry into
that file:

```json
{
  "ciel-runtime-router": {
    "type": "streamable-http",
    "url": "http://<router>/ca/mcp",
    "headers": {"Authorization": "Bearer <router token>"},
    "mode": "optional"
  }
}
```

Everything else in the file (other servers, provider, model) is preserved, and
a launch without a managed router removes the entry again so Muse never shows a
dead server. With the entry in place the session can call `restart_session`
(relaunch itself with `resume`), `submit_input`, `llm_options`, `send_message`
and `telemetry_logs`.

Address rules, because Muse runs inside WSL on Windows:

- Router on loopback → the local placeholder token is attached (native Muse).
- Muse in WSL → the router must be bound to a WSL-reachable address
  (`ciel-runtime muse --ca-web-address <windows-wsl-ip>`) and the router must
  accept external clients (`router_debug_external_access`); the entry then
  carries that URL and the router's external token.
- When the entry cannot be attached (WSL plus a loopback router, or external
  access off) the launch still proceeds and the router log names the remedy:
  `muse_router_mcp_skipped reason=…`.

`muse.router_mcp=false` in the workspace config disables the attach.

## Message injection paths

Channel delivery into Muse is not one fixed mechanism. Ciel Runtime models the
options once and maps them onto the path the caller selects, and refuses with a
reason when a path cannot honour an option instead of silently degrading it.
The implementation lives in `muse_injection.py` (options, capability rules, argv
builders) and `muse_msp.py` (the MSP client).

| Path | Transport | Parameters it carries |
| --- | --- | --- |
| `msp` | `muse serve` host the runtime owns, Muse Session Protocol over stdio (newline-delimited JSON-RPC 2.0) | `ifBusy=queue\|steer\|replace`, `displayText`, `reasoningEffort`, idempotent `commandId`, `turn/steer` with `expectedTurnId`, `turn/interrupt`, `approval/decide`, `userInput/answer` |
| `console` | The interactive TUI through the terminal proxy (today's default launch) | bracketed paste, submit retries, submit confirmation, wake on delivery |
| `exec` | Headless `muse exec --prompt-file … --json` (starts a new session) | prompt file, provider/model/base-url, permission profile, workspace, JSONL events |
| `session-message` | Muse's cross-session bus (`muse session-message send --target …`) | target session name/uuid, in-reply-to token, steer/queue/notify requested in the body, 8 KiB limit |

Delivery intentions map onto the path's own vocabulary:

| Intent | MSP `ifBusy` | session-message body | console |
| --- | --- | --- | --- |
| `queue` | `queue` (wire default) | "Queue this for your next turn:" | paste when idle |
| `steer` | `steer`, or `turn/steer` with the live turn id | "Steer the active turn with this message:" | paste now |
| `replace` | `replace` | not expressible | not expressible |
| `notify` (display only) | refused — the turn API has no display-only submission | "Notify only - do not add this to your model context:" | refused |

Options can be declared per workspace under `muse.injection` and overridden per
call (`options_from_config`), so one channel message may travel as a queued
turn, a steer into the running turn, or a peer message.

### Live-verified behaviour (Muse Code 1.3.0-R3233.1, 2026-09-19)

`python scripts/probe_muse_msp_injection.py` opens a real host
(`wsl -e …/muse serve --no-session-log --disable-shell --disable-write`), starts
an `echo`-provider session, and delivers one message per intent:

```
initialize      -> muse 1.3.0, schema fingerprint sha256:ab69549a…, durability ephemeral
session/start   -> session 01a0b82c-…, provider echo
queue           -> turn/start accepted, disposition=started
steer           -> turn/start accepted, disposition=steered (same turn id)
replace         -> accepted (host answered disposition=queued: the turn had already finished)
redelivery      -> command id rotated, no command_id_conflict
notification    -> session/started
```

Protocol facts learned live, which the client encodes:

- The handshake is gated: `initialize` must be answered and the `initialized`
  notification sent before any session request, and `clientInfo.name` must match
  `^[a-z0-9_]+$`.
- Approval modes spell differently on the wire
  (`allowAll|promptUnmatched|onRequest|denyUnmatched`) than on the CLI, and a
  mode may not exceed the one the host's startup posture sealed.
- `workspaceRoot` must be an absolute path as the host sees it, which matters
  across the Windows/WSL boundary.
- An applied `commandId` may not be reused for a new command
  (`-32030 command_id_conflict`); the service rotates the id after an
  acknowledged delivery and only retries with the same id when an attempt was
  never acknowledged.
- `muse schema generate-json-schema --out DIR` exports the exact wire contract
  of the installed binary (47 methods, an error table, and a method index).

Not yet wired: the launch path still runs the TUI through the console proxy, and
`view/subscribe`/`view/page` returned `methodNotFound` to the minimal client on
this build, so live item streaming through our own client is unverified while
the official `@muse-code/sdk` facade documents it.
