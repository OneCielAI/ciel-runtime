# Changelog

This file records stable Ciel Runtime releases. Changes are grouped by user-visible
capability, followed by the complete commit ledger merged into each release.

## Unreleased

- Improve Alibaba Singapore Token Plan cache reuse for Codex: stateless
  full-history Responses requests now use Qwen's implicit prefix cache instead
  of forcing the response-ID session-cache header, while oversized requests
  align their summarized prefix to pair-safe 24-item checkpoints.
- Deliver Web Chat, configured external event streams/webhooks, and Ciel MCP
  `submit_input` calls to interactive Claude Code sessions through Claude's
  authenticated session socket by default. Windows named pipes and Unix
  AF_UNIX sockets are supported; explicit TTY and router overrides remain.
- Add Claude Fable 5.1 to the Anthropic catalog and model policy.
- Keep Anthropic routed sessions on standard 200K context unless the selected
  model explicitly includes `[1m]`, avoiding an unintended usage-credit beta.
- Advertise provider descriptions in the gateway model catalog for Claude Code
  2.1.257 and later model-picker discovery.
- Apply each routed provider's configured subagent model to every Claude Code
  2.1.257+ subagent with the new force-model environment switch.

## 0.2.37 — 2026-09-01

- Add the authenticated remote runtime bridge with shared OpenAI-compatible and
  Anthropic-compatible endpoints, router-host credential ownership, streaming
  conversion, and remote provider/model selection.
- Add durable OTLP log ingestion with appendable named files, cursor-based
  reads, rolling/TTL retention, and lightweight agent notifications.
- Harden external message delivery, Windows console input cleanup, Codex exit
  diagnostics, transcript memory bounds, and routed stream recovery.
- Add Alibaba Model Studio Kimi K3 metadata and correct Token Plan native
  Anthropic routing to `/apps/anthropic/v1/messages`.

- Neutralize stale Windows Console/ConPTY mouse, focus, and bracketed-paste
  modes at safe startup/cleanup boundaries without filtering F9 or normal
  bracketed-paste input. Legacy consoles receive no raw reset escape text when
  VT output activation fails, and all child/console cleanup steps remain
  best-effort after setup or runtime errors.
- Import the fresh Start Plan JWT written by the official ZCode 0.16.5 OAuth
  init/poll flow from its encrypted shared credential store. Start Plan login
  now delegates to that official flow instead of reusing a stale Desktop
  provider snapshot; the Desktop selection remains an import fallback.
- Separate Z.AI Start Plan from Coding Plan using the installed official ZCode
  Desktop 3.9.1 contract. Start Plan imports its selected Desktop JWT and sends
  Anthropic Messages to
  `https://zcode.z.ai/api/v1/zcode-plan/anthropic/v1/messages`; Coding Plan keeps
  its own CLI credential and documented `https://api.z.ai/api/anthropic` base.
- Rebuild the request-scoped Start Plan verification flow from the installed
  ZCode bundle: fetch the official client CAPTCHA configuration, use the
  official Aliyun SDK, and attach fresh verification, region, and session
  headers to each upstream attempt.

## 0.2.36 — 2026-08-24

- Align the Aliyun CAPTCHA page with the documented V3 callback contract:
  submit `CaptchaVerifyParam` only from `success`, ignore later callbacks once
  submission starts, and release the model request only after the accepted
  result socket has completed `shutdown_request`.

## 0.2.35 — 2026-08-24

- Finalize the state-bound Z.AI Start Plan CAPTCHA HTTP response before the
  one-shot local receiver releases the paused model request, preventing the
  browser from reporting `Failed to fetch` after Ciel Runtime accepted the
  verification result.

## 0.2.34 — 2026-08-24

- Match ZCode's request-time CAPTCHA interaction lifecycle: pause the active
  model request, expose the state-bound Aliyun verification URL in the attached
  Codex/Claude terminal, and continue the same request after verification.
- Keep CAPTCHA notices out of agent prompt input and channel wake submission;
  the common terminal proxy displays request, completion, failure, and timed
  reminders without synthesizing Enter.
- Persist only the short-lived interaction state in the workspace router
  instance and replace it atomically so remote/browserless hosts can surface a
  pending challenge without persisting the returned CAPTCHA header.

## 0.2.33 — 2026-08-24

- Send `User-Agent: ZCode/<configured app version>` for every Z.AI provider
  profile, not only Start Plan.
- Match ZCode's Anthropic Start Plan authentication wire contract by sending
  the same credential in both `Authorization: Bearer` and `x-api-key`.
- Allow an explicitly configured remote CAPTCHA receiver port to be reused by
  consecutive model requests after the prior receiver shuts down.

## 0.2.32 — 2026-08-24

- Keep Z.AI Start Plan wire identity aligned with the installed ZCode app:
  `User-Agent: ZCode/3.8.1` and the OAuth JWT in `Authorization: Bearer`, without
  adding `X-Api-Key`.
- Add opt-in remote CAPTCHA callback settings for browserless hosts: bind host,
  fixed/dynamic port, public HTTP(S) origin, and timeout are configurable through
  `ciel-runtimectl provider-options` or environment variables.
