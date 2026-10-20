---
type: diagnostic
title: SSE network restoration and receiver recovery
description: Separate observed retry activity from unverified recovery failures.
timestamp: 2026-10-20
tags: [sse, network, recovery, diagnostics]
---

# Confirmed observations

A local incident router's two log files contain 338 URLError disconnect warnings,
from 2026-10-20T04:01:16 through 2026-10-20T08:46:09 (log-local timestamps).
These are repeated failed attempts, not evidence the retry thread stopped.
Earlier errors include three BrokenPipeError and one ConnectionAbortedError.
No private message bodies, endpoint URLs or credentials were copied.

# Source verification

- external_event_receiver.py:696-775 retries while enabled, with 1-second initial
  backoff doubling to 30 seconds; urlopen is called with timeout=90. Successful
  SSE response resets backoff and updates connected status.
- The same path sends a resume cursor and advances it after frame admission.
- external_event_receiver.py:758-760 logs only the exception class. URLError.reason
  is not preserved in this warning, preventing DNS/TLS/refusal attribution from
  the supplied lines alone.
- external_event_receiver.py:685-694 does not generally respawn unexpected worker
  exits; automatic replacement there is limited to a stop/start handoff.
- The existing heartbeat-idle test covers reconnect after an HTTP stream stall,
  not a real network-interface outage/restoration.

# Correction

The earlier answer said bounded exponential backoff and receiver state exposure
were unimplemented. This contradicts source: both already exist. Additional
observability or recovery gaps must be demonstrated individually rather than
presented as wholly missing reconnect handling.

# Unresolved

Network-restored time, endpoint reachability after restoration, actual nested
URLError reason, installed build identity and end-to-end recovery are unverified.
Thus an incident-specific Ciel reconnect defect is not yet proven. Recovery of
Ciel's subscriber after a reachable endpoint returns is the runtime's
responsibility; restoring the remote server/network itself is not.

# Citations

- https://docs.python.org/3/library/urllib.error.html — URLError.reason contains
  the underlying reason string or exception.
