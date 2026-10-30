---
type: Fix Contract
title: Bounded launch model-list probe retry
description: Source-level contract for one provider-owned timeout retry without bypassing readiness.
tags: [launch, readiness, cielairouter, timeout]
timestamp: 2026-10-29
---
# Scope

ProviderStatusPolicy gains probe_retry_timeout_seconds, default 0.0. Only CielAiRouter opts in at 30.0 seconds; its initial timeout stays 10.0 seconds. Both generic and catalog status GET probes use one second attempt only for TimeoutError or URLError whose immediate reason is TimeoutError. HTTP errors (including an HTTPError carrying TimeoutError as reason), authentication rejection, TLS, DNS, connection refusal, parsing and projection failures never trigger retry.

# Fail closed

Two timeouts remain model-list probe timeout / unreachable, retaining the existing launch blocker. A recovered retry reports its success and 30s timeout. Generic probes distinguish a timeout before headers from a body-read timeout after headers. Retry-enabled generic probes read at most 1 MiB plus one overflow-detection byte, rather than the legacy 128 KiB preview (the measured catalog was 389,740 bytes); malformed, oversized or structurally incomplete catalogs remain unreachable. Providers without retry retain their preview reads. Optional context lookup errors after a parsed model catalog report context unavailable without changing reachability.

The policy bounds attempts and socket-operation timeout values. urllib timeouts are not an absolute whole-request wall-clock deadline; a continuously trickling response can exceed 40 seconds overall. No bypass, background retry or infinite retry was introduced.

# Evidence boundaries

The historical authenticated HTTP 200 timings (15.9 / 25.4 seconds) are recorded in docs/journal/2026/10/27/diagnostics/launch-base-url-timeout/findings.md. They justify the latency case but do not establish the cause of the latest screenshot incident. Betty v9077265 identity was established by the parent investigation; workspace configuration ACL restrictions prevent claiming the exact latest cause.

No commit, push, installation, remote command, actual CLI launch or live-account test was performed in this source-only subtask. Parent owns loopback and CLI verification. See [tests](tests.md).
