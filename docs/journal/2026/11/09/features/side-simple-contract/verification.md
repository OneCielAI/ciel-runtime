---
type: verification
title: Owned real console - six Walkie events answered or queued while main stays busy with a 273k context
description: Owned launch of the working-tree snapshot with an owned Walkie MCP fixture. Main read 16 source files and stayed in a foreground sleep; status, info (code read), new request, restated request, reorder and P1 events were each committed and answered by one host send 10-17 s after receipt, and a redelivered event produced nothing new. Two earlier runs found three defects that are fixed here.
tags: [side-intake, sse, work-mode, queue, walkie, verification, real-console]
timestamp: 2026-11-09
---

# Probe

`.local/owned_side_contract_probe.py` is not committed. It is an owned, isolated launch:

| Part | Setup |
| --- | --- |
| Isolation | Working-tree snapshot `.local/probe-side-contract`; new workspace holding a copy of its `ciel_runtime_support`. |
| Config | Copied, with the receiver and remote sections removed. |
| Port and environment | Free router port. `CIEL_RUNTIME_*`, `CLAUDE*`, `ANTHROPIC*`, `CODEX*` and `OPENAI*` stripped. |
| Main model | `_Walkie_Opus55-Sol61-Fast`, the model the live Walkie sessions run. |
| Side settings | Model `_Walkie_Haiku_Terra` (Prody's side model), `context_mode full`, operator Walkie preset `{source: urn:owned:walkie, receiver_id: default, preset: walkie, server: walkie}`. |
| Source | A loopback SSE source sends Walkie-shaped CloudEvents (`type com.ciel.walkie.support.message`, `data.payload {}`, `data.text ""`). Only `conversation` and `message_id` name the message. |
| Walkie fixture | An owned HTTP MCP server named `walkie`. It holds the messages, answers `walkie_read_messages` in the real result shape (`{"messages":[{"id",…,"text"}]}`) and records every `walkie_send_message`. |

Main ran its task from a file: read 16 source files, then run `py -3 -c "import time; time.sleep(580)"` in the foreground twice. Events were sent one at a time after main's Bash started. The board was seeded with:
- the active goal n1 `결제 모듈 리팩터링` (P2);
- three queued P3 goals: n4 `문서 번역 업데이트`, n5 `CI 캐시 정리` and n6 `로그 보존 정책 변경`.

# Valid run `owned-side-contract-20261108-224432`

- **Main.** Bash from 04:45:10Z. Context 272,967 tokens when events began and 294,221 at the end. `ALL DONE` was never reached, so every event was committed while main was busy (`main_finished_at_commit` false for all six).
- **Side context.** The side context was main's full conversation: 6 messages and all 17 tool results, 1.14 MB (`side-contexts/*/latest.json` and each actor projection).

| Event | Kind → action | Received → claim | Claim → first response | Claim → commit | Received → send recorded | Model requests | Input / cache read (first request) | Tools |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 9001 status | status → answer | 0.2 s | 11.0 s | 11.7 s | 12.6 s | 1 | 104,688 / 94,720 | none |
| 9002 info | info → answer | 2.1 s | 6.3 s | 14.3 s | 17.2 s | 2 | 115,214 / 94,720 | `work_side_code_search` |
| 9003 request | request → create n7 | 2.5 s | 8.3 s | 11.7 s | 14.9 s | 1 | 115,212 / 94,720 | none |
| 9004 restated | request → update n7 | 2.0 s | 5.7 s | 9.0 s | 11.7 s | 1 | 115,341 / 94,720 | none |
| 9005 reorder | request → prioritize n6 | 0.9 s | 6.9 s | 8.7 s | 10.2 s | 1 | 115,383 / 94,720 | none |
| 9006 P1 | request → create n8 P1 | 2.6 s | 9.2 s | 10.4 s | 13.6 s | 1 | 115,441 / 94,720 | none |

Medians: claim → first response 7.6 s, claim → commit 11.1 s, received → send recorded 13.1 s. Every actor got `source_message` from the host prefetch (6 fixture reads); none called a Walkie read tool.

Messages the fixture recorded (`walkie.json`; [rendering](evidence/owned-run/10-walkie-thread-rendered.png)):

| Reply to | Text the host sent |
| --- | --- |
| 9001 | `진행 중: 결제 모듈 리팩터링(현재 실행 단계)…` / `대기열: 문서 번역 업데이트, CI 캐시 정리, 로그 보존 정책 변경.` + `큐에 등록하지 않았습니다.` |
| 9002 | `` `MAX_MATCHES` 상수이며 값은 `80`입니다. `ciel_runtime_support/side_code_read.py:15`… `` + `큐에 등록하지 않았습니다.` |
| 9003 | `ACK — 로그인 화면의 비밀번호 입력란에 보기/숨기기 토글 버튼을 추가하고, 모바일 브라우저에서도 정상 동작하도록…` + `n7 등록 · P3 · 대기 4번째 (앞 3건).` |
| 9004 | `ACK — … 데스크톱 Safari에서도 정상 동작하는지 반드시 확인하도록 기존 요청에 추가합니다.` + `n7 갱신 · P3 · 대기 4번째 (앞 3건).` |
| 9005 | `ACK — '로그 보존 정책 변경' 작업을 대기열에서 두 번째로 올려 달라는 요청입니다.` + `n6 갱신 · P3 · 대기 2번째 (앞 1건).` |
| 9006 | `ACK — 운영 결제 API가 현재 500 오류를 내는 문제를 최우선으로 조사하고 복구해 달라는 요청으로 등록했습니다.` + `n8 등록 · P1 · 대기 1번째 (앞 0건).` |

Totals: 6 sends, 0 repeated `client_id`. The final queue was n8 (P1), n4, n6, n5, n7. The active goal n1 never moved.

**Redelivery.** The 9003 CloudEvent was sent again with a new transport id. Nothing new was produced:

| | Count |
| --- | --- |
| `runtime-inputs.jsonl` rows for `walkie-9003` | 1 |
| Receipts | 1 |
| Fixture sends before → after | 6 → 6 |
| Sends replying to 9003 | 1 |

**Main transcript.**
- It never contains the CloudEvent type.
- The only side-derived text is the existing P1 system notice `Side P1 queued n8 · 운영 결제 API 500 오류 긴급 대응` and the handoff prompts for the reorder and the P1.

**Screenshots** ([main busy](evidence/owned-run/02-main-busy.png), [info answered](evidence/owned-run/04-info.png), [request](evidence/owned-run/05-request.png), [reorder](evidence/owned-run/07-reorder.png), [P1](evidence/owned-run/08-p1.png), [redelivery](evidence/owned-run/09-redelivery.png)).
- They show main inside the foreground sleep and the side pane with every receipt `submitted` and `Parent context: full`.
- Rows holding local user paths and the window title are blacked out. The matching `.txt` screen dumps carry `[redacted]` on the same rows.

# Defects found by the earlier runs (fixed above)

**Run `221236`: main's typed prompt lost its beginning, so main was idle during the events.**
- The Walkie thread still exposed three defects.
- The probe crashed at its last keystroke: attaching to a second console detached the first.

| Event | Defect | Evidence | Fix |
| --- | --- | --- | --- |
| 9002 info | The actor's `work_side_code_search` was refused with `conversation identity is unavailable`, and the reply asked the person for the file. | Debug log: `ciel-work@inline tool.call skipped: re-entry (the plugin's own code raised it; origin ciel-work)`. The engine does not run the plugin's own hook for agents that plugin spawned, so `sideTool` never added a token or identity. | `dispatch_work_tool` serves `work_side_code_read`/`search` before the identity check, bounded to the launch workspace, only while side intake is enabled. `sideTool` passes them through natively. On the pre-fix snapshot the same native call returns the identity error; the fixed router returns the match. |
| 9006 P1 | The production outage was merged into the active `결제 모듈 리팩터링` item (update n1 → P1). | Receipt `action update, item_id n1`. | The PROMPT now updates only an item with the same requested outcome. A different problem, failure, incident or feature is a new item even in the same area, and an urgent new problem is a create at P1. |
| 9003 request | The recap dropped "모바일 브라우저에서도 동작". | Reply text. | The PROMPT requires every concrete requirement, constraint, target, environment and deadline in the recap. |

**Run `223242`.** Every side reply was already correct, including `MAX_MATCHES` = 80 and P1 as new n8. But main, on the copied default model ASTRA, said it had no Read tool, declined the foreground wait and ended its turn, so main was idle. The router's captured request for the valid run lists `Read` among main's 292 tools; why the model said otherwise was not investigated.

**Pane.** The side pane's detail showed `external ACK: not_sent` above `Delivery: sent once by the host outbox` for the same receipt. `external_ack` is set once at intake and never updated, so the line was removed. A pane test now asserts that it is absent. The screenshots of run `224432` were taken before this removal and still show the old line.

# Comparison with the Prody baseline (same side model)

| | Prody, old contract | Owned run, new contract |
| --- | --- | --- |
| Claim → first model response | median 49 s | median 7.6 s |
| Claim → reply | median 81 s (reply tool call) | median 11.1 s (commit), 13.1 s received → host send recorded |
| Side input per request | median 332k tokens | 104k–115k tokens |
| Cache read on an actor's first request | 7,680 | 94,720 |

The two are not equal conditions:
- Prody's main conversation is longer, so its side input was about three times larger.
- The cache numbers come from different builds. Whether the new parent-prefix `cache_control` mark causes the higher first-request cache read has not been isolated by an A/B run.

# Checks

- Focused side/Work tests (244) pass, plus `test_work_mod_write_gate` (45) after the pane change.
- Four CI groups ran before the last fixes: unit 3459, channel 607, runtime 458, router 1545.
- ruff 0.15.22: all checks passed. `claude plugin validate`: passed.
- No live router or session was touched. Live routers 9524 (pid 41224) and 9505 (pid 41824) were unchanged before and after every run.
