---
type: diagnostic
title: Native side context and tool interception constraints
description: Verified engine contracts that affect parent-system sharing recent-context selection and secure side mutations.
tags: [mods, native, context, permission, constraints]
timestamp: 2026-11-05
---

# Historical status

The native-only limitation below records the 2026-11-05 investigation. The 2026-11-06 routed context implementation (`docs/journal/2026/11/06/features/side-decision/routed-context.md`) supersedes its implementation status for routed sessions; direct gateway remains unsupported. Unused selector and observer prototypes were preserved under local scratch rather than shipped as active implementation.

# Confirmed API contracts

Installed plugin-authoring claude-code.d.ts14359-14365: turn.step messageCount and history are pinned; hook can rewrite model/effort, not messages. AgentSpec.prompt replaces system(478-481). Native agent fork inherits full parent history/model and ignores modeloverride(300-340). model.fork reuses exact parent prefix but denies all tools(2672-2690). model.complete accepts one user prompt and custom system, no MCP tool loop(6352-6413).

Therefore the current dedicated native background agent does NOT yet meet the user's full/recent30percent plus identical-parent-system mode requirement. Configuring a flag is not implementing that behavior. Parent context retrieval through session.messages is capped at4096entries, so treating that as full without verification would also be wrong. Precise per-message token counts are not available through the inspected direct mod APIs; existing runtime count_tokens controller uses local estimate_tokens, not native tokenizer proof. The unconnected selector requires counts supplied explicitly and preserves complete tool pairs.

# Child tool call evidence

turn.step docs14397-14399 explicitly say a subagent spawned through agent.spawn steps past the spawning hook, as its tool calls do. Actual packaged CLI probes observed child work_status/resolve arriving without parent hook-injected session/token; resolution failed. Structured final decision is now applied by the parent complete hook with host-bound event/lease. Native disallowedTools prohibits direct Work writes; other parent MCP tools remain available with inherited permissions.

Attempting a second hooks module was refused by native validate (one module per plugin). Attempting duplicate unfiltered agent.spawn/tool.call registration through imported helper was also refused. Those attempts were removed from active hooks; plugin validate passes. A historical comment-only side-observer.js is not loaded. Do not claim arbitrary direct child Work writes are safely mediated by the parent's same hook.

# Remaining design proof

Need a supported context-preserving tool-enabled path for configurable context/system/model modes, and actual model/route cache measurement. Do not simulate this by serializing full messages as quoted user text and calling it identical context. No native internals/history rewrite or permission relaxation was performed.
