---
type: Fix
title: Model aware compact safeguard
description: Project an explicit compact token window for routed CielAiRouter and reserve a safe native compact above90percent measured context usage.
tags: [cielairouter, compaction, model, environment, safeguard]
timestamp: 2026-10-23
---

# Context

Emily screenshot shows context857571/872000 and a local context-limit notice,
followed by manual /compact. Another screenshot shows the environment window
outranks /autocompact settings. Actual Emily process environment/version remain
unavailable because read-only remote exec failed. The screenshot is not proof
of an incorrect numeric value or of automatic compaction being enabled.

# Startup environment

Existing launch already refreshes current model catalog via
ensure_model_cache_for_launch -> ModelCacheLifecycleService.ensure_for_launch ->
launch_profile/reapply_launch_catalog_profile before env_vars projection.
Reuse this path, do not add another model metadata parser.

For routed CielAiRouter only, ClaudeLimitPolicy now emits the derived early
trigger as CLAUDE_CODE_AUTO_COMPACT_WINDOW tokens and omits percentage override.
CLAUDE_CODE_MAX_CONTEXT_TOKENS remains actual selected-model capacity.
Example fixture context872000, guardedbudget716750 gives window715040 and noPCT.
A full environment fixture with different output reserve yields837120; values
are calculated from its actual model/output profile, not a fixed hardcode.
Explicit windows retain existing capacity/budget caps. Other providers/direct
and nativeAnthropic behavior are unchanged. Unknown explicit --model overrides
still clear stale selected-model limits; no unverified profile is invented.

# Runtime90percent safety

- Restricted to local Claude+CielAiRouter, defaulton, provider setting
  compact_safeguard. Existing recovery menu exposes an independent toggle.
- Every2seconds reads bounded2MiB exact-session transcript tail. Requires
  fresh timestamp at/after launch identity, matching upstream/runtime alias,
  complete native assistant record and all3 nonnegative integer input/cache
  counters. Sidechains/wrongmodel/stale/missing fields give no trigger.
- Denominator is full current model context capacity, not reserved input budget.
  Exactly90percent does nothing; strictly above90percent queues automatically.
- Existing single-slot compact repository, manual priority and safe tool/turn
  dispatch boundaries are reused. Reservation itself does not pause external
  input. Actual dispatch begins normal compaction admission gating.
- Samples are deduplicated across repeated polling/process handoff; new native
  checkpoint, usage drop or model change invalidates pending reservation.
  The remembered sample list is bounded128.
- Only confirmed automatic safeguard dispatch persists continuation authority.
  A manual-labeled native checkpoint may use that exact matching scope/time
  intent to continue already-observed unfinished work through existing safety
  checks. Uncertain/failed/manual commands do not grant continuation.

# Verification

[Final](evidence/final-tests.txt):509 exact-file tests OK with42 existing retired
architecture skips. Ruff entire tree and whitespace checks passed.

[Actual child environment](evidence/child-env.txt): selected model context872000,
auto compact token window837120, percentage override absent.

[Safe dispatch](evidence/dispatch-tests.txt): measured19000/20000 queues one
request; active tool and active turn defer without output or admission pause;
then actual OSpipe receives exactly /compact+Enter once, confirmed dispatch
starts admission gating. Duplicate poll sends nothing.

Tests cover boundary90/90plus, aliases/cache counters, unknown/stale/sidechain,
manual priority, checkpoint/model supersession, settings persistence/menu,
confirmed dispatch versus uncertain/manual continuation, native/direct launch
preservation. No real provider call or live session restart was performed.

# Limitations

This is a fallback at a safe native turn boundary, not a forced interrupt of
active tools or a continually running turn. The native client's configured early
window remains the primary trigger. Missing fresh matching usage evidence fails
closed; historical resumed records are diagnostic-only. The bounded tail can
miss a very large record. No live Emily recovery is claimed.

# Citations

- https://code.claude.com/docs/en/model-config#set-the-auto-compact-window
- https://code.claude.com/docs/en/env-vars
