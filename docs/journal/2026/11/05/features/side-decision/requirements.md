---
type: specification
title: Independent SSE decision and configurable context experiments
description: User requirements for independent MCP-enabled side processing with Work priority adjustment and measured context/model profiles.
tags: [sse, side-intake, work, priority, caching, configuration]
timestamp: 2026-11-05
---

# User requirements

The side processor must read and answer incoming messages independently of a long main Work item, use every MCP available to main under the same permission boundaries, inspect workload, merge related requests into existing items, and create roots only for new independent work. It may infer urgency from context and explicit P1..P5 item priorities, adjust the highest applicable queue order, and ask/advise main only when necessary for high-priority handling. Thousands of daily SSE events must not all enter main context.

# Context and model controls

Provide selectable full-transcript and recent-context modes; recent defaults to30percent of conversational input tokens, with system configuration retained and tool-call/result boundaries preserved. Actual selected token count/ratio and context freshness must be visible. Same system alone does not guarantee parent cache reuse: tools and message prefix also participate.

Allow inherit-main-model or a separately selected side model, analogous to an advisor selection. The user authorized comparison experiments with CielAiRouter's display name `AP Keys - SSE`; initial local cache lookup found no matching display fields. Subsequent configured provider catalog command `ciel_runtime.py models cielairouter` resolves upstream `API KEYS - SSE`, alias `ciel-runtime-cielairouter-api-keys-sse`; copy variants are distinct and not implicitly selected. User spelling AP vs catalog API is preserved as an observation. Do not invent the model ID or expose API credentials.

All controls must be reachable in initial runtime settings, live settings, and ciel-runtimectl through one canonical validator/configuration model. Add descriptions, effective values and apply timing; CLI-only is insufficient. Do not duplicate the existing session/btw_with_mcp selector.

# Comparison contract

Compare full/recent30percent context and inherited/separate model profiles on the same event corpus. Only the chosen primary run may mutate Work or send real replies; comparison runs are shadow/read-only. Measure decision correctness, P1 ordering, existing-work merging, main escalation count, latency, actual uncached/cache-read/cache-write/output usage, and selected provider/model/route. Cold/warm and cross-model cache domains are separate. Fixture request fingerprints are not real cache-hit proof.

# History and authority

Do not insert and later delete main API history as a cache technique. UI-only logs/panes are separate from model transcript. Preserve durable original/decision/action/result/reply status. Side model cannot forge parent session, execution approval, source identity or reply destinations. Queue reprioritization is not execution authorization. No live-session/router restart is authorized by these requirements.

# Complete settings and release channels

`ciel-runtimectl work` must expose all supported Work options, not only the new side flags: mode, plan approval, auto-continue, completed retention, parallel enable/pause/capacity/P1 reservation/launch rate/rework, discovery enable/instruction/file, SSE delivery, side context/model/priority/comparison. Enumerate from the same schema as initial/live menus and assert full parity, validation, descriptions and effective scope. Never expose private host tokens.

User explicitly changed future experimental release target to beta, not nightly. Channel selection is sticky across install/update/check/TUI/ctl: beta downloads only the newest beta dist-tag, nightly only newest nightly, and stable only newest latest stable. A newer version in another channel never triggers automatic channel migration. A missing or failed selected-channel artifact must not silently fall back to another channel. Only explicit user channel selection permits switching. Existing running sessions are not restarted.

# Status

Specification and capability-probe stage only. No claim of independent tool-enabled processing, compaction independence or cache savings yet. Plan: C:\\Users\\djlov\\.claude\\plans\\ethereal-mixing-dusk.md.
