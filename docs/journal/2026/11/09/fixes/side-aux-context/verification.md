---
type: Verification Record
title: Side auxiliary-context hotfix verification
description: Fail-first tests, full CI, owned A/B, registry integrity, installed smoke, and Prody state are recorded.
timestamp: 2026-11-09T11:36:00Z
tags: [verification, side-actor, sse]
---

# Automated

- Fail-first boundary test: released behavior replaced the parent for
  `auxiliary`, `compaction`, and headerless suggestion requests (3 failures).
- Fixed focused tests: 50 passed.
- Side/router related modules: 384 passed.
- Local CI groups: unit 3462, router 1548, channel 607, runtime 458; all passed.
- GitHub CI run `42607006790`: success.
- Publish run `42607006860`: success.

# Owned runtime A/B

| Build | Status event | Request event | Walkie sends |
|---|---|---|---:|
| released `84cd701` | `invalid_answer`, final `Silence` | `invalid_answer`, final `Stay silent` | 0 |
| fixed `6d0f7ce` | `answer/status`, submitted | `create/request`, submitted | 2 |

The fixed run had no duplicate client ID and no remaining owned router process.
The UI proof is [fixed submitted](evidence/fixed-submitted.png). Machine-readable
results are in [owned A/B summary](evidence/owned-ab.json).

# Release

- beta: `0.2.51-beta.20261109-112329.6d0f7ce`
- registry tarball integrity verified; 512 source files matched commit `6d0f7ce`.
- `latest` and `nightly` remained unchanged.
- immutable snapshot and globally installed package matched all 566 files.
- installed boundary smoke: main/headerless-main accepted; auxiliary,
  compaction, and headerless suggestion rejected.

See [registry verification](evidence/registry-verification.json) and
[installed smoke](evidence/installed-smoke.json).

# Remaining live-session condition

Prody's currently running Claude process was launched before this fix. The new
request-class launch environment cannot enter an already-running process without
a restart. The live process was deliberately not restarted. Its existing stored
parent is still the old suggestion snapshot, so new natural receipts can still
show `invalid_answer` until Prody's next normal relaunch. The persisted Walkie
connector is already ready for that launch.
