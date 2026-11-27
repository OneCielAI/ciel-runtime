---
type: deployment-record
title: 사이클 6 — 사이드 보드 쓰기 차단, 게이트 결정 유실 회귀, 오래된 미바인딩 예약, planning 복귀 알림
description: 2026-11-27 17시 배포. 77ca963과 165aa17의 영향 회귀(임시 격리 Windows, Linux py310/py311), npm 발행, 7개 루트 해시 확인 설치, 라우터 다섯 개 교체로 라이브 적용한 기록
tags: [deployment, regression, side-intake, work-parallel, work-gate, andy, celly, kobe, prody, betty]
timestamp: 2026-11-27T17:45:00Z
---

# 사이클 6 배포

## 포함한 수정

| 기록 | 커밋 | 파일 |
| --- | --- | --- |
| [side-board-write-gate](../../fixes/side-board-write-gate/record.md) | `77ca963` | `claude_mods/ciel-work-gate/hooks/register.js` |
| [side-gate-shortfall-commits](../../fixes/side-gate-shortfall-commits/record.md) | `165aa17` | `side-intake.js`, `work_texts.py`, `texts.generated.js` |
| [stale-unbound-reserve](../../fixes/stale-unbound-reserve/record.md) | `165aa17` | `claude_mods/ciel-work/hooks/register.js` |
| [scope-edit-planning-notice](../../fixes/scope-edit-planning-notice/record.md) | `165aa17` | `work_mcp_tools.py`, `work_workflow_prompts.py` |

- `270f6ec`는 CHANGELOG 항목만 바꿨다(설치 대상 아님).
- 작업 트리의 `work_parallel_legacy_source.py`와 그 테스트는 이번 수정이 아니라서 커밋하지 않았다.

## 회귀

### 게이트 수정(77ca963), 사이클 7 목록

- 목록은 `cycle7.files`(10개 파일)이다.
- Windows 임시 격리: 10/10 통과(`cycle7-win.txt`).
- Linux py310·py311: 10/10 `failures=0 errors=0`(`cycle7-py31x.log`).

### 165aa17까지 포함한 최종 코드, 사이클 9 목록

- 목록은 `cycle9.files` 25개와 `test_work_mod_real_backend`다.
- **Linux py310·py311**(`run_serial.sh`, 파일 하나씩): 26/26 `failures=0 errors=0`. `test_work_mod_real_backend`는 두 버전 모두 `RAN 120 failures=0 errors=0 skipped=4`이다.
- **Windows 임시 격리**: 25개 파일 모두 통과(`cycle9-win.txt`).
  - side 13, gate_check 27, mod_write_gate 157, texts 10, architecture 219+2, fork_gate 35, side_real_backend 8, mcp_tools 33, release_hints 3, http_controller 37, workflow_surfaces 8, running_fork_relay 3, gate_parked_forks 9.
  - `test_work_mod_real_backend`: 117 passed, 3 skipped, 19 subtests passed (3201 s).
- `claude plugin validate ciel-work`와 `ciel-work-gate` 통과. `claude plugin test ciel-work` 99 pass / 0 fail.
- 사이클 8(중간 코드)의 Linux real-backend 실행은 register.js가 다시 바뀌어 중단했고, 사이클 9로 대체했다.

## 발행

- `77ca963`: `Publish to npm` 런 `46031909382` 성공. `0.2.51-beta.20261127-161448.77ca963`(`gitHead 77ca96373fad`).
- `165aa17`: 런 `46041350408` 성공. `0.2.51-beta.20261127-172252.165aa17`(`gitHead 165aa1792abd`).

## 파일 설치

- 오버레이 `overlay-165aa17`은 기반 `fd96473`, stamp `stamp-fd96473`(fd96473 오버레이의 `.new` 버전 파일, `bb508304ba64`/`38016c142aab`)이다.
  - 코드 7개 모두 tarball과 git이 같다.
  - `ciel-work/hooks/register.js e80dec9e54d7 → cfb48f81145d`
  - `side-intake.js 4e68ae848165 → 9aa9b6df4fec`
  - `texts.generated.js ae5cbd0a0b99 → bee1dc728504`
  - `work_mcp_tools.py 24496c0c320f → 94799e41978d`
  - `work_texts.py af411d6c0e95 → f649f86fa208`
  - `work_workflow_prompts.py a71fd5624dba → 909e0ee39a5d`
  - `ciel-work-gate/hooks/register.js → 55642b8e707b`
  - `runtime_constants.py → 39da1511a0b0`
  - `package.json → 79037a2e5d93`
- 17:32:49Z check 7/7 `base` → apply 7/7 `ok=True` → recheck 7/7 `new`. 로컬 2개 루트의 package.json은 `skipped_absent`이다.

## 라이브 적용

