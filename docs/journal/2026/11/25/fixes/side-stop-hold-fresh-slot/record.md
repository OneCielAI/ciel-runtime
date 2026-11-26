---
type: fix-record
title: Stop 대기 중 사이드 액터 거부 처리, fresh 예약 슬롯, 원본이 정한 ACK·memo
description: Celly 사이드 액터가 Stop 뒤 시작되지 못해 격리·실패 회신이 나가던 원인, Andy에서 P0가 일반 액터 뒤에 묶이던 원인, 원본 지정 ACK·memo 규칙과 그 수정·검증
tags: [side-actor, side-fresh, stop-hold, walkie, ack, priority]
timestamp: 2026-11-25T21:00:00Z
---

# Stop 대기 중 사이드 액터 거부 처리, fresh 예약 슬롯, 원본이 정한 ACK·memo

모든 시각은 UTC다. 운영 호스트 조회는 읽기 전용(REST·전사·로그·엔진 바이너리 문자열)이었다. 운영 세션·라우터 재시작과 운영 상태 파일 수정은 하지 않았다.

## 사용자 요구 (2026-11-25)

- Celly #161253 화면: "셀리의 사이드액터가 대답할 필요 없다. 단지 ack + memo를 했어야 한다. 여전히 주어지는 SSE 메시지의 명령을 제대로 따르지 못한다."
- "앤디 세션에서는 SSE 메시지가 와도 side actor가 아예 시작도 못하는 일이 생기는데?"

## 관측과 원인

### Celly: 사이드 액터가 한 번도 시작되지 못함 (확인)

- REST 영수증(16:30Z 이후 수신분): `processing_error` 46건이 모두 같은 사유 "the session was stopped and nobody has sent input since"였다. 첫 실패 18:19:32, 마지막 20:20:52, 각 6회 시도 뒤 격리됐다. 그중 P0가 20건 넘게 있었다. 격리 뒤 실패 회신이 Walkie에 27건 제출됐다(`delivery=submitted`). 사용자가 본 #161253 "이 메시지는 사이드 처리에서 자동으로 처리하지 못했습니다. 원문을 메인 세션에 그대로 전달했고…"가 이 회신이다(`side_reply_outbox.FAILURE_TEXT`).
- 이 문구는 Claude Code 엔진이 낸 것이다. `claude.exe` 2.1.332 바이너리의 `yWt()`가 Stop 대기(`Fee`)가 서 있으면 이 사유를 돌려주고, `$.agent.spawn`은 그 사유로 `{deny}`를 반환한다. 대기는 Stop(`mrr()`)이 세우고, 사람 본인의 입력(`NJo` → `dfs()`)만 푼다. plugin-authoring 형식 문서도 같다: `agent.spawn` "past a Stop or a host's limit nothing is spawned", `session.stop` "input it queues is refused until it returns or the person sends input".
- Celly 메인 전사 `9430f5e8`: 17:46:27.036 큐 dequeue → 17:46:27.089 `[Request interrupted by user]` → 17:46:27.163 `/compact`(같은 promptId)이 이어졌다. 18:06:08 압축 완료 뒤 사람 입력이 없었다(20:20까지). 라우터 로그에는 이 시각에 `channel_compact_request_injected`가 없어, 이 `/compact`를 라우터가 입력한 것은 아니다. 누가 입력했는지는 확인하지 못했다.
- 같은 대기 때문에 메인으로 보낸 원문도 읽히지 않았다. 전사의 `plugin_note` 첨부가 `content: []`, `heldContent: ["[Ciel side 처리 실패 · 원문 전달 …"]` 형태였고, 17:43:25 이후 ciel-work의 메인 깨우기 프롬프트가 한 건도 들어가지 않았다. 따라서 실패 회신의 "메인 세션에 그대로 전달했고"는 실제로는 보류된 상태였다.
- **결함(확인):** 모드가 이 거부를 일반 `processing_error`로 기록해 시도 횟수를 쓰고, 6회 뒤 격리하고, 메인 전달과 실패 회신을 냈다. 아무것도 실행되지 않은 거부를 실패로 센 것이다.

