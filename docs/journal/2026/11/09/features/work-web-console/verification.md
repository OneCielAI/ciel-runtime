---
type: Verification
title: Work web console implementation evidence
description: Focused tests, real-controller browser run, screenshots and known remaining release work.
timestamp: 2026-11-10T00:20:00Z
tags: [work, web, rest, playwright, tls, acme, verification]
---

# Focused automated checks

| Area | Result |
|---|---|
| Work HTTP v1 + legacy compatibility | 29 tests OK |
| Work UI contract and console route mounting | 10 tests OK |
| Router access | 17 tests OK |
| Web access | 17 tests OK |
| Web endpoint/TLS settings | 22 tests OK |
| Router server transport | 4 tests OK |
| TLS runtime | 5 tests OK |
| Same-port TLS listener | 3 tests OK |
| ACME lifecycle | 8 tests OK |
| Stdlib ACME/JWS/HTTP-01 adapter | 3 tests OK |
| Runtime paths | 3 tests OK |
| Ruff and compileall | clean |
| Live router 9505/9524 health after focused runs | unchanged, HTTP 200 |

The broad `test_*runtime.py` discovery seen earlier was not repeated. Focused tests ran with `CIEL_RUNTIME_TEST_ISOLATED=1`.

# Architecture budget

The first full unit run failed two budget checks: `ciel_runtime.py` had 5007 lines against the ratchet budget of 4980. TLS composition moved to `ciel_runtime_support/web_tls_composition.py`, and the Work console shell is now mounted by `WorkHttpRoutes` inside `work_http_runtime.py`. The facade went back to exactly 4980 lines, the same as the base commit, with LF endings preserved. `test_architecture_budget.py` (2 tests) and `test_runtime_compaction_binding.py` (24 tests) then passed.

# Real browser proof

`evidence/e2e_work_console.py` started a real `ThreadingHTTPServer` with:
- `WorkHttpController` and its real file-backed current-session store;
- `WorkWebController`;
- existing `WebAccessHttpController` account/session implementation;
- existing `RouterAccessPolicy` with a non-loopback peer;
- a separate administrator-token REST client.

Playwright Chromium then:
1. was redirected from `/ca/work/ui` to `/ca/login`;
2. signed in with an owned test web account;
3. created two goals through the browser and moved the waiting goal by `queue_position`;
4. loaded the live settings projection;
5. accepted a third goal from a separate REST client using the existing administrator bearer token;
6. observed that external goal in the browser through Work SSE;
7. received 409 `version_conflict` for a stale external write;
8. rendered a 390 px viewport with no horizontal overflow and no page errors.

Evidence:
- [Login](evidence/00-login.png)
- [Desktop console](evidence/01-work-console-desktop.png)
- [Mobile console](evidence/02-work-console-mobile.png)
- [Machine result](evidence/e2e-result.json)

# TLS/ACME evidence

- The owned real-router direct-certificate run is recorded in [TLS and ACME](tls-acme.md#verified-on-2026-11-09): [screenshot](evidence/03-work-console-direct-tls.png) and [JSON](evidence/tls-router-result.json).

- Direct certificate bundles are loaded into a TLS server context only after SAN, validity and key-match validation.
- The router server transport hook is applied before watchdog/services start.
- Managed ACME uses a dedicated port-80 HTTP-01 listener, RSA/JWS ACME v2 adapter, restricted account storage and atomic certificate activation.
- Live Let's Encrypt staging and production directory fetches both advertised `classic`, `shortlived`, `tlsserver`, and a `newOrder` endpoint.

A complete public certificate issuance was not run: no user-selected public IP target was supplied, and such a run creates an external CA account/order and requires public port 80. This is reported as unverified, not claimed complete.

# Full regression groups

The final run, after the same-port TLS change, passed all supported groups with inherited `CIEL_RUNTIME_*` variables removed by `scripts/run_test_group.py`:

| Group | Result |
|---|---|
| unit | 3534 tests OK, 50 skipped, 394.942 s |
| router | 1552 tests OK, 692.795 s |
| channel | 607 tests OK, 80 skipped, 15.704 s |
| runtime | 458 tests OK, 19 skipped, 45.178 s |

Live routers remained on the same listening PIDs before and after the run: port 9505 PID 76496 and port 9524 PID 82844; both returned HTTP 200. Output: [final run](evidence/full-tests/all-groups-final.txt); the first run, before the TLS change: [first run](evidence/full-tests/all-groups.txt).

# Remaining

- Commit/merge to beta, wait for CI and Publish, verify registry SHA, install locally, and smoke the installed package.
- Perform a public-IP issuance only when an owned public target is selected.
