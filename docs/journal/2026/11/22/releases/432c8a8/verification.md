---
type: verification-record
title: 432c8a8 시험·배포 증거
description: 세 Work 수정본을 커밋 432c8a8로 통합한 검증·배포 증거.
tags: [work-mode]
timestamp: 2026-11-23T01:38:00Z
---

## 시험·배포 증거

- 통합 최종 검증: Windows 948건 + subtest 361건에서 failure 0, 기존 `test_work_parallel_legacy_reviewer` 스키마 부분검사 1건은 실패이지만 코드 변경과 무관해 제외했다. Python 3.10·3.11의 Linux 기존 컨테이너에서는 1,026건에서 failure 0, 같은 기존 무관 ERROR 1.
- Ciel Work mod 게이트: `claude plugin validate ciel_runtime_support/claude_mods/ciel-work` 통과, `claude plugin test ciel_runtime_support/claude_mods/ciel-work` **94/94 통과**.
- commision: `432c8a8` 통합 커밋 → `feature/priority-models` 및 `beta`에 push.
- Publish: GitHub Publish `45095141396` success, npm `@beta` = `0.2.51-beta.20261123-013244.432c8a8`.
- npm 패키지 검사: `npm pack –dry-run`/다운로드한 패키지 내부 파일들의 SHA-256 prefix가 설치본과 전 개 일치한다
  - `work_parallel_mcp.py` `809cdeb57909`, `work_texts.py` `12616f23337c`, `register.js` `c9f55df3340c`, `texts.generated.js` `5ea0f6d00f9b`.
- 해시 검증 overlay 로컬 설치(백업 태그 `overlay-432c8a8`): 로컬 3경로, Celly(G:), KOBE, Prody 모두에서 기존 해시를 확인한 뒤 교체했고 설치 후 해시를 커밋 값과 재검증했다.
- 라이브 반영 확인(Deployment = 파일 설치 + 재시작): 사용자 지시에 따라 네 라우터만 정확히 중단해 Keep-alive가 다시 기동하게 했다. 새 PID는 Celly 61892(URL은 Celly 전용 `.claude.json` 프로젝트와 같다), KOBE 15480, Prody 12320, Andy 48136이다. 기동 시각은 Celly는 01:38:33Z ~ 01:38:36Z 사이, KOBE 01:38:33Z, Prody 01:38:36Z이다.
- Celly Work 모드는 수정 코드의 2문단 공지가 현 화면에 실제로 송출되었으므로 01:33:41에 성공적으로 `reloaded (19 hooks)`되었고 01:36:09에 `Work mode is on`이 나타났다.

## 잔여

- Celly 메인 턴이 명령행으로 여전히 멈출 수 있는지는 실제 흐름까지는 아직 고하지 않는다. 이 문서에서 축적된 게이트로만 증거를 나열한다.
- npm 메타데이터(버전 문구·package.json)는 위 overlay로 직접 바꾸지 않았고 설치본의 package version 표기가 언제 갱신되는지는 이 기록 범위 밖이다.
- spaun 전후 intent release 동시성 경계에 대한 동시 실행 재현 테스트는 분산 CAS 검증 범위 밖으로 남는다. `p0_launch_spawning`과의 동시 경계 템플릿은 냉동된 패턴 예외(`spawn attempt lost`, Celly 2026-11-18)로 남아 있다.

## 출처 결정

- 채택된 Git 상태: 현재 HEAD `432c8a8` (feature/priority-models = beta). 릴리스된 npm 결과와 배포 파일은 이 커밋의 해시와 일치한다.
- 배포 전 최종 릴리스 검증에서는 위 수치를 사용하고, 내부 테스트 재현 결과는 위에 남긴다.
