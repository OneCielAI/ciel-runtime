# Directory Update Log

## 2026-10-24
* **Update**: Linked the fix (299b711, nightly 20261025-025145).
* **Update**: Read Regina (.25) read-only: at the 02:02 failure the live menu PID held the lease and the router had not started yet; installed file matches 4af30c7.
* **Update**: Second Regina report (full backup, 10-25 02:02) uses the same code path; confirmed no published or branch version changes the live-owner lease check.
* **Creation**: Reproduced Regina's menu backup failure in isolation and traced it to the launch-lease check added in 09017fb. No code or remote state changed.
