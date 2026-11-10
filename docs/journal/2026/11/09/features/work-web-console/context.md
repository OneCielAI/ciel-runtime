---
type: Context
title: Work web console and REST API — request and decisions
description: Why a browser Work console is being built, what the user decided, and the security finding that changed the access policy.
timestamp: 2026-11-09T22:30:00Z
tags: [work, web, rest, tls, acme, security]
---

# Request

The terminal Work Observatory is a Claude Code Mod pane. It cannot use the full screen, it receives keys only after a click, and it exposes only part of the Work operations. The user asked for a separate web interface that shows the current Work state live and makes Work easier to operate.

Requirements given during planning:
- Every web feature must also be exposed as REST, so other services can use it later.
- The existing queue position and queue manipulation REST must be reorganised under the new interface.
- No new API key: REST and the browser use the existing web security logic.

# Decisions

| Question | Answer |
|---|---|
| Scope | Current session only |
| Web operations | Full Work operation |
| TLS | Both internal-CA/user certificate and public-IP certificate |
| Public-IP certificate | ACME automation included in this work |
| Credentials | Existing web account session and existing router administrator token; no new key |
| Proxy handling | Fix the existing policy: proxied requests must authenticate; only direct local requests keep loopback trust |

# Findings that shaped the design

- **Existing queue REST.** The only existing queue manipulation REST is `POST /ca/work/nodes/reorder` (commit `701ed64`, 2026-10-26). No `/queue`, `/position` or `/prioritize` route existed in any branch.
- **REST and MCP diverged.**
  - REST implements four mutations directly on the tree.
  - MCP exposes more operations: plan set, select, execute, replan, focus, urgent and priority.
- **REST accepted any session.** The existing Work REST accepted any well-formed `session_id`, not only the router's current session.
- **Loopback bypass.**
  - `RouterAccessPolicy.request_allowed` returned allowed for every loopback peer before checking tokens or sessions.
  - Tailscale Serve proxies to `http://127.0.0.1:<port>`, so a request forwarded through an HTTPS frontend was treated as local.
- **Spoofable Secure flag.** The session cookie's `Secure` flag trusted any `X-Forwarded-Proto` header.
