---
type: release-record
title: Side 콘솔 페이징·편집기·ACK 지침·워커 도구 지침 배포
tags: [side-sse, work-fork, release, beta]
timestamp: 2026-11-26T03:58:00Z
---

# Side 콘솔 페이징·편집기·ACK 지침·워커 도구 지침 배포

## 변경

- Side 탭 첫 목록을 15건으로 분리해 영수증 먼저 표시, 검수·집계는 별도 조회. 편집기 전체 화면.
- 사이드 지침(기본 `PROMPT`와 관리형 `side_source_instructions`)에 ACK 필수 `version` 조회·`client_id` UUID·정확한 `message_id`·`memo`를 명시. #161497 400의 원인이 운영 스키마 기준 필수 필드 누락임을 먼저 입증했다.
- Work 포크 지침(`mod_fork_rules`)에 Agent/Explore·TaskStop·SendMessage 금지, 직접 조사와 `work_parallel_report` 경로, 거절된 도구 재호출 금지 명시. 권한 가드는 변경하지 않았다.

## 검증

- Windows 격리(Edge 브라우저 포함): 149건 통과, 134 서브테스트. `claude plugin validate` 통과, `claude plugin test` 95 통과·0 실패.
- 기존 Linux py310·py311: 각각 139건 통과, 10건 스킵(브라우저), 118 서브테스트.
- npm beta `0.2.51-beta.20261126-034736.b86fa04`(gitHead `b86fa04`) tarball 8개 코드 파일이 커밋·작업 트리와 SHA-256 일치. 오버레이는 `61d2f8b` 기준이며 7개 승인 루트 모두 `base` 확인 후 적용 `ok=true`.

## 라이브 효과와 미확인 항목

파일 설치는 7개 루트에서 확인했다. 모드 지침은 각 CLI가 ciel-work를 다시 로드할 때, 라우터 변경은 라우터 재시작 때 적용된다. 재시작은 하지 않았다. 이번 증거 범위에서 Andy 화면의 재시작 후 포크·사이드 복귀 실패 원인은 입증되지 않았으므로 수정하지 않았고, 입력 JSON 손상·워커 리스 권한 거절·worktree 관련 보고도 추적 항목으로 남긴다.
