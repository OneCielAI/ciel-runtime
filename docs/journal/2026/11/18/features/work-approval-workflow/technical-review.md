---
type: review
title: Work 승인 워크플로우 기술 리뷰
description: 현재 코드(3fb838e)와 Walkie 이벤트 표본을 기준으로 재사용할 부분, 선행 수정이 필요한 결함, 위험, 구현 순서를 정리한다.
timestamp: 2026-11-18T14:00:00Z
tags: [work, side-actor, walkie, approval, workflow, technical-review]
---

# 결론

구현 가능하다. 포크 실행, 결과 제출, 메인 리뷰, 재작업의 뼈대는 이미 있다. 새로 필요한 핵심은 **goal별 워크플로우 상태, 메인의 제안서 제출, 호스트가 검증하는 승인**이다.

먼저 고쳐야 할 것이 두 가지 있다.
- 승인 이벤트가 중복으로 버려질 수 있는 구조
- 사이드 액터의 메모 수정이 실행 중인 포크의 보고를 거부시키는 구조

# 현재 코드와의 대응

근거는 커밋 3fb838e의 코드다.

| 요구 | 현재 | 판단 |
|---|---|---|
| 요청 등록, ACK, 복기 | `side_work_decision.py:392`가 `planning` root 생성. ACK·복기 outbox 있음 | 재사용 |
| 메인에게 제안 요청 | handoff는 P1·대기·시한 등 일부만(`:442-446`). P2~P5 생성은 알림 없음 | 새 상태와 할 일 큐 필요 |
| 제안서 저장 | 노드 `note`, `evidence`는 2,000자에서 잘림(`work_plan_tree.py:145-182`) | 별도 저장소 필요 |
| Walkie 승인 요청 | 메인이 Walkie 도구로 직접 보냄. 프롬프트에 승인 버튼(`approvers`), 검수(`approval_kind: 'review'`) 규칙이 있음 | 전송 기능은 새로 안 만듦. 메시지 ID 등록만 추가 |
| 승인 확인 | 제품 코드에 `message.approved`, `approver_id`, `all_approved` 처리가 없음 | 신규 |
| 승인 후 시작 | `_execute`는 활성 goal만 전환(`work_mcp_tools.py:848-858`) | goal 지정 전환 필요 |
| 승인 전 차단 | 일반 경로는 `executing`이어야 실행. P0 경로는 phase를 무시(`work_parallel_mcp.py:258-294`) | 공통 게이트 필요 |
| 포크 결과 제출 | `artifact` 해시와 `evidence` 문자열, `coverage`, `verification` | QA·증거 구조로 확장 |
| 메인 리뷰와 재작업 | `work_parallel_item_review`, `max_rework=2`, 대체 리스 존재 | 재사용, QA별 판정 추가 |
| 우선순위별 포크 모델 | `work_fork_models.py` | 재사용 |

# Walkie 이벤트 표본

Celly 세션의 사이드 포크 기록에서 필드 이름과 건수만 확인했다.

- `message.approved` 17건, `message.rejected` 2건.
- 승인자: `data.payload.approver_id`. 19건 모두 `payload.sender_id`와 같고, 최상위 `data.sender_id`와는 **달랐다**. 최상위 값을 승인자로 쓰면 안 된다.
- 승인 대상: `data.message_id`, `message_context.id`, `payload.message_id`가 19건 모두 일치.
- 판정: `payload.decision`, `all_approved`, `choice`, `note`.
- 스레드 검증: `approvals[]`에 `user_id`, `decision`, `superseded_by`, `withdrawn_at` 등이 있다.
- Walkie 전송 도구 속성에 `approvers`, `approval_kind`, `approval_link`, `choice_request`, `review_request`, `task_confirmation`이 있다.

# 선행 수정

## P1. 승인 이벤트가 중복으로 버려질 수 있음 (코드 확인)

`_source_holder`(`side_intake_processing.py:12-41`)는 메시지 하나에 사이드 액터 하나만 허용하고, 같은 메시지의 이후 이벤트를 `duplicate_source`로 처리한다. 승인 요청 메시지에 다른 이벤트가 먼저 처리되었거나, 두 번째 승인자·거절이 오면 사라진다.

**수정:** `message.approved`, `message.rejected`는 메시지가 아니라 이벤트 ID로 구분한다.

## P2. 사이드 메모가 실행 중인 포크를 무효화함 (코드와 기존 시험으로 확인)

