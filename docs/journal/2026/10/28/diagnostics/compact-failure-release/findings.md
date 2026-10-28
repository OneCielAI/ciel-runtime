---
type: Diagnostic
title: Compact failure must not permanently gate external input
description: Investigate retry and explicit failure release separately from success verification.
timestamp: 2026-10-28
tags: [compact, sse, recovery]
---
# User requirement

Compact failures must retry automatically within a finite budget. If retry fails or ends with an exception, release the SSE injection gate so new external input can trigger compact again. Preserve failed status, queued messages and exact submission ownership; do not forge verified shrink.

# Initial observed evidence

Both protected local routers still run679245f (9524 PID18536, 9505 PID30608). Current admission snapshots are verified, not currently stuck. Available current-day native boundary records show successful compact boundaries. No exact current-day native compact failure was identified in the two bound transcripts. Mentions inside prompt/tool text are NOT failure evidence.

9505 logged compaction_checkpoint_pending during05:46-05:50;05:50:54 checkpoint_and_history_shrink_verified released it. Latest recorded compact11:23:57→11:29:05 also verified.9524 last recorded04:37:44→04:41:16 verified. This does not disprove reported intermittent failure on another attempt/host; exact incident correlation remains open.

# Source facts

runtime_compaction_binding.external_admission_paused explicitly forbids external SSE as a recovery probe (lines661-668). observe_failure at538-558 only handles parse_context_overflow; ordinary compact errors return without a transition. Further retry/expiry controller audit pending.

# Safety

Read-only on live routers, transcripts, queues and admission state. No forced compact, gate unlock or restart. Reproduction and behavior changes use owned isolated fixtures.

# Confirmed source gap and implementation under verification

Prior code recognizes only two exact idle-timeout strings. Other structured terminal compact errors are ignored. Retry exhaustion changes to blocked and still gates all external input; checkpoint/shrink confirmation timeout also holds indefinitely. These are source-supported behavior gaps against the updated user requirement, NOT a correlation to a captured live failure.

Changed contract: failed_open is a durable failure (never verified), retaining attempts/reason/release time. Known terminal native compact failures retry with the existing finite budget. Budget exhaustion, command submission uncertainty, retry queue exception, and confirmation lease expiry release the compact gate without automatic uncertain replay. Polling alone does not rearm failed_open; new native compact, manual command or confirmed overflow starts a fresh cycle. Old command ids cannot dispatch again; fresh checkpoint+shrink may still verify late. Unreadable admission state remains a separate hold, never assumed safe.

Focused first new-contract baseline7 tests exposed the old release/recognition gaps. Implementation initial related suite143 tests had25 failures (old assertions expecting permanent hold, plus duplicate inherited test discovery). Test contracts were revised explicitly, not deleted; new class no longer inherits unrelated test methods. Final related135 tests passed, with no errors. Initial actual CLI harness launch failed before startup because Python3.14 lacks psutil; retry uses Python3.11 with installed psutil. Actual CLI proof still running.

# Poll-loop exception defect

channel_compact_injection previously re-raised writer exceptions after recording uncertainty. channel_compact_poll caught only pre-submit file-lock contention; Windows/POSIX terminal outer loops then exited. This is source-confirmed exception propagation, not proof of the reported live incident. Added typed CompactSubmissionUncertain only after successful durable controller failure accounting; compact polling contains that type and continues external delivery. Arbitrary read/state failures still propagate or hold distinctly; non-compact session actions retain old behavior.

# Real native verification

Actual Claude Code2.1.312, owned launcher/router/free ports/fresh HOME, minimized console, egress sentinel. run-final-source scenario: first manual compact HTTP500; automatic retry after30 seconds also HTTP500; failed_open release logged at attempt2; buffered SSE delivered and model invoked compact_session; new native compact checkpoint+shrink verified. Queue message status replied. All9 explicit checks true; zero owned foreground-window samples; live9505 PID30608 and9524 PID18536 unchanged. Screenshots and event lines retained. Upstream native compaction may make multiple HTTP requests for a single /compact; command dispatch identities, not raw HTTP counts, determine duplicate submission.

Egress sentinel rejected background CONNECT attempts; no external generation was sent. No private request bodies/native transcript dumps are retained by this new harness. Initial run-source proof lacked sentinel recording (blocked proxy endpoint); final-source proof has explicit denied destinations.

Final source proof: [checks](evidence/scripts/run-final-source-130106/checks.json), [failure then SSE compact screenshot](evidence/scripts/run-final-source-130106/03-sse-retriggered-compact-success.png). New actual terminal failure was not observed in protected production transcripts, so this is controlled reproduction, not a claimed production incident diagnosis.

# Final local checks

Expanded focused compact/poll and architecture suite: see final-focused-all.txt (completed). Additional final queued-reservation exception regression and78 targeted tests passed (final-exceptions.txt). Earlier focused379 run had one old permanent-hold expectation in continuation boundary; updated to require failed_open without verified/synthetic continuation authority. Repository ruff and documentation metadata pass. Live PID18536/30608 unchanged.

# Release in progress

Exact commit fe71169c25e926cfb598676b721269d1d7586874 pushed to nightly and nightly-20261028. CI40742039176 six jobs succeeded; Publish40742039193 succeeded. Registry nightly version0.2.51-nightly.20261028-181854.fe71169, exact gitHead plus SHA1/SHA512 verified. Tarball SHA25682dfec4c0c459b0d989fc0884d030da047a182cad7c6afcd4175ffb30b418a8c.

Published515 files:505 identical to commit,8 only CRLF,2 only version stamps (package.json and runtime_constants.VERSION); no missing files. New snapshot462 exact files and global npm515 exact files. Installed140 focused tests passed, with four changed modules loading from the new snapshot (see evidence/release/installed-tests.txt). Actual installed CLI proof pending; local home switch not yet performed. Running live routers remain untouched on679245f.

# Installed verification and local switch

Installed actual CLI run-installed-132637 on fe71169 snapshot passed all8 functional checks: first compact terminal500,30s bounded retry500, failed_open release, queued SSE message delivered/replied, exactly one model compact tool call after release, new compact cycle verified, three screenshots, protected live PIDs unchanged. [Checks](evidence/scripts/run-installed-132637/checks.json), [screen](evidence/scripts/run-installed-132637/03-sse-retriggered-compact-success.png), [events](evidence/scripts/run-installed-132637/compact-events.txt).

Focus noninterference did NOT pass:22 foreground samples in one ~1.07s interval were observed despite minimized/no-activate launch. This is a harness observation, not concealed as9/9 success. Functional checks are8/8; live PID identity remained unchanged. Background CONNECT attempts to raw.githubusercontent.com/api.anthropic.com/downloads.claude.ai/api.github.com/github.com were denied by the egress sentinel.

At2026-10-28T13:28:41-05:00 HKCU CIEL_RUNTIME_HOME and generated statusline switched to verified fe71169 snapshot. Snapshot462 files/global515 files exact, installed140 focused tests passed. See [switch](evidence/release/local-switch.json) and [after](evidence/release/live-after-install.json). No install.ps1 and no live restart.

Installed version0.2.51-nightly.20261028-181854.fe71169. Current live9505 PID30608/9524 PID18536 still679245f; installation does not hot-load the running sessions. Thus production application and reported intermittent failure correlation remain unverified, distinct from verified installed controlled recovery.
