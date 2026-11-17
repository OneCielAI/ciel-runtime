---
type: diagnosis
title: Celly side 400 "API key is not scoped to a workspace" — the gateway's official-anthropic path sends no anthropic-workspace-id
description: Anthropic requires anthropic-workspace-id for an API key that is not scoped to a workspace. The CielAiRouter gateway's anthropic provider case builds only the auth headers and has no code that adds that header; ciel-runtime's own anthropic path forwards it.
timestamp: 2026-11-17T04:30:00Z
tags: [cielairouter, anthropic, workspace, headers, celly, side-intake]
---

# Question (user, 2026-11-17)

> 앤트로픽 api를 사용할때 같이 보내야하는 헤더를 ciel ai router에서 안보내서 생기는거아냐?

# Anthropic's contract (official docs)

Source: <https://docs.claude.com/en/api/overview>, the Authentication table, fetched 2026-11-17.

> `anthropic-workspace-id` — ID of the workspace the request runs in … Required with an API key that isn't scoped to a workspace. Optional for other API keys. A key created for a single workspace runs in that workspace when you omit the header.

> If you are using the Client SDKs … you pass anthropic-workspace-id yourself when your key needs it.

The error Celly's side forks received (30 of 31 forks, 01:01–03:44Z; [record.md](record.md)) is that condition, word for word: "This API key is not scoped to a workspace, so this request must include the anthropic-workspace-id header".

# Gateway code (read-only; F:\omini-router\ciel-ai-router)

The checkout is on branch `fix/gateway-usage-provider-mismatch` at `d38b2be3a8`; `origin/main` is `48de6ac7f5`. The same lines are in both.

- **The `anthropic` provider case.** `open-sse/executors/default.ts` `case "claude": case "anthropic":` sets only:
  - `x-api-key`, plus `Authorization: Bearer` for non-official base URLs, when the connection has an API key;
  - `Authorization: Bearer <accessToken>` otherwise.
- **No workspace header anywhere in the gateway.** `git grep -i "anthropic-workspace|workspace-id|workspace_id|workspaceId"` finds no code that adds `anthropic-workspace-id` to an upstream request. The hits are Codex `workspaceId` → `chatgpt-account-id`, and `responseHeaders.ts`, which only ranks `*-workspace-id` response headers.
- **Custom headers do not reach the official case.** `applyCustomHeaders(…, providerSpecificData.customHeaders)` is called only for `openai-compatible-*` / `anthropic-compatible-*` connections (default.ts:650, :710). A custom header on an `anthropic` connection is therefore not sent.

# ciel-runtime (for contrast)

- **Pass-through.** ciel-runtime's own routed `anthropic` provider forwards an inbound `anthropic-workspace-id` unchanged (`tests/test_claude_native_provider.py::test_routed_anthropic_provider_headers_prefer_configured_api_key`).
- **No header is sent here.** Celly's side requests go to the gateway (`cielairouter`, model `_API_KEYS-SSE-1`). Claude Code does not send that header, so the gateway receives none from the client.

# Conclusion

**Confirmed.**
- The gateway's `anthropic` executor has no code that sends `anthropic-workspace-id`.
- Anthropic requires that header for a key that is not workspace-scoped.
- The 400 text names exactly that requirement.

**Not verified.**
- Which connection the `_API_KEYS-SSE-1` combo used for `anthropic/claude-sonnet-5-5-high`, and whether that connection's stored key is the unscoped kind. Gateway connection records are on the central gateway and were not read.
- Whether the same key succeeds with the header. That needs a request against Anthropic with that key, which was not made.

**What resolves it (not applied).** Either of the following:
- The gateway sends `anthropic-workspace-id` for an `anthropic` connection that stores a workspace id. That is a gateway change, outside this repository.
- That connection's key is replaced by a key created for a single workspace, which runs in that workspace without the header (per the docs above).
