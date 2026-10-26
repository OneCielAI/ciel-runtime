---
type: Investigation
title: Walkie durable_replied skip after unclean reboot
description: Read-only proof that Walkie inputs 14877+ were skipped because the queue reissued ids whose status rows from before the 13:32Z unclean reboot say replied.
tags: [walkie, runtime-input, durable_replied, id-reuse, crash, diagnostics]
timestamp: 2026-10-26T16:15:00Z
---
# Scope

Read-only investigation of router 9505 (`9505-aae7db361c7f`, workspace digest `aae7db361c7f`, `G:/ciel-Walkie`). I did not restart, signal or modify any router, session, queue, cursor, config or log. The only HTTP call was `GET /health`. Times are UTC unless marked local (the logs use local UTC-5). Peer report: `G:/ciel-Walkie/docs/okf/journals/2026/10/26/runtime-injection/handoff.md`.

Legend: **Confirmed** means a log line, file record or source line shown in `evidence/`. **Hypothesis** means not verified.

# Answer in one paragraph (confirmed)

Windows rebooted uncleanly at 13:32:16Z. The private runtime-input queue `runtime-inputs.jsonl` lost the 31 rows it had issued since 11:27Z (ids 14877-14907); in their place there is a 172,094-byte NUL run. The status history `runtime-input-status.jsonl` was fsynced and kept those 31 ids marked `replied` (14889 marked `failed`). After the reboot the queue gives a new input the readable max id + 1, which was 14876 + 1, so it issued 14877 again for new Walkie events. The injector checks the status of an input by integer `request_id` only, so every new input whose id collides with a pre-crash row is skipped: `durable_replied` for collisions with replied rows, `prior_submission_failed` for 14889. On the queue side nothing ties a status row to the message it described.

# 1. What `durable_replied` checks, and which code runs

Running code (confirmed, `evidence/05-running-version.txt`):

- `/health` on 9505: version `0.2.51`, source_fingerprint `f86ee3638ee760f3`, pid 43372, active client 70272.
- Both processes run `C:\Users\djlov\.local\share\ciel-runtime-work-breakdown-20261026-122744\ciel_runtime.py` (`serve` and `cli --continue`), and User `CIEL_RUNTIME_HOME` points at that snapshot. sha256(snapshot `ciel_runtime.py`)[:16] is `f86ee3638ee760f3`, the fingerprint `/health` reports (`runtime_primitives.py:8-10`). The source tree on nightly-20261020 HEAD d952c5a differs (`7829293dd0103a91`). The snapshot adds retry code, but the predicate, the status store and the queue append are textually the same in both.
- The 13:48Z launch also ran from this snapshot: `router.log:15` `claude_work_mode plugin_dir=...ciel-runtime-work-breakdown-20261026-122744...`.

Predicate (confirmed, snapshot line numbers, `evidence/08-code-paths.txt`):

- `channel_pending_injection.py:240-244`: `durable = wake_store.lifecycle.admission_status(message_id)`. If it is `replied` or `skipped`, the injector logs `channel_stdin_proxy_skipped_noise message_id=<id> reason=durable_<status>` and drops the input. Lines 225-239 run first and skip an id whose row is `failed` (`channel_stdin_proxy_blocked ... reason=prior_submission_failed`).
- `channel_wake_delivery_repository.py:66-69`: `admission_status` returns `status` from `RuntimeInputStatusRepository.get(message_id)`.
- `runtime_input_status.py:393-402`: `get` returns the **last row in the history file whose `request_id` equals the integer id**. It does not look at child_epoch, attempt_id, event id, created_at or queue generation.
- The history file is `WORKSPACE_STATE_DIR / "runtime-input-status.jsonl"` (`runtime_paths.py:120-124,184`), i.e. `%APPDATA%\ciel-runtime\workspaces\aae7db361c7f\runtime-input-status.jsonl`, wired at `ciel_runtime.py:605-609`.
- A row becomes `replied` through `ChannelWakeDeliveryRepository.complete` (`channel_wake_delivery_repository.py:129-139`) once there is wake-completion evidence.

# 2. The records that matched ids 14877-14892

Confirmed (`evidence/01-status-rows-14876-14907.txt`, `evidence/03-router-log-excerpts.txt`):

