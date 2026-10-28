---
type: Diagnostic
title: CielAiRouter launch blocked by Base URL TimeoutError
description: The launch gate's 10 s catalog probe timed out while ciel-router-01 answered the authenticated catalog in 16-27 s; identical gate code in every compared version.
tags: [launch, readiness, cielairouter, timeout]
timestamp: 2026-10-27
---
# Scope

User screenshots #17/#18: launch menu (provider cielairouter, base URL `https://ciel-router-01.ezonebot.com`) shows `Launch blocked: Base URL: unreachable (TimeoutError)` after installing `0.2.51-nightly.20261027-080148.ea59a61`. Read-only probes only; no config, live router or session was changed. Raw output: [probe-outputs.txt](evidence/probe-outputs.txt), [gate-rounds-1.txt](evidence/gate-rounds-1.txt), [gate-alternating-cold.txt](evidence/gate-alternating-cold.txt).

# Confirmed

| Fact | Evidence |
|---|---|
| The message comes from `provider_status._generic_status` (any exception → `unreachable (<type>)`); `provider_readiness.launch_readiness_errors` turns it into the block. The menu calls `preflight_lines()` on open and `launch_readiness_errors()` on launch. | source lines 145, 61-62; `prelaunch.py` 323, 515-577 |
| CielAiRouter probes `GET /v1/models?prefix=alias&configuredOnly=true&availableOnly=true` with a 10.0 s timeout. | `providers/cielairouter.py` 227-231 |
| The gate code is byte-identical from 0555b6c to ea59a61; eec7d69→ea59a61 changed only Work files. | probe-outputs §7 |
| Network path healthy: DNS 0.023 s, TCP 0.041 s, TLS 1.3 0.069 s, unauthenticated GET 401 in 0.207 s. | §1 |
| With the API key the same GET returned 200 only after TTFB 15.9 s and 25.4 s (750 models, 389,740 bytes); `/v1/models` answered 503 after 13.0 s / 18.8 s. | §3 |
| ea59a61's gate reproduced the block 3/3 (10.1 s). Minutes later the same snapshot passed in 0.2-3.7 s. | §2, §5, gate-rounds-1 |
| The previous release eec7d69 is blocked the same way: `launch_readiness_errors` returned `Launch blocked: Base URL: unreachable (TimeoutError)` at 09:10:18 and 09:15:16 (10.1 s each); other samples in the same window passed in 0.2-6.3 s. | gate-alternating-cold |

# Conclusion (bounded)

The block occurs whenever the server's authenticated catalog response takes longer than the 10 s probe timeout. It is intermittent and not caused by the ea59a61 change: the same gate code blocks eec7d69 under the same server latency. "unreachable" here means "no response within 10 s"; the server was reachable and eventually returned 200.

# Unknown

- Why ciel-router-01's catalog response is intermittently 4-27 s (2026-10-02 measurement: 0.17-2.56 s). Server side, outside this repository; not investigated.
- Whether the user's machine in #17 is this host: the menu shows `127.0.0.1:9505` and the same base URL/model, but identity of the screenshot host is not verified.

# Not done

- No code change: no defect attributable to the new version was found. Changing the gate's semantics or timeout would be a product decision, not a regression fix.
- No actual launch-menu screenshot: opening the real menu on the live account touches the configuration that serves the running routers (9505/9524); not done without authorization. The function-level gate run above is the menu's own code path.
