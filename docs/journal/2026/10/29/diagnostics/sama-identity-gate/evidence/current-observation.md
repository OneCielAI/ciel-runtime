---
type: Diagnostic Observation
title: SAMA current gate observation
description: Current candidate runtime metadata without an established historical incident cause.
tags: [identity, diagnostics, sama]
timestamp: 2026-10-29
---
# Current observation (2026-10-29)

- Candidate local router `8872`: workspace `E:/aap/u22/home/ganti-ai`, account `aapw-a20u22`, package `0.2.51-nightly.20261030-015514.9077265`.
- Its read-only `/ca/tui/status` currently reports `admission_blocked:false`, `state:not_blocked`, and one queued runtime input. This is not proof that it is the user-reported grant session.
- Direct profile, router-log, and workspace reads are denied by ACL.
- Synthetic identity witness: one qualifying published identity is `open`; two qualifying candidates produce `held/native_handoff_pending`. This is fail-closed ambiguity, not incident proof.
- No identity bypass or live restart was performed.
