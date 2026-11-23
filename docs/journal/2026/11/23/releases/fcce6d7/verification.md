---
type: verification-record
title: fcce6d7 시험·배포 증거
description: P0 세션 간 유지, side 교정 전달, side 액터 정지 확인, 질문 대기 계약 분리를 담은 fcce6d7의 검증·배포·라이브 반영 기록.
tags: [work-mode, side-intake, p0, release]
timestamp: 2026-11-23T11:35:00Z
---

# 변경 범위

- [side-fresh-retirement](/docs/journal/2026/11/23/fixes/side-fresh-retirement/record.md): 끝난 side 액터를 엔진 목록으로 확인, 스킬 이름 `work-operations`.
- [side-correction-resume](/docs/journal/2026/11/23/fixes/side-correction-resume/record.md): 호스트 교정 resume 허용(Celly `invalid_answer` 격리의 원인).
- [question-wait-release-hint](/docs/journal/2026/11/23/fixes/question-wait-release-hint/record.md): 라우터가 `capabilities:["question_wait"]`를 광고할 때만 포크 프롬프트에 질문 대기 계약을 붙임.
- [p0-durability](/docs/journal/2026/11/23/fixes/p0-durability/record.md): P0를 세션 간 유지.

# 시험

- 영향 회귀 `final.102159`(최종 트리): Windows 격리·Linux py310·py311 각 813 실행, 실패 0, 오류 0, skip 58.
- 실CLI(Claude Code 2.1.330) 실행 8: 15/15.
- 플러그인 validate·test 결과는 각 수정 기록의 검증 절을 따른다(side-fresh-retirement: validate 통과, test 94 통과).

# 배포

- 커밋 fcce6d7 → `feature/priority-models`, `beta` push. GitHub Publish 45160770706 success. npm beta `0.2.51-beta.20261123-105743.fcce6d7`.
- 해시 확인 오버레이(백업 태그 `overlay-fcce6d7`, 15개 파일, 새 파일 `skills/work-operations/SKILL.md`): 로컬 3루트, Celly, KOBE, Prody. 모두 설치 전 해시가 base와 일치했고 설치 후 해시가 new와 일치했다. Andy가 쓰는 `ciel-runtime-hotfix5-20261119-210220` 루트는 11:04:48Z 설치.

# 라이브 반영 (파일 설치와 구분)

- 모드 재로드: Celly 11:04:51Z, KOBE 11:11:06Z, Prody 11:11:40Z, Andy 11:19:22Z. 이 시각부터 모드 변경이 라이브다.
- Celly side: 재로드 뒤 영수증 6건 전부 processed, 격리 0. 새 side 액터 9개 중 2개가 교정을 받은 뒤 processed로 커밋됐다. 교정 전달 수정의 운영 효과다.
- 라우터 Python 변경(P0 정리, 계보 채택, 능력 광고)은 네 라우터가 설치 전에 시작돼 아직 라이브가 아니다. 능력 광고가 없으므로 모드는 질문 대기 계약을 포크 프롬프트에 붙이지 않는다(의도된 배포 순서 호환). 라우터 재시작은 금지 범위라 하지 않았다.

# 남은 것

- 재로드 직후 `Agent type 'ciel-work:side-fresh' not found` 1회 실패 경로(원인 미확인).
- side 지시 문구 일부가 관리 프롬프트 레지스트리가 아닌 코드 문자열이다.
