---
type: fix-record
title: Celly 메인 모델의 터미널 선택 질문이 턴을 두 시간 멈춘 문제
description: 2026-11-22 22:50:17Z Celly 메인 모델이 AskUserQuestion을 호출해 CLI 화면에 질문을 띄웠고 2026-11-23T00:50:14Z에 답변이 도착하기 전까지 메인 턴이 약 두 시간 멈춰 있었다. Work 호스트가 떠 있던 동안 무인 사이드 모드에서 엔진에서 온 AskUserQuestion을 거부하도록 했다.
tags: [work-mode, celly]
timestamp: 2026-11-23T01:14:00Z
---

## 운영 관측 (읽기 전용, Celly)

- 2026-11-22 22:50:17Z: 트랜스크립트에 `isSidechain=false`인 메인 턴의 네이티브 `AskUserQuestion` 호출이 있다. 질문은 "n1323(155673, Walkie P3)과 n1327(155744, Walkie P3)은 P0인 0.2.169 릴리스(n1335)의 선행 작업입니다. 그런데 P3 슬롯 1개를 n1299(WOL)가 쓰고 있어… 어떻게 할까요?"였다. 선택지는 "선행 작업 P0 상속 (Recommended)", "P3 동시 실행 한도 3으로", "그대로 대기"였다.
- 이 도구 호출의 결과는 2026-11-23T00:50:14Z("Your questions have been answered…")에야 도착했다. 그 사이 메인 턴은 두 시간 동안 멈춰 있었다.
- 이 질문은 Ciel Work 호스트가 `$.ui.ask`로 건 것이 아니라 모델이 부른 `AskUserQuestion`이었다.
- 원래 사람이 결정할 일은 Walkie 등 외부 채널로 보내는 규약이 있다. 하지만 Work 호스트는 이를 `tool.call` 훅에서 막지 않고 있었다.

## 이전 차단 `002189d`가 막지 못한 이유

이전 차단이 막은 건 `recoverSubmittedReview`가 스스로 부르던 `$.ui.ask`뿐이다.

모델이 부르는 `AskUserQuestion`은 `parallelToolCall`에서 이 도구에 대한 거부 규칙이 없어 그대로 통과했다. 포크도 마찬가지였다.

## 수정

1. `ciel_runtime_support/claude_mods/ciel-work/hooks/register.js`의 `parallelToolCall` 시작 부분에 거부를 추가했다.
   - 엔진에서 온 원본 도구 호출일 때만 작동한다. 포크(`agentId`가 있는 쪽)는 무조건 거부하고, 메인에서는 `work_side_settings`에서 사이드 무인 모드가 켜져 있는지를 확인해 거부한다.
   - 내부·다른 플러그인·사이드 모드가 꺼진 메인에서는 원래 흐름을 막지 않는다.
2. 거부 문구는 `ciel_runtime_support/work_texts.py`의 관리 문구 `mod_terminal_question_refused`로 두고, 모드 기본 텍스트 파일(`texts.generated.js`)을 소스에서 다시 생성했다. 코드에 모델용 텍스트를 직접 쓰지 않는다. 이 문구는 터미널에서 묻지 말고 외부 선택 채널을 써야 하며, 결정이 꼭 필요하면 그 동안 다른 독립 작업을 계속할 수 있도록 `waiting_on`·`release_hint`를 적어 두라고 안내한다.

## 검증

- 실패 테스트를 먼저 썼다: 하니스 시나리오 `side-refuses-native-terminal-question`을 만들고 공짜로 통과됨(수정 전에는 'ran')을 확인한 뒤 통과시켰다.
- 호환된 선택이 되도록 실제 거부 문구와 하니스 스텁을 수정한 뒤 테스트를 다시 돌려 통과시켰다.
- `claude plugin validate ciel-work` 통과. `claude plugin test ciel-work` **94/94**.
- Windows 로컬: `test_work_texts.py`, `test_work_mod_write_gate.py`, `test_work_parallel_native.py`, `test_work_parallel_p0_events.py` **195건 통과, 33 skip**.

## 커밋·배포

- 최종 다섯 호스트 운영 반영(베타/배포 및 SessionMod real-load 게이트)은 다른 문서의 배포 기록을 따른다. 아래에 배포 메커니즘에 대한 증거를 남긴다.

## 남은 항목

- 사이드 무인 모드가 꺼진 메인, 비엔진 origin, 다른 플러그인은 이 차단을 거치지 않으므로 고정 확인이 아니다.
- `work_side_settings` 응답을 읽지 못하면 fail-open으로 빠져나간다. 무인 모드에서 이 경로가 등장하면 이를 좁혀야 한다.
- 이전 운영 Tik에서 본 조정 액션 등 네 개 호스트의 모니터링/전달 경고는 이 기록 범위 밖이다.