### Celly: ACK·memo 지시를 따르지 않은 것처럼 보인 이유 (확인)

- 이벤트 `56c59cd96fb4`(Walkie 161250)의 `data.agent_context.guidance`는 "받자마자 messages.work.update(conversation_id, message_id="161250", state="ack", memo=…)로 ACK와 메모를 먼저 보낸다"였다. 이 이벤트는 19:26:17 수신, 19:32:14에 같은 Stop 대기 거부 6회로 격리됐다. 사이드 액터가 실행된 적이 없으므로 지시를 읽을 기회도 없었다.
- 대조(Andy, `ff24aa6` 리로드 뒤): side-fresh `afcfa003`(이벤트 `33ff2ea3a5d1`), `acda8d73`(`307544b57584`), `ab40068f`(`072b2ff50cde`)는 `walkie_call_tool`로 `messages.work.update(state=ack)`를 실제로 불렀다.
- 남은 지침 문제(확인): `ff24aa6` 지침은 ACK 예시로 `walkie_send_message`(글 올리기)만 들었고, 큐에 넣으면 호스트가 결과 회신을 따로 보내도록 했다. 원본이 "ACK와 memo"를 정해 둔 경우에도 별도 답이 나갈 수 있었다.

### Andy: SSE가 와도 사이드 액터가 시작되지 않음 (확인)

- 세션 모드 `fresh`(`ciel-work:side-fresh`). 영수증 순서: 게이트 재확인 `37c96f83777e` 클레임 19:46:58 → 처리 20:01:09(14분). P0 `33ff2ea3a5d1` 수신 19:50:50 → 클레임 20:01:14(10분 24초 대기, 앞 건 처리 5초 뒤). P0 `307544b57584` 수신 19:57:28 → 클레임 20:03:12(앞 건 처리 20:03:08 직후). P1 `072b2ff50cde` 수신 19:45:10 → 클레임 20:12:49(27분 39초). 모든 클레임이 앞 건 처리 6초 안에 이어졌다.
- 원인: `faa2c7e`(KOBE 2–3개 중첩 대응)가 fresh 모드에서 활성 액터를 엄격히 1개로 묶었다. 모드는 `running.length >= 1`이면 클레임하지 않았고, 라우터 `work_side_claim`도 살아 있는 fresh 리스가 하나라도 있으면 `urgent_only` 클레임까지 거부했다. 그래서 P0/P1 예약 슬롯이 fresh 모드에서 없어졌다. 사용자 요구 "P1 이벤트를 먼저 처리"(2026-11-14)와 충돌한다. 이 세션의 사용자 메시지에는 "fresh 액터 1개만"이라는 지시가 없었고, `faa2c7e` 기록의 근거는 KOBE 중첩 관측이었다.

## 수정

