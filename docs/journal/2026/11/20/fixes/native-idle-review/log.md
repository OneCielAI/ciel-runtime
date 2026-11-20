# Change log

## 2026-11-20

- 10:35Z: Real-backend suite finished: 117 pass, 0 fail, 3 skipped. Observed Andy post-restart on c955630: P0 n2471 Gen-4 launched cleanly; P1 n2503 Gen-2 was orphaned by startup sweep before resume, leaving its prior worktree's commit behind, an active coordination run, and Gen-1's `rework_ready` row in `review.pending`.
- 10:08Z: Local install of beta c955630 for Andy. The global npm package moved from 71db761 to c955630. The `CIEL_RUNTIME_HOME` folder Andy's session runs from (hotfix5) and the default home now match the npm package file for file (0 hash differences each). Andy's running router and claude.exe were not restarted.
- 10:15Z: `test_work_mod_real_backend.py` ran 117 tests with 3 fixture failures (all assuming `native_observation` never publishes reviews). Updated the 3 test fixtures (product code unchanged); all 3 pass on rerun, full real-backend rerun started.
- 09:43Z: npm beta 0.2.51-beta.20261120-093213.c955630 published (gitHead c955630b). Its two changed files hash the same as the local, Celly and KOBE overlays (570F1C2D…, FA499625…).
- 09:32Z: beta c955630 pushed (fast-forward from 9e718ac). Linux Docker (Python 3.11.2, Node 22) on the archived merge: 510 tests, 0 failures, 81 skipped.
- 09:29Z: Local, Celly and KOBE overlays of the two files (hash-verified, backups). Beta merge c955630 built locally; Linux run started.
- 09:25Z: f51f8eb committed. Windows affected bundle 510 pass; plugin test 89 pass.
- 09:15Z: Fail-first tests written: router publication test and mod wake scenario fail on 9fa764d.
- 09:10Z: Read Andy's n2505 state and transcript; review delay traced to turn-end-only idle publication.
