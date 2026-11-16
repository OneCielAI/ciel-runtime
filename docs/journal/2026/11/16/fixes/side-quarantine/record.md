---
type: fix-record
title: Side events no longer quarantined for a full item note, swapped action/kind, or an oversized Walkie thread
description: Three causes made the Walkie side pane show "failed" rows whose messages never got a reply; each now commits, with the real failed decisions replayed as evidence.
timestamp: 2026-11-16T03:10:00Z
tags: [side, sse, quarantine, walkie, fix]
---

# Symptom (user, 2026-11-16)

> 최근 리스트에 실패가있는데? / 이렇게 실패하는게 정상이고 아무런 문제가없다는거야?

The Walkie side pane listed failed rows. Each one is a person's message that was quarantined: it is never retried and gets no reply.

# Measured causes

These come from the `side_failure` records in Walkie's side inbox, covering the 12 hours before 02:35Z.

| Count | Recorded reason | Cause |
| --- | --- | --- |
| 23 | `work_side_resolve: side update exceeds note capacity; original preserved` | Every side `update` appends `Side request: …` to the item note. Items that kept receiving follow-ups reached the 2000-character limit, and every later update about them was refused at commit, then quarantined as `invalid_answer`. Examples: n1849 at 1966 chars with 4 side requests, n1847 at 1984 chars with 3. |
| 4 | `work_side_resolve: invalid side decision action` | The model (deepseek-v4.1-flash via `_API_KEYS-SSE-1`) put the classification in `action` and the action in `kind`, as `action:"status"/"info"` with `kind:"answer"` (seen in the fork transcripts). |
| 1 (+5 earlier) | `Source thread exceeds byte limit` / `main context budget` | The Walkie thread (63773) is larger than 384 KiB. After 6 attempts and 12 compaction waits it was quarantined. |

# Fix

- **Note capacity** (`side_work_decision.side_note`):
  - The note text written by main or the plan is never changed.
  - When the new side request does not fit, the oldest side requests are dropped first and counted on one line: `[N earlier side requests trimmed for space; their messages stay in the side inbox]`. The count accumulates across compactions.
  - If the room is still short, the new request is cut to fit and ends with `…`.
  - If main's own note fills the item, the note is left unchanged and the decision still commits.
  - The result carries `note_compacted`.
- **Swapped fields** (`validate`):
  - When each value is valid only in the other field (action ∈ kinds, kind ∈ actions), the two are swapped back. This extends the existing `action == kind` canonicalization.
  - The kind/action consistency check still runs, and unknown values are still refused.
  - The prompt now spells out kind (the classification) and action (what the host does), with an example JSON.
- **Oversized thread** (`side-intake.js fetchSourceMessage`):
  - The thread is read once.
  - If it does not fit, main is still compacted first, as designed, when that can make room.
  - Past that, or past the 384 KiB static limit, the fork gets the root, the target message and the newest replies that fit, with `thread_truncated` and `omitted_replies`. The prompt tells it not to assume the omitted content and to clarify if it needs it.
  - If root plus target alone do not fit, the event still fails as before.

# Verification

- **Replaying the real failed decisions** on a copy of the live Walkie board ([evidence/replay-current.json](evidence/replay-current.json) and [evidence/replay-da29cfa.json](evidence/replay-da29cfa.json)):
  - **Published da29cfa:** 5 of the 6 still fail with the same reasons (2 note capacity, 3 invalid action).
  - **Fixed code:** all 6 commit.
    - n1849: note 1966 → 1561 chars, `trimmed 2`, relay kept.
    - n2018: note 1739 → 1284 chars, `trimmed 2`.
    - The three swapped answers commit as `answer` with kind status/info.
  - **9ff9a7009139 (n1861)** does not reproduce on the current board with either build, because the board changed after it failed.
- **Tests:**
  - `tests/test_side_decision_capacity.py` (8): side_note cases, commit on a full note, release on a full note, swapped fields, unknown values refused.
  - `tests/side_prefetch_harness.mjs` with `test_side_intake_mod.py`: a 300-reply thread over 384 KiB is bounded and read once (pages 0 and 240). Root and target are present, the newest replies are kept in ascending order, the result is ≤ 384 KiB and `omitted_replies` is consistent. A middle target is kept. Root plus target too big still fails.
  - Side suites: Windows 180 passed; Linux Docker 180 passed.
  - `claude plugin test ciel-work`: 103/103. `claude plugin validate`: passed. ruff 0.15.22: clean.
