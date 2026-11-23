---
type: verification-record
title: 9677d0f 시험·배포 증거
description: 사이드 점검이 질문으로 멈춘 포크를 보고 기록된 결정으로 답하게 한 수정(9677d0f)의 검증, 배포, 라이브 반영 기록. 파일 설치와 라이브 효과를 구분한다.
tags: [side-fresh, gate-check, parked-fork, release]
timestamp: 2026-11-23T17:15:00Z
---

# 변경 범위

- [side-parked-fork-answers](/docs/journal/2026/11/23/fixes/side-parked-fork-answers/record.md): 점검 대상에 질문으로 멈춘 포크를 넣었고, `fork_answers` 결정과 호스트 재개를 추가했다. 관리 프롬프트 `gate_actor`·`gate_recheck`도 고쳤다.

# 시험

- 실패 테스트 14건을 수정 전에 먼저 확인했다(Python 9, 모드 하네스 4, register.js 경로 1).
- 영향 회귀 20개 파일: Windows 격리·py310·py311 각 964/0.
- CI 상당
  - py310 unit shard 1–3 EXIT 0(shard 1은 옛 문구를 고정한 테스트를 고친 뒤 재실행).
  - py311 router/channel/runtime 1686/628/460 EXIT 0.
  - ruff 0.15.22, compileall, check:docs, npm pack.
- `claude plugin validate` 통과, `claude plugin test` 94/0.
- 실엔진 로드 15/15: real-load-4. 드라이버가 페이지 응답을 기록하도록 고친 뒤.

# 배포

- 9677d0f를 `feature/priority-models`·`beta`에 push했다.
- Publish 45222673557 success. npm beta `0.2.51-beta.20261123-165946.9677d0f`. gitHead가 일치하고, 배포본 9개 파일이 오버레이 new와 같다.
- `overlay-9677d0f`(9개 파일): 로컬 hotfix5·`.local/share`·npm 전역·Celly 17:11:00Z, KOBE 17:11:04Z, Prody 17:11:10Z. 설치 전 모두 base, 설치 후 모두 new였다.

# 라이브 반영 (파일 설치와 구분)

- 모드 재로드(`ciel-work: reloaded (19 hooks`)

  | 호스트 | 재로드 시각 |
  |---|---|
  | Celly | 17:11:20Z |
  | KOBE | 17:12:35Z |
  | Andy | 17:13:10Z(턴 종료 17:13:09Z 직후) |
  | Prody | 17:13:58Z 기준 미관측 |
- 라우터 Python 변경(점검 대상, `fork_answers` 판정, 관리 프롬프트 기본값)은 라우터 재시작 전까지 라이브가 아니다.
  - 이 사이 모드는 라우터가 `fork_answers`를 광고하지 않으므로 보내지 않는다.
  - 라우터가 고친 관리 프롬프트를 보내지 않으므로 사이드 액터도 그 지시를 받지 않는다.
- KOBE 라우터(관측, 재시작 주체 미확인)
  - `/health` 지문 `bc9f4d1c0730c8cc`, 버전 `0.2.51-beta.20261123-152815.cc2bac9`, pid 7452.
  - KOBE 대화 기록은 16:28:25Z에 새 세션 `dd8e657c`로 바뀌었다. 이 작업에서는 재시작하지 않았다.
  - 따라서 KOBE에서는 5e02ee7·cc2bac9의 라우터 변경(큐 순서, `root_id` 승인, 재시도 awaiting, `execute_root`)이 그 재시작 이후 라이브다.
  - 9677d0f의 라우터 변경은 다음 재시작부터다.