1. **Stop 대기 처리(`side-intake.js`, 모드 측):** `$.agent.spawn` 거부 사유가 엔진의 Stop 대기 문구이면 `SpawnHeld`로 구분한다. 실패를 기록하지 않는다(`work_side_fail` 호출 없음, 시도 0, 격리·메인 전달·실패 회신 없음). 대기 동안 클레임을 멈추고, 한 번 `Side actors paused: … any input you type in this session resumes them.`을 남긴다. 사람 입력(`prompt.submit`의 origin `composer`/`bridge`, `isMeta` 아님; `register.js`가 `sidePersonInput` 호출) 뒤 3초 후, 또는 5분마다 한 번 다시 시도한다. 거부된 클레임의 리스가 10초 넘게 남아 있으면 새 클레임 없이 그 클레임으로 다시 시도한다(리스가 끝나면 라우터가 같은 호스트에 돌려준다). 생성에 성공하면 `Side actors resumed`를 남기고 대기를 푼다. 엔진의 대기는 사람의 Stop이므로 우회하지 않는다.
2. **fresh 예약 슬롯(`side-intake.js`, `side_intake_mcp.py`):** fresh 모드에서 일반 액터는 여전히 1개다. 하나가 돌고 있으면 `urgent_only` 클레임(P0/P1만 받음)으로 하나를 더 시작할 수 있고, 둘이 돌면 클레임하지 않는다. 라우터도 같은 규칙이다(살아 있는 리스 2개 이상이면 거부, 1개면 `urgent_only`만 허용).
3. **원본이 정한 ACK·memo(`side-intake.js` PROMPT, `side_intake_settings.py` 동기화, 관리형 `side_source_instructions`):** 원본·메타데이터(예: `data.agent_context.guidance`)가 ACK 방법을 정했으면 그대로 한 번 한다. `messages.work.update(state="ack", memo)`라면 `walkie_call_tool`로 operation_id `"messages.work.update"`를 부르고, memo에 처리 결과(큐 등록, 포크 전달, 메인에 넘김)를 적고, 따로 답하지 않는다(`"no_reply": true`, `sent_via: ["mcp__walkie__walkie_call_tool"]`). 요청 자체의 답은 맡은 메인·포크가 한다. 큐 결과 회신 규칙에 "원본이 ACK 방법을 정했으면 그 memo가 반응"을 더했다. 이 변경은 모드 코드 안의 프롬프트 상수(`PROMPT`)를 고친 것이다.

## 검증

- **수정 전 실패(최신 HEAD `ff24aa6`):** 새 테스트 4건 실패 — [red-before-fix.txt](evidence/red-before-fix.txt).
  - `test_stop_hold_pauses_claims_without_counting_attempts`: 거부마다 `work_side_fail(processing_error)` 호출.
  - `test_fresh_mode_keeps_the_reserved_p0_p1_slot`: fresh에서 예약 클레임 없음(`[False]`).
  - `test_fresh_session_keeps_the_reserved_p0_p1_claim_behind_one_live_claim`(라우터): 살아 있는 fresh 리스 1개에서 `urgent_only` 클레임이 None.
  - `test_every_spawned_fork_prompt_states_prompt_only_prohibitions`: 지침에 `data.agent_context.guidance`·`messages.work.update` 규칙 없음.
