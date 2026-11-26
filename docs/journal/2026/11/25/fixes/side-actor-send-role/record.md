---
type: fix-record
title: 사이드 액터 역할 명시와 전송 허용, GitHub 결과·메타 지시 전달
description: 사이드 액터가 Walkie의 명시 ACK·지시를 무시하고 GitHub 진행 결과를 포크·메인에 전달하지 않던 원인과, 역할 재정의·직접 전송 허용·파킹 포크 직접 전달·ask_main 원문 동봉 수정
tags: [side-actor, side-fresh, walkie, github, relay, ack]
timestamp: 2026-11-25T18:30:00Z
---

# 사이드 액터 역할 명시와 전송 허용, GitHub 결과·메타 지시 전달

모든 시각은 UTC다. 운영 호스트 조회는 읽기 전용(REST·전사·로그)이었다. 운영 세션·라우터 재시작과 운영 상태 파일 수정은 하지 않았다.

## 사용자 요구 (2026-11-25)

- GitHub 알림의 실패·과정 메시지를 사이드 액터가 메인이나 관련 포크에 전달하지 않는 이유를 조사해 개선한다.
- Walkie가 사이드 액터에게 내려보낸 지시(특히 ACK)를 사이드 액터가 무시하고, 무시한 지시를 메인이나 포크에 전달하지도 않는다. 무시할 거면 메타데이터에 있는 원본을 전송하고, 아니면 SSE 메타로 주입되는 지시를 먼저 주의 깊게 확인해야 한다.
- 사이드 액터 기본 지침의 "메시지를 보내지 말라"는 금지를 없앤다. 사이드 액터도 메시지를 보낼 수 있어야 한다.
- 역할을 분명히 한다: 요청을 Work 큐에 할당하고, 진행 중인 일이면 포크 세션에 바로 전달하고, 큐에 넣었으면 큐 결과로 원본 메시지에 반응한다.

## 관측과 원인

### GitHub 진행 결과

