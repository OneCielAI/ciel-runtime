---
type: Verification
title: Compaction resume and contention improvements
description: Evidence for resumable semantic maps, native image forwarding and short admission mutations.
tags: [compaction, recovery, verification]
timestamp: 2026-10-24T00:00:00Z
---
# Changes

- Exact-scope map progress: prompt digests, ordered split decisions and validated summaries persisted before honoring cancellation. New scope/history/model/endpoint/budget identities never reuse earlier results. 1-hour TTL; four runs; 8 MiB journal; 1024 steps; 256 KiB per summary. Persistence failure disables reuse, not compaction.
- Recognized image bytes replaced by explicit unavailable-image markers only in the textual map projection. Original native compact request is unchanged when its text-only estimate fits. This does not estimate visual-token cost; the global estimator remains unchanged.
- Checkpoint scanning outside seed/admission mutations. Short snapshot/read-compare-commit; changed scope/state/path/file signature rejects stale observations; no sample/no reservation avoids a full scan. Native checkpoint and shrink proof still own admission release.
- `awaiting_shrink` deadline records `shrink_unconfirmed` without opening admission or replaying commands. Later valid proof still verifies. Existing SSE recovery status/menu exposes phase, reason and evidence times; statusline shows reused map count.
- Lock-busy exception includes its lock path. Watchdog env/behaviour changes are deliberately excluded: no claim that the Claude apps gateway timing explains this custom endpoint.

# Verification

- [Focused tests](evidence/focused-tests.txt): 672 tests, OK, 43 environment-specific skips. Exact-file isolated runner, temporary homes, stripped inherited Runtime/Claude/Anthropic/Codex environment. No broad discovery on the live machine.
- Real loopback disconnect/resume: first attempt completed three summaries before cancellation; retry reused three and generated only three more; final upstream opened once. This is synthetic transport evidence, not real-model/fleet recovery.
- Review regression: one-shot overflow/shrink observations survive repeated state conflicts without promoting stale checkpoint proof; disabling safeguard cancels its queued reservation.
- Barrier-controlled full-binding slow scan: seed/scope/admission locks available to a concurrent thread; stale revision and epoch/session changes reject commits; two concurrent dispatchers produce exactly one claim.
- Shrink deadline remains gated, with no queued replay, and accepts a later real small request. Menu output contains phase/reason/checkpoint-observed/failure time.
- [Ruff](evidence/ruff.txt), [metadata](evidence/metadata.txt), [npm pack dry run](evidence/pack-dry-run.json). Compile passed. Existing facade/port architecture tests remain enforced.

# Follow-up review defects

2e55ca4 was published as nightly `0.2.51-nightly.20261024-061214.2e55ca4` before an independent review finished. It was not installed locally. Three defects were confirmed and fixed:

- Unlocked state reads. Measured on this Windows host with a 3 s concurrent writer/reader loop: unlocked reads caused 2194 failed atomic saves, 253 failed reads and 1978 leftover `.tmp` files; locked reads caused 0/0/0. Through the binding itself the old code produced `scope_save PermissionError`. Scope and admission reads now take their short file locks; identity, configuration and transcript checks run before the admission lock is retaken.
- Progress identity omitted provider request options. On 2e55ca4 a changed Ollama `temperature` reused summaries (3 calls instead of 6). The identity now includes a digest of the projected summary request.
- The size bound measured compact JSON while the repository writes indented JSON in text mode (CRLF on Windows). On 2e55ca4 the journal reached 1202 bytes with a 1200-byte bound, which the next reader quarantines. The bound now measures the bytes written.

Each new regression test fails on 2e55ca4 and passes on the fix.

# Limits

No live session restarted; no remote machine changed. A crash between upstream completion and durable save can repeat one summary call: this is not exactly-once execution. The cache stores model summary text; it is private local state, not a guarantee that a model never repeats sensitive source text. Raw source prompts/base64 are not explicitly persisted.

# Release

- Nightly: `f276c63` pushed fast-forward; Publish to npm and CI succeeded. Registry `nightly` = `0.2.51-nightly.20261024-063453.f276c63`, gitHead `f276c63e5f85258def26ed6949d636cdd626f2d4`.
- Local install ([deployment](evidence/deployment.json)): exact version packed and extracted with member checks, `npm install -g`, then per-file atomic replacement of 453 files into `~/.local/share/ciel-runtime` and `~/.local/bin`. The 13 changed modules hash identically across git, tarball, snapshot and global npm. `install.ps1` was not used.
- [Installed-path tests](evidence/installed-tests.txt): 318 tests OK, importing `~/.local/share/ciel-runtime/ciel_runtime_support/context_compaction_progress.py`. `ciel-runtime --version` reports the new version.
- Live routers 9489/9505/9524/9385 kept PIDs 77744/33444/26568/69748 and their old versions. No session restarted; no remote host touched. The fixes apply to new launches only.
- Observed once on the first launch after replacement: a Python 3.14 `SyntaxWarning` for `return` in a `finally` block at `codex_app_server_session.py:417`. That file is unchanged by these commits; not addressed here. Changes apply only to new launches unless a session is explicitly restarted separately.