- Preserve loopback-only behavior by default and retain the state-bound,
  one-request CAPTCHA result receiver when remote callback access is enabled.

## 0.2.31 — 2026-08-24

- Force Z.AI Start Plan OAuth through the state-bound
  `http://localhost:9899/callback` authorization-code flow even when ZCode's
  hosted CLI init endpoint is available; Coding Plan keeps its hosted polling
  flow and existing 404 fallback.

## 0.2.30 — 2026-08-24

- Enabled Z.AI Start Plan routed clients by acquiring a fresh request-scoped
  verification value through the official Aliyun CAPTCHA browser SDK before
  every upstream attempt; verification results return only to a state-bound
  loopback receiver and are never persisted.
- Refresh Start Plan CAPTCHA headers independently for retries, serialize
  concurrent verification requests, and keep every other provider isolated
  from the Start Plan-only headers.
- Route Codex Responses through the Start Plan gateway's verified Anthropic
  Messages endpoint; the advertised OpenAI base currently exposes no standard
  Responses or Chat Completions route.
- Accept both native Win32 and ConPTY/SSH VT arrow-key sequences in Windows
  prelaunch menus, including the provider environment menu shown after OAuth.

## 0.2.29 — 2026-08-24

- Split Z.AI general API, Coding Plan, and Start Plan into independent provider
  profiles so OAuth login never overwrites the legacy/manual `zai` API key.
- Added the verified general API and Coding Plan OpenAI/Anthropic endpoints and
  plan-scoped `zai-oauth --profile coding-plan|start-plan` credential handling.
- Added official GLM-5.1, GLM-5.2, and GLM-5.3 context/output profiles.
- Registered the installed ZCode Start Plan endpoints and JWT credential shape,
  but fail launch explicitly because a live request proved that the private
  runtime's fresh Aliyun CAPTCHA header is required; Ciel does not fabricate or
  bypass it.

## 0.2.28 — 2026-08-24

- Replaced manual `zcode://` callback pasting in the Z.AI authorization-code
  fallback with a one-shot, state-bound listener at
  `http://localhost:9899/callback`.
- Validate the exact callback host, port, path, and state before token exchange;
  reject occupied ports, unrelated paths, oversized request targets, and stale
  callbacks without changing the stored credential.
- Use the same localhost redirect URI for authorization and token exchange, and
  return a secret-free browser completion page.

## 0.2.27 — 2026-08-24

- Repair Codex startup only when an invalid TOML file contains equivalent MCP
  `http_headers` in both a legacy child table and a managed inline table;
  preserve a timestamped backup before the atomic rewrite.
- Reject a pasted Z.AI authorization-page URL with explicit callback guidance
  and keep the prelaunch API-key panel alive when OAuth validation fails.

## 0.2.26 — 2026-08-24

- Added ZCode as a first-class launch client in the interactive menu,
  `ciel-runtime zcode`, `ciel-runtimectl launch-zcode`, and `--ca-runtime zcode`.
- Routes ZCode through the selected Ciel provider with a workspace-isolated
  ZCode home and custom Anthropic-provider configuration, without overwriting
  the user's normal ZCode configuration.
- Shares Z.AI OAuth Coding Plan credentials across ZCode, Claude, Codex, AGY,
  and other routed clients; ZCode TUI OAuth results are imported after exit.
- Added Z.AI OAuth login/status/logout actions to the provider API-key panel.
- Restores Windows bracketed-paste mode on runtime exit to prevent literal
  `ESC[200~` / `ESC[201~` markers from leaking into the parent prompt.

## 0.2.25 — 2026-08-23

- Added the ZCode wrapper's authorization-code contract as a cross-platform manual
  callback fallback when the ZCode CLI init endpoint returns HTTP 404.
- Validates the callback target and OAuth state before token exchange, retains
  transient tokens in memory, and leaves configuration unchanged on cancellation.

## 0.2.24 — 2026-08-23

- Added cross-platform Z.AI OAuth login through ZCode's CLI init/poll contract and
  Coding Plan API-key resolution. Transient OAuth tokens remain in memory; Ciel
  persists only the resolved API key through its existing credential store.
- Added `ciel-runtimectl zai-oauth login|status|logout` with `--no-browser` support,
  secret-free errors, and transaction-safe failure behavior.
- Added official `glm-5.3` model metadata: 1M context, 128K maximum output, mandatory
  thinking, and `low`/`high`/`max` reasoning-effort normalization.

## 0.2.23 — 2026-08-23

This release promotes 41 commits developed and validated after `0.2.22`. The
range changes 110 files with 9,990 insertions and 256 deletions.

### Workspace memory and remote context

- Isolated downloaded memory by launch workspace instead of sharing one global tree.
- Synchronized manifest-defined memory into the actual runtime working directory.
- Added atomic download/overwrite support for OKF, Markdown, JSON, YAML, TOML, and text files.
- Appended verified memory-root/index guidance to native instructions and routed system context.
- Kept the memory pointer last through instruction refresh and wire-level prompt truncation.
- Projected memory paths relative to the workspace so replicated projects remain portable.

