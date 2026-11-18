# Log

## 2026-11-18

- Lead brief: Andy side-actor failures (page limit, claim order, note capacity, answer shape, persisted file), on top of 1b42a73.
- Read-only tally of Andy's side inbox on this PC (counts only). Page-limit failures are all from 11-18. Note-capacity refusals stopped on 11-16. The 14 persisted-file refusals all fall between 11-17 13:45 and 14:25Z.
- Persisted file: the 88 saved Walkie results Claude Code wrote in that window all carry the 2.1.326 suffix. The current rule accepts 88, the pre-ab6edbe rule 0. Cause measured, no code change.
- Long threads: every page is read, a ring of the newest 1600 replies is kept plus root and target, the omitted count goes to the receipt, a 100-page cap per pass with one jump to the target, never fails.
- Claim order: P0/P1 first, then a retry lane alternated one for one with new events (`claim-lane.json`).
- Side note on an executing goal goes to `progress`, so the approval scope digest of running forks stays the same.
- Clamps for summary/reply/note/title/understanding. One corrective turn before a shape refusal counts.
- Real load, first run: the corrective send inside the fork's turn end was declined by the engine ("Side SSE native fork authorization did not match its pending host record"). Added one retry 1.5 s after the hook. Second run: delivered, the fork corrected itself, and the decision committed.
- Fail-before 9 tests on HEAD; after all pass. Mod suite 105/105, ruff clean.
- Windows groups and Linux shards: recorded below when they finish.
- See [record.md](record.md).
