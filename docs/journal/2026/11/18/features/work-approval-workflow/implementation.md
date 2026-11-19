---
type: implementation
title: Work 승인 워크플로우 구현 기록
description: spec.md 전체(§1-§12)와 §11.1 KOBE 발견 사항의 구현 내용, 파일별 대응, 검증 결과, 확인된 것과 확인하지 못한 것.
timestamp: 2026-11-18T15:40:00Z
tags: [work, approval-workflow, walkie, side-actor, gate-check, kobe, implementation]
---

# 요약

[spec.md](spec.md)의 상태 머신, 제안서, 호스트 승인 검증, 주기 점검, 증거 보고서, QA별 리뷰, 재작업과
에스컬레이션, 측정, 단계별 프롬프트 관리, 제안·리뷰 포크, 모든 화면을 구현했다. 설정
`work_workflow_enabled`(기본 꺼짐)가 꺼져 있으면 워크플로우 기록이 만들어지지 않고, 사이드 결정·
중복 처리·ACK는 지금과 같다.

**기반 커밋:** KOBE 핫픽스 1b42a73(hotfix/kobe-worker) 위에 다시 얹었다. 점검이 메인 작업 중에도 도는 것,
세션 시작의 맨 앞에서 타이머를 켜는 것, P0 → P1 → 나머지 순서, 가벼운 세션 시작, 긴 스레드 로그,
필터된 모델 오류 문구는 핫픽스의 구현을 그대로 쓴다. 이 변경은 그 위에 워크플로우만 더한다
(점검 틱의 `workflowRound`, 라우터 점검 응답의 `approvals_due`·`workflow_delegations`, 순서는 핫픽스의
`effective_priority_for`).

실제 런처와 claude.exe(2.1.327), 대역 업스트림, 대역 Walkie(SSE + MCP)로 SSE 요청부터 완료까지
전체 흐름을 E2E로 확인했다([evidence/e2e/run](evidence/e2e/run/result.json)).

# 스펙 절별 변경

