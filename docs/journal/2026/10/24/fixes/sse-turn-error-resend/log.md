# Update Log

## 2026-10-24
- **Update**: Published `0.2.51-nightly.20261024-102326.a7e28f9` (gitHead verified) and installed it locally: per-file atomic replacement, identical hashes, 256 installed-path tests OK; live routers unchanged, .14 untouched. See [release](/verification.md#release).
- **Creation**: Implemented resend of SSE inputs after transient turn errors (Codex and Claude), launch-backlog expiry, stale-scope compaction diagnostics, and menu/statusline visibility. Verified with 909 isolated tests, a real codex-cli 0.158.0 app-server E2E, a real Claude Code 2.1.310 transcript E2E, and a key-driven menu E2E. Not committed or installed.
