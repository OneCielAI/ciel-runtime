---
type: fix-record
title: Work 검수 기록의 완료 행 보존 한도
description: Andy work-parallel-finalization.json이 2MB 상한에 닿아 새 검수 행을 쓰지 못한 원인과, 오래된 finalized 행을 먼저 지우는 보존 정책과 검증
tags: [work-parallel, finalization, review, retention, byte-limit]
timestamp: 2026-11-25T22:30:00Z
---

# Work 검수 기록의 완료 행 보존 한도

모든 시각은 UTC다. 운영 호스트 조회는 읽기 전용(파일 복사·REST·전사)이었다. 운영 세션·라우터 재시작과 운영 상태 파일 수정은 하지 않았다.

## 관측 (2026-11-25 21:27Z 모니터링 틱)

- Andy finding `submitted, completion receipt, no review row`가 1건에서 7건이 됐다: `n1166`(기존), `n2849`·`n2850`·`n2851`·`n2860`·`n2844`·`n2845`(신규, P1). 모두 완료 영수증은 있는데 검수 행이 없었다.
- `work-parallel-finalization.json` 2,097,049바이트, 상한 `MAX_BYTES` 2,097,152바이트(남은 103바이트). 행 328개 중 327개가 `finalized`이고, 행 하나의 중앙값은 약 5.9KB다(큰 필드: `workspace` 590KB, `verification` 430KB, `summary` 429KB 합계). 가장 오래된 행은 11-15 07:38, 마지막 갱신은 16:50:35(`n2842` approve)였다.
- 메인 전사: 20:17:53과 20:30:03에 `recover_submitted_review`가 `Parallel read failed: finalization byte limit`로 거절됐다. 20:42, 20:55, 21:15에 메인이 "n2845 정정본은 검증했지만 Work 승인을 기록하지 못했다"고 보고했다. Andy 메인 세션은 21:42에 세션 간 메시지로 `n2845` 대체 리스 `65dd1317`에 검수 핀이 없다는 진단을 보내왔다.
- Celly의 같은 파일은 1,922,279바이트(92%)다.

## 원인 (확인)

- `WorkParallelFinalizationStore._change`는 직렬화 크기가 `MAX_BYTES`를 넘으면 `require(len(raw) <= MAX_BYTES, "finalization byte limit")`로 쓰기를 거부한다. 새 검수 행을 만드는 `pending()`도 이 경로를 쓴다.
- 이 저장소에는 완료(`finalized`) 행을 지우는 경로가 없었다. 삭제는 미결정 행을 옮기는 `remove_undecided`뿐이다. 그래서 approve/close된 행이 계속 쌓여 상한에 닿았다.
- 같은 모양의 결함이 2026-11-22 Celly 조정(coordination) 저장소에 있었다. 그때는 `_trim_history`(`work_parallel_receipts.py`)로 고쳤다(목표 3/4, 보호 24시간).

## 읽는 쪽 영향 조사 (확인)

- approve/close 뒤 리스는 `done`이다(`work_parallel.py` `finalize_accepted`). 리스를 `submitted`로 되돌리는 경로는 없다. `pending()`의 유일한 호출자 `_publish_pending_review`는 `submitted` 리스와 살아 있는 완료 run을 요구한다. 그런데 archive 단계가 이 run을 지운다. 따라서 지운 finalized 행이 다시 검수되는 경로는 없다.
- 재작업 한도와 세대는 리스의 `rework` 값과 결정된 행 자신의 세대를 쓴다. 옛 검수 행은 읽지 않는다.
- 위험 한 가지: `projection().superseded()`는 `rework_ready` 선행 행을, 그 대체 리스의 행이 남아 있을 때만 숨긴다. stranded 저널의 `recovered` 행도 같다(`work_parallel_mcp.py`). 그래서 대체 리스로 지목된 행은 지우면 안 된다.
- 지운 행에 대해 같은 결정을 다시 보내면 멱등 재응답 대신 "durable completion receipt required" 오류가 난다. 그래서 24시간보다 어린 finalized 행은 보호한다.

## 수정 (`ciel_runtime_support/work_parallel_finalization.py`)

- `_change`에서 직렬화 크기가 `MAX_BYTES`를 넘으면 `_trim_finalized`를 부른다. 그다음 다시 검증하고 다시 잰다. 마지막 `require(... "finalization byte limit")`는 그대로 둔다.
- `_trim_finalized`는 `state == "finalized"`이고 `updated_at`이 24시간(`FINALIZED_PROTECT_SECONDS`)보다 오래된 행만, 오래된 순서로 지운다. 크기가 `HISTORY_TRIM_TARGET`(`MAX_BYTES * 3 // 4`) 이하가 되면 멈춘다. 다음 행은 지우지 않는다.
  - 미결정, deciding, awaiting_capacity, rework_ready 행
  - 이 파일 안에서 `replacement_lease_id`로 지목된 행
  - stranded 저널(`work-parallel-stranded-v1.json`)이 지목한 행