- 같은 변경으로 기존 `test_fresh_side_runs_single_active_actor…`의 기대값을 바꿨다. 이전 기대는 "P1도 두 번째 fresh 액터를 열지 않음"이었고, 이번 수정이 의도적으로 뒤집은 동작이다. 하네스 클레임 스텁이 `urgent_only`를 라우터처럼 따르게 고쳤다. 일반 이벤트는 여전히 하나만 돈다(검증).
- `claude plugin validate ciel-work` 통과, `claude plugin test ciel-work` 95 통과·0 실패([plugin-test.txt](evidence/plugin-test.txt)). `compileall` 0, `check_project_metadata.py` 0.
- **실제 `claude.exe` 2.1.332 로드 E2E** ([run_stop_hold_real.py](evidence/e2e-source/run_stop_hold_real.py), [result.json](evidence/e2e-run3/result.json), [debug-side-lines.txt](evidence/e2e-run3/debug-side-lines.txt)). 실제 Work·side 디스패치(소유 상태 폴더, `session: fresh`), 스텁 Walkie MCP, 스텁 Messages API만 썼다. 실 자격 증명은 쓰지 않았고, 임시 폴더는 삭제됐으며, 실사용 프로필 해시는 바뀌지 않았다(`~/.claude.json`만 바뀜, 이전 실행과 같은 엔진 기록). 검증한 모드 2개·Python 3개 파일 SHA-256이 작업 트리와 같다. 18개 확인 항목이 모두 통과했다.
  - 1단계(Stop 대기): 입력한 메인 턴을 ConPTY로 Esc를 보내 멈췄다. 엔진이 0.06초 만에 메인 요청을 닫았다. 44.2초에 `agent_context.guidance`로 `messages.work.update(state="ack", memo)`를 요구하는 Walkie 이벤트를 넣었다. 디버그 로그: `$.agent.spawn (ciel-work): refused: the session was stopped and nobody has sent input since` → `Side actors paused …`. 대기 중 `work_side_fail` 0회, 영수증 `side_failure` 없음, 사이드 모델 요청 0회.
  - 70.2초에 사람 입력("resume please")을 넣었다. 5.2초 뒤 side-fresh가 시작됐다(`Side actors resumed`). 그 액터가 `walkie_call_tool` `messages.work.update {message_id 9201, state ack, memo "Work 큐에 새 항목으로 등록했습니다(사이드 액터)."}`를 한 번 불렀고, `walkie_send_message`는 0회였다. 영수증: `create`, `no_reply=true`, `reported_sends=["mcp__walkie__walkie_call_tool"]`, 호스트 회신 없음(`delivery=not_applicable`).
  - 이전 실행(e2e-run1·2, 기록 생략)은 거부된 클레임의 리스(120초)를 기다려 입력 뒤 95초가 걸렸다. 그래서 수정 1의 "리스가 남은 클레임 재시도"를 더했고, run3에서 5.2초를 확인했다. 같은 실행들의 Esc 확인은 하네스가 전사 문구를 잘못 찾아 실패했다. 스텁 서버가 클라이언트 종료 시각을 직접 기록하도록 바꿨다.
  - 2단계(예약 슬롯): 일반 이벤트 B의 side-fresh를 스텁 모델이 붙잡아 둔 동안(87.7초 시작) P0 C를 넣었다. C는 `urgent_only` 클레임(89.7초)으로 B가 끝나기 전에 시작돼 먼저 처리됐다(90.0초). B는 그 뒤(90.3초) 처리됐다.
- **결과 불명·미확인:** 실제 운영에서 Stop 뒤 사람 입력으로 재개되는 장면은 아직 보지 못했다. 원본 지정 ACK·memo를 실제 Walkie 서버가 받는지는 스텁으로만 확인했다(Andy 운영 전사에서 같은 호출이 이미 쓰이고 있다).

- **Windows 격리**(`CIEL_RUNTIME_*` 제거, TEMP 설정, `CIEL_RUNTIME_TEST_ISOLATED=1`): 영향 스위트 63개 파일 1042 통과·52 스킵·0 실패 ([windows-impact.txt](evidence/tests/windows-impact.txt)).
  - `test_work_mod_real_backend.py`: 117 통과·1 실패·3 스킵 ([windows-real-backend.txt](evidence/tests/windows-real-backend.txt)). 실패는 `idle-web-rework-late` 시나리오다. 시험용 임시 저장소의 `git rev-parse`가 30초 제한을 넘었다(228초 경과). 같은 트리에서 그 시나리오만 다시 돌리니 통과했다(162초). `ff24aa6` 실행에서도 통과한 시나리오이고, 이번 변경은 git 작업 경로를 건드리지 않았다. 그래서 실행 중 부하로 인한 시간 초과로 판단한다. 다만 원인은 재현으로 입증하지 않았다.
- **Linux 기존 `ciel-runtime-test-py310`·`py311`:** 영향 스위트 각 1094 통과·100 스킵·0 실패, 실제 백엔드 각 120 통과·4 스킵·0 실패, `compileall`·metadata 0 ([evidence/tests](evidence/tests/)).

## 발행·설치

발행·해시 확인 설치·라이브 반영 관측은 [Work 검수 기록의 완료 행 보존 한도](/docs/journal/2026/11/25/fixes/finalization-retention/record.md)와 같은 배포로 나가며, 그 기록의 "발행·설치"에 함께 적는다.