| 스펙 | 파일 | 내용 |
|---|---|---|
| §3 상태 머신, §5 강제 규칙 | `work_workflow.py`(신규), `work_plan_tree.py` | 보드 문서 `workflows[root]` 저장·검증, 전이표와 감사 기록(이전·다음 상태, 행위자, 근거, 시각, 프롬프트 다이제스트). `set_phase(executing)`과 `runnable_leaves`가 공통 게이트 `gate()`를 확인 |
| §5.1 P0 포함 차단 | `work_parallel_mcp.py` | `_p0_runnable_leaves`, `_reserve_assignment`(P0 의도 생성 전), `dispatch_refusal`에 같은 게이트 |
| §4.1 요청 기록 | `side_work_decision.py`, `side_intake_mcp.py`, `work_workflow_tools.py` | 사이드 `create`가 같은 트랜잭션에서 intake → proposal_due 기록(대화·메시지·요청자·원문 다이제스트·복기) |
| §4.2 제안서 | `work_workflow.py`, `work_workflow_tools.py` | 필수 항목 검증, UI 변경 시 benchmarks·mockups 필수, QA·단계 형식과 연결 검증, 버전·다이제스트·확인 코드, 본문은 세션 폴더 `workflow/<root>/proposal-*.json`, `steps[]`를 하위 노드(`ref wf:<root>:S-n`)로 생성. 새 버전은 이전 승인을 무효화 |
| §4.3 승인 요청 | `work_approval_requested` | 메인이 보낸 메시지 ID·대화·스레드·승인자·제안서 버전 등록. UI 변경이면 `mockups_attached`가 모든 목업을 나열해야 함 |
| §4.4 승인 판정 | `work_workflow_approval.py`(신규) | 6가지 조건을 호스트 코드로 확인. 승인자는 `payload.approver_id`만 사용(최상위 `sender_id` 아님). 같은 이벤트·같은 승인은 한 번만 반영 |
| §4.4.1 주기 점검 | `work_mcp_tools.py`, `register.js`, `side-intake.js` | 핫픽스의 일괄 점검 틱(9cba321) 안에서 처리한다. 틱의 첫 `work_side_gate_check` 호출이 `read_threads`와 함께 `approvals_due`를 핫픽스의 `effective_priority_for` 순서(P0 → P1 → 나머지)로 돌려준다. 호스트는 등록된 승인 요청 메시지 한 건만 읽고(스레드 전체는 읽지 않음, [결정](decisions/approval-recheck-request-only.md)), 스레드 읽기와 함께 그 틱의 두 번째 호출 하나에 실어 보낸다. 판정은 SSE와 같은 호스트 검증이다. SSE 승인 이벤트도 같은 리더(`sideApprovalRead`)로 요청 메시지를 읽는다 |
| §4.5 실행 지시 | `work_workflow_tools.attach_packet`, `register.js parallelAssignment` | 포크 배정에 스펙, 해당 단계, 연결 QA, 금지 사항, 보고 형식, 재작업 지시(재작업일 때), 에스컬레이션 모델 |
| §4.6 결과 보고서 | `work_workflow_parallel.report_check/report_record`, `work_parallel_mcp._worker_event` | 연결 QA 누락, 증거 파일 없음, 해시 불일치, 작업 공간 밖 경로를 거부. 증거를 세션 폴더로 복사하고 보고서 본문 보관. 조정 영수증은 기존 형식 유지(보고서 다이제스트를 `evidence_digest`로) |
| §4.7 메인 리뷰 | `work_workflow_parallel.review_apply`, `work_parallel_mcp._main_review` | QA별 `qa_verdicts` 필수, approve는 모두 pass + `evidence_checked=true`, rework는 `{failed_qa, observed, fix, evidence_required}` |
| §4.8 재작업·에스컬레이션·완료 | `work_workflow.py`, `work_rework_grants.py`(신규), `work_parallel.py` | 한도 초과 시 `escalated`, `escalate_model`(다음 재작업 1회 허용, 모델 지정) / `escalate_main` / `cancel`. 추가 재작업 허용은 원장 행이 아닌 별도 파일에 저장해 이전 빌드가 원장을 계속 읽을 수 있게 함. 모든 단계 승인 + 남은 리프 없음이면 `completed`, `final_report: due` |
| §4.9 답변만 필요한 요청 | `side_work_decision.py` | `ask_main: true`인 answer가 메인 handoff(`kind: question`)를 만들고 메인 답은 기존 handoff 경로로 돌아감 |
| §6 메인 할 일 | `work_workflow.todo`, `work_workflow_tools.status/main_notice`, `register.js workflowNotice` | P0 → 우선순위 → 도착 순. 한 건만, 라우터 claim으로 한 번. 유휴면 깨움, 작업 중이면 행을 붙이고 유휴가 되면 한 번 깨움. 깨움 프롬프트에는 호스트 문장만(제목·요청자 메모 없음). 리뷰는 원장이 리뷰를 게시한 뒤에만 제시 |
| §6.1 제안·리뷰 포크 | `work_workflow_tools.delegate/delegations_due/delegated_event`, `register.js workflowDelegate` | 메인 모델로 실행(모델 지정 없음), 구현 슬롯과 별개, 리뷰 포크는 구현 포크와 다름, 결과는 호스트가 제출하고 작성 주체 기록. 대기 건수·대기 시간 기준 자동 위임 설정 |
| §7 저장 구조 | 보드 `workflows`, 세션 폴더 `workflow/<root>/` | 제안서·보고서·리뷰 본문(다이제스트 주소), `evidence/<lease>/` 증거 사본 |
| §8 인터페이스 | `work_mode_launch.WORK_TOOL_NAMES`, `register.js WORK`, `HOST_ONLY_SIDE_TOOLS` | `work_proposal_submit`, `work_approval_requested`, `work_proposal_status`, `work_workflow_resolve`, `work_workflow_delegate`, 호스트 전용 `work_side_workflow_event`(HOST_ONLY_SIDE_TOOLS·WORK_TOOL_NAMES·테스트 HOST_REPLIES 모두 등록). 포크 게이트는 `work_proposal_status`만 추가 허용 |
| §9 화면 | `work_workflow_http.py`(신규), `work_http_controller.py`, `work_ctl.py`, `work_workflow_menu.py`(신규), `work_settings_menu.py`, `work_live_settings.py`, `work_web_ui.py`, `register.js` | REST `GET /ca/work/api/v1/workflow`·`/workflow/prompts`·`/workflow/metrics`, 명령 `workflow-resolve`·`workflow-delegate`·`workflow-prompt-set`·`workflow-prompt-reset`. ctl `work workflow|workflow-prompts|workflow-metrics|workflow-resolve|workflow-delegate|prompt-set|prompt-reset`. 메뉴 행 2개(현황·조작, 프롬프트). 웹 Workflow 탭. `/work-mode workflow`, Work 패널 한 줄, `/work-mode parallel` 줄. `priority_diagnosis`에 `awaiting_proposal`·`awaiting_approval`·`escalated` |
| §9.1 프롬프트 관리 | `work_workflow_prompts.py`(신규) | 11개 단계 프롬프트 기본값·현재값·지문·이력, 지문 비교 갱신(충돌 409 `prompt_conflict`), 되돌리기. 다음 요청부터 적용, 전이 기록에 다이제스트 |
| §10 품질 측정 | `work_workflow.metrics` | 포크 모델별 단계 수, 1회 통과율, 평균 재작업, 최종 통과율, 실패 QA, 토큰·시간(포크가 보고한 값) |
| §11.1 P1 | `side_intake_processing._source_holder` | 워크플로우가 켜져 있으면 `message.approved`·`message.rejected`는 메시지가 아니라 이벤트로 구분 |
| §11.1 P2 | `side_work_decision.py` (핫픽스 9cba321) | 승인되어 실행 중인 goal의 사이드 메모는 `progress`에 저장(승인 범위 지문 밖). 계획 중 goal은 `note`. rebase 때 핫픽스 구현을 그대로 썼다 |
| §11.1 P3 | `side_ack_outbox.py`, `work_workflow_tools._settle_receipt` | 등록된 승인 요청의 승인·거절 이벤트에는 ACK·복기·최종 답변 없음. 반영된 승인만 시작 안내 1회 |

