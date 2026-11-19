---
type: decision
title: 승인 재점검은 승인 요청 메시지만 읽는다
description: 주기 승인 점검이 스레드 전체를 읽지 않고 등록된 승인 요청 메시지 한 건과, 필요할 때 커서 이후 답글만 읽도록 바꾼 결정과 구현.
timestamp: 2026-11-18T17:40:00Z
tags: [work, approval-workflow, walkie, gate-check, decision]
---

# 배경

50f7d11의 주기 승인 점검은 `side-intake.js workflowThread` → `fetchSourceMessage`로 스레드 전체를 읽었다
(페이지당 200, 최대 8페이지). 긴 스레드에서는 `Source thread exceeds page limit`로 실패한다.
Andy에서 사이드 이벤트 134건이 정확히 이 오류로 실패했다(리드 측정).

사용자 결정(2026-11-18): 주기 점검은 관련 업데이트만 읽고, 스레드 전체는 절대 읽지 않는다.

# 구현

| 부분 | 내용 |
|---|---|
| `register.js` "workflow approval read" 구역 | 자체 완결 구역. 사이드 접수의 리더와 코드를 공유하지 않는다. 같은 대화의 요청들을 한 번에 처리한다. `walkie_read_messages(after = 요청 id - 1)` 한 페이지로 그 대화의 요청들을 찾고, 그 페이지에 없는 요청만 따로 읽는다. 행의 id, 대화, 스레드 계보(등록된 스레드의 답글이거나 루트 자신, 스레드가 없으면 루트 메시지)를 확인하고, 맞지 않으면 라우터에 보내지 않는다. 행에 `approvals[]`가 없고 지난 점검 뒤 행이 바뀌었을 때만 기록된 커서 이후 답글을 최대 2페이지 읽고 커서를 옮긴다 |
| `register.js workflowRound` | 한 틱의 모든 요청을 `work_side_workflow_event({check, checks:[…]})` 한 번으로 보낸다. 순서는 라우터 순서(P0 → P1 → 나머지) |
| `work_workflow_approval._request_row` | `source_context: verified_request`. 계보와 대화를 읽기 결과의 필드가 아니라 행 자체에서 판단한다. 행에 `approvals[]`가 없으면 같은 스레드에서 이 메시지에 답한(`reply_to_message_id`) 답글의 `approvals[]`만 인정한다 |
| `work_workflow_tools` | `checks[]` 일괄 처리. 요청별 커서와 행 다이제스트를 보드가 아닌 세션 폴더 `workflow/<root>/approval-check.json`에 기록한다(보드 버전 변화 없음). `approvals_due[].request_read`가 그 값을 돌려준다 |

읽기 횟수: 지난 점검 뒤 바뀌지 않은 요청은 한 번만 읽는다. 행에 `approvals[]`가 있으면 항상 한 번이다.
스레드 길이 때문에 실패하는 경우는 없다. 한 틱에 32건까지 처리하고, 답글은 요청당 최대 2페이지만 읽는다.

# 시험

`tests/test_work_approval_workflow_recheck.py`(7건), `tests/workflow_approval_read_harness.mjs`.

| 시험 | 결과 |
|---|---|
| 답글 2,000개 스레드의 요청: `walkie_read_messages` 1회, 스레드 읽기 0회 | 통과 |
| 같은 대화의 요청 3건: 읽기 1회, 라우터 순서 유지 | 통과 |
| `approvals[]` 없는 행: 커서 이후 답글 2페이지, 커서 이동. 바뀌지 않았으면 1회 | 통과 |
| 다른 스레드·다른 대화·루트로 위조한 행: 모드에서 거부 | 통과 |
| 유실된 승인을 요청 메시지 한 건으로 회수, 커서 기록으로 보드 버전 변화 없음 | 통과 |
| 라우터: 다른 스레드·계보 없음·다른 대화·다른 id·다른 메시지에 답한 답글 → 모두 거부 | 통과 |

