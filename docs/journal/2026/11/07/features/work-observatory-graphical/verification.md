---
type: verification
title: Graphical Observatory verification
description: Engine, focused, real-console and Betty .12 verification of the corrected Work/SSE dashboard.
tags: [verification, observatory, sse, betty]
timestamp: 2026-11-07T21:00:00-06:00
---

# Local evidence

## Engine

`claude plugin validate` passes (no digest is expected because the plugin names a Client surface module).
`claude plugin test`:

- graphical active SSE processing receipt: pass;
- `/work-mode settings` fallback: pass;
- terminal graphical navigation: pass;
- mobile navigation: pass;
- total 22 pass / 77 fail. A clean HEAD baseline is 21 pass / 77 fail; all 77 are the pre-existing
  `work.test.ts` fixture failures (`clock.after` and dependent assertions), unchanged by this feature.

Focused Python/harness suite: 35 passed. Clean committed export: ruff passes.

## Real console

Owned conhost, isolated copied config, router port 9611, fixed test UUID. No prompt was submitted. The harness seeded
one seven-node Work hierarchy and two redacted SSE receipts in its isolated workspace store. The screenshot proves:

- Work and SSE visible together;
- progress 1/4;
- nested done/running/blocked rows;
- P1/P3 receipts and inspector;
- graphical colors and progress cells.

The owned process tree ended, its copied config was removed, and live router health on 9505/9489/9524 remained 200.

# Betty .12

Pending beta publish/install and on-target screenshot. The read-only pre-check established u42 currently runs
`0.2.51-beta.20261108-005232.93be24c` from
`C:\ProgramData\aap-win\sandboxes\u42\home\betty-ai\.npm-global\node_modules\@one-ciel-ai\ciel-runtime` and that
its `register.js` exists. The old build is the plain-list implementation.