`ciel_runtime.py`는 바꾸지 않았다(4980줄).

# 기존 시험을 바꾼 곳

`work.test.ts`의 HOST_REPLIES에 `work_proposal_status`, `work_side_workflow_event`를 추가했다(규칙상 필수).
점검·시작 처리 관련 기존 시험과 하네스는 핫픽스 1b42a73의 것을 그대로 쓴다.

# 검증

| 항목 | 결과 |
|---|---|
| 새 시험 | Python 4개 파일 42건, node 하네스 1개, mod 시험 1개 파일 6건. 720eeb6에서 Python은 import 실패, mod 시험 6건 실패 |
| ruff | 통과 |
| `claude plugin test ciel-work` (1b42a73 위) | 111 pass, 0 fail |
| register.js·side-intake.js·`.mjs` 관련 Python 시험(실제 백엔드 제외) | 494건 통과 |
| 실제 백엔드 시험 | [evidence/tests/results.md](evidence/tests/results.md) |
| Linux Python 3.10 3개 샤드 | 1b42a73 4,974건 → 이 변경 5,016건, 모두 통과 |
| 실제 E2E | 통과(1b42a73 위에서 다시 실행) |

## E2E

실제 런처(`ciel_runtime.py cli --ca-runtime claude`)와 claude.exe 2.1.327, 대역 업스트림(각본 모델),
대역 Walkie(SSE 스트림 + MCP `walkie_read_messages`·`walkie_call_tool`·`walkie_send_message`).
스크래치는 `D:\Temp` 아래, 끝난 뒤 프로세스 0개, 스크래치 삭제.

| 단계 | 결과 |
|---|---|
| SSE 요청 → ACK·복기·등록 답변 → goal n1 `proposal_due` | 통과, 포크 0개 |
| 메인 제안서(UI 변경, 목업 M-1, 벤치마킹) → Walkie 승인 버튼 → 등록 `awaiting_approval` | 통과, 포크 0개 |
| 요청자 승인, `message.approved` SSE는 일부러 보내지 않음 → 주기 점검이 스레드를 읽어 반영 | 통과, 기록 `approval.via = check`, 29초 |
| 포크 실행 → 증거 보고서(포크가 받은 배정에 스펙·단계·QA) → 메인 rework | 통과 |
| 재작업 포크(재작업 지시 포함) → 보고 → 메인 approve → `completed` | 통과, 전이 기록 `… awaiting_review → rework → awaiting_review → executing → completed`, `final_report: due` |

[스크린샷](evidence/e2e/run/shots/04-completed.png), [result.json](evidence/e2e/run/result.json),
[tool-calls.json](evidence/e2e/run/tool-calls.json). 스크린샷의 작업 폴더 줄은 가렸고, 도구 기록의
`worker_token` 값(폐기된 E2E 리스의 능력값)은 바꿔 적었다. `unknown conversation lease` 한 건은
대역 모델이 이전 알림을 보고 `lease_id: null`로 부른 것이다(각본의 한계). 다음 호출에서 올바른 리스로 approve했다.

E2E에서 고친 결함:

1. 메인이 바쁠 때 붙인 알림 뒤에 메인을 다시 깨우지 않음 → 유휴가 되면 한 번 깨움.
2. 이번 claude.exe에서 붙인 행이 다음 요청에 보이지 않음(관측). 깨움 프롬프트가 호스트 문장으로 된 할 일 자체를 담도록 바꿈.
3. 포크 결과가 원장에 게시되기 전에 메인에게 리뷰를 알려 `work_parallel_item_review`가 거부됨 → 게시된 뒤에만 제시.

# 확인한 것과 확인하지 못한 것

확인한 것:
- 위 시험과 E2E 결과.
- 대역 Walkie의 승인 행 형식(`approvals[]`의 `user_id, decision, superseded_by, withdrawn_at`, `payload.approver_id`)은 기술 리뷰의 측정값을 따랐다.

확인하지 못한 것:
- 실제 Walkie에서 승인 요청 메시지 행이 `approvers`를 어떤 필드로 돌려주는지. 코드는 행에 `approvers`가 있으면 등록된 승인자와 교집합을 쓰고, 없으면 등록된 승인자만 쓴다.
- 실제 Walkie의 `walkie_send_message`가 돌려주는 메시지 ID 필드. 메인이 등록할 때 직접 넣는다.
- 제안·리뷰 포크의 실제 실행(claude.exe에서 메인 모델로 뜨고 답을 호스트가 제출하는 과정)은 E2E에 넣지 못했다. 단위·mod 시험으로 요청 형식, 위임 기록, 리뷰 포크 제약만 확인했다.
- 포크가 보고하는 토큰·시간은 포크가 `usage`로 보낼 때만 기록된다(E2E에서는 0).
