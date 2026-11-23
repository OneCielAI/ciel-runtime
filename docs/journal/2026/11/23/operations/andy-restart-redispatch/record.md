---
type: diagnostic-record
title: Andy 재시작 후 Work 큐 로드와 포크 재개
description: 사용자 보고("재시작 후 Work 보드 큐가 로드되지 않음", "재시작되면 포크를 자동으로 재개해야 하지 않나")에 대한 읽기 전용 확인. 큐 로드, 재시작 때 끊긴 리스의 재개, 남은 P0 리프가 실행되지 않는 근거.
tags: [work-parallel, p0, restart, orphan-sweep, andy]
timestamp: 2026-11-23T11:25:00Z
---

# 범위

읽기 전용. Andy 라우터 REST(`127.0.0.1:9505`), 원장 `work-parallel.json`(v6188), `work-parallel-p0.json`(v949), 세션 대화 `feeac3ad`를 읽었다. 운영 세션·라우터 재시작과 상태 파일 변경은 하지 않았다.

# 큐 로드

- 11:19Z `/ca/work` 200, 532 ms, 노드 494개, `queue` 20행, `queue_ids` 30개 이상. 세션 `claude:feeac3ad-…`.
- 11:20Z 헤드리스 브라우저로 `/ca/work/ui`를 열었다(클릭·입력 없음). 트리와 큐가 표시됐다: [work-ui-1120.png](work-ui-1120.png). 상단 수치는 `68 / 69 completed`, `0 active`, `40 orphaned`다.
- 같은 화면의 콘솔 오류는 CSP `frame-ancestors` 메타 경고 1건과 404 리소스 1건이다. 404의 대상 URL은 이번에 기록하지 못했다(미확인).
- Andy 터미널 안의 Work 창(`/work-mode`, 10:58:28Z에 열림)은 이 머신에서 화면을 캡처하지 못했다. 그 창의 표시 상태는 미관측이다.

# 재시작 때 끊긴 리스의 재개

- 라우터 pid 117840 시작 10:57:50Z. 시작 고아 정리가 10:57:51–52Z에 리스 4건을 `orphaned`로 닫았다(`startup orphan sweep: owner process identity absent`): n2594(P1, P0 디스패치), n1847(P3), n1834(P3), n1835(P3).
- n2594: 호스트가 10:59:10.775Z에 P0 intent `3cc94777…`를 만들었다(`dispatch_without_approval: true`, 새 리스 `785a4e3e…`, 소유 pid 53056). 10:59:17.792Z `bound`. 그 사이 메인의 Work 호출은 `work_status` 1건뿐이었다. 따라서 이 재개는 메인 지시가 아닌 호스트 경로로 일어났다. 어떤 이벤트가 그 시각에 펌프를 깨웠는지는 엔진 debug 로그가 없어 확인하지 못했다.
- 포크는 10:59:16–11:04:34Z에 실행됐다(도구 34회). 운영 쓰기 전에 멈추고 `waiting_input`으로 보고했다: 시험 전용 승인·검수와 Alerts 행, 24시간 만료 정리 담당을 확인하지 못함. 현재 메인 답을 기다린다.
- n1847은 `Bob Ai reply with test-only AAP agent id on IT Hotline 157365` 대기다. n1834·n1835는 현재 보드에 노드가 없다.

# 남은 P0 리프가 실행되지 않는 근거

`_p0_runnable_leaves`는 리프나 조상에 `waiting_on`이 있거나 done/blocked이면 리프를 제외한다. 11:21Z 보드 기준 P0 루트 7개의 미완료 리프 전부가 대기를 갖거나 열린 리스를 갖는다.

| 루트 | 미완료 리프 | 대기를 가진 노드와 내용 |
|---|---|---|
| n1445 | n2594 | 열린 리스 `waiting_input`(위 포크) |
| n1756 | n1824·n1825·n1826 | n1823 `123240 SDK 검수 완료`, 이후 앞 단계 완료 연쇄 |
| n2442 | n2472·n2473 | `Walkie 156289 … merged to main`, n2472 완료 |
| n1936 | n1962·n2561·n2562·n2563·n2564 | 131641 r2 배포, `PR #113 … readback`, Bob 빌드, 배포, 운영 E2E |
| n2578 | n2582 | `… merge path verified by main` |
| n2584 | n2601·n2602 | `131641 (n2561) release readback first …`, n2601 완료 |
| n2624 | n2627·n2629·n2631·n2632 | Celly 경로 증거, 검증, Daniel 승인, 메인 검증 |

- 호스트 점검(11:13:22Z)도 같은 결과를 메인에 알렸다: `P0 0 goal blocked (n1445: its leaves already hold open leases; open leases: waiting_input 1; n1756: every remaining leaf waits or is blocked …)`, P1·P2·P3·P5 모두 `free slot but no runnable item`.
- 이 대기들은 메인이 보드에 쓴 조건이다. Work 런타임은 대기 문구가 실제 외부 요청과 맞는지 검사하지 않는다. 사용자가 지적한 n1823 `123240 SDK 검수 완료`는 Andy가 11:22:05Z에 "Daniel에게 재검수 요청은 보내지 않았다"고 확인했다. 즉 이 대기는 실제로 기다릴 외부 응답이 없다. 런타임 결함이 아니라 메인이 쓴 대기 조건의 오류다.

