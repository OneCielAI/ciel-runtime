---
type: fix-record
title: 사이드 판정 파싱 무한 루프와 보정 불가 실패
description: decisionOf 무한 루프가 hooks worker를 멈춰 ciel-work를 언로드한 결함, 없는 item_id·refs 형태로 보정 없이 격리된 결함의 원인과 수정
tags: [side-fresh, ciel-work, hooks-worker, decisionOf, refs]
timestamp: 2026-11-25T09:00:00Z
---

# 사이드 판정 파싱 무한 루프와 보정 불가 실패

모든 시각은 UTC다. 운영 호스트 조회는 읽기 전용(REST·전사·로그)이었다. 운영 세션·라우터 재시작과 상태 파일 수정은 하지 않았다.

## 결함 1: `decisionOf` 무한 루프 → ciel-work 언로드

- **관측:** Prody 메인 `8ee391dd` 07:50:00.141 `ciel-work was unloaded: it crashed the hooks worker`, 07:52:01부터 Work 호출이 `conversation identity is unavailable`로 거부됐다(08:00:19까지 5건 더).
- **원인(재현 확인):** `side-intake.js` `decisionOf`는 `text.lastIndexOf('{', start - 1)`로 거꾸로 훑는다. JavaScript `lastIndexOf(ch, -1)`은 위치를 0으로 고정하므로, `text[0]`이 `{` 또는 `}`이고 `"decision"` 조각이 없으면 `start=0`에서 영원히 돈다. `node -e` 재현에서 `{"analysis_focus":"test"}` 입력이 20회 넘게 `start=0`을 반복했다.
- **실제 입력과의 대조:** 크래시 7건 모두, 같은 대화의 side-fresh 최종 답이 6초 전에 이 형태였다. 앞뒤 공백을 빼면 `{`로 시작하고 `"decision"` 키가 없다. 반대로 이 형태 답 7건 뒤에는 모두 크래시가 있었다.

| 호스트(대화) | 최종 답(서브에이전트) | 크래시 |
| --- | --- | --- |
| Prody bg dispatch `2e7bf245` | `d50032da` 11-24 23:47:28 | 23:47:34 |
| Prody bg dispatch `2e7bf245` | `49565a1f` 02:56:15 | 02:56:21 |
| Prody 메인 `8ee391dd` | `ab216dd0` 03:30:48 | 03:30:54 |
| Prody 메인 `8ee391dd` | `a0dc1a68` 07:49:54 | 07:50:00 |
| KOBE 메인 `9b0de8fe` | `a4d2bb3c` 01:00:40 | 01:00:46 |
| KOBE 메인 `9b0de8fe` | `a64016ba` 03:08:54 | 03:09:00 |
| Celly `109c4883` | `d2da6ad2` 04:58:53 | 04:58:59 |

  Andy(side 답 453건)와 Betty(side intake 꺼짐)에서는 이 형태 답과 크래시가 모두 0건이었다. KOBE bg `a0e19ac8`의 크래시 2건(11-24 23:45:54, 02:45:11)은 이번 스캔 도중 KOBE SSH 인증이 만료돼 대조하지 못했다. [Prody 세션 중단 진단](/docs/journal/2026/11/25/diagnostics/prody-session-crash/record.md)에서 미확인으로 남긴 hooks worker 크래시 사유는 이 결함으로 설명된다. 엔진이 hooks worker를 언로드한 내부 판단 기준(시간 제한 등)은 확인하지 못했다. 위 대응은 상관 관계와 재현으로 뒷받침되며, 엔진 디버그 기록으로 확인한 것은 아니다.
- **수정(45f07b7):** 인덱스 0에서 탐색을 멈추고, `"decision"` 키가 있는 위치 앞의 `{`만 후보로 잡는다. 해당 형태 3종이 `side answer carries no decision JSON`으로 즉시 거부되는지 하네스로 확인한다(10초 제한).

## 결함 2: 없는 item_id가 보정 기회 없이 격리

