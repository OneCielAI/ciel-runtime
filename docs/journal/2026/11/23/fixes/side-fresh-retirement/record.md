---
type: fix-record
title: Side 액터 정지 거부 후 추적 유지와 실제 엔진 검증
description: TaskStop 거부에도 Side 액터 추적을 지우던 결함의 수정, 실제 claude.exe 격리 검증에서 드러난 두 결함(스킬 이름 충돌로 session.start 건너뜀, 이미 끝난 액터에 대한 무의미한 TaskStop 재시도)과 그 수정.
tags: [work-mode, side-intake, side-fresh, taskstop, real-load, celly]
timestamp: 2026-11-23T09:00:00Z
---

# 배경

계획 파일 `starry-brewing-candle.md` 범위: `side-intake.js`의 `retire`/`stopRetired`가 `TaskStop` 거부·예외에도 추적을 지웠다. 앞선 수정(11-22)은 정확한 대상의 정지 응답과 native 목록의 종료/부재가 둘 다 확인될 때까지 추적을 유지하고, 1초×시도 간격으로 최대 3회 재시도한다. 하네스 검증만 있었고, 실제 CLI에서 정지 거부 후 회복은 검증되지 않아 배포를 보류했다.

# 실제 엔진 검증 방법

- 드라이버 [run_side_stop_real.py](evidence/e2e-source/run_side_stop_real.py): 설치된 Claude Code 2.1.330이 작업 트리 스냅샷의 `ciel-work`·`ciel-work-gate`와 테스트 전용 모드 [stop-fault](evidence/e2e-source/stop-fault/hooks/register.js)를 `--plugin-dir`로 로드한다. MCP는 실제 Work·side 디스패치(`dispatch_work_tool`)이고 Messages API는 스크립트 대역이다. 실제 자격 증명은 쓰지 않는다. 스크래치는 `D:/Temp` 아래이며 실행 후 지운다. 이 드라이버가 시작한 프로세스 트리만 종료한다.
- stop-fault는 ciel-work의 `$.tool.call` TaskStop을 듣는다($ 호출은 호출한 훅을 뺀 모든 훅을 지난다, `claude-code.d.ts` Tier 문서). 처음 본 task id의 TaskStop을 두 번 거부하고, 모든 호출과 결과, 엔진 목록상 상태를 debug 로그에 남긴다.

# 실행 1 — 스킬 이름 충돌 (session.start 건너뜀)

- 증거: [run1-skill-conflict](evidence/run1-skill-conflict/result.json).
- 08:37:35Z `session.start hook skipped: threw ciel-work: $.command.register: "/work-mode" refused: it is the plugin's /ciel-work:work-mode`.
- 이번에 추가한 스킬 `skills/work-mode`가 플러그인 명령 `/work-mode`와 이름이 겹쳤다. session.start 전체가 건너뛰어져 사이드 시작과 폴링이 없었고, 두 이벤트 모두 `received`로 남았다. `claude plugin validate`와 `claude plugin test`(94 통과)는 이 충돌을 잡지 못했다.
- 수정: 스킬 이름을 `work-operations`로 바꿨다. 실패 테스트 `test_no_bundled_skill_takes_a_registered_command_name`을 먼저 추가했고, 수정 전 교집합 `work-mode`로 실패했다.

# 실행 2 — 이미 끝난 액터에 TaskStop 재시도

- 증거: [run2-fresh](evidence/run2-fresh/result.json).
- 두 이벤트 모두 `processed`. 이벤트 2는 액터 1의 정지가 거부되는 동안 처리됐다.
- 엔진 관측: 액터는 `agent_completion ... exitPath=completed`(08:45:44.083Z) 뒤에 retire된다. 거부 시점 목록 상태는 이미 `completed`였다. 거부가 아닌 호출도 `Error: Task <id> is not running (status: completed)`(`isError: true`, `task_id` 없음)를 돌려줬다.
- 결함: 앞선 수정은 확인 조건으로 `result.task_id === id`를 요구했다. 그래서 끝난 액터가 확인되지 않았고, 3회 재시도 뒤 `actor cleanup unconfirmed` 로그를 남겼다. 추적(`retiredSideActors`, `actors`)은 이벤트마다 남아 계속 늘어난다.
- 수정(`side-intake.js`): `sideActorEnded`는 엔진 목록에서 부재 또는 completed/failed/killed이면 종료로 본다. 목록상 살아 있는 액터에만 TaskStop을 호출한다. TaskStop 응답과 무관하게, 호출 뒤의 목록으로 확인한다. 거부되거나 실패해 여전히 살아 있으면 추적을 유지하고 재시도한다.
- 실패 테스트 먼저: `test_a_completed_side_actor_is_retired_without_stop_retries`(수정 전 TaskStop 3회 발생).
- 하네스 조정: `side_intake_harness.mjs`와 `side_fresh_harness.mjs`의 agent.list를 `[]`에서 "생성된 액터는 TaskStop 전까지 running으로 목록에 남음"으로 바꿨다. 이벤트마다 정지 1회라는 기대는 그대로다. `side_cleanup_harness` 기대는 이미 끝난 `failed`·`completed` 액터를 정지 대상에서 빼고 `idle-side`만 남겼다. 외부 fork는 계속 건드리지 않는다.

