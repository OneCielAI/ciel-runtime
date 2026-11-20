---
type: fix
title: Side complaints are queued and reach main; a P0 root keeps its follow-ups at P0
description: The side actor answered a user's complaint without queueing it or telling main, and recorded a P0 root's follow-up as P1. The host now enforces both.
timestamp: 2026-11-20T13:40:00Z
tags: [work, side-intake, complaint, p0, walkie]
---

# Report

Two user requirements, both from Celly's Walkie threads on 2026-11-20:

1. "사이드액터가 봤을때 불만. 혹은 사용자가 화남 등의 상황에서는 사이드액터가 메인에게 바로 알려야지 사이드액터가 알아서 처리하고 큐에도 등록도안하면안댐"
2. "와키의 원본 루트 아이템이 p0이면 사용자의 대화도 p0로. 넣어줘야함"

Evidence, read from a read-only copy of Celly's side receipts:
- Event 153677 (the user complained that a reply came without an approval button):
  - Committed decision: `{'action': 'answer', 'kind': 'info', 'relay': None}`.
  - The reply ended with "큐에 등록하지 않았습니다."
  - No Work item was written, and main got no notice.
- Event 153687 (a follow-up in the same thread):
  - The event carries `thread_task {priority: 1, emergency: true}`. HEAD's `source_priority` reads that as P0.
  - Celly's `work-parallel-p0.json` had an active P0 marker for the goal n1006, set by the user at 12:04Z.
  - The committed decision was `{'action': 'update', 'item_id': 'n1006', 'priority': 'P1'}`.
  - The reply ended with "n1006 갱신 · P1 · 대기 3번째 (앞 2건)."

# Cause

Confirmed in source (3d42d1d):
- `side_work_decision.apply` takes priority only from the model's `decision.priority`:
  - `source_priority` P0 was a prompt hint and was not enforced.
  - An already active P0 marker was not consulted, so the canonical P1 was shown.
- No decision field marks a complaint, so the host could not stop an answer-only commit.
  - `ask_main` reached main only through an in-memory host call after the commit.
  - A result lost before the receipt write was recovered without any notice.

# Change

- **Decision field `complaint`** (`side_work_decision.validate`):
  - The host turns a complaint into `update` when `item_id` is given, and into `create` otherwise.
  - It sets `kind` `request` and `ask_main` true.
  - Applied to a done or missing item, the complaint becomes a new goal; the done item is not reopened.
  - With Work off there is no queue to write to, so the complaint is answered and `ask_main` is set.
- **Durable main notice**:
  - `finish` stores the host-rendered notice (managed prompt `side_complaint_notice`) as a `pending` release notice and lists it in the re-issue index. This happens under the receipt lock, before the processed receipt is published.
  - `side_notice.prepare` grants the `pending` state once, the same at-most-once claim released-wait notices use. Claim recovery after a lost result also renders and stores it.
  - The mod sends the router's text, and an idle main gets the wake `mod_side_ask_main_wake` (managed text): "act on it now".
- **P0 inheritance** (`side_work_decision.apply`):
  - The host's `source_priority` P0 makes a create, update or prioritize P0, whatever the model wrote.
  - A goal whose P0 marker is active stays P0. Its canonical P1 and the P0 marker are not rewritten, and no second activation is made.
  - The outcome line shows `P0`.
- **Prompts**:
  - New managed prompt `side_complaint_and_urgency`. It is added to every side instruction, with or without the approval workflow, and is editable without a restart.
  - The built-in side instruction lists `complaint` among the decision fields. The Python default and the mod `PROMPT` are kept identical.
- **Test fixture**: `tests/side_relay_harness.mjs` now waits for the notice instead of a fixed 20 ms. On 3d42d1d that wait lost the append in 2 of 5 runs.
- Not changed: `source_priority`. Celly's events carry no `data.priority`, so HEAD already read 153687 as P0.

# Verification

See [evidence](/docs/journal/2026/11/20/fixes/side-complaint-p0/evidence/).

Fail-first: the new tests run on a HEAD (3d42d1d) tree copy fail. In `fail-first-mcp.txt`, 6 tests ran with 5 failures and 1 error:
- The 153687 reproduction gives `('P1', 'P1', 'P1')`, where `('P1', 'P0', 'P0')` is expected.
- An existing P0 updated with P3 gives `'P3'`.
- Every complaint decision is refused with "invalid side decision fields".
- The registry prompt is missing.

The mod scenario fails with `KeyError: 'complaint'`. HEAD's wake also called the notice a "released recorded wait".

After the change (commit 03e064d):
- `test_side_intake_mcp.py` 39 tests pass (1 skipped); `test_side_relay_notice.py` 10 pass in 8 consecutive runs.
- Windows, isolated config, affected bundle of 52 test files (side, work gate, approval workflow, mod write gate, side HTTP, P0, priority, workflow, texts, real-backend, restart-terminal, native): 847 tests, 0 failures, 55 skipped ([evidence/bundle-windows.txt](/docs/journal/2026/11/20/fixes/side-complaint-p0/evidence/bundle-windows.txt)).
  - The first run had 1 failure: `test_work_texts` looked for `T('…')` in register.js only, and the new wake text is used from side-intake.js. The check now reads every hook file except the generated one; it passes on the HEAD copy too.
- `claude plugin validate`: passed. `claude plugin test` on ciel-work: 89 pass, 0 fail. ruff: clean. `ciel_runtime.py`: 4980 lines (unchanged).
- Real claude.exe 2.1.329 load of the exact mod bytes (owned scratch profile, stub upstream, stub Walkie; driver from the 11-18 side-actor-failures journal): the mod loaded with no unload or load failure, the side fork got its event, was asked once to correct an answer without decision JSON, and the decision committed with no failure counted ([evidence/real-load/result.json](/docs/journal/2026/11/20/fixes/side-complaint-p0/evidence/real-load/result.json)).
- Linux Docker (Python 3.11.2, Node 22.23.5), same 52-file bundle on an archive of 03e064d: 847 tests, 0 failures, 58 skipped ([evidence/bundle-linux.txt](/docs/journal/2026/11/20/fixes/side-complaint-p0/evidence/bundle-linux.txt)).

# Installed (not yet effective)

The ten changed files were overlaid with hash checks and backups (`*.backup-hotfix8`). Every target held 3d42d1d's bytes before the overlay.
- Local: Andy's `ciel-runtime-hotfix5-20261119-210220` home, the default home and the global npm package.
- Celly: elevated overlay; all ten verified.
- KOBE: package scp plus a hash-checked overlay; all ten verified (the underlying version is c955630).
  - The first KOBE attempt stopped at its first stage-hash check before copying anything. My script's path conversion had produced empty file names. It was rebuilt and rerun.

No process was restarted. Each session runs the change only after its next restart.
