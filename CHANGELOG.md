# Changelog

This file records stable Ciel Runtime releases. Changes are grouped by user-visible
capability, followed by the complete commit ledger merged into each release.

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
