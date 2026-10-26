---
type: Diagnostic
title: Celly SSE inputs held behind an unconfirmable compaction
description: After the 05:43 relaunch on a7e28f9, celly's external inputs stay queued because the native checkpoint scan never succeeds on Python 3.12+ for Windows, so compaction admission never reopens.
tags: [celly, delivery, sse, compaction, windows, python]
timestamp: 2026-10-24T11:30:00Z
---
# Scope

- **Session.** Local sandbox celly: Windows account `aapw-a15u17`, home `G:\ciel-bridge-service`.
  - Router 9385, pid 52680, launcher pid 31868, started 05:43 after the 05:37 reboot.
  - Claude runtime, provider cielairouter.
- **Version.** `0.2.51-nightly.20261024-102326.a7e28f9`. The package hash of `runtime_compaction_control.py` equals the local install.
- **Python.** The router runs Python 3.13. Its own tracebacks in `router.log.1` name `C:\Program Files\Python313\Lib\socketserver.py`.
- **Access.** Read-only throughout. Nothing in celly's session, status store, cursor or state files was written, and nothing was restarted.

# Observed (✅)

**Delivery** ([status](evidence/request-status-all.jsonl), [delivery health](evidence/delivery-health.json), [router log](evidence/celly-router-9385.log)):
- 6725 and 6726 were injected over the session socket at 05:47:11 and 05:47:14.
  - Both reached the transcript ([transcript](evidence/transcript-6725-6735.txt)): 6725 as a user record, 6726 as a `queued_command` attachment.
  - Both are still `submitted`.
- 6727–6735 have been `queued` since 05:51–06:10. None was ever attempted.
- From 05:49:23 every delivery pass logs `channel_llm_inject_deferred reason=context_recovery`, with 80+ lines up to 06:16.
- At launch, 251 stranded old rows (2198–6362) were marked `skipped / launch_replay_window_expired`. This is the a7e28f9 launch expiry; it does not involve 6725+.

**Compaction admission** (`compaction-admission.json`):
- 05:47:18: Claude Code's native compaction request was observed (`admission_paused phase=compacting`).
- 05:49:15: Claude wrote compact boundary `af792554` (auto, preTokens 697279, postTokens 23071) into the session transcript. That is after `requested_at`.
- The router logged `smaller_request_before_checkpoint` for each following request, but never `native_checkpoint_observed`.
- 06:07:24: `confirmation_timeout`. The state is now `phase=blocked, failure=checkpoint_unconfirmed`, with `shrink_observed` 223680 tokens.
- The router rescans the 427 MB transcript about every 2.5 s, 2–3.7 s per scan (921 scans by 06:17). This is consistent with a scan result that is never cached.

**Why the scan never yields the checkpoint:**
- `native_checkpoint_from_transcript` (`runtime_compaction_control.py:246-251`) compares the `os.fstat` signature with `Path.stat()`, including `st_ctime_ns`. The `path.stat()` comparison was added in `2e55ca4` (2026-10-24 01:04, "resume mapped work and shorten admission locks"). Before that it compared only fstat to fstat.
- On Windows, `os.fstat().st_ctime_ns` is the change time and `os.stat().st_ctime_ns` is the creation time, on Python 3.12, 3.13 and 3.14. 3.10 and 3.11 agree ([probe](evidence/ctime-probe-by-python.txt), on C: and G:). CPython tracks this as [python/cpython#157671](https://github.com/python/cpython/issues/157671), an open issue.
- Installed code on celly's transcript:
  - Python 3.13: None in 6/6 scans, file unchanged during each ([probe](evidence/scan-probe-py313.json)).
  - Python 3.11: the `af792554` checkpoint in 5 of 6 scans. The one None scan coincided with an append ([probe](evidence/scan-probe-py311.json)).
- Replay on a private copy of celly's state, with the live transcript read only ([script](evidence/admission_replica.py), [result](evidence/admission-replica-py313.txt)):
  - Installed code, Python 3.13: stays `blocked`, admission paused on all 10 calls.
  - The same code with only `st_ctime_ns` read the same way on both sides: `native_checkpoint_observed`, then `checkpoint_and_history_shrink_verified evidence=next_request_shrink` (223680), admission open on the first call.

**Same pattern on this machine's own routers (Python 3.14):**
- 9524 and 9464 are in `phase=compacting` with `smaller_request_before_checkpoint` and `context_recovery` deferrals.
- On 9524's transcript, the installed scan returns None 3/3 under 3.14. Under 3.11 it returns the 06:11:35 manual `/compact` checkpoint 3/3 ([probe](evidence/own-session-9524-scan.txt)).

**Visibility:**
- `/ca/channel/delivery-health` reports `state=not_blocked, admission_blocked=false` while the router defers.
- `runtime_input_delivery_health.py:156-160` sets `admission_blocked` to False whenever status history is readable. It does not read the compaction admission state.

# Not determined

- Why 6725 and 6726 stay `submitted` although both reached the transcript.
- The Python versions of the .14 sandboxes. They were not checked, because the request concerned celly only.
- The transcript writes the cadence probe saw: 10 changes in 60 s, gaps of 0.05–27 s ([cadence](evidence/transcript-write-cadence.json)). They decide how often a scan races an append even with the ctime comparison removed, but that rate was not measured in the router.

# Correction

- One probe was first labelled Python 3.14. The `python` on this shell's PATH is 3.11.6, so the file is now `scan-probe-py311.json`. The 3.14 results above come from explicit `pythoncore-3.14-64` runs.
