---
type: Verification
title: Walkie id reuse fix verification
description: Runtime inputs no longer reuse an id that the status history recorded, the queue append is durable, and delivery health reports reuse. Verified by unit tests and real-CLI runs against the previous snapshot and the worktree.
tags: [walkie, runtime-input, durable_replied, id-reuse, fix, verification]
timestamp: 2026-10-26T20:10:00Z
---
# Change (worktree `ciel-runtime-wt-work`, branch nightly-20261026)

- `channel_message_repository.py`: a new port `issued_id_floor` returns the highest id the status history recorded. The next queue id is `max(queue max id, issued floor) + 1`. When the floor is higher, the router logs `WARN chat_message_id_above_issued_floor` with both values. An unreadable floor is logged and ignored, so allocation never stops. The append now calls `flush()` and `os.fsync()` before returning, like the status store.
- `runtime_input_status.py`: `recorded_max_request_id(path)` reads the status history incrementally, caches by file identity and offset, rescans when the file shrinks or is replaced, and ignores damaged rows (NUL bytes are stripped first).
- `ciel_runtime.py` and `channel_message_context.py`: only the private runtime-input queue gets the floor, from the status file next to it. The public chat queue is unchanged.
- `runtime_input_delivery_health.py`: `reused_id_count` and `reused_id_newest` count queue rows (newest 1 MiB) whose terminal status was recorded more than 1 s before the row was created. The health text adds `reused ids N (newest #id, skipped as already delivered)`.
- `runtime_sse_recovery.py`: reuse is reported as a problem with reason `input_id_reuse`, never as restart authority. A restart reopens the same files, so it would not help.

# Unit tests

- New tests fail on the previous code: 3 failures and 7 errors ([evidence/14](evidence/14-new-tests-on-previous-code.txt)).
- 19 related test modules, 776 tests, pass on Python 3.14.2 and 3.10.6 after the line-ending normalization. Live router PIDs were the same before and after ([evidence/17](evidence/17-focused-tests-after-lf.txt)). Ruff 0.15.22 passes ([evidence/16](evidence/16-ruff.txt)).

# Real CLI runs

Harness: [evidence/e2e/walkie_reuse_e2e.py](evidence/e2e/walkie_reuse_e2e.py). It uses the real launcher, router and `claude.exe` 2.1.310 with a loopback model fixture, scratch HOME and config, and stripped inherited variables. The console starts minimized without activation. Before launch it seeds the Walkie crash shape: a queue with id 1, then a NUL run ending in CRLF; status rows for ids 1-4 ending in `replied`; cursor 4. It then posts notifications one at a time to `/ca/chat/notify` until one is injected.

| Run | Runtime | Ids issued | Decisions | Fixed observer on the run's files |
|---|---|---|---|---|
| `run-old-142155`, `run-old-142518` | snapshot 2faae14 (before the fix) | 2, 3, 4, 5 | 2, 3, 4 `skipped_noise reason=durable_replied`; 5 injected | `reused_id_count` 3, newest 4 |
| `run-fixed-142440` | worktree | 5 | `WARN chat_message_id_above_issued_floor queue_max_id=1 issued_floor=4 next_id=5`; 5 injected, then queued, submitted and replied | `reused_id_count` 0 |

- The old snapshot reproduces Walkie: three post-crash inputs never reached the model. The fixture saw only the baseline turn and `WALKIE-NOTE-FOUR`.
- The worktree gave the first post-crash input a fresh id, and it reached the model (`WALKIE-NOTE-ONE`).
- Screenshots: `run-old-142155/02-after-notifications.png` (only id 5 arrives) and `run-fixed-142440/02-after-notifications.png` (id 5 is the first notification).
- Live routers 9505 and 9524 had the same PIDs before and after each run. Teardown killed only processes that carried the run's scratch path.
- Foreground: the owned console was never the foreground window in the last three runs (0 samples). The first worktree run (`focus-unexplained-run-fixed-142315`) counted 73 foreground samples, about 3.6 s; it had no timestamps, so the cause was not determined. The sampler now records intervals.
- Invalid run: `invalid-run-old-141934-fixture-shape`. The fixture had no Anthropic Messages reply shape, and one marker was a substring of another. Both were fixed before the runs above.
- After the runs I renamed one comprehension variable in `console_watch.py` for ruff and normalized line endings. Behavior is unchanged.

# What this does not do

- It does not recover the 31 inputs (14877-14907) that Walkie lost. Their queue rows are NUL bytes; only status rows and some event-ack rows remain.
- The running Walkie router keeps the code it was started with until the user restarts it.
- SendMessage and the SSE EOF loop are not changed ([evidence/18](evidence/18-sendmessage-eof-disposition.txt)).
