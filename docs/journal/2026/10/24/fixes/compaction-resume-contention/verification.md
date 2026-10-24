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

- [Focused tests](evidence/focused-tests.txt): 636 tests, OK, 43 environment-specific skips. Exact-file isolated runner, temporary homes, stripped inherited Runtime/Claude/Anthropic/Codex environment. No broad discovery on the live machine.
- Real loopback disconnect/resume: first attempt completed three summaries before cancellation; retry reused three and generated only three more; final upstream opened once. This is synthetic transport evidence, not real-model/fleet recovery.
- Review regression: one-shot overflow/shrink observations survive repeated state conflicts without promoting stale checkpoint proof; disabling safeguard cancels its queued reservation.
- Barrier-controlled full-binding slow scan: seed/scope/admission locks available to a concurrent thread; stale revision and epoch/session changes reject commits; two concurrent dispatchers produce exactly one claim.
- Shrink deadline remains gated, with no queued replay, and accepts a later real small request. Menu output contains phase/reason/checkpoint-observed/failure time.
- [Ruff](evidence/ruff.txt), [metadata](evidence/metadata.txt), [npm pack dry run](evidence/pack-dry-run.json). Compile passed. Existing facade/port architecture tests remain enforced.

# Limits

No live session restarted; no remote machine changed. A crash between upstream completion and durable save can repeat one summary call: this is not exactly-once execution. The cache stores model summary text; it is private local state, not a guarantee that a model never repeats sensitive source text. Raw source prompts/base64 are not explicitly persisted.

# Release

Nightly push, registry gitHead verification and local installed-path verification pending. Changes apply only to new launches unless a session is explicitly restarted separately.