- The status history has full queued, submitted and replied rows for 14877-14907, written between 06:27:35 and 08:24:14 local (11:27Z-13:24Z), all with child_epoch `a4cb3e0f`. Example: 14881 at status lines 11661-11663, replied 06:37:08 local, attempt `5c9807e7`. 14889 is `failed stale_unconfirmed` (line 11687, 07:01:54 local).
- Those rows belong to **earlier, different inputs**. The pre-crash log shows them received and injected: `router.log.1:18513` `event_ack_received ... stream_id=33162 ... request_id=14877` at 06:27:35 local, `router.log.1:18516` `channel_stdin_proxy_injected ... message_ids=14877 ... transport=session_socket`, completion evidence at `router.log.1:18545`, and the last one `router.log.1:29865` (14907, 08:24:03 local).
- The event-ack store holds both generations for some ids: 14880 is stream 33165 at 11:36:05Z and stream 33226 at 14:54:50Z, and 14886 and 14887 also appear twice (`evidence/07-event-acks-request-id-reuse.txt`). That store does not have a row for every input.
- The queue shows the gap (`evidence/02-runtime-inputs-tail-structure.txt`): line 2680 is id 14876 at 05:55:18 local. Line 2681 is 172,094 NUL bytes followed by CRLF and cannot be decoded. Line 2682 is a **new** id 14877 at 09:10:04 local. No row with a pre-crash 14877-14907 timestamp is left.
- The status file was last modified at 08:24:14.76 local, the time of the 14907 `replied` write. Nothing was appended after the reboot.

So a fresh root post such as Walkie 90486 (now id 14881, 15:15:41Z) matched the row for the old 14881, which was received at 11:36:56Z and replied at 11:37:08Z. Content and threading play no part: only the integer id is compared.

Why the ids were reissued (confirmed by code):

- `channel_message_repository.py:95-96`: `next_id = self.max_id() + 1`. `max_id` (`:337-339`) scans the decodable queue rows, ids recoverable from damaged rows (`_RECOVERABLE_ID`, which matches nothing in a NUL run) and the legacy `.jsonl.1` file from Oct 14. The status history is not consulted. The readable max was 14876, so the next id was 14877.
- The cursor read path saw the rollback: `router.log:23` at 08:48:56 local, `channel_llm_cursor_queue_generation_reset recovered_cursor=0`. `channel_cursor_repository.py:32-33` emits this only when the stored cursor is above the queue tail. The cursor was reset. The status history was not.
- New inputs never get a `queued` row: `runtime_input_gateway.py:28-45` calls `transition(id, "queued")`, and `runtime_input_status.py:149-150` returns the existing terminal row without writing anything or logging.

Why the queue lost rows and the status store did not:

- Confirmed: the System log has Kernel-Power 41 and EventLog 6008, "The previous system shutdown at 8:32:16 AM on 10/26/2026 was unexpected", with boot at 13:33:02Z (`evidence/04-windows-unclean-shutdown.txt`). `router.log.1` stops at 08:32:30 local.
- Confirmed: the status store `_record` calls `flush()` and `os.fsync()` on every row (`runtime_input_status.py:338-348`). The queue append opens the file in text mode `"a"`, writes and closes it, with no `flush`/`fsync` call (`channel_message_repository.py:127-133`).
- Confirmed: the CRLF after the NUL run matches the code. `_ends_unterminated()` sees a NUL tail, adds the `"\n"` separator, and text mode on Windows writes it as `\r\n` (`:128-130`). The first append after the reboot (new 14877) is therefore the one that met the damaged tail.
- Hypothesis, not verified: NTFS kept the extended file length but not the cached data of the appends that were never flushed, so the range reads back as zeros. The size fits about 31 rows (nearby rows are 3.5-7.3 KB), but the lost bytes cannot be recovered to prove it.
- Other files in the same workspace (`event-acks.jsonl`, `chat-messages.jsonl`, the status history) have 0 NUL bytes.

# 3. Why neither restart cleared it

Confirmed:

