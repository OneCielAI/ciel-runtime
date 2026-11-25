---
type: fix-record
title: 사이드 게이트와 GitHub 알림 전달
description: 사이드 프레시의 대기 판정, 원본 스레드 연결, 포크 전달 진단과 검증
tags: [side-fresh, work-gates, github, walkie]
timestamp: 2026-11-25T02:00:00Z
---

# 사이드 게이트와 GitHub 알림 전달

## 운영 관측

- 현재 세션에서 fresh 전사와 연결된 해제 영수증: Andy 89(주기 점검 29), Celly 31(12), KOBE 2(1), Prody 3(2), Betty 0(side intake 꺼짐). Andy에서 blocked→pending 주기 점검 영수증 2건도 확인. 조회 표면과 재시도 귀속이 달라 다른 호스트의 상태 해제를 0으로 단정하지 않는다.
- Celly n1505 전사에 GitHub 이벤트 c042188a6f39, 99052dba1436, 591f4db6dda7 수신이 있고 본문은 `[object Object]`였다. n1406 영수증 3ef59cde48b5는 23:10Z 이후 조건을 18:05Z에 해제했다. Prody a148161fe339는 answer와 비어 있지 않은 release_waits를 같이 내고 해제 없이 처리됐다.
- Betty #160442 관련 전사에서 n1799의 대기와 f00c3218/bf937a8a 미바인딩 예약이 P1 슬롯 2/2를 차지했다는 보고를 확인했다. 이는 GitHub 이벤트 전달과 별개다. 어느 코드 경로에서 예약이 남았는지는 입증하지 못했고 운영 예약은 변경하지 않았다.

## 반영 및 검증

- Work 리프의 기존 question_wait 계약에 원본 Walkie 대화·루트 기록을 추가했다. 알림의 검증된 스레드 식별자와 보드 바인딩/refs를 후보 선정에만 사용한다. SHA, run/attempt, 결론 확인은 별도다.
- 포크 및 메인 relay에 읽을 수 있는 본문과 원본 식별자를 전달한다. 새 answer가 비어 있지 않은 해제 요청을 조용히 버리지 않도록 거부한다. 게이트 틱은 UTC 시각과 당시 대기/힌트의 해시를 사용하고, 인식 가능한 명시 UTC 기한 이전에는 해제를 거부한다. 조용한 스레드의 blocked 노드도 계속 점검한다.
- Windows 및 Linux py310/py311 주 회귀 각각 217 통과·13 스킵, 플러그인 95 통과. 이후 변경을 포함한 Windows 재검증: 게이트·중계·모드 112 통과, 최신 판정 응답을 포함한 사이드 회귀 146 통과·13 스킵(서로 겹치는 스위트). Linux 두 버전에서 게이트·중계 40건씩 추가 통과(마지막 본문·응답 변경 전).
- 실제 격리 CLI의 fresh 게이트 → 메인 모델 요청에서 정지 포크 알림 확인(64.6초). 별도 실제 CLI의 합성 외부 이벤트 → 기존 그룹 대기 해제 및 하위 노드 유지 확인(165.6초). 두 프로브 모두 기존 프로필 변경 없음. 이 결과는 GitHub SSE를 받아 같은 파킹 포크가 읽는 종단 검증을 대신하지 않는다.
- 최종 Windows 격리 회귀는 224 통과·16 스킵(125 서브테스트 통과). 마지막 후보순위 정리 직전 Linux py310/py311 판정·전달 회귀는 각각 89 통과·15 스킵. 재개 하네스는 기존 리스에 원본 Walkie 루트·메시지·본문이 담긴 전달 텍스트가 넘어가는 것을 확인했다.
- 최종 빌드의 격리 실제 CLI에 합성 Walkie GitHub CloudEvent를 durable inbox부터 주입했다. fresh 액터가 원본 스레드 `1000`을 받아 기존 그룹의 대기를 해제했고, 메인 모델 요청에 해제 알림이 실제 포함됐다(영수증 processed 12.2초, 기존 프로필 변경 없음). 이 시험은 SSE 전송 계층과 동일 파킹 포크의 실제 다음 모델 요청까지는 포함하지 않는다. 파킹 포크 본문 전달은 하네스로만 확인했다.
- Betty 읽기 전용 REST 재조회에서도 두 예약이 `reserved`, `slot=true`, `host_id=null`이었다. 이후 05:09Z 틱과 05:56Z Betty 보고에서 `d03b00dafd41`(n1815, 04:59:40Z)과 `bf2b51597a26`(n1816, 05:16:59Z)이 같은 증상으로 재발했다.
- **Betty 미바인딩 예약 근본 원인 입증:** Betty 저장소(`sgswms-99004`)는 `remote.origin.partialclonefilter=blob:none`, `remote.origin.promisor=true`이고 HEAD 트리에 누락 blob 1건(`ce945ba95767...`)이 있다. `work_parallel_workspace._git`가 `git worktree add`를 실행할 때 git의 `reset --hard`가 `git fetch origin` → `git-remote-https` → `git-credential-manager get`을 띄우고 대화형 인증에서 멈췄다. `_git`는 `GIT_TERMINAL_PROMPT=0`/`GCM_INTERACTIVE=never`를 주지 않았고, Windows `subprocess.run(timeout=30)`은 `cmd\git.exe` 래퍼만 종료하고 `mingw64\bin\git.exe`와 `git-credential-manager.exe` 손자 프로세스 및 상속 파이프를 남겨 `communicate()`가 영구 봉쇄되면서 예약 해제가 실행되지 않았다.
- **KOBE/Celly/Andy side-fresh 실패·중복 근본 원인 입증:**
  1. `work_side_intake_session=fresh`에서도 `max_active=2` + urgent 예약 슬롯 때문에 KOBE에서 2~3개의 `side-fresh`가 동시에 중첩 실행됐다(최대 동시 3개, 43분 실행 건 포함).
  2. Celly 5건·KOBE 3건의 `invalid_answer`는 `{"understanding":...,"decision":{...}}`의 바깥 `}` 하나 누락, KOBE `68f98974`·`d548fdd3`는 `no_reply=true`의 `reply` 누락·`understanding` 누락, KOBE `a2031514`·`472f3fdd`는 `CORRECTABLE`에 `priority`/`queue_position` 누락, Andy `50f0bced`는 유효 JSON 턴 직후 백그라운드 알림으로 추가된 짧은 산문 턴이 최종 `e.answer`를 덮어쓴 것이었다. 이 실패들이 격리 후 원문을 메인에 밀어 넣어 메인 컨텍스트를 오염시켰다.
