# Change log

## 2026-11-07

- Proved the SSE combo had no executable targets (poolSize 0) while the main combo answered on the same router.
- Settled the duplicated raw main fallback for receipt `e55f54c82f35` so it is not resubmitted.
- Committed `f37cbac`: the side always inherits the main session model, connection and auth; raw main injection is removed; legacy pending fallbacks are reclaimed by the side.
- Installed local `0.2.51-local.20261107-f37cbac`. Live verification is waiting for the user to relaunch the Walkie session.
