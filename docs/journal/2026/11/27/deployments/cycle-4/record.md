---
type: deployment-record
title: 사이클 4 — 사이드 대기 확인·ACK 게이트, Andy 기본 지침, 예약 응답 유실, git 타임아웃, kill guarantee
description: 2026-11-27 사이클 2–4에서 고친 결함 6건의 영향 회귀(임시 격리 Windows, Linux py310/py311)와 배포 기록
tags: [deployment, regression, side-intake, work-parallel, router, andy, celly]
timestamp: 2026-11-27T10:55:00Z
---

# 사이클 4 회귀와 배포

## 포함한 수정

| 기록 | 파일 |
| --- | --- |
| [side-wait-check](../../fixes/side-wait-check/record.md) | `side-intake.js`, `work_texts.py`, `texts.generated.js` |
| [side-ack-gate](../../fixes/side-ack-gate/record.md) | `side-intake.js`, `work_texts.py`, `texts.generated.js` |
| [side-default-from-andy](../../fixes/side-default-from-andy/record.md) | `side_intake_settings.py`, `side-intake.js` |
| [lost-reserve-reply](../../fixes/lost-reserve-reply/record.md) | `register.js` |
| [git-timeout-not-unavailable](../../fixes/git-timeout-not-unavailable/record.md) | `work_parallel_mcp.py`, `work_parallel_workspace.py` |
| [kill-guarantee-listener](../../fixes/kill-guarantee-listener/record.md) | `router_process_lifecycle.py` |

- 이 작업 트리에 함께 있는 `ciel_runtime_support/work_parallel_legacy_source.py`와 `tests/test_work_parallel_legacy_source.py`는 이번 수정이 아니다. 커밋하지 않는다.

## 회귀

### Windows

- 임시 `CIEL_RUNTIME_CONFIG_DIR`, `CIEL_RUNTIME_TEST_ISOLATED=1`, `CIEL_RUNTIME_*` 상속 제거 상태에서 실행했다.
- **사이클 2**(`cycle2-win.txt`): lifecycle과 work_parallel 21개 파일 전부 통과.
- **사이클 4b**(최종 코드, `cycle4b-win.txt`): 영향 목록 19개 파일.
  - 사이드: wait_check 7, ack_gate 6, intake_mod 75, failures_hotfix 20, background_prompts 9, fork_answers 8, instruction_view 6, intake_mcp 53, notice_claim 19, **real_backend 8**, relay_notice 15, release_notice 6, reply_connector 11.
  - Work: gate_check 27, mod_write_gate 156, texts 10.
  - architecture: contracts 219, budget 2.
  - `test_work_mod_real_backend`: 117 passed, 3 skipped, 19 subtests passed (2833 s).
  - 4a(초기 코드) 실행의 같은 파일은 3000 s 제한에 결과 없이 끝났다. 4b는 2833 s로 제한 안에 통과했다.
- 최초 4a 실행에서 `test_side_real_backend`가 10 실패했다(180 s 시간 초과). 원인은 대기 확인 게이트가 결정 항목을 action과 무관하게 대상으로 삼아 `advise` 흐름까지 교정 대기에 둔 것이다. 대상을 `update`·`prioritize`로 좁힌 뒤 4b에서 통과했다.
- `claude plugin validate ciel-work` 통과.
- `claude plugin test ciel-work`:
  - 4a 코드는 99 pass / 0 fail.
  - 4b 코드의 첫 실행은 98 pass / 1 fail이었다. 실패한 것은 `workflow.test.ts` "the gate check runs while main is busy …"의 두 번째 틱 호출 수(기대 4, 실제 3)다.
  - 같은 코드를 세 번 다시 실행하면 3/3 모두 99/0이다. HEAD 모드로도 세 번 실행하면 3/3 모두 99/0이다.
  - 이 테스트는 모의 시계 cadence에 민감하며, 이번 변경이 건드리지 않는 게이트 체크 경로다. 재현되지 않아 결함으로 보지 않는다.

### Linux py310 / py311 (기존 컨테이너, `/runner`)

- 4병렬 `run_each.sh`, 영향 19개 파일:
  - 18개 파일이 두 버전 모두 `failures=0 errors=0`이다.
  - `test_side_real_backend`만 4병렬에서 실패했다(py310 2, py311 6+4 error).
  - 같은 tarball로 단독 실행하면 두 버전 모두 8/8 OK다(`rb-py310.txt`, `rb-py311.txt`).
  - 수정 전 코드(사이클 3)도 4병렬에서 같은 파일이 1건 실패했다. 이 파일은 병렬 부하에 민감하며, 이번 변경 영향은 단독 통과로 배제했다.
- `test_work_mod_real_backend`:
  - 4병렬 실행 중 docker 연결 오류(`unable to upgrade to tcp, received 500`)로 py311 결과가 남지 않았다.
  - `run_serial.sh`(파일 하나씩, `docker exec -d`)로 재실행했다. 결과는 **완료가 아니다.**
    - 마지막 줄이 `RAN` 요약이 아니라 진행 표시(py310 103자, py311 105자, 실패 표시 없음)다. 즉 1200 s 제한에 걸려 120개 중 약 103개에서 중단됐다.
    - 이어 실행된 `test_side_real_backend`는 두 버전 모두 `RAN 8 failures=0 errors=0`이다(`cycle4-serial-py310.log`, `cycle4-serial-py311.log`).
  - 사이클 3(수정 전 코드)에서는 같은 파일이 두 버전 모두 `RAN 120 failures=0 errors=0 skipped=4`였다.
  - 테스트별 시간을 남기는 `run_tests_v.py`로 다시 실행했다.
    - **py310**(1800 s 제한): `RAN 120 failures=0 errors=0 skipped=4`, `rc=0`(`wmrb-v-py310.log`). 가장 긴 테스트는 47 s다(host-phase retry 3건).
    - **py311**(3000 s 제한): `RAN 120 failures=0 errors=0 skipped=4`, `rc=0`(`wmrb-v-py311.log`). 가장 긴 테스트는 138 s다.
