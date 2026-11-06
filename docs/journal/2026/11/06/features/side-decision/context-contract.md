---
type: diagnostic
title: Native parent context contract boundaries
description: Installed public APIs do not combine exact parent prefix with configurable token-tail history and tool-capable persistent custom-model execution.
tags: [context, mods, cache, api]
timestamp: 2026-11-06
---

# Verified public API surfaces

Source: installed `plugin-authoring/types/claude-code.d.ts`, native build 2.1.310, bundle `ea7c89487e13197033a335b7eb3ddbdb`.

| Surface | Confirmed capability | Boundary |
| --- | --- | --- |
| `model.fork` (2673–2690, 6523–6533) | Last parent request model/system/tools/messages prefix plus a prompt | All tool execution denied; no history/model selection argument |
| Agent `fork` (286–340, 3254–3259) | Background real subagent with inherited context/model | Model override ignored; exact byte-identical system serialization not promised; no history selector |
| `agent.register` (467–556) | MCP tools, custom model, background, maxTurns | `prompt` replaces the parent system |
| `session.send` (12157–12180) | Finished subagent resumes from its transcript | Delivery result is queue admission, not executed answer |
| `prompt.compose` (3074–3079, 8650–8656) | Current composed system text sections | Not the last wire request including engine cache boundaries/reminders |
| `session.messages({as:'api'})` (663–678, 2805–2809, 11838–11865) | Normalized next-request conversation after compaction | Newest4096entries; no pagination; excludes system/tool list/per-send additions |
| `session.append` (11101–11118) | Text-only hidden user notes or UI-only system notices | Cannot import assistant/tool/thinking history or replace operator system |
| `session.usage` (11519–11545, 12370–12386) | Aggregate last input totals and estimated breakdown | No per-message token counter or token suffix selector |
| `turn.step` (14362–14402) | Model/effort rewrite | Messages pinned; children skip their spawning hook |

# Actual native Agent fork wire probe

Owned run `side-actor-3c208b89` used `agent.spawn({subagentType:'fork'})`, resumed the same actor and observed two completed turns and both fixture MCP tools while main streamed. The parent request text was inherited. The model was unchanged.

Two substantive system blocks matched exactly; the leading native billing block differed (`cc_is_subagent=true` and fingerprint). Native fork also appended fork boilerplate to the parent user content and extended the environment system message. The observed whole messages prefix and whole tools/system fingerprints were not identical. Main's initial tool list lacked the MCP tools while fork included them, so that particular tools difference is not evidence of inherent fork filtering. These are measured request differences, not a claim of cache miss, cost, or cache exclusion semantics. No recent-token selector or separate fork model was verified. Summary: `evidence/native-fork-summary.json`.

# Native-only implementation consequence (superseded for routed path)

See [routed-context](routed-context.md): the existing Ciel router now projects the captured parent API prefix while native side agents continue owning tool execution. The native API limits below remain true; the earlier conclusion did not cover this router boundary.

Full/recent30percent plus exact parent system and optional separate side model remains unsupported by the inspected public composition. Native Agent fork is a distinct supported experiment worth wire verification, not a synonym for tool-less model.fork. It cannot by itself satisfy recent30percent or separate-model controls. No undocumented history mutation, false cache-sharing claim, or quoted-transcript substitution was introduced.

At the native-only investigation stage, dedicated side actors retained their own prompt/tools and the history/comparison settings were stored-only. That implementation status is superseded by the routed full/recent/comparison path described above. Owned native/router comparisons now run; their cache read/write values prove plumbing only, not paid-backend savings. Bounded authorized API KEYS - SSE measurements returned HTTP404/model_not_found, no_executable_targets, poolSize0. No successful live model response or real cache usage is available; the real-provider full/recent cost comparison remains unperformed.
