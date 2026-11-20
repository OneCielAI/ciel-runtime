---
type: fix
title: A forward-only side decision posts nothing to the source thread
description: Prody asked in thread 154103 for no thread reply when the side actor only forwards a request to Work. The user decided on 2026-11-20 that forward-only decisions post nothing. no_reply is now accepted there, and the default side instruction says so.
timestamp: 2026-11-20T22:50:00Z
tags: [work, side, reply, no_reply, prody, prompt]
---

# Request

Prody (thread 154103, relayed by Andy_Main): on other people's threads, do not post a pure "forwarded to Work" notice reply. User decision 2026-11-20: 전달만인 경우는 무답장 (forward-only means no reply). This changes the 2026-11-19 rule (one message per event, understanding then result) only for forward-only decisions.

# Cause

Confirmed in source (a6fb91a):
- `no_reply` was kept only when `action == 'answer'` and no `ask_main` (`side_work_decision.validate`). A create, update or prioritize decision lost the flag, so the reply outbox always posted the queue outcome.
- An `answer` that named a wait to release kept `no_reply` while the host later dropped the release (only an update releases a wait).

# Change (fc9ada9)

- `side_work_decision.validate`: `no_reply` survives whenever the decision is forward-only — `ask_main` not true, no `release_waits`, `relay` not true, no `time_limit`. One condition covers answers and queue decisions. A complaint gains `ask_main` earlier in the same function, so it is never silent.
- Nothing else changes: the reply outbox already makes a `no_reply` result `not_applicable` and posts nothing; the board still queues and main's review still records the decision.
- Default side instruction (`side-intake.js` PROMPT = `DEFAULT_SIDE_INSTRUCTION`): new rule "전달만 하는 경우의 무답장 (필수 규칙, 사용자 2026-11-20)" after the reply item. Set `no_reply` when a create/update/prioritize only registers or forwards the request and understanding and reply carry nothing more, and never when `ask_main`, `release_waits`, `relay` or `time_limit` is present. That rule is new model-facing text in code, in the existing prompt pair.

# Verification

[fail-first.txt](/docs/journal/2026/11/20/fixes/forward-no-reply/evidence/fail-first.txt).

- Fail-first on a6fb91a: a forward-only create with `no_reply` raised `KeyError: 'no_reply'` (flag dropped); an answer naming a wait kept the flag.
- After the change: the decision still queues, `apply` returns `no_reply`, and the reply `prepare` is `not_applicable` with no `arguments`. The same decision with `ask_main`, and one with `complaint`, keep posting.
- Windows, isolated, ten side test files: 218 tests, 0 failures, 13 skipped ([windows.txt](/docs/journal/2026/11/20/fixes/forward-no-reply/evidence/windows.txt)).
- Linux in the fixed container `ciel-runtime-test-py311` (Python 3.11.18, pytest 9.1.1): the same ten files, 218 tests, 0 failures ([linux.txt](/docs/journal/2026/11/20/fixes/forward-no-reply/evidence/linux.txt)).
- `test_side_instruction_view` confirms the prompt pair is still identical. ruff passes.

# Installed

Hash-checked overlay (hotfix12), three files, no process restarted. Each target held the fc9ada9 base bytes first; the bases are a6fb91a's files (the same bytes b3da3c6 carries).
- Local: Andy home, default home, global npm and Celly — 3 files each, 12 verified ([install-local.txt](/docs/journal/2026/11/20/fixes/forward-no-reply/evidence/install-local.txt)).
- KOBE: 3 files ([install-kobe.txt](/docs/journal/2026/11/20/fixes/forward-no-reply/evidence/install-kobe.txt)).
- Prody and Betty (.12): 3 files each ([install-aap12.txt](/docs/journal/2026/11/20/fixes/forward-no-reply/evidence/install-aap12.txt)), whose first line is the refused first attempt described in the evidence index.
PUBLISH_PLACEHOLDER

# Open

- Whether a live side actor applies the new no_reply rule is not yet observed; it needs a real forward-only event after install.
