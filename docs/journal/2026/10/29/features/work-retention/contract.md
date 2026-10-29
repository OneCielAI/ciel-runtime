---
type: Feature Contract
title: Completed Work automatic retention
description: Default-on backend cleanup deletes Work nodes completed at least24hours ago while preserving unfinished work, explicit focus holds, progress counts and optimistic concurrency.
tags: [work, retention, cleanup, rest, mcp]
timestamp: 2026-10-29
---
# Behavior

- Only completed Work nodes are eligible after86,400seconds. The router runs a workspace-local maintenance service at startup and then every60seconds. When a router is stopped, the next router start performs catch-up. There is no operating-system scheduled task.
- Completion is tracked by `completed_at`, independently from later note/evidence edits. Reopening an item clears its completion time; finishing it again starts a fresh window. Automatically completed ancestors have their own completion timestamps.
- Existing completed records use their valid last-touch timestamp conservatively. Missing/invalid legacy timestamps start a fresh24h window, not immediate deletion. Future timestamps wait rather than expire.
- Only fully completed, fully expired subtrees are removed. Unfinished/reopened/blocked/waiting nodes are not removed. Recent descendants prevent removal of their completed ancestor. Explicit focus nodes, descendants and ancestors remain protected; focus is never silently cleared by retention.
- The parent receives a numeric `expired_done_steps` aggregate. Full task details are deleted, but done/total counts remain stable and unfinished parent state/approval/wait/updated time is not changed. Node IDs are never reused.
- Store maintenance uses the existing cross-process lock, atomic replace and version increment. Notifications invalidate UI/SSE consumers; stale mutation versions conflict. `read_snapshot()` remains read-only.
- Only `work-sessions/<64hex>/work-plan.json` under this workspace is visited. Directory reparse points and symlinks are not followed; malformed records are skipped, not quarantined by the maintenance scan. No project files, downloaded files, transcripts, request logs or evidence folders are deleted by this feature.

# Controls and API

- Menu → LLM options → **Work completed auto-delete (24h)**, default **on**.
- REST `POST /ca/config/llm`: `{"action":"option","key":"work_completed_auto_delete","value":"off"}` or `"on"`.
- REST `GET /ca/work` and MCP `work_status(format=json)` expose `retention.enabled`, `completed_after_seconds`, `sweep_interval_seconds`, `focused_items_preserved`.
- Existing common auth remains unchanged. A user may disable the policy without changing Work mode. The service runs independently of Work-mode instructions to retire completed history.

# Verified

-19new retention tests pass: exact24h boundary, recent/reopened work, legacy/future/unknown timestamps, focus, version/CAS/observer/noop, concurrent cleanup and work update, links, corrupt state, progress and menu persistence.
- Focused combined154tests:153pass,1existing Windows `os.replace` PermissionError in `test_read_snapshot_observes_whole_atomic_documents_during_writes`. That exact test also fails on untouched HEAD support code; baseline output retained. No test was removed or weakened to hide it.
- Native mod71tests:69pass/2existing baseline failures (native plan exemption cleanup and planning-denial toast). No claim all mod tests pass.
- Actual owned production backend HTTP6checks: disabled retains old items; menu/MCP policy exposure; actual periodic worker deletes only old done, retains recent/open and2/3progress; stale CAS409; config persists; owned backend restart retains deletion and counts. No direct/manual sweep was used for that acceptance.
- Actual Claude2.1.312 UI17checks:40leaves/20done fixture;10old done leaves expire from the actual worker while10recent done and20open remain;20/40progress stable; Completed tab, expand/collapse/Inspector/companion native composition pass. Screenshots retained.
- These fixtures use isolated homes/config/free ports, deny egress through an owned proxy sentinel, and terminate only owned descendants. Protected9505/9524 before/after identities match.

# Related Work display correction

This release also includes the source-verified `display_current` correction: compact current row uses the same runnable selection as continuation and shows inherited ancestor waits/blocks. This does not change the continuation or permission contract. It is not present in the prior e3cb9a1 package.

# Release boundary

User explicitly requested local/nightly feature deployment. At this journal write the feature is source-verified, not yet published/installed. Exact release identity and installed proofs will be recorded separately. Unrelated Gemini/Kimi provider changes and TLS evidence are excluded.
