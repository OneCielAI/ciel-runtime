---
type: Diagnosis
title: Kobe .14 SSE inputs not delivered after an unattended restart
description: Why Walkie SSE inputs to Kobe's session on .14 were not delivered on 2026-10-26, and whether clock or timezone difference is the cause.
tags: [kobe, aap-14, sse, channel, restart, clock]
timestamp: 2026-10-27T01:30:00Z
---
# Answer

Clock or timezone difference is not the cause. After the company-prompt unattended restart at 12:15 PDT, the launcher submitted none of the queued SSE inputs. They waited until the next launch at 16:08 PDT, which expired them as older than the 600-second launch replay window. Time explains only the final expiry, not the non-delivery. Which branch blocked submission after the restart was not observed.

All .14 access was read-only (SSH, file reads). No remote file was written, the Kobe router was not called, and no queue, c‍ursor, configuration or process was changed.

# Clock and timezone (confirmed)

- .14 clock is behind this PC by 0.03 to 2.74 seconds: intersection of three SSH-bracketed readings ([p1](evidence/p1.raw.txt), [p3](evidence/p3.raw.txt), [p7](evidence/p7.raw.txt)). This PC is within 0.08 s of time.windows.com ([w32tm](evidence/local-w32tm.raw.txt)). The .14 w32time service is not running.
- .14 is Pacific Daylight Time (UTC-7). Queue `time` is local, status rows carry -07:00, ack and CloudEvent `time` are UTC; all are the same instants in different notation.
- `validate_cloud_event` reads only `id`, `source` and `type` (`ciel_runtime_support/external_event_receiver.py:205`); CloudEvent `time` is not used for admission.
- The only time-based drop is the launch backlog window, `CHANNEL_LLM_LAUNCH_RECENT_SECONDS_DEFAULT = 600.0` (`runtime_constants.py:127`), applied with the router's own clock to queue rows written on the same host (`runtime_input_status.py:271-293`). The expired inputs had waited up to 3 h 51 min; seconds of skew cannot produce that.

# Timeline, 2026-10-26 PDT (confirmed)

| Time | Event | Evidence |
| --- | --- | --- |
| 12:14 | #7653 submitted and replied | [p3](evidence/p3.raw.txt) |
| 12:15:09 | Company prompt v16 unattended restart; restart notice #7654 queued (tty) | [p3](evidence/p3.raw.txt), [p6](evidence/p6.raw.txt) |
| 12:16-16:03 | SSE #7655-#7677 queued (session_socket); none submitted. Transcript shows 0 socket injections from 19:15Z to 23:08Z while 28 user texts and 25 turns occurred | [p3](evidence/p3.raw.txt), [p4](evidence/p4.raw.txt) |
| 16:08:00-01 | New launch: #7654-#7676 (23 rows) skipped `launch_replay_window_expired` | [p3](evidence/p3.raw.txt) |
| 16:08:13 | #7677 (queued 16:03, inside 600 s) submitted | [p3](evidence/p3.raw.txt) |
| 16:18 | #7678 submitted and replied; delivery normal afterwards | [p3](evidence/p3.raw.txt) |

# Repeating pattern (confirmed)

All 12 restart notices in the queue are company-prompt unattended restarts. The five with status history (#5148, #5155, #5161, #6044, #7654) were never submitted and were later expired by a normal launch ([p6](evidence/p6.raw.txt)).

# Code path consistent with the observation (not observed as the cause)

- `runtime_launch.py:867`: an unattended restart sets `CIEL_RUNTIME_STARTUP_AUTO_ANSWER=1` in the launcher's own environment.
- `channel_terminal_proxy.py:624-625`: channel input is ready only when there is no startup responder, or ConPTY is used and `startup_answers.input_ready()` is true. With console-input transport the gate never opens.
- `startup_prompt_answers.py:371-374`: with ConPTY, an unread screen or a startup dialog text still on screen returns not ready before the 180-second give-up check at line 377.
- `channel_pending_poll.py:151`: when input is not ready the poll returns without scanning and without a log line.

Which of these branches applied, or whether compaction admission hold applied instead, is not determined: the router log level was ERROR, so the WARN lines were not written, and only current compaction state survives. That it can recur on the next company-prompt restart is an inference from the unchanged code.

# Not determined

- The exact gate branch, or compaction hold, between 12:15 and 16:08.
- Who started the 16:05-16:08 launch.
- The code version the 12:15 launcher had loaded.

# Separate observation

#7650 and #7677 are recorded `failed stale_unconfirmed`, yet the transcript shows both reached the model (19:04:45.9Z, 23:08:36.8Z). A failed status row does not prove non-delivery.
