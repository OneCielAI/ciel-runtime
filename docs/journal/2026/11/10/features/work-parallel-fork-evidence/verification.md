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

The four isolated repository groups were run with every inherited `CIEL_RUNTIME_*` variable removed and an independent temporary config directory per group. Final-source results: unit 3,606 (52 skipped), router 1,552, channel 607 (80 skipped), runtime 458 (19 skipped); all passed. The router group was rerun independently after its first concurrent run was stopped before completion.

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

- `run-6-installed-beta`: the same probe against the installed registry snapshot `ciel-runtime-4ade76a…` (beta `0.2.51-beta.20261111-002410.4ade76a`) passed. Two distinct native forks were confirmed running for `n2` and `n3`; fork marker windows overlapped for 55.9 s; main's 40 s foreground command overlapped both forks for 38.3 s; each fork transcript held only its own canary and main held neither; desktop and 390 px Parallel captures showed two confirmed cards with no page error. [Result](evidence/run-6-installed-beta/result.json), [desktop](evidence/run-6-installed-beta/05-web-parallel.png), [terminal](evidence/run-6-installed-beta/03-two-forks-terminal.png).

# Release

- Beta `0.2.51-beta.20261111-002410.4ade76a`: CI and npm publish passed; registry gitHead `4ade76a961559248a2f3a3c21980b2beda985104` and SHA-512 integrity matched; 520 published source files matched the commit; latest `0.2.51` and nightly `0.2.51-nightly.20261106-020333.c00c8b6` were unchanged. The beta tag first landed on the docs-only commit `f9f163f` after overlapping pushes; a manual beta publish of the same head restored it to `4ade76a`.
- Local install: immutable snapshot and npm global package matched on all 574 files; `CIEL_RUNTIME_HOME` points future launches at the snapshot. No live session was restarted.

Key evidence: [result JSON](evidence/run-5-final-source/result.json), [terminal with two native forks](evidence/run-5-final-source/03-two-forks-terminal.png), [`/work-mode parallel`](evidence/run-5-final-source/04-work-mode-parallel.png), [main busy while both forks run](evidence/run-5-final-source/05-main-busy-forks-running.png), [desktop Parallel tab](evidence/run-5-final-source/05-web-parallel.png), and [390 px Parallel tab](evidence/run-5-final-source/05-web-parallel-mobile.png).

# Safety

The probe strips inherited Claude, Anthropic, Codex, OpenAI, and Ciel Runtime variables; restores only the owned copied provider configuration; disables side intake, receivers, remote bridge, remote memory, and self-update; uses a free port and a newly initialized workspace; and terminates only its own process tree. No live user session or router is restarted.
