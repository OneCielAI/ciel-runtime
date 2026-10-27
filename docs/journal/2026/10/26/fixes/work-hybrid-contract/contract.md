---
type: Decision
title: Hybrid Work planning and execution contract
description: Work executes approved goals while independent requests queue and plan concurrently without native Plan Mode semantics.
tags: [work, hybrid, planning, execution, context]
timestamp: 2026-10-26T00:00:00Z
---

# Contract

Work is a per-goal hybrid planning/execution board, not native Plan Mode. An approved active goal retains `executing` while independent requests are queued and planned. Each root stores its own phase; changing priority requires an explicit user request and `user_requested=true`.

A `within_scope=true` child is only a refinement inside already approved scope. New scope, title/note changes, and reopening completed work return that goal to planning. `expected_version` rejects stale mutations.

# Context boundary

Automatic prompt context contains a bounded active summary and queue identifiers, not queued subtrees. `work_status(root_id=...)` reads one exact goal on demand. Raw user messages remain in native conversation history; this board is not history isolation or a security boundary.

# Evidence

- Real Claude Code 2.1.310 isolated CLI: [hybrid run result](evidence/work-hybrid-e9a6e66d/result.json).
- Decision screenshots: [active execution](evidence/work-hybrid-e9a6e66d/01-active-executing-shell-allowed.png), [incoming queue](evidence/work-hybrid-e9a6e66d/02-incoming-request-during-active-turn.png), [priority/resume](evidence/work-hybrid-e9a6e66d/03-priority-finished-active-resumed.png).
- Existing native plan boundary: [native plan](evidence/native-plan-api/01-native-plan-and-before-api.png), [idle refresh](evidence/native-plan-api/02-http-idle-refreshed.png).

# Confirmed findings and limits

The initial queued-subtree reset hypothesis did not reproduce on this checkout: new independent nested roots already preserved active execution. The confirmed defect was that every active descendant addition forced planning, including scoped refinements; switching priorities discarded the former active approval. The old prompt also sliced a whole snapshot as JSON, potentially truncating it mid-object.

Focused verification: 136 exact Python Work tests and 53 exact mod tests pass. Plugin validation passes with its pre-existing missing-icon warning. Strict OKF validation passes without issues. Both isolated CLI runs use fresh HOME/config/workspace/ports, strip inherited runtime/provider variables, and use a rejecting egress proxy. Two updater CONNECT attempts (downloads.claude.ai, github.com) were rejected; there was no proof of total OS-level network isolation. Only owned subprocess trees were stopped, and observed live router PID/fingerprint pairs on 9505/9524 stayed unchanged.

The native Plan/API scenario verifies exact host plan-file writes, unrelated repository-write denial, and idle HTTP band refresh. The hybrid scenario verifies a raw request arriving during an executing turn, independent goal queueing, scoped active refinement, denied implicit priority change, explicit CAS priority change, denied unapproved queued write, and resumed approved active write. Native Bash deny remains effective under bypassPermissions. Ask-boundary preservation is covered by focused dispatch/mod regressions, not a new full interactive approval-dialog run. The scripted loopback model fixture proves host/tool behavior, not a live remote model choosing correct scope classification.

Within-scope classification remains a model assertion, not a security proof. Existing shell policy is preserved unchanged. This implementation does not remove raw queued requests or previous tool results from native history. HTTP additions remain conservative scope changes; the new within_scope refinement option is on the model Work tool. No live install, restart, commit or push was performed.
