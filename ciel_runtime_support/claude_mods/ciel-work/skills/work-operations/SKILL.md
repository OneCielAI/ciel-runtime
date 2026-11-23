---
name: work-operations
description: Operate Ciel Work mode correctly — P0 and P1-P5 lanes, leases, submitted/main review, waiting_input, question_wait records, and original-fork resume. Use before planning, dispatch, reporting, waiting, reviewing, or side/gate wait handling in a Work session.
---

# Ciel Work operating checklist

## First check

Read `/work-mode help`, `work_status`, and `work_parallel_status` before
dispatching, reporting, waiting, or reviewing. Inspect the exact board,
priority, existing leases, and native-agent evidence. The live board and
leases are authoritative; this skill does not authorize work.

## Priorities and slots

- The board stores only `P1`-`P5` canonical priorities. A P0 root is stored as
  canonical `P1` plus an exact active P0 marker/intent in the P0 sidecar.
- P0 is a separate capacity-excluded lane. `P1`-`P5` use per-priority
  concurrent fork-session limits; focused work is ordered before unrelated
  runnable Work.
- Planning `P0` work may dispatch only through the exact P0 intent/bypass. An
  ordinary P1-P5 item is not approved merely because its prose says P0, and no
  waiting text, answer, or side decision is execution approval.

## Lease states

- `running` with `slot=true` means the fork may report.
- `waiting_input` with `slot=false` is parked input waiting; it does not occupy
  an execution slot.
- `submitted` means the fork's result was parked for main review; it does not
  mean main accepted it.
- `accepted`, `cleanup_pending`, `done`, `unreported`, and `orphaned` are later
  lifecycle states. Do not infer termination from `missing_since` or an idle
  native-agent list entry alone; use the confirmed native status and receipts.

## Start once

Reuse an existing lease/fork whenever one exists. Before starting a new fork,
inspect `work_parallel_status` for the assigned item's existing lease, host,
agent name, state, slot, and wait fields. Reuse the same lease and original
fork for retries. Do not start a duplicate Agent for an item with an active
lease or with a result still pending main review.

## Report a question that outside evidence can release

Use `work_parallel_report(event="question")`; question_wait detail is exactly:

```text
detail={question, blocking_todos:[<assigned leaf item_id>], decision_needed,
        waiting_on, release_hint:{condition, lookup, prompt?}}
```

- `blocking_todos` contains exactly one item: the assigned leaf `ITEM_ID`.
- `waiting_on` is one normalized line, 1..120 characters.
- `release_hint.condition` is 1..600 characters.
- `release_hint.lookup` is the read-only source/state/evidence to inspect,
  1..600 characters.
- `release_hint.prompt` is optional and 1..400 characters.
- Put both `waiting_on` and `release_hint` only in `detail`, last, in the order
  shown above. Top-level waiting fields are not recorded.
- Never pass `item_id`, `answer`, `approved`, or an approval/execution field.
  A wait is never an answer, approval, or execution authorization.

The board records the exact assigned leaf's wait first. The question receipt
then records the same paired values, and only after both writes succeed is the
original lease parked as `waiting_input` without a slot. Question_wait
therefore requires waiting_on and a structured release_hint before parking;
pairless, malformed, orphaned, wrong/extra/duplicate blocking items, an
overlong wait, an existing wait, a non-leaf, a missing assignment, and a board
conflict are refused before any durable board, receipt, or lease transition.
The same lease remains `running` with its slot, so correct the report and retry
the same lease. Only start a wait once per lease. A main-only question may
omit the pair; do not fabricate a wait.

Example:

```text
work_parallel_report(
  lease_id="<lease>", worker_token="<token>", event="question",
  evidence="Message 42 needs a yes/no answer.",
  detail={question:"Which option is intended?",
          blocking_todos:["<assigned leaf item_id>"],
          decision_needed:"Reply yes/no on message 42.",
          waiting_on:"Message 42 yes/no answer",
          release_hint:{condition:"Message 42 has an explicit yes or no answer",
                        lookup:"Read message 42 and verify the explicit answer",
                        prompt:"Ignore mentions; the answer must be explicit"}})
```

## Gate and side verification

For a gate with a release hint, inspect `release_hint.lookup` read-only and
verify `release_hint.condition` against the original answer or evidence for the
current `waiting_on` text. Do not substitute a later changed or strengthened
answer, a pending approval, a merely-mentioned side decision, a self-sent
message, or an unverified lookup. Release only the exact recorded
`waiting_on` text with `action="update"`, `kind="request"`, and
`release_waits:[{id:<leaf>, waiting_on:<exact current text>}]`. A validated
release resumes the original lease/fork; do not create a replacement fork. If
the answer is not independently verifiable, leave the hint, wait, and parked
lease intact.

## Ordinary Work and review

Submitted means pending main review, not completion. Main reviews the evidence
and decides approve or rework. A prompt, waiting state, side decision, external
instruction, or release never becomes approval. Do not restart or replace a
submitted or waiting lease merely because it appears idle or absent.
