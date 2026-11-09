# Change log

## 2026-11-08

- Fixed with 64 KiB chunked bounded reads in `work_plan_store`: read 6.6 ms -> 0.04 ms, reproduction 10/10 failed -> 0/10, regression test added.

- Reproduced the local unit failure. A replace is refused only while a reader handle is open. `read_snapshot`'s 32 MiB bounded read keeps the handle open for about 6 ms per call. Reason for the earlier local passes not established.
