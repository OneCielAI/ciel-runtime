---
type: Verification
title: Native Work fork evidence verification
description: Evidence that parallel Work means independent native Claude forks, not lease reservations or main-session work.
timestamp: 2026-11-10T23:45:00Z
tags: [work, parallel, native-fork, web, terminal, context-isolation, verification]
---

# Definition

A Work item is shown as **processing now** only when the packaged Claude host has read a native `AgentInfo` row for that Work lease, with status `pending` or `running`, within the freshness window. A reserved lease, a held slot, a host id, or lease state `running` is not sufficient evidence.

The main session is the coordinator and is not a fork. Side actors are excluded and shown separately. Other sessions contribute only aggregate device capacity; their item and agent metadata is not returned.

# Implemented evidence path

| Layer | Evidence contract |
|---|---|
| Native observation | The host compares `$.agent.list()` to the current session's bound Work leases. It sends only lease id, agent id/name, fixed native status, and a complete-scan flag. |
| Durable store | `work-parallel-native.json` has a separate version from the lease ledger. It stores ids, status, and server timestamps only; it rejects prompt, transcript, result, reasoning, tool output, or worker capability fields. |
| Classification | Fresh `pending`/`running` rows are confirmed. Waiting, reservations, stale/missing rows, validating work, and ended work have separate groups. Missing from a complete scan is uncertainty, not proof of death, and does not release a slot. |
| Main-context isolation | Assignment notes are read by the packaged host and passed directly into the fork prompt. Model-visible parallel status omits `item_note`; observations are not appended to the main transcript. |
| Terminal | `/work-mode parallel` and the observatory show `FORKS`, `DEVICE`, and `SIDE` as separate quantities and name the confirmed Work items and native fork agents. |
| Web | `/ca/work/api/v1/parallel` returns the current session's classified fork cards and aggregate device capacity without raw leases. `parallel.changed` carries no fork content and makes an open Parallel tab refetch. |

# Tests

Focused tests on the final source:

- Native evidence and MCP projection: missing-file compatibility, strict field/status validation, independent CAS, throttled heartbeat, missing-row uncertainty, terminal statuses, session redaction, resume generation replacement, prune semantics, and worker refusal.
- Claude mod harness: native-list filtering, content-free observation, no main append, direct host spawn, terminal grouping, and the exact evidence version carried into observation writes.
- Web UI: confirmed/waiting/reserved/uncertain/validating/ended rendering, aggregate capacity, mobile overflow, and `parallel.changed` refresh.
- HTTP/SSE, OpenAPI and observatory: browser-safe projection, documented evidence/redaction rules, content-free no-id invalidation, and independent FORKS / DEVICE / SIDE display.

The four isolated repository groups are run with every inherited `CIEL_RUNTIME_*` variable removed and an independent temporary config directory per group. Final counts will be recorded after the final-source rerun completes.

# Owned real-model proof

The probe launches an owned Claude Code session, its own router port, temporary profile, temporary git repository and two approved Work items. It starts two native `fork` agents, each writing a unique canary to its own marker file for about 70 seconds. During their overlap, main runs an unrelated 40-second foreground command. It reads back:

1. two distinct native agent ids recorded as running;
2. the current-session `/parallel` API projection naming both Work items;
3. overlapping fork marker windows and the main command's independently stamped interval;
4. each canary in its corresponding fork transcript and neither canary in the main transcript;
5. terminal and web desktop/mobile screenshots while both forks are confirmed.

Attempts are retained as evidence rather than overwritten:

- `run-1-no-repo-root`: dispatch refusal identified that the probe workspace had to be its own exact git root.
- `run-2-trust-prompt-exit`: the Claude trust prompt defaulted to exit; the driver was corrected to select Yes.
- `run-3-status-leak`: two forks ran concurrently, but model-visible status echoed item A's assignment note into main; the status projection was fixed to omit item notes.
- `run-4-no-main-assignment`: two independent fork canaries stayed out of main and both forks were confirmed. It also exposed two probe defects: the screenshot required no bearer on loopback, and an injected Escape interrupted main's foreground command. The probe now records main start/end stamps and no longer sends Escape.
- `run-5-final-source`: final-source acceptance passed. Two distinct native fork ids were confirmed as running for `n2` and `n3`; fork marker windows overlapped for 58.8 s; main's independently stamped 40.0 s foreground interval was wholly inside the two-fork overlap; each confirmed fork transcript contained only its own canary; neither canary appeared in main. The REST API returned two confirmed cards, and desktop plus 390 px Playwright captures showed both cards with no page error.

Key evidence: [result JSON](evidence/run-5-final-source/result.json), [terminal with two native forks](evidence/run-5-final-source/03-two-forks-terminal.png), [`/work-mode parallel`](evidence/run-5-final-source/04-work-mode-parallel.png), [main busy while both forks run](evidence/run-5-final-source/05-main-busy-forks-running.png), [desktop Parallel tab](evidence/run-5-final-source/05-web-parallel.png), and [390 px Parallel tab](evidence/run-5-final-source/05-web-parallel-mobile.png).

# Safety

The probe strips inherited Claude, Anthropic, Codex, OpenAI, and Ciel Runtime variables; restores only the owned copied provider configuration; disables side intake, receivers, remote bridge, remote memory, and self-update; uses a free port and a newly initialized workspace; and terminates only its own process tree. No live user session or router is restarted.
