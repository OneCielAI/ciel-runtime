# Walkie durable_replied skip (router 9505, 2026-10-26)

Investigation of Walkie inputs that were received but skipped as `durable_replied` after the 13:32Z unclean reboot (read-only on the live router), and the fix that stops queue ids from reusing recorded status ids.

- [findings.md](findings.md): answers to the six questions, with confirmed facts and hypotheses kept apart
- [evidence/01-status-rows-14876-14907.txt](evidence/01-status-rows-14876-14907.txt): status history rows for the reused ids
- [evidence/02-runtime-inputs-tail-structure.txt](evidence/02-runtime-inputs-tail-structure.txt): the NUL run in the queue and the reissued ids
- [evidence/03-router-log-excerpts.txt](evidence/03-router-log-excerpts.txt): log lines from before the reboot (original ids) and after it (reused ids), plus the restarts
- [evidence/04-windows-unclean-shutdown.txt](evidence/04-windows-unclean-shutdown.txt): Kernel-Power 41 and EventLog 6008
- [evidence/05-running-version.txt](evidence/05-running-version.txt): /health, process command lines, fingerprint match
- [evidence/06-other-instances.txt](evidence/06-other-instances.txt): scan of other instances and workspaces
- [evidence/07-event-acks-request-id-reuse.txt](evidence/07-event-acks-request-id-reuse.txt): ids recorded with two SSE stream ids
- [evidence/08-code-paths.txt](evidence/08-code-paths.txt): code excerpts from the running snapshot
- [evidence/09-state-after-14907.txt](evidence/09-state-after-14907.txt): queue and status state after id 14907
- [evidence/10-sse-eof-pattern.txt](evidence/10-sse-eof-pattern.txt): EOFError spacing and the client code that raises it
- [evidence/11-sendmessage-blocked.txt](evidence/11-sendmessage-blocked.txt): the default tool block list that removes SendMessage
- [evidence/12-new-code-read-only-on-live-walkie.txt](evidence/12-new-code-read-only-on-live-walkie.txt): the fixed observer run read-only on the live files
- [evidence/13-window-closed-14908.txt](evidence/13-window-closed-14908.txt): 14908 was the first id injected again
- [evidence/14-new-tests-on-previous-code.txt](evidence/14-new-tests-on-previous-code.txt): new tests fail on the previous code
- [evidence/15-focused-tests.txt](evidence/15-focused-tests.txt), [evidence/17-focused-tests-after-lf.txt](evidence/17-focused-tests-after-lf.txt): focused tests on Python 3.14 and 3.10
- [evidence/16-ruff.txt](evidence/16-ruff.txt): ruff
- [evidence/18-sendmessage-eof-disposition.txt](evidence/18-sendmessage-eof-disposition.txt): SendMessage and EOF, recorded and not changed
- [evidence/e2e/](evidence/e2e/walkie_reuse_e2e.py): real-CLI runs before and after the fix
- [fix-verification.md](fix-verification.md): the fix and how it was verified
- [log.md](log.md): change history
