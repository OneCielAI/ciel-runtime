---
type: Verification
title: Immediate side claim and side-released waits, verified
description: Tests, owned Claude Code probes, a Prody-shaped reproduction and a real-model owned end-to-end run of the side claiming at once and releasing waits for an idle and a busy main.
timestamp: 2026-11-10T17:20:00Z
tags: [side-intake, sse, waits, verification, e2e]
---

# Real-model end-to-end run (owned, isolated)

- **Run setup** ([probe](evidence/owned_side_release_probe.py), [result](evidence/e2e-release/result.json)):
  - the source snapshot of this change in a real console;
  - a real router on a free port, with a copied config in a temp profile;
  - main model `_Walkie_Opus55-Sol61-Fast`, side model `inherit`;
  - a loopback SSE source feeding the production receiver → side inbox → side actor path.
- **Seeded board.** Goal `n1` (P1, executing) with two waits:
  - `n2` waiting on `Daniel approval 118269 via Walkie`;
  - `n3` waiting on `CI run 4312 result`.

| Phase | Main state | received→claim | received→resolved | resolved→main reached | Wait after | Main handoff |
|---|---|---|---|---|---|---|
| A: "Daniel: I approve design 118269…" | idle | **0.766 s** | 14.9 s | 0.606 s, a plugin prompt started main's turn | `n2` cleared | none (0) |
| B: "CI run 4312 finished: all checks passed" | inside a running 75 s foreground command | **0.122 s** | 6.4 s | 0.606 s, appended into the running turn (`isMeta` user row) | `n3` cleared | none (0) |

- **Phase B ordering.** The release was resolved while the 75 s command was still running (`b_resolved_while_busy: true`).
  - The appended row was written at 17:04:44.9Z, between the `Bash` tool use (17:04:31.7Z) and its result (17:05:49.6Z).
  - Main's next answer said that the CI wait had been released.
- **Both side actors** ran as `ciel-work:side-intake` with `release_waits`. Each released node's note records the event that released it.
- **Screenshots:**
  - [04 idle main prompted](evidence/e2e-release/04-main-woken-idle.png): `Prompt from the ciel-work plugin: Ciel side released recorded wait n2 …`, plus the log line `Side released n2 → main (queued)`.
  - [06 main busy](evidence/e2e-release/06-main-busy.png) and [08 release appended while the command runs](evidence/e2e-release/08-main-after-busy-release.png): `Side released n3 → main (appended)`.
  - [09 final](evidence/e2e-release/09-final.png): Work 2/2 complete.
- **Expected in this probe:** "reply not sent (no reply route)". The loopback source has no reply connector; this is the existing review notice, unrelated to this change.
- **Live sessions untouched.** The PIDs of the three live routers and three live `claude.exe` processes were identical before and after (82844/77720, 76496/54988, 5880/25960). The probe killed only its own tree and router port.

# Owned Claude Code probes (2.1.321, loopback model)

- [receipt_ready_probe](evidence/receipt_ready_probe.py) ([run](evidence/receipt-ready-85ed3cbe/result.json)):
  - The wake frame reached `session.receive` (origin `peer`) in 1 ms in all three states: inside a running tool, inside an open stream, and idle.
  - It was consumed, and no model request carried it.
- [delivery_probe](evidence/delivery_probe.py) ([run](evidence/delivery-4f4c90b8/result.json)):
  - A user-role `session.append` reached main's next request inside the tool-result message.
  - An append while idle started no turn.
  - `session.send` to a running background agent returned `isDelivered: true` and arrived in its next request with no new spawn.
- [clock_mcp_probe](evidence/clock_mcp_probe.py) and [spawn_latency_probe](evidence/spawn_latency_probe.py) ruled out clock, MCP and spawn stalls. See the [diagnosis](diagnosis.md).

# Prody-shaped reproduction

[claim_rotation_repro](evidence/claim_rotation_repro.py) used 230 quarantined pending rows plus one fresh event, over 20 trials:

| Code | Claim calls until the fresh event | Per-call time |
|---|---|---|
| before | 1-3 in every trial (8 needed 2, 5 needed 3) | up to 0.66 s |
| after | 2 in the first two trials (legacy rows being indexed), then 1 | 0.03-0.09 s |

A claim payload test with 5 answered handoffs of 20K characters each: before, 110,063 characters in 14 pages; after, under 8,000 characters with no paging.

# Automated tests

New tests:

| Test | What it proves | Fails on the old code |
|---|---|---|
| `test_side_claim_latency.py` | quarantine index; fresh event claimed on the first call | yes |
| `test_side_handoff_flow.py::test_settled_handoffs_do_not_inflate_every_claim` | settled handoffs no longer inflate every claim | yes |
| `test_side_work_decision.py` | release, changed/foreign wait, dropped outside update, child-handoff activation | the activation test fails with the exact ciel-walkie error |
| `test_side_release_notice.py` | running fork messaged, busy main appended, idle main prompted, parked worker named, undeliverable fork falls back to main | — |
| `test_work_mod_write_gate.py` | receipt signal, burst drain, single-flight throttled pane refresh | burst drain fails on the old pump: 1 claim instead of 3 |

Results with `CIEL_RUNTIME_*` unset and a temp profile:

| Group | Tests | Result |
|---|---|---|
| unit | 3568 | OK (50 skipped) |
| router | 1552 | OK |
| channel | 607 | OK (80 skipped) |
| runtime | 458 | OK (19 skipped) |

# Not verified here

- **Fork path with a real model.** The running-fork path was proven at engine level (delivery probe) and in the harness, but not in this end-to-end run. A real `work_parallel` worker waiting on a node was not dispatched.
- **Prody.** Nothing on Prody was changed. Its 231 quarantined events were counted, not reprocessed.