# 실행 3·4 — 살아 있는 액터의 정지 거부와 회복

- 방법: 대역이 액터 1의 응답을 붙잡아 running 상태로 유지한다. 스냅샷 register.js 끝에 주석을 붙이면 엔진의 plugin-dir watch가 ciel-work를 핫 리로드한다(`hooks module ciel-work@inline reloaded`). 새 session.start의 `startSide`가 목록상 살아 있는 소유 액터를 정지한다. stop-fault는 그 TaskStop을 두 번 거부한다.
- 실행 3([run3-reload-live](evidence/run3-reload-live/result.json)): 정지·처리 검사는 모두 충족했다. 다만 드라이버가 재로드를 `loaded` 행으로 셌는데, 엔진은 재로드를 `reloaded`로 기록한다. 그래서 `hot_reloaded=false`가 나왔다(판정식 오류). 재로드 판정을 두 번째 `session.start settled`로 고쳐 다시 돌렸다.
- 실행 4([run4-reload-live](evidence/run4-reload-live/result.json)), `passed: true`, 13개 검사 전부 충족:
  - 09:01:15.873Z ciel-work 재로드, 09:01:15.968Z session.start 완료.
  - 액터 `aca6a71a…` TaskStop 거부 09:01:16.115Z·09:01:17.125Z. 두 번 모두 엔진 목록상 `running`.
  - 세 번째 TaskStop 09:01:19.176Z 통과: `Successfully stopped task`, 목록 `running → killed`, 엔진 `agent_completion exitPath=cancelled`.
  - `actor cleanup unconfirmed` 0건. 스스로 끝난 액터에 대한 TaskStop 0건.
  - 이벤트 2: 첫 거부 직후 투입, 18.0 s에 `processed`. 액터 1을 재시도하는 동안 처리가 진행됐다.
  - 이벤트 1: 정지된 액터는 답하지 않았고, claim 리스(120 s) 만료 뒤 재클레임돼 130.8 s에 `processed`.
  - 스냅샷 해시가 소스와 일치(`side-intake.js` 20f0d781…, `register.js` 80856d69…, `texts.generated.js` 9297e094…). 라이브 `~/.claude/settings.json`과 ciel-runtime bin은 불변. `~/.claude.json`은 이 머신의 다른 라이브 Claude Code 세션도 쓰는 파일이라 판정에서 제외하고 변경 사실만 기록했다(이 실행은 자체 `CLAUDE_CONFIG_DIR` 사용).

# 관측했지만 이번 변경과 무관한 것 (미결)

- 실행 2·3·4 모두에서, 대역이 게이트 재점검 이벤트에 `ack`로만 답하자 호스트가 교정 메시지를 보냈다. 첫 전달은 `$.session.send`가 액터를 재개할 때 ciel-work 자신의 `agent.spawn` 거부(`register.js:3383`, "Side SSE native fork authorization did not match its pending host record.")로 막혔고, 1.5 s 뒤 재시도로 전달됐다. 이번 변경이 건드리지 않은 경로다. 첫 거부가 의도된 것인지는 확인하지 않았다.
- 실행 4·5에서 재로드 직후 이벤트 2의 첫 claim이 `work_side_fail`로 끝났고 5 s 뒤 재클레임으로 처리됐다. 실행 5([run5-reload-live](evidence/run5-reload-live/result.json), `passed: true`, 13개 검사 충족)에서 사유를 수집했다: `processing_error: Agent type 'ciel-work:side-fresh' not found`. 엔진 로그를 보면 09:05:39.932Z 재로드 후 09:05:41.065Z `$.agent.spawn`이 `AgentTypeError`로 실패했고, 그 사이에 `$.agent.register`가 없었다. 다음 등록은 09:05:46.373Z였다. 엔진 문서는 "unloaded, a plugin's types go"라고 한다. 모듈 변수 `freshRegistered`가 true인 사본이 그 호출을 했다는 것까지는 관측과 맞는다. 그 사본이 재로드 전 사본의 남은 타이머인지, 캐시된 `side-intake.js`인지는 구분하지 못했다. 이벤트는 유실되지 않았지만 실패 1회가 격리 한도(6회)에 들어간다. 원인 경로를 확인하기 전에는 수정하지 않는다(미결).
- 이벤트 `0bb483e0…`의 `invalid_answer`는 대역이 게이트 재점검 이벤트에 `ack`로 답한 결과다. 검증 드라이버의 산물이고 제품 판정 대상이 아니다.

# 검증

- 실패 테스트 먼저: `test_no_bundled_skill_takes_a_registered_command_name`, `test_a_completed_side_actor_is_retired_without_stop_retries`. 둘 다 수정 전 실패, 수정 후 통과.
- Windows 격리: `test_side_intake_mod.py`, `test_work_mod_write_gate.py`, `test_work_texts.py`, `test_side_real_backend.py` 220 통과/33 skip.
- `claude plugin validate` 통과(`skills: ciel-work:work-operations`), `claude plugin test ciel-work` 94 통과. JS는 `.mjs` 사본으로 `node --check` 통과.
- 영향 회귀 `sidestop.085650`(26개 파일): Windows·py310·py311 각 793 실행/0 실패/58 skip.
- 실행 5(13/13), 실행 7·8(교정 검사 포함 15/15) 통과. 결과 파일은 `evidence/run*/result.json`.
