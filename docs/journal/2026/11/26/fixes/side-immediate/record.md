---
type: fix-record
title: SSE 사이드 액터 배정에서 우선순위 제거
description: Work 실행 우선순위를 SSE 접수와 분리한다.
tags: [side-sse, work, priority]
timestamp: 2026-11-26
---

- 동일 Work 세션의 기존 사이드 액터 수와 P0–P5가 신규 Side 배정을 제한하지 않는다. 기존 `urgent_only` 호출 인자는 호환성 때문에 받되 무시한다. 수신 우선순위는 Work 결정에 전달한다. 중복, 리스 세대, Stop 대기, 실패 백오프는 유지한다.
- 사이드 펌프는 순차적으로 배정하고, 회전 조회 창 안에서는 도착순을 사용하며 실패 재시도와 신규 요청을 교대한다. 무제한 처리량이나 엔진 Stop 상태의 무조건 즉시 실행을 보장하는 것은 아니다.
- 수정 전: 기존 액터가 실행 중일 때 P4 배정이 `None`으로 실패했다. 수정 후 Windows 및 Linux py310/py311에서 각 209건 통과, 17건 스킵. Claude 플러그인 테스트 실패 0.
- 실제 Claude Code 2.1.332를 스텁 모델로 실행한 격리 검증: 기존 액터가 실행 중인 상태에서 P4 입력이 1.3초 뒤 새 액터의 모델 요청을 시작했고 기존 액터보다 먼저 완료했다. Stop 이후 사용자 입력으로 재개하는 기존 동작도 확인했다. [결과](evidence/native/result.json), [터미널 캡처](evidence/native/console-tail.txt). 결과 JSON의 일부 `p0_*` 키는 재사용한 드라이버의 옛 이름이며 이번 입력은 P4였다.
