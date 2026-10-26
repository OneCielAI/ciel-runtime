# Directory Update Log

## 2026-10-26
* **Update**: Installed-code CLI E2E passed: in all seven cases the child requested the parent's current model, and no `inherit` reached the wire. The 9464 router changed PID during a superseded attempt's window (cause not established) and is recorded.
* **Update**: Pushed source commit d15822f to nightly, verified CI and npm Publish, installed nightly 0.2.51-nightly.20261026-044702.d15822f locally with per-file replacements, and checked installed tests and unchanged live PIDs. Installed-code E2E verification in progress.

## 2026-10-25
* **Creation**: Forced Claude subagents and teammates onto the parent's current model for every non-Anthropic provider, kept `inherit` intact on the direct data path, and recorded exact-file tests and real Claude CLI E2E. Not committed, pushed or installed.
