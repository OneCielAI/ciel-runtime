---
type: Security Decision
title: Work web console access boundary
description: The web console and its REST API use the router's existing credentials; forwarded loopback requests must authenticate.
timestamp: 2026-11-09T22:30:00Z
tags: [security, router-access, web-session, proxy]
---

# Boundary

The Work console, Work REST v1 and the legacy `/ca/work*` routes all pass through the existing router access check before dispatch. No Work-specific credential is created.

| Caller | Access |
|---|---|
| Direct local peer, no forwarding headers | Allowed (unchanged) |
| Loopback peer carrying `Forwarded` / `X-Forwarded-*` | Treated as forwarded; needs the existing admin token or a signed-in web session |
| External peer, external access off | 403 |
| External peer, external access on | Existing admin token (`Authorization: Bearer`, `x-ciel-runtime-token`) or signed-in web session |
| Remote-bridge token | Only its existing LLM/file paths; never Work |

# Changes

- `router_access.request_from_forwarding_proxy` recognises a loopback peer that carries forwarding headers. `RouterAccessPolicy.request_allowed` no longer grants loopback trust to such a request.
- The session cookie is marked `Secure` only when:
  - the connection itself is TLS; or
  - a loopback peer carries the managed frontend marker `x-ciel-runtime-forwarded: 1` together with `X-Forwarded-Proto: https`.

  A client-supplied `X-Forwarded-Proto` alone no longer sets it.

# Kept as is

- Session cookies stay `HttpOnly; SameSite=Strict`, and session-authenticated writes still require a same-origin `Origin`.
- No CORS headers are added. The browser console is same-origin. Other services use the existing administrator bearer token.

# Tests

- `tests/test_router_access.py::test_forwarded_loopback_request_does_not_bypass_external_auth`
- `tests/test_web_access.py::test_forged_forwarded_proto_does_not_mark_direct_http_cookie_secure`
- `tests/test_web_access.py::test_managed_forwarded_https_marks_cookie_secure`
