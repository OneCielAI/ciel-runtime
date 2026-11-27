---
type: deployment-record
title: e9eef86 재배포 — 버전 스탬프 정렬, Celly 라우터 교체, Andy 라우터 MemoryError 정지 복구
description: 사용자 지시 "다시 수정본 전부 배포해 베타도 푸시하고"에 따른 beta/npm e9eef86 발행, 7개 루트 버전 스탬프 오버레이, 라우터별 실행 코드 확인, Andy 라우터 MemoryError 정지와 162787 처리 경과
tags: [deployment, beta, npm, overlay, router, andy, celly, memory, side-intake]
timestamp: 2026-11-27T06:15:00Z
---

# e9eef86 재배포

## 발행

- `beta` = `e9eef86`(fast-forward). `Publish to npm` 런 `45945452055` 성공, `@one-ciel-ai/ciel-runtime@beta` = `0.2.51-beta.20261127-052302.e9eef86`(`gitHead` 일치).
- 코드 차이: 7개 루트 전수 해시 비교에서 590개 중 589개 일치, 차이는 버전 문자열뿐(`runtime_constants.py`, `package.json`). 실행 코드는 이미 설치된 `1ca7dc3` 과 같다.

## 버전 스탬프 오버레이 (`stamp-e9eef86`)

- `apply_stamp.py`는 파일이 tarball과 **버전 필드만** 다를 때만 교체하고 나머지는 거부한다(백업 `.overlay-backups/stamp-e9eef86`).
- 로컬 4개(hotfix5, `.local/share/ciel-runtime`, AppData npm, `G:\ciel-bridge-service`), KOBE `u24`, Prody `u41`, Betty `u42`: 모두 check=`base` → apply `ok=True`. 결과 해시 `runtime_constants.py 877078f3875d`, `package.json 0c90ac9e0a40`(package.json이 없는 로컬 2개 루트는 `skipped_absent`).
- 사후 읽기: 원격 3개 루트에서 `work_parallel_workspace.py 08d780edac39`, `observatory.client.js 047df59f819d` 확인, 원격 임시 파일 잔여 없음.

## 라우터별 실행 코드 (라이브 효과와 파일 설치 구분)

| 호스트 | 라우터 pid · 기동(UTC) | `/health` 버전 문자열 | 메모리의 코드 |
| --- | --- | --- | --- |
| Andy `9515` | `83616` · 11-27 05:56:19 | `e9eef86` | 1ca7dc3 동일 코드 |
| Celly `9385` | `66068` · 11-27 06:05 (이번 교체) | `e9eef86` | 1ca7dc3 동일 코드 |
| KOBE `9779` | `12360` · 11-27 03:13:06 | `1ca7dc3` | 1ca7dc3 |
| Prody `9592` | `7836` · 11-27 01:40:23 | `61d2f8b` | 1ca7dc3 (파일 01:35:06 교체 후 기동) |
| Betty `9564` | `11836` · 11-27 01:40:46 | `61d2f8b` | 1ca7dc3 (파일 01:35:06 교체 후 기동) |

- **정정:** 04:12Z 틱 기록의 "Prody 라우터 버전 61d2f8b(구 빌드)"는 틀렸다. 그때 `1ca7dc3` 오버레이는 코드 파일 2개만 바꾸고 버전 문자열은 그대로 두었기 때문에, 01:40Z에 기동한 Prody/Betty 라우터는 새 코드를 실행하면서도 옛 문자열을 보고한다. 문자열만 다르므로 재기동하지 않았다.
- **Celly:** 1ca7dc3 때 `taskkill` 접근 거부로 라우터 `111684`(11-26 00:12Z 기동, 61d2f8b 코드)가 남아 있었다. 이번에는 사용자의 앞선 지시("라우터 재시작")를 완료하려고, 권한 상승 스크립트 `celly_elev_stop_router.ps1`로 **pid가 9385 리스너이고 명령줄이 `ciel_runtime.py serve`임을 확인한 뒤** 그 프로세스만 종료했다. 슈퍼바이저가 같은 포트로 `66068`을 띄웠고 `/health` 버전 `e9eef86`, 클라이언트 `[104616]`, Work REST 0.1초 응답을 확인했다.
- **모드(`observatory.client.js`):** Prody 01:40:12Z, Betty 01:35:21Z, Celly 01:39:21Z 전사에 `ciel-work: reloaded`가 있다(`--plugin-dir` 핫리로드). KOBE는 03:12:36Z, Andy는 05:55:47Z에 런처가 새로 떠서 새 파일을 로드했다. 이번 스탬프는 모드 파일을 바꾸지 않는다.