- `finalization_store(store)`가 stranded 저널의 지목 목록을 읽는 함수를 넘긴다. 그 파일을 읽을 수 없으면(형식 오류 등) 지우지 않고 기존처럼 거부한다. 스키마 필드는 늘리지 않았다(`validate`가 `{schema, version, reviews}`를 고정한다). 이전 빌드도 finalized 행이 없는 상태를 그대로 읽는다.
- 범위 밖으로 남긴 것(추적): stranded 저널도 같은 상한에 보존 정책이 없다(현재 73KB / 108KB). `work-parallel.json`(1.34MB)에도 리스 정리가 없다. 둘 다 아직 실패하지 않았다.

## 검증

- **수정 전 실패(최신 HEAD `ff24aa6`, 같은 테스트):** 3건이 실제 운영과 같은 `finalization byte limit`로 실패했다 — [red-before-fix.txt](evidence/red-before-fix.txt). 이 3건은 상한 근처 새 검수 행, 미결정·재작업·대체 행 보존, stranded 대체 보존이다. 기존 동작을 지키는 3건(stranded 읽기 실패 시 거부, 지울 것이 없으면 거부, 상한 아래면 지우지 않음)은 수정 전후 모두 통과한다.
- 수정 후 `test_work_parallel_finalization_retention.py` 6건과 기존 `test_work_parallel_finalization.py`이 모두 통과했다(120 통과, 135 서브테스트).
- **실제 파일 사본 확인**([real_copy_check.py](evidence/real_copy_check.py), [real-copy-check.txt](evidence/real-copy-check.txt)). 운영 파일은 복사만 했고, Andy 파일 해시는 확인 전후로 같았다.
  - Andy 사본: 2,097,049 → 1,569,482바이트. finalized 106행을 지웠고(11-20 02:37까지, 오래된 순), 새 행이 써졌다. 미결정·재작업 행과 대체 지목 행은 모두 남았다. 남은 가장 오래된 finalized 행(11-15 07:38, `78a79561`)은 stranded 저널이 지목한 행이라 보호된 것이다.
  - Celly 사본: 상한 아래라 지운 행이 없고, 새 행이 써졌다.

- **Windows 격리 영향 스위트**(파이널라이제이션·`work_parallel_mcp`·stranded·receipts를 쓰는 69개 파일): 65개 파일 통과, 1097 통과·5 실패·36 스킵 ([windows-impact.txt](evidence/tests/windows-impact.txt)). 실패와 비정상 종료를 하나씩 확인했다.
  - `test_prelaunch_work_settings_pin.py` 4건: pytest 실행기의 `redirected stdin is pseudofile` 문제로, `ff24aa6` 때와 같은 기존 실행기 문제다. 저장소 unittest 경로 실행기로는 4건 통과.
  - `test_work_parallel_workspace_trust.py::test_every_git_call_of_a_prepare_carries_the_exact_root`: 수정 전 HEAD를 `git archive`로 받아 돌려도 같은 실패가 났다. `faa2c7e`가 `_git`을 `subprocess.run`에서 `Popen`(타임아웃 시 프로세스 트리 종료)으로 바꿨는데 시험은 `run`만 기록해, 기록이 비어 `[] is not true`로 실패했다. 제품 동작 문제가 아니라 시험 공백이었다. 시험이 `Popen`도 기록하게 고쳤고, 15건 통과. 고친 시험은 git 호출마다 정확한 루트의 `safe.directory`가 붙는지 다시 검사한다.
  - `work_mod_real_backend_server.py`(exit 2)·`work_owner_fixture.py`(exit 5): 테스트 파일이 아니라 보조 모듈이 목록에 들어간 것이다(수집 대상 없음). Linux 목록에서는 `tests/test_*.py`만 남겼다.
- **Linux 기존 `py310`·`py311`:** 실제 백엔드 각 120 통과·4 스킵·0 실패. 영향 스위트(`tests/test_*.py` 66개 파일)는 시험 수정을 반영한 뒤 다시 돌렸다.
  - py310: 1124 통과·101 스킵·0 실패, `compileall`·metadata 0.
  - 테스트 컨테이너 상태: 앞선 py310 실행에서 `docker exec`가 `procReady not received`로 실패했다. 컨테이너 PID는 17,391개였고, 그 실행의 오류 621건은 이 상태에서 난 것이다. py311 실행은 PID 14,562개에서 40분 넘게 진행되지 않았다. 두 테스트 컨테이너만 재시작했다(운영과 무관). 재시작 뒤 같은 목록을 다시 돌렸다. PID가 쌓인 원인(어느 시험이 자식 프로세스를 남기는지)은 확인하지 못했고 추적 항목으로 남긴다.

  - py311(컨테이너 재시작 뒤): 1124 통과·101 스킵·0 실패, `compileall`·metadata 0. 실행이 끝났을 때 PID는 370개였다 ([evidence/tests](evidence/tests/)).

## 발행·설치

DEPLOY_PLACEHOLDER