승인 지문에 `note`가 포함된다(`work_parallel_mcp.py:226-237`). 사이드의 `update`와 대기 해제는 `note`를 바꾼다. 기존 시험 `test_assigned_scope_change_invalidates_scoped_assignment`는 `note`가 바뀌면 포크 보고가 `Work approval scope changed`로 거부됨을 고정하고 있다. P0는 예외다.

Celly의 현재 세션 기록에서는 이 오류가 0건이라 실제 발생은 확인되지 않았다.

**수정:** 사이드 관찰 내용은 지문에 들어가지 않는 별도 필드에 저장한다.

## P3. 승인 이벤트에도 ACK·복기·최종 답변이 나감

외부 이벤트마다 세 번 전송하는 구조다(`side_ack_outbox.py:76-82`). 워크플로우 이벤트는 이 전송에서 제외하고 시작 안내 한 번만 보낸다.

# 설계 판단

| 주제 | 선택 | 이유 |
|---|---|---|
| 승인 게이트 | 새 상태 | `waiting_on`은 `planning` goal을 시작시키지 못하고, 게이트 점검 사이드가 해제할 수 있다 |
| 승인 판정 | 호스트 코드 | 외부 문장은 프롬프트 주입 경로다. 모델의 판단을 승인 근거로 쓰지 않는다 |
| 제안서 저장 | 보드에는 다이제스트, 본문은 세션 폴더 | 보드 크기와 노드 필드 제한 |
| 단계 생성 | 제안서의 `steps[]`를 하위 노드로 생성 | 기존 리스·진단을 그대로 활용 |
| 차단 위치 | `runnable_leaves`, P0 경로, 예약 시점 공통 함수 | 경로마다 따로 막으면 빠지는 곳이 생긴다 |
| 메인 알림 | 할 일 큐 + 깨우기 | Stop 훅 방식은 사용자가 제거를 결정했다 |

# 위험

| 위험 | 대응 |
|---|---|
| 메인이 병목이 되어 제안서가 밀림 | 대기 건수와 대기 시간 표시, 우선순위 순서로 한 건씩 전달 |
| 메인 컨텍스트가 제안서와 리뷰로 빠르게 참 | 본문은 파일로 저장, 메인에는 한 건씩 전달 |
| 승인과 제안 개정이 겹침 | 승인 판정을 보드 트랜잭션 안에서 버전 비교 |
| 승인 요청을 보냈는데 메시지 ID를 못 받음 | 고정 `client_id`로 재전송, 불확실 상태 표시 |
| 승인 이벤트가 유실됨 | 대기 중인 요청을 주기적으로 스레드에서 다시 읽어 대조 |
| 포크가 가짜 증거를 제출 | 해시만으로는 내용을 보장하지 못하므로 메인이 내용을 확인. 가능한 QA는 호스트나 메인이 다시 실행 |
| 재시작 중 승인 도착 | 입력 보관(이번에 배포한 restart hold)과 멱등 처리 |
| 대기가 풀린 포크의 자동 재개 | 재개 시 워크플로우 상태와 승인 세대도 확인 |

# 사용자 결정 반영 (2026-11-18)

- 승인 요청은 메인이 보낸다. Celly의 프롬프트에 "승인 버튼(`approvers`)을 붙이고, 글로 승인받지 않고 `message.approved`를 받은 뒤 진행한다"는 규칙이 있음을 확인했다.
- P0도 승인을 기다린다. 지금은 P0 goal이 등록 즉시 포크를 받으므로(c23087e), 워크플로우 대상 P0에서는 이 동작을 막아야 한다. 승인 후에는 P0 레인으로 즉시 시작한다.
- 단순 질의를 메인에 바로 전달하는 경로가 필요하다. 지금 사이드 프롬프트는 `advise`를 쓰지 않도록 되어 있어 이 경로를 되살리거나 대체해야 한다.

# 구현 순서

1. 선행 수정 P1~P3
2. 상태 저장소, 전이, 공통 실행 게이트, 조회 화면
3. 메인 제안서 도구와 할 일 큐
4. 승인 요청 전송, 승인·거절 처리, 보드 시작
5. 포크 배정 내용, QA·증거 보고 강제
6. QA별 메인 리뷰, 재작업 지시, 에스컬레이션, 최종 보고
7. 측정, 전체 화면, E2E

# 아직 확인하지 못한 것

- 메인이 승인 요청 메시지 ID를 등록할 때 쓸 전송 결과의 정확한 필드.
- 호스트가 보낸 승인 요청 자체가 `message.created` SSE로 돌아오는지. 돌아온다면 P1에 직접 걸린다.
- `all_approved`가 없는 승인 유형의 존재 여부. 표본 17건에는 모두 있었다.
- 표본은 Celly 한 세션이다.
