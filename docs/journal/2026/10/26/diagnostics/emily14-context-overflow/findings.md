---
type: Investigation
title: Emily context reached 102.8 percent without the ninety-percent safeguard
description: The existing CielAiRouter ninety-percent compact safeguard never saw Emily's usage because her model alias reports different upstream model names; the overflow came from a single step past 100 percent, and the following compaction timed out once.
tags: [cielairouter, compaction, safeguard, emily, diagnostics]
timestamp: 2026-10-26T21:16:00Z
---
# Target and scope

User screenshot: `ctx 895,991/872,000 tok (102.8%)`, `Prompt is too long · automatic compaction failed: API Error: Stream idle timeout - no chunks received`, then `Compacting conversation…`. Read-only on .14: no session, queue, config, process or file changed. Probes ran Python over SSH from stdin (`evidence/scripts/`). Remote local time is UTC-7.

- Emily: home `C:\Data\Source\SGSWMSCrossing\u4\home\emily-ai`, account `aapw-a11u4`. Launcher pid 4356, router pid 23436 on port 8966 (instance `8966-c27b0d656c05`), Claude Code 2.1.310 pid 7256 started 13:38 local with model `ciel-runtime-cielairouter-_walkie_opus55-sol61-fast` ([e1](evidence/e1.txt)).
- Runtime `0.2.51-nightly.20261026-201733.3c95a4b`. Router log level WARN ([e2](evidence/e2.txt), [e9](evidence/e9.txt)).
- Session `a9a016f2-bd19-4066-8b44-6ee329194e16` (transcript 1.2 GB).

# Confirmed

1. A ninety-percent safeguard for CielAiRouter already exists (commit 1c48c8e, 2026-10-23; included in 3c95a4b). `runtime_compaction_binding.py:524-545` samples the newest native assistant usage and queues one `/compact` when it is strictly above 90% of the context window (872,000 here).
2. It cannot see Emily's usage. Her transcript records carry the upstream model names `claude-opus-5-5` and `gpt-6.1-sol` ([e4](evidence/e4.txt)). The sampler (`native_model_usage.py:20-24, 64-66`) only accepts names derived from the configured alias. A local run of the same code with Emily-shaped records returns no sample for both names and for all three spellings of the alias ([10-sampler-probe](evidence/10-sampler-probe.txt)).
3. The overflow was a single step. Opus counts stayed between 749,363 and 767,163 (at most 87.98% of 872,000) from 20:40 to 20:48:39Z. The next response, at 20:49:12Z, was counted by `gpt-6.1-sol` at 895,991 (102.75%). No sample lay between 90% and 100%, so a 90% trigger would not have fired before this response even with matching names.
4. The next turn failed at 21:02:21Z with `Prompt is too long · automatic compaction failed: API Error: Stream idle timeout - no chunks received`. At the same time the router's own semantic compaction (20 chunks, 2,610,712 estimated tokens) retried one chunk after an upstream failure at 13:59:17 local and ended `client_cancelled` at 14:02:38 local ([e3](evidence/e3.txt), [e9](evidence/e9.txt)).
5. Claude Code then compacted at 21:06:42Z: trigger `auto`, 900,462 → 18,917 tokens, 244 s. Router admission state is `verified` after that shrink.

# Not determined

- Which provider the router resolved for the safeguard. The router reads the workspace config `workspaces\c27b0d656c05\config.json`; access to that directory was denied ([e8](evidence/e8.txt)). The shared config names `nvidia-hosted` as the current provider ([e5](evidence/e5.txt)). If the workspace config also does, the safeguard is disabled for this router as well.
- Why the alias answered with `gpt-6.1-sol` for that one response, and why its count was 129,336 higher than the previous Opus count.
- Why Claude Code received no chunks for the whole compaction request.

# Citations

[1] [processes](evidence/e1.txt), [versions](evidence/e2.txt), [router state](evidence/e3.txt), [transcript usage](evidence/e4.txt), [config keys](evidence/e5.txt), [launch state](evidence/e7.txt), [workspace access](evidence/e8.txt), [router log](evidence/e9.txt), [sampler probe](evidence/10-sampler-probe.txt)
