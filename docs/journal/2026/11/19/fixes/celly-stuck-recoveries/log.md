# Change log

## 2026-11-20

- 03:40Z: Review reminder ignores a pane snapshot older than the review queue (Andy: 20 finalized results reminded). Fail-first scenario added. n2471 API-block lease recorded as an open observation.
- 03:35Z: Linux Docker (Python 3.11.2, Node 22) ran the 451-test bundle with zero failures.

- 03:10Z: Wrote this journal: copy repro before/after and fail-first tests as evidence. Peer reported that n2040 surfaced for review about 8 minutes after its receipt and was finalized; cause unknown.
- 03:08Z: Celly router still logs old-code KeyErrors after the overlay. The fix needs a session restart; none was done.
- 03:07Z: KOBE overlay (4 files, hash-verified, backup). Celly side session set to `fresh` through REST. Andy and local workspace configs set to `fresh`.
- 03:03Z: Local home switched to the hotfix copy. Celly overlay (4 files, hash-verified, backup).
- 02:40Z–03:00Z: Reproduced all five stuck rows on Celly's state copy with db23560 (KeyError) and fixed them (rework_ready). Added two P0 tests and two mod tests; all four fail on db23560.
