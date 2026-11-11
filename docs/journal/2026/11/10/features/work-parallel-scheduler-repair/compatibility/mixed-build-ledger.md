---
type: Compatibility Contract
title: Mixed-build Work parallel ledger compatibility
description: The device ledger remains readable by live 4eebfdc and 5c91ea8 processes while new lifecycle metadata moves to a separate sidecar.
tags: [work, parallel, compatibility, migration, ledger]
timestamp: 2026-11-10T00:00:00Z
---

# Constraint

Live processes use strict lease-field validation from builds `4eebfdc` and `5c91ea8`. Those validators reject unknown fields. The newer lifecycle timestamp fields `reserved_at`, `bound_at` and `state_changed_at` therefore cannot be persisted in shared `work-parallel.json` while old sessions remain alive.

# Contract

- `work-parallel.json` keeps schema 1, its existing states and the oldest reader's field set.
- Well-formed lifecycle timestamp fields written by the previous beta are normalized away before the next mutation; malformed values still fail closed.
- New assignments, receipts, root approval generations, run timestamps, recovery state, scheduler diagnostics and bounded history live in `work-parallel-coordination.json` under independent CAS.
- Native fork evidence remains in `work-parallel-native.json`.
- No live router or session is restarted for migration.

# Verified reader compatibility

A new reservation serialized by the changed store was loaded and validated by both source validators extracted from `4eebfdc` and `5c91ea8`. Both accepted it. The persisted lease fields were:

`artifact, cleanup_proof, created_at, dispatch_name, evidence, host_id, id, idle_proof, item_id, mode, owner, plan_version, priority, report_hash, rework, role, scope, slot, state, terminal, usage`.

# Repair safety

Legacy reserved/unbound leases cannot become `orphaned` because terminal validation requires a host id. Positive operator-confirmed repair removes that provisional reservation instead. Owners missing a birth identity are always `unknown`, never dead, because PID identity cannot be proven.

# Related concepts

- [Scheduler lifecycle](/architecture/scheduler-lifecycle.md)
- [Focused verification](/verification/focused-tests.md)
