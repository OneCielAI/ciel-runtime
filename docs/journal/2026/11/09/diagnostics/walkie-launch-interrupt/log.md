# Change log

## 2026-11-09
- Traced the codex SyntaxWarning on a Claude launch to a one-time compile of an always-imported module.
- Found the launch ended with KeyboardInterrupt in the readiness check; a clean check now returns no blockers.
- Recorded the side effect of an earlier reproduction that ran with inherited session variables.
- Timed the check's network calls, searched both snapshots for console Ctrl+C senders, and recorded the 12:00 relaunch.
