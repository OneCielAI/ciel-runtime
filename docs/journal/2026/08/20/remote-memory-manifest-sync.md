# Remote memory manifest synchronization

## Requested behavior

- Use a memory endpoint separate from Remote Instructions.
- Let the endpoint describe a folder tree and per-file download addresses.
- Replace the local memory folder from that manifest whenever a runtime starts.
- Support common text memory formats including OKF and Markdown.
- Append only the local memory-index address to the bottom of the system
  instruction file.

## Implemented contract

- `ciel-runtimectl remote-memory` stores and reports the separate manifest
  endpoint, authorization template, target directory, timeout, and size limits.
- Manifest schema version 1 declares `index` and a `files` array. Every file has
  a portable relative `path`, HTTP(S) `url` or `download_url`, optional
  `format`, and optional SHA-256.
- Supported formats are OKF, Markdown, JSON, YAML, TOML, and plain text.
- Every runtime launch downloads a fresh manifest and fresh file bodies into a
  sibling staging directory. The previous tree is replaced only after all
  validation succeeds.
- A managed block at the bottom of `CLAUDE.md`, `AGENTS.md`, or `GEMINI.md`
  contains only `Memory index: <workspace-relative path>`. Existing user text
  is preserved and the block is replaced rather than duplicated.
- Remote Instruction refreshes re-project the current memory pointer so a
  later instruction download cannot remove it.

## Safety evidence

- Reject absolute, traversal, backslash, drive-qualified, duplicate, and
  unsupported-format paths.
- Require the declared index to be one of the downloaded files.
- Require valid UTF-8 and enforce manifest, per-file, total-byte, and file-count
  limits.
- Verify optional SHA-256 before replacing the prior tree.
- Attach the configured Authorization value only to URLs on the manifest
  origin. Cross-origin public file URLs do not receive it.
- Keep the prior memory tree unchanged after a download or verification
  failure.

## Verification

- The service test performed a real loopback HTTP manifest request, downloaded
  a nested OKF/Markdown tree, propagated same-origin authorization, and verified
  the resulting file content: 7 service tests passed.
- Launch/instruction integration: 3 tests passed. Configuration CLI: 25 tests
  passed. Existing Remote Instruction/compaction compatibility: 12 tests
  passed.
- The repository's complete pre-release `npm test` passed: unit 1,105, router
  902, channel 372, and runtime 247 tests; 136 tests were skipped by their
  declared environment conditions.
- Full Ruff, architecture budgets (`ciel_runtime.py` 4,977 lines against a
  4,980-line ratchet), and `git diff --check` passed.
- Documentation metadata checks passed for 22 Markdown files.
- `npm pack --dry-run --json` succeeded with 380 entries and includes
  `ciel_runtime_support/remote_memory.py` and `docs/Remote-Memory.md`.
- The executable CLI boundary `python ciel_runtime.py remote-memory` exited 0
  and reported the stored enabled state, endpoint, masked authorization state,
  directory, timeout, and every configured download limit.