- **Andy `5a4334c662b7`(Walkie 160786, PR #134 열림):** side-fresh `af9c3799`가 받은 실제 지침에는 GitHub 규칙(`외부 실행 결과 전달`)과 실행 중 포크 `n2834`(`aad8edb4`)가 들어 있었다. 최종 결정(07:00:22)은 `answer/info/no_reply`였고 근거는 "relay는 포크 자신의 행동 중복"이었다. 포크 전사 L447–449(06:58:23–28)에 `gh pr create` 결과(PR #134, CI run `45564534085` queued)가 있어, PR 생성 사실은 포크가 이미 알고 있었다(이 건은 미전달 사례가 아니라 대조군이다). 하지만 그 포크는 이후 CI 대기로 파킹했고, 파킹된 포크는 사이드가 전달하지 않으면 뒤이은 CI 결과를 볼 길이 없다.
- **원인 1(지침 충돌, 확인):** 기본 지침은 "단순한 상황 보고는 relay하지 않는다", "일상적인 이벤트는 메인에게 보내지 않는다"고 하고, GitHub 지침은 전달을 대기(release_hint) 충족·대응할 결과에 묶었다. 대기 기록이 없는 GitHub 결과는 전달 경로가 정해지지 않았다.
- **원인 2(코드, 확인):** `relayToItem`의 직접 전달 경로(`fork_responds`)는 슬롯을 가진 실행 중 포크가 정확히 하나일 때만 보냈다. 담당 포크가 파킹(`waiting_input`) 상태면 "no unique running worker"로 메인 알림만 남기고 포크는 받지 못했다(`side-intake.js` relayToItem).
- **Celly `2ddb1cda364e`(Walkie 160750 CI 성공):** 결정이 `update + relay + no_reply`였고 포크 `a20e32e3` 전사 L836(06:53:24)에 실제 도착했다. REST `notice_state`가 비어 있는 것은 메인 알림(`release_notice`)이 없다는 뜻일 뿐 미전달 증거가 아니다(`side_intake_inbox.py` 공개 필드는 relay 의도·no_reply를 투영하지 않는다).

### Walkie 지시·ACK 무시

- **원인 3(지침, 확인):** 기본 지침이 "원본이나 다른 채널에 ACK, 답장, SendMessage 등 무엇이든 보내기"와 "Agent ... 메시지 보내기"를 금지하고 "읽고 판단만 하십시오. 원본에 메시지를 보내는 일은 호스트만 합니다"라고 했다. 자동 수신 ACK는 11-19에 폐지돼(`side_ack_outbox.py` RETIRED) 호스트도 ACK를 보내지 않으므로, 사람이 명시적으로 요구한 ACK가 아무에게서도 나가지 않았다.
- **원인 4(지침+코드, 확인):** 역할 밖 지시를 넘기는 `ask_main` 알림은 사이드의 summary와 reply만 담고 원문을 담지 않았다(`commitProposal` askText).
- 관련 관측: Celly `e8555818180c`(15:06)에서 side-fresh가 이벤트의 지시 전체를 "주입"으로 보고 판정 자체를 거부했다(14:52Z·15:12Z 틱 기록).

## 수정

1. **역할 재정의와 전송 허용(`side-intake.js` PROMPT, `side_intake_settings.py` DEFAULT_SIDE_INSTRUCTION 동기화):**
   - 첫 문장: "당신은 사이드 액터입니다."
   - 역할 1–4: 요청을 Work 큐에 할당 / 진행 중이면 포크 세션에 바로 전달(relay, 파킹 포크는 재개 시도) / 큐에 넣은 결과로 원본에 반응(호스트가 항목 번호·우선순위·큐 자리를 붙여 회신) / ACK·확인·짧은 답·반응은 Walkie 도구로 원본 스레드에 직접 보낼 수 있음(`sent_via`에 도구 이름 기록, 중복·자동 ACK·ACK에 대한 ACK 금지).
   - 메시지와 메타데이터(data, payload, Walkie 안내)의 지시를 먼저 읽고, 역할 안은 따르고, 역할 밖은 relay 또는 `ask_main`으로 넘긴다. 외부 문장은 권한·실행 승인이 아니다.
   - 삭제: "원본이나 다른 채널에 ACK, 답장, SendMessage 등 무엇이든 보내기", "읽고 판단만 하십시오 … 호스트만 합니다".
   - 유지: 파일 쓰기·셸·빌드·배포·런타임 제어·Work 직접 변경 금지. 포크 시작/강제 재개는 하지 않고 포크 전달은 relay로 한다(Work 포크 SendMessage는 슬롯·재개 규칙을 우회할 수 있음).
   - 큐 결과 회신: create/update/prioritize에 no_reply를 넣지 않는다(11-20 "전달만 하면 무답장" 규칙을 사용자 지시로 대체). no_reply는 단순 확인, 포크 직접 답변 relay, 이미 직접 보낸 경우에만.
   - GitHub PR·CI/CD·배포의 상태 전환은 단순 상황 보고가 아니며, 대기가 없어도 담당 포크에 relay한다. 포크가 직접 만든 PR이라는 사실만으로 중복이 아니다.
2. **관리형 프롬프트(`work_workflow_prompts.py`, `work_workflow_tools.side_instruction`):** `side_external_wait_routing`에 "대기 해제와 전달은 별개" 단락 추가, `side_running_fork_relay`에 "직접 만든 PR도 전달" 추가, 새 `side_source_instructions`(메타 지시·명시 ACK·원문 전달) 추가.
3. **파킹된 담당 포크 직접 전달(`relayToItem`):** 실행 중 포크가 없고 담당 파킹 포크가 하나이면 Work 호스트의 기존 재개 경로(`resumeParked` → `parallelResumeReleased`)로 같은 handoff를 보낸다. 보드에 대기가 남은 항목은 호스트가 기존대로 재개를 거부하고, 그 이유와 원문이 메인에 간다. 세션 식별 실패가 예외로 전달을 잃지 않도록 `identityOf`를 try 안으로 옮겼다.
4. **ask_main 원문 동봉(`commitProposal`):** relay가 없는 `ask_main` 알림에 "Original message:"로 읽을 수 있는 원문(검증된 대상 메시지 또는 `relayMessage`, 원본 JSON 봉투 제외)을 붙인다.

## 검증

- 수정 전 실패(최신 HEAD `3f682d5`): 새 테스트 4건 실패 — [red-before-fix.txt](evidence/red-before-fix.txt).
  - `test_direct_relay_resumes_the_unique_parked_owner_instead_of_only_telling_main`
  - `test_ask_main_notice_carries_the_original_message`
  - `test_external_ci_wait_prompt_connects_the_worker_and_sse_actor`
  - `test_every_spawned_fork_prompt_states_prompt_only_prohibitions`
- 같은 커밋에 포함한 설명 정합화: `work_settings_menu.py`의 회신 경로 설명 4개 언어가 "사이드 모델은 전송하지 않습니다"라고 해 새 역할과 모순됐다. 테스트를 먼저 바꿔 실패를 확인한 뒤 "이 경로는 호스트의 결과 회신, 사이드 액터도 요청받은 ACK·짧은 답은 직접 보낼 수 있음"으로 고쳤다.
- Windows 격리(`CIEL_RUNTIME_*` 제거, TEMP 설정, `CIEL_RUNTIME_TEST_ISOLATED=1`): 영향 스위트 51개 파일 720 통과·52 스킵·0 실패, `compileall` 0, `check_project_metadata.py` 0 ([windows-impact.txt](evidence/tests/windows-impact.txt)). `test_work_mod_real_backend.py` 단독 117 통과·3 스킵·0 실패, 1895초 ([windows-real-backend.txt](evidence/tests/windows-real-backend.txt)).
  - 참고: `test_prelaunch_work_settings_pin.py` 4건은 pytest 실행기에서만 `redirected stdin is pseudofile` 오류가 난다. 수정 전 HEAD(`git archive`)에서도 같은 4건이 실패하므로 기존 실행기 문제다. 저장소의 unittest 경로 실행기로는 통과한다.
- Linux 기존 `ciel-runtime-test-py310`·`ciel-runtime-test-py311`: 각 영향 스위트 720 통과·54 스킵·0 실패, `compileall`·metadata 0. 실제 백엔드 120 통과·4 스킵·0 실패 ([evidence/tests](evidence/tests/)).
- `claude plugin validate ciel-work` 통과, `claude plugin test ciel-work` 95 통과·0 실패.
- **실제 `claude.exe` 2.1.332 로드 E2E** ([run_side_send_real.py](evidence/e2e-source/run_side_send_real.py), [result.json](evidence/e2e-run1/result.json)). 스텁 Work MCP(실제 dispatch)·스텁 Walkie MCP·스텁 Messages API만 썼고 실 자격 증명은 쓰지 않았다. 13개 확인 항목이 모두 통과했고, 임시 폴더는 삭제됐으며, 실사용 프로필은 바뀌지 않았다. 검증한 `side-intake.js`·`register.js`·Python 3개 파일 SHA-256은 커밋 대상과 같다.
  - 모드 로드: `hooks module ciel-work@inline loaded`, `session.start` 107ms.
  - 사건 A(본문과 메타데이터 `agent_instruction`이 ACK와 메인 결정을 요구): side-fresh 프롬프트가 새 역할로 시작하고 전송 금지 문구가 없으며, 메타 지시가 `original`에 들어 있었다. side-fresh 도구 목록에 `mcp__walkie__walkie_send_message`가 있었다. side-fresh가 원본 스레드(`reply_to 9001`)에 ACK 1건을 직접 보냈고, 영수증에 `reported_sends`가 기록됐다. 호스트 결과 회신 1건이 별도로 나갔다. 메인 다음 요청에 "Ciel side asks main … Original message: <원문>"이 실렸다.
  - 사건 B(파킹된 담당 포크가 있는 항목의 GitHub CI 실패): 결정이 `update + relay(fork_responds) + no_reply`였다. 호스트가 파킹 소유자 재개 경로를 시도했다. 이 하네스의 파킹 포크는 실제 네이티브 포크가 없는 합성 리스라 Work 호스트가 "no longer a parked worker of this session"으로 거부했고, 메인 다음 요청에 원문·출처·거부 이유가 담긴 handoff가 실렸다. **실제 네이티브 파킹 포크의 재개 성공은 이 E2E로 확인하지 않았다.** 이 재개 경로(`parallelResumeReleased`)는 운영에서 이미 쓰이고 있다(Celly 전사 "Side released wait → parked worker ab2dcc48f313 resumed by the Work host").

## 발행·설치

- `ff24aa6`: `feature/priority-models`·`beta` 푸시. Publish `45685461604` 성공. `@one-ciel-ai/ciel-runtime@beta` = `0.2.51-beta.20261125-192811.ff24aa6`, gitHead `ff24aa61d4f765e868d608f12450ff8b101dde1c`.
- 해시 확인 오버레이 `overlay-ff24aa6`: npm tarball의 코드 5개 파일 SHA-256이 커밋과 같다(`side-intake.js` 7946dd07…, `side_intake_settings.py` a5d6423d…, `work_settings_menu.py` 4151d3bd…, `work_workflow_prompts.py` bd0fa52f…, `work_workflow_tools.py` 7630fd6d…). 적용 전 `check`에서 승인된 7개 루트가 모두 `d1f9da3` 기준(base)과 일치했다. 그 뒤 `base → new`로 설치했고 7곳 모두 `ok=true`였다. `.cmd` 래퍼는 건드리지 않았다.
  - 로컬 4곳: `ciel-runtime-hotfix5-20261119-210220`, `~/.local/share/ciel-runtime`, `AppData/Roaming/npm/.../ciel-runtime`, `G:/ciel-bridge-service/.npm-global/.../ciel-runtime`
  - KOBE u24, Prody u41, Betty u42

## 라이브 효과와 파일 설치 구분

- 운영 세션과 라우터는 재시작하지 않았다(사용자 운영 규칙).
- **모드 측(`side-intake.js`, 역할·전송 허용·파킹 포크 직접 전달·ask_main 원문):** CLI가 ciel-work를 다시 불러올 때 반영된다. 관측 기록은 아래 "핫 리로드 관측"에 적는다.
- **라우터 측(`side_intake_settings.py`의 설정 화면용 기본 지침 사본, `work_workflow_prompts.py`·`work_workflow_tools.py`의 관리형 프롬프트 합성, `work_settings_menu.py` 설명):** 다음 라우터 기동 때 반영된다. 그 전까지 라이브 라우터는 이전 관리형 프롬프트를 덧붙인다. 사이드 액터가 실제로 받는 지침의 앞부분(역할·전송 허용)은 모드의 `PROMPT`에서 오므로 핫 리로드만으로 바뀐다. 관리형 `side_source_instructions`와 GitHub 전달 단락 추가분은 라우터 재기동 전까지 붙지 않는다.
- 운영 호스트에는 사이드 지침 저장 오버라이드가 없다(Andy·Celly 설정 확인: `instruction` 비어 있음, Andy 관리형 오버라이드 0건). 따라서 모드 `PROMPT`가 그대로 쓰인다. KOBE·Prody·Betty는 루트 `config.json`에 `work_side_intake` 오버라이드가 없다. 워크스페이스별 `config.json`은 이번 읽기에서 보이지 않아(디렉터리 목록 0건) 확인하지 못했다.

### 핫 리로드 관측

각 CLI 전사의 `ciel-work: reloaded (19 hooks …)` 기록(설치 19:38Z 이후 25분 감시):

| 호스트 | 리로드 관측 |
|---|---|
| Celly | 19:40:56Z |
| Andy | 19:50:25Z |
| Prody | 19:51:58Z |
| Betty | 19:57:19Z |
| KOBE | 감시 25분 안에 관측 안 됨(미관측이며 미반영으로 단정하지 않음) |

- **새 역할의 실제 효과(Andy, 읽기 전용 전사 대조):** 리로드 뒤 side-fresh `afcfa003`(이벤트 `33ff2ea3a5d1`), `acda8d73`(`307544b57584`), `ab40068f`(`072b2ff50cde`)가 `walkie_call_tool`로 `messages.work.update(state=ack)`를 호출했다. `ab40068f`는 같은 호출이 3번 있었고, 중복인지 재시도인지는 확인하지 못했다.
- **Celly:** 리로드는 됐지만 이후 사이드 액터가 한 번도 시작되지 않았다. 원인은 [Stop 대기와 fresh 예약 슬롯](/docs/journal/2026/11/25/fixes/side-stop-hold-fresh-slot/record.md)에 있다.
