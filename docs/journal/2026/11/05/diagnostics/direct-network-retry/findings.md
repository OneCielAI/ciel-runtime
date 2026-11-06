---
type: diagnostic
title: Direct network retry wire evidence
description: Distinguish CLI direct gateway traffic from router native-protocol forwarding before changing retries.
tags: [cielairouter, retry, wire, diagnostic]
timestamp: 2026-11-05
---

# Confirmed source paths

runtime_launch.py486-492 resolves launch data path and passes it to environment projection. claude_environment.py637-648 assigns CLAUDE_CODE_MAX_RETRIES=configured_retry_limit only for direct launches and zero for routed launches. launch_data_path.py explicitly documents direct model HTTP bypassing local request interception. upstream_retry.py517 names router-to-provider native protocol forwarding upstream_direct_request; that log label alone is NOT proof of CLI bypass.

# Owned actual CLI wire measurement

Two actual installed binaries tested against owned loopback HTTP fixtures, not mocked openers:

| CLI | First response | Message HTTP hits | Result |
|---|---|---:|---|
| 2.1.310 and 2.1.318 | empty HTTP502 | 2 | ok |
| 2.1.310 and 2.1.318 | connection closed before response | 2 | ok |
| 2.1.310 and 2.1.318 | HTTP200 transient SSE error | 2 | ok |
| 2.1.310 and 2.1.318 | HTTP200 empty SSE | 1 | API Error: Stream ended without receiving any events |

Controls: MAX_RETRIES=3, RETRY_WATCHDOG=0, NONSTREAMING_TIMEOUT_RETRIES=0, DISABLE_NONSTREAMING_FALLBACK=1. Bare print-mode probes are not yet proof for interactive/resume mode. Empty stream is a confirmed unhandled class, not yet proven to explain the user's reported HTTP502.

# Live local observation

Router9524 launch log explicitly has ANTHROPIC_BASE_URL=http://127.0.0.1:9524. Its process loads immutable a2e92bf. At13:53:46 four router-origin HTTP502 records reached local POST /v1/messages. Earlier same operation timeout retries spent1/3,2/3,3/3 at approximately300-second intervals. This establishes that particular session's routed path, not all CielAiRouter sessions' path. No restart or configuration change performed.

# Grant sandbox read-only observation

SAMA router8872 health: account aapw-a20u22, ganti-ai workspace, version0c3b177. Recent runtime observations show routed /v1/messages HTTP200 and ongoing tools. A snapshot of161 events contains25 work_node_update calls,19 tool errors,0 network error events. Screenshot14 supplies repeated id-only input and NO CHANGE errors; recent API intentionally excludes raw tool arguments, so input stripping vs generation is not independently resolved. Direct router.log read was refused; no access bypass attempted. SSE worker connected/heartbeat observed with recent EOF reconnection history; this does not establish side mailbox consumption.

# Reproduced router native-protocol transport gap

Owned real HTTP server consumes the request then closes without sending response headers. Production native passthrough before change: upstream_hits=1, RemoteDisconnected, downstream_bytes=0. Native direct CLI retries the same class with MAX_RETRIES=3; routed CLI has native retries disabled, while runtime's retry policy excluded this typed disconnect. The live transcript separately records WinError10054/ConnectionResetError failures. This identifies a concrete mismatch, not proof that every502 shares this cause.

Source correction admits only ConnectionResetError/RemoteDisconnected (including typed URLError.reason) raised by open_once into the existing request-scoped retry budget. Read-time failures, generic transport strings, broken pipe and TLS EOF remain excluded. Already exposed output prevents retry. This can duplicate upstream computation/billing just like the existing open-timeout policy; no claim is made that a consumed request was unsent.

After correction the same real HTTP fixture reaches upstream_hits=2, returns211 bytes and one downstream200 response.48 focused retry/integration/initial-stream tests pass; a permanent real-socket regression verifies exact byte preservation and one downstream success.

# Remaining

Need interactive exact-launch repro, per-case wire timestamps/status/framing and retry budget/exhaustion correlation. Do not change production retry policy from empty-stream evidence alone or replay after tool/output exposure. JSON mod rendering and selection-description defects tracked separately; no claim they are fixed.
