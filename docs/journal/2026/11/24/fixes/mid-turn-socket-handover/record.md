---
type: fix-record
title: 실행 중 턴에 들어간 소켓 입력을 실패로 기록하던 결함
description: ciel-work 모드가 적재된 Claude Code 2.1.331에서 턴 중에 받은 세션 소켓 입력을 라우터가 20초 뒤 unseen_failed로 기록하던 결함의 증거, 원인, 수정, 검증
tags: [channel, session-socket, betty, claude-code-2.1.331]
timestamp: 2026-11-24T07:35:00Z
---

# 실행 중 턴에 들어간 소켓 입력을 실패로 기록하던 결함

## 관측 (Betty, 읽기 전용, 2026-11-24 06:13–06:30Z)

- Betty는 side 액터를 쓰지 않는다. SSE 메시지는 Claude Code 메시징 소켓으로 메인에 바로 들어간다.
- 06:13Z 라우터 로그
  - `channel_windows_console_unseen_failed message_id=7867`(06:13:04Z), `7866`(06:13:24Z)
  - `channel_stdin_proxy_blocked ... reason=prior_submission_failed detail=stale_unconfirmed action=skipped` ERROR 2건
- 같은 메시지의 Betty 대화 기록(Claude Code 2.1.331)
  - 06:12:43.812Z, 06:12:44.987Z: `queue-operation enqueue`. `content`가 없다.
  - 06:13:36Z: `queued_command` 첨부 2건(타임스탬프는 enqueue 시각). 그 뒤에 `queue-operation remove reason=absorbed_mid_turn`(내용 있음)이 기록됐다.
  - 06:13:36.376Z: 모델 요청에 들어갔다. 라우터도 `channel_llm_inject_skipped reason=already_in_request`를 남겼다.
- 범위(11-23 20:11Z 이후 Betty 라우터 로그 두 개)
  - `unseen_failed` 31건, `confirmed` 8건.
  - 31건 모두 대화 기록에 첨부와 remove가 있다. 모두 실제로 전달됐다.
  - 11-23 이후 채널 입력 78건: 실행 중 인계 53건(첨부가 remove보다 먼저 47건, 나중 6건), idle 프롬프트 25건.
  - 채널 입력의 enqueue는 모두 내용이 없었다. 내용 있는 enqueue는 task-notification 31건과 사람이 입력한 1건뿐이다.
- 예: 7833은 22:17:02Z에 remove(absorbed_mid_turn)가 기록된 뒤 15분 동안 `queued`로 남았다가 22:32:15Z에 실패로 기록됐다.
- 커서 복구 경로에도 나타났다.
  - 06:34:10Z 로그: `channel_stdin_proxy_recover_queued_only message_id=7847 cursor=7868 recovered_cursor=7846`
  - 일괄 판정이 첨부를 먼저 쓰는 인계를 `queued`로 읽었다.
  - 이번에는 clear floor(7862)에 막혔다. 7863–7865는 `durable_replied`로 건너뛰었고, 재주입은 관측되지 않았다.
- 다른 네 호스트(Andy, Celly, KOBE, Prody)는 side 액터 경로라 이 경로를 쓰지 않는다. 현재 로그의 `unseen_failed`는 0건이다.
- 메시지 유실은 관측되지 않았다. 실패 기록이 발신 측에 미치는 영향은 확인하지 않았다(event-ack의 `injected`는 `submitted` 시점에 이미 나간다).

## 원인 (소스와 기록 대조)

- `channel_transcript.wake_state_evidence_from_text`
  - queue 기록은 `content`로만 메시지를 식별한다. 그래서 내용 없는 enqueue는 증거가 되지 못한다. 인계 전까지 상태가 `missing`이고, `advance_channel_inflight`의 20초 unseen 규칙이 실패로 바꾼다.
  - `queued_command` 첨부는 remove 뒤에 올 때만 전달로 본다(4cccb27, 2026-09-23 관측 순서). 첨부가 먼저 오면 전달이 확인되지 않고 `queued`로 남는다.