# Work 모드 자동 활성화

- 라우터 로그(현지 UTC−6): 10:57:55Z `claude_work_mode plugin_dirs=…ciel-work,…ciel-work-gate`로 claude.exe 시작. 모드가 10:58:11Z `work_status`, 10:58:27Z부터 `work_parallel_event`·`work_parallel_status`를 호출했다. 첫 사용자 입력(10:59:01Z)에는 이미 Work 규칙이 주입돼 있었다.
- 따라서 설정상 켜진 Work 모드는 세션 시작 후 약 16초 안에 동작했다. `Ciel Work mode is on` 안내 줄은 11:01:58Z에 따로 나왔다.

# 재시작 때 리스 상태

- 시작 고아 정리 로그: 4건 모두 `state=waiting_input owner=dead`. 즉 재시작 시점에 실행 중이던 포크는 없었고, 네 포크는 이미 메인 답을 기다리던 상태였다.
- n2594만 새 P0 intent(`continuation: initial`)로 다시 실행됐다. 시작 후 75초, 사용자 입력 후 9초다. 모드는 10:58:11Z부터 동작했으므로 그 사이 지연의 원인은 엔진 debug 로그가 없어 확인하지 못했다(미결).

# 우선순위별 집계

라우터 `priority_diagnosis`(11:31Z)와 보드에서 독립 재집계한 값이 일치한다.

| 우선순위 | 슬롯 | 사용 | 미완료 리프 | 대기·blocked | 계획 단계 | 열린 리스 | 후보 |
|---|---|---|---|---|---|---|---|
| P0 | 제한 없음 | 0 | 18 | 17 | 0 | 1 (`waiting_input`) | 0 |
| P1 | 2 | 0 | 78 | 68 | 8 | 2 (`submitted`) | 0 |
| P2 | 1 | 0 | 3 | 3 | 0 | 0 | 0 |
| P3 | 1 | 0 | 65 | 46 | 18 | 1 (`submitted`) | 0 |
| P5 | 1 | 0 | 53 | 43 | 10 | 0 | 0 |

- 집계 오류는 찾지 못했다. 빈 슬롯은 있지만 실행 가능한 리프가 0이다.
- 대기 보유 노드 예: n2401–n2405 `main session doing this itself`, n2577 `… read by main`, n2582 `… verified by main`, n2296 `Work host lease review/cleanup for n2303–n2308`, n594·n724·n721 blocked(옛 승인 소진 메모). 메인 자신의 작업을 대기로 적은 노드는 포크 실행 대상이 될 수 없다.
- 오래된 `submitted` 리스 20건이 메인 검수 대기(`validating`)로 남아 있다.

# 프리즈로 보인 구간

- 재시작 후 메인 요청 26건의 첫 응답 블록까지 시간: 중앙값 42 s, 최대 309 s(11:24:33Z → 11:29:41Z), 164 s(11:10:36Z → 11:13:20Z).
- 11:24:33Z 요청은 입력 489,863 토큰(1.8 MB, 메시지 181개)으로 CielAiRouter(`127.0.0.1:20128`, 원 모델 gpt-6.2-sol)에 갔다. 10:59:06Z 첫 요청은 304,797 토큰이었다. 26 s 뒤 스트림 프레임(ping 포함)이 들어왔고, 그 뒤 라우터는 `channel_turn_projection_persistent age_seconds=240/360`만 남겼다.
- 그 5분 동안 모델이 사고 토큰을 내고 있었는지, 업스트림이 멈췄는지는 라우터 로그로 구분되지 않는다(미확인). 세션은 계속 살아 있었고 11:29:41Z에 응답했다.

# 판정

- ✅ 큐는 REST와 웹 UI 양쪽에서 로드된다. 터미널 Work 창은 미관측.
- ✅ Work 모드는 세션 시작 직후 자동으로 동작했다.
- ✅ 재시작 때 실행 중인 포크는 없었다(4건 모두 `waiting_input`). 그중 실행 가능한 n2594는 호스트가 다시 띄웠다.
- ✅ 우선순위별 집계는 보드와 일치한다. 포크가 시작되지 않는 것은 미완료 리프 전부가 메인이 쓴 대기, 계획 단계(승인 전), 열린 리스에 묶여 있기 때문이다. 진행하려면 메인이 n2594의 질문에 답하고, 실제 근거가 없는 대기(n1823 등)와 `main session doing this itself` 같은 대기를 바로잡아야 한다.
- 이번 조사에서 입증된 제품 결함은 없다. 미결: n2594 재실행이 모드 시작보다 약 60초 늦은 이유, 309 s 응답 구간의 업스트림 상태.

# 함께 관측한 것

- Andy ciel-work 모드 재로드 11:19:22Z(`reloaded (19 hooks …`). 이 시점부터 fcce6d7 모드 코드가 Andy에서 동작한다. 라우터는 설치(11:04:48Z) 전에 시작됐으므로 라우터 쪽 fcce6d7 변경은 아직 라이브가 아니다.
