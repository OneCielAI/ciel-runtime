# Alibaba Singapore Qwen3.8 official alignment

## Scope

- Align Ciel Runtime's Alibaba Model Studio Singapore provider data with the
  official `qwen3.8-max` model page supplied by the user.
- Preserve user-selected custom models and avoid inventing a Workspace ID.

## Confirmed evidence

- Alibaba's official model page, updated 2026-08-03, lists `qwen3.8-max` for
  Singapore International scope.
- Official limits are a 1,000,000-token context window, 991,808-token normal
  input, 983,616-token thinking input, and 131,072-token output.
- Singapore supports text, image, and video input; function calling; structured
  outputs; web search; partial mode; and context caching.
- The official Singapore endpoints contain an account-specific Workspace ID:
  `https://{WorkspaceId}.ap-southeast-1.maas.aliyuncs.com/compatible-mode/v1`
  and the corresponding `/apps/anthropic` route.
- A live authenticated Token Plan `/models` request returned HTTP success and
  exactly one `qwen3.8-max` entry. The response did not expose context metadata.

## Changes

- `alims-intl` now defaults to `qwen3.8-max`, includes it in the fallback
  catalog, records `ap-southeast-1`, and uses the official context/output
  limits.
- `alitoken` and `alitoken-individual` use the same confirmed 1,000,000-token
  context value when `qwen3.8-max` is selected.
- A dated migration adds Qwen3.8 to existing Model Studio catalogs. It upgrades
  only the exact former Qwen3.7 default profile and preserves non-default model
  selections and custom catalog entries.
- The legacy default endpoint remains available because the official sources
  checked here do not prove that it has been retired. The provider accepts the
  account-specific official workspace URL through `base_url` and derives its
  Anthropic route from it.

## Verification

- Focused Ruff checks passed.
- Alibaba provider tests passed: 31.
- Codex runtime tests passed: 69, with 12 environment-dependent skips.
- Documentation metadata and `git diff --check` passed.
- An in-memory migration of the current legacy configuration produced
  `qwen3.8-max`, region `ap-southeast-1`, context/max model length `1000000`,
  max output `131072`, and effort `xhigh` for the applicable Alibaba providers.
- Full repository verification passed: unit 1,113, router 905, channel 379,
  runtime 247; 2,644 total tests with 136 environment-dependent skips.
- Full Ruff, documentation metadata, `git diff --check`, compilation, and npm
  package dry-run checks passed. The package contains 387 files, including the
  changed provider, migration, documentation, and journal files.
- Nightly publication is pending.
