# Change log

## 2026-11-09
- Executed the repair through Celly's local aap-win console: all five rows requeued with backup, four actors handled the distinct sources, and the duplicate source was settled without another actor. Zero new 409s.
- Recorded the 4eebfdc relaunch: side work resumed after main's 15:14:21 request, and five events stayed quarantined.
- Wrote and tested a re-queue script. Celly's account must run it, because this account is denied Celly's state.
- Read Celly's router log and transcripts. All 38 side requests after the 503be09 relaunch were refused: the captured parent came from the old build and lacked the two new side tools.
- Counted the retries per event: five events reached six attempts, and two were at four when Celly exited.
