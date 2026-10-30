---
type: Release Verification
title: Compact Work arbitration nightly
description: Evidence for exact source publication, installed compact recovery and live-state preservation.
tags: [compact, work, nightly, release]
timestamp: 2026-10-29
---
# Source

Commit `037c55fc252641c41d8058db66fa055e9f43dfdf` fixes reproduced compact generation holds, echo/submission receipt arbitration, recovery-protected wake session commands and native Work Stop/compact lifecycle races. [Contract](../../fixes/compact-work-arbitration/contract.md). It does not establish the historical screenshot's exact causal origin.

# Verification boundary

Source focused152pass; native88pass/2unchangedbaselinefailures;19new lifecycle cases pass. Owned actualCLI passes failure/retry/retrigger verified withWork enabled and3screenshots. CI40984613846passed six jobs; Publish40984613839passed including published tarball verification. Installed CLI proof passed. No live or managed-host restart authorized or performed.

# Published identity and package

- `0.2.51-nightly.20261030-012435.037c55f`, exact gitHead and nightly tag verified.
- SHA256 `391b111507602bdca34f379d74f6c168c2b4c001406640094a0468b39bf58cdd`; SHA1 and SHA512 match registry.
- All516package files checked:506identical,8CRLF-only,2explicitly verified version stamps; no unexplained difference.
- Fresh snapshot463files exact; global npm local-tarball `--ignore-scripts` install516files exact. No install.ps1 executed.

# Installed regression

-150/150 focused Python tests pass, with imports pinned to the installed snapshot and sources pinned to037c55f. Source152includes2architecture checks (covered by CI); installed list deliberately excludes test-root architecture scanning.
- Native Work88pass/2unchangedbaselinefailures; all19new compact lifecycle regressions pass. Full native suite is not reported all-green. Existing failures: native plan exemption cleanup and planning denial toast count. [Results](evidence/native-installed-tests.txt).
- Protected9385PID89420/9505PID70956/9524PID18536and4unrelated file digests unchanged after installation.

# Installed actual CLI and local switch

Actual Claude Code2.1.312 on the verified snapshot passed compact terminal failure, bounded retry, queued SSE release and a new verified compact cycle with Work enabled. The queue reached generation exactly once;4compact HTTP requests observed including native follow-up. [Result](evidence/installed-cli/result.json); [before](evidence/installed-cli/01-before-compact.png), [retry](evidence/installed-cli/02-retry-running.png), [verified](evidence/installed-cli/03-sse-retriggered-compact-success.png). Owned foreground activation0/1296samples; external requests intercepted by owned egress sentinel.

HKCU CIEL_RUNTIME_HOME switched to `C:/Users/djlov/.local/share/ciel-runtime-037c55fc252641c41d8058db66fa055e9f43dfdf` at2026-10-29T20:30:24-05:00. Generated statusline exactly matches the snapshot template; previous script backed up. Protected three router PID/version/fingerprint identities and4unrelated files unchanged. No live-session/managed-host restart or queue/history mutation performed. Existing processes retain prior imported code; fixes apply on their next normal launch.

# Additional request

SSE gate slash on/off/status and visible statusline are a subsequent implementation under n607, not part of037c55f. This release does not claim those controls are shipped.
