# Change log

## 2026-11-09
- Completed the console: every REST command has a control (side review/handoff/retry, orphan release, sibling move, replace plan). Owned browser run verified each against the stores; capped the toast stack; added lease titles.
- Released fe559b3 to beta, verified the registry, installed locally and ran the TLS smoke on the installed snapshot.
- Replaced the whole-socket TLS wrap with per-connection TLS detection. Local clients keep `http://127.0.0.1`, and external peers must use HTTPS on the same port. Proved it on an owned real router: plain local health, verified HTTPS, REST v1 over HTTPS, and the console over HTTPS with live SSE.
- Fixed the queue card layout (title and meta on separate lines) and refreshed the browser evidence.
- Passed all four isolated full regression groups: unit 3530, router 1552, channel 607 and runtime 458 tests; live routers 9505/9524 remained healthy on their original PIDs.
- Fixed the facade line-budget regression without behaviour change: TLS composition is a support module, and `/ca/work/ui` is mounted through `WorkHttpRoutes`. `ciel_runtime.py` is back to 4980 lines and the budget tests pass.
- Recorded the request, the user's decisions and the loopback-proxy finding.
- Closed the forwarded-loopback authentication bypass and the spoofable Secure cookie flag, with tests.
- During a TLS/ACME review, a broad unisolated `unittest discover -p test_*runtime.py` run started production-facing runtime paths at 17:29. At the same minute, live router 9505 began reporting HTTP 401 and `cielairouter requires a configured API key`. A separate P1 follow-up item records the required incident investigation and fix; causation is not yet claimed.
- Added current-session REST v1, durable idempotency receipts, queue/active/sibling semantics, settings/parallel/side/audit projections and human operator commands while preserving legacy Work REST.
- Added the responsive Work web console and wired `/ca/work/ui` into the router.
- Added direct TLS certificate validation/activation, ACME lifecycle, a stdlib ACME v2 RSA/JWS adapter and a bounded HTTP-01 listener. Live Let's Encrypt staging/production directory profile discovery passed; public issuance still needs an owned public IP target.
- Completed an owned real-controller Playwright run: login, browser CRUD/queue mutation, external bearer REST mutation observed over SSE, stale-CAS rejection, desktop/mobile screenshots and zero page errors.