- **추가 반영:**
  - `work_parallel_workspace.py`: `_git`에 `GIT_TERMINAL_PROMPT=0`, `GCM_INTERACTIVE=never`, `GIT_GCM_INTERACTIVE=never`를 강제하고, 타임아웃 시 Windows `taskkill /PID /T /F`·POSIX `os.killpg`로 전체 프로세스 트리를 종료한 뒤 파이프를 회수한다.
  - `side-intake.js` & `side_intake_mcp.py`: `session="fresh"`에서는 라우터와 모드 양쪽에서 동시 활성 `side-fresh`를 1개로 제한한다(라우터 단독 재기동만으로도 즉시 중복 차단). `decisionOf`에서 바깥 `}` 누락 복구, `no_reply=true` 시 `reply`·누락 `understanding` 보완(라우터 `work_side_understanding` prepare에서도 동일 보완), `CORRECTABLE`에 `priority`/`queue_position`/`item_id`/`note`/`title`/`refs`/`release_waits` 추가, 백그라운드 알림 후속 산문 턴일 때 같은 액터 직전 assistant 메시지에서 유효 JSON 회수.
- **최종 회귀 검증:**
  - Windows 격리 회귀 12개 파일: **310 통과, 16 스킵, 0 실패**.
  - Linux 기존 `ciel-runtime-test-py310` / `ciel-runtime-test-py311`: 각 **310 통과, 16 스킵, 0 실패**, `compileall` 0, `check_project_metadata.py` 0.
  - `claude plugin validate ciel-work` 통과, `claude plugin test ciel-work` **95 통과, 0 실패**.
  - 동일 파킹 포크까지의 실제 SSE 종단 검증은 이번 긴급 배포 전 미실행(하네스 및 durable-inbox 실 CLI 검증으로 대체, 별도 추적).
