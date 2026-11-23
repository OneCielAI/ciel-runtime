---
type: fix-record
title: Side 교정 메시지가 액터에 도달하지 못해 첫 거부에 격리되던 결함
description: Celly side 이벤트가 연속 실패(invalid_answer)로 격리된 원인. 호스트의 교정 전송이 엔진의 resume agent.spawn으로 들어오고 register.js가 자체 side 네임스페이스 가드로 거부해, 교정이 한 번도 전달되지 않았다. 증거, 실패 테스트, 수정, 검증.
tags: [work-mode, side-intake, correction, celly, real-load]
timestamp: 2026-11-23T10:00:00Z
---

# 사용자 보고

Celly 보고(Image #24, #25): 사이드 액터가 외부 이벤트를 계속 처리하지 못해 메인이 원문을 직접 받는다. 실패 분류는 `invalid_answer`, 1회 시도.

# 운영 증거 (읽기 전용, Celly 라우터 REST `127.0.0.1:9385/ca/work/api/v1/side`, 세션 4ee83c21)

- 영수증 63건: processed 48, quarantined 13(+superseded 1 포함), failed 0.
- 격리 사유(영수증 `failure.reason`):
  - 7건: `work_side_understanding: decision refs required: give the source ids in refs ...` — 메시지 스레드 이벤트(`source_context=thread`)에서 queue 결정에 refs 누락.
  - 5건: `side answer carries no decision JSON` — 주로 게이트 재점검(`not_walkie`). 최종 답이 "할 일 없음", 산문, 펜스만 있는 경우.
  - 1건: `superseded by a newer gate re-check`(정상).
- 전부 `attempt_count 1`, `quarantined true`. `invalid_answer`는 `side_intake_processing.fail`에서 즉시 영구 격리된다(`permanent = category in ('invalid_request','invalid_answer')`).
- 실패한 side 액터 대화 15개(Celly `subagents/agent-*.jsonl`, `ciel-work:side-fresh`)에 교정 문구 `your answer was refused`가 0건. 지난 8시간 Celly 프로젝트 전체 jsonl 265개에서도 0건.
- 따라서 "한 번 교정 후 실패 집계"(2026-11-18 Andy 요구, `correctSide`)가 Celly에서 한 번도 동작하지 않았다. 두 번의 교정 시도(즉시·1.5 s 뒤)가 모두 전달되지 않으면 `correctLater`가 `invalid_answer`를 기록한다. 이것이 attempt 1 격리와 일치한다.
- Celly에는 엔진 debug 로그가 없어 Celly에서 거부 문구를 직접 보지는 못했다.

# 원인 (격리 실엔진에서 관측, Claude Code 2.1.330 — Celly와 같은 버전)

- 실행 3~5([side-fresh-retirement](/docs/journal/2026/11/23/fixes/side-fresh-retirement/record.md))에서 교정 전송 직후 엔진 로그: `agent.spawn ciel-work:side-fresh: refused by a hook (Side SSE native fork authorization did not match its pending host record.)`, `$.session.send ... not delivered: a plugin declined the resume`.
- 엔진은 끝난(또는 idle) 에이전트에 보내는 메시지를 `agent.spawn`의 `resume.agentId`로 전달한다. 이 resume은 액터 자신의 description `Side SSE <id>`를 가진다.
- `register.js` `parallelAgentSpawn`는 `sideSpawnInput`이 pending 행을 찾지 못하면(pending은 최초 spawn 직후 지워짐) `Side SSE ` 접두사를 무단 side spawn으로 보고 거부한다(`register.js:3383`). 호스트 자신의 교정 resume까지 거부된 것이다.

# 수정

- `side-intake.js`: `correctionResumes` 집합. `sendCorrection`이 전송 동안만 해당 액터 id를 넣고 끝나면 뺀다.
- `sideSpawnInput`: `e.resume.agentId`가 진행 중인 교정 전송 대상이고, `next.origin.plugin === 'ciel-work'`이며, 액터가 이 호스트 소유(`ownedSideActors`)이고 retired가 아니며 현재 이벤트가 있을 때만 허용한다. 그 밖의 side 네임스페이스 resume·spawn은 기존대로 거부한다.
- 실패 테스트 먼저: `test_correction_resume_of_the_owned_side_actor_is_delivered`(`work_mod_harness` 시나리오 `side-correction-resume-of-owned-actor`). 수정 전: 전송 2회 모두 거부, `invalid_answer` 1건 — Celly 패턴 그대로. 수정 후: 첫 전송 전달, 실패 0, 다른 id의 같은 resume은 계속 거부.

# 검증

- Windows: `test_work_mod_write_gate.py`, `test_side_actor_failures_hotfix.py`, `test_side_intake_mod.py` 223 통과/33 skip.
- 실엔진(Claude Code 2.1.330, 드라이버 [run_side_stop_real.py](/docs/journal/2026/11/23/fixes/side-fresh-retirement/evidence/e2e-source/run_side_stop_real.py)):
  - 실행 6: 교정이 첫 시도에 전달(`asked the side actor for 331a083ada9d ...`), `not delivered`·resume 거부 0, 교정 답이 `processed`로 커밋. 정지 검사 하나는 드라이버 변수 덮어씀으로 잘못 false였다.
  - 실행 7·8(드라이버 수정 후): 15/15 통과. 실행 8은 최종 코드(`register.js` 674f6c57…, `side-intake.js` 2c4d1a73…, `texts.generated.js` d3403348…) 기준.
- 영향 회귀 `sidecorr.095712`(능력 분리 전 코드): py310·py311 각 811 실행/0 실패/58 skip. 최종 트리 `final.102159`: 결과는 릴리스 기록에 적는다.

# 남은 것

- 교정이 전달되면 refs 누락·JSON 누락 답은 한 번 다시 답할 기회를 얻는다. 두 번째 답도 틀리면 지금처럼 격리된다. 모델이 교정에 맞게 답하는지는 운영 반영 후 관측해야 한다.
- 운영 반영에는 설치 후 Celly 세션 재로드가 필요하다(재시작 금지 범위).