### External messages and terminal delivery

- Restored complete routed SSE message bodies in Codex wake prompts.
- Added prompt-render detection and bounded retry for cold-start and deferred Windows ConPTY submissions.
- Preserved Unicode, whitespace, prompt offsets, and submitted-turn evidence across retries.
- Isolated Windows installer state and repaired stale temporary runtime pins.
- Preserved parallel tool results and rejected invalid replayed tool names before upstream submission.
- Recovered turns stopped by the repeated-tool guard without repeating the completed side effect.
- Restored Claude's stateless internal Ciel MCP launch configuration and `/ca/mcp` POST tool endpoint.

### Transcripts and compaction

- Added incremental transcript-delta webhooks with CloudEvents, durable cursors, retry, and compaction-boundary events.
- Sanitized legacy Anthropic history containing empty `tool_use.name` values so affected sessions can compact and continue.

### Providers and models

- Aligned Alibaba Model Studio Singapore `qwen3.8-max` context limits and workspace endpoint migration.
- Kept Alibaba OpenAI-compatible and Anthropic-compatible request wires distinct.
- Added OpenRouter and OpenCode Zen/Go routes for Ox Alpha/free catalog aliases.
- Forwarded supported OpenRouter reasoning-effort values instead of dropping them.
- Added the TaBiAI/Tabitoken provider with separate Anthropic Messages and OpenAI Chat endpoints and its verified model catalog.

### Usage observability

- Added a workspace-scoped SQLite usage ledger for routed requests.
- Added authenticated event, resumable SSE stream, and time-range snapshot endpoints.
- Added per-consumer API keys with scopes, expiry, digest-only storage, and revocation.
- Added outbound CloudEvent delivery with per-endpoint durable cursors, stable idempotency keys, and retry after failed delivery.
- Added interval audit snapshots, including a daily default, grouped by provider, model, and runtime.
- Added rotation-safe backfill from earlier workspace JSONL usage logs and explicit legacy paths.
- Added equivalent startup environment variables and persistent `ciel-runtimectl` configuration commands.

### Documentation and verification

- Added evidence journals for WING, delluhiold, Hyundai, Alibaba, OpenRouter,
  TaBiAI, remote memory, transcript delivery, and usage observability incidents.
- Made usage backfill tests platform-native so the same contract passes on Windows and Linux CI.

### Complete commit ledger

Every audited pre-release commit between `0.2.22` (`b7ea382`) and the release
preparation commit is listed below in chronological order.

- `60866dc` docs: trace external SSE wake-only delivery
- `13f5d91` fix: isolate remote memory to project workspaces
- `5e5bdec` fix: load remote memory from workspace state
- `6648365` fix: sync remote memory into launch workspace
- `21872ed` fix: keep memory pointer last in system prompt
- `c4071ef` fix: restore memory pointer after wire truncation
- `72a2474` fix: show routed SSE bodies in Codex wakes
- `98665f2` docs: record WING SSE wake verification
- `fc290a0` fix: retry Codex wake submission on ConPTY
- `b410b4c` fix: wait for Codex prompt render before submit
- `e4b86e6` docs: record Codex wake submit verification
- `fe7c4b0` feat: align Alibaba Singapore Qwen3.8
- `c8d920e` docs: record Qwen3.8 nightly verification
- `4eab5fb` docs: record runtime deployment diagnostics
- `5d24d79` fix: preserve parallel tool turns in routed sessions
- `5a43567` fix: repair stale Windows runtime pins
- `f6f7a27` fix: isolate Windows installer state
- `511148d` fix: drop invalid replayed tool names
- `87525c6` fix: wait for cold-start wake render
- `fad60f5` fix: track ConPTY prompt render offsets
- `f357b91` fix: retry deferred wake submissions
- `39f1119` fix: preserve wake render whitespace
- `46d0e4e` docs: record WING wake submission verification
- `bbf2181` fix: recover repeated tool guard turns
- `b70a228` feat: stream transcript deltas to webhooks
- `d7d9369` fix: sanitize invalid Anthropic tool history
- `5223153` docs: record delluhiold compaction verification
- `f62a72c` docs: record live compaction recovery
- `ce72ccc` fix: align Alibaba dual endpoint request wires
- `28ef138` feat: append remote memory guidance to system context
- `22a3006` fix: project remote memory paths relative to workspace
- `849814b` feat: add Ox Alpha provider routes
- `70c5d32` fix: sync OpenCode model endpoint catalog
- `65e69f1` docs: record Ox Alpha deployment evidence
- `c243e75` fix: forward OpenRouter reasoning effort
- `c37d0e4` docs: record OpenRouter effort deployment
- `d81eb00` feat: add Tabitoken provider
- `f98310b` docs: record Tabitoken deployment
- `4d1b68d` feat: add durable usage observability
- `45a9456` test: use platform-native backfill paths
- `549e6a6` docs: record usage observability deployment

## 0.2.22 — 2026-08-20

- Published the previous stable runtime baseline. See commit `b7ea382`.