- `wake_states_from_text`(커서 복구 일괄 판정)와 `queued_dropped_from_text`도 같은 순서 가정을 쓴다. 첨부 뒤의 remove를 "버림"으로 읽는다.
- 이 기록 형태가 나오는 조건(실엔진, 아래 검증)
  - 맨 Claude Code 2.1.331은 내용 있는 enqueue를 쓰고 remove 뒤에 첨부를 쓴다. 입력 크기(195자, 3,434자)와 상관없었다.
  - `session.receive`만 통과시키는 모드를 적재해도 같았다.
  - 제품 ciel-work 모드를 적재하면 Betty와 같은 형태(내용 없는 enqueue, 첨부 먼저)가 나왔다.
  - ciel-work의 어느 훅이 Claude Code의 기록 방식을 바꾸는지는 확인하지 않았다.

## 수정

- `channel_transcript.py`
  - 인계는 첨부와 queue 이탈(remove/dequeue)의 짝이다. 순서는 상관없다. 첨부가 먼저면 이탈이 기록될 때 전달로 본다(prompt_record는 첨부).
  - 같은 규칙을 일괄 판정(`wake_states_from_text`)과 `queued_dropped_from_text`에 적용했다. 첨부 없이 remove만 있으면 지금처럼 버림이다.
  - 새 함수 `queue_holds_unattributed_input(text, since)`
    - 보낸 시각(오차 2초) 이후 CLI가 기록한 내용 없는 enqueue가 아직 큐에 있는지 센다.
    - 내용 있는 enqueue(task-notification)는 같은 내용의 이탈과 짝지어 빼고, 나머지 이탈은 내용 없는 항목을 하나씩 뺀다. `popAll`은 비운다.
  - `ChannelWakeStateReader.queue_holds_input_since(since)`
  - `queued_age_seconds_from_text`: 메시지를 가리키는 enqueue나 첨부가 없으면 그 메시지의 remove 시각부터 경과를 잰다.
    - 이유: 수정 전에는 진짜로 버려진 입력(첨부 없는 remove)도 20초 unseen 규칙에 우연히 걸려 실패로 끝났다.
    - 수정 뒤에는 그 입력이 `queued`로 남는다. 그래서 기존 버림 판정(`queued_is_stale`: 경과 180초 이상이고 버림 증거가 있음)이 경과 시간을 읽을 수 있어야 한다.
    - 내용 있는 enqueue가 있는 기존 형태는 지금처럼 enqueue 시각부터 잰다.
- `channel_inflight.py`
  - `ChannelInflightEffects.queue_holds_input`(선택 포트)
  - unseen 규칙: `missing`이어도 CLI가 보낸 뒤의 큐 항목을 들고 있으면 실패로 바꾸지 않고 `queued_unattributed`로 기다린다. 큐 항목이 빠졌는데도 메시지를 가리키는 기록이 없으면 지금처럼 실패다.
- `ciel_runtime.py`: 운영 inflight 효과에 이 포트를 연결했다.

## 검증

- 실패 테스트 먼저: `tests/test_channel_mid_turn_handover.py`(19건, Betty 기록 형태 그대로)
  - 수정 전 코드에서 10 실패 / 9 통과([red-before-fix.txt](evidence/red-before-fix.txt)). 수정 전 코드는 HEAD 사본에 새 파서 함수만 추가해 import가 되게 한 것이다.
  - 수정 후: 19 통과.
- 실엔진: [run_mid_turn_socket_real.py](evidence/e2e-source/run_mid_turn_socket_real.py)
  - 조건: Claude Code 2.1.331, 스텁 Messages API, 격리 `CLAUDE_CONFIG_DIR`, 실제 자격 증명 없음.
  - 첫 턴이 Bash로 35초 동안 대기한다. 그 대기 5초째에 제품 소켓 클라이언트로 id=901을 보낸다.
  - 0.5초마다 실제 대화 기록을 읽는다. 판정은 두 가지로 한다: 수정 전 `channel_transcript`(`git show 7edfc0c`)와 수정 코드. 둘 다 제품 `advance_channel_inflight`로 돌린다.

  | 실행 | 적재 모드 | 기록 형태 | 수정 전 | 수정 후 |
  |---|---|---|---|---|
  | [run1-bare-200](evidence/run1-bare-200/result.json) | 없음 | 내용 있는 enqueue, remove 뒤 첨부 | 35.0초 완료 | 35.0초 완료 |
  | [run2-bare-3400](evidence/run2-bare-3400/result.json) | 없음 | 같음 | 33.0초 완료 | 33.0초 완료 |
  | [run3-receive-pass-3400](evidence/run3-receive-pass-3400/result.json) | session.receive 통과 | 같음 | 32.1초 완료 | 32.1초 완료 |
  | [run4-ciel-work-3400](evidence/run4-ciel-work-3400/result.json) | ciel-work, ciel-work-gate | **내용 없는 enqueue, 첨부 먼저, remove absorbed_mid_turn** | **20.3초 실패** | 32.6초 완료 |

  - 네 실행 모두 스텁 API가 받은 모델 요청에 입력이 들어 있었다.
  - 라이브 `~/.claude/settings.json`은 바뀌지 않았다. `~/.claude.json`은 운영자의 라이브 세션이 계속 쓰는 파일이라 비교에서 뺐다. 스크래치는 모두 지웠다.
