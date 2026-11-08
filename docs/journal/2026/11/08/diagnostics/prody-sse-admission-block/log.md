# Change log

## 2026-11-08

- Found Prody's receiver in `backoff/admission/ValueError` with 16 consecutive failures and no admitted event since 03:49 PST.
  - Code confirms that one rejected frame blocks the stream: the cursor advances only after admission succeeds.
  - The exact message is not determined: the router logs at ERROR only.
- Identified the `runtime_queued` pane rows as stale legacy board receipts. `n173` no longer exists.
- Corrected "side-inboxes absent": SYSTEM is denied access to the workspace state directory.
