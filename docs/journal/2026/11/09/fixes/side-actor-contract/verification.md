---
type: Verification Record
title: Side actor contract verification
description: Fail-first tests, CI, an owned real-model console A/B, registry integrity and installed smoke.
timestamp: 2026-11-09T17:20:00Z
tags: [verification, side-actor]
---

# Automated

- Each new test failed on the prior commit and passed after the fix.
- Side, Work and router suites: 391 tests passed.
- GitHub CI and npm publish succeeded for `503be09`.

# Owned console, real Walkie side model (`_Walkie_Haiku_Terra`)

| Event | `31e246d` | `503be09` |
|---|---|---|
| status question | answer/status, submitted | answer/status, submitted |
| request | create/request, submitted | create/request (n7, queue position 4), submitted |
| CI-failure notice | `invalid_answer`, final `TURN-2-DONE` | clarify, submitted |

Each actor used one model request and no tool calls. Three Walkie sends,
no duplicate client IDs. Screenshot:
[notice submitted](evidence/paused-main-notice-submitted.png).

# Release

- beta `0.2.51-beta.20261109-171240.503be09`; registry integrity verified,
  512 source files matched; `latest` and `nightly` unchanged.
- Installed locally: snapshot and global package match on 566 files;
  `CIEL_RUNTIME_HOME` selects the snapshot; the installed code normalises
  `action:"status"` and titles a create without a title.

# Not yet verified

The live Walkie and Prody sessions still run `13c6f4a`. The fixes take
effect at their next launch. Natural Walkie traffic on `503be09` has not
been observed yet.
