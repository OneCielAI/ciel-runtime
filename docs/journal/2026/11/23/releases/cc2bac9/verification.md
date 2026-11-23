---
type: verification-record
title: 5e02ee7·cc2bac9 시험·배포 증거
description: 큐 순서·대기 목표 승인·포크 재시도(5e02ee7)와, 그 배포 직후 드러난 혼합 버전 승인 결함 수정(cc2bac9)의 검증, 배포, 라이브 반영 기록. 파일 설치와 라이브 효과를 나눠 적는다.
tags: [work-mode, release, rollout, mixed-version]
timestamp: 2026-11-23T15:45:00Z
---

# 변경 범위

- [work-queue-restart](/docs/journal/2026/11/23/fixes/work-queue-restart/record.md)(5e02ee7)
  - 큐 순서 디스패치, `work_execute` `root_id`로 대기 목표 승인, 포크 API 재시도 awaiting.
  - 배포 전 검사에서 함께 고친 기존 결함: 줄 수 예산, import 순환, 설정 픽스처.
- [execute-root-capability](/docs/journal/2026/11/23/fixes/execute-root-capability/record.md)(cc2bac9)
  - 라우터가 `execute_root`를 광고할 때만 모드가 대기 목표의 `root_id`를 보낸다. 지침 문장도 이때만 붙인다.
  - 설정 CAS 테스트의 경쟁 조건 수정.

# 시험

- 5e02ee7 최종 트리
  - CI 상당: py310 unit shard 1–3, py311 router/channel/runtime 모두 EXIT 0.
  - 3환경 41개 파일: 각 1303/0, `real_backend` 각 120/0.
  - 그 밖에 ruff 0.15.22, compileall, check:docs, npm pack.
  - 라우터 실기동: 3환경 health 200, 동시 버스트 200/200·64/64.
  - 실엔진 로드 15/15.
- cc2bac9
  - 실패 테스트 3건을 먼저 확인했다.
  - 3환경 각 1428/0. py310 shard 1–3 EXIT 0(shard 2는 CAS 테스트 수정 뒤 재실행).
  - quality 명령, `claude plugin validate`·`test`(94/0), 실엔진 로드 15/15.

# 배포

| 커밋 | Publish | npm beta | 오버레이 |
|---|---|---|---|
| 5e02ee7 | 45193606117 success | `0.2.51-beta.20261123-142311.5e02ee7` | `overlay-5e02ee7` 14개 파일(`docs/` 없는 루트는 13개) |
| cc2bac9 | 45205553069 success | `0.2.51-beta.20261123-152815.cc2bac9` | `overlay-cc2bac9` 4개 파일 |

- 두 번 모두 npm gitHead가 푸시한 커밋과 같았다. 배포본 파일과 오버레이 new의 해시도 모두 일치했다.
- 설치 대상: 로컬 hotfix5(Andy), `.local/share/ciel-runtime`, npm 전역, Celly(`G:`), KOBE, Prody.
- 시각: 5e02ee7은 14:36:38–14:37:08Z, cc2bac9는 15:39:33–15:39:47Z. 설치 전 해시는 모두 base, 설치 후는 모두 new였다.

# 라이브 반영 (파일 설치와 구분)

- 모드 재로드(`ciel-work: reloaded (19 hooks`)

  | 호스트 | 5e02ee7 모드 | cc2bac9 모드 |
  |---|---|---|
  | Andy | 14:36:44Z | 15:39:49Z |
  | KOBE | 14:37:14Z | 15:41:42Z(턴 종료 `turn_duration` 15:41:41Z 직후) |
  | Celly | 15:16:37Z | 15:39:35Z |
  | Prody | 없음(fcce6d7 모드에서 바로 cc2bac9로) | 15:42:09Z(앞에 턴 경계 없음) |

  재로드 시점을 정하는 규칙은 확인하지 못했다. 관측한 것만 적는다.
  - Andy: 마지막 턴 종료가 설치 전인 15:38:47Z였다. 설치(15:39:33Z) 16초 뒤에 재로드됐고, 그 사이 턴 경계는 없었다.
  - Celly: 15:39:31Z 턴 종료, 설치 2초 뒤에 재로드됐다. 5e02ee7 때는 설치(14:36:38Z) 40분 뒤에 재로드됐다.
  - KOBE: 턴 종료 1초 뒤에 재로드됐다.
  - Prody: 턴 경계 없이 설치 약 2.5분 뒤에 재로드됐다.

- 5e02ee7 모드가 라이브인 동안 메인 지침에 `root_id` 문장이 실제로 들어 있었다(Andy 14:44:33·15:20:55Z, Celly 15:28–15:37Z 첨부 기록).
- 위험 구간(5e02ee7 모드 + 옛 라우터)에서 `root_id`가 든 `work_execute` 호출은 네 호스트 모두 0건이다.
  - Prody의 15:11:26Z 호출은 `root_id`가 없는 활성 목표 승인이었다. Prody는 그때 아직 fcce6d7 모드였다.
- 라우터 Python 변경은 네 라우터 모두 라이브가 아니다.
  - 해당 변경: P0 intent 정리(fcce6d7), 큐 순서·`root_id` 승인·재시도 분류기 awaiting(5e02ee7), `execute_root` 광고(cc2bac9).
  - 예: Celly 라우터 지문은 `84eb402c`(fcce6d7 이전), 버전 `…013244.432c8a8`이다.
  - 라우터 재시작은 이 작업의 금지 범위라서 하지 않았다.
- 그래서 cc2bac9 모드는 지금 옛 라우터에서 대기 목표의 `root_id`를 거부한다. 대기 목표 승인은 라우터가 재시작된 뒤에 열린다.

# 남은 것

- 네 호스트 모두 cc2bac9 모드로 재로드된 것을 관측했다(15:42:09Z).
- Celly에서 재로드 뒤 첫 메인 지침 첨부(15:43:12Z)에는 Work 규칙이 있고 `root_id` 문장은 없었다. 옛 라우터라 `execute_root` 광고가 없으니 의도한 대로다. Andy는 15:43:36Z까지 새 지침 기록이 없어 미관측이다.
- 라우터 재시작 전까지 큐 자동 재디스패치 수정은 라이브가 아니다. 이 때문에 실엔진 재시작 E2E(점검 리스트 14)를 운영에서 확인할 수 없다.
- [Celly P0 장부](/docs/journal/2026/11/23/operations/celly-p0-ledger-full/record.md)에 대해 남은 것: 이전 scope의 미발사 intent 13행에 정리 경로가 없다.