- **결론:** 영향 19개 파일이 Windows와 Linux py310/py311 모두에서 통과했다.
  - `test_side_real_backend`는 단독 또는 직렬로 실행해 통과를 확인했다.
  - `test_work_mod_real_backend`는 Linux에서 시간 기록 실행으로 통과를 확인했다.

## 배포

### 발행

- 커밋 `2fe04af`를 `feature/priority-models`와 `beta`에 fast-forward 푸시했다.
- `Publish to npm` 런 `45994568451` 성공. `@one-ciel-ai/ciel-runtime@beta` = `0.2.51-beta.20261127-115656.2fe04af`(`gitHead` `2fe04aff42f3` 일치).

### 파일 설치(7개 루트)

- `build_overlay_from_stamp.py`로 npm tarball에서 오버레이 `overlay-2fe04af`를 만들었다. 기반은 `ebf972e`, stamp는 `stamp-ebf972e`다.
  - 코드 8개 파일 모두 tarball과 `git show 2fe04af`가 같다.
  - 새 해시: `register.js e80dec9e54d7`, `side-intake.js 06e7e1d3e42b`, `texts.generated.js ae5cbd0a0b99`, `router_process_lifecycle.py 6b213e36291b`, `side_intake_settings.py 073e7e2d3780`, `work_parallel_mcp.py 1334fb2c09c6`, `work_parallel_workspace.py 594e029d612d`, `work_texts.py af411d6c0e95`, `runtime_constants.py 03036d499918`, `package.json 37e7812bc480`.
- check는 7/7 루트에서 모든 파일이 `base`였다(로컬 2개 루트의 package.json은 `skipped_absent`).
- apply는 7/7 `ok=True`다. 재check 결과 7/7 모두 `new`이며 `.cmd` 래퍼는 건드리지 않았다.

### 라이브 적용(재시작)

| 호스트 | 모드 핫리로드 | 라우터 교체(UTC) | 결과 |
| --- | --- | --- | --- |
| Andy `9515` | 12:10:44Z `ciel-work: reloaded` | `83616`(e9eef86) → `51288`, 12:11Z | `2fe04af`, 클라이언트 `[93580]` 유지, REST·MCP 정상 |
| Celly `9385` | 12:09:23Z `ciel-work: reloaded` | `60548`(ebf972e) → `58896`, 12:12:45Z(권한 상승 스크립트, pid·명령줄·리스너 확인 후 종료) | `2fe04af`, 클라이언트 `[84588]` 유지 |
| KOBE `9779` | (확인 안 함) | `12360`(1ca7dc3) → `16120`, 12:15:08Z | `2fe04af`, 런처 `11368` 유지, claude 동일 |
| Prody `9592` | (확인 안 함) | `7836`(1ca7dc3 코드) → `7716`, 12:15:47Z | `2fe04af`, 런처 `10788` 유지, claude 동일 |
| Betty `9564` | (확인 안 함) | `11836`(1ca7dc3 코드) → `11296`, 12:17:09Z | `2fe04af`, 런처 `5624` 유지, claude 동일 |

- 각 라우터는 `/health` pid와 명령줄(`ciel_runtime.py serve`)을 확인한 뒤 그 pid만 종료했다. 슈퍼바이저가 같은 포트로 다시 띄웠다. 런처·Claude 세션·상태 파일은 그대로다.
- KOBE·Prody·Betty의 모드 핫리로드는 이번에 확인하지 않았다. 이 세 호스트의 모드 수정(사이드 대기 확인, 예약 회수)은 그 세션이 새 파일을 로드했다는 증거를 본 뒤에 라이브로 판정한다.

### 재시작 부작용(12:17Z 틱)

- **Andy:**
  - 12:11:32–12:11:45Z 라우터 교체 구간에 `Work reconciliation: Parallel read failed: MCP server "ciel-runtime-router" is not connected` 19건과 `work_host_page … not connected` 1건이 나왔다. 교체 중 재연결 대기에서 생긴 것이다.
  - 같은 구간에 포크 3개(n2510 `ae761f31`, n2968 `a3ded728`, n2956 `a77850735`)가 `API Error: Connection refused (ECONNREFUSED)`를 받았다(12:11:32–34Z).
  - 모드가 재시도를 예약했고(12:13:16Z, 1m×10), `ae761f31`은 12:18:29Z까지 진행이 이어졌다. **제 교체가 원인**이다.
- **Celly·KOBE·Prody·Betty:** 교체 뒤 새 실패는 0이다.
  - Celly 전사의 `auto continue could not read the Work status` 3건(11:55:45, 12:03:48, 12:10:45Z)은 교체 전에 생긴 것이라 부작용이 아니다.
  - Celly 12:16:34Z JSON 트레이스백은 모델 스크립트다.

### 남은 항목

- Andy `a0874c5a`(n2923)와 Celly `6ee265a6`(n1577)의 미바인딩 예약은 이번 수정이 기록하는 키가 아니어서 자동으로 풀리지 않는다. 운영 상태 파일은 수정하지 않았다.
- 대기 확인 게이트의 라이브 효과는 다음 대기 항목 이벤트에서 `wait_items`가 들어간 사이드 프롬프트와 그 답의 `wait_checks`·`release_waits`로 확인한다.
