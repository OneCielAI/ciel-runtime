# Change log

## 2026-11-08

- Found Prody's receiver in `backoff/admission/ValueError` with 16 consecutive failures and no admitted event since 03:49 PST.
  - Code confirms that one rejected frame blocks the stream: the cursor advances only after admission succeeds.
- Armed a one-shot metadata-only capture while Prody was stopped, captured the frame on resume, then restored the original package file by SHA-256.
  - Exact cause: the valid CloudEvent is 16,055 characters, 55 over the 16,000-character `btw_with_mcp` limit.
- Identified the `runtime_queued` pane rows as stale legacy board receipts. `n173` no longer exists.
- Corrected "side-inboxes absent": SYSTEM is denied access to the workspace state directory.