- 회귀(격리: `CIEL_RUNTIME_*` 해제, 임시 `CIEL_RUNTIME_CONFIG_DIR`, `CIEL_RUNTIME_TEST_ISOLATED=1`)
  - 영향 범위: `ciel_runtime`, `channel_*`를 쓰는 테스트 파일 165개
  - Windows 파일별 pytest: 159개 파일 통과. 합계 3,136 통과 / 11 실패 / 153 skip.
    - 실패 11건(6개 파일)은 모두 저장소 기준 unittest 실행기(`run_tests.py`)로 다시 돌리면 통과한다.
    - 4개 파일(`test_claude_compact_continuation`, `test_claude_stream_recovery_settings`, `test_prelaunch_default_action`, `test_prelaunch_work_settings_pin`)은 메뉴가 `sys.stdin.fileno()`를 읽는다. pytest 표준 입력 캡처가 이것을 막는다(`UnsupportedOperation: redirected stdin is pseudofile`).
    - 나머지 2개(`test_router_debug`, `test_unisolated_test_guard`)는 기준 072313c에도 있던 pytest 전용 실패다.
  - Linux 기존 컨테이너(unittest 실행기): `ciel-runtime-test-py310`, `ciel-runtime-test-py311` 각 165개 파일 실패 0.
  - Windows 채널 핵심 22개 파일 한 번에 실행: 736 통과 / 0 실패 / 122 skip.
  - `ruff 0.15.22`, `compileall`, `npm run check:docs`, `npm pack --dry-run` 통과.

## 배포 (2026-11-24)

- 커밋 4886d62를 `feature/priority-models`와 `beta`에 push했다. `git ls-remote`로 두 브랜치 모두 4886d62임을 확인했다.
- Publish to npm 실행 45351132124
  - 1차 시도
    - `npm publish` 단계는 성공했다(`0.2.51-beta.20261124-075049.4886d62`).
    - 그러나 검증 단계가 15분 동안 `ETARGET`(레지스트리 미반영)으로 재시도하다 실패했다.
    - 워크플로가 beta 태그를 7edfc0c로 되돌렸다.
  - 실패한 잡만 재실행(attempt 2): 성공. beta = `0.2.51-beta.20261124-080748.4886d62`, gitHead 4886d62.
  - beta tarball의 변경 파일 3개 해시가 오버레이 new와 같다. 1차 버전의 tarball도 같았다.
- 해시 확인 오버레이 `overlay-4886d62`(3개 파일, base 7edfc0c)를 설치했다. 모든 루트에서 설치 전 base 3/3, 설치 후 new 3/3이었다.

  | 대상 | 설치 시각 |
  |---|---|
  | 로컬 hotfix5, `.local/share/ciel-runtime`, npm 전역 | 08:08:23Z |
  | Celly(관리자 권한) | 08:08:32Z |
  | KOBE | 08:08:56Z |
  | Prody | 08:09:20Z |
  | Betty | 08:20:20Z |

  - Betty는 08:09–08:13Z 첫 적용 호출이 아무것도 출력하지 않고 끝났다. 재점검에서 base 3/3이라 다시 적용했고, 그 결과 new 3/3이었다.
- 라이브 효과(파일 설치와 구분)
  - 세 파일 모두 라우터 프로세스가 읽는 Python이다. 운영 라우터는 이 작업에서 재시작하지 않았으므로 어느 호스트에서도 아직 라이브가 아니다.
  - 이 수정이 겨냥한 Betty에서도 08:21Z 틱까지 `unseen_failed` 7873(07:41Z)이 나왔다. 이전 코드가 돌고 있다는 뜻이다.