| 호스트 | 모드 리로드 | 라우터 교체(UTC) |
| --- | --- | --- |
| Andy `9515` | 17:37:33Z ciel-work-gate, 17:37:36Z ciel-work | `99124` → `115520`, 17:37:26–38Z |
| Celly `9385` | 17:39:04Z ciel-work-gate, 17:39:08Z ciel-work | `79720` → `78204`, 17:38:47–17:39:09Z(권한 상승 스크립트) |
| KOBE `9779` | 다음 틱에 확인 | `17112` → `2452`, 17:40:07–23Z |
| Prody `9592` | 다음 틱에 확인 | `8412` → `7052`, 17:41:01–39Z |
| Betty `9564` | 다음 틱에 확인 | `9172` → `11060`, 17:41:53–17:42:19Z |

- 모든 교체에서 런처와 Claude 세션이 같다(`claude same True`). 새 라우터는 모두 `0.2.51-beta.20261127-172252.165aa17`이다.

## 라이브 효과

- **유령 예약 해제(Andy):**
  - `a0874c5a`(n2923, 08:20:08Z 예약)가 18:10:21Z에 해제됐다. coordination 아카이브의 outcome은 `native launch never started`다.
  - 17:54Z와 18:04Z에는 아직 예약이 남아 있었다.
  - 새 모드의 첫 펌프 흔적은 17:52:15Z의 parked 로그다. 첫 관측 뒤 15분이 지나야 해제되므로, 모드 리로드(17:37:36Z) 시점보다 늦어졌다.
  - 해제 전 확인:
    - 같은 ledger 복사본에 router `release`를 재현하면 받아들여진다(`release_replay.py`).
    - 호스트 투영으로 보면 `owner_process own`, `workspace_path` 있음, `dispatch_name` 있음이다(`project(..., host_view=True)`).
- **해제 뒤:**
  - 18:15:46Z에 P3는 `active 0, candidates 6, can_fill`이다.
  - n2779 `c4a612a6`은 `slot True`(재획득)다.
  - 18:15:35Z `Work: resumed parked worker a879d922407… on n2888`.
- **게이트 결정 유지·ACK client_id·사이드 보드 쓰기 차단:** 해당 이벤트가 오면 다음 틱에서 확인한다.

## 재시작 부작용

- **Andy:**
  - n2549 포크(`a20552e35fbd`)가 17:37:36Z `API Error: Connection to the API was lost (StreamTruncated)`을 받았다. 이 포크는 17:37:08Z exit 계약 요청에 답하던 중이었다.
  - Andy_Main은 17:38Z 무렵 `ciel-runtime-router` MCP가 한 번 끊겼다가 다시 연결됐다고 보고했다.
  - 둘 다 제 라우터 교체(17:37:26–38Z)가 원인이다.
- **Celly:** 17:38:54Z `tool.call hook skipped: snapshot-paged` 1건이 교체 구간에 관측됐다. 교체가 원인인지는 확인되지 않았다. 18:06:54Z `router-error` 1건은 교체 구간 밖이다.
- **KOBE·Prody·Betty:** 교체에 인과를 연결할 수 있는 후속 부작용은 확인하지 못했다. 18:35Z 탐침은 3개 호스트 모두 응답했다.

## 18:36:26Z 후속 읽기 검증

- Andy P3는 `full`, `active 1`, `fenced_states: {reserved: 1}`, 후보 5개다. 이전 `a0874c5a` 해제는 위 아카이브로 확인했으며, 후속 조회에서 해당 리스는 나오지 않았다. 최근 parallel 실패 4개에는 `reserve-unbound-stale`이 보이지 않았다(전체 이벤트 부재를 뜻하지 않는다).
- n2779 `c4a612a6`은 앞서 슬롯을 재획득했으나 이 시점에는 `waiting_input`, `slot False`다. n2549 `50129428` 역시 `waiting_input`, `slot False`다. 슬롯 획득이나 이 스냅샷만으로 포크의 지속 실행·종료를 단정하지 않는다.

## 남은 항목

- `work_status` parallel 블록이 빈 신원으로 계산되던 결함(runtime=codex)을 고쳤다. 고친 코드는 다음 사이클에 배포한다. 테스트는 `test_status_json_parallel_block_is_this_conversations`이고, 수정 전 실패와 수정 후 34 passed를 확인했다.
- Andy 18:10–18:13Z `Ciel Work gate wg-…: router-error` 3건(미확인).
- Andy_Main 보고 [3]: stranded·finalization 행이 `rework_ready`·launch `bound`로 남아 있는데, 교체 리스는 이미 unreported/orphaned다. 디스패치는 막지 않지만(`_stranded_item_pending`은 intent/pending만 본다) 종결 경로가 없다.
- Andy_Main 보고 (4): `work_status`의 parallel 요약이 `runtime=codex`로 계산된다(미확인).
- n2957 포크의 `__unparsedToolInput` 중첩 손상 루프(16:49Z)는 별도로 추적한다.
- 공유 컨테이너의 workspace_prepare_retained 원인이 라우터 로그 회전(12 MB, 백업 1개)으로 사라지는 문제. 10:26Z 건은 원인을 확인하지 못했다.
