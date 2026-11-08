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

## Beta 8bd9fe2 release and local install

- CI 42441323599 and Publish 42441323714: success. npm `beta` = `0.2.51-beta.20261108-031852.8bd9fe2`;
  `latest` and `nightly` unchanged.
- Registry tarball verified (integrity, gitHead, 509 source files); extracted to a new immutable snapshot;
  `npm install -g` of that tarball; User `CIEL_RUNTIME_HOME` switched to the snapshot. No live session or
  router was restarted.
- [Installed smoke](evidence/installed-beta-smoke-8bd9fe2.json) ([script](evidence/installed-beta-smoke.py)):
  563 matching package files, `claude plugin validate` passes, Client module present, isolated ctl
  settings read/write.

## Real SSE event and side fork (owned console)

[Probe](evidence/real-sse-fork-probe.py): installed snapshot plus the patched mod, isolated copied
config, router port 9611. A local SSE source registered as the router's `default` receiver sends one P1
CloudEvent. It goes through the production receiver, then the side inbox, then the mod's
`ciel-work:side-intake` fork. No prompt goes to the main session.

On 8bd9fe2 itself:

- the pane did not open at all ([screenshot](evidence/real-sse-bug-pane-not-opened.png)); only the old
  `btw_with_mcp` pane opened;
- after the first fix, a finished fork stayed painted
  ([screenshot](evidence/real-sse-bug-stale-fork-cells.png)).

After the three corrections ([result](evidence/real-sse-fork-result.json)), the SSE column moved
through these states:

| Frame | SSE column |
| --- | --- |
| [before event](evidence/real-sse-00-before-event.png) | SSE FLOW 0 · No active side fork |
| [fork active](evidence/real-sse-01-fork-active.png) | SSE FLOW 1 · AI 1 · ACTIVE SSE FORKS 1 · ▶ fork a8cdd466f0da… · model ciel-runtim… · P1 processing |
| [fork ended](evidence/real-sse-02-fork-ended-pending.png) | No active side fork · P1 pending |
| [answered](evidence/real-sse-03-answered.png) | ANS 1 · No active side fork · P1 answered |

The owned process tree and router ended, the copied config was removed, and live routers
9505/9489/9524 stayed at 200.

Focused tests: `tests.test_work_observatory` 7 OK. `claude plugin test`: 22 pass / 77 fail, the same
pre-existing `work.test.ts` baseline.

## Corrected beta 0a881e0 release and local install

- CI 42444984691 and Publish 42444984697: success. npm `beta` =
  `0.2.51-beta.20261108-041205.0a881e0`; `latest` and `nightly` unchanged.
- Registry integrity and gitHead verified (509 source files); extracted to immutable snapshot
  `ciel-runtime-0a881e09382316de5ba635dc9ba66ddda7d784d6`, installed globally from the verified tarball,
  and User `CIEL_RUNTIME_HOME` moved to that snapshot. No running process was restarted.
- [Installed smoke](evidence/installed-beta-smoke-0a881e0.json): 563 package files match the snapshot,
  five isolated CLI/ctl commands pass, and `claude plugin validate` passes. Live routers
  9505/9489/9524 remained HTTP 200.

# Betty .12

Pending beta publish/install and on-target screenshot. The read-only pre-check established u42 currently runs
`0.2.51-beta.20261108-005232.93be24c` from
`C:\ProgramData\aap-win\sandboxes\u42\home\betty-ai\.npm-global\node_modules\@one-ciel-ai\ciel-runtime` and that
its `register.js` exists. The old build is the plain-list implementation.
