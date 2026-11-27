---
type: fix-record
title: 사이드 액터의 Work 보드 직접 쓰기 차단 (n2459 161117 불만 기록 유실)
description: 사이드 액터가 work_node_update로 n2459 progress를 통째로 바꿔 Daniel 161117 불만(두 요청)의 기록이 사라진 사건의 원인과, ciel-work-gate가 사이드 액터의 work_* 쓰기 도구를 기계적으로 거부하도록 한 수정
tags: [side-intake, ciel-work-gate, work-board, n2459, andy, daniel, fail-first]
timestamp: 2026-11-27T16:00:00Z
---

# 사이드 액터의 Work 보드 직접 쓰기 차단

## 사용자 요청

- 이미지 #72(Walkie 161245): "n2459가 더이상 진행이 안되고 유실된 이유 확인.. 원인 방지대책"

## 사실 (Andy 9515, 읽기 전용)

상세 추출은 [live-trace](evidence/live-trace.txt)에 있다.

1. **기록:** 2026-11-25 Daniel 161117이 두 가지를 요청했다.
   - (1) ACK 없이 글을 등록하면 Walkie 서버가 오류와 오류 메시지를 반환한다.
   - (2) 사이드 액터 시스템 프롬프트를 리뷰하고, 고칠 점을 ciel runtime 기본값에 반영한다.
   - 사이드 영수증 `d8f556ff41`이 이것을 n2459의 `update`·`complaint`로 처리했다. 새 항목이 아니라 `progress`에 덧붙인 노트였다.
   - 처리 시각: 16:53Z 수신, 19:00Z 처리 시작, 19:14:57Z 커밋.
   - 회신 161245와 메인 알림은 19:15Z에 나갔다.
2. **메인:** 19:15:30Z부터 원문과 n2459를 조회했지만 19:25:53Z에 대화가 압축됐다.
   - 압축 요약은 "기존 n2459에 연결됐으며, 새 서버 정책의 구현 승인은 확인되지 않았습니다"였다.
   - 이후 두 요청에 대한 Work 항목은 만들어지지 않았다.
3. **유실:** 2026-11-26 14:05:59Z, 사이드 액터 `agent-a06f6e79161b66bab`(`ciel-work:side-fresh`, 이벤트 659fcb7d3b, Walkie 162016 반려)가 직접 `work_node_update(id=n2459, progress=…)`를 호출했다.
   - 라우터는 "Updated n2459"로 받아들였다.
   - `progress`는 교체되는 필드라서 161117 불만 기록과 앞선 사이드 요청 블록이 모두 지워졌다.
   - 현재 보드에서 161117은 n2459의 `refs`에만 남아 있다. 두 요청을 담은 노드는 없다.
4. **n2459 자체:** 멈추지 않았다.
   - n2900(PR #141 배포·새 검수 요청)이 2026-11-27 13:54:50Z에 done이 됐다.
   - 남은 leaf n2807은 "Daniel Yun decision on the NEW 144618 review request"를 기다린다.

## 원인

- **직접 원인:** 사이드 액터가 Work 보드를 직접 쓸 수 있었다.
  - 사이드 기본 지침에는 "Work를 직접 변경하기 (work_* create/update/execute/report/handoff/resolve 등) … 하지 않습니다"가 있다. 하지만 지침 스스로 "도구를 막는 장치가 아니라 이 프롬프트의 정책"이라고 적는다.
  - `ciel-work-gate`는 사이드 모양의 포크를 그대로 통과시켰다(`judge()`의 `kind !== 'work'` → null).
  - fresh 사이드 타입은 메인의 시스템 프롬프트(`$.prompt.compose()`)로 등록된다. 그 프롬프트의 Work 규칙에는 `work_node_update`로 보드를 갱신하라는 문장이 있다.
- **기여 원인:**
  - 불만이 독립 항목이 아닌 progress 노트로만 남았다.
  - 메인이 알림을 받은 직후 압축됐다.
  - 둘 다 이번 수정의 대상이 아니다. 아래 "남은 항목"에 적었다.

## 수정 (`ciel_runtime_support/claude_mods/ciel-work-gate/hooks/register.js`)

- 사이드 액터(`classify` kind `side`: `Side SSE <12hex>` fork 또는 `ciel-work:side-fresh`)가 `mcp__ciel-runtime-router__work_*` 도구를 부르면 거부한다(rule `side-work-write`).
  - 읽기는 허용한다: `work_status`, `work_parallel_status`, `work_proposal_status`, 사이드 전용 `work_side_code_read`·`work_side_code_search`.
- 거부문: `Ciel Work side gate: <tool> is not available to a side actor. Put the Work change in your decision JSON; the host records it.`
  - 게이트의 기존 거부문과 같은 방식(코드 리터럴)이다. 관리형 프롬프트 레지스트리 밖에 있는 모델 대상 문장이라는 점을 밝혀 둔다.
- 사이드의 다른 호출(Walkie, Read, Bash, 라우터의 work_ 아닌 도구)은 이전과 같이 건드리지 않는다. 라우터 조회도 하지 않는다.
- 호스트(ciel-work 모드)의 `$.mcp.call`은 tool.call 훅을 거치지 않으므로, 사이드 결정의 커밋 경로는 그대로다.
- 기존 테스트 `side_actor_passes`는 사이드의 `work_side_resolve`가 통과하는 것을 기대했다. 지침이 금지한 쓰기이므로 거부로 바꿨다.

## 검증

- **fail-first:** [red-before-fix](evidence/red-before-fix.txt). 수정 전 `work_node_update`가 통과했다(2 failed).
- **수정 후:** `tests/test_work_fork_gate.py` 35 passed. `claude plugin validate ciel-work-gate` 통과.
  - 쓰기 도구 17종이 tool.call과 tool.check에서 모두 거부된다.
  - 읽기 5종과 Bash·Walkie 호출은 통과한다.
  - 라우터 호출은 0이다.
- 영향 회귀(Windows 임시 격리, Linux py310/py311)는 [사이클 7](../../deployments/cycle-7/record.md)에 있다.

## 남은 항목

- Daniel 161117의 두 요청은 Andy 보드에 항목이 없다. (2)는 오늘 ciel-runtime 쪽 수정([side-ack-gate](../side-ack-gate/record.md), [side-default-from-andy](../side-default-from-andy/record.md))과 겹친다. (1) Walkie 서버 오류 반환은 추적되지 않는다.
  - 운영 보드는 수정하지 않는다. 등록 여부는 Andy 세션과 사용자가 정한다.
- 불만(complaint) update가 독립 항목 없이 progress 노트로만 남는 설계와, 메인 압축 직후 알림 유실은 별도 판단이 필요하다. 이번에 코드를 바꾸지 않았다.