## Andy 라우터 정지 (05:39–05:56Z)

- **관측:** 라우터 `9505`(pid `95176`) 로그에 05:39:06Z `transcript_delta_delivery_failed poll MemoryError`, 05:39:13Z `work_retention_failed error=MemoryError`, 05:39:15Z `fork_turn_watch_poll_failed error=MemoryError`가 찍혔다. 그 뒤로 10분당 로그가 약 4,000줄에서 12줄로 줄었고, 05:39:22Z부터 `router_lifetime_keep_alive reason=listener_reachable_health_busy`만 남았다. `/health`는 10초·60초 모두 응답하지 않았다. 프로세스 자체는 private 399 MB로 크지 않았다.
- **호스트:** 커밋 사용률은 직전 관측 97%(138.5/142.5 GB), 이번 92%→91%였다. paged pool은 25.4 GB였고, 다른 세션의 `grep -rho` 2개가 각 2.39 GB를 쓰고 있었다(손대지 않음). MemoryError는 호스트 커밋 고갈과 같은 시각에 나왔지만, 어느 스레드가 왜 멈췄는지는 프로세스를 종료해 덤프를 남기지 못했으므로 **미확정**이다.
- **경과:** 05:54:50Z Andy Claude CLI가 `exit_0`으로 끝났다(6.06시간 실행, 종료 주체 미확인). 라우터는 `router_managed_lifetime_shutdown reason=idle_no_clients` → `router_kill_guarantee reason=claude_exit state=already_down` → `router_lifetime_stopped stopped=False`를 남기고 리스너를 계속 잡고 있었다. 05:55:47Z 같은 콘솔(`cmd.exe` 39688)에서 `py -3 … cli --continue`가 다시 떴고, 새 라우터 `83616`은 포트 `9515`에서 기동했다. 05:55:59Z에 `restart_hung_router.py`로 응답하지 않는 옛 `95176`(9505 리스너·`serve` 명령줄 확인)만 종료했다. 새 라우터는 MemoryError 0건, `health_busy` 0건이다.
- **추적(미수정):** 정지한 라우터를 kill guarantee가 `already_down`으로 판정해 남겨 둔 점은 위 로그 순서로만 관측됐다. 재현 테스트가 없어 결함으로 확정하지 않았고 코드도 고치지 않았다.

## 162787 처리 경과 (Andy 사이드)

- 첫 수신분은 처리에 14.7분이 걸렸고 답변 전송이 `uncertain`이었다(설계상 무조건 재전송 금지).
- 05:23:16Z Walkie 재알림 → 영수증 `9a0a8ef59a`: claim 05:24:12Z, 처리 05:37:51Z(14.6분). 결정은 `update n2952`(실행 중 포크가 직접 답변)였고 `delivery=not_applicable`이다.
- 05:39–05:56Z 라우터 정지 구간이 이어졌다.
- 06:01:37Z 세 번째 재알림 → `61739b01df`: claim 06:10:56Z(9.3분 대기), 06:12Z 기준 `processing`이다.
- 재기동 후 새 라우터에서 claim 7, ack 6, reply 8, renew 16회가 관측돼 사이드 수신·처리는 재개됐다. 다만 claim 대기 9분과 처리 14분대는 이번 배포로 바뀌지 않는다(배포 코드는 워크트리 예산·애니메이션뿐).
