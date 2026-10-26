# Update Log

## 2026-10-24
- **Creation**: Read-only diagnosis of celly (router 9385, a7e28f9, Python 3.13). Inputs 6727–6735 queued, deferred `context_recovery` since 05:49. The native checkpoint scan never returns the 05:49:15 compact boundary because `2e55ca4` compares `Path.stat` against `os.fstat` including `st_ctime_ns`, which differ on Windows for Python 3.12+. A replay on a state copy verifies at once without that difference. Same stall seen on local routers 9524 and 9464. No changes made.
