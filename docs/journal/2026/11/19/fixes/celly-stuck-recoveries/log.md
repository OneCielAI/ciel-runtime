# Change log

## 2026-11-20

- 06:58Z: npm beta 0.2.51-beta.20261120-065417.9e718ac published (gitHead 9e718ac2). Local, Celly and KOBE carry the same work_parallel_mcp.py (hash 2BA145B7…).
- 06:45Z: rework-of-rework P0 intent fix (084600b): Linux 454 pass; beta 9e718ac pushed.

- 05:24Z: npm beta 0.2.51-beta.20261120-051824.6e0818e published (gitHead 6e0818e8). Local home, Celly and KOBE overlays carry the same four changed files (hash-checked).
- 05:18Z: beta 6e0818e pushed (merge of ad6eb74, cfe88b6, 460f264). Linux Docker 453 pass on the archived commit; Windows real-backend bundle 161 pass.
- 04:50Z: main review AttributeError on a run with exit None fixed (Betty report); fail-first test added.

- 03:40Z: Review reminder ignores a pane snapshot older than the review queue (Andy: 20 finalized results reminded). Fail-first scenario added. n2471 API-block lease recorded as an open observation.
- 03:35Z: Linux Docker (Python 3.11.2, Node 22) ran the 451-test bundle with zero failures.

- 03:10Z: Wrote this journal: copy repro before/after and fail-first tests as evidence. Peer reported that n2040 surfaced for review about 8 minutes after its receipt and was finalized; cause unknown.
- 03:08Z: Celly router still logs old-code KeyErrors after the overlay. The fix needs a session restart; none was done.
- 03:07Z: KOBE overlay (4 files, hash-verified, backup). Celly side session set to `fresh` through REST. Andy and local workspace configs set to `fresh`.
- 03:03Z: Local home switched to the hotfix copy. Celly overlay (4 files, hash-verified, backup).
- 02:40Z–03:00Z: Reproduced all five stuck rows on Celly's state copy with db23560 (KeyError) and fixed them (rework_ready). Added two P0 tests and two mod tests; all four fail on db23560.
