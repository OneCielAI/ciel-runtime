---
type: operations-record
title: Celly P0 intent 장부 256/256으로 P0 포크가 뜨지 못한 사건
description: Celly 라우터가 fcce6d7 이전 코드로 돌아 끝난 리스의 intent를 비우지 못했고, 새 P0 포크가 막혀 메인이 '임시 P1' 우회와 수동 장부 정리를 했다. 원인, 라이브 코드 지문, 정리 결과, 남은 위험.
tags: [celly, p0, intent-ledger, rollout, router-restart]
timestamp: 2026-11-23T15:20:00Z
---

# 시간순 (읽기 전용 관측)

- 13:03–13:16Z: Celly 메인이 n1061·n997을 '임시 P1'로 내렸다. 진행 메모: "새 포크 시작용 P0 intent 장부 full 우회(임시 P1) → 시작 확인 후 P0 복원". 이렇게 띄운 포크는 P1 모델(`_Walkie_P2`)로 돈다. P0 모델은 `_Walkie_P1-Celly`다.
- 13:25:20Z: 메인이 사용자에게 물었다. 내용은 "P0 장부는 여전히 256/256(13:22Z 재확인, cancelled 0)"이고, 선택지는 "내가 런타임 수정" / "메인이 직접 정리"였다. 사용자가 보낸 화면(Image #28)이 이 질문이다.
- 15:11:34Z: 메인이 설치된 `work_parallel_p0.py`를 확인했다(디스크에는 정리 규칙이 있음).
- 15:13:08Z: 메인이 백업 `work-parallel-p0.backup-20261123T151308Z.json`을 만들고, 런타임의 `evictable_intents` 규칙으로 계산했다. 결과는 before 256(bound 106, terminal 137, intent 13), evictable 181.
- 15:13:13Z: `prune_p0.py --apply`로 256→75. 사용자 승인 문구는 "메인이 장부 정리"였다. 15:13:22Z에 n1061을 P0로 되돌렸다.

# 원인 (입증)

- 라이브 라우터 지문(`/health` `source_fingerprint`)은 `84eb402c0c3ee7cf`, 버전은 `0.2.51-beta.20261123-013244.432c8a8`이다. 이는 fcce6d7 이전의 `ciel_runtime.py`(overlay-fcce6d7 base)와 같다. 디스크에 설치된 파일은 5e02ee7(`bc9f4d1c0730c8cc`)이다.
- 이 라이브 코드는 장부가 가득 차면 cancelled 행만 비운다. 끝난 리스(done/orphaned)의 intent 181행이 쌓였는데 cancelled는 0이라 `P0 intent bound`로 새 intent를 만들 수 없었다.
- 정리 규칙(`evictable_intents`)은 fcce6d7([p0-durability](/docs/journal/2026/11/23/fixes/p0-durability/record.md))에 들어 있다. Celly에는 11:04Z에 설치됐다. 라우터가 재시작되지 않아 라이브가 아니다. 라우터 재시작은 이 작업의 금지 범위이고 사용자가 결정한다.

# 현재 상태 (15:14:07Z, 읽기 전용, 잠금은 읽는 동안만)

- 장부 v2373, intent 75개. 모두 이전 세션 scope 4개의 것이고 현재 세션(`4ee83c21`)의 것은 0개다. 마지막 갱신은 04:27:07Z다.
- 75행의 상태: terminal 44, bound 18, intent 13. 리스 상태로는 unreported 45, orphaned 11, accepted 3, 리스 없음 13, done 2, submitted 1.
- 현재 코드 규칙으로 더 비울 수 있는 행은 0개다.
  - unreported 리스는 검수·복구 대상이라 일부러 남긴다(`RETIRED_LEASE_STATES` 주석).
  - 리스 없는 열린 intent 13행은 어떤 규칙으로도 비워지지 않는다.
- 루트 마커: active 43(현재 scope 8), cleared 1.

# 남은 위험 (미확인, 추적)

- 이전 scope의 미발사 intent 13행(리스 없음)은 정리 경로가 없다. 재시작할 때마다 새 scope가 생기므로 같은 종류가 다시 쌓일 수 있다. 실제 증가 속도는 측정하지 않았다.
- unreported 45행은 검수나 복구가 끝나야 빠진다. 많이 쌓이면 상한에 다시 닿을 수 있다.
- 정리 뒤 현재 scope에서 새 P0 intent가 만들어지는지는 15:14Z 기준 미관측이다. 다음 틱에서 확인한다.
- 메인이 라우터 실행 중에 상태 파일을 직접 고쳤다. 라우터 쪽에서 버전 충돌이 났는지는 아직 확인하지 않았다.
