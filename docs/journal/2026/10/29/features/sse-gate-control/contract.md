---
type: Feature
title: SSE compact admission gate controls
description: Local slash on/off/status, launch-menu toggle and truthful statusline for compact admission.
tags: [sse, compact, admission, slash, statusline]
timestamp: 2026-10-29
---
# Contract

- `/sse-gate on`, `/sse-gate off`, `/sse-gate status` and no-argument status are handled locally by the routed Claude API shortcut, never by the upstream model. The command asset is installed through the existing ownership-aware installer.
- `sse_gate_enabled` is a workspace runtime config preference, default on. LLM options exposes **SSE compact gate** as an Enter toggle. Changes apply to subsequent admission checks without restarting.
- Off bypasses only these known compact-admission reasons: `context_recovery`, `compaction_checkpoint_pending`, `compaction_shrink_pending`, `compaction_retry_wait`. Identity, native handoff, unreadable config/state and unknown safety reasons still hold. Delivery/inflight ownership, uncertain-write guards, queue records, cursors and compaction evidence are never cleared or rewritten as successful.
- RuntimeCompactionBinding remains the common decision owner for terminal/direct-socket/Codex consumers. Evidence observation still runs while the preference is off; on re-enables an existing unresolved hold.
- Statusline always shows `SSE gate on/off`. Effective state is `held`, `bypassed`, `open`, or `unknown`, including the reason when known. The state snapshot is diagnostic only; failure to write it cannot release admission. Missing or older-than-15-second evidence is `unknown`, never asserted open. Idle native clients can retain that stale/unknown display until their next status refresh; mode remains visible.
- Direct-native sessions which bypass the router do not gain a router-local slash handler. This follows the existing routed command model; no native hooks/settings were changed.

# Verification

See [verification](verification.md) and [focused results](evidence/focused-tests.json).