모든 시험은 50f7d11에서 실패한다(Python 7건 중 6건 오류, 1건 실패, mod 시험 1건 실패).
E2E는 실제 런처로 다시 통과했다. 승인 재점검의 Walkie 호출은 `walkie_read_messages(after=9102)` 한 번뿐이었고,
`threads.read`는 SSE 요청을 사이드가 접수할 때만 나타났다([evidence/e2e/run/result.json](../evidence/e2e/run/result.json)).

# 확인하지 못한 것

- 실제 Walkie의 `walkie_read_messages`가 스레드 답글(승인 요청 메시지)을 돌려주는지. 리드가 지정한 호출이며, 대역 Walkie에서만 확인했다. 돌려주지 않으면 그 요청은 이번 틱에 점검되지 않고 다음 틱에 다시 읽는다(실패 없음).
- 결정이 답글로 기록되는 경우의 필드(`reply_to_message_id`, 답글의 `approvals[]`). 측정한 적이 없는 형식이다.

# 같은 틱, 같은 배치 (2026-11-18 추가)

사용자 요구: 주기 점검의 한 틱은 막혀 있거나 기다리는 모든 항목을 한 배치로, 각 항목의 최신 상태만 확인한다.

- 승인 재점검은 별도 반복이 아니라 그 틱의 일부다. 라우터가 틱의 `work_side_gate_check` 응답에 `approvals_due`를 싣고, 호스트는 요청 메시지 하나당 한 번 읽어 그 틱의 두 번째 `work_side_gate_check` 호출에 `approvals_read`로 돌려준다. 판정은 라우터의 호스트 코드가 하고(`approval_results`), 사이드 포크나 사이드 이벤트는 만들지 않는다. 별도 `work_side_workflow_event` 호출도 없다.
- `workflowRound`에는 같은 틱의 꼬리(위임 포크 시작, 메인 알림)만 남았다.
- 시험: `test_the_reads_return_with_the_same_ticks_gate_check_and_the_host_decides`(라우터), mod 시험(한 틱에 점검 호출 2번, workflow 호출 0번, 포크 0개, 같은 대화의 요청 3건은 읽기 1회).
- E2E 다시 통과. 승인 재점검의 Walkie 호출은 `walkie_read_messages(after=9102)` 한 번이었고, workflow 이벤트 호출은 0번이었다.
- 두 번째 핫픽스(9cba321, 3caf256) 위로 rebase하고 한 호출로 합쳤다. 틱의 첫 호출(`thread_reads: true`)이 `read_threads`와 `approvals_due`를 함께 돌려주고, 호스트는 스레드마다 한 번(커서 이후), 승인 요청 메시지마다 한 번 읽어 두 번째 호출 하나에 `threads_read`와 `approvals_read`를 같이 싣는다. 라우터는 승인을 먼저 판정하고 같은 호출에서 게이트 이벤트를 만든다. 승인 행과 위임 포크 목록은 틱의 첫 호출에서만 나온다(두 번째 호출이 위임을 다시 점유하지 않도록). 승인 읽기만 있는 두 번째 호출은 판정 후 바로 답한다(`approvals_checked`).
- 요청 메시지 리더는 `side-intake.js`로 옮겼다(`sideApprovalRead`). 핫픽스가 SSE 처리 전의 스레드 선읽기(`fetchSourceMessage`)를 없앴으므로, SSE로 온 승인/거절 이벤트도 같은 리더로 등록된 요청 메시지 한 건을 읽어 판정한다. 라우터가 클레임에 `request_read`를 싣는다. SSE 경로에는 행 다이제스트를 주지 않아 `approvals[]`가 없는 행이면 커서 이후 답글을 읽는다.
- 시험: 라우터 `test_twenty_waiting_items_on_three_threads_and_two_approvals_make_one_tick`(첫 호출: 스레드 3개·게이트 20개·승인 2건, 두 번째 호출: 이벤트 1개, 스레드 읽기 3, 승인 2건 적용, 다음 호출은 아무것도 내주지 않음). mod 시험 "20 waiting items on 3 threads and 2 awaiting approvals"(점검 호출 2번, 스레드 읽기 3번, 요청 읽기 2번(대화 2개), Walkie 호출 합계 5번, workflow 호출 0번, 포크 1개 이하). 두 시험 모두 2b11374(이 변경 전)에서 실패하고 변경 후 통과한다.
