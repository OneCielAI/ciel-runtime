---
okf_version: "0.1"
---
# Celly SSE injection stall

- [Finding](finding.md) — external inputs queued behind a compaction admission that never reopens: the Windows `stat`/`fstat` `st_ctime_ns` mismatch makes every native checkpoint scan return nothing on Python 3.12+.
- [Evidence](evidence/) — celly status rows, delivery health, router log copy (token redacted), scan probes, admission replay, ctime probe.