- All the state involved is per workspace, not per router launch: `workspaces/aae7db361c7f/runtime-input-status.jsonl` and `runtime-inputs.jsonl`. A CLI-only restart (15:27:38Z, `router.log:5350`) and a full launcher plus router restart (15:33:38Z, router pid 43372 started at 15:35:49Z) both reopen the same two files with the same running snapshot.
- On launch, `prepare_for_launch` (`channel_delivery_context.py:205-238`) only moves the cursor, clear floor and launch guard, and expires rows whose status is `queued` behind the floor. Rows already `replied`/`failed` are not touched. The in-memory delivery caches are rebuilt, but `admission_status` reads the file.
- The queue's id frontier is still below 14907, so new inputs keep colliding: `router.log:5697` `fast_forward last_id=14882`. The restart notice itself became input 14884 (`router.log:5349` `claude_session_restart_notice_queued request_id=14884`, channel `restart`, transport `tty`) and was skipped as `durable_replied` (`router.log:5375`).
- The behaviour the code predicts has been seen after the restarts. 14889 arrived at 16:08:23Z and was dropped by the failed row: `router.log:7962` `channel_stdin_proxy_blocked cursor=14888 message_id=14889 reason=prior_submission_failed detail=stale_unconfirmed action=skipped`. 14890-14892 were skipped `durable_replied` at 16:09Z.
- Expected from the code, not yet observed: 14893-14907 will also be skipped, and 14908 is the first id with no pre-crash row. This is still happening; ids 14877-14892 have been received and not injected.

# 4. Why `recover_sse_delivery status` said problem:false, known_queued_count:0

Confirmed by code:

- `runtime_sse_recovery.py:120-175`: `problem = admission_blocked or stale or paused`, where `stale` needs `known_queued_count > 0` with an old enough oldest queued input.
- `runtime_input_delivery_health.py:105-162`: `known_queued_count` counts request ids whose **latest status row is `queued`**. The latest rows for the new ids are the pre-crash `replied`/`failed` rows, and their `queued` transitions were refused without being written, so the count is 0. `admission_blocked` is `False` whenever the history is readable.
- The tool does not compare queue rows with the status rows' provenance and does not count skipped_noise decisions, so this state shows as healthy.
- Side fact, confirmed by code: `_input_rows` reads only the first 8 MiB (`_SCAN_BYTES`, `runtime_input_delivery_health.py:16`) of the 16.4 MB queue, so `queued_coverage` cannot be `complete` here. I did not see the peer's full output, so its `queued_coverage` value is unconfirmed.

# 5. The EOFError reconnect loop

Confirmed (`router.log.1`, `router.log`):

- `external_event_sse_disconnected receiver=default error=EOFError phase=read` appears 449 times in `router.log.1`, the first at 00:53:09 local (05:53Z), about 59 per hour. That includes 11:27Z-13:24Z, when all 31 original inputs were injected and completed. `router.log` has 132 more since 13:49:48Z.
- After the reboot, events still reach the queue (`event_ack_received ... outcome=queued request_id=...`). The skip is decided later, from status rows.
- Conclusion: there is no evidence linking the EOF loop to this skip, and the evidence above argues against it being the cause. I did not investigate what causes the EOF loop itself.

# 6. Other live instances

Confirmed (`evidence/06-other-instances.txt`):

- Router logs modified today for 9464, 9466, 9489 and 9524 have 0 `durable_replied` lines.
- Of the 13 workspaces, only `aae7db361c7f` has a status max id above its queue max id (14907 vs 14892) or a NUL-damaged queue line.
- In 9505's pre-crash `router.log.1`, all 60 ids skipped `durable_replied` had been injected earlier in the same log, which is the normal rescan case. In the post-crash `router.log`, 15 ids were skipped and 0 were injected.

# Related log lines the peer saw (confirmed by code)

- `channel_llm_inject_deferred ... reason=input_transport_tty` comes from `channel_llm_context.py:135-148`. The router-side injector defers any input whose transport is not `router`, and these inputs carry `meta.input_transport=session_socket` (evidence 02). The label says tty but the condition is "not router". Delivery is left to the terminal injector, which then skips the input as described above. This line is a consequence, not a separate cause.
- I did not examine `channel_windows_console_deferred reason=active_turn` in depth.

# Open (not determined)

- How exactly the filesystem turned the unflushed appends into NULs (the hypothesis above).
- What caused the 13:32:16Z unexpected shutdown. The event log only records that it happened.
- Whether the old 14877-14907 rows could be recovered from somewhere other than the event-ack store (which has only some of them) and the status history (which has no message bodies).

# Fix (2026-10-26)

The worktree change and its verification are in [fix-verification.md](fix-verification.md). The lost inputs 14877-14907 are not recoverable from the queue.