- **관측:** KOBE `b41a2e1bd98f`(06:27:34 게이트 재확인)의 side-fresh `a07fe32a`가 58분 뒤 07:40:53에 `action=update, item_id="153865"`(Walkie 메시지 id)로 답했다. `work_side_resolve`가 `side target Work item missing`으로 거부했고 격리됐다. 07:41:09에 원문이 메인으로 전달됐다(appended).
- **원인:** `work_side_understanding` prepare가 제안을 저장할 때 item_id 존재를 확인하지 않았다. 저장되면 `actor.understood=true`라 보정 턴이 금지되고, resolve 거부가 곧 격리가 됐다. 문구도 `CORRECTABLE`에 없었다.
- **수정(45f07b7):** prepare에서 Work 보드 노드 id를 확인하고, 없으면 저장 전에 거부한다(불만 update는 resolve에서 create로 바뀌므로 제외). `CORRECTABLE`에 `side target Work item missing`을 추가했다.
- 같은 커밋에서 회귀 2건을 고쳤다. 명시적 빈 understanding은 다시 거부한다(누락일 때만 summary로 보완). `work_gate_check.tick_event`는 `blocked` 키가 없는 행을 `False`로 본다.

## 결함 3: refs의 따옴표 없는 UUID·숫자

- **관측:** KOBE `f5ae6c68b340`(08:06:09 게이트 재확인)의 side-fresh `ab5656ff`가 08:29:31에 산문으로 답했다. 08:29:32 보정이 전달됐고 08:31:23 보정 답이 왔다. 보정 답의 `refs`가 `[138846,…,4c846761-5ce7-4f79-b65f-db09b5605413,226,…]`이었다. UUID가 따옴표 없는 토큰이라 JSON 파싱이 실패했고(`Expecting ',' delimiter`), 격리 후 08:31:28 원문이 메인으로 전달됐다.
- **수정(d1f9da3):** 모드는 `"refs"` 배열의 따옴표 없는 원소를 원문 그대로 문자열로 감싼 뒤 파싱한다(18자리 id 반올림 방지). 라우터 `validate`는 양의 정수 refs를 정확한 10진 문자열로 받는다. 0·음수·bool·실수·객체·null은 계속 거부한다.

## 검증

- 수정 전 실패 확인: 하네스 10초 시간 초과(결함 1), prepare 미거부(결함 2), `KeyError: 'decision'`·`invalid side decision refs`(결함 3).
- Windows 격리(`CIEL_RUNTIME_*` 제거, 임시 설정, `CIEL_RUNTIME_TEST_ISOLATED=1`): 12개 스위트 334 통과·13 스킵·0 실패, `compileall` 0, `check_project_metadata.py` 0.
- Linux 기존 `ciel-runtime-test-py310`·`ciel-runtime-test-py311`: 각 334 통과·13 스킵·0 실패, `compileall`·metadata 0.
- `claude plugin validate ciel-work` 통과, `claude plugin test ciel-work` 95 통과·0 실패.

## 발행·설치

- `45f07b7`: Publish `45576187246` 성공, `0.2.51-beta.20261125-082601.45f07b7`(gitHead 일치). 설치하지 않았다(d1f9da3이 포함).
- `d1f9da3`: `feature/priority-models`·`beta` 푸시. Publish `45578825302` 성공. `@one-ciel-ai/ciel-runtime@beta` = `0.2.51-beta.20261125-084356.d1f9da3`, gitHead `d1f9da35f5566b2499ecf84540b1ce1b5d14e160`.
- 해시 확인 오버레이 `overlay-d1f9da3`(tarball 코드 5개 파일 SHA-256 = 커밋)를 승인된 7개 루트에 `base → new`로 설치했다. 모두 `ok=true`이고 `.cmd` 래퍼는 바꾸지 않았다.
- **모드 라이브 반영 관측(09:01Z 틱에서 확인):** 설치 뒤 각 CLI 전사에 `ciel-work: reloaded (19 hooks …)`가 기록됐다. Celly 08:55:06, Prody 08:55:33, Betty 08:57:03, KOBE 08:58:14. 결함 1·3의 모드 측 수정은 이 4곳에서 동작 중이다. Prody는 이 리로드로 07:50 언로드에서 회복했다(08:35 이후 Work 식별 거부 0). Andy CLI는 08:37:36부터 종료 상태라 반영되지 않았다. 아래 "다시 불러올 때 반영"은 설치 시점의 판단이다.
- **라이브 효과와 파일 설치 구분(설치 시점):** 라우터는 재시작하지 않았다. Celly 114288·KOBE 10660·Prody 8384·Betty 6068은 `faa2c7e`로 동작 중이다. 라우터 측 변경(`side_intake_mcp`, `side_understanding_outbox`, `side_work_decision`, `work_gate_check`, 앞선 `side_forward` fe52b22)은 다음 라우터 기동 때 반영된다. `side-intake.js`(결함 1·3의 모드 측)는 각 CLI가 ciel-work를 다시 불러올 때 반영된다. Prody 메인은 07:50 이후 ciel-work가 언로드된 상태라 CLI 재기동 전까지 Work 식별이 없다.
