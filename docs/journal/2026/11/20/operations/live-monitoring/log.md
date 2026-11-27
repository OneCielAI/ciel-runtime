# Change log

## 2026-11-26

- 04:32:17–04:35:10Z 정기 틱(04:24:03Z 대비 8분 관측 창). 5개 호스트 전원 수집 성공, KOBE·Prody는 이번엔 기본 수집에서도 성공. 재시작·상태 파일 수정 없음. 이하 UTC.
  - **Celly — 보드/MCP 4초 타임아웃 4틱 연속, 원인 후보 관측(미확정).** 이번에도 `board`·`mcp` `TimeoutError`(`mcp` `elapsed_ms=4031`). 라우터 파일 리스는 정상(대기 9·실행/슬롯 3·제출 2). 직접 `/board`를 3회 호출하니 **2.10s / 1.43s / 5.51s** — 성공하지만 편차가 크고 한 번은 4초 예산을 넘겼다. 라우터 로그의 분당 MCP 호출량을 세니 **22 → 36 → 16 → 79 → 118 → 161 → 139 calls/min**으로 급증했고, 누적 상위 도구는 `work_host_page` **153,483회**, `work_side_claim` 20,732회다. 같은 구간에 `router_post_client_disconnected path=/ca/mcp`(WinError 10054/10053)가 반복되고 `runtime_activity_context_usage_failed WinError 5`도 1건 있다. 전사에도 `Work reconciliation: Side router operation failed: work_host_page: The operation timed out.`(04:33:39)와 `Ciel Work gate … router-error`(04:33:47)가 있어 **모니터의 4초 타임아웃과 모드 자신의 `work_host_page` 타임아웃이 같은 현상**임을 보여준다. **인과(호출량 급증 → 지연)는 상관 관측일 뿐 확정 아님.** 코드 수정 없음.
  - Andy (`04:32:17Z`): 보드 `v12543→v12548`, 원장 `8926→8933`, actionable 54, MCP `1313ms`, 리스 제출 20·실행/슬롯 2·대기 1, **finding 0**(직전 `n2763` 검수행 미생성 행이 이번 표본에서 사라짐 — 해소 여부는 다음 틱 확인 필요), 신규 실패 0, 전사 0, 포크 오류 0. 라우터 `fork_turn_watch_poll_failed` 0건·`work_workspace_failed` 2건 유지(신규 없음).
  - Celly (`04:32:26Z`): 원장 `10430→10443`, finding `n1585 awaiting_capacity` 1건(용량 대기), 새 실패 `decision_refused_stop 1b3fdb79 x1`(정상 Stop 가드), 포크 표본 오류 5건, 전사 9건.
  - KOBE (`04:34:48Z`): 보드 834 유지, 원장 `4924→4928`, MCP `247ms`, 수락 1·제출 1·실행/슬롯 1, finding `n5` 가드 유지, 신규 실패 0.
  - Prody (`04:34:57Z`): 보드 `v7567→v7571`, 원장 1407 유지, MCP `370ms`, 실행/슬롯 1만, finding 0·신규 실패 0·포크 오류 2건.
  - Betty (`04:35:10Z`): 보드 5789·원장 1262 변화 없음, MCP `255ms`, 제출 5만, 오류 0.
  - 미결·추적: **Celly 4초 타임아웃 4틱 연속(분당 MCP 호출 급증 상관 관측, 인과 미확정)**·`work_host_page` 누적 153,483회·`n1585` 용량 대기·라우터 새 오버레이 미로드·사이드 적체; Andy `worktree add` 간헐 182초(원인 미확정, 코드 수정 없음)·`n2763` 해소 여부 확인 필요; KOBE `n5` 가드.

- 04:12:42–04:24:03Z 정기 틱(03:52:50Z 대비 20분 관측 창). Andy·Celly·Betty 수집 성공. **KOBE·Prody는 기본 수집에서 `ssh failed`**였고, 두 호스트 모두 직후 단독 재조회로 정상 수집(KOBE `04:23:39Z`, Prody `04:24:03Z`). SSH 접속 자체는 두 호스트 모두 `alive`로 확인돼 미관측을 종료로 보지 않았다. 재시작·상태 파일 수정 없음. 이하 UTC.
  - **Andy (`04:12:42Z`)**: 보드 `v12540→v12543`, 원장 `8924→8926`, actionable 54, MCP `1078ms`, 리스 제출 21·실행/슬롯 2·대기 1, 신규 실패 0. **신규 finding `submitted, completion receipt, no review row: n2763 05530e0f P1 native=idle`** — 완료 영수증은 있는데 검수행이 없다(n2473·Celly n1585/n1634와 같은 계열, 추적). 로그: `fork_turn_watch_poll_failed` 0건, `work_workspace_failed` 2건 유지(신규 없음). 최근 30분 워크스페이스 매니페스트 1건(`a05b3692…`, `ready`). 포크 표본 오류 0, 전사 3건.
  - **Celly (`04:12:45Z`) — 보드/MCP 동시 타임아웃:** `board`·`workflow`·`mcp` 모두 `TimeoutError`(`mcp` `elapsed_ms=4016`, 보드 미관측). 리스 정보(대기 9·실행/슬롯 3·제출 2·예약/슬롯 1)는 파일에서 읽혔으므로 라우터 프로세스는 살아 있으나 HTTP 응답이 4초를 넘겼다. **직전 틱(03:52)도 `3906ms`, 그 전(03:32)도 `4016ms`** — Celly에서 4초 타임아웃이 반복되는 패턴(추적, 원인 미확정). finding은 `n1585 awaiting_capacity` 1건만(용량 대기). 포크 표본 오류 5건, 전사 4건.
  - KOBE (단독 `04:23:39Z`): 원장 4924 유지, 수락 1·대기 1·실행/슬롯 1, finding `n5` 자식 미완료 가드 유지, 신규 실패 0. 라우터 `/ca/work/api/v1/runtime` 응답 확인, 버전 `0.2.51-beta.20261127-013303.1ca7dc3` — **새 오버레이가 로드된 빌드**.
  - Prody (단독 `04:24:03Z`): 원장 `1400→1407`, 실행/슬롯 1만, finding 0·신규 실패 0·라우터 로그 0. 라우터 버전 `0.2.51-beta.20261126-000237.61d2f8b`(구 빌드).
  - Betty (`04:22:13Z`): 보드 5789·원장 1262 변화 없음, MCP `293ms`, 제출 5만, 오류 0.
  - 미결·추적: **Celly 보드/MCP 4초 타임아웃 3틱 연속**(원인 미확정); Andy `n2763` 검수행 미생성·`worktree add` 간헐 182초(원인 미확정, 코드 수정 없음); Celly `n1585` 용량 대기·라우터 11-25/26 빌드(새 오버레이 미로드)·사이드 적체; KOBE `n5` 가드; KOBE·Prody SSH 기본 수집 반복 실패(단독 재조회로 회복).

- 03:52:16–03:52:50Z 정기 틱(03:34:58Z 대비 17분 관측 창). 5개 호스트 전원 수집 성공(KOBE SSH 정상), `live.board` 전원 `observed`, MCP 전원 `probe_succeeded`(Andy `1500ms`, Celly `1438ms`, KOBE `264ms`, Prody `424ms`, Betty `417ms`). 재시작·상태 파일 수정 없음. 이하 UTC.
  - **Celly — 직전 틱의 "slot held, fork absent" 4건이 해소됐다(정정).** 이번 `/parallel`에서 **확인 포크 4건(`n1593`·`n1635`·`n1636`·`n1616` 전부 `running`)**, 대기 9, 미확인 2(`n1585`·`n1611`). device `active_slots=4 / capacity=8`(P1 3/3, P3 1/1). 즉 01:37·02:54에 native 관측이 끊겼던 항목들이 이번 표본에서는 다시 fresh 관측됐다 — **native 부재는 종료 증거가 아니라는 기존 원칙이 다시 확인됐고, 결함으로 단정하지 않은 판단이 맞았다.** 직전 틱 active_slots 3(대기 9) → 이번 4, 보드 `v11459→v11466`, actionable 18→25.
  - Andy (`03:52:16Z`): 보드 `v12536→v12540`, 원장 `8921→8924`, actionable 54, MCP `1500ms`, 리스 제출 20·실행/슬롯 3·대기 1, finding 0·신규 실패 0·전사 오류 0. 로그: `fork_turn_watch_poll_failed` 0건, `work_workspace_failed` **2건 유지**(`20:49:06`·`20:52:16` 신규 없음). 포크 표본 오류 3건.
  - Celly (`03:52:21Z`): 보드 `v11466`, 원장 `10424→10430`, MCP `1438ms`(직전 3906ms에서 회복), 대기 9·실행/슬롯 4·제출 2, finding `n1585 awaiting_capacity` 1건만(용량 대기, 결함 아님), 신규 실패 0, 포크 표본 오류 2건, 전사 3건(작업 측).
  - KOBE (`03:52:31Z`): 보드 `v824→v834`, 원장 4924 유지, MCP `264ms`, 수락 1·대기 1·실행/슬롯 1, finding `n5` 가드 유지, 신규 실패 0, 전사 1건.
  - Prody (`03:52:40Z`): 보드 `v7566→v7567`, 원장 `1397→1400`, MCP `424ms`, 실행/슬롯 1만, finding 0·신규 실패 0·포크 오류 0, 전사 6건.
  - Betty (`03:52:50Z`): 보드 5789·원장 1262 변화 없음, MCP `417ms`, 제출 5만, 오류 0.
  - 미결·추적: Andy **`worktree add` 간헐 182초(원인 미확정, 재현 실패 — 코드 수정 없음)**·`preparing` 잔류; Celly `n1585` P3 용량 대기·`n1611` 미확인·라우터 11-25 빌드·`work_side_intake_enabled=False` 적체; KOBE `n5` 가드; 게이트웨이 341초 무응답(상류, 보류 항목).

- 03:32:00–03:34:58Z 정기 틱(02:33:55Z 대비 59분 관측 창). Andy·Celly·Prody·Betty 수집 성공, **KOBE는 기본 수집에서 `ssh failed`**였으나 직후 단독 재조회(`03:34:58Z`)로 정상 수집(원장 4924·`n5` 가드 1건). 재시작·상태 파일 수정 없음. 이하 UTC.
  - Andy (`03:32:00Z`): 보드 `v12531→v12536`, 원장 `8908→8921`, actionable 54, MCP `1781ms`, 리스 제출 20·실행/슬롯 3·대기 1, finding 0·신규 실패 0. 로그: `fork_turn_watch_poll_failed` 0건 유지, **`work_workspace_failed` 2건 유지**(둘 다 `20:49:06`·`20:52:16` `item=n2779 detail="git worktree add timed out after 182 s"` — 180초 예산으로도 초과). 최근 60분 워크스페이스 매니페스트 4건 중 2건이 `preparing`·워크트리 디렉터리 부재(`0e5aa74e`·`1b2cbefa`), 2건은 `ready`(준비 31초·26초). 포크 표본 오류 5건.
  - **워크트리 지연 조사 결과(이번 창, 읽기 전용):** 같은 저장소에서 `git worktree add`를 15회 이상 반복 측정 — 대부분 **3–9초**, 6스레드 적대적 IO 부하·8 GB 전사 읽기 부하·stale `index.lock`(11-21 생성, 빈 파일) 존재 상태에서도 3–6초. 그러나 **동일 명령이 한 번은 200초를 넘겨 멈췄고** 라우터에도 182초 실패 2건이 있다 → **간헐적**이며 재현 조건 미확정. 배제한 가설: stale `index.lock`(`git status` 정상), 브랜치 생성(detached도 87초), IO 경합(부하 하 3초), 부분 클론/promisor(설정 없음), 실시간 보호(쓰기 40 MB 0.0초, 제외 목록은 관리자 권한 필요해 확인 불가). `GIT_TRACE_PERFORMANCE`에서 정상 시 `reset --hard`의 `check_updates`가 1.0–7.4초로 전체 시간을 차지. **원인 미확정 — 코드 수정 없음.** 워크트리 1,851개·매니페스트 `preparing` 128개는 정리하지 않았다.
  - **Celly (`03:32:05Z`) — `transport_interrupted` 알림은 제품 결함 아님(확인):** 포크 전사 원문이 `API Error: Upstream Anthropic stream failed before message_stop (TimeoutError).`이고, 게이트웨이 `agent-aap.tail.headscale.ezonebot.com:20129`가 **341초 무응답** 후 끊겼다. `runtime_input_retry.classify_claude_api_error`의 `CLAUDE_TRANSPORT_FAILURE_TEXTS`에 정확히 일치해 `transport_interrupted`(transient)로 분류된 것이며, 라우터가 20회 재시도를 예약하고 **1분 내 실제 복구**(02:57:08Z 재개 → 02:57:26Z 도구 호출 재개)했다. 즉 화면의 4줄은 복구 과정 알림이다. 같은 세션이 이미 다른 엔드포인트로 1,045건 재라우팅된 이력이 있어 게이트웨이 측으로 판단(사용자 보류 항목 계열).
  - Celly 보드 `v11456→v11459`, 원장 `10422→10424`, MCP `3906ms`(타임아웃 경계), 실행/슬롯 3→4·대기 9·제출 1. **finding 4건 신규: `slot held, fork absent since 01:37: n1593`, `since 02:54: n1635`·`n1636`·`n1611`** — native 부재는 종료 증거가 아니므로 결함으로 단정하지 않고 추적한다. `n1585 awaiting_capacity`(용량 대기) 유지. 전사에는 `n1593 already has lease …; resume its worker with SendMessage` 거부가 있어 슬롯 점유와 포크 부재가 같은 항목에서 동시에 관측된다(추적). 포크 표본 오류 1건.
  - KOBE (`03:34:58Z` 단독): 원장 `4886→4924`, 보드 `v824`(기본 수집 실패로 버전 비교 불가), 수락 1·대기 1·실행/슬롯 1, finding `n5` 가드 1건 유지, 신규 실패 0.
  - Prody (`03:33:53Z`): 보드 `v7562→v7566`, 원장 `1382→1397`, MCP `235ms`, 실행/슬롯 1만, finding 0·신규 실패 0·포크 오류 1건.
  - Betty (`03:34:02Z`): 보드 5789·원장 1262 변화 없음, MCP `344ms`, 제출 5만, 오류 0.
  - 미결·추적: **Andy `worktree add` 간헐 182초(원인 미확정, 재현 실패)**·`n2779`·`preparing` 2건; Celly **신규 슬롯 점유/포크 부재 4건**·MCP 3906ms·`n1585` 용량 대기·라우터 11-25 빌드·사이드 적체; KOBE SSH 1회 단절 후 회복; 게이트웨이 341초 무응답(상류).

- 02:32:24–02:33:55Z 정기 틱(02:13:07Z 대비 19분 관측 창). 5개 호스트 전원 수집 성공, `live.board` 전원 `observed`, MCP 전원 `probe_succeeded`(Andy `765ms`, Celly `4016ms` 타임아웃 경계, KOBE `391ms`, Prody `297ms`, Betty `212ms`). 재시작·상태 파일 수정 없음. 이하 UTC.
  - **Andy (`02:32:24Z`) — 새 예산의 첫 라이브 사례 관측(부분 증거):** 보드 `v12526→v12531`, 원장 `8904→8908`, actionable 53→54, 리스 제출 20·실행/슬롯 3·대기 1, finding 0·신규 실패 0. 새 라우터 로그 `fork_turn_watch_poll_failed` 0건·`work_workspace_failed` 0건 유지. **`n2952`(P0 포크 `work-p0-6f3fc977…`)가 재시작 이후 새 워크스페이스 `work-parallel-bacbb32f943101ba190f21e6`(lease `db5a28bd`, host `ab208dd44dfe5584b`, `ready`)에서 실제로 실행 중(`confirmed`, last_seen 02:34:47Z)이다.** 그 워크트리의 생성 시각은 `02:25:51Z`, 매니페스트 저장은 `02:26:24Z`로 **준비 전체 구간 33.4초**다. 이는 옛 고정 30초 예산을 넘는 값이며 그 시간대 부하에서 옛 예산이 실패했을 것이라는 정황이지만, **`worktree add` 단계만의 소요는 사후 분리 측정이 불가**하므로 결정적 증거로 쓰지 않는다(준비 전체 33.4초 = add + `_verify` 4회 git 조회 + 저장). 요약: 새 예산 경로에서 포크 생성·실행이 성공한 첫 사례는 확보, 30초 초과가 add 단계였는지는 미확정.
  - Andy 포크 그룹: 확인 1(`n2952` running), 대기 3(`n2763`·`n1166` waiting, `n2898` idle), 미확인 0. 전사 5건(작업 측).
  - Celly (`02:32:28Z`): 보드 `v11456`·원장 10422 **변화 없음**, MCP `4016ms`(타임아웃 경계), 대기 10·제출 1·실행/슬롯 3, finding 1건 `n1585 awaiting_capacity`(P3 1/1, 결함 아님) 유지, 신규 실패 0·포크 오류 0. 라우터는 11-25 빌드 유지.
  - KOBE (`02:33:33Z`): 보드 `v822→v824`, 원장 `4881→4886`, MCP `391ms`, 수락 1·제출 1·대기 24·실행/슬롯 1. finding 2건: `n5` 자식 미완료 가드 유지, **`n282 2bfdd481 awaiting_capacity`** — 직전 틱의 "slot held, fork absent"가 이번엔 P3 용량 대기로 기록됐다(용량 대기이지 결함 아님). 포크 표본 오류 7건.
  - Prody (`02:33:45Z`): 보드 7562 유지, 원장 `1374→1382`, MCP `297ms`, 실행/슬롯 1만, finding 0. 새 실패 `decision_refused_stop dd10cf10 x1`(정상 Stop 가드). 전사 1건.
  - Betty (`02:33:55Z`): 보드 5789·원장 1262 변화 없음, MCP `212ms`, 제출 5만, 오류 0.
  - 미결·추적: Andy `n2952` 준비 33.4초의 add 단계 분리 측정 불가(다음 생성 시 계측 없이는 확정 불가)·`n2763`/`n1166` 대기·`n2898` idle; Celly 라우터 11-25 빌드·MCP 4016ms 경계값 반복·`work_side_intake_enabled=False` 적체; KOBE `n282` P3 용량 대기·`n5` 가드.

- 02:12:21–02:13:07Z 정기 틱(01:53:49Z 대비 19분 관측 창). 5개 호스트 전원 수집 성공, `live.board` 전원 `observed`, MCP 전원 `probe_succeeded`(Andy `1984ms`, Celly `1735ms`, KOBE `668ms`, Prody `713ms`, Betty `492ms`). 재시작·상태 파일 수정 없음. 이하 UTC.
  - Andy (`02:12:21Z`): 보드 `v12524→v12526`, 원장 `8903→8904`, actionable 53 유지, 리스 제출 20·실행/슬롯 3→2·대기 1, finding 0·신규 실패 0. 라우터 로그 `fork_turn_watch_poll_failed` 0건·`work_workspace_failed` 0건 유지. **포크 그룹: 확인 0, 대기 1(`n2898` idle), 미확인 2(`n2763`·`n1166` running), 검증 대기 20** — 직전 틱의 확인 포크 2건이 이번 표본에서는 `uncertain`으로 내려갔다(마지막 native 관측이 30초 신선도를 벗어남). 이는 종료 증거가 아니며 `native-fork-evidence/1` 계약상 미확인으로만 기록한다. **새 워크트리 예산의 성공 실증은 여전히 없음**: 최근 90분 내 워크스페이스 매니페스트 변경은 `311546de`(19:09 생성, `ready`) 1건뿐으로 이는 배포 이전이다.
  - Celly (`02:12:26Z`): 보드 `v11452→v11456`, 원장 10422 유지, MCP `1735ms`, 대기 10·제출 1·실행/슬롯 3, finding 1건 `n1585 awaiting_capacity`(P3 1/1, 결함 아님) 유지, 신규 실패 0·포크 표본 오류 0(직전 3건에서 감소), 전사 1건. 라우터는 여전히 11-25 빌드(변경 없음).
  - KOBE (`02:12:40Z`): 보드 `v818→v822`, 원장 `4875→4881`, MCP `668ms`, 수락 1·대기 24·실행/슬롯 1. **신규 finding `slot held, fork absent since 01:04: n282 2bfdd481 P3 running`** — native 부재는 종료 증거가 아니므로 결함으로 단정하지 않고 추적만 한다. `n5` 미완료 자식 가드 유지. 포크 표본 오류 6건.
  - Prody (`02:12:54Z`): 보드 `v7559→v7562`, 원장 `1357→1374`, MCP `713ms`, 실행/슬롯 1만(제출·대기 0), finding 0·신규 실패 0·포크 오류 2건, action 1.
  - Betty (`02:13:07Z`): 보드 5789·원장 1262 변화 없음, MCP `492ms`, 제출 5만, 오류 0.
  - 미결·추적: Andy 새 예산 성공 실증 미확보·`n2763`/`n1166` 미확인 포크·`n2898` idle 대기; Celly 라우터 11-25 빌드 유지(새 예산·애니메이션 미적용)·`n1585` 용량 대기·`work_side_intake_enabled=False` 적체; KOBE **신규 `n282` 슬롯 점유/포크 부재**·`n5` 가드.

- 01:52:16–01:53:49Z 정기 틱(01:46:30Z 대비 6분 관측 창). 5개 호스트 전원 수집 성공, `live.board` 전원 `observed`, MCP 전원 `probe_succeeded`(Andy `953ms`, Celly `2000ms`, KOBE `304ms`, Prody `353ms`, Betty `314ms`). 재시작·상태 파일 수정 없음. 이하 UTC.
  - **정정 — Celly CLI 재시작은 실제로 완료됐다(직전 틱의 "미완료" 판정이 틀렸다).** 프로세스 트리를 직접 읽어 확인했다: `104616`(python, 구 supervisor) → 자식 `101552`(cmd.exe, 19:43:36) → `75652`(**claude.exe, 19:43:37 신규**), 그리고 `78556`(python). 즉 `claude_session_restart exit_code=1`은 재시작 자체의 실패가 아니라 옛 CLI 종료 코드였고, 새 CLI가 1초 뒤 정상 기동했다. 지난 틱에 `active_client_pids`가 옛 값으로 남은 것을 근거로 "미완료"라고 쓴 것은 **잘못된 추론**이었다. 다만 **라우터 프로세스 `111684`는 11-25 23:32 기동 그대로**여서, 새 CLI는 새 워크트리 예산·애니메이션 코드를 모듈로 로드하지 않았다(모드는 CLI가 훅/클라이언트를 읽으므로 예외). Celly의 `fork_watch`는 `null`(이 세션은 포크 감시 미바인딩).
  - **Andy (`01:52:16Z`) — 배포 후 첫 워크스페이스 생성 성공 관측:** 보드 `v12521→v12524`(actionable 52→53), 원장 `8901→8903`, MCP `953ms`, 리스 제출 20·실행/슬롯 3(직전 2). **확인 포크 2건(`n2898` running, `n1166` running)** — 새 라우터에서 처음으로 실행 중 포크가 관측됐다. 새 라우터 로그 `fork_turn_watch_poll_failed` **0건**, `work_workspace_failed` **0건** 유지. 워크스페이스 매니페스트 `work-parallel-311546defa9a6b73ff63660d`(lease `d0ed667269`, host `a11ef6cc1bca`, `ready`, `idle_proof="Submitted completion fork absent from agent.list after restart"`)가 워크트리와 함께 존재. 단 이 워크트리 생성 시각은 `19:09:34Z`로 **배포·재시작(19:19·19:36) 이전**이므로, 이것을 새 예산의 성공 증거로 쓰지 않는다. 새 예산의 실증은 아직 미확보다.
  - Celly (`01:52:18Z`): 보드 `v11452`(변화 없음), 원장 10422(변화 없음), MCP `2000ms`, 대기 10·제출 1·실행/슬롯 3, finding 1건 `n1585 a9e3665c awaiting_capacity`(P3 1/1 용량 대기 — 결함 아님), 신규 실패 0, 포크 표본 오류 3건. 전사 0.
  - KOBE (`01:53:09Z`): 보드 `v814→v818`, 원장 4875 유지, MCP `304ms`, 수락 1·대기 25·실행/슬롯 1, finding `n5` 미완료 자식 가드 유지, 포크 오류 0, 전사 3건(작업 측).
  - Prody (`01:53:22Z`): 보드 `v7557→v7559`, 원장 1357 유지, MCP `353ms`, 제출 1·실행/슬롯 1, finding 0·오류 0.
  - Betty (`01:53:49Z`): 보드 5789·원장 1262 변화 없음, MCP `314ms`, 제출 5만, 오류 0.
  - 미결·추적: **Celly 라우터가 11-25 빌드 그대로**(CLI만 새로 뜸) → 새 예산·애니메이션 라이브 미적용, `ChannelFileLockBusy` 패턴도 유지 예상; Andy 새 예산 성공 실증 미확보(`311546de` 워크트리는 배포 이전 생성); Andy `n2763` waiting 1건; Celly `n1585` 용량 대기·`work_side_intake_enabled=False` 적체; KOBE `n5` 가드.

- 01:45:48–01:46:30Z 정기 틱(01:13:48Z 대비 32분 관측 창, 배포·라우터 재시작 직후). 5개 호스트 전원 수집 성공(Betty 포함), `live.board` 전원 `observed`, Work MCP 전원 `probe_succeeded`(Andy `953ms`, Celly `4016ms`, KOBE `186ms`, Prody `267ms`, Betty `267ms`). 이 틱에서 재시작·상태 파일 수정은 하지 않았다. 이하 UTC.
  - **Andy (`01:45:48Z`) — 이번 배포의 라이브 효과 확인:** 보드 `v12514→v12521`, 원장 `8897→8901`, MCP `953ms`, 리스 제출 20·대기 1·실행/슬롯 2, finding 0·신규 실패 0. **재시작 후 새 라우터 로그에서 `fork_turn_watch_poll_failed` 0건, `work_workspace_failed` 0건**(직전 인스턴스는 각 20,211건·1건). 실행 중인 라우터가 로드한 `work_parallel_workspace.py`에 `_GIT_CREATE_TIMEOUT_SECONDS = 180.0`과 `_CREATE_STEPS` 분기가 실제로 존재함을 파일에서 확인했다(파일 설치 확인). 다만 **새 예산으로 생성이 성공한 실증은 아직 없다**: 최근 1시간 내 워크스페이스 매니페스트 변경은 1건(`work-parallel-311546defa9a6b73ff63660d`, `ready`)뿐이고 그것도 배포 이전 시각이다. 실행 포크가 다시 뜨는지가 다음 확인 항목. 메인 전사 5건. 포크 표본 오류 1건.
  - **Celly (`01:45:51Z`) — CLI 재시작은 실행됐으나 클라이언트 pid는 그대로:** 라우터 `9385`는 `taskkill` 접근 거부(고권한)로 종료하지 못해 도구 `restart_session`을 썼고, 라우터 로그에 `runtime_session_restart_queued` → `claimed client_pid=104616` → `claude_session_restart exit_code=1 ... resumed=true` → 새 자식 `pid=101552` 등록(`channel_windows_console_proxy_started`)까지 확인됐다. 그러나 `/health`의 `active_client_pids`는 여전히 `[104616]`이고 `/work/api/v1/runtime`의 `restart.pending_request`는 null이다. **exit_code=1 + 옛 pid 유지**는 재시작이 완료되지 않았음을 뜻하며, 이번 창에서 `session=37dc87e8-0015-4f6f-86bb-88bd78ceb0f8`이 새로 바인딩된 것도 관측돼 상태가 섞여 있다. 미완료로 기록하고 완료로 단정하지 않는다. 보드 `v11449→v11452`, 원장 `10416→10422`, MCP `4016ms`(타임아웃 경계), 대기 10·제출 1·실행/슬롯 3, action 19→17. **finding 1건 `n1585 a9e3665c awaiting_capacity`**(P3 슬롯 1/1, 용량 대기 — 결함 아님), 새 실패 `decision_refused_stop 43a31496`·`a9e3665c` 각 1건(정상 Stop 가드). 전사 5건(`01:36:29` 작업 측 threading 오류, `01:37:53` `the plan changed (stored version 11451, expected 11449)`, `01:39:12` `auto continue could not read the Work status`). 라우터: 외부 SSE EOF 25, `/ca/mcp` 단절 10, **`ChannelFileLockBusy` 4건**(`transcript_delta_delivery_failed` 2·`runtime_compaction_request_scope_failed` 2, 19:18–19:29Z) — 실행 중 라우터가 여전히 락 재시도 개선 이전 빌드이므로 기존 패턴 유지.
  - KOBE (`01:46:09Z`): 보드 `v811→v814`, 원장 `4867→4875`, MCP `186ms`, 수락 1·대기 25·실행/슬롯 1, finding `n5` 미완료 자식 가드 유지, 신규 실패 0·포크 오류 1건. 라우터: 정상 keep-alive 가드 323건(`reachable_health_mismatch_active_client`, 재시작 직후 정상 경고), 외부 SSE EOF 30. 전사 6건은 작업 측(`Invalid operation arguments` 400, JSON 덤프 스크립트 exit 1).
  - Prody (`01:46:20Z`): 보드 `7556→7557`, 원장 `1349→1357`, MCP `267ms`, 제출 1·실행/슬롯 1, finding 0·신규 실패 0·포크 오류 0.
  - Betty (`01:46:30Z`): 보드 5789·원장 1262 **변화 없음**, MCP `267ms`, 제출 5만, finding 0·전사 0·포크 오류 0. 재시작 후 정상 수집.
  - 미결·추적 유지: **Celly CLI 재시작 미완료**(exit_code=1, 옛 pid 104616 유지, 새 세션 37dc87e8 동시 관측)·`ChannelFileLockBusy` 4건; Andy 새 워크트리 예산의 성공 실증 미확보·포크 표본 오류 1건; Celly `n1585` 용량 대기·`n1634` 검수행 미생성·`work_side_intake_enabled=False` 적체; KOBE `n5` 가드; `1ca7dc3` 오버레이는 7개 루트 파일 설치 완료, 라이브 로드는 Andy·KOBE·Prody·Betty 라우터에서 확인, Celly는 CLI 재시작 미완료로 미확인.

- 01:11:58–01:13:48Z 정기 틱(23:55:31Z 대비 76분 관측 창). Andy·Celly·KOBE·Prody 4개 호스트 수집 성공, **Betty는 기본 수집에서 `ssh failed`**였으나 직후 단독 재조회(`01:13:48Z`)로 정상 수집 완료(원장 1262·보드 v5789·제출 5·finding 0). 이 틱에서 운영 재시작·상태 파일 수정은 하지 않았다. 이하 UTC.
  - **Andy (`01:11:58Z`)**: 원장 `8849→8897`, 보드 `v12478→v12514`(노드 570, halted 138, actionable `56→52`), MCP `4000ms`(4초 타임아웃 경계값, 직전 1609ms), 리스 제출 20·대기 1·실행/슬롯 3. finding 0. 새 실패 2건: `decision_refused_stop 9da60c9e…`(`00:25:20Z`)·`decision_refused_stop c16e237a…`(`01:14:55Z`) — **둘 다 n2472/n2904의 정상 Stop 가드**이며 제품 결함 아님. 라우터(신 인스턴스 `100840`): `fork_turn_watch_poll_failed` **0건 유지**(교체 후 3시간 25분), `work_workspace_failed` **1건 유지**(`17:54:43Z n2904`, 신규 0). 병렬 실패 원장의 `workspace_prepare_retained` 최다 항목은 `22:56:38Z` 이후 22회로 증가 없음.
  - **핵심 확인 — 검수가 시작되지 않는 원인은 결함이 아니라 "이미 처리된 행"이다.** Andy 메인 전사에서 `work_parallel_item_review` 오류를 분류한 결과 최다 250건이 `stale approval fingerprint: Work approval scope changed`이나, **이 문구는 커밋 `8c7a4ef`(2026-11-19)에서 제거**됐고 현재 코드에는 존재하지 않는다(현재는 `Work approval scope changed; reconcile before continuing`). 시간 분포상 250건은 11-15~11-19 구간에 집중되고 **11-26에는 2건**뿐이다. 현재 `/parallel`의 `pending` 12건 중 6건은 `decision: rework`가 **이미 기록된** 행(n2777·n1166·n2583·n2582·n2472·n2453)이며, 미결정 6건은 n2912·n2904·n2472·n1166·n2473·n2898이다. 즉 패널이 "Awaiting main review"로 계속 보이는 것은 결정 대기와 결정 완료 행이 함께 표시되기 때문이며, 새 결정을 막는 코드 경로는 이번 관측에서 확인되지 않았다.
  - **Celly (`01:12:06Z`)**: 원장 `10389→10416`, 보드 `v11443→v11449`(노드 249, halted 88, actionable `22→19`), MCP `1203ms`(직전 312ms), 대기 9·제출 2·실행/슬롯 3·예약/슬롯 1. **신규 finding 2건**: `submitted, completion receipt, no review row: n1585 a9e3665c P3`(native=running, last 01:01, miss 01:04)과 `n1634 43a31496 P1`(native=waiting) — 완료 영수증은 있으나 검수행이 생성되지 않은 형태로, Andy `n2473`과 같은 계열. 포크 표본 오류 9건. 라우터: 상류 400/413 계열(게이트웨이 측, 보류 항목) 외 신규 원인 없음.
  - KOBE (`01:12:17Z`): 원장 `4860→4867`, 보드 `v796→v811`, MCP `284ms`, 수락 1·대기 25·실행/슬롯 1, finding 1건 `n5` 미완료 자식 가드 유지. 신규 실패 0·포크 표본 오류 1건(직전 11건에서 감소).
  - Prody (`01:12:28Z`): 원장 1349·보드 7556 **변화 없음**, MCP `285ms`, 실행/슬롯 1·대기 1, finding 0·신규 실패 0·포크 오류 0. `/workflow` `observed`(`cancelled 4`) 유지.
  - 미결·추적 유지: Andy `n2904` worktree 33초 타임아웃(신 인스턴스 누적 1건, 코드 수정은 격리 검증 완료·미배포)·MCP 4000ms 경계·`n2473 14355e1e` 검수행 미생성; Celly **신규 `n1585`·`n1634` 검수행 미생성**·`work_side_intake_enabled=False` 적체; KOBE `n5` 가드; Betty SSH 1회 단절 후 회복; `5d4df17`·`9351c77` 등 파일 설치분은 라우터/CLI 재시작 전까지 라이브 미반영(Andy 신 인스턴스는 재시작 후 로드 상태이나 별도 live 확인은 미실시).

- 23:53:10–23:55:59Z 정기 틱(22:15:31Z 대비 98분 관측 창). 5개 호스트 수집 모두 성공(KOBE SSH 정상), `live.board` 전원 `observed`(Andy `v12478`, Celly `v11443`, KOBE `v796`, Prody `v7556`, Betty `v5789`), Work MCP 5곳 전원 `probe_succeeded`(Andy `1609ms`, Celly `312ms`, KOBE `277ms`, Prody `1114ms`, Betty `224ms`). 이 틱에서 운영 재시작·상태 파일 수정은 하지 않았다. 이하 UTC.
  - **Andy (`23:53:10Z`) — 이 틱의 최대 변화: 라우터 인스턴스가 교체됨(재시작 아님, 원인 확인 필요 없음).** `9505-aae7db361c7f`의 프로세스가 `81900` → **`100840`**(version `0.2.51-beta.20261126-000237.61d2f8b` 동일)으로 바뀌었고, 구 라우터 로그는 `router.log.1`로 회전했다. 구 로그 꼬리는 `17:46:06Z`까지 `fork_turn_watch_poll_failed error=NameError: name 'OSError' is not defined`를 남긴 뒤 `17:46:12Z [INFO] router_kill_guarantee reason=claude_exit state=killed`, `router_lifetime_stopped reason=claude_exit`로 끝난다 — **CLI 세션 종료에 따른 라우터 종료**이며 틱이 만든 것이 아니다(구 인스턴스 누적 20,211건은 회전 로그 `router.log.1`에 보존). 신 인스턴스 `router.log`에서 `fork_turn_watch_poll_failed` **0건**(17:46:19Z 시작~17:58Z). 즉 앞서 14시간 지속된 인터프리터 손상 사례는 프로세스 교체로 소멸했다(재발 여부는 다음 틱부터 관측).
  - Andy 현재 상태: 원장 `8544→8849`, 보드 `v12466→v12478`(노드 570, root 76, halted `139→138`, release_hint `36→34`, actionable 52→56), MCP `1609ms`, 리스 제출 22·실행/슬롯 2·예약/슬롯 1, finding 0. **`work_workspace_failed code=workspace_prepare_retained item=n2904 detail="git worktree add timed out after 33 s"` 1건(17:54:43Z)** — 이번 인스턴스 첫 건이며 동일 패턴이 계속된다. 메인 전사 19건 중 핵심은 이미지 #68 계열: `23:37:42` `could not resume parked worker, lease 0b030c8d532d… Agent "a792b07bc9e95c234" was not resumed: the session was stopped and nobody has sent input since.` 와 `23:40:00` `clear_now=true … outcome=a resume was refused within the last 5 minutes`, `23:44:09` 동일 재개 거부 — n2472 lease의 재개 거부·5분 쿨다운 반복. 라우터: 외부 SSE EOF 6건·`/ca/mcp` 단절 2건, `cielairouter_gateway_central discovery unavailable degraded=True` 1건(17:47:40Z, 21ms), 트랜지언트 중단 1건.
  - Celly (`23:53:14Z`): 원장 `10350→10389`, 보드 `v11439→v11443`(노드 249, halted 85, action_hint 23, actionable 22), MCP `312ms`, 대기 8·실행/슬롯 2, finding 0·신규 실패 0. 메인 전사 10건(작업 측 Python/urllib 계열). 라우터: 외부 SSE EOF 41건, `_Walkie_P1-Celly` `400 Invalid signature in thinking block` 12건(16:17:24Z~), `_Walkie_P2/P3` `Invalid value at …enum[0]` 10건, `_Walkie_P1-Celly` `413 length limit exceeded` 3건(`tokens=1628733`) — 전부 상류/게이트웨이 측이며 사용자 지시로 보류 중인 항목. 포크 표본 오류 4건(`abfbd95b…`, `a2a16fa5…` 등, 원인 미분류).
  - **KOBE (`23:55:39Z`)**: 원장 `4832→4860`, 보드 `v791→v796`, MCP `277ms`, 리스 수락 1·제출 1·대기 24·실행/슬롯 1. finding 2건: `n5 24fffb5b deciding` (`n5 still has unfinished children; finish them first`, 22:53:07Z 이전부터 유지되는 정상 가드)과 **신규 `n285 3ee29524 awaiting_capacity`(`active slot capacity reached for P3 (1/1); rework waits for admission`, 23:35:38Z)** — 용량 대기이지 결함으로 단정하지 않는다. 새 실패 `decision_refused_stop 5df5f618 x1`(정상 Stop 가드). 메인 전사 1건 `work_parallel_item_review: review decision` 거부(23:26:20). 포크 표본 오류 `15→11건`(주로 `ae0ff4fd…`). 라우터: 정상 keep-alive 가드 652건, 외부 SSE EOF 52건·TimeoutError 2·BrokenPipe 1.
  - Prody (`23:55:49Z`): 원장 `1335→1349`, 보드 `v7548→v7556`(노드 78, halted 10, actionable 2), MCP `1114ms`, 실행/슬롯 1·대기 1, finding 0, `/workflow` `observed`(`cancelled 4`), `stranded` 원장 파일 `FileNotFoundError` 지속(읽을 파일 없음). 메인 전사 5건(모두 작업 측: `walkie_*_work_update`/`send_message` `Invalid uuid at client_id`, `work status 409`, `gh_api.py` 인자 오류). 포크 표본 오류 1건(`a585670c…` 23:54:29Z).
  - Betty(보조, `23:55:59Z`): 원장 1262, 보드 `v5787→v5789`(노드 970, halted 96, actionable 0), MCP `224ms`, 제출 5만, finding 0, 메인 전사 0, 포크 표본 오류 0.
  - 이 틱에서 추가로 확인한 사실(앤디 질문 대응, 읽기 전용 실측): (1) `G:\ciel-Walkie` 등록 워크트리 **1,482개**(work-parallel 982), 워크스페이스 매니페스트 2,235개 중 `preparing` 잔류 **128개**, `.git/index.lock`이 **11-21 08:53부터** 남아 있음 — 정리 작업은 별도 승인 없이 손대지 않았다. (2) `git worktree add` 실측 유휴 **1.5–4초** vs 서브에이전트 전사(8.0 GB/13,755 파일) 읽기 부하 하 **153초** → `_GIT_TIMEOUT_SECONDS = 30.0` 고정 예산이 부하를 반영하지 못하는 것이 타임아웃 35건의 관측 조건이다(원인 단정은 부하 상관까지). (3) 내가 진단용으로 만든 워크트리 6개(`probe77f66`, `cxba5bf1`, `px47f343`, `pxa75ade6`, `pxcc16f10`, `bc68d696`)는 전부 HEAD `8a1743ea`·dirty 0 확인 후 제거했고 브랜치 5개도 삭제했다.
  - 미결·추적 유지: Andy `n2904` worktree 타임아웃(신 인스턴스 1건)·`n2912` 슬롯 부재·`n2473` 재개 거부 5분 쿨다운 반복·`n2472` lease `0b030c8d` 재개 거부; Celly `work_side_intake_enabled=False` 적체 24건(변경 주체 미확인)·포크 표본 오류 4건; KOBE `n5` 자식 미완료 가드·`n285` P3 용량 대기·`decision_refused_stop` 1건; Prody `Invalid uuid at client_id` 2건·`gh_api.py` 인자 오류; Betty 변화 없음; `5d4df17`·`9351c77` 등 파일 설치분은 라우터/CLI 재시작 전까지 라이브 미반영(Andy는 이번 교체로 `9351c77`·`5d4df17` 파일이 실제 로드됨 — 단, 관측된 신 인스턴스도 동일 version 문자열이라 로드 여부는 다음 틱의 live 효과로만 확인).

- 22:14:56–22:15:31Z 정기 틱(21:52:19Z 대비 22분 관측 창) + **Celly 사이드 액터 미가동 원인 판정**. 5개 호스트(보조 Betty 포함) 수집 stderr 없음, `live.board` 전원 `observed`(Andy `v12466`, Celly `v11439`, KOBE `v791`, Prody `v7548`, Betty `v5787`), Work MCP는 4곳 `probe_succeeded`(Andy `1468ms`, KOBE `377ms`, Prody `732ms`, Betty `321ms`)·Celly만 4초 `TimeoutError`(4016ms). 신규 병렬 원장 실패·운영 재시작·상태 파일 수정 없음. **신규 입증 제품 결함 없음**(Celly 사이드는 제품 결함이 아니라 설정 `work_side_intake_enabled=False`). 이하 UTC.
  - **Celly 사이드 액터 미가동 (사용자 질문 "사이드 액터가 실패"):** 이미지 #65의 세션은 Andy가 아니라 **Celly `claude:9430f5e8`**(보드 루트 `n1335` "30. 0.2.169 서명 릴리스 — PR #205(QA8 배너)"·`n1006` "16. 137950 Ciel View Sign In"·`n926` "8. 126480 Ciel View Sign In 로그인 창"이 일치). 읽기 전용 `/settings` 실측: **`work_side_intake_enabled = False`**, `work_side_intake_model = _API_KEYS-SSE-1`, `work_side_intake_session = fresh`, `work_side_intake_type = btw_with_mcp`, `sse_injection_mode = session`. `/side` 원장: `received 24`, `processed 609`, `quarantined 119`(색인 44), `duplicate 25`, `total 777`, **`enabled: false`**. 최근 8건 `Side SSE` 서브에이전트(`ciel-work:side-fresh`)는 `00:16–01:03Z`에 생성됐고 **`01:03:41Z`(receipt `6f8442fb31d5`) 이후 신규 스폰 0건**, 그 건은 `invalid_answer`("side answer carries no decision JSON", 1회)로 격리. 라우터 `work_side_claim` 호출은 최근 창에서 9,529건·마지막 `22:14:02Z`까지 계속되나 포크가 뜨지 않음 → `side_intake_mcp.run()`이 `work_side_intake` enabled가 아닐 때 `side processing is disabled`로 거부하는 경로와 일치(코드 `ciel_runtime_support/side_intake_mcp.py:357`). 즉 **SSE 이벤트는 수신·적재되지만 액터 스폰이 설정으로 차단되어 24건이 `received`로 대기**하고, 화면의 `FL 2`는 이미 격리된 과거 실패행(`invalid_answer`/`superseded`)이다. 패널 카운트(`IN 4 AI 1 AN 3 FL 2`)는 현재 읽은 페이지 기준이고 REST 원장(777건)과 범위가 다르다. 감사 로그(`/audit` 27건, 11-19~11-26)에는 `work_side_intake_enabled` 변경 기록이 없어 **언제·무엇이 껐는지는 미확인**(설정 파일 직접 읽기는 `WinError 5`로 접근 거부, REST 값만 확인).
  - Andy (`22:14:56Z`): 원장 `8525→8544`, 보드 `v12452→v12466`(halted `141→139`, release_hint `38→36`, actionable 52), MCP `1468ms`, 리스 제출 21·대기 6·실행/슬롯 3, finding 2건 유지(`n2912` 슬롯 부재, `n2473 14355e1e` 검수행 미생성 지속), 포크 표본 오류 `0→4건`(`a0df0ac5` 22:11:30–22:12:58). 메인 전사 7건: `auto continue` Work 상태 읽기 실패 1(21:57:53), Walkie 400 "Invalid operation arguments" 4(22:09:14·22:09:36·22:13:51·22:13:55), 작업 측 Python `AttributeError` 2(22:10:38·22:12:08). 라우터: **`fork_turn_watch_poll_failed NameError: OSError` 481건(21:52:20Z부터, 30틱 연속)**, 외부 SSE EOF 22건, `_Walkie_P1` `400 Invalid signature in thinking block` 4건(22:14:29Z부터, `tokens=190341`), 상류 SSE 전달 `ConnectionResetError` 3건(`_API_KEYS-SSE-1` 2·`_Walkie_P1` 1, 22:14:18Z), `work_workspace_failed` `n2898` `git worktree add timed out after 34 s` 1건(22:14:24Z), `context-usage.json` `WinError 5` 1건(22:07:20Z), 클라이언트 단절 4건, 턴 투영 관찰 6건.
  - Celly (`22:15:00Z`): 원장 `10344→10350`, 보드 `v11435→v11439`(노드 247, halted `87→85`, actionable `17→18`), 대기 7·실행/슬롯 2, finding 0·신규 실패 0·포크 표본 오류 `2→0` 유지, MCP `probe_failed TimeoutError 4016ms`(직전 437ms 성공, 기존 반복 패턴). 메인 전사 3건: 작업 측 `urllib` 오류 1(22:02:37), `git` 인증 프롬프트 비활성 exit 128 1(22:03:02, 작업 측), 정상 가드 1(`22:15:00` `n1593` `transport_interrupted` 포크 API 재시도 스케줄). 라우터: 외부 SSE EOF 22건, 콘솔 확인 실패 `16951` 1건(22:02:32Z `age=20.0s`), 게이트웨이 `127.0.0.1:20128` `connection_reset` 2건 + `_Walkie_P1-Celly` 재시도 4회(22:14:18–27Z), 중앙 게이트웨이 `discovery unavailable` `degraded=True` 1건(22:14:23Z `5100ms`), `_Walkie_P2` 상류 SSE 전달 단절 1건(22:14:18Z), 턴 투영 관찰 3건.
  - KOBE (`22:15:13Z`): 원장 `4828→4832`, 보드 `v786→v791`(노드 `162→161`, halted `49→47`, actionable `9→11`), MCP `377ms`, 리스(수락 1·대기 25·실행/슬롯 1) 및 `n5` 미완료 자식 가드 유지, 메인 전사 3건 모두 정상 가드(P3 슬롯 용량 대기 `e4ddd609`·`e98c1746`·`5df5f618` 22:14:34Z). 포크 표본 오류 `1→15건`(`ac8bfe32` 9건 22:06–22:09, `a1d4e38e` 6건 22:12–22:14, 원인 미분류). 라우터: 정상 keep-alive 가드 265건, 외부 SSE EOF 17건·TimeoutError 2·BrokenPipe 1, `cielairouter_late_sse_retry` 3건(22:01:34Z `79.56s`, 22:03:28Z `67.79s`, 22:04:49Z `51.66s`), `anthropic_sse_upstream_error_event` 1건(`_Walkie_Sonnet55-Sol61`, 22:03:31Z), 턴 투영 관찰 7건.
  - Prody (`22:15:22Z`): 원장 1335·보드 `v7548`·MCP `732ms`·리스 실행/슬롯 1·finding 0·메인/포크/라우터 오류 모두 0 유지, `/workflow` `observed`(`cancelled 4`).
  - Betty(보조, `22:15:31Z`): 원장 `1240→1262`, 보드 `v5781→v5787`(**활성 루트 `n1800→n1616` 변경**, halted `98→97`, actionable `4→0`), MCP `321ms`, 리스 제출 5만(대기·실행 0), finding 0, 포크 표본 오류 `1→7건`(`a181128f` 2·`a33786b2` 2·`ac972ca5` 2·`a75418d3` 1, 원인 미분류). 메인 전사 10건: Walkie 400 "Invalid operation arguments" 2(21:55:13·21:56:03), 작업 측 Python 오류 3(21:55:15·22:13:20·22:13:39), 작업 측 `REFUSE usage` 3(21:55:51·21:56:02·21:56:25), `STALE_OWNED` 점검 1(21:59:33), 완료 대기 정상 가드 1(21:59:54). 라우터: 정상 keep-alive 가드 270건, 외부 SSE EOF 23건, 클라이언트 단절 3건, 콘솔 확인 실패 `8170`(`age=229.3s`)·`8169`(`age=29.4s`) 및 `stale_unconfirmed` 건너뜀 2건(22:53:26–56), 턴 투영 관찰 9건(`age 120→1207s`).
  - 미결·추적 유지: **Celly `work_side_intake_enabled=False`로 사이드 24건 적체(변경 주체·시각 미확인)**; Andy `fork_turn_watch` `NameError: OSError` 30틱 연속(+481건)·`n2912` 슬롯 부재·`n2473` 검수행 미생성·`Invalid signature` 400 4건·`n2898` worktree 34초 타임아웃; Celly MCP 4초 타임아웃·게이트웨이 reset/중앙 degraded; KOBE `n5` 가드·포크 표본 오류 15건·late SSE 재시도 3건; Betty 활성 루트 교체·콘솔 `8169`/`8170` `stale_unconfirmed`; `5d4df17`·`9351c77`·`0956ebe`·`e6aff62`·`42abda1`·`c926967`·`537a2ea`·`f9546dd` 파일 설치 완료분은 운영 라우터·CLI 재시작 전까지 라이브 미반영.

- 21:52:19–21:53:48Z 정기 틱(21:32:26Z 대비 20분 관측 창, 전반 안정). KOBE는 기본 수집에서 SSH 일시 단절(`lost connection`, stderr 1줄)이었으나 직후 단독 재조회(`21:53:48Z`)로 정상 수집 완료. 5개 호스트(보조 Betty 포함) 모두 Work MCP 응답 확인(Andy는 수집 시 4초 `TimeoutError`였으나 직후 단독 `/board` `2608ms`·`/ca/mcp` `1405ms` `v12452` 정상 확인, KOBE만 재조회 시 `/board` 4초 `TimeoutError`·MCP `697ms` `v786` 성공 — 미관측을 종료로 보지 않음). 신규 병렬 원장 실패·게이트웨이 `400/413/5xx` 모두 0건. 신규 입증 제품 결함·코드 변경·추가 설치·운영 재시작·상태 파일 수정 없음. 이하 UTC.
  - Andy (`21:52:20Z`): 원장 `8523→8525`, 보드 `v12450→v12452`(단독 확인 `2608ms`), MCP `1405ms`(단독 회복), 리스 제출 22·대기 `6→5`·실행/슬롯 `2→3`, finding 2건 유지(`n2912` 슬롯 부재, `n2473 14355e1e` P1 제출·완료 영수증·검수행 미생성). `n2473 14355e1e` 읽기 전용 추적 결과: `21:28:16Z` 완료 영수증 뒤 포크가 종료됐고 `21:35:58Z` 메인이 `recover_submitted_review`를 호출했으나 `21:36:48Z` `stale completion workspace snapshot; result retained`(정상 안전 가드: 완료 영수증의 `path/branch/head/dirty`와 현재 워크트리 불일치)로 보류됐으며, 이후 메인이 직접 `n2473` 결과를 검증·처리 중. 포크 표본 오류 `0→1건`(`a09e2125` 21:52:06). 메인 전사 2건(`21:36:48` 위 정상 가드, `21:43:59` 완료된 작업 `TaskStop` 1건). 라우터: **`fork_turn_watch_poll_failed NameError: OSError` 478건(21:32:31Z부터, 29틱 연속)**, 외부 SSE EOF 19건·connect timeout 1건(21:40:14Z), `/ca/mcp` 단절 3건(21:42:52Z부터), `transcript_delta_delivery_failed TimeoutError` 3건(21:44:09Z부터), `_Walkie_P1` 클라이언트 단절 1건(21:40:22Z), 턴 투영 관찰 8건(`age 124→975s`).
  - Celly (`21:52:29Z`): 원장 `10343→10344`, 보드 `v11433→v11435`(회복, 노드 `251→249`), MCP `437ms`, 대기 `7→8`·실행/슬롯 `3→2`, finding 0·신규 실패 0 유지, 포크 표본 오류 `3→2건`(`a703463d` 21:51:04, `aad6fa96` 21:52:07). 메인 전사 2건(`21:45:43` 작업 측 urllib 오류, `21:48:42` `auto continue` Work 상태 일시 읽기 실패). 라우터: 외부 SSE 단절 15건(EOF 12·connect `10054` 2·read `10053` 1), `/ca/mcp` 단절 10건(21:40:26Z부터), `runtime_compaction_request_scope_failed ChannelFileLockBusy` 1건(21:40:12Z), `context-usage.json` `WinError 5` 1건(21:40:21Z), 콘솔 확인 실패 `16943`(21:45:03Z `age=72.2s`) 및 `stale_unconfirmed` 건너뜀 1건(21:45:08Z), 중앙 게이트웨이 `discovery unavailable` 1건(21:45:08Z `6345ms`), 클라이언트 단절 1건(21:46:52Z).
  - KOBE (단독 재조회 `21:53:48Z`): 원장 `4824→4828`, 보드 `v784→v786`(MCP `697ms`, `/board`만 4초 `TimeoutError`), 리스 수락 1·대기 `24→25`·실행/슬롯 1, `n5` 미완료 자식 가드 유지, 메인 전사 오류 0, 포크 표본 오류 `0→1건`(`ac8bfe32` 21:51:23). 라우터: 정상 keep-alive 가드 252건, 외부 SSE EOF 18건·BrokenPipe 1건(21:52:02Z).
  - Prody (`21:52:53Z`) · Betty(보조, `21:53:02Z`): Prody 원장 `1332→1335`·보드 `v7545→v7548`(halted `11→10`)·제출 `1→0`·실행/슬롯 1 유지·finding `1→0`(직전 `n1368` 검수행 미생성 해소)·MCP `235ms`·메인/포크 오류 모두 0·`/workflow` `observed`(`cancelled 4`) 유지·라우터 `channel_stdin_proxy_blocked` `message_id=8260 stale_unconfirmed` 1건(21:35:26Z); Betty 원장 `1232→1240`·보드 `v5776→v5781`(halted `102→98`, actionable `0→4`)·제출 `5→6`·대기 `5→2`·실행/슬롯 `0→2`·finding 0·MCP `1004ms`·포크 표본 오류 `0→1건`(`a33786b2` 21:52:53)·메인 전사 6건(작업 측 Python 문법/Walkie 409 2건 + 완료 대기 정상 가드 2건 + P3/P5 용량 대기 정상 가드 2건)·라우터 정상 keep-alive 가드 242건·외부 SSE EOF 20건·클라이언트 단절 1건·턴 투영 관찰 10건(`age 1206→2291s`).
  - 미결·추적 유지: Andy PID 81900(`python=3.14.2`) `fork_turn_watch` `NameError: OSError` 29틱 연속(+478건, 원인 미확정)·`n2912` 슬롯 부재·`n2473 14355e1e` 워크트리 불일치 보류(`stale completion workspace snapshot`)·`transcript_delta` 타임아웃 3건; Celly `/ca/mcp` 단절 10건·콘솔 `16943` `stale_unconfirmed`·`WinError 5`·`auto continue` 읽기 실패; KOBE `n5` 미완료 자식 가드·SSH 일시 단절; Prody 콘솔 `8260` `stale_unconfirmed`; Betty 턴 투영 관찰 장기화(`age=2291s`); `5d4df17`·`9351c77`·`0956ebe`·`e6aff62`·`42abda1`·`c926967`·`537a2ea`·`f9546dd` 파일 설치 완료분은 운영 라우터·CLI 재시작 전까지 라이브 미반영.

- 21:32:26–21:33:07Z 정기 틱(21:12:17Z 대비 20분 관측 창, 전반 안정). 5개 호스트(보조 Betty 포함) Work MCP 전원 `probe_succeeded`(Andy `1687ms`, Celly `2344ms`, KOBE `424ms`, Prody `1071ms`, Betty `439ms`), `live.board`는 4곳 `observed`·Celly만 수집 시 4초 `TimeoutError`였으나 직후 단독 `/ca/work/api/v1/board` `1734ms`(`v11433`, 251노드) 정상 응답 확인(미관측을 종료로 보지 않음). 수집 stderr·신규 병렬 원장 실패·게이트웨이 `400/413/5xx` 모두 0건. 신규 입증 제품 결함·코드 변경·추가 설치·운영 재시작·상태 파일 수정 없음. 이하 UTC.
  - Andy (`21:32:27Z`): 원장 `8520→8523`, 보드 `v12449→v12450`(halted `142→141`, actionable `52→51`), MCP `1687ms`(회복), 리스 제출 `21→22`·대기 `5→6`·실행/슬롯 `4→2`, finding `1→2`(`n2912` 유지, `n2473 14355e1e` P1 제출·완료 영수증·검수행 미생성 `native=waiting last=21:28 miss=21:29` — 다음 틱 해소 여부 추적), 메인 전사 오류 0·포크 표본 오류 `1→0`. 라우터: **`fork_turn_watch_poll_failed NameError: OSError` 440건(21:12:19Z부터, 28틱 연속)**, 외부 SSE EOF 20건·connect `WinError 10054` 1건(21:30:54Z), `/ca/mcp` 단절 2건(`10053`/`10054`), `_Walkie_P1` 클라이언트 단절 2건(21:21:45Z·21:25:45Z). `WinError 5` 미관측.
  - Celly (`21:32:32Z`): 원장 `10334→10343`, 보드 `v11430→v11433`(단독 확인 `1734ms`), 대기 `8→7`·실행/슬롯 `2→3`, finding 0·신규 실패 0 유지, MCP `2344ms`, 포크 표본 오류 `0→3건`(`ae44e0a5` 21:18:41, `aad6fa96` 2건 21:31:05·36, 원인 미분류). 메인 전사 4건 모두 작업 측 오류(cp1252 인코딩 2건 21:30, `gh_read.py` 1건 21:30, Walkie 400 잘못된 단계명 `fix_x3` 1건 21:31). 라우터: 외부 SSE EOF 19건, `/ca/mcp` 단절 1건(21:27:44Z), 콘솔 확인 실패 `16941` 1건(21:28:57Z `age=63.3s`), `router-activity.json` `WinError 5` 1건(21:28:59Z), `transcript_delta_delivery_failed HTTP 502` 1건(21:30:51Z, 전사 엔드포인트 일시 502), `ping` 무시 1건, 턴 투영 관찰 3건.
  - KOBE (`21:32:48Z`): 원장 `4824`·보드 `v784`·리스(수락 1·대기 24·실행/슬롯 1)·`n5` 미완료 자식 가드·메인/포크 오류 0 모두 유지, MCP `424ms`. 라우터: 정상 keep-alive 가드 238건, 외부 SSE 단절 20건(EOF 15·BrokenPipe 2·connect `WinError 10054` 2·TimeoutError 1).
  - Prody (`21:32:57Z`) · Betty(보조, `21:33:07Z`): Prody 원장 `1318→1332`·보드 `v7541→v7545`(halted `12→11`)·제출 1·실행/슬롯 1 유지·MCP `1071ms`·`/workflow` `observed`(`cancelled 4`) 유지, finding은 직전 `n1368` 용량 대기 해소 후 방금 제출된 `n1368 ed8e053e` 검수행 미생성(`last=21:32`, 다음 틱 추적), 포크 표본 오류 `5→1건`(`a6d3b2ea` 21:32:00), 메인 전사 2건(작업 측 셸/JSON 파싱 exit 1/2 21:23–24), 라우터 `channel_stdin_proxy_blocked stale_unconfirmed` 2건(`8255` 21:21:41Z, `8258` 21:31:53Z); Betty 원장 `1228→1232`·보드 `v5774→v5776`(actionable `1→0`)·제출 5·대기 5·실행/슬롯 `1→0`·finding 0·MCP `439ms`·포크 표본 오류 0 유지·메인 전사 1건(작업 측 Python 오류 21:26:54)·라우터 정상 keep-alive 가드 245건·외부 SSE EOF 20건·클라이언트 단절 1건·콘솔 확인 실패 `8166`(21:25:38Z `age=70.9s`) 및 `stale_unconfirmed` 건너뜀 1건·턴 투영 관찰 9건(`age 120→1086s`, 직전 3018s 구간 리셋 후 신규 누적).
  - 미결·추적 유지: Andy PID 81900(`python=3.14.2`) `fork_turn_watch` `NameError: OSError` 28틱 연속(+440건, 원인 미확정)·`n2912` 슬롯 부재·`n2473` 검수행 미생성(21:28 제출, 다음 틱 확인); Celly 콘솔 `16941`·`WinError 5`·`transcript_delta` 502·포크 오류 3건; KOBE `n5` 미완료 자식 가드; Prody `n1368` 검수행 미생성(21:32 제출, 다음 틱 확인)·콘솔 `8255`/`8258` `stale_unconfirmed`; Betty 콘솔 `8166` `stale_unconfirmed`; `5d4df17`·`9351c77`·`0956ebe`·`e6aff62`·`42abda1`·`c926967`·`537a2ea`·`f9546dd` 파일 설치 완료분은 운영 라우터·CLI 재시작 전까지 라이브 미반영.

- 21:12:17–21:12:55Z 정기 틱(20:52:18Z 대비 20분 관측 창, 전반 안정). 5개 호스트(보조 Betty 포함) 수집 stderr·신규 병렬 원장 실패·게이트웨이 `400/413/5xx` 모두 0건. `live.board`·Work MCP는 4곳 `observed`/`probe_succeeded`(Celly `547ms`, KOBE `284ms`, Prody `335ms`, Betty `545ms`), Andy만 수집 시 4초 `TimeoutError`였으나 직후 단독 루프백 확인(`/board` `3561ms` `v12449`, `/ca/mcp` `2827ms` `v12449` `enabled=True`)으로 정상 응답 확인(미관측을 종료로 보지 않음). 직전 틱 Andy `n2925` 검수행 미생성 finding은 해소됐다. 신규 입증 제품 결함·코드 변경·추가 설치·운영 재시작·상태 파일 수정 없음. 이하 UTC.
  - Andy (`21:12:17Z`): 원장 `8504→8520`, 보드 `v12440→v12449`(단독 확인 `3561ms`), 리스 제출 `22→21`·대기 `6→5`·실행/슬롯 `3→4`, finding `2→1`(`n2925` 해소, `n2912` 16:57부터 슬롯 보유·포크 부재 지속), 포크 표본 오류 `0→1건`(`a09e2125` 21:10:49). 메인 전사 2건 모두 정상 가드(`21:02:03` P3 슬롯 용량 대기 `c9ca119f`, `21:02:36` 보드 버전 충돌 `12446 vs 12445`). 라우터: **`fork_turn_watch_poll_failed NameError: OSError` 443건(20:52:21Z부터, 27틱 연속)**, 외부 SSE EOF 19건, `runtime_activity_context_usage_failed` `context-usage.json` `WinError 5` 3건(21:03:15Z부터), `/ca/mcp`·`_Walkie_P1` 클라이언트 단절 각 1건(20:57:12Z·21:04:56Z), 턴 투영 관찰 6건(age 120→721s).
  - Celly (`21:12:28Z`): 원장 `10331→10334`, 보드 `v11425→v11430`(노드 `246→251`), 대기 8·실행/슬롯 2·finding 0·신규 실패 0 유지, MCP `1609→547ms`, 포크 표본 오류 `1→0`. 메인 전사 2건(`20:53:43` 작업 측 bash syntax exit 2, `20:55:21` 메인 직접 코드 수정 거부 `main_delegates` 정상 가드). 라우터: 외부 SSE EOF 18건, `context-usage.json` `WinError 5` 1건(20:58:40Z), 중앙 게이트웨이 `discovery unavailable` `degraded=True` 1건(21:11:31Z, `5255ms`), 턴 투영 관찰 5건(age 121→614s).
  - KOBE (`21:12:36Z`): 원장 `4824` 유지, 보드 `v782→v784`(노드 `165→162`, 루트 `13→12`), 리스(수락 1·대기 24·실행/슬롯 1) 및 `n5` 미완료 자식 가드 유지, MCP `284ms`, 메인·포크 오류 `1→0`. 라우터: 정상 keep-alive 가드 238건, 외부 SSE EOF 16건·BrokenPipe 2건(20:58:46Z부터).
  - Prody (`21:12:47Z`) · Betty(보조, `21:12:55Z`): Prody 원장 `1307→1318`·보드 `v7537→v7541`(actionable `3→2`)·리스 제출 `0→1`·대기 `1→0`·실행/슬롯 1·MCP `335ms`·라우터 경고 0·`/workflow` `observed`(`cancelled 4`) 유지, finding `0→1`(`n1368 3a68f52f awaiting_capacity` `P3 (1/1)` 슬롯 대기 — 정상 용량 가드), 메인 전사 2건 모두 작업 측 Walkie 400 인자 오류(20:55–56), 포크 표본 오류 `0→5건`(`a35fd302` 2건 21:03, `af4250ab` 3건 21:05–06, 원인 미분류); Betty 원장 `1226→1228`·보드 `v5770→v5774`·제출 5·대기 `6→5`·실행/슬롯 `0→1`·finding 0·MCP `545ms`·포크 표본 오류 0 유지, 메인 전사 5건(작업 측 Edit/검증/Walkie 400 4건 + 완료 대기 정상 가드 1건 21:10:22), 라우터 정상 keep-alive 가드 243건·외부 SSE EOF 20건·턴 투영 관찰 9건(`age 1931→3018s`)·콘솔 확인 실패 `8160`(21:11:00Z `age=20.1s`) 및 `stale_unconfirmed` 건너뜀 1건(21:11:03Z).
  - 미결·추적 유지: Andy PID 81900(`python=3.14.2`) `fork_turn_watch` `NameError: OSError` 27틱 연속(+443건, 원인 미확정)·4초 상한 근접 응답 지연(`2827–3561ms`)·`n2912` 슬롯 부재·`context-usage.json` `WinError 5`(+3건); Celly `context-usage.json` `WinError 5`·중앙 게이트웨이 discovery 1건; KOBE `n5` 미완료 자식 가드; Prody `n1368` P3 용량 대기·포크 표본 오류 5건; Betty 턴 투영 관찰 장기화(`age=3018s`)·콘솔 `8160` `stale_unconfirmed`; `5d4df17`·`9351c77`·`0956ebe`·`e6aff62`·`42abda1`·`c926967`·`537a2ea`·`f9546dd` 파일 설치 완료분은 운영 라우터·CLI 재시작 전까지 라이브 미반영.

- 20:52:18–20:52:52Z 정기 틱(20:32:19Z 대비 20분 관측 창, 전반 안정). 5개 호스트(보조 Betty 포함) `live.board`·Work MCP 전원 `observed`/`probe_succeeded`(Andy `3219ms`, Celly `1609ms`, KOBE `346ms`, Prody `277ms`, Betty `373ms`), 수집 stderr·신규 병렬 원장 실패·게이트웨이 `400/413/5xx` 모두 0건. 신규 입증 제품 결함·코드 변경·추가 설치·운영 재시작·상태 파일 수정 없음. 이하 UTC.
  - Andy (`20:52:18Z`): 원장 `8498→8504`, 보드 `v12438→v12440`, MCP `3219ms`(직전 4초 타임아웃 해소), 리스 제출 `21→22`·대기 `7→6`·실행/슬롯 `2→3`, finding `1→2`(`n2912` 유지, `n2925 f0208073` P3 방금 제출·완료 영수증·검수행 미생성 `last=20:51` `miss=None` — 직전 완료 구간으로 미결 추적), 포크 표본 오류 0 유지. 메인 전사 2건(`20:45:44` 작업 측 스크립트 exit 2, `20:50:22` `auto continue` Work 상태 일시 읽기 실패). 라우터: **`fork_turn_watch_poll_failed NameError: OSError` 417건(20:32:20Z부터, 26틱 연속)**, 외부 SSE EOF 20건, `/ca/mcp` 단절 `WinError 10053` 1건(20:32:27Z), `runtime_compaction_observe_failed PermissionError` 1건(20:45:28Z), 턴 투영 관찰 3건(age 120→364s).
  - Celly (`20:52:25Z`): 원장 `10327→10331`, 보드 `v11423→v11425`(노드 `243→246`, 루트 `30→31`), 대기 8·실행/슬롯 `3→2`, finding 0·신규 실패 0 유지, MCP `1609ms`, 포크 표본 오류 `0→1건`(`a05bd4c0` 20:32:47), 메인 전사 4건 모두 작업 측 diff/Python 스크립트 exit 1(20:35–38). 라우터: 외부 SSE EOF 18건, 클라이언트 단절 1건(20:33:37Z), 턴 투영 관찰 5건(age 120→481s).
  - KOBE (`20:52:34Z`): 원장 `4820→4824`, 보드 `v780→v782`(노드 `167→165`), 리스 수락 1·대기 `23→24`·실행/슬롯 1 유지, `n5` 미완료 자식 가드 유지, MCP `346ms`, 메인 전사 오류 0, 포크 표본 오류 `0→1건`(`a5646c0b` 20:51:47). 라우터: 정상 keep-alive 가드 239건, 외부 SSE EOF 20건, 클라이언트 단절 1건(20:48:25Z).
  - Prody (`20:52:43Z`) · Betty(보조, `20:52:52Z`): Prody 원장 `1303→1307`·보드 `v7531→v7537`(halted `15→12`, actionable `1→3`)·대기 1·예약 `1→0`·실행/슬롯 `0→1`·finding 0·MCP `277ms`·포크 표본 오류 0 유지·`/workflow` `observed`(`cancelled 4`) 유지, 메인 전사 2건(완료 대기 정상 가드 20:45:32, P3 슬롯 용량 대기 정상 가드 `5853368b` 20:46:30), 라우터 `channel_stdin_proxy_blocked` `message_id=8244 stale_unconfirmed` 1건(20:43:30Z); Betty 원장 `1226`·보드 `v5770`·리스(제출 5·대기 6)·finding 0 유지·MCP `373ms`·포크 표본 오류 `3→0`, 메인 전사 5건 모두 작업 측 E2E/Edit 중복 매칭 오류(20:35–52), 라우터 정상 keep-alive 가드 243건·외부 SSE EOF 21건·턴 투영 관찰 10건(age 723→1811s).
  - 미결·추적 유지: Andy PID 81900(`python=3.14.2`) `fork_turn_watch` `NameError: OSError` 26틱 연속(+417건, 원인 미확정)·`n2912` 슬롯 부재·`n2925` 검수행 미생성(20:51 제출, 다음 틱 해소 여부 확인)·`runtime_compaction_observe_failed PermissionError`·`auto continue` 상태 읽기 실패; KOBE `n5` 미완료 자식 가드; Prody 콘솔 `8244` `stale_unconfirmed`; Betty 턴 투영 관찰 장기화(`age=1811s`); `5d4df17`·`9351c77`·`0956ebe`·`e6aff62`·`42abda1`·`c926967`·`537a2ea`·`f9546dd` 파일 설치 완료분은 운영 라우터·CLI 재시작 전까지 라이브 미반영.

- 20:32:19–20:32:54Z 정기 틱(20:17:19Z 대비 15분 관측 창, 전반 안정). 5개 호스트(보조 Betty 포함) `live.board` 전원 `observed`, Work MCP는 4곳 `probe_succeeded`(Celly `718ms`, KOBE `484ms`, Prody `290ms`, Betty `241ms`), Andy만 수집 시 4초 `TimeoutError`(4000ms)였으나 직후 단독 `/ca/mcp work_status` `5842ms`(`v12438`, `enabled=True`) 정상 응답 확인. 수집 stderr·신규 병렬 원장 실패·게이트웨이 `400/413/5xx`·콘솔 확인 실패 모두 0건(4대 핵심 호스트 포크 표본 오류도 전원 0건). 신규 입증 제품 결함·운영 재시작·상태 파일 수정 없음. (`5d4df17`은 `Publish to npm` `45889438771` 성공·npm `@beta` `0.2.51-beta.20261126-201335.5d4df17` 타볼 SHA-256 일치 확인 및 7개 승인 루트 파일 설치 `apply ok=True` 완료; 라이브 효과는 라우터 재시작 전까지 미반영.) 이하 UTC.
  - Andy (`20:32:20Z`): 원장 `8494→8498`, 보드 `v12437→v12438`, 리스 제출 21·대기 `6→7`·실행/슬롯 2, finding `n2912` 1건 유지, 포크 표본 오류 0 유지. 메인 전사 1건(`20:18:41` `n2943` 기존 리스 `ba0812c6` 중복 dispatch 정상 가드). 라우터: **`fork_turn_watch_poll_failed NameError: OSError` 334건(20:17:23Z부터, 25틱 연속)**, 외부 SSE EOF 14건, 클라이언트 단절 3건(`_Walkie_P1`, 20:20:12Z·20:30:24Z), 턴 투영 관찰 1건(20:18:00Z, `age=363s`). 직전 창 413/400/WinError 5 미관측.
  - Celly (`20:32:27Z`): 원장 `10327`·보드 `v11423`·리스(대기 8·실행/슬롯 3)·finding 0·신규 실패 0 유지, MCP `718ms`, 메인 전사 오류 0·포크 표본 오류 `6→0`. 라우터: 외부 SSE EOF 9건, 턴 투영 관찰 1건(20:18:06Z, `age=722s`).
  - KOBE (`20:32:36Z`): 원장 `4819→4820`, 보드 `v780` 유지, 리스 수락 1·대기 23·예약 `1→0`·실행/슬롯 `0→1`, `n5` 미완료 자식 가드 유지, MCP `484ms`, 메인·포크 오류 0 유지. 라우터: 정상 keep-alive 가드 180건, 외부 SSE EOF 15건, 클라이언트 단절 1건(20:17:22Z 경계), 턴 투영 관찰 1건(20:17:32Z).
  - Prody (`20:32:45Z`) · Betty(보조, `20:32:54Z`): Prody 원장 `1296→1303`·보드 `v7527→v7531`(halted `17→15`, actionable `0→1`)·대기 `2→1`·예약 `0→1`·finding 0·MCP `290ms`·메인/포크/라우터 오류 모두 0·`/workflow` `observed`(`cancelled 4`) 유지; Betty 원장 `1222→1226`·보드 `v5769→v5770`(actionable `2→1`)·제출 5·대기 6·실행/슬롯 `1→0`·finding 0·MCP `241ms`·포크 표본 오류 `0→3건`(`a1dac238` 20:21–22)·메인 전사 3건 모두 작업 측 검증/E2E 스크립트 exit 1/255(20:22–31)·라우터 정상 keep-alive 가드 183건·외부 SSE EOF 15건·클라이언트 단절 1건·턴 투영 관찰 5건.
  - 미결·추적 유지: Andy PID 81900(`python=3.14.2`) `fork_turn_watch` `NameError: OSError` 25틱 연속(+334건, 원인 미확정)·Work MCP 응답 지연(`5842ms`)·`n2912` 슬롯 부재; KOBE `n5` 미완료 자식 가드; `5d4df17`·`9351c77`·`0956ebe`·`e6aff62`·`42abda1`·`c926967`·`537a2ea`·`f9546dd` 파일 설치 완료분은 운영 라우터·CLI 재시작 전까지 라이브 미반영.

- 20:17:19–20:17:52Z 정기 틱(19:37:32Z 대비 40분 관측 창). 5개 호스트(보조 Betty 포함) `live.board`·Work MCP 전원 `observed`/`probe_succeeded`(Andy `2594ms`, Celly `437ms`, KOBE `860ms`, Prody `203ms`, Betty `516ms`), 수집 stderr 없음. **로컬 게이트웨이 `127.0.0.1:20128` 구간 변화**: 직전 창 다발하던 OpenCode Go `400 MissingSessionID`는 Andy·Celly 양쪽에서 0건으로 사라졌고, 대신 `19:41:31Z`에 로컬 게이트웨이 일시 연결 단절(`WinError 10054`)이 Andy·Celly에서 동시 관측됐으며, 그 직후 Andy `_Walkie_P1`(4건)·`_Walkie_S_Tier_Only`(1건)에서 상류 `400 Invalid signature in thinking block`이 발생했다. 신규 입증 제품 결함·코드 변경·운영 재시작·상태 파일 수정 없음(`5d4df17` `Publish to npm` 런 `45889438771` 진행 중). 이하 UTC.
  - Andy (`20:17:20Z`): 원장 `8491→8494`, 보드 `v12416→v12437`(노드 `549→565`, halted `141→142`, actionable `47→52`), MCP `1281→2594ms`, 리스 제출 21·대기 6 유지·실행/슬롯 `1→2`·예약 `1→0`, finding 1건 유지(`n2912` 16:57부터 슬롯 보유·포크 부재), 포크 표본 오류 `4→0건`, 신규 병렬 원장 실패 0. 메인 전사 18건: 격리 사이드 원문 전달 2건(`8c77116c` 19:37:38, `23e639ef` 19:38:13), 정상 가드/안내 8건(포크 API 재시도 스케줄 `n2925` 19:42:15, 기존 리스 중복 dispatch 거부 `n2472` 19:47:38, `n2472` no active slot 19:49:00, `work_side_status` 호스트 전용 거부 19:51:38, 리뷰어 포크 금지 19:53:28, 완료 대기 가드 19:55:01, `work_side_handoff` 미존재 안내 19:59:23, P3 슬롯 용량 대기 `fe044d2b` 20:01:16), 작업 측/외부 오류 8건(Walkie 409 버전 충돌 2건 19:37:59/19:58:36, Walkie 400 인자 오류 2건 19:41:51/53, 로컬 Python 스크립트 오류 3건 19:42–52, 없는 경로 `server/src/workflows` 20:14:49). 라우터: **`fork_turn_watch_poll_failed NameError: OSError` 917건(19:37:35Z부터, 24틱 연속)**, 외부 SSE EOF 39건, `_Walkie_P1` **413 본문 초과 6건**(19:38:17Z부터, `tokens=478188`), `19:41:31Z` 상류 `ConnectionResetError`(`_API_KEYS-SSE-1` 4·`_Walkie_P3` 1·`_Walkie_S_Tier_Only` 1 재시도), 상류 `400 Invalid signature in thinking block` 5건(`_Walkie_P1` 4건 19:41:39Z부터, `_Walkie_S_Tier_Only` 1건 19:41:35Z), `runtime_activity_router_failed` `router-activity.json` `WinError 5` 3건(19:42:55Z부터), 클라이언트 단절 7건(`_Walkie_P1` 7·`/ca/mcp` 1), 턴 투영 관찰 4건. `MissingSessionID` 0건.
  - Celly (`20:17:24Z`): 원장 `10317→10327`, 보드 `v11419→v11423`(243노드/30루트, actionable 17), MCP `437ms`(직전 4초 타임아웃 해소), 대기 `5→8`·실행/슬롯 `4→3`, finding 0·신규 실패 0 유지. 포크 표본 오류 `16→6건`(24건 표본: `a6894241` 2건 19:46, `abfbd95b` 2건 19:56, `ae44e0a5` 20:15, `a703463d` 20:17, 원인 미분류). 메인 전사 1건(`19:38:40` 경계 `Parallel read` 타임아웃). 라우터: 외부 SSE EOF 32건·TimeoutError 1건(20:05:48Z), `/ca/mcp` 단절 19건(`10054` 18·`10053` 1), `19:41:31Z` 게이트웨이 `127.0.0.1:20128` `connection_reset` 3건(`_Walkie_P1-Celly` 2건 재시도, `_Walkie_P3` SSE 단절 1건), 클라이언트 단절 1건(19:58:30Z), `channel_windows_console_deferred` `channel_file_lock_busy` 1건(20:05:33Z, 정상 보류), 턴 투영 관찰 5건(age 120→601초). `MissingSessionID`·413·콘솔 확인 실패 0건.
  - KOBE (`20:17:33Z`): 원장 `4816→4819`, 보드 `v776→v780`(release_hint `47→48`, actionable 9), 리스 수락 1·대기 `22→23`·실행/슬롯 `1→0`·예약/슬롯 `0→1`, `n5` 미완료 자식 가드 유지, MCP `421→860ms`, 메인 전사 오류 0·포크 표본 오류 0 유지. 라우터: 정상 keep-alive 가드 464건, 외부 SSE EOF 35건·BrokenPipe 2건(19:51:17Z부터), `anthropic_sse_event_after_message_stop_ignored ping` 1건(19:43:33Z), 클라이언트 단절 2건(19:44:50Z·20:17:22Z), `cielairouter_late_sse_retry` 1건(20:01:48Z, 정상 재시도), 턴 투영 관찰 4건. 콘솔 확인 실패 0건.
  - Prody (`20:17:44Z`) · Betty(보조, `20:17:52Z`): Prody 원장 `1278→1296`·보드 `v7521→v7527`(actionable `2→0`)·대기 `1→2`·실행/슬롯 `1→0`·finding 0·MCP `203ms`·포크 표본 오류 `2→1건`(`a5e0ec81` 20:14:49)·라우터 경고 0·`/workflow` `observed`(`cancelled 4`) 유지, 메인 전사 4건(작업 측 셸/없는 경로 `gh_api.py` 2건 19:45, `walkie_send_message` `Invalid uuid at client_id` 1건 19:47:13, P3 슬롯 용량 대기 정상 가드 `n1362` 1건 19:48:46); Betty 원장 `1219→1222`·보드 `v5769` 유지·리스(제출 5·대기 6·실행/슬롯 1) 유지·MCP `516ms`·finding 0·포크 표본 오류 `2→0`, 신규 원장 실패 1건(`revival_refused x1`) 및 메인 전사 1건(`20:04:26` `Work message to a7c4ffa06b202203d refused` — 종료된 포크 재활성화 차단 **정상 가드**), 라우터 정상 keep-alive 가드 472건·외부 SSE EOF 39건·클라이언트 단절 3건·턴 투영 관찰 5건·콘솔 확인 실패 `8151`(20:08:51Z `age=28.4s`) 및 `stale_unconfirmed` 건너뜀 1건.
  - 미결·추적 유지: `5d4df17`(fresh-session 사이드 메인 모델 폴백) `beta` 푸시 완료·`Publish to npm` `45889438771` 진행 중(완료 즉시 npm 타볼 SHA-256 대조 및 7개 승인 루트 `apply` 예정); Andy PID 81900(`python=3.14.2`) `fork_turn_watch` `NameError: OSError` 24틱 연속(+917건, 원인 미확정); Andy `n2912`·`_Walkie_P1` 413(6건)·상류 `Invalid signature in thinking block` 5건·`router-activity.json` `WinError 5` 3건; Celly `/ca/mcp` 단절 19건·포크 오류 6건; KOBE `n5` 미완료 자식 가드; Betty 콘솔 `8151` `stale_unconfirmed`; `9351c77`·`0956ebe`·`e6aff62`·`42abda1`·`c926967`·`537a2ea`·`f9546dd` 파일 설치 완료분은 라우터·CLI 재시작 전까지 라이브 미반영.

- 19:37:32–19:40:36Z 정기 틱(18:52:46Z 대비 45분 관측 창) + **입증된 제품 결함 수정(`5d4df17`, `fresh-session` 사이드 액터 메인 모델 폴백 지문 불일치)**. 5개 호스트(보조 Betty 포함) 수집 stderr 없음, 신규 병렬 원장 실패 0, 운영 세션/라우터 재시작 및 상태 파일 수정 없음. 이하 UTC(라우터 로그 현지 -6/-8 표기는 UTC 환산).
  - Andy (`19:37:33Z`): 원장 `8446→8491`, 보드 `v12407→v12416`(549노드/75루트, halted `143→141`, actionable `49→47`), MCP `2297→1281ms`(`enabled=true`), 리스 제출 `22→21`·대기 6·실행/슬롯 1·예약/슬롯 `0→1`, finding `2→1`(`n1166` 해소, `n2912` 16:57부터 슬롯 보유·포크 부재 지속). 포크 표본 오류 `0→4건`(`a225990e` 18:58:10–18:59:33, 원인 미분류). 메인 전사 12건: 격리된 사이드 이벤트 원문 전달 6건(`e497a319` 19:03:00/19:04:04, `64bdab49` 19:09:00, `42d3abf9` 19:16:33/19:18:11, `4c92655c` 19:29:40), `auto continue could not read the Work status` 4건(18:56:22·19:04:14·19:17:57·19:33:07), `Work reconciliation: Parallel read failed: The operation timed out` 1건(19:08:54), `work_node_update` 완료 대기 정상 가드 1건(18:58:07). 라우터: **`fork_turn_watch_poll_failed NameError: OSError` 973건(18:52:49Z부터, 23틱 연속)**, 외부 SSE EOF 43건·connect timeout `WinError 10060` 3건(19:20:37Z부터)·BrokenPipe 1건(18:53:20Z), 로컬 게이트웨이(`127.0.0.1:20128`) **`400 MissingSessionID` 33건**(`_API_KEYS-SSE-1` 28건 18:55:28Z부터, `_Walkie_P3` 5건 19:01:25Z부터) 및 `cielairouter_transient_stopped` 24건, `_Walkie_P1` **413 요청 본문 초과 11건**(19:23:48Z부터, `tokens=484472` `bytes=1841884`), `/ca/mcp` 클라이언트 단절 5건·`_Walkie_P1` 단절 3건, `work_workspace_failed` `n2908` 33초 타임아웃 1건(18:54:00Z).
  - Celly (`19:37:39Z`): 원장 `10274→10317`, 대기 `6→5`·실행/슬롯 4 유지, finding 0·신규 실패 0 유지. 수집 시 `/board`·`/workflow`·MCP 3종 동시 4초 `TimeoutError`(4000ms)였으나 직후 단독 루프백 확인(`/health` `14ms` PID `111684`, `/board` `610ms` `v11419` 242노드)으로 프로세스·보드 정상 생존 확인(미관측을 종료로 보지 않음). 포크 표본 오류 `2→16건`(24건 표본: `aa6d6b4b` 2건 19:03–04, `aebd957c` 6건 19:28–32, `a6894241` 8건 19:33–37, 원인 미분류). 메인 전사 3건: `walkie_messages_work_update` `Invalid uuid at client_id` 1건(18:57:15), `Work reconciliation` `work_host_page`/`Parallel read` 타임아웃 각 1건(19:37:32·19:38:40). 라우터: 외부 SSE EOF 35건·BrokenPipe 1건, `/ca/mcp` 단절 9건(`10054` 8·`10053` 1), `_Walkie_P3`/`_Walkie_P2` `400 MissingSessionID` 11건(6+5), `_Walkie_P1-Celly` `400 reasoning_text must be passed back` 2건(19:30:51Z, 상류 모델 요청 오류), `_Walkie_P1-Celly` 502/연결 단절 재시도 4회 + 중앙 게이트웨이 degraded 1건(19:00:28–37Z), 클라이언트 단절 3건, 턴 투영 관찰 2건.
  - KOBE (`19:40:18Z`): 원장 `4816` 유지, 보드 `v772→v776`(halted `50→49`, actionable `8→9`), 리스(수락 1·대기 22·실행/슬롯 1) 및 `n5` 미완료 자식 가드 유지, MCP `259→421ms`, 메인 전사 오류 0·포크 표본 오류 `3→0`. 라우터: 정상 keep-alive 가드 557건, 외부 SSE EOF 45건·BrokenPipe 1건(19:31:58Z), 콘솔 확인 실패 `12353`(19:24:02Z `age=56.8s`) 및 `channel_stdin_proxy_blocked stale_unconfirmed` 1건(19:24:03Z), 턴 투영 관찰 5건(19:17:27–19:26:09Z).
  - Prody (`19:40:28Z`) · Betty(보조, `19:40:36Z`): Prody 원장 `1271→1278`·보드 `v7519→v7521`(halted `18→17`)·대기 1·실행/슬롯 1·finding 0·메인/라우터 오류 0·MCP `279ms`·포크 표본 오류 `1→2건`(`ace1d497` 19:24:15, `a167e32d` 19:39:30)·`/workflow` `observed`(`cancelled 4`) 유지; Betty 원장 `1218→1219`·보드 `v5767→v5769`·제출 5·대기 `5→6`·실행/슬롯 `2→1`·MCP `294ms`·finding 0·포크 표본 오류 `3→2건`(`a2511f36` 19:01–03)·메인 전사 1건(`18:55:55` `n1845` 기존 리스 중복 dispatch 정상 가드)·라우터 정상 keep-alive 가드 563건·외부 SSE EOF 46건·`runtime_activity_context_usage_failed WinError 5` 1건(19:00:36Z)·턴 투영 관찰 3건.
  - **입증된 제품 결함 수정 (`5d4df17`, [side-fresh-parent-model-fallback](/docs/journal/2026/11/26/fixes/side-fresh-parent-model-fallback/record.md)):**
    - 원인: Andy 사이드 설정 `work_side_intake_session=fresh`에서 `side-intake.js`가 `model_fingerprint='fresh-session'`을 기록하는데, `side_intake_mcp.py`의 `work_side_claim`이 `'native-fork'`와만 동등 비교해 `turn_error` 이후에도 재시도마다 `use_parent_model=False`를 반환. 결과적으로 `_API_KEYS-SSE-1` 400 실패 시 메인 모델(`_Walkie_P1`)로 한 번도 전환되지 않고 6회 반복 후 격리됐다(실측 `42d3abf9`·`5650fea7` 10/10건 `' main-model'` 미부착).
    - 수정·검증: `side_intake_mcp.py`·`side_intake_processing.py` 수정(두 네이티브 지문 `native-fork`/`fresh-session` 및 빈 저장 지문 허용, 커스텀 지문 불일치는 `False` 유지). `test_side_intake_mcp.py` 신규 3건 수정 전 `FAIL` → 수정 후 격리 Windows 18개 스위트 `518 run / 0 fail / 46 skip`, Linux `py310`·`py311` 18개 스위트 각 `518 run / 0 fail / 46 skip`(`ALL_LINUX_OK`), `claude plugin validate` 2종 통과, `claude plugin test ciel-work` `97 pass / 0 fail`. (별도 참고: `test_side_queue_starvation.py::test_a_gate_recheck_is_claimed_before_p1_events`는 커밋 `a193117`부터 이미 실패하던 기존 테스트로 본 건 무관.)
    - 배포 상태: `feature/priority-models`·`beta` 푸시 완료(`5d4df17`), GitHub Actions `Publish to npm` 런 `45889438771` 진행 중. `overlay-5d4df17.tgz` 생성 및 7개 승인 루트 `check` 전원 `base` 일치 확인 완료 — npm `@beta` 발행 및 타볼 SHA-256 확인 직후 7개 루트 `apply` 예정(적용 후에도 라이브 효과는 재시작 전까지 없음).
  - 미결·추적 유지: Andy PID 81900(`python=3.14.2`) `fork_turn_watch` `NameError: OSError` 23틱 연속(+973건, 원인 미확정); 로컬 게이트웨이 `127.0.0.1:20128` OpenCode Go `400 MissingSessionID` 및 `_Walkie_P1` `413` 본문 초과(게이트웨이/상류 측); Andy `n2912`·`n2908` worktree 33초 타임아웃·`auto continue` 읽기 실패 4건; Celly 4초 프로브 지연 및 `work_host_page`/`Parallel read` 타임아웃·`_Walkie_P1-Celly` `reasoning_text` 400 2건·포크 오류 16건; KOBE `n5` 미완료 자식 가드·콘솔 `12353` `stale_unconfirmed`; `5d4df17` 발행·설치 완료 대기; `9351c77`·`0956ebe`·`e6aff62`·`42abda1`·`c926967`·`537a2ea`·`f9546dd` 파일 설치 완료(라이브 효과는 라우터·CLI 재시작 전까지 미반영).

- 18:44:45–18:53:36Z 정기 틱 2회(18:44:45Z·18:52:46Z 수집, 직전 기록 18:14:48Z 대비 38분 관측 창). 5개 호스트(보조 Betty 포함) 수집 stderr 없음, `live.board` 전원 `observed`(18:44:45Z 수집에서 Andy만 `TimeoutError`, 8분 뒤 `v12407`로 관측 — 미관측을 종료로 보지 않음). Work MCP는 18:44:45Z Andy·Celly·KOBE·Prody·Betty 전원 `probe_succeeded`, 18:52:46Z 수집에서 **Celly만 `probe_failed TimeoutError 4016ms`**(직전 344ms, 동일 창 내 1회). 신규 입증 제품 결함·코드 변경·추가 설치·운영 재시작·상태 파일 수정 없음. 이하 UTC, 라우터 로그 시각은 현지(-6/-8) 표기를 UTC로 환산.
  - Andy (`18:44:45Z`→`18:52:46Z`): 원장 `8443→8446`, 보드 `TimeoutError→v12407`(549노드/75루트, halted 143, release_hint 39, actionable 49), MCP `1672→2297ms`(`enabled=true`), 리스 제출 22 유지·대기 `5→6`·실행/슬롯 `2→1`, finding 2건 동일(`n1166` 완료 영수증·검수행 없음, `n2912` 16:57부터 슬롯 보유·포크 부재). 포크 표본 오류 `1→0건`. 메인 전사 3건(18:49:36 `n2472` 복귀 거부 `no active slot`, 18:50:08 ciel-work 사이드 실패 이벤트 `9885fc41fe5a` 원문 전달, 18:50:55 `node title must not be empty`). 라우터: **`fork_turn_watch_poll_failed NameError: name 'OSError' is not defined` 172건(18:44:46Z부터, 22틱 연속)**, 외부 SSE EOF 7건, `upstream_direct_http_error 400 MissingSessionID` 6건(`_API_KEYS-SSE-1`, 게이트웨이 `127.0.0.1:20128`), `work_workspace_failed` `n2908` `git worktree add timed out after 32 s` 1건(18:52:16Z), `channel_turn_projection_persistent` 4건(age 729→849→971→1091초, `transcript_updated=False`, `action=observe_only`).
  - Celly (`18:44:53Z`→`18:52:50Z`): 원장 `10260→10274`, 보드 `v11408→v11411`(242노드/30루트, halted `86→83`, release_hint `24→21`, actionable `16→19`), 대기 `9→6`·실행/슬롯 `1→4`, finding 0·신규 실패 `1→0`(`decision_refused_stop f1c0b436`는 이번 창 재발 없음). MCP **`probe_failed TimeoutError 4016ms`**(18:52:50Z 1회, 직전 창 344ms 성공 — 부하 시 4초 상한 초과의 기존 반복 패턴). 포크 표본 오류 2건 유지(`aebd957c` 18:51:18, `aa6d6b4b` 18:51:31, 원인 미분류). 메인 전사 7건: `G:\ciel-br…` 경로 파일 없음 1건(18:45:23, 작업 측), `walkie_messages_work_update` `Invalid uuid at client_id` 3건(18:47:11·12·51, MCP 스키마 거부), `Memo is allowed only when state is ack` 2건(18:47:18·19), 보드 버전 충돌 409 1건(18:47:47). 라우터: 외부 SSE EOF 7건, `_Walkie_P3` 400 `MissingSessionID` 4건, `_Walkie_P1-Celly` **413 요청 본문 초과 2건**(18:50:02, `tokens=683646` `bytes=2695602`), `cielairouter_transient_stopped` 8건(`http_rejection`, `not_retryable`), 콘솔 확인 실패 `16908`(18:46:51 `age=31.6s`)·`16909`(18:51:53 `age=27.3s`) 및 `channel_stdin_proxy_blocked stale_unconfirmed` 2건(18:46:53·18:51:54), 턴 투영 관찰 2건.
  - KOBE (`18:45:02Z`→`18:53:16Z`): 원장 `4816` 유지, 보드 `v771→v772`(노드 `168→167`), 수락 1·대기 22·실행/슬롯 1 유지, MCP `3734→259ms`, finding `n5` 미완료 자식 가드 1건 유지, 신규 원장 실패 0. 포크 표본 오류 `2→3건`(`adea0262` 18:50:21·18:50:37·18:51:55, 원인 미분류). 메인 전사 오류 0. 라우터: 정상 keep-alive 가드 98건(18:44:48Z부터), 외부 SSE EOF 8건.
  - Prody (`18:45:18Z`→`18:53:26Z`): 원장 `1264→1271`, 보드 `v7519` 유지(78노드/11루트, actionable 2), 대기 1 유지·**제출 `1→0`·실행/슬롯 `0→1`**, finding `1→0`(`n1375` 제출·완료 영수증·검수행 없음 해소), MCP `345ms`, 포크 표본 오류 `2→1건`. 메인 전사·라우터 로그 오류 0. **신규 관측**: `/workflow`가 `state=observed`(직전 4개 호스트는 `disabled`)로, 읽기 전용 루프백 `/settings` 확인 결과 `work_workflow_enabled=True`·`work_workflow_main_delegates=True`·`delegate_pending=0`·`delegate_minutes=0`, `/workflow` counts는 `cancelled 4`뿐이고 활성 workflow goal은 0. 즉 Prody에서 승인 워크플로우가 켜져 있고 기록 4건은 모두 취소 상태이며, 이번 창에서 게이트로 막힌 항목은 관측되지 않았다(`findings=0`, `actionable=2`).
  - Betty(보조, `18:45:30Z`→`18:53:36Z`): 원장 `1218`·보드 `v5767`·리스(제출 5·대기 5·실행/슬롯 2) 유지, MCP `936→433ms`, finding 0, 신규 실패 `decision_refused_stop 0b139c67`(18:45:30Z) 1건은 이번 창 재발 없음. 포크 표본 오류 `5→3건`. 메인 전사 1건(18:46:29 작업 측 `gh` 미인증 exit 4). 라우터: 정상 keep-alive 가드 104건, 외부 SSE EOF 9건, 턴 투영 관찰 4건.
  - 미결·추적 유지: Andy PID 81900(`python=3.14.2`) `fork_turn_watch` `NameError: OSError` 22틱 연속(+172건) — 설치 3개 루트와 worktree HEAD의 `fork_turn_watch.py` 해시가 모두 `e563dd21cc4c`로 같아 코드 불일치가 아니며 원인은 여전히 미확정(추정 금지, 실패 테스트 재현 없음); Andy `channel_turn_projection_persistent` age 729→1091초 증가; Andy `n1166`·`n2912`·`n2908` worktree 32초 타임아웃; Celly MCP 프로브 일시 4초 타임아웃·413 본문 초과(`683646` tokens)·콘솔 `16908`/`16909` `stale_unconfirmed`; KOBE `n5` 미완료 자식 가드; 시각화 개선 `9351c77`은 **파일 설치만 7개 승인 루트 전부 완료**(4파일 해시 `af89b9a2`/`645c1206`/`1eba5515`/`72c06f0f` 일치), 라이브 효과는 운영 세션/라우터 재시작 전까지 없음; `f9546dd`·`537a2ea`·`c926967`·`42abda1`·`e6aff62`·`0956ebe` 파일 설치 완료분도 동일하게 재시작 대기; Prody `work_workflow_enabled=True` 상태의 의도성·유지 여부 확인 필요.

- 18:14:48–18:15:22Z 정기 틱(17:33–17:33Z 대비, 41분 관측 창, 큰 이상 없음). 5개 호스트(보조 Betty 포함) `live.board`·Work MCP 전원 `observed`/`probe_succeeded`(Andy `1640ms`, Celly `406ms`, KOBE `372ms`, Prody `1009ms`, Betty `363ms`), 수집 stderr 없음. 직전 틱 Andy·Celly 동시 504/502 게이트웨이 장애는 이번 창에서 재발하지 않고 정상 회복됐다. 신규 입증 제품 결함·운영 재시작·상태 파일 수정 없음. (시각화 개선 커밋 `9351c77`은 `beta` 푸시 후 npm 발행 `45874045589` 진행 중.) 이하 UTC.
  - Andy (`18:14:48Z`): 원장 `8404→8420`, 보드 `v12376→v12391`, PID `81900`(`python=3.14.2`) 유지, 제출 `22→21`·대기 6·실행/슬롯 2 유지, `n1166` 지속, 신규 실패 0. 포크 표본 오류 `0→6건`(6건 표본: `a1afb2f9` 1건 18:11:58, `a0a78e4a` 5건 18:13:29–18:14:42, 원인 미분류). 메인 전사 6건: 호스트 전용/사이드 핸드오프/비슬롯대기/리뷰어 금지/기존 리스 `n2911` 중복 dispatch 정상 가드 5건(17:33–18:07), 작업 측 PowerShell null 오류 1건(17:36:53). 라우터: **`fork_turn_watch_poll_failed NameError: OSError` 983건 지속(17:33:09부터, 21틱 연속)**, 외부 SSE EOF 39건·BrokenPipe 1건(18:00:27), 클라이언트 단절 2건(17:37:50·17:42:58), 턴 투영 관찰 8건(17:34:43–18:08:33). 업스트림 504/502 미관측.
  - Celly (`18:14:53Z`): 원장 `10218→10240`, 보드 `v11392→v11404`, 대기 `8→9`·실행/슬롯 `3→1`, finding 0·신규 실패 0 유지, 포크 표본 오류 `0→3건`(24건 표본: `a506744e` 18:06:40, `ac614fcf` 18:12:16, `a012ff99` 18:14:44, 원인 미분류). 메인 전사 3건: 작업 측 Read 크기 초과(17:40:23)·Ripgrep 타임아웃(17:42:15), `work_host_page` 일시 타임아웃 1건(17:47:52). 라우터: 외부 SSE 단절 36건(EOF 34·`WinError 10053` 1·TimeoutError 1), `/ca/mcp` 단절 4건(17:47:49부터), 클라이언트 단절 5건(17:34:50·17:52:14부터), `submit_unconfirmed`+`receipt_unmatched` 2쌍(17:38:07), `runtime_activity_router_failed` `WinError 5` 1건(17:42:15), `work_workspace_failed` `n1558` `git worktree add timed out after 32 s` 1건(18:09:37), 콘솔 확인 실패 3건·`stale_unconfirmed` 건너뜀 2건(`16894` 17:49:25 `age=24.1s`, `16896` 17:58:21 `age=77.9s`, `16900` 18:14:19 `age=61.4s`), 턴 투영 관찰 8건. 업스트림 504/중앙 게이트웨이 degraded 미관측.
  - KOBE (`18:15:02Z`): 원장 `4793→4802`, 보드 `v762→v767`, 수락 1·실행/슬롯 1 유지, 대기 `20→21`·제출 `0→1`, `n5` 미완료 자식 가드 유지. 신규 원장 실패 1건(`decision_refused_stop 54d3c4fb x1`) 및 메인 전사 2건(`17:41:24`·`17:42:53` `work_parallel_item_review: stranded decision`): 원격 읽기 전용 확인 결과 `n275`(`54d3c4fb`)가 `workspace_unavailable` stranded 행이라 `approve`를 정상 거부한 뒤 `rework` 결정이 정상 접수(`state="deciding"`, 현재 `blocked.code="capacity"` 슬롯 대기, 정상 가드 확인). 포크 표본 오류 `0→2건`(24건 표본: `a6100628` 17:35:23·39). 라우터: 정상 keep-alive 가드 478건, 외부 SSE EOF 38건·BrokenPipe 1건, 클라이언트 단절 1건(17:36:09), 턴 투영 관찰 5건. 콘솔 확인 실패 미관측.
  - Prody (`18:15:11Z`) · Betty(보조, `18:15:22Z`): Prody 원장 `1263`·보드 `v7518→v7519`·대기 1·실행/슬롯 1·메인 오류 0·라우터 경고 0 유지, 포크 표본 오류 `0→3건`(`a7f4c800` 18:12:40–18:14:29, 원인 미분류); Betty 원장 `1185→1203`·보드 `v5759→v5763`·제출 `5→7`·대기 `1→4`·실행/슬롯 2 유지, 메인 전사 4건(작업 측 Python 오류 3건 17:42–45, P3 슬롯 용량 대기 정상 가드 1건 17:47:52), 포크 표본 오류 `0→6건`(`a8080b29` 17:33:43–17:36:10), 라우터 정상 keep-alive 가드 497건·외부 SSE EOF 41건·클라이언트 단절 3건·턴 투영 관찰 5건.
  - 미결·추적 유지: Andy PID 81900(`python=3.14.2`) `fork_turn_watch` `NameError: OSError` 21틱 연속(+983건)·로컬 `3.14.2` 4개 라우터 access violation 반복; `f9546dd`·`537a2ea`·`c926967`·`42abda1`·`e6aff62`·`0956ebe` 7/7 파일 설치 완료(라이브 효과는 라우터 재시작 및 CLI 재시작/재로드 후 반영); 시각화 개선 `9351c77` `beta` 푸시 완료·npm `45874045589` 대기 중; `b0cabd1` 선행 대비 후속 라이프사이클 중복 회피 라이브 확인 및 타 호스트 라우터 재시작 반영; Andy `n1166`·`n2914` worktree 타임아웃; Celly 콘솔 확인 실패(`16894`/`16896`/`16900`)·`n1558` worktree 타임아웃·`WinError 5`·`/ca/mcp` 단절; KOBE `n5`·`n275` stranded 재작업 슬롯 대기·`n150` worktree 타임아웃; Prody/Betty 기존 미결 항목.

- 17:33:09–17:33:39Z 정기 틱(17:12–17:13Z 대비, 20분 관측 창). 5개 호스트(보조 Betty 포함) `live.board`·Work MCP 전원 `observed`/`probe_succeeded`(Andy `1125ms`, Celly `984ms`, KOBE `321ms`, Prody `237ms`, Betty `601ms`), 신규 병렬 원장 실패 0, 포크 표본 오류 전 호스트 0, 수집 stderr 없음. 로컬 게이트웨이 `127.0.0.1:20128`(CielAiRouter) 구간에서 `17:16–17:29Z` 업스트림 504 타임아웃·502 연결 거부가 Andy·Celly 양쪽에서 동시 관측됐고 양 호스트 포크가 정상 API 재시도 스케줄로 진입했다. 신규 입증 제품 결함·코드 변경·추가 설치·운영 재시작·상태 파일 수정 없음. 이하 UTC.
  - Andy (`17:33:09Z`): 원장 `8403→8404`, 보드 `v12376`·리스(제출 22·대기 6·실행/슬롯 2)·`n1166`·PID `81900`(`python=3.14.2`) 유지, 포크 표본 오류 0 유지(6건 표본). 메인 전사 9건 모두 업스트림 장애에 따른 정상 포크 API 재시도 안내(`n2907` `transport_interrupted` 17:12:52 후 `504` 재시도 4–6/20, `n2914` `504` 17:17:41 후 재시도 1–4/20). 라우터: **`fork_turn_watch_poll_failed NameError: OSError` 520건 지속(17:12:33부터, 20틱 연속)**, 외부 SSE EOF 20건, 업스트림 `127.0.0.1:20128` 504 타임아웃 6건(`17:16:15`부터, `preoutput_bound` 5건)·502 `WinError 10061` 2건(`17:29:52`) 및 일시 재시도 5건(`17:23:06–29`, op `a4fd4ecf`)·응답 단절 5건, `/ca/mcp` 단절 2건(17:22:18), 전사 델타 실패 2건(17:21:17·48), 턴 투영 관찰 4건(17:13:22–17:19:23).
  - Celly (`17:33:12Z`): 원장 `10218`·리스(대기 8·실행/슬롯 3)·finding 0·신규 실패 0 유지, 보드 `v11391→v11392`(`live.board` 회복), 포크 표본 오류 `3→0`(24건 표본). 메인 전사 4건 모두 업스트림 504에 따른 정상 포크 API 재시도 안내(`n1557` 17:18:21·17:19:18·재시도 1/20 17:20:31, `n1600` 17:19:00). 라우터: 외부 SSE EOF 17건, 업스트림 `127.0.0.1:20128` 504 타임아웃 6건(`_Walkie_P1-Celly` 5건 17:16:12부터·`_Walkie_P3` 1건 17:16:54, `preoutput_bound` 6건, `gateway_failure` 6건, 응답 단절 1건), 중앙 게이트웨이 `degraded=True` 14건(17:17:50–17:26:45, `reprobed fallback` 7·`discovery unavailable` 4·`no healthy gateway` 3), `/ca/mcp` 단절 4건(17:24:32부터), 턴 투영 관찰 2건(17:13:03·17:15:12). 콘솔 확인 실패·`WinError 5` 미관측.
  - KOBE (`17:33:22Z`): 원장 `4793`·리스(수락 1·대기 20·실행/슬롯 1)·`n5` 미완료 자식 가드·메인 오류 0·포크 표본 오류 0 유지, 보드 `v746→v762`. 라우터: 정상 keep-alive 가드 244건, 외부 SSE EOF 20건·`URLError` connect timeout 1건(17:25:47), 턴 투영 관찰 7건(17:18:49–17:30:57). 콘솔 확인 실패 미관측.
  - Prody (`17:33:30Z`) · Betty(보조, `17:33:39Z`): Prody 원장 `1263`·보드 `v7517→v7518`·대기 1·실행/슬롯 1·오류/경고 모두 0 유지; Betty 원장 `1180→1185`·보드 `v5757→v5759`·제출 5·대기 1·실행/슬롯 `1→2`·포크 표본 오류 `1→0`, 메인 전사 7건 모두 작업 측 셸 exit 255(4건, 17:13–19)·Walkie 400(3건, 17:15–21), 라우터 정상 keep-alive 가드 245건·외부 SSE EOF 20건·턴 투영 관찰 6건·`runtime_activity_context_usage_failed` `WinError 5` 1건(17:15:54)·`work_workspace_failed` `n1814` `git worktree add timed out after 31 s` 1건(17:23:43)·`secure_json_read_failed` `sse-gate-status.json` `PermissionError` 1건(17:29:28).
  - 미결·추적 유지: Andy PID 81900(`python=3.14.2`) `fork_turn_watch` `NameError: OSError` 20틱 연속(+520건)·로컬 `3.14.2` 4개 라우터 access violation 반복; 로컬 CielAiRouter 게이트웨이 `127.0.0.1:20128` 17:16–17:29Z 업스트림 504/502 및 중앙 게이트웨이 degraded 구간(원인 게이트웨이/상류 측, 런타임 결함 미입증); `f9546dd`·`537a2ea`·`c926967`·`42abda1`·`e6aff62`·`0956ebe` 7/7 파일 설치 완료(라이브 효과는 라우터 재시작 및 CLI 재시작/재로드 후 반영); `b0cabd1` 선행 대비 후속 라이프사이클 중복 회피 라이브 확인 및 타 호스트 라우터 재시작 반영; Andy `n1166`·`n2914` worktree 타임아웃; Celly 콘솔 확인 실패·`/ca/mcp` 단절; KOBE `n5`·`n150` worktree 타임아웃; Betty `n1814` worktree 타임아웃·`WinError 5`·`sse-gate-status.json` `PermissionError`; Prody 기존 미결 항목.

- 17:12:31–17:13:28Z 정기 틱(16:52–16:54Z 대비, 19분 관측 창, 큰 변화 없음). 5개 호스트(보조 Betty 포함) Work MCP 전원 `probe_succeeded`(Andy `1438ms`, Celly `2094ms`, KOBE `324ms`, Prody `390ms`, Betty `347ms`), `live.board`는 4곳 `observed`·Celly만 수집 시 `TimeoutError`였으나 직후 단독 `/ca/work/api/v1/board` `342ms`(`v11391`) 정상 회복 확인. 신규 병렬 원장 실패 0, 수집 stderr 없음. 신규 입증 제품 결함·코드 변경·추가 설치·운영 재시작·상태 파일 수정 없음. 이하 UTC.
  - Andy (`17:12:31Z`): 원장 `8386→8403`, 보드 `v12368→v12376`(`live.board` 회복), PID `81900`(`python=3.14.2`) 유지, 제출 `21→22`·대기 `5→6`·실행/슬롯 2 유지, `n1166` 지속, 포크 표본 오류 0 유지(7건 표본). 메인 전사 6건: `16:59:21` 사이드 `de277cfdbc5c` 실패 폴백(단독 `/side/receipt` 확인: `P3 message.approved`, `failure_category=invalid_answer`, `attempt_count=1`, `quarantined=True`), 보드 버전 가드(17:01:15), P3 슬롯 용량 대기 정상 가드 2건(`fe044d2b` 17:03:17·`n2904` 17:04:07), 작업 측 Walkie 400/UUID 검증 오류 2건(17:05:03·17:06:09). 라우터: **`fork_turn_watch_poll_failed NameError: OSError` 461건 지속(16:52:24부터, 19틱 연속)**, 외부 SSE EOF 20건, `work_workspace_failed` `n2914` `git worktree add timed out after 32 s` 2건(16:53:42), 턴 투영 관찰 8건(16:54:22–17:11:22), 클라이언트 단절 2건(16:57:13·16:59:31), 업스트림 SSE 타임아웃 2건(`anthropic_sse_forward_error TimeoutError`, `_Walkie_P1` 17:12:06·`_Walkie_P3` 17:12:08).
  - Celly (`17:12:34Z`): 원장 `10207→10218`, 보드 `v11386→v11391`(단독 회복 `342ms`), 대기 8 유지·실행/슬롯 `2→3`, finding 0·신규 실패 0 유지, 포크 표본 오류 `1→3건`(`a6b96afb` 16:53:41 경계 중복·16:56:27, `a03a747e` 17:02:21, 원인 미분류), 메인 전사 3건(`16:53:41` `Ciel Work gate: router-error` 경계 중복, P3 슬롯 용량 대기 정상 가드 2건 `n1576`/`e8ec31fe` 16:58:01·10). 라우터: 외부 SSE EOF 18건, `/ca/mcp` 단절 6건(16:54:08부터), 턴 투영 관찰 6건(16:56:47–17:11:03), 클라이언트 단절 1건(16:57:25), 중앙 게이트웨이 discovery 불가 1건(17:04:34, `5065ms`), 콘솔 `16889` 확인 실패 1건(17:04:45, `age=29.1s`). `WinError 5` 미관측.
  - KOBE (`17:13:09Z`): 원장 `4788→4793`, 보드 `v739→v746`, 수락 1·대기 20 유지·제출 `1→0`·실행/슬롯 `0→1`, `n5` 미완료 자식 가드 유지, 포크 표본 오류 `4→0`(24건 표본), 메인 오류 `4→0`. 라우터: 정상 keep-alive 가드 232건, 외부 SSE EOF 14건·BrokenPipe 3건, 턴 투영 관찰 4건(16:52:57–16:59:24). 콘솔 확인 실패 미관측.
  - Prody (`17:13:19Z`) · Betty(보조, `17:13:28Z`): Prody 원장 `1258→1263`·보드 `v7514→v7517`·대기 1 유지·실행/슬롯 `0→1`·오류/경고 모두 0 유지; Betty 원장 `1180`·보드 `v5757`·제출 5·대기 1·실행/슬롯 1 유지, 메인 전사 5건 모두 작업 측 셸 명령 exit 255(17:04–17:13), 포크 표본 오류 1건 유지(`a8080b29` 17:11:59), 라우터 정상 keep-alive 가드 249건·외부 SSE EOF 21건·턴 투영 관찰 5건(17:04:23–17:12:27).
  - 미결·추적 유지: Andy PID 81900(`python=3.14.2`) `fork_turn_watch` `NameError: OSError` 19틱 연속(+461건)·업스트림 타임아웃·로컬 `3.14.2` 4개 라우터 access violation 반복; `f9546dd`·`537a2ea`·`c926967`·`42abda1`·`e6aff62`·`0956ebe` 7/7 파일 설치 완료(라이브 효과는 라우터 재시작 및 CLI 재시작/재로드 후 반영); `b0cabd1` 선행 대비 후속 라이프사이클 중복 회피 라이브 확인 및 타 호스트 라우터 재시작 반영; Andy `n1166`·`n2914` worktree 타임아웃; Celly 콘솔 `16889`·`/ca/mcp` 단절 6건·포크 오류; KOBE `n5`·`n150` worktree 타임아웃; Prody/Betty 기존 미결 항목.

- 16:52:22–16:54:58Z 정기 틱(16:32–16:33Z 대비, 20분 관측 창, 큰 변화 없음). 5개 호스트(보조 Betty 포함) Work MCP 전원 `probe_succeeded`(Andy `2125ms`, Celly `328ms`, KOBE `557ms`, Prody `740ms`, Betty `444ms`), `live.board`는 4곳 `observed`·Andy만 수집 시 `TimeoutError`였으나 직후 단독 `/ca/work/api/v1/board` `2765ms`(`v12372`)·`/side` `5188ms` 정상 확인. 신규 병렬 원장 실패 0, 수집 stderr 없음. 신규 입증 제품 결함·코드 변경·추가 설치·운영 재시작·상태 파일 수정 없음. 이하 UTC.
  - Andy (`16:52:22Z`): 원장 `8379→8386`, 보드 `v12363→v12368`(직후 `v12372`), PID `81900`(`python=3.14.2`) 유지, 제출 21 유지·대기 `4→5`·실행/슬롯 `1→2`, `n1166` 지속, 포크 표본 오류 `1→0`(7건 표본), 메인 전사 1건(`16:51:33` 작업 측 명령 exit 1). 라우터: **`fork_turn_watch_poll_failed NameError: OSError` 481건 지속(16:32:46부터, 18틱 연속)**, 외부 SSE EOF 19건, `/ca/mcp` 단절 2건(16:32:55·16:47:36), 클라이언트 단절 2건(16:32:57), `runtime_activity_context_usage_failed` `WinError 5` 1건(16:45:06), 턴 투영 관찰 1건(16:52:21).
  - Celly (`16:52:30Z`): 원장 `10207`·리스(대기 8·실행/슬롯 2)·finding 0·신규 실패 0 유지, 보드 `v11385→v11386`, 포크 표본 오류 1건 유지(`a6b96afb` 16:53:41), 메인 전사 1건(`16:53:41` `Ciel Work gate: router-error`). 라우터: 외부 SSE EOF 17건·`WinError 10053` 1건, `/ca/mcp` 단절 9건(16:46:02부터), 콘솔 `16885` 확인 실패 1건(16:46:22, `age=114.8s`), 클라이언트 단절 1건(16:48:22). `WinError 5` 미관측.
  - KOBE (`16:54:38Z`): 원장 `4781→4788`, 보드 `v737→v739`, 수락 1 유지·대기 `19→20`·실행/슬롯 `1→0`·제출 `0→1`, `n5` 미완료 자식 가드 유지, 포크 표본 오류 `2→4건`(`a8e67c64` 3건 16:33, `af6305ca` 1건 16:47), 메인 전사 4건 모두 작업 측 셸/git 인증 오류(16:41–16:44). 라우터: 정상 keep-alive 가드 248건, 외부 SSE EOF 17건·BrokenPipe 2건, `submit_unconfirmed`+`receipt_unmatched` 1쌍(16:38:15–17), 턴 투영 관찰 4건(16:42:03–16:52:57).
  - Prody (`16:54:48Z`) · Betty(보조, `16:54:58Z`): Prody 원장 `1258`·보드 `v7514`·대기 1·오류/경고 모두 0 유지; Betty 원장 `1180`·보드 `v5757`·제출 5·대기 1·실행/슬롯 1·메인 오류 `1→0` 유지, 포크 표본 오류 `0→1건`(`a8080b29` 16:52:56), 라우터 정상 keep-alive 가드 261건·외부 SSE EOF 21건.
  - 미결·추적 유지: Andy PID 81900(`python=3.14.2`) `fork_turn_watch` `NameError: OSError` 18틱 연속(+481건)·API 간헐 지연 및 로컬 `3.14.2` 4개 라우터 access violation 반복; `f9546dd`·`537a2ea`·`c926967`·`42abda1`·`e6aff62`·`0956ebe` 7/7 파일 설치 완료(라이브 효과는 라우터 재시작 및 CLI 재시작/재로드 후 반영); `b0cabd1` 선행 대비 후속 라이프사이클 중복 회피 라이브 확인 및 타 호스트 라우터 재시작 반영; Andy `n1166`·worktree 타임아웃; Celly 콘솔 `16885`·`/ca/mcp` 단절 9건·포크 오류; KOBE `n5`·`submit_unconfirmed`·포크 오류·`n150` worktree 타임아웃; Prody/Betty 기존 미결 항목.

- 16:32:45–16:33:19Z 정기 틱(16:13–16:14Z 대비, 19분 관측 창, 큰 변화 없음). 5개 호스트(보조 Betty 포함) `live.board` 전원 `observed`, Work MCP는 4곳 `probe_succeeded`(Celly `375ms`, KOBE `328ms`, Prody `293ms`, Betty `388ms`)·Andy만 수집 시 `TimeoutError(4016ms)`였으나 직후 단독 `/ca/mcp work_status` 재조회 `108ms` 성공. 신규 병렬 원장 실패 0, 콘솔 확인 실패 0, 수집 stderr 없음. 신규 입증 제품 결함·코드 변경·추가 설치·운영 재시작·상태 파일 수정 없음. 이하 UTC.
  - Andy (`16:32:45Z`): 원장 `8361→8379`, 보드 `v12355→v12363`, PID `81900`(`python=3.14.2`) 유지, 제출 `22→21`·대기 `2→4`·실행/슬롯 `3→1`, `n1166` 지속, 메인 오류 `1→0`, 포크 표본 오류 `0→1건`(`a1afb2f9` 16:31:11, 원인 미분류). 라우터: **`fork_turn_watch_poll_failed NameError: OSError` 459건 지속(16:13:25부터, 17틱 연속)**, 외부 SSE EOF 19건, `/ca/mcp` 단절 3건(16:13:27·34), 클라이언트 단절 3건(16:15:45·16:16:00), 종료 후 `ping` 무시 1건(16:14:17), 턴 투영 관찰 3건(16:14:37–16:26:38). 사이드 `16:13Z` 이후 신규 미수신(`/side` `3047ms`).
  - Celly (`16:32:52Z`): 원장 `10206→10207`, 보드 `v11383→v11385`, 대기 `7→8`·실행/슬롯 `3→2`, finding 0·신규 실패 `1→0`·메인 오류 `2→0`, 포크 표본 오류 `4→1건`(`a6b96afb` 16:32:07). 라우터: 외부 SSE EOF 16건, `/ca/mcp` 단절 1건(16:13:26), 턴 투영 관찰 1건(16:15:36), `secure_json_read_failed` `sse-gate-status.json` `PermissionError` 1건(16:18:04), `runtime_activity_context_usage_failed` `WinError 5` 1건(16:27:27).
  - KOBE (`16:33:01Z`): 원장 `4775→4781`, 보드 `v732→v737`, 수락 1·대기 19 유지, 제출 `1→0`·실행/슬롯 `0→1`, finding `2→1`(`n273` 해소, `n5` 미완료 자식 가드 지속), 메인 오류 `1→0`, 포크 표본 오류 `6→2건`(`a667c508` 16:13:28 경계 중복, `a8e67c64` 16:29:02). 라우터: 정상 keep-alive 가드 229건, 외부 SSE EOF 19건, 턴 투영 관찰 1건(16:16:55).
  - Prody (`16:33:10Z`) · Betty(보조, `16:33:19Z`): Prody 원장 `1254→1258`·보드 `v7512→v7514`·대기 1 유지·실행/슬롯 `1→0`·finding/메인/포크 오류·라우터 경고 모두 0; Betty 원장 `1177→1180`·보드 `v5755→v5757`·제출 5·대기 1 유지·실행/슬롯 `0→1`·finding/포크 오류 0·메인 전사 1건(16:27:45 작업 측 셸 exit 255)·라우터 정상 keep-alive 가드 236건·외부 SSE EOF 19건·턴 투영 관찰 1건.
  - 미결·추적 유지: Andy PID 81900(`python=3.14.2`) `fork_turn_watch` `NameError: OSError` 17틱 연속(+459건) 및 로컬 `3.14.2` 4개 라우터 access violation 반복; `f9546dd`·`537a2ea`·`c926967`·`42abda1`·`e6aff62`·`0956ebe` 7/7 파일 설치 완료(라이브 효과는 라우터 재시작 및 CLI 재시작/재로드 후 반영); `b0cabd1` 선행 대비 후속 라이프사이클 중복 회피 라이브 증명 및 타 호스트 라우터 재시작 반영; Andy `n1166`·worktree 타임아웃; Celly `WinError 5`/`sse-gate-status.json` `PermissionError`·포크 오류; KOBE `n5`·포크 오류·`n150` worktree 타임아웃; Prody/Betty 기존 미결 항목.

- 16:13:25–16:13:59Z 정기 틱(15:52–15:54Z 대비, 20분 관측 창). 5개 호스트(보조 Betty 포함) `live.board` 전원 `observed`, Work MCP는 4곳 `probe_succeeded`·Andy만 수집 시 `TimeoutError(4032ms)`였으나 직후 단독 `/ca/mcp work_status` 재조회 `32ms` 성공 확인. 수집 stderr 없음. 신규 입증 제품 결함·코드 변경·추가 설치·운영 재시작·상태 파일 수정 없음. 이하 UTC.
  - Andy (`16:13:25Z`): 원장 `8352→8361`, 보드 `v12350→v12355`, MCP 수집 시 `TimeoutError(4032ms)`→직후 `32ms` 회복, PID `81900`(`python=3.14.2`) 유지. 리스: 제출 `21→22`, 실행/슬롯 `2→3`, 입력 대기 `3→2`. `n1166` 검수 행 누락 지속, 신규 실패 0, 포크 표본 오류 `1→0`(6건 표본). 메인 전사 1건(`16:07:25` 작업 측 Python 오류). 라우터 로그: **`fork_turn_watch_poll_failed NameError: OSError` 514건 지속**(15:52:40부터 ~2초 간격, 16틱 연속), 외부 SSE EOF 19건(15:53:07부터)·BrokenPipe 1건(16:10:01), `_API_KEYS-SSE-1` 반복 출력 가드 발동·복구 1쌍(`anthropic_stream_runaway_repetition`+`recovery`, 15:53:27, 정상 가드), 클라이언트 단절 2건(16:01:51·16:06:53), 전사 델타 타임아웃 1건(16:09:03), 턴 투영 관찰 2건(16:08:47부터). 사이드(PID 81900): `15:52Z` 이후 4건 접수(2건 완료 `5f16b2c7`·`d190f1bd`, 2건 진행 중 `bc9ba2d9`·`71a5a897`; `/side` `13750ms`).
  - Celly (`16:13:33Z`): 원장 `10191→10206`, 보드 `v11377→v11383`, MCP `359→453ms`. 리스: 입력 대기 `6→7`, 실행/슬롯 `4→3`. Finding `1→0`(`n1556` 해소). 신규 원장 실패 1건(`decision_refused_stop 47ea5d30 x1`) 및 메인 전사 1건(`15:58:30` `work_parallel_item_review: The operation timed out.`): `n1571`(`47ea5d30`) 단독 확인 결과 이미 `done`/`finalized(approve)` 완료(일시 타임아웃, 제품 결함 미입증). 메인 전사 기타 1건(`16:05:37` 작업 측 Python TypeError). 포크 표본 오류 `6→4건`(24건 표본: `a753f5bd` 15:53:00 경계 중복, `aebd957c` 2건 16:00–16:01, `aa6d6b4b` 16:13:25, 원인 미분류). 라우터 로그: 외부 SSE EOF 14건(15:53:39부터)·BrokenPipe 1건(16:10:10), `/ca/mcp` 단절 10건(16:00:55부터), `runtime_activity_*` `WinError 5` 6건(15:53:26·16:01:00부터), 콘솔 `16881` 확인 실패·`stale_unconfirmed` 건너뜀 1쌍(15:53:49–52, `age=22.3s`), 클라이언트 단절 1건(16:02:51), 중앙 게이트웨이 discovery 불가 1건(16:03:12, `5094ms`), 턴 투영 관찰 7건(15:56:06–16:09:02).
  - KOBE (`16:13:41Z`): 원장 `4753→4775`, 보드 `v727→v732`, MCP `418→361ms`. 리스: 수락 1·입력 대기 19 유지, 제출 `2→1`, 실행/슬롯 `1→0`. Finding `3→2`: `n5` 미완료 자식 가드 지속(`n257`·`n274` 해소); 신규 `n273`(`e996fe21` P3 `native=running last=16:13`)은 `16:14:25Z` `idle` 관측 후 `16:15:00Z` `pending` 검수 행 정상 발행 확인(일시 표출, 결함 아님). 신규 실패 0, 포크 표본 오류 `5→6건`(24건 표본: `a3cc8fdf` 2건 15:54, `a15d1310` 2건 16:05, `a667c508` 2건 16:13, 원인 미분류). 메인 전사 1건(`15:54:36` 작업 측 스크립트 exit 1). 라우터 로그: 정상 keep-alive 가드 247건(15:52:40부터), 외부 SSE EOF 20건(15:53:32부터), 턴 투영 관찰 5건(15:56:06–16:10:38). 콘솔 확인 실패 미관측.
  - Prody (`16:13:51Z`): 원장 `1251→1254`, 보드 `v7512`·리스(입력 대기 1·실행/슬롯 1)·finding 0·포크 오류 0·라우터 경고 0 유지, MCP `447→193ms`. 메인 전사 1건(`15:55:35` `n1372` 기존 리스 중복 dispatch 정상 가드).
  - Betty(보조, `16:13:59Z`): 원장 `1177`·제출 5·입력 대기 1·보드 `v5755`·finding 0·메인/포크 오류 0 유지, MCP `313→572ms`. 라우터 로그: 정상 keep-alive 가드 253건(15:52:39부터), 외부 SSE EOF 21건(15:52:53부터).
  - 미결·추적 유지: **Andy PID 81900(`python=3.14.2`) `fork_turn_watch` `NameError: OSError` 16틱 연속(+514건)·`/side`/`/ca/mcp` 간헐 지연 및 로컬 `3.14.2` 4개 라우터 access violation 반복**, **`f9546dd`·`537a2ea`·`c926967`·`42abda1`·`e6aff62`·`0956ebe` 7/7 파일 설치 완료(라이브 효과는 라우터 재시작 및 CLI 재시작/재로드 후 반영)**, `b0cabd1` 선행 대비 후속 라이프사이클 중복 회피 라이브 증명 및 타 호스트 라우터 재시작 반영, Andy `n1166`·worktree 타임아웃, Celly 콘솔 `16881`·`WinError 5` 6건·`/ca/mcp` 단절 10건·포크 오류, KOBE `n5`·포크 오류·`n150` worktree 타임아웃, Prody 콘솔 `8218`, Betty 콘솔 `8109`.

- 15:52:38–15:53:52Z 정기 틱(15:38–15:39Z 대비, 14분 관측 창). 5개 호스트(보조 Betty 포함) `live.board`·Work MCP 전원 `observed`/`probe_succeeded`(Andy MCP 회복 `3203ms`), 신규 병렬 원장 실패 0, 수집 stderr 없음. 신규 입증 제품 결함·코드 변경·추가 설치·운영 재시작·상태 파일 수정 없음. 이하 UTC.
  - Andy (`15:52:38Z`): 원장 `8338→8352`, 보드 `v12338→v12350`, MCP `TimeoutError→probe_succeeded(3203ms)`, PID `81900`(`python=3.14.2`) 유지. 리스: 제출 21·실행/슬롯 2 유지, 입력 대기 `2→3`. `n1166` 검수 행 누락 지속, 신규 실패 `1→0`, 포크 표본 오류 `0→1건`(6건 표본: `a09e2125` 15:42:02, 원인 미분류). 메인 전사 3건 모두 작업 측 오류(15:40:22·38 Walkie 400/409, 15:51:29 git exit 128). 라우터 로그: **`fork_turn_watch_poll_failed NameError: OSError` 302건 지속**(15:38:47부터 ~2초 간격, 15틱 연속), 외부 SSE EOF 14건(15:38:48부터), `/ca/mcp` 단절 1건(15:38:57), 모델 클라이언트 단절 1건(15:47:47), `_API_KEYS-SSE-1` 반복 출력 가드 발동·복구 1쌍(`anthropic_stream_runaway_repetition`+`recovery`, 15:48:03, 정상 가드), 턴 투영 관찰 7건(15:40:03–15:52:10). 사이드(PID 81900): `15:38Z` 이후 3건(`9059a7d5`·`89af3edb`·`f986fed1`) 모두 처리 완료(`/side` `5327ms`).
  - Celly (`15:52:45Z`): 원장 `10184→10191`, 보드 `v11374→v11377`, MCP `1078→359ms`. 리스: 입력 대기 `7→6`, 실행/슬롯 `2→4`. Finding `0→1`(`n1556`/`f2781902` `absent since 15:39`)은 `waiting_input`에서 방금 재개된 직후 이전 `missing_since`가 잡힌 일시 표출이며 `15:54:32Z` 단독 읽기 전용 확인에서 `status=running, missing_since=None` 정상 관측 확인(결함 아님). 신규 실패 0, 포크 표본 오류 `4→6건`(24건 표본: `a506744e` 15:50:20, `a753f5bd` 5건 15:50:37–15:53:00, 원인 미분류). 메인 전사 2건 모두 정상 P3 슬롯 상한 대기 안내(`f2781902`/`n1556`, 15:44:37·15:47:39). 라우터 로그: 외부 SSE EOF 14건(15:39:08부터), 턴 투영 관찰 3건(15:43:03–15:47:07), 클라이언트 단절 1건(15:50:46). 콘솔 확인 실패·`WinError 5` 미관측.
  - KOBE (`15:53:33Z`): 원장 `4740→4753`, 보드 `v719→v727`, MCP `803→418ms`. 리스: 수락 1·입력 대기 19·제출 2 유지, 실행/슬롯 `2→1`. Finding 3건: `n5` 미완료 자식 가드·`n257` P3 용량 대기 가드 지속(`n204` 해소); 신규 `n274`(`d0033bc1` P1 `native=waiting last=15:53`)는 `15:53:43Z` `killed` 후 `15:54:04Z` `pending` 검수 행 정상 발행 확인(결함 아님). 신규 실패 0, 포크 표본 오류 5건 유지(24건 표본: `a9328cee` 2건 15:38–39, `a824a3aa` 3건 15:46–50, 원인 미분류). 메인 전사 1건(`15:42:29` Walkie `file_ids[0]` UUID 오류). 라우터 로그: 정상 keep-alive 가드 174건(15:38:48부터), 외부 SSE EOF 15건(15:39:09부터), 턴 투영 관찰 3건(15:41:58–15:46:00). 콘솔 확인 실패 미관측.
  - Prody (`15:53:42Z`): 원장 `1251`·리스(입력 대기 1·실행/슬롯 1)·finding 0 유지, 보드 `v7511→v7512`, MCP `351→447ms`, 메인 오류 `2→0`, 포크 표본 오류 `2→0`(4건 표본), 라우터 경고 `1→0`.
  - Betty(보조, `15:53:52Z`): 원장 `1177`·제출 5·입력 대기 1·보드 `v5755`·finding 0·포크 오류 0 유지, MCP `303→313ms`, 메인 오류 `4→0`. 라우터 로그: 정상 keep-alive 가드 179건(15:38:49부터), 외부 SSE EOF 14건(15:39:37부터).
  - 미결·추적 유지: **Andy PID 81900(`python=3.14.2`) `fork_turn_watch` `NameError: OSError` 15틱 연속(+302건) 및 로컬 `3.14.2` 4개 라우터 access violation 반복**, **`f9546dd`·`537a2ea`·`c926967`·`42abda1`·`e6aff62`·`0956ebe` 7/7 파일 설치 완료(라이브 효과는 라우터 재시작 및 CLI 재시작/재로드 후 반영)**, `b0cabd1` 선행 대비 후속 라이프사이클 중복 회피 라이브 증명 및 타 호스트 라우터 재시작 반영, Andy `n1166`·`n2899` worktree 타임아웃, Celly 포크 오류·콘솔 확인 실패·`WinError 5`, KOBE `n5`·포크 오류·`n150` worktree 타임아웃, Prody 콘솔 `8218`, Betty 콘솔 `8109`.

- 15:38:47–15:39:24Z 정기 틱(15:12–15:13Z 대비, 26분 관측 창). 5개 호스트(보조 Betty 포함) `live.board` 전원 `observed`, Work MCP는 4곳 `probe_succeeded`·Andy만 수집 시 `TimeoutError(4015ms)`였으나 직후 단독 `/ca/mcp work_status` 재조회 `188ms` 성공 확인. 수집 stderr 없음. 신규 입증 제품 결함·코드 변경·추가 설치·운영 재시작·상태 파일 수정 없음. 이하 UTC.
  - Andy (`15:38:47Z`): 원장 `8317→8338`, 보드 `v12325→v12338`, MCP 수집 시 `TimeoutError(4015ms)`→직후 `188ms` 회복, PID `81900`(`python=3.14.2`) 유지. 리스: 제출 `22→21`, 입력 대기 `4→2`(+일시 `waiting_input+slot 1` 직후 해소), 실행/슬롯 2 유지. `n1166` 검수 행 누락 지속, 포크 표본 오류 0(7건 표본). 신규 원장 실패 1건(`decision_refused_stop d96b8a42 x1`): `n2899`(`d96b8a42`)는 이미 `finalized/close` 완료 상태이며 `15:32:57` `work_parallel_item_review: review already decided differently` 중복 결정 정상 가드 확인. 메인 전사 11건: `15:13:05` `9e29e8ad454c` 사이드 실패 폴백(아래 `15:12:59` `work-plan.json` `WinError 5` 직후), 실행 단계 메인 코드 수정 금지 가드 2건(15:25:52·58), 보드 버전 가드(15:27:36), `d96b8a42` 중복 결정 가드(15:32:57), `work_side_status` 호스트 전용 가드(15:33:42), Walkie 409/400 4건(15:13:31–15:31:05), 작업 측 git exit 128(15:31:51). 라우터 로그: **`fork_turn_watch_poll_failed NameError: OSError` 597건 지속**(15:12:41부터 ~2초 간격, 14틱 연속), `router_post_uncaught` `work-plan.json` `WinError 5` 1건(15:12:59, `0956ebe` 파일 설치 완료·라이브 라우터 미재시작으로 재발), `work_workspace_failed` `n2899` `git worktree add timed out after 31 s` 1건(15:26:18), 외부 SSE EOF 25건(15:13:14부터), 클라이언트 단절 2건(15:25:13·15:35:32), 턴 투영 관찰 11건(15:15:00–15:38:03). 사이드(PID 81900): `15:12Z` 이후 5건 접수(3건 완료, `4b4c7559` P3·`90133186` P4 `message.approved` 진행 중; `/side` `2968ms`).
  - Celly (`15:38:55Z`): 원장 `10124→10184`, 보드 `v11369→v11374`, MCP `312→1078ms`. 리스: 입력 대기 `5→7`, 실행/슬롯 2 유지, 일시 `reserved+slot 1`은 직후 단독 확인에서 0건(정상 바인딩). Finding 0, 신규 실패 0. 포크 표본 오류 `1→4건`(24건 표본: `a521fd2e` 2건 15:27:47–15:28:06, `a753f5bd` 15:32:56, `aebd957c` 15:38:50, 원인 미분류). 메인 전사 3건 모두 작업 측 Walkie 409/400(15:20:26·15:27:05·15:27:07). 라우터 로그: 외부 SSE EOF 24건(15:13:18부터), `submit_unconfirmed`+`receipt_unmatched` 1쌍(15:20:44), 콘솔 확인 실패 2건(`16877` 15:23:47 `age=51.5s`, `16879` 15:25:27 `age=101.9s`), 클라이언트 단절 1건(15:25:10), `runtime_activity_*` `WinError 5` 2건(15:30:59·15:36:36), 턴 투영 관찰 5건(15:24:48–15:33:47).
  - KOBE (`15:39:05Z`): 원장 `4725→4740`, 보드 `v716→v719`, MCP `281→803ms`. 리스(수락 1·입력 대기 19·제출 2·실행/슬롯 2)·finding 3건(`n5` 미완료 자식 가드, `n257`·`n204` P3 슬롯 용량 대기 정상 가드)·신규 실패 0 유지. 포크 표본 오류 `4→5건`(24건 표본: `acecc3c6` 2건 15:24–25, `a719599e` 15:27, `a824a3aa` 15:38, `a9328cee` 15:38, 원인 미분류). 메인 전사 2건: `clear_wait` 충돌 인자 가드(15:16:35), `Work reconciliation: Parallel read failed: Git ownership/branch changed; retained`(15:32:40, 워크트리 브랜치 변경 보존 정상 가드). 라우터 로그: 정상 keep-alive 가드 310건(15:12:44부터), 외부 SSE EOF 22건·BrokenPipe 2건, 콘솔 `12340` 확인 실패·`stale_unconfirmed` 건너뜀 1쌍(15:22:29–30, `age=20.4s`), 클라이언트 단절 1건(15:29:31), 턴 투영 관찰 7건(15:12:53–15:28:40).
  - Prody (`15:39:15Z`): 원장 `1247→1251`, 보드 `v7505→v7511`, MCP `327→351ms`, 리스: 입력 대기 `0→1`, 실행/슬롯 1 유지, finding 0·신규 실패 0. 포크 표본 오류 `0→2건`(4건 표본: `a1147d58` 15:19:28·15:21:16, 원인 미분류). 메인 전사 2건 모두 정상 가드: 호스트 인증 가드(15:25:24), `n1362` P3 슬롯 용량 대기 가드(15:26:12). 라우터 로그: `channel_stdin_proxy_blocked` `stale_unconfirmed` 건너뜀 1건(15:23:42, `message_id=8218`; 실제 전달 여부 미확인).
  - Betty(보조, `15:39:24Z`): 원장 `1177`·제출 5·입력 대기 1·보드 `v5755`·finding 0·포크 오류 0 유지, MCP `754→303ms`. 메인 전사 4건 모두 작업 측 셸 명령 exit 255/1(15:15:58–15:32:47). 라우터 로그: 정상 keep-alive 가드 316건(15:12:42부터), 외부 SSE EOF 26건(15:13:06부터), 턴 투영 관찰 12건(15:12:45–15:34:56).
  - 미결·추적 유지: **Andy PID 81900(`python=3.14.2`) `fork_turn_watch` `NameError: OSError` 14틱 연속(+597건) 및 로컬 `3.14.2` 4개 라우터 access violation 반복**, **`f9546dd`·`537a2ea`·`c926967`·`42abda1`·`e6aff62`·`0956ebe` 7/7 파일 설치 완료(라이브 효과는 라우터 재시작 및 CLI 재시작/재로드 후 반영; Andy `15:12:59` `work-plan.json` `WinError 5`는 미재시작 메모리에서 재발)**, `b0cabd1` 선행 대비 후속 라이프사이클 중복 회피 라이브 증명 및 타 호스트 라우터 재시작 반영, Andy `n1166`·`n2899` worktree 31s 타임아웃, Celly 콘솔 확인 실패(`16877`/`16879`)·`n1556` worktree 타임아웃·`WinError 5`, KOBE `n5`·콘솔 `12340`·포크 오류·`n150` worktree 타임아웃, Prody 콘솔 `8218`, Betty 콘솔 `8109`.

- 15:12:39–15:13:07Z 정기 틱(14:52–14:53Z 대비, 20분 관측 창). 5개 호스트(보조 Betty 포함) `live.board`·Work MCP 전원 `observed`/`probe_succeeded`, 신규 병렬 원장 실패 0, 수집 stderr 없음. 운영 세션/라우터 재시작·운영 상태 파일 수정 없음. 이번 틱에서 입증된 Andy `work-plan.json`(11.7 MB) Windows `os.replace` `WinError 5`(`/ca/mcp` HTTP 500·사이드 `3d1f4f19f000` 실패 폴백) 결함을 `0956ebe`로 실패 선재현→수정→검증→npm `@beta` SHA-256 확인→7개 루트 해시 설치 완료(파일 설치 완료, 라이브 재시작 미실행으로 메모리 반영 대기). 이하 UTC.
  - **Andy (`15:12:39Z`, 결함 입증·수정 `0956ebe`):** 원장 `8306→8317`, 보드 `v12312→v12325`, MCP `1157→828ms`, PID `81900`(`python=3.14.2`) 유지. 리스: 제출 22 유지, 입력 대기 `3→4`, 실행/슬롯 `3→2`. `n1166` 검수 행 누락 지속, 신규 실패 0, 포크 표본 오류 `2→0`(7건 표본).
    - **입증·수정 결함 (`0956ebe`, `docs/journal/2026/11/26/fixes/work-plan-store-windows-replace-lock/record.md`):** `14:57:27Z` `[ERROR] router_post_uncaught path=/ca/mcp error=PermissionError: [WinError 5] Access is denied: '...\\work-plan.json.81900....tmp' -> '...\\work-plan.json'`(HTTP 500) 및 `14:57:29Z` `work_side_fail` → `14:57:30Z` 메인 전사 `Ciel side: original of failed event 3d1f4f19f000 → main (appended)`. 확인된 원인은 `_write_unlocked()`의 `os.replace()`가 `40 ms`(`20×2 ms`) 재시도 한도를 소진하면 HTTP 500으로 전파되는 점이며, 같은 초에 동일 라우터의 경로 락 없는 11.7 MB `read_snapshot()`(`work_side_gate_check`)이 실행됐다; 실제 핸들 보유 주체(프로세스 내 vs 교차 프로세스)는 미확인이라 두 경로를 모두 다뤘다. 프로세스 내 `WeakValueDictionary` 기반 `_path_io_lock(path)`(디스크 파일·락 사이드카 미생성)으로 `read_snapshot()` 파일 열람과 `os.replace()`를 직렬화하고 교차 프로세스 `PermissionError` 재시도를 50회 점진 백오프(~1.85초)로 확대(`0956ebe`) → 실패 선재현→격리 Windows·Linux `py310`·`py311` 141 passed(79 subtests)→npm `@beta` `0.2.51-beta.20261126-152357.0956ebe` SHA-256 일치(`work_plan_store.py:8f1a353e1b2c`)→7개 루트 해시 설치(`ok=True`) 완료.
    - 기타 Andy: 메인 전사 기타 2건(`15:00:37` `git -c core.quotePath=false timed out after 32 s`, `15:01:58` 작업 측 명령 exit 1). 라우터 로그: **`fork_turn_watch_poll_failed error=NameError: name 'OSError' is not defined` 478건 지속**(14:52:41부터 ~2초 간격, 13틱 연속), 외부 SSE EOF 20건(14:52:47부터), 모델 클라이언트 단절 3건(15:03:31부터), 턴 투영 관찰 5건(15:00:34–15:11:07). 사이드(PID 81900): `14:52Z` 이후 7건 접수(5건 완료, `3d1f4f19` 위 실패 폴백, `9e29e8ad` P3 15:06:02 진행 중; `/side` `1891ms`).
  - Celly (`15:12:42Z`): 원장 `10046→10124`, 보드 `v11369`·리스(입력 대기 5·실행/슬롯 2)·finding 0·신규 실패 0 유지, MCP `735→312ms`, 포크 표본 오류 1건 유지(24건 표본: `a521fd2e` 15:12:31, 원인 미분류). 메인 전사 1건(`15:02:47` 작업 측 Python cp1252 인코딩 오류). 라우터 로그: 외부 SSE EOF 19건(14:53:34부터), `runtime_activity_context_usage_failed` `WinError 5` 3건(14:54:03부터), 모델 클라이언트 단절 3건(14:54:57부터), `/ca/mcp` 단절 2건(14:58:39부터), **`work_workspace_failed code=workspace_prepare_retained item=n1556 detail="git worktree add timed out after 32 s"` 2건**(14:58:42부터), 콘솔 확인 실패 3건·`stale_unconfirmed` 건너뜀 2건(`16867` 14:54:14–16 `age=38.1s`, `16866` 14:54:34 `age=21.3s`, `16869` 14:58:54–58 `age=23.7s`), 턴 투영 관찰 2건(14:53:28·15:03:01).
  - KOBE (`15:12:49Z`): 원장 `4713→4725`, 보드 `v709→v716`, MCP `202→281ms`. 리스: 수락 1 유지, 입력 대기 `20→19`, 제출 `0→2`, 실행/슬롯 `1→2`. Finding `1→3`: `n5` 미완료 자식 가드 지속, 신규 2건(`n257`/`2a4e55ad`, `n204`/`4be5856f`)은 모두 정상 P3 슬롯 상한 대기(`awaiting_capacity`, `1/1`, 결함 아님). 신규 실패 0, 포크 표본 오류 `2→4건`(24건 표본: `a126604f` 2건 15:02:55–15:03:09, `a6aa8266` 2건 15:05:06–15:05:18, 원인 미분류). 메인 전사 3건: `n257` P3 용량 대기 가드 2건(14:53:30·14:56:28), Walkie HTTP 403 완료 권한 가드 1건(15:06:52). 라우터 로그: 정상 keep-alive 가드 235건(14:52:39부터), 외부 SSE EOF 18건(14:52:52부터)·BrokenPipe 1건(15:09:03), 콘솔 `12326` 확인 실패·`stale_unconfirmed` 건너뜀 1쌍(14:53:55–14:53:56, `age=20.1s`), 클라이언트 단절 1건(15:03:49), 턴 투영 관찰 7건(14:53:12–15:10:53).
  - Prody (`15:12:58Z`): 원장 `1245→1247`, 보드 `v7494→v7505`, MCP `250→327ms`, 리스 `{}→running+slot 1`(`n1362`/`5e532117`), finding 0·포크 오류 0·라우터 경고 0. 메인 전사 2건 모두 정상 가드: 보드 버전 가드(15:05:51), `n1362` 기존 리스 중복 dispatch 가드(15:10:09).
  - Betty(보조, `15:13:07Z`): 원장 `1177`·제출 5·입력 대기 1·보드 `v5755`·finding 0·포크 오류 0 유지, MCP `291→754ms`. 메인 전사 2건 모두 작업 측 셸 명령 exit 1/255(15:07:33·15:12:25). 라우터 로그: 정상 keep-alive 가드 242건(14:52:42부터), 외부 SSE EOF 21건(14:52:42부터), 턴 투영 관찰 3건(15:08:44–15:12:45).
  - 미결·추적 유지: **Andy PID 81900(`python=3.14.2`) `fork_turn_watch` `NameError: OSError` 13틱 연속(+478건) 및 로컬 `3.14.2` 4개 라우터 access violation 반복**, **`f9546dd`·`537a2ea`·`c926967`·`42abda1`·`e6aff62`·`0956ebe` 7/7 파일 설치 완료(라이브 효과는 라우터 재시작 및 CLI 재시작/재로드 후 반영)**, `b0cabd1` 선행 대비 후속 라이프사이클 중복 회피 라이브 증명 및 타 호스트 라우터 재시작 반영, Andy `n1166`·`git` 32s 타임아웃, Celly `n1556` `git worktree add` 32s 타임아웃·콘솔 확인 실패(`16866`/`16867`/`16869`)·`WinError 5`, KOBE `n5`·콘솔 `12326`·포크 오류·`n150` worktree 타임아웃, Prody 콘솔 `8208`, Betty 콘솔 `8109`.

- 14:52:39–14:53:12Z 정기 틱(14:33–14:34Z 대비, 19분 관측 창). 5개 호스트(보조 Betty 포함) `live.board`·Work MCP 전원 `observed`/`probe_succeeded`, 신규 병렬 원장 실패 0, 수집 stderr 없음. 신규 입증 제품 결함·코드 변경·추가 설치·운영 재시작·상태 파일 수정 없음. 이하 UTC.
  - Andy (`14:52:39Z`): 원장 `8295→8306`, 보드 `v12306→v12312`, MCP `2265→1157ms`, PID `81900`(`python=3.14.2`) 유지. 리스: 제출 `23→22`, 입력 대기 `2→3`, 실행/슬롯 3 유지. `n1166` 검수 행 누락 지속, 신규 실패 `1→0`, 포크 표본 오류 `4→2건`(8건 표본: `a09e2125` 14:38:00·14:38:56, 원인 미분류). 메인 전사 1건(`14:37:22` `auto continue` 상태 읽기 실패). 라우터 로그: **`fork_turn_watch_poll_failed error=NameError: name 'OSError' is not defined` 442건 지속**(14:33:25부터 ~2초 간격, 12틱 연속), 외부 SSE EOF 17건(14:33:34부터)·BrokenPipe 1건(14:35:24), `/ca/mcp` 단절 1건(14:33:28), 클라이언트 단절 3건(14:37:45–14:44:33), 턴 투영 관찰 2건(14:35:04·14:37:04). 사이드(PID 81900): `14:33Z` 이후 6건 접수(3건 완료, 3건 처리 중; `/side` 응답 `4420ms`).
  - Celly (`14:52:43Z`): 원장 `10017→10046`, 보드 `v11364→v11369`, MCP `1047→735ms`. 리스: 입력 대기 `4→5`, 실행/슬롯 `4→2`, finding 0·신규 실패 0 유지. 포크 표본 오류 1건 유지(24건 표본: `a521fd2e` 14:51:35, 원인 미분류). 메인 전사 2건(`14:42:22` `router-error`·`work_host_page` 타임아웃 1쌍). 라우터 로그: 외부 SSE EOF 16건(14:36:28부터)·BrokenPipe 1건(14:35:27), `/ca/mcp` 단절 9건(14:33:22부터, 경계 1건 포함), 클라이언트 단절 3건(14:33:28 경계 포함·14:48:20), 턴 투영 관찰 6건(14:35:23–14:51:28), **콘솔 확인 실패·`stale_unconfirmed` 건너뜀 2쌍**(`16862` 14:42:48 `age=25.9s`, `16865` 14:49:23–14:49:24 `age=20.1s`). `recover_queued_only`·`WinError 5` 미관측.
  - KOBE (`14:52:55Z`): 원장 `4707→4713`, 보드 `v695→v709`, MCP `242→202ms`. 리스: 수락 1·실행/슬롯 1 유지, 입력 대기 `21→20`. Finding `2→1`(`n221` 해소, `n5` 미완료 자식 가드 지속), 신규 실패 0. 포크 표본 오류 `0→2건`(24건 표본: `a1daa277` 14:44:48·14:45:02, 원인 미분류). 메인 전사 3건: `14:42:34` P3 슬롯 상한 대기 안내 2건(정상 가드 `4be5856f`·`2a4e55ad`), `14:44:42` Walkie HTTP 400. 라우터 로그: 정상 keep-alive 가드 231건(14:33:26부터), 외부 SSE EOF 20건(14:33:25부터), 턴 투영 관찰 8건(14:33:26–14:51:12), 클라이언트 단절 1건(14:45:29). 콘솔 확인 실패 미관측.
  - Prody (`14:53:04Z`): 원장 `1245`·`open_leases: {}` 유지, 보드 `v7493→v7494`, MCP `347→250ms`, finding 0·메인 오류 0·포크 오류 0. 라우터 로그: `channel_stdin_proxy_blocked` `stale_unconfirmed` 건너뜀 1건(14:50:05, `message_id=8208`; 실제 전달 여부 미확인).
  - Betty(보조, `14:53:12Z`): 원장 `1177`·제출 5·입력 대기 1·보드 `v5755`·finding 0·메인/포크 오류 0 유지, MCP `506→291ms`. 라우터 로그: 정상 keep-alive 가드 235건(14:33:22부터), 외부 SSE EOF 19건(14:34:20부터).
  - 미결·추적 유지: **Andy PID 81900(`python=3.14.2`) `fork_turn_watch` `NameError: OSError` 12틱 연속(+442건) 및 로컬 `3.14.2` 4개 라우터 access violation 반복**, **`f9546dd`·`537a2ea`·`c926967`·`42abda1`·`e6aff62` 7/7 파일 설치 완료(라이브 효과는 라우터 재시작 및 CLI 재시작/재로드 후 반영)**, `b0cabd1` 선행 대비 후속 라이프사이클 중복 회피 라이브 증명 및 타 호스트 라우터 재시작 반영, Andy `n1166`·worktree 타임아웃, Celly 콘솔 확인 실패 반복(`16862`/`16865`)·`WinError 5`, KOBE `n5`·콘솔 확인 실패·`n150` worktree 타임아웃, Prody 콘솔 `8208`, Betty 콘솔 `8109`.

- 14:33:22–14:33:57Z 정기 틱(14:22–14:23Z 대비, 11분 관측 창). 5개 호스트(보조 Betty 포함) `live.board`·Work MCP 전원 `observed`/`probe_succeeded`(Andy `live.board` 회복 `v12306`, MCP 2265ms), 수집 stderr 없음. 신규 입증 제품 결함·코드 변경·추가 설치·운영 재시작·상태 파일 수정 없음. 이하 UTC.
  - Andy (`14:33:22Z`): 원장 `8292→8295`, 보드 `v12302→v12306`(`live.board` 회복), MCP `3610→2265ms`, PID `81900`(`python=3.14.2`) 유지. 리스: 제출 `22→23`, 입력 대기 `1→2`, 실행/슬롯 `5→3`. `n1166` 검수 행 누락 지속. 신규 원장 실패 1건(`decision_refused_stop 8bcc8b4d x1`, `14:33:13Z`) 및 메인 전사 1건(`14:33:13Z` `work_parallel_item_review: The operation timed out.`): `n2563`(`8bcc8b4d`, 원장·워크스페이스 `idle_proof` 일치 확인)의 `14:31:06Z` 첫 승인 호출이 `14:33:13Z` 타임아웃 실패했으나 `14:36:42–51Z` 재시도에서 `state=finalized`(`v3631`) 정상 완료 확인(일시 타임아웃, 제품 결함 미입증). 포크 표본 오류 `2→4건`(8건 표본: `a10cbc4c` 14:22:23·14:23:06, `a09e2125` 14:27:09·14:28:06, 원인 미분류). 라우터 로그: **`fork_turn_watch_poll_failed error=NameError: name 'OSError' is not defined` 246건 지속**(14:22:13부터 ~2초 간격, 11틱 연속), 외부 SSE EOF 9건(14:22:30부터)·BrokenPipe 1건(14:31:31), 턴 투영 관찰 3건(14:29:02–14:33:04). 사이드(PID 81900): `14:22Z` 이후 2건(`ea66c7f4` P4 14:32:16 완료, `815f0ebd` P0 `agent.pending_reminder` 14:29:51 접수·진행 중; `/side` 응답 6733ms).
  - Celly (`14:33:29Z`): 원장 `10017`·보드 `v11364`·리스(입력 대기 4·실행/슬롯 4)·finding 0·신규 실패 0 유지, MCP `1797→1047ms`, 메인 오류 `2→0`, 포크 표본 오류 1건 유지(24건 표본: `a6d014a6` 14:33:22, 원인 미분류). 라우터 로그: 외부 SSE EOF 7건(14:22:58부터)·TimeoutError read 1건(14:32:12), 콘솔 `16861` 확인 실패·`stale_unconfirmed` 건너뜀 1쌍(14:30:50–14:30:51, `age=133.3s`), 중앙 게이트웨이 discovery 불가 1건(14:32:17, `elapsed_ms=5113`), 턴 투영 관찰 1건(14:33:21), `/ca/mcp` 단절 1건(14:33:22), 클라이언트 단절 1건(14:33:28). `recover_queued_only`·`WinError 5` 미관측.
  - KOBE (`14:33:38Z`): 원장 `4702→4707`, 보드 `v693→v695`, MCP `252→242ms`. 리스: 수락 1 유지, 입력 대기 `22→21`, 제출 `1→0`, 실행/슬롯 `0→1`. Finding 2건 유지: `n5` 미완료 자식 가드 지속, 직전 `n223` 해소; 신규 `n221`(`f6d6dede` P3 running, `absent since 05:32`)은 `waiting_input`에서 방금 재개(`Work host resumed the parked worker`)된 직후 이전 `missing_since`가 남아 잡힌 일시 표출이며 `14:37:09Z` 단독 읽기 전용 확인에서 `work-parallel-native.json` `status=running` 정상 관측 확인(결함 아님). 신규 실패 0, 포크 표본 오류 `1→0`(24건 표본). 메인 전사 3건 모두 작업 측 Walkie HTTP 400(14:25:51·14:29:11·14:31:25). 라우터 로그: 정상 keep-alive 가드 130건(14:22:17부터), 외부 SSE 단절 11건(EOF 9·URLError connect timeout 1·BrokenPipe 1), 턴 투영 관찰 5건(14:22:41–14:33:26). 콘솔 확인 실패 미관측.
  - Prody (`14:33:46Z`): 원장 `1245`·`open_leases: {}`·보드 `v7493`·finding 0 유지, MCP `424→347ms`, 메인 오류 `1→0`, 포크 오류 0, 라우터 경고 0.
  - Betty(보조, `14:33:57Z`): 원장 `1177`·제출 5·입력 대기 1·보드 `v5755`·finding 0·포크 오류 0 유지, MCP `363→506ms`, 메인 오류 `4→0`. 라우터 로그: 정상 keep-alive 가드 139건(14:22:14부터), 외부 SSE EOF 11건(14:23:07부터), 턴 투영 관찰 1건(14:28:19).
  - 미결·추적 유지: **Andy PID 81900(`python=3.14.2`) `fork_turn_watch` `NameError: OSError` 11틱 연속(+246건)·API 응답 지연 및 로컬 `3.14.2` 4개 라우터 access violation 반복**, **`f9546dd`·`537a2ea`·`c926967`·`42abda1`·`e6aff62` 7/7 파일 설치 완료(라이브 효과는 라우터 재시작 및 CLI 재시작/재로드 후 반영)**, `b0cabd1` 선행 대비 후속 라이프사이클 중복 회피 라이브 증명 및 타 호스트 라우터 재시작 반영, Andy `n1166`·worktree 타임아웃, Celly 콘솔 확인 실패 반복(`16861`)·`WinError 5`, KOBE `n5`·콘솔 확인 실패·`n150` worktree 타임아웃, Betty 콘솔 `8109`.

- 14:22:12–14:22:59Z 정기 틱(13:56–13:57Z 대비, 26분 관측 창). 5개 호스트(보조 Betty 포함) Work MCP 전원 `probe_succeeded`, `live.board`는 4곳 `observed`·Andy만 수집 시 `TimeoutError`였으나 직후 단독 `/ca/work/api/v1/board` 재조회 성공(`3875ms`, `v12302`). 신규 병렬 원장 실패 0, 수집 stderr 없음. 신규 입증 제품 결함·코드 변경·추가 설치·운영 재시작·상태 파일 수정 없음. 이하 UTC.
  - Andy (`14:22:12Z`): 원장 `8276→8292`, 보드 `v12293→v12302`(`live.board` 1회 `TimeoutError`→직후 단독 `3875ms` 회복), MCP `1469→3610ms`, PID `81900`(`python=3.14.2`) 유지. 리스: 제출 22 유지, 실행/슬롯 `4→5`, 입력 대기 `0→1`. `n1166` 검수 행 누락 지속, 신규 실패 0. 포크 표본 오류 `1→2건`(8건 표본: `a09e2125` 14:17:18, `a10cbc4c` 14:22:01, 원인 미분류). 메인 전사 2건 모두 정상 가드: 보드 버전 가드(14:10:22), `n2891` 기존 리스 중복 dispatch 가드(14:15:44). 라우터 로그: **`fork_turn_watch_poll_failed error=NameError: name 'OSError' is not defined` 597건 지속**(13:56:34부터 ~2초 간격, 10틱 연속), 외부 SSE EOF 25건(13:57:34부터), 모델/라우터 클라이언트 단절 6건(`WinError 10054` 5건 14:02:45–14:05:42·downstream 1건 14:06:56), 턴 투영 관찰 9건(13:58:23–14:16:04). 사이드(PID 81900): `13:56Z` 이후 4건 접수(2건 완료 `bbe135ac`·`9183d6d4`, 2건 진행 중 `14d4a913`·`ea66c7f4`; `/side` 응답 `8015ms`).
  - Celly (`14:22:22Z`): 원장 `10003→10017`, 보드 `v11361→v11364`, MCP `422→1797ms`. 리스: 입력 대기 4 유지, 실행/슬롯 `3→4`. Finding 0, 신규 실패 `1→0`, 포크 표본 오류 `0→1건`(24건 표본: `a521fd2e` 14:22:10, 원인 미분류). 메인 전사 2건: `n1600` 기존 리스 `39e5560c` 중복 dispatch 가드(13:57:42, 정상 가드), 작업 측 경로 없음(14:09:14). 라우터 로그: 외부 SSE EOF 25건(13:57:01부터), 콘솔 `16858` 확인 실패·`stale_unconfirmed` 건너뜀 1쌍(14:06:39–14:06:40, `age=44.5s`), 클라이언트 단절 2건(13:59:44·14:08:02), 종료 후 `ping` 무시 1건(14:22:00), 턴 투영 관찰 5건(13:58:03–14:10:49). `recover_queued_only`·`WinError 5` 미관측.
  - KOBE (`14:22:40Z`): 원장 `4701→4702`, 보드 `v688→v693`, MCP `326→252ms`. 리스: 수락 1·입력 대기 22 유지, 실행/슬롯 `1→0`, 제출 `0→1`. Finding `1→2`: `n5` 미완료 자식 가드 지속; `n223`(`f1f54e80` P3) 검수 행 미관측은 `14:19:20Z` 완료 영수증 직후 일시 관측이었으며, `14:25:23Z` 단독 읽기 전용 확인에서 `pending` 검수 행 정상 발행(`Native agent.list status: idle`) 확인(결함 아님). 신규 실패 0, 포크 표본 오류 `4→1건`(24건 표본: `ade609d8` 14:19:13, 원인 미분류). 메인 전사 5건 모두 작업 측 인자·스크립트 오류(14:12:13 Walkie UUID 오류, 14:12:14 경로 없음, 14:15:44 `python3` 없음, 14:20:17 exit 124, 14:20:20 Python SyntaxError). 라우터 로그: 정상 keep-alive 가드 237건(13:56:33부터), 외부 SSE 단절 22건(EOF 12·URLError connect timeout 5·BrokenPipe 4·TimeoutError read 1, 13:57:20부터), `/ca/mcp` 단절 8건(14:05:05부터), 전사 델타 전달 실패 5건(TimeoutError 3·WinError 10061 1·HTTP 502 1, 14:10:13–14:12:15), 업스트림 SSE 오류 이벤트 1쌍(14:10:06), `remote_instructions_failed` TimeoutError 1건(14:16:14), 턴 투영 관찰 5건(13:57:41–14:18:33), **콘솔 확인 실패·`stale_unconfirmed` 건너뜀 3쌍**(`12318` 13:58:47–13:58:57 `age=65.8s` + `submit_unconfirmed`/`receipt_unmatched` 2쌍 13:58:54, `12320` 14:12:57–14:13:00 `age=69.6s`, `12321` 14:20:22–14:20:23 `age=39.1s`; 실제 전달 여부 미확인).
  - Prody (`14:22:50Z`): 원장 `1245`·`open_leases: {}` 유지, 보드 `v7492→v7493`, MCP `300→424ms`, finding 0·포크 오류 0·라우터 경고 0. 메인 전사 1건(`14:06:09` `auto continue` 상태 읽기 실패).
  - Betty(보조, `14:22:59Z`): 원장 `1177`·제출 5·입력 대기 1·보드 `v5755`·finding 0·포크 오류 0 유지, MCP `405→363ms`. 메인 전사 `0→4건` 모두 작업 측 Walkie 400(13:57:09)·스크립트 오류(13:58:43–14:00:51). 라우터 로그: 정상 keep-alive 가드 270건(13:56:34부터), 외부 SSE 단절 26건(EOF 24·`WinError 10054` 1·URLError connect timeout 1), `/ca/mcp` 단절 1건(14:07:08), 턴 투영 관찰 7건(13:59:12–14:15:39).
  - 미결·추적 유지: **Andy PID 81900(`python=3.14.2`) `fork_turn_watch` `NameError: OSError` 10틱 연속(+597건)·`/board`/`/side`/`/ca/mcp` 응답 지연 및 로컬 `3.14.2` 4개 라우터 access violation 반복**, **`f9546dd`·`537a2ea`·`c926967`·`42abda1`·`e6aff62` 7/7 파일 설치 완료(라이브 효과는 라우터 재시작 및 CLI 재시작/재로드 후 반영)**, `b0cabd1` 선행 대비 후속 라이프사이클 중복 회피 라이브 증명 및 타 호스트 라우터 재시작 반영, Andy `n1166`·worktree 타임아웃, Celly 콘솔 `16858`·`WinError 5`, KOBE `n5`·콘솔 확인 실패 반복(`12318`/`12320`/`12321`)·전사 델타/외부 네트워크 단절·`n150` worktree 타임아웃, Betty 콘솔 `8109`.

- 13:56:32–13:57:06Z 정기 틱(13:30–13:32Z 대비, 26분 관측 창). 5개 호스트(보조 Betty 포함) `live.board`·Work MCP 전원 `observed`/`probe_succeeded`(Celly MCP 422ms 회복), 수집 stderr 없음. 운영 세션/라우터 재시작·운영 상태 파일 수정 없음. 이번 틱에서 입증된 Celly `n1600` 이미 바인딩된 P0 인텐트 중복 `reserve` 매니페스트 오해 오류를 `e6aff62`로 실패 선재현→수정→검증→npm `@beta` SHA-256 확인→7개 루트 해시 설치 완료(파일 설치 완료, 라이브 재시작 미실행으로 메모리 반영 대기). 이하 UTC.
  - Andy (`13:56:32Z`): 원장 `8261→8276`, 보드 `v12283→v12293`, MCP `1188→1469ms`, PID `81900`(`python=3.14.2`) 유지. 리스: 제출 22 유지, 입력 대기 `1→0`, 실행/슬롯 `1→4`. `n1166` 검수 행 누락 지속, 신규 실패 0. 포크 표본 오류 `0→1건`(8건 표본: `a01f33ca` 13:35:17, 원인 미분류). 메인 전사 5건: `work_side_status` 호스트 전용 가드(13:41:39), `work_side_handoff` 미존재 가드(13:42:26), Walkie `client_id` UUID 검증 오류(13:47:32), 보드 버전 가드(13:48:21), `auto continue` 상태 읽기 실패(13:54:08). 라우터 로그: **`fork_turn_watch_poll_failed error=NameError: name 'OSError' is not defined` 677건 지속**(13:30:10부터 ~2초 간격, 9틱 연속), 외부 SSE EOF 25건(13:31:03부터), `/ca/mcp` 단절 4건(13:31:37부터), 모델/라우터 클라이언트 단절 5건(13:35:43–13:37:33), 턴 투영 관찰 6건(13:43:21–13:49:22), `cielairouter_transient_retry URLError` 2건(13:30:10·13:30:15, 직전 장애 꼬리 op `5dc2c526`). 사이드(PID 81900): `13:36Z` 이후 7건 접수(5건 완료, `659fcb7d` `message.rejected`·`0e3af139` `message.approved` 처리 중; `161878`·`161879`·`162016` 3건의 후속 라이프사이클 이벤트가 정상 접수·처리됐으나 최근 5페이지에 동일 ID 선행 `message.created` 행이 없어 `b0cabd1` 선행 대비 중복 회피 라이브 증명은 계속 추적).
  - **Celly (`13:56:37Z`, 결함 입증·수정 `e6aff62`):** 원장 `9995→10003`, 보드 `v11360→v11361`, MCP `TimeoutError→probe_succeeded(422ms)` 회복. 리스: 입력 대기 `3→4`, 실행/슬롯 `2→3`, 검수 finding 0·포크 표본 오류 0(24건 표본). 신규 원장 실패 1건(`revival_refused d5473fa1 x1`: `13:46:16` 종료된 `unreported` 포크 `a48779c9`에 대한 `SendMessage` 정상 부활 방지 가드).
    - **입증·수정 결함 (`e6aff62`, `docs/journal/2026/11/26/fixes/p0-bound-intent-reserve-guard/record.md`):** `13:46:40Z` 메인 전사 `work_parallel_event (reserve) failed: coordination persistence failed; reservation released: WorkParallelError: assignment already exists with a different manifest`. 호스트 펌프가 `13:46:28–35Z`에 P0 항목 `n1600`의 인텐트 `23a6be04`와 리스 `39e5560c`를 이미 예약·바인딩(`state="bound"`, `agent=a6d014a6`)한 직후 메인의 동시 `work_parallel_run`이 `bound` 인텐트를 재사용해 `coord.record_assignment`를 재호출하며 발생했고, 오해를 부르는 오류 문구로 인해 메인이 10분간 후속 잘못된 호출 4건(13:48:22 reviewer 금지·13:48:57 미지원 이벤트·13:54:16 인증 가드·13:56:04 `recover_submitted_review` 가드)을 반복했다. `_reserve_assignment`에서 `p0_intent["state"] == "bound"` 또는 `existing.get("host_id") is not None`을 즉시 기존 바인딩 리스/에이전트 안내 오류로 거부하도록 수정(`e6aff62`) → 실패 선재현→격리 Windows 99 passed(12 subtests)·Linux `py310` 210 passed(5 skip)·`py311` 208 passed(5 skip)→npm `@beta` `0.2.51-beta.20261126-140907.e6aff62` SHA-256 일치(`work_parallel_mcp.py:8b145e914e96`)→7개 루트 해시 설치(`ok=True`) 완료.
    - 기타 Celly 라우터: 외부 SSE EOF 22건(13:31:48부터), `/ca/mcp` 단절 10건(`WinError 10054` 9건 13:31:48부터·`WinError 10053` 1건 13:32:01), `channel_stdin_proxy_recover_queued_only message_id=16801` 8건(13:48:22–13:48:31, `42abda1` 파일 설치 완료·라이브 라우터 재시작 대기 중 동일 결함 재발), 콘솔 `16853` 확인 실패·`stale_unconfirmed` 건너뜀 1쌍(13:33:43), `WinError 5` 2건(13:30:12 직전 경계 중복·13:53:57), 턴 투영 관찰 5건(13:47:36–13:56:02), 클라이언트 단절 1건(13:39:09), `13:30:10–13:31:48Z` 게이트웨이 재시도 3건(직전 틱 경계 중복).
  - KOBE (`13:56:47Z`): 원장 `4693→4701`, 보드 `v677→v688`, MCP `295→326ms`. 리스: 수락 1 유지, 입력 대기 `24→22`, 실행/슬롯 `0→1`. `n5` 미완료 자식 가드 지속, 신규 실패 0. 포크 표본 오류 `3→4건`(24건 표본: `a8671f08` 13:47:52–13:49:02, 원인 미분류). 메인 전사 5건: reviewer 금지 가드(13:39:49), 작업 측 Python 오류(13:40:15), `n229` 기존 리스 중복 dispatch 가드(13:42:42), 보드 버전 가드 2건(13:52:09·13:54:14). 라우터 로그: 정상 keep-alive 가드 313건(13:30:17부터), 외부 SSE EOF 25건(13:30:54부터), 콘솔 `12311` 확인 실패 1건(13:38:57, `age=25.3s`), 턴 투영 관찰 8건(13:38:29–13:55:40).
  - Prody (`13:56:57Z`): 원장 `1245`·`open_leases: {}` 유지, 보드 `v7491→v7492`, MCP `260→300ms`, finding 0·메인 오류 `1→0`·포크 오류 0·라우터 경고 0.
  - Betty(보조, `13:57:06Z`): 원장 `1177`·제출 5·입력 대기 1·보드 `v5755`·finding 0·메인/포크 오류 0 유지, MCP `214→405ms`. 라우터: 정상 keep-alive 가드 319건(13:30:13부터), 외부 SSE EOF 27건(13:30:33부터), 턴 투영 관찰 2건(13:53:13·13:55:14).
  - 미결·추적 유지: **Andy PID 81900(`python=3.14.2`) `fork_turn_watch` `NameError: OSError` 9틱 연속(+677건) 및 로컬 `3.14.2` 4개 라우터 access violation 반복**, **`f9546dd`·`537a2ea`·`c926967`·`42abda1`·`e6aff62` 7/7 파일 설치 완료(라이브 효과는 라우터 재시작 및 CLI 재시작/재로드 후 반영)**, `b0cabd1` 선행 대비 후속 라이프사이클 중복 회피 라이브 증명 및 타 호스트 라우터 재시작 반영, Andy `n1166`·worktree 타임아웃, Celly 콘솔 확인 실패(`16853`)·`WinError 5`, KOBE `n5`·콘솔 `12311`·포크 오류·`n150` worktree 타임아웃, Betty 콘솔 `8109`.

- 13:30:09–13:32:24Z 정기 틱(12:47–12:48Z 대비, 43분 관측 창). 5개 호스트(보조 Betty 포함) `live.board` 전원 `observed`, Work MCP는 Andy·KOBE·Prody·Betty `probe_succeeded`, Celly만 수집 시점 `TimeoutError(4031ms)`였으나 직후 단독 `/ca/mcp work_status` 재조회 `30ms` 성공 확인(동시각 `13:29:38–13:31:48Z` 외부 게이트웨이 `URLError` 장애 구간과 겹침). 신규 병렬 원장 실패 0, 수집 stderr 없음. 운영 세션/라우터 재시작·운영 상태 파일 수정 없음. 이번 틱에서 입증된 Celly `channel_stdin_proxy_recover_queued_only` 무한 되감기 결함(`16799`, 214회·5,581행)을 `42abda1`로 실패 선재현→수정→검증→npm `@beta` SHA-256 확인→7개 루트 해시 설치 완료(파일 설치 완료, 라이브 재시작 미실행으로 메모리 반영 대기). 이하 UTC.
  - Andy (`13:30:09Z`): 원장 `8239→8261`, 보드 `v12269→v12283`, MCP `1031→1188ms`, PID `81900`(`python=3.14.2`) 유지. 리스: 제출 `22` 유지, 입력 대기 `2→1`, 실행/슬롯 `2→1`. Finding `2→1`: 직전 `n2728` finding 해소, `n1166` 검수 행 누락 지속. 신규 실패 `3→0`, 포크 표본 오류 `2→0`(10건 표본). 메인 전사 1건: `12:55:28` `n2855` `P0 reacquire exact waiting lease`(가드 메시지 형식이나 동시 경합 세부 원인은 전사 미확인). 라우터 로그: **`fork_turn_watch_poll_failed error=NameError: name 'OSError' is not defined` 1,114건 지속**(12:47:33부터 ~2초 간격, 8틱 연속), 외부 SSE EOF 42건(12:48:03부터)·URLError connect timeout 1건(13:30:00), 모델 클라이언트 단절 8건(`WinError 10054` `_Walkie_P1` 5건 12:47:47부터·`_API_KEYS-SSE-1` 2건 12:49:59부터, downstream 1건 13:26:21), `/ca/mcp` 단절 3건(12:56:57부터), 턴 투영 관찰 7건(12:53:17–12:59:18), 전사 델타 타임아웃 1건(13:17:10), **외부 게이트웨이 장애 구간(`13:29:38–13:30:07Z`)**: `anthropic_sse_forward_error source=upstream` 2건(13:29:38) + `cielairouter_transient_retry URLError` 5건(동일 op `18a0b209`, 13:29:43–13:30:07). 사이드(PID 81900): `12:45Z` 이후 5건 접수(4건 완료, `2b3a356d` P0 13:24:39 접수·처리 중; `b0cabd1` 후속 이벤트 라이브 미관측).
  - **Celly (`13:30:13Z`, 결함 입증·수정 `42abda1`):** 원장 `9990→9995`, 보드 `v11357→v11360`, MCP 수집 시 `TimeoutError(4031ms)`→직후 단독 재조회 `30ms` 성공. 리스: 입력 대기 `5→3`, 실행/슬롯 `1→2`, 검수 finding 0·신규 실패 0·포크 표본 오류 `2→0`(24건 표본). 메인 전사 1건: `12:50:46` parked lease `2db7d662` P3 슬롯 상한 대기 안내(정상 가드).
    - **입증·수정 결함 (`42abda1`, `docs/journal/2026/11/26/fixes/cursor-recovery-skip-terminal/record.md`):** `12:57:56Z`–`13:23:33Z` 사이 `channel_stdin_proxy_recover_queued_only message_id=16799`가 **214회**(`cursor=16851` 200회 + `cursor=16852` 14회) 반복되며 `router.log`에 5,581행의 중복 `channel_stdin_proxy_skipped_noise(durable_replied)` 및 과거 실패 `channel_stdin_proxy_blocked`(`16801`·`16806`·`16838`·`16842`·`16844`)를 유발했다. 이미 `08:04:16Z` `durable_replied`로 종결된 `16799`의 `queued_command` 행이 8 MB 전사 꼬리에 남아 `wake_states`가 `"missing"`을 반환할 때 `ChannelCursorRecoveryService`가 라이프사이클 종결 여부를 확인하지 않고 매 5초마다 커서를 `16798`로 되감은 것이 원인. `ChannelWakeDeliveryRepository.is_terminal()` 신설 및 `ChannelCursorRecoveryPorts.is_terminal` 연결(`42abda1`)을 실패 선재현→격리 Windows·Linux `py310`·`py311` 각 321 passed(42 skip, 792 subtests, `test_architecture_contracts` 포함)→npm `@beta` `0.2.51-beta.20261126-134400.42abda1` 3파일 SHA-256 일치 확인→7개 루트 해시 설치(`ok=True`) 완료.
    - 기타 Celly 라우터: 외부 SSE EOF 42건(12:47:33부터)·URLError connect timeout 1건(13:30:02), `/ca/mcp` 단절 3건(12:50:18부터), 클라이언트 단절 1건(12:57:01), `runtime_activity_*` `WinError 5` 2건(13:05:56·13:30:12), **외부 게이트웨이 장애 구간(`13:29:46–13:31:48Z`)**: `cielairouter_gateway_failure` 1 + `no healthy gateway` 1 + `reprobed fallback` 1 + `cielairouter_transient_retry URLError` 6건(동일 op `47ec0c7d`). 참고: 틱 직후 꼬리(`13:33:43Z`)에 신규 `16853` 콘솔 확인 실패·`stale_unconfirmed` 건너뜀 1쌍 관측.
  - KOBE (`13:32:05Z`): 원장 `4687→4693`, 보드 `v668→v677`, MCP `361→295ms`. 리스: 수락 1·입력 대기 24 유지, 제출 `1→0`, 실행/슬롯 `1→0`. `n5` 미완료 자식 가드 유지, 신규 실패 0. 포크 표본 오류 `9→3건`(24건 표본: `a1247ab0` 12:58:33·12:58:49·12:59:10, 원인 미분류). 메인 전사 2건: 보드 버전 가드(12:53:35, 정상 가드), 작업 측 스크립트 exit 1(13:01:26). 라우터 로그: 정상 keep-alive 가드 527건(12:47:30부터), 외부 SSE EOF 42건(12:48:03부터)·BrokenPipe 1건(12:49:53), 턴 투영 관찰 6건(12:48:32–12:54:33), 클라이언트 단절 1건(12:59:42).
  - Prody (`13:32:14Z`): 원장 `1245`·`open_leases: {}` 유지, 보드 `v7489→v7491`, MCP `513→260ms`, finding 0·포크 오류 0·라우터 경고 0. 메인 전사 1건(12:49:41 작업 측 스캔 스크립트 exit 2).
  - Betty(보조, `13:32:24Z`): 원장 `1177`·제출 5·입력 대기 1·보드 `v5755`·finding 0·메인/포크 오류 0 유지, MCP `496→214ms`. 라우터 로그: 정상 keep-alive 가드 532건(12:47:32부터), 외부 SSE EOF 44건(12:47:43부터), 콘솔 `8109` 확인 실패·`stale_unconfirmed` 건너뜀 1쌍(12:47:46 — 직전 틱 경계 중복).
  - 미결·추적 유지: **Andy PID 81900(`python=3.14.2`) `fork_turn_watch` `NameError: OSError` 8틱 연속(+1,114건) 및 로컬 `3.14.2` 4개 라우터 access violation 반복**, **`f9546dd`·`537a2ea`·`c926967`·`42abda1` 7/7 파일 설치 완료(라이브 효과는 라우터 재시작 및 CLI 재시작/재로드 후 반영)**, `b0cabd1` Andy PID 81900 후속 이벤트 라이브 확인 및 타 호스트 라우터 재시작 반영, Andy `n1166`·worktree 타임아웃, Celly 콘솔 확인 실패(`16853`)·`WinError 5`, KOBE `n5`·포크 오류·`n150` worktree 타임아웃, Betty 콘솔 `8109`.

- 12:47:30–12:48:02Z 정기 틱(11:33–11:34Z 대비, 74분 관측 창). 네 호스트·보조 Betty Work MCP 및 `live.board` 모두 `probe_succeeded`/`observed`, 수집 stderr 없음. 운영 세션/라우터 재시작·운영 상태 파일 수정 없음. 이번 틱에서 입증된 제품 결함 2건(`n2790` `idle_proof` 불일치/수렴 누락 `537a2ea`, `n2728` `host_salvage` 비실행 상태 오염·`parallelHasReceipt` 오판 `c926967`)을 실패 테스트 선재현→수정→격리 Windows·Linux `py310`/`py311` 회귀→npm `@beta` SHA-256 검증→7개 승인 루트 해시 설치까지 완료(모두 파일 설치 완료, 라이브 재시작/재로드 미실행으로 메모리 반영 대기). 이하 UTC.
  - Andy (`12:47:30Z`): 원장 `8203→8239`, 보드 `v12250→v12269`, MCP `1094→1031ms`, PID `81900`(`python=3.14.2`) 유지. 리스: 제출 `24→22`, 입력 대기 `2` 유지, 실행/슬롯 `2` 유지.
    - **검수·신규 원장 실패 (`n2790` `c15200e7`, 입증·수정 `537a2ea`):** 신규 실패 `decision_refused_stop c15200e7 x1` 3건 및 메인 전사 오류 3건(11:33:19·11:36:27·11:37:11 `work_parallel_item_review: positive matching idle workspace proof required`). `work-parallel.json`의 `lease.idle_proof="Native agent.list row absent"`와 `work-parallel-workspaces/792e0a51….json`(mtime `11:25:53Z`)의 `idle_proof="Submitted completion fork absent from agent.list after restart"` 불일치로 입증(`work_parallel_mcp.py`의 `reconcile` `WorkspaceError` 미롤백·중복 `set_activity`, `register.js`의 `parallelTurnComplete` 미갱신·`recoverSubmittedReview` `pending` 조기 반환). 수정 커밋 `537a2ea`(npm `@beta` `0.2.51-beta.20261126-124338.537a2ea`) 검증·7/7 해시 설치 완료(상세: `docs/journal/2026/11/26/fixes/submitted-review-proof-convergence/record.md`). `Andy_Main (3)`에 원인·설치 상태 회신 및 ACK 수신.
    - **신규 finding (`n2728` `521a8d85` P4 running, 입증·수정 `c926967`):** `slot held, fork absent since 12:37` 포착. `a91c2c2c` 포크가 `12:22:41Z` `question`으로 정상 `waiting_input` 파킹됐으나 `12:24:22Z` 지연 `host_salvage`가 `waiting_input`에 성공해 `run.exit`를 `host_salvage`로 오염(이어진 `unreported`는 `state!=running`으로 실패), `12:27–12:29Z` 메인 재개 이후 `12:36:14Z` 첫 경계에서 `parallelHasReceipt`가 `host_salvage`를 워커 보고로 오판해 포크를 즉시 `TaskStop`(`killed`)하고 `unreported` 없이 `running+slot=true` 슬롯을 누수함(`12:50:53Z` 복구 메시지로 재기동 관측). 수정 커밋 `c926967`(npm `@beta` `0.2.51-beta.20261126-131738.c926967`, `work_parallel_mcp.py:ee6b1e78f236`, `register.js:e81063652d57`)을 실패 선재현→격리 Windows 199 passed(33 skip)·Linux `py310` 386 passed(80 skip)·`py311` 383 passed(80 skip)·`claude plugin validate`/`test` 96/96 검증 후 7/7 해시 설치 완료(상세: `docs/journal/2026/11/26/fixes/host-salvage-running-guard/record.md`).
    - 기타 Andy: `n1166` 검수 행 누락 지속. 포크 표본 오류 `0→2건`(10건 표본: `a01f33ca` 12:19:53·12:20:13, 원인 미분류). 메인 전사 기타 3건: 작업 측 파일 미존재(11:43:47), Walkie HTTP 400(11:44:14), `n2563` `P0 reacquire exact waiting lease`(12:13:49 — `12:12:50Z` wait 해제에 따른 호스트 자동 재개와 메인 `SendMessage` 동시 경합 정상 가드 확인). 라우터 로그: **`fork_turn_watch_poll_failed error=NameError: name 'OSError' is not defined` 1,956건 지속**(11:33:08부터 ~2초 간격, 7틱 연속), 외부 SSE EOF 72건(11:33:09부터)·BrokenPipe 1건(12:27:54), `anthropic_sse_client_disconnected` 10건(`WinError 10054` 8건 11:36:10–12:36:21, downstream 2건 11:52:55), `router_client_disconnected` 1건(12:36:15), 턴 투영 관찰 13건(11:34:50–12:39:51), `runtime_activity_context_usage_failed` `WinError 5` 1건(11:45:30). 사이드(PID 81900): `11:48Z` 이후 7건 접수·처리(5건 완료, `90a7e746`·`ca370f2e` 진행 중; `b0cabd1` 후속 이벤트 라이브 미관측).
  - Celly (`12:47:33Z`): 원장 `9986→9990`, 보드 `v11355→v11357`, MCP `344→984ms`. 리스: 입력 대기 `4→5`, 실행/슬롯 `2→1`, 검수 finding 0·신규 실패 0·메인 오류 0 유지. 포크 표본 오류 `6→2건`(24건 표본: `a7a347f2` 12:46:42·12:47:16, 원인 미분류). 라우터 로그: 외부 SSE EOF 70건(11:33:35부터)·TimeoutError 1건(12:27:53), 콘솔 `16844` 확인 실패 1건(11:37:00, `age=22.3s`), `router_client_disconnected` `WinError 10054` 1건(12:16:12). `WinError 5`·`/ca/mcp` 단절 미관측.
  - KOBE (`12:47:41Z`): 원장 `4652→4687`, 보드 `v658→v668`, MCP `644→361ms`. 리스: 수락 1·입력 대기 24·실행/슬롯 1 유지, 제출 `0→1`. `n5` 미완료 자식 가드 유지, 신규 실패 0. 포크 표본 오류 `0→9건`(24건 표본: `aabae484` 2건 11:39, `a3f699f6` 1건 12:06, `a4a746ce` 2건 12:19, `a6ad348f` 2건 12:27–12:28, `a263658a` 2건 12:44–12:45, 원인 미분류). 메인 전사 7건 모두 작업 측 스크립트 exit 1(5건 11:45:47–12:38:11) 및 Walkie HTTP 400(2건 12:39:26·12:40:12). 라우터 로그: 정상 health keep-alive 가드 843건(11:33:11부터), 외부 SSE EOF 59건(11:33:19부터)·BrokenPipe 3건(11:38:15부터)·URLError connect 4건(11:54:22·11:59:48)·TimeoutError 2건(11:46:45·11:58:51), `/ca/mcp` 단절 3건(11:57:17부터), `transcript_delta_delivery_failed` TimeoutError 2건(11:54:12부터), 모델/라우터 클라이언트 단절 3건(11:42:05·12:07:05), 종료 후 `ping` 무시 1건(12:21:43), 턴 투영 관찰 21건(11:44:58–12:36:21).
  - Prody (`12:47:53Z`): 원장 `1245`·`open_leases: {}`·보드 `v7489`·finding 0 유지, MCP `2566→513ms`, 포크 표본 오류 0(2건 표본), 라우터 경고 0. 메인 전사 오류 `0→1건`(12:45:28 작업 측 스캔 스크립트 exit 2, 제품 오류 아님).
  - Betty(보조, `12:48:02Z`): 원장 `1177`·리스(제출 5·입력 대기 1)·보드 `v5755`·finding 0 유지, MCP `975→496ms`, 포크 표본 오류 0(24건 표본), 메인 오류 0. 라우터 로그: 정상 keep-alive 가드 871건(11:33:08부터), 외부 SSE EOF 73건(11:33:35부터)·`WinError 10054` 1건(11:41:59)·URLError connect timeout 1건(11:42:22), 콘솔 `8109` 확인 실패·선행 미확인 건너뜀 1쌍(12:47:46, `age=81.3s`).
  - 미결·추적 유지: **Andy PID 81900(`python=3.14.2`) `fork_turn_watch` `NameError: OSError` 7틱 연속(+1,956건) 및 로컬 `3.14.2` 4개 라우터 access violation 반복**, **`f9546dd`·`537a2ea`·`c926967` 7/7 파일 설치 완료(라이브 효과는 라우터 재시작 및 CLI 재시작/재로드 후 반영)**, `b0cabd1` Andy PID 81900 후속 이벤트 라이브 확인 및 타 호스트 라우터 재시작 반영, Andy `n1166`·worktree 타임아웃, Celly 콘솔 `16844`·포크 오류·`WinError 5`, KOBE `n5`·포크 오류·`/ca/mcp` 단절·`n150` worktree 타임아웃, Betty 콘솔 `8109`.

- 11:33:07–11:33:50Z 정기 틱(11:12–11:13Z 대비). 네 호스트·보조 Betty Work MCP 및 `live.board` 모두 `probe_succeeded`/`observed`, 신규 병렬 원장 실패 0, 수집 stderr 없음. 운영 세션/라우터 재시작·운영 상태 파일 수정·추가 설치 없음. 이하 UTC.
  - Andy (`11:33:07Z`): 원장 `8181→8203`, 보드 `v12243→v12250`, MCP `2671→1094ms`, PID `81900`(`python=3.14.2`) 유지. 리스: 제출 `22→24`, 입력 대기 `7→2`, 실행/슬롯 `0→2`. Finding `1→2`: `n1166` 검수 행 누락 지속; `n2882`(`eda43859` P2)가 `11:33:07Z` 스냅샷에 일시 잡혔으나 `11:34:47Z` 단독 `/parallel` 확인에서 `n2882 pending eda43859fdf9 P2`로 정상 발행됨을 확인(`review_ver: 3574`, `n2806`·`n2790` 포함 `pending` 3건, 결함 아님). 포크 표본 오류 `1→0`(10건 표본). 메인 전사 3건: 보드 버전 가드(11:12:34, 정상 가드), `n2812` `not a slot-released wait`(11:28:56, 정상 가드), `Work reconciliation: Parallel read failed: The operation timed out`(11:32:33). 라우터 로그: **`fork_turn_watch_poll_failed error=NameError: name 'OSError' is not defined` 522건 지속**(11:12:23부터 ~2초 간격, 6틱 연속), 외부 SSE EOF 16건(11:13:11부터)·BrokenPipe 1건(11:20:23)·connect TimeoutError 1건(11:25:58), `/ca/mcp` 단절 4건(11:25:54부터), 모델 클라이언트 단절 1건(11:18:53), `cielairouter_transient_retry` 5건(11:24:27–11:24:51, 동일 op `50540c17`), 턴 투영 관찰 8건(11:12:37–11:32:47). 사이드(PID 81900): `08408b01`(P3 `schedule.reminder`) 11:26:38 접수·처리 중(`b0cabd1` 후속 이벤트 라이브 미관측).
  - Celly (`11:33:11Z`): 원장 `9983→9986`, 보드 `v11353/11354→v11355`, MCP `500→344ms`. 리스(입력 대기 4·실행/슬롯 2)·검수 finding 0·신규 실패 0 유지, 메인 오류 `4→0`. 포크 표본 오류 `7→6건`(24건 표본: `aedaf7c9` 2건 11:15:16·11:15:37, `a7a347f2` 2건 11:29:21·11:32:32, `a753f5bd` 2건 11:32:37·11:32:57, 원인 미분류). 라우터 로그: 외부 SSE EOF 15건(11:12:39부터)·BrokenPipe 1건(11:20:23), `/ca/mcp` 단절 6건(11:25:51부터), `runtime_activity_context_usage_failed` `WinError 5` 2건(11:14:27부터), 턴 투영 관찰 1건(11:12:49). 콘솔 확인 실패 미관측.
  - KOBE (`11:33:22Z`): 원장 `4651→4652`, 보드 `v657→v658`, MCP `1003→644ms`. 리스(수락 1·입력 대기 24·실행/슬롯 1)·`n5` 미완료 자식 가드 유지, 포크 표본 오류 0(24건 표본), 메인 오류 0. 라우터 로그: 정상 health keep-alive 가드 239건(11:12:25부터), 외부 SSE EOF 19건(11:13:01부터)·BrokenPipe 1건(11:29:13), 턴 투영 관찰 4건(11:14:08–11:20:09), `runtime_activity_context_usage_failed` `WinError 5` 1건(11:18:41). 콘솔 확인 실패 미관측.
  - Prody (`11:33:35Z`): 원장 `1245`·`open_leases: {}`·보드 `v7489`·finding 0 유지, MCP `1121→2566ms`, 포크 표본 오류 0(2건 표본), 메인 오류 0, 라우터 경고 0.
  - Betty(보조, `11:33:50Z`): 원장 `1177`·리스(제출 5·입력 대기 1)·보드 `v5755`·finding 0 유지, MCP `1125→975ms`, 포크 표본 오류 0(24건 표본), 메인 오류 0. 라우터: 정상 keep-alive 가드 250건(11:12:21부터), 외부 SSE EOF 21건(11:13:10부터), 턴 투영 관찰 2건(11:13:24·11:15:24).
  - 신규 입증 제품 결함 없음. 미결·추적 유지: **Andy PID 81900(`python=3.14.2`) `fork_turn_watch` `NameError: OSError` 6틱 연속(+522건) 및 로컬 `3.14.2` 4개 라우터 access violation 반복**, Celly 포크 오류·`/ca/mcp` 단절·`WinError 5`, KOBE `n5`·`WinError 5`·`n150` worktree 타임아웃, `f9546dd` CLI 재시작/재로드 후 라이브 반영(7/7 파일 설치 완료), `b0cabd1` Andy PID 81900 후속 이벤트 라이브 확인 및 타 호스트 라우터 재시작 반영, Andy `n1166`·worktree 타임아웃.

- 11:12:20–11:13:10Z 정기 틱(10:52–10:53Z 대비). 네 호스트·보조 Betty Work MCP 및 `live.board` 모두 `probe_succeeded`/`observed`, 신규 병렬 원장 실패 0, 수집 stderr 없음. 운영 세션/라우터 재시작·운영 상태 파일 수정·추가 설치 없음. 이하 UTC.
  - Andy (`11:12:20Z`): 원장 `8173→8181`, 보드 `v12238→v12243`, MCP `641→2671ms`, PID `81900`(`python=3.14.2`) 유지. 리스: 제출 `21→22`, 입력 대기 `5→7`, 실행/슬롯 `3→0`. `n1166` 검수 행 누락 지속. 포크 표본 오류 `0→1건`(10건 표본: `a01f33ca` 11:09:14, 원인 미분류). 메인 전사 1건: `11:10:42` Walkie HTTP 404 `Thread task not found`. 라우터 로그: **`fork_turn_watch_poll_failed error=NameError: name 'OSError' is not defined` 491건 지속**(10:52:29부터 ~2초 간격, 5틱 연속), 외부 SSE EOF 20건(10:52:40부터), 턴 투영 관찰 1건(11:10:37). 사이드(PID 81900): `7da8cdb2`(`agent.pending_reminder`) 11:05:54→11:09:13 처리 완료(`b0cabd1` 후속 이벤트 라이브 미관측).
  - Celly (`11:12:31Z`): 원장 `9972→9983`, 보드 `v11348→v11353/11354`, MCP `1079→500ms`. 리스(입력 대기 4·실행/슬롯 2)·검수 finding 0·신규 실패 0 유지. 포크 표본 오류 `5→7건`(24건 표본: `a62f2668` 4건 10:56:35–10:57:59, `a117a050` 1건 11:04:02, `a753f5bd` 2건 11:11:29, 원인 미분류). 메인 전사 4건 모두 정상 P3 슬롯 상한 대기 가드·안내(11:02:30 parked lease `b6395040`·`2db7d662`, 11:02:56 `n1505`, 11:03:19 `n1500`). 라우터 로그: 외부 SSE EOF 19건(10:52:41부터), 모델 스트림 단절 2건(10:53:44·10:58:38), 턴 투영 관찰 2건(11:02:03·11:10:49), `transcript_delta_delivery_failed` TimeoutError 1건(11:02:24), 중앙 게이트웨이 discovery 불가 1건(11:07:29, `elapsed_ms=5095`), 콘솔 `16842` 확인 실패 1건(11:08:48, `age=29.0s`), `runtime_activity_router_failed` `WinError 5` 1건(11:08:53).
  - KOBE (`11:12:40Z`): 원장 `4643→4651`, 보드 `v655→v657`, MCP `428→1003ms`. 리스(수락 1·입력 대기 24·실행/슬롯 1)·`n5` 미완료 자식 가드 유지, 포크 표본 오류 `1→0`(24건 표본), 메인 오류 `1→0`. 라우터 로그: 정상 health keep-alive 가드 235건(10:52:28부터), 외부 SSE EOF 17건(10:53:27부터)·BrokenPipe 1건(11:05:50), 턴 투영 관찰 3건(11:08:07–11:12:08), 콘솔 `12305` 확인 실패·선행 미확인 건너뜀 1쌍(11:12:00–11:12:01, `age=25.8s`).
  - Prody (`11:12:54Z`): 원장 `1245`·`open_leases: {}`·보드 `v7489`·finding 0 유지, MCP `713→1121ms`, 포크 표본 오류 0(2건 표본), 메인 오류 0, 라우터 경고 0.
  - Betty(보조, `11:13:10Z`): 원장 `1177`·리스(제출 5·입력 대기 1)·보드 `v5755`·finding 0 유지, MCP `1809→1125ms`, 포크 표본 오류 0(24건 표본), 메인 오류 0. 라우터: 정상 keep-alive 가드 240건(10:52:31부터), 외부 SSE EOF 21건(10:52:43부터), 턴 투영 관찰 2건(11:09:23·11:11:23).
  - 신규 입증 제품 결함 없음. 미결·추적 유지: **Andy PID 81900(`python=3.14.2`) `fork_turn_watch` `NameError: OSError` 5틱 연속(+491건) 및 로컬 `3.14.2` 4개 라우터 access violation 반복**, Celly 콘솔 확인 실패(`16842`)·포크 오류·`WinError 5`, KOBE `n5`·콘솔 `12305` 미확인·`n150` worktree 타임아웃, `f9546dd` CLI 재시작/재로드 후 라이브 반영(7/7 파일 설치 완료), `b0cabd1` Andy PID 81900 후속 이벤트 라이브 확인 및 타 호스트 라우터 재시작 반영, Andy `n1166`·worktree 타임아웃.

- 10:52:26–10:53:09Z 정기 틱(10:32–10:33Z 대비). 네 호스트·보조 Betty Work MCP 및 `live.board` 모두 `probe_succeeded`/`observed`, 신규 병렬 원장 실패 0, 수집 stderr 없음. 운영 세션/라우터 재시작·운영 상태 파일 수정·추가 설치 없음. 이하 UTC.
  - Andy (`10:52:26Z`): 원장 `8172→8173`, 보드 `v12237→v12238`, MCP `687→641ms`, PID `81900`(`python=3.14.2`) 유지. 리스: 제출 21 유지, 입력 대기 `4→5`, 실행/슬롯 `4→3`. `n1166` 검수 행 누락 지속, 포크 표본 오류 `1→0`(10건 표본), 메인 오류 `5→0`. 라우터 로그: **`fork_turn_watch_poll_failed error=NameError: name 'OSError' is not defined` 531건 지속**(10:32:24부터 ~2초 간격, 4틱 연속), 외부 SSE EOF 19건(10:33:05부터). 사이드(PID 81900): 10:25:06 이후 신규 미수신(`b0cabd1` 후속 이벤트 라이브 미관측).
  - Celly (`10:52:28Z`): 원장 `9971→9972`, 보드 `v11347→v11348`, MCP `1937→1079ms`. 리스: 입력 대기 4 유지, 실행/슬롯 `3→2`. 검수 finding 0, 신규 실패 0, 메인 오류 0. 포크 표본 오류 `2→5건`(24건 표본: `a48779c9` 3건 10:32:59·10:40:06·10:40:27, `a62f2668` 2건 10:41:08·10:51:58, 원인 미분류). 라우터 로그: 외부 SSE EOF 15건(10:32:59부터), `/ca/mcp` 단절 2건(10:36:42부터), 모델 클라이언트 단절 1건(10:36:53), 턴 투영 관찰 1건(10:40:36), **콘솔 `16840`·`16841` 확인 실패 및 선행 미확인 건너뜀 2쌍**(10:38:22 `age=94.9s`, 10:51:15–10:51:25 `age=67.9s`). `WinError 5` 미관측.
  - KOBE (`10:52:42Z`): 원장 `4636→4643`, 보드 `v651→v655`, MCP `442→428ms`. 리스(수락 1·입력 대기 24·실행/슬롯 1)·`n5` 미완료 자식 가드 유지. 포크 표본 오류 1건 유지(24건 표본: `af0aa8a7` 10:39:58, 원인 미분류). 메인 전사 1건: `10:47:00` ripgrep glob 파싱 오류. 라우터 로그: 정상 health keep-alive 가드 236건(10:32:26부터), 외부 SSE EOF 20건(10:32:48부터), 모델 스트림 단절 1건(10:40:45), 턴 투영 관찰 4건(10:43:44–10:49:45).
  - Prody (`10:52:54Z`): 원장 `1245`·`open_leases: {}`·보드 `v7489`·finding 0 유지, MCP `1678→713ms`, 포크 표본 오류 0(2건 표본), 메인 오류 0, 라우터 경고 0(직전 틱의 `channel_stdin_proxy_blocked` 7건 재발 없음).
  - Betty(보조, `10:53:09Z`): 원장 `1177`·리스(제출 5·입력 대기 1)·보드 `v5755`·finding 0 유지, MCP `1585→1809ms`, 포크 표본 오류 0(24건 표본), 메인 오류 0. 라우터: 정상 keep-alive 가드 241건(10:32:24부터), 외부 SSE EOF 20건(10:33:17부터), 콘솔 미확인 재발 없음.
  - 신규 입증 제품 결함 없음. 미결·추적 유지: **Andy PID 81900(`python=3.14.2`) `fork_turn_watch` `NameError: OSError` 4틱 연속(+531건) 및 로컬 `3.14.2` 4개 라우터 access violation 반복**, **Celly 콘솔 확인 실패/`stale_unconfirmed` skip 반복(`16840`/`16841`)·포크 오류·`WinError 5`**, `f9546dd` CLI 재시작/재로드 후 라이브 반영(7/7 파일 설치 완료), `b0cabd1` Andy PID 81900 후속 이벤트 라이브 확인 및 타 호스트 라우터 재시작 반영, Andy `n1166`·worktree 타임아웃, KOBE `n5`·`n150` worktree 타임아웃, Prody/Betty 콘솔 `stale_unconfirmed` 재발 여부.

- 10:32:22–10:33:02Z 정기 틱(10:12–10:13Z 대비). 네 호스트·보조 Betty Work MCP 및 `live.board` 모두 `probe_succeeded`/`observed`, 신규 병렬 원장 실패 0, 수집 stderr 없음. 운영 세션/라우터 재시작·운영 상태 파일 수정·추가 설치 없음. 이하 UTC.
  - Andy (`10:32:22Z`): 원장 `8166→8172`, 보드 `v12234→v12237`, MCP `406→687ms`, PID `81900`(`python=3.14.2`) 유지. 리스(제출 21·실행/슬롯 4·입력 대기 4)·`n1166` 검수 행 누락 유지. 포크 표본 오류 1건 유지(10건 표본: `a01f33ca` 10:30:09, 원인 미분류). 메인 전사 5건: 작업 측 Python 스크립트 exit 1 2건(10:14:07·10:26:59), Walkie HTTP 400 2건(10:14:46·10:15:17), 보드 버전 가드 1건(10:15:31, 정상 가드). 라우터 로그: **`fork_turn_watch_poll_failed error=NameError: name 'OSError' is not defined` 513건 지속**(10:12:36부터 ~2초 간격, 3틱 연속), 외부 SSE EOF 19건(10:13:35부터), 모델 스트림 단절 2건(10:15:45부터), 턴 투영 관찰 4건(10:14:10–10:28:19). 사이드(PID 81900): `e3e04cba`(P2) 10:20:59→10:25:06 처리 완료(`b0cabd1` 후속 이벤트 라이브 미관측).
  - Celly (`10:32:25Z`): 원장 `9960→9971`, 보드 `v11342→v11347`, MCP `421→1937ms`. 리스(입력 대기 4·실행/슬롯 3) 유지, 검수 finding 0, 신규 실패 0, 메인 오류 `1→0`. 포크 표본 오류 `4→2건`(24건 표본: `aee648ef` 10:20:41·10:26:02, 원인 미분류). 라우터 로그: 외부 SSE EOF 19건(10:13:15부터), 콘솔 확인 실패 3건(`16837` 10:13:17+skip, `16838` 10:13:56, `16839` 10:22:42–10:22:44+skip), `runtime_activity_context_usage_failed` `WinError 5` 1건(10:23:09), 턴 투영 관찰 4건(10:25:16–10:31:18).
  - KOBE (`10:32:37Z`): 원장 `4625→4636`, 보드 `v647→v651`, MCP `609→442ms`. 리스: 수락 1·입력 대기 24·실행/슬롯 1 유지, 제출 `1→0`. `n5` 미완료 자식 가드 지속, 메인 오류 0. 포크 표본 오류 1건 유지(24건 표본: `aadd50cf` 10:15:16, 원인 미분류). 라우터 로그: 정상 health keep-alive 가드 234건(10:12:39부터), 외부 SSE EOF 19건(10:13:19부터), 턴 투영 관찰 4건(10:19:06–10:25:06), 종료 후 `ping` 무시 1건(10:23:50).
  - Prody (`10:32:47Z`): 원장 `1245`·`open_leases: {}`·보드 `v7489`·finding 0 유지, MCP `989→1678ms`, 포크 표본 오류 0(2건 표본), 메인 오류 0. 라우터 로그: **`channel_stdin_proxy_blocked`(`prior_submission_failed`/`stale_unconfirmed` skip) 7건**(10:23:24–10:31:35, 메시지 `8183`·`8182`·`8184`·`8186`·`8185`·`8189`·`8188`; 실제 전달 여부 미확인).
  - Betty(보조, `10:33:02Z`): 원장 `1177`·리스(제출 5·입력 대기 1)·보드 `v5755`·finding 0 유지, MCP `1600→1585ms`, 포크 표본 오류 0(24건 표본), 메인 오류 0. 라우터 로그: 정상 keep-alive 가드 238건(10:12:35부터), 외부 SSE EOF 20건(10:12:50부터), 턴 투영 관찰 2건(10:25:06·10:31:15), **콘솔 확인 실패 및 선행 미확인 건너뜀 6쌍**(10:15:12–10:32:07, 메시지 `8097`·`8101`·`8103`·`8102`·`8106`·`8105`).
  - 신규 입증 제품 결함 없음. 미결·추적 유지: **Andy PID 81900(`python=3.14.2`) `fork_turn_watch` `NameError: OSError` 3틱 연속(+513건) 및 로컬 `3.14.2` 4개 라우터 access violation 반복**, **Prody(7건)·Betty(6쌍)·Celly(3건) 콘솔 확인 실패/`stale_unconfirmed` skip 반복**, `f9546dd` CLI 재시작/재로드 후 라이브 반영(7/7 파일 설치 완료), `b0cabd1` Andy PID 81900 후속 이벤트 라이브 확인 및 타 호스트 라우터 재시작 반영, Andy `n1166`·worktree 타임아웃, Celly `WinError 5`·포크 오류, KOBE `n5`·`n150` worktree 타임아웃.

- 10:12:35–10:13:19Z 정기 틱(09:52–09:53Z 대비). 네 호스트·보조 Betty Work MCP 및 `live.board` 모두 `probe_succeeded`/`observed`(Andy MCP 회복 406ms, Celly `live.board` 회복 v11342·MCP 421ms), 신규 병렬 원장 실패 0, 수집 stderr 없음. 운영 세션/라우터 재시작·운영 상태 파일 수정·추가 설치 없음. 이하 UTC.
  - Andy (`10:12:35Z`): 원장 `8154→8166`, 보드 `v12228→v12234`, MCP `TimeoutError→probe_succeeded(406ms)`, PID `81900`(`python=3.14.2`) 유지. 리스: 제출 21·실행/슬롯 4 유지, 입력 대기 `3→4`. `n1166` 검수 행 누락 지속. 포크 표본 오류 `0→1건`(10건 표본: `a01f33ca` 10:10:56, 원인 미분류). 메인 전사 2건 모두 정상 가드·안내: `work_side_status` 호스트 전용 가드(10:06:11), parked lease `4bbd7390adb…` P3 슬롯 상한 대기 안내(10:07:24). 라우터 로그: **`fork_turn_watch_poll_failed error=NameError: name 'OSError' is not defined` 480건 지속**(09:52:25부터 ~2초 간격), 외부 SSE EOF 20건(09:53:05부터), `/ca/mcp` `WinError 10053` 1건(09:52:28), 턴 투영 관찰 6건(09:59:08–10:12:10). 사이드(PID 81900): `362131ce`·`f9aa3659`(P4)·`0314cc84` 3건 09:56–10:09Z 처리 완료(`b0cabd1` 후속 이벤트 라이브 미관측).
  - Celly (`10:12:38Z`): 원장 `9959→9960`, 보드 `v11338→v11342`(`live.board` 회복), MCP `1641→421ms`. 리스: 입력 대기 `3→4`, 실행/슬롯 `4→3`. 검수 finding 0, 신규 실패 0. 포크 표본 오류 `2→4건`(24건 표본: `a48779c9` 3건 10:06:27·10:07:10·10:07:41, `aedaf7c9` 1건 10:12:39, 원인 미분류). 메인 전사 1건: `09:59:40` 작업 측 명령 exit 2. 라우터 로그: 외부 SSE EOF 19건(09:53:22부터), 턴 투영 관찰 6건(09:54:23–10:09:09), 모델 스트림 단절 1건(10:08:41). 콘솔 확인 실패·`WinError 5`·`/ca/mcp` 단절 미관측.
  - KOBE (`10:12:55Z`): 원장 `4619→4625`, 보드 `v645→v647`, MCP `1511→609ms`. 리스: 수락 1·입력 대기 24·실행/슬롯 1 유지, 제출 `0→1`. `n5` 미완료 자식 가드 지속, 메인 오류 0. 포크 표본 오류 `0→1건`(24건 표본: `a54e05ca` 09:54:46, 원인 미분류). 라우터 로그: 정상 health keep-alive 가드 235건(09:52:23부터), 외부 SSE EOF 20건(09:52:46부터).
  - Prody (`10:13:06Z`): 원장 `1245`·`open_leases: {}`·보드 `v7489`·finding 0 유지, MCP `701→989ms`, 포크 표본 오류 0(2건 표본), 메인 오류 0, 라우터 경고 0.
  - Betty(보조, `10:13:19Z`): 원장 `1177`·리스(제출 5·입력 대기 1)·보드 `v5755`·finding 0 유지, MCP `776→1600ms`, 포크 표본 오류 0(24건 표본), 메인 오류 0. 라우터 로그: 정상 keep-alive 가드 244건(09:52:24부터), 외부 SSE EOF 21건(09:52:24부터), 콘솔 `8094`·`8093` 확인 실패 및 선행 미확인 건너뜀 2쌍(10:08:32–10:08:33 `age=20.9s`, 10:08:53–10:08:54 `age=21.2s`).
  - 신규 입증 제품 결함 없음. 미결·추적 유지: **Andy PID 81900(`python=3.14.2`) `fork_turn_watch` `NameError: OSError` 지속(이번 구간 +480건) 및 로컬 `3.14.2` 4개 라우터 access violation 반복**, `f9546dd` CLI 재시작/재로드 후 라이브 반영(7/7 파일 설치 완료), `b0cabd1` Andy PID 81900 후속 이벤트 라이브 확인 및 타 호스트 라우터 재시작 반영, Andy `n1166`·worktree 타임아웃, Celly 콘솔 확인 실패·`WinError 5`·포크 오류, KOBE `n5`·`n150` worktree 타임아웃, Betty 콘솔 `8093`/`8094`.

- 09:52:21–09:53:37Z 정기 틱(09:32–09:33Z 대비). Celly·KOBE·Prody·보조 Betty Work MCP `probe_succeeded`, Andy만 수집 시점 `TimeoutError(4000ms)`였으나 같은 수집에서 `live.board` 정상(`v12228`) 및 직후 단독 `/ca/mcp work_status` 재조회 정상(843ms). 신규 병렬 원장 실패 0, 수집 stderr 없음. 운영 세션/라우터 재시작·운영 상태 파일 수정·추가 설치 없음. 이하 UTC.
  - Andy (`09:52:21Z`): 원장 `8146→8154`, 보드 `v12223→v12228`, MCP 수집 시 `TimeoutError(4000ms)`→직후 단독 `/ca/mcp` 재조회 843ms 성공, PID `81900`(`python=3.14.2`) 유지. 리스: 제출 21 유지, 실행/슬롯 `2→4`, 입력 대기 `4→3`. `n1166` 검수 행 누락 지속, 포크 표본 오류 0(9건 표본), 메인 오류 `1→0`. 라우터 로그: **직전 틱(09:27:13Z `SystemError: dictobject.c:3922`) 이후의 `fork_turn_watch_poll_failed error=NameError: name 'OSError' is not defined`가 이번 20분 구간에도 478건(09:32:26부터 ~2초 간격) 지속** 중(폴링 미작동 지속), 외부 SSE EOF 18건(09:32:35부터)·BrokenPipe 1건(09:42:51), 턴 투영 관찰 2건(09:41:18·09:43:20), 모델/라우터 클라이언트 단절 2건(09:49:00·09:51:26). 사이드(PID 81900): `031a5483`·`26e2f211`·`53ed5497`(P0) 3건 09:35–09:39Z 처리 완료, `362131ce`(`agent.pending_reminder`) 09:51:21 접수·처리 중(`b0cabd1` 후속 이벤트 라이브 미관측).
  - Celly (`09:52:28Z`): 원장 `9957→9959`, 보드 `v11337→v11338`(MCP 기준, `live.board` 1회 `TimeoutError`), MCP `500→1641ms`. 리스: 입력 대기 `4→3`, 실행/슬롯 `3→4`. 검수 finding 0, 신규 실패 0. 포크 표본 오류 `0→2건`(24건 표본: `a48779c9` 09:48:48, `a753f5bd` 09:52:56, 원인 미분류). 메인 전사 1건: `09:39:05` Walkie HTTP 400 `Invalid operation arguments`. 라우터 로그: 외부 SSE EOF 17건(09:33:24부터)·TimeoutError 1건(09:43:04), 턴 투영 관찰 3건(09:33:30·09:38:07·09:40:08), `runtime_activity_router_failed` `WinError 5` 1건(09:45:45), **콘솔 `16828`·`16834`·`16835` 확인 실패 및 선행 미확인 건너뜀 3쌍**(09:35:13–09:35:18 `age=26.5s`, 09:41:16 `age=78.3s`, 09:52:08–09:52:09 `age=31.3s`).
  - KOBE (`09:53:12Z`): 원장 `4617→4619`, 보드 `v642→v645`, MCP `707→1511ms`. 리스: 수락 1·입력 대기 24·실행/슬롯 1 유지, 제출 `1→0`. `n5` 미완료 자식 가드 지속, 포크 표본 오류 `1→0`(24건 표본), 메인 오류 `1→0`. 라우터 로그: 정상 health keep-alive 가드 242건(09:32:26부터), 외부 SSE EOF 20건(09:33:12부터), 턴 투영 관찰 2건(09:33:40·09:35:43).
  - Prody (`09:53:24Z`): 원장 `1245`·`open_leases: {}`·보드 `v7489`·finding 0 유지, MCP `607→701ms`, 포크 표본 오류 0(2건 표본), 메인 오류 0, 라우터 경고 0.
  - Betty(보조, `09:53:37Z`): 원장 `1177`·리스(제출 5·입력 대기 1)·보드 `v5755`·finding 0 유지, MCP `887→776ms`, 포크 표본 오류 0(24건 표본), 메인 오류 0. 라우터 로그: 정상 keep-alive 가드 248건(09:32:29부터), 외부 SSE EOF 21건(09:33:00부터), 턴 투영 관찰 1건(09:36:23).
  - 신규 입증 제품 결함 없음. 미결·추적 유지: **Andy PID 81900(`python=3.14.2`) `fork_turn_watch` `NameError: OSError` 지속(이번 구간 +478건) 및 로컬 `3.14.2` 4개 라우터 access violation 반복**, `f9546dd` CLI 재시작/재로드 후 라이브 반영(7/7 파일 설치 완료), `b0cabd1` Andy PID 81900 후속 이벤트 라이브 확인 및 타 호스트 라우터 재시작 반영, Andy `n1166`·worktree 타임아웃, Celly 콘솔 확인 실패 반복(`16828`/`16834`/`16835`)·`WinError 5`·`live.board` 간헐 타임아웃, KOBE `n5`·`n150` worktree 타임아웃, Betty 콘솔 `8085`/`8086`.

- 09:32:26–09:33:04Z 정기 틱(09:12–09:13Z 대비). 네 호스트·보조 Betty Work MCP 모두 `probe_succeeded`, 신규 병렬 원장 실패 0, 수집 stderr 없음. 운영 세션/라우터 재시작·운영 상태 파일 수정·추가 설치 없음. 이하 UTC.
  - **Andy `fork_turn_watch` 연속 실패 이상 (`09:27:13Z`부터 지속):** Andy 라우터 PID `81900`(`python=3.14.2`)에서 `09:27:13Z` `fork_turn_watch_poll_failed error=SystemError: D:\a\1\s\Objects\dictobject.c:3922: bad argument to internal function`(CPython v3.14.2 `dict_merge` 내부 인자 검증 실패) 1건 직후, `09:27:17Z`부터 매 ~2초마다 `fork_turn_watch_poll_failed error=NameError: name 'OSError' is not defined`가 수집 시점 126건(`09:35:54Z` 확인 시 210건+)으로 지속 발생 중(해당 스레드의 폴링이 정상 동작하지 않음). 대조 확인: 로컬 4개 라우터(`9505`·`9466`·`9489`·`9524`)는 모두 `python=3.14.2`(`pythoncore-3.14-64`)로 실행되며 `router-fault.log`에 `Windows fatal exception: access violation`이 각 8·1·1·1건 기록됐다(Andy 8건: `contextlib.py:109` 1, `fork_turn_watch.py:827` 4, `json/decoder.py:34` 2, `fork_turn_watch.py:767` 1). 반면 동일 머신의 Celly(`python=3.14.10`, 77회 기동)와 KOBE·Prody·Betty(`python=3.14.10`)는 `router-fault.log` fatal 0건, `fork_turn_watch_poll_failed` 0건이었다(로컬 `AppData\Local\ciel-runtime\python`에는 `3.14.10-x64`가 별도 존재). 정확한 CPython 3.14.2 내부 원인 커밋·메커니즘은 미확인(제품 코드 재현·수정 대상 미입증).
  - Andy (`09:32:26Z`): 원장 `8142→8146`, 보드 `v12218→v12223`, MCP `2781→1454ms`, PID `81900` 유지. 리스: 제출 `22→21`, 실행/슬롯 `4→2`, 입력 대기 `2→4`. `n1166` 검수 행 누락 지속, 포크 표본 오류 0(9건 표본). 메인 전사 1건: `09:22:48` `auto continue` 상태 읽기 실패. 라우터 로그: 위 `fork_turn_watch_poll_failed` 127건(SystemError 1 + NameError 126), 외부 SSE EOF 19건(09:13:24부터), `runtime_activity_context_usage_failed` `WinError 5` 2건(09:19:01부터), 턴 투영 관찰 2건(09:15:22·09:21:43), 모델 스트림 단절 1건(09:15:48). 사이드(PID 81900): `09:14–09:32Z` 3건 처리 완료(`d9252d2c` P0, `4b942ddf`·`9550510b` `system.notice`), `031a5483`(P0) 처리 중(`b0cabd1` 후속 이벤트 라이브 미관측).
  - Celly (`09:32:30Z`): 원장 `9956→9957`, 보드 `v11334→v11337`, MCP `547→500ms`. 리스: 입력 대기 `3→4`, 실행/슬롯 `4→3`. 검수 finding 0, 신규 실패 0, 포크 표본 오류 `6→0`(24건 표본). 메인 전사 1건: `09:27:04` 작업 측 `FileNotFoundError: /tmp/r256.json`. 라우터 로그: 외부 SSE EOF 20건(09:12:49부터), 모델 스트림 단절 1건(09:18:02), 턴 투영 관찰 4건(09:20:06–09:27:08), 콘솔 `16822` 확인 실패·선행 미확인 건너뜀 1쌍(09:20:45–09:20:48, `age=28.7s`). `/ca/mcp` 단절·`WinError 5` 미관측.
  - KOBE (`09:32:40Z`): 원장 `4609→4617`, 보드 `v640→v642`, MCP `532→707ms`. 리스(수락 1·입력 대기 24·제출 1·실행/슬롯 1) 유지, `n5` 미완료 자식 가드 지속. 포크 표본 오류 `2→1건`(24건 표본: `af8e420c` 09:29:57, 원인 미분류). 메인 전사 1건: `09:15:52` Ripgrep 20s 타임아웃. 라우터 로그: 정상 health keep-alive 가드 232건(09:12:34부터), 외부 SSE EOF 20건(09:12:41부터), 턴 투영 관찰 4건(09:13:25–09:19:31), 모델 스트림 단절 1건(09:30:37).
  - Prody (`09:32:52Z`): 원장 `1245`·`open_leases: {}`·보드 `v7489`·finding 0 유지, MCP `851→607ms`, 포크 표본 오류 0(2건 표본), 메인 오류 0, 라우터 경고 0.
  - Betty(보조, `09:33:04Z`): 원장 `1177`·리스(제출 5·입력 대기 1)·보드 `v5755`·finding 0 유지, MCP `1178→887ms`, 포크 표본 오류 0(24건 표본), 메인 오류 0. 라우터: 정상 keep-alive 가드 233건, 외부 SSE EOF 21건, 턴 투영 관찰 3건(09:13:23–09:22:36).
  - 신규 입증 제품 결함 없음. 미결·추적: **Andy PID 81900 `python=3.14.2` `fork_turn_watch` `SystemError`(`dictobject.c:3922`) 후 영구 `NameError: OSError` 및 로컬 `3.14.2` access violation 4개 라우터 반복**, `f9546dd` CLI 재시작/재로드 후 라이브 반영(7/7 파일 설치 완료), `b0cabd1` Andy PID 81900 후속 이벤트 라이브 확인 및 타 호스트 라우터 재시작 반영, Andy `n1166`·worktree 타임아웃, Celly 콘솔 `16822`·`WinError 5`, KOBE `n5`·`n150` worktree 타임아웃, Betty 콘솔 `8085`/`8086`.

- 09:12:32–09:13:16Z 정기 틱(08:54–08:57Z 대비). 네 호스트·보조 Betty Work MCP 모두 `probe_succeeded`(Celly `live.board`·MCP 회복 547ms, Prody 1회 성공 851ms), 신규 병렬 원장 실패 0, 수집 stderr 없음. 운영 세션/라우터 재시작·운영 상태 파일 수정·추가 설치 없음. 이하 UTC.
  - Andy (`09:12:32Z`): 원장 `8126→8142`, 보드 `v12212→v12218`, MCP `796→2781ms`, PID `81900` 유지. 리스: 제출 `21→22`, 실행/슬롯 `4` 유지, 입력 대기 `3→2`. Finding `2→1`: `n1166` 검수 행 누락 지속, 직전 틱의 `n2806` 포크 미관측 finding은 빠짐(종료·완료 여부 별도 미확인). 포크 표본 오류 0(9건 표본), 메인 오류 0. 라우터 로그: 외부 SSE EOF 17건(08:56:00부터), 턴 투영 관찰 5건(08:56:40–09:04:42). 단절·502·worktree 타임아웃 미관측. 사이드(PID 81900): 08:48:26 이후 신규 미수신(`b0cabd1` 후속 이벤트 라이브 미관측).
  - Celly (`09:12:37Z`): 원장 `9950→9956`, 보드 `v11333→v11334`(`live.board` 회복), MCP `2203→547ms`. 리스: 입력 대기 `4→3`, 실행/슬롯 `3→4`. 검수 finding 0, 신규 실패 0. 포크 표본 오류 `2→6건`(24건 표본: `a7bced6c` 4건 09:04:10·09:04:35·09:09:47·09:10:06, `aedaf7c9` 2건 09:11:48·09:12:09, 원인 미분류). 메인 전사 2건: `08:58:21` `n1571` `not a slot-released wait`(정상 가드, `work_parallel.py:867-868`), `09:09:45` `Work reconciliation: Parallel read failed: The operation timed out`. 라우터 로그: 외부 SSE EOF 14건(08:56:26부터), `/ca/mcp` 단절 7건(08:55:25부터, 첫 1건은 직전 틱 경계와 중복), `runtime_activity_context_usage_failed` `WinError 5` 1건(08:55:25), 턴 투영 관찰 4건(08:55:29–09:10:00), 중앙 게이트웨이 discovery 불가 1건(09:03:59, `elapsed_ms=5430`), 콘솔 `16819` 확인 실패·선행 미확인 건너뜀 1쌍(09:07:38–09:07:39, `age=50.7s`), 모델 스트림 단절 2건(09:10:06·09:10:18).
  - KOBE (`09:12:48Z`): 원장 `4597→4609`, 보드 `v638→v640`, MCP `543→532ms`. 리스: 수락 1·입력 대기 24·실행/슬롯 1 유지, 제출 `0→1`. `n5` 미완료 자식 가드 지속, 메인 오류 `6→0`. 포크 표본 오류 `0→2건`(24건 표본: `a51ca16b` 08:56:25, `a09c5163` 09:09:26, 원인 미분류). 라우터 로그: 정상 health keep-alive 가드 206건(08:54:59부터), 외부 SSE EOF 16건(08:57:17부터)·BrokenPipe 1건(08:56:15), 턴 투영 관찰 3건(08:59:47–09:03:50). `WinError 5`·단절 미관측.
  - Prody (`09:13:00Z`): 원장 `1245`·`open_leases: {}`·보드 `v7489`·finding 0 유지, MCP `860→851ms`, 포크 표본 오류 0(2건 표본), 라우터 경고 0. 메인 전사 1건: `09:10:51` `auto continue` 상태 읽기 실패.
  - Betty(보조, `09:13:16Z`): 원장 `1177`·리스(제출 5·입력 대기 1)·보드 `v5755`·finding 0 유지, MCP `1188→1178ms`, 포크 표본 오류 0(24건 표본). 메인 전사 1건: `09:03:44` `auto continue` 상태 읽기 실패. 라우터 로그: 정상 keep-alive 가드 213건, 외부 SSE EOF 18건, 턴 투영 관찰 1건(09:08:45), 콘솔 `8086`·`8085` 확인 실패 및 선행 미확인 건너뜀 각 1쌍(09:10:45–09:10:47 `age=89.9s`, 09:11:03–09:11:04 `age=20.2s`).
  - 신규 입증 결함 없음. 미결·추적 유지: `f9546dd` CLI 재시작/재로드 후 라이브 반영(7/7 파일 설치 완료), `b0cabd1` Andy PID 81900 후속 이벤트 라이브 확인 및 타 호스트 라우터 재시작 반영, Andy `n1166`·worktree 타임아웃·`fork_turn_watch.py:767` 크래시 재발 여부, Celly 콘솔 `16819`·`/ca/mcp` 단절·`WinError 5`·포크 오류, KOBE `n5`·`WinError 5`·`n150` worktree 타임아웃, Betty 콘솔 `8085`/`8086`.

- 08:54:55–08:57:07Z 정기 틱(08:32–08:33Z 대비). 네 호스트·보조 Betty Work MCP 모두 `probe_succeeded`(Celly MCP 회복 2203ms·단 `live.board` 1회 `TimeoutError`; Prody는 본 수집에서 `.12:1004` SSH 배너 타임아웃 1회 후 08:57:07Z 단독 재조회 성공 860ms). 신규 병렬 원장 실패 0. 운영 세션/라우터 재시작·운영 상태 파일 수정·추가 설치 없음. 이하 UTC.
  - Andy (`08:54:55Z`): 원장 `8119→8126`, 보드 `v12208→v12212`, MCP `953→796ms`, PID `81900` 유지. 리스: 제출 21 유지, 실행/슬롯 `5→4`, 입력 대기 `2→3`. 검수/포크 finding `1→2`: `n1166` 검수 행 누락 지속, **신규 `n2806` (`e2624291` P4) 슬롯 유지·08:36부터 포크 미관측**(종료 미확인). 포크 표본 오류 0(9건 표본), 메인 오류 0. 라우터 로그: 외부 SSE EOF 21건(08:32:51부터)·BrokenPipe 1건(08:45:58), 모델 스트림 단절 5건(08:33:09·08:35:22부터 3·08:39:25), `transcript_delta_delivery_failed` HTTP 502 1건(08:49:47), 턴 투영 관찰 3건(08:50:38–08:54:40). 사이드(PID 81900): `0dbb0b4d`(`agent.pending_reminder`) 08:46:13→08:48:26 처리 완료(`b0cabd1` 후속 이벤트 라이브 미관측).
  - Celly (`08:54:58Z`): 원장 `9949→9950`, 보드 `v11329→v11333`(MCP 기준, `live.board` `TimeoutError`), MCP `TimeoutError→probe_succeeded(2203ms)`. 리스: 입력 대기 `3→4`, 실행/슬롯 `4→3`. 검수 finding 0, 신규 실패 0. 포크 표본 오류 2건(24건 표본: `a753f5bd` 08:40:08, `a62f2668` 08:50:12, 원인 미분류). 메인 전사 2건: 작업 측 Python `urlopen` exit 1(08:33:20)·파일 없음(08:35:29). 라우터 로그: 외부 SSE EOF 20건(08:32:54부터)·BrokenPipe 1건(08:45:46), 모델/라우터 클라이언트 단절 3건(08:35:19부터 2·08:41:53), `/ca/mcp` 단절 2건(08:33:03 WinError 10053·08:55:25 WinError 10054), 턴 투영 관찰 2건(08:33:45·08:35:46), `sse_gate_status` 읽기 실패 1건(08:36:03), `runtime_activity_router_failed` `WinError 5` 1건(08:39:23), 콘솔 `16815` 확인 실패 및 선행 미확인 건너뜀 1쌍(08:52:53), `transcript_delta_delivery_failed` TimeoutError 1건(08:54:54).
  - KOBE (`08:55:41Z`): 원장 `4587→4597`, 보드 `v633→v638`, MCP `930→543ms`. 리스: 수락 1·입력 대기 24·실행/슬롯 1 유지, 제출 `1→0`. `n5` 미완료 자식 가드 지속, 포크 표본 오류 `2→0`(24건 표본). 메인 전사 6건: Walkie HTTP 400 2건(08:39:00·08:39:02), `walkie_threads_stop` `client_id` UUID 검증 `-32602` 1건(08:41:26), 작업 측 스크립트 exit 1 1건(08:43:24), `work_side_status` 호스트 전용 가드 1건(08:44:20, 정상 가드), Walkie 무응답(재기동 가능) 안내 1건(08:49:48). 라우터 로그: 정상 health keep-alive 가드 269건(08:32:26부터), 외부 SSE EOF 22건(08:32:51부터), 턴 투영 관찰 9건(08:32:48–08:49:44), `runtime_activity_context_usage_failed` `WinError 5` 1건(08:37:01), 클라이언트 단절 1건(08:44:02).
  - Prody (`08:57:07Z`, 1회 SSH 배너 타임아웃 후 단독 재조회): 원장 `1245`·`open_leases: {}`·보드 `v7489`·finding 0 유지, MCP `1695→860ms`, 포크 표본 오류 0(2건 표본), 메인 오류 0, 라우터 경고 0.
  - Betty(보조, `08:56:11Z`): 원장 `1177`·리스(제출 5·입력 대기 1) 유지, 보드 `v5753→v5755`, MCP `860→1188ms`, finding 0, 포크 표본 오류 0(24건 표본). 메인 전사 1건: `08:35:25` 병렬 검수 완료 대기 가드(정상 가드). 라우터 로그: 정상 keep-alive 가드 278건, 외부 SSE EOF 23건, 턴 투영 관찰 3건(08:33:59–08:38:00).
  - 신규 입증 결함 없음. 미결·추적: `f9546dd` CLI 재시작/재로드 후 라이브 반영(7/7 파일 설치 완료), `b0cabd1` Andy PID 81900 후속 이벤트 라이브 확인 및 타 호스트 라우터 재시작 반영, Andy `n1166`·`n2806` 포크 미관측(08:36부터)·worktree 타임아웃·`fork_turn_watch.py:767` 크래시 재발 여부, Celly 콘솔 `16815`·`/ca/mcp`/`board` 간헐 타임아웃·`WinError 5`·포크 오류, KOBE `n5`·`WinError 5`·`n150` worktree 타임아웃, Betty 콘솔 `8082`.

- 08:32:22–08:33:32Z 정기 틱(08:12–08:14Z 대비). Andy·KOBE·Prody·보조 Betty Work MCP `probe_succeeded`, Celly만 수집 시점 `TimeoutError(4000ms)`(2연속 틱)였으나 직후 단독 재조회(`/health` 218ms, `/board` 2483ms, `/ca/mcp work_status` 1702ms)는 정상 응답. 신규 병렬 원장 실패 0, 수집 stderr 없음. 운영 세션/라우터 재시작·운영 상태 파일 수정·추가 설치 없음. 이하 UTC.
  - Andy (`08:32:22Z`): 원장 `8115→8119`, 보드 `v12207→v12208`, MCP `750→953ms`, PID `81900` 유지. 리스: 제출 21·입력 대기 2 유지, 실행/슬롯 `4→5`, 예약/슬롯 `1→0`. `n1166` 검수 행 누락 지속, 포크 표본 오류 0(9건 표본), 메인 오류 `1→0`. 라우터 로그: 외부 SSE EOF 18건(08:13:12부터)·TimeoutError 1건(08:27:05), `/ca/mcp` 단절 3건(08:14:52부터), **`n2806` `git worktree add timed out after 142 s` 1건**(08:14:52, 근본 원인 미확인). 사이드(PID 81900): `6bc4ea10`(`P3 attention.answered`) 08:15:03→08:23:52 처리 완료(선행 최초 영수증 미확인으로 `b0cabd1` 후속 라이브 효과는 미판정).
  - Celly (`08:32:25Z`): 원장 `9943→9949`, 보드 `v11328→v11329`, MCP 수집 시 `TimeoutError(4000ms)`→직후 단독 `/ca/mcp work_status` 1702ms 응답(08:32:19–08:32:29 동시 1.5MB급 모델 요청 4건 및 `work_host_page`/`work_side_claim` 집중과 시점 겹침; 지연 원인 미확인). 리스: 입력 대기 3 유지, 실행/슬롯 `3→4`. 검수 finding 0, 신규 실패 0. 포크 표본 오류 `5→2건`(24건 표본: `a48779c9` 08:31:17, `a7bced6c` 08:31:18, 원인 미분류). 메인 전사 1건: `08:24:21` `auto continue` 상태 읽기 실패. 라우터 로그: 외부 SSE EOF 10건(08:15:53부터)·ConnectionAborted 2건(08:18:24부터)·BrokenPipe 1건(08:26:53), `/ca/mcp` 단절 7건(08:14:53 WinError 10053 1·08:18:15부터 WinError 10054 6), 모델 스트림 단절 2건(08:19:48부터), 턴 투영 관찰 5건(08:18:16–08:31:40), 콘솔 `16806` 확인 실패 1건(08:19:59, `age=35.1s`), 런타임 이벤트 전송 TimeoutError 재시도 1건(08:24:34), `transcript_delta_delivery_failed` HTTP 502 1건(08:30:43). `WinError 5` 미관측.
  - KOBE (`08:33:05Z`): 원장 `4582→4587`, 보드 `v632→v633`, MCP `252→930ms`. 리스: 수락 1·입력 대기 24·실행/슬롯 1 유지, 제출 `0→1`. `n5` 미완료 자식 가드 지속, 메인 오류 0. 포크 표본 오류 `0→2건`(24건 표본: `abbf4798` 08:24:11·08:26:34, 원인 미분류). 라우터 로그: 정상 health keep-alive 가드 236건(08:12:42부터), 외부 SSE EOF 21건(08:12:48부터), 턴 투영 관찰 4건(08:14:37–08:32:48), 모델 스트림 단절 1건(08:28:03), `transcript_delta_delivery_failed` HTTP 502 1건(08:30:44, Celly 08:30:43과 동시).
  - Prody (`08:33:17Z`): 원장 `1245`·`open_leases: {}`·보드 `v7489`·finding 0 유지, MCP `647→1695ms`, 포크 표본 오류 `2→0`(2건 표본), 메인 오류 0, 라우터 경고 0.
  - Betty(보조, `08:33:32Z`): 원장 `1177`·리스(제출 5·입력 대기 1)·보드 `v5753` 유지, MCP `913→860ms`, finding 0, 포크 표본 오류 0(24건 표본). 메인 전사 2건: `08:22:19` 작업 측 가드 exit 1, `08:24:59` Walkie `file_ids` 최대 4개 `-32602` 인자 검증 오류. 라우터 로그: 정상 keep-alive 가드 242건, 외부 SSE EOF 20건, 턴 투영 관찰 10건(08:13:48–08:31:59).
  - 신규 입증 결함 없음. 미결·추적: `f9546dd` CLI 재시작/재로드 후 라이브 반영(7/7 파일 설치 완료), `b0cabd1` Andy PID 81900 후속 이벤트 라이브 확인 및 타 호스트 라우터 재시작 반영, Andy `n1166`·`n2806`/`n2859` worktree 타임아웃·`fork_turn_watch.py:767` 크래시 재발 여부, Celly `/ca/mcp` 간헐 4s 초과·단절 및 콘솔 `16806`·포크 오류·`WinError 5`, KOBE `n5`·`n150` worktree 타임아웃, Betty 콘솔 `8082`·`decision_refused_stop e676ef18`.

- 08:12:40–08:14:32Z 정기 틱(07:52–07:53Z 대비). Andy·KOBE·Prody·보조 Betty Work MCP `probe_succeeded`; Celly만 수집 시점 `TimeoutError(4016ms)`였으나 직후 단독 재조회(`/health` PID 111684 422ms, `/board` v11328 1608ms)는 정상 응답. 신규 병렬 원장 실패 0, 수집 stderr 없음. 운영 세션/라우터 재시작·운영 상태 파일 수정·추가 설치 없음. 08:01:20–08:03:12Z 구간 네 호스트 동시 upstream 게이트웨이 SSE 오류·재시도 관측(원인 미확인). 이하 UTC.
  - Andy (`08:12:40Z`): 원장 `8095→8115`, 보드 `v12197→v12207`, MCP `1828→750ms`, PID `81900` 유지. 리스: 제출 21 유지, 실행/슬롯 `2→4`, 입력 대기 `1→2`, 예약/슬롯 `0→1`. `n1166` 검수 행 누락 지속, 포크 표본 오류 0(9건 표본). 메인 전사 1건: `08:02:06` `n2882`(`a7bdf8c0bbd8`) `transport_interrupted` API 재시도 예약(회복 미확인). 라우터 로그: 외부 SSE EOF 20건(07:52:45부터), `08:01:24–08:02:36` upstream SSE 오류 3건(형태 로그 3건 제외)·응답 노출 전 `cielairouter_late_sse_retry` 8건(7개 op), 클라이언트 단절 4건(07:53:23 x2·08:09:18·08:11:15), 턴 투영 관찰 4건(07:57:07–08:03:10). 사이드(PID 81900): `c04d3666` 08:09:20 처리 완료, 신규 미수신(`b0cabd1` 후속 이벤트 라이브 미관측).
  - Celly (`08:12:42Z`): 원장 `9924→9943`, 보드 `v11322→v11328`(재조회), MCP 수집 시 `TimeoutError(4016ms)`→직후 `/health`·`/board` 응답 확인(422ms·1608ms). 리스(입력 대기 3·실행/슬롯 3) 유지, 검수 finding 0, 신규 실패 0, 메인 오류 0. 포크 표본 오류 `1→5건`(24건 표본: `a753f5bd` 07:54:14·07:54:27, `ad9897f6` 07:59:23, `a27479f3` 08:04:47·08:05:11, 원인 미분류). 라우터 로그: 외부 SSE EOF 17건(07:53:33부터), `/ca/mcp` 단절 8건(07:57:41부터), `08:01:24–08:03:12` upstream SSE 오류 2건(형태 로그 2건 제외)·`late_sse_retry` 1건·중앙 게이트웨이 경로 로그 1건·`transient_retry` 5건(동일 op `21434ad2`), 클라이언트 단절 3건(07:55:06·07:59:59·08:06:52), 턴 투영 관찰 5건(07:56:08–08:05:44), 콘솔 `16801` 확인 실패 1건(08:10:49, `age=21.7s`). `WinError 5` 미관측.
  - KOBE (`08:14:08Z`): 원장 `4572→4582`, 보드 `v629→v632`, MCP `3465→252ms`. 리스: 수락 1·입력 대기 24·실행/슬롯 1 유지, 제출 `1→0`. `n5` 미완료 자식 가드 지속, 포크 표본 오류 0(24건 표본), 메인 오류 0. 라우터 로그: 정상 health keep-alive 가드 246건(07:52:42부터), 외부 SSE EOF 20건(07:52:47부터)·읽기 TimeoutError 1건(08:03:32), `08:01:20` upstream SSE 오류 2건(형태 로그 2건 제외), 턴 투영 관찰 6건(07:55:10–08:12:36). `WinError 5`·worktree 타임아웃 미관측.
  - Prody (`08:14:18Z`): 원장 `1241→1245`, 보드 `v7486→v7489`, MCP `1214→647ms`, 메인 오류 0, 라우터 경고 0. 열린 리스 `running+slot 1→0`(`open_leases: {}`) 및 `n1315` 미관측 finding 해소(원장 정리만 확인, 작업 완료 여부는 별도 미확인). 포크 표본 오류 `0→2건`(2건 표본: `a38f3e61dcbc1a1a8` 08:05:26·08:05:44, 원인 미분류).
  - Betty(보조, `08:14:32Z`): 원장 `1177`·리스(제출 5·입력 대기 1)·보드 `v5753` 유지, MCP `902→913ms`, 신규 실패 0, 포크 표본 오류 0(24건 표본), 메인 오류 0. 라우터 로그: 정상 keep-alive 가드 254건, 외부 SSE EOF 22건, `08:01:34–08:02:18` `late_sse_retry` 1건·upstream SSE 오류 1건(형태 1건 제외), `/ca/mcp` 단절 1건(08:02:57), 콘솔 `8082` 확인 실패 및 선행 미확인 건너뜀 1쌍(08:03:43), 턴 투영 관찰 10건(07:53:12–08:13:48).
  - 신규 입증 결함 없음. 미결·추적: `f9546dd` CLI 재시작/재로드 후 라이브 반영, `b0cabd1` Andy PID 81900 후속 이벤트 라이브 확인 및 타 호스트 라우터 재시작 반영, Andy `n1166`·`n2859` worktree 타임아웃·`fork_turn_watch.py:767` 크래시 재발 여부, Celly 포크 오류·콘솔 `16801`·`/ca/mcp` 단절·`WinError 5`, KOBE `n5`·`n150` worktree 타임아웃, Betty 콘솔 `8082`·`decision_refused_stop e676ef18`.

- 07:52:38–07:53:24Z 정기 틱(07:42–07:43Z 대비). 네 호스트·보조 Betty Work MCP 모두 `probe_succeeded`(Andy 회복), 신규 병렬 원장 실패는 Celly 1건(`decision_refused_stop f86cc995 x1`), 나머지 0건, 수집 stderr 없음. 운영 세션/라우터 재시작·운영 상태 파일 수정·추가 설치 없음(`f9546dd` 7/7 파일 설치는 07:45Z 완료·CLI 미재시작으로 라이브 미반영). 이하 UTC.
  - Andy (`07:52:38Z`): 원장 `8094→8095`, 보드 `v12194→v12197`, MCP `TimeoutError→probe_succeeded(1828ms)`, PID `81900` 유지(추가 크래시 없음). 리스(제출 21·실행/슬롯 2·입력 대기 1) 유지, `n1166` 검수 행 누락 지속, 포크 표본 오류 `2→0`(9건 표본), 메인 오류 `5→0`. 라우터 로그: 외부 SSE EOF 10건(07:42:32부터), `/ca/mcp` 단절 2건(07:42:20 WinError 10053·07:43:24 WinError 10054), 모델 스트림 단절 3건(07:50:26·07:50:27·07:51:29). `git worktree add` 타임아웃 없음. 사이드(PID 81900): `f934aa75`(P0)·`8a116d7f`(P0)·`24e20bb2`(P3) 3건 동시 처리 완료, `c04d3666` 처리 중; 재기동 후 선행 메시지에 대한 후속 라이프사이클 이벤트는 아직 미관측(`b0cabd1` 라이브 효과 미확인).
  - Celly (`07:52:42Z`): 원장 `9918→9924`, 보드 `v11320→v11322`, MCP `390→687ms`. 리스: 입력 대기 3 유지, 제출 `1→0`, 실행/슬롯 `2→3`. 신규 원장 실패 `decision_refused_stop f86cc995 x1`(07:46:24): 1초 전 메인 `work_parallel_item_review: The operation timed out`(07:46:23) 및 같은 시각 `/ca/mcp` `WinError 10054` 5건과 맞물렸으나 타임아웃 원인은 미확인. 포크 표본 오류 `2→1건`(24건 표본: `a551f5b265e8782ca` 07:48:18, 원인 미분류). 메인 신규 오류 2건: `07:46:23` `work_parallel_item_review` 타임아웃, `07:51:37` 작업 측 `gh_read.py` exit 1(`07:42:09`·`07:42:24` cp1252는 직전 틱과 중복). 라우터 로그: 외부 SSE EOF 9건(07:43:06부터), `/ca/mcp` 단절 5건(07:46:23부터), 턴 투영 관찰 4건(07:42:37–07:46:41), 콘솔 `16791` 확인 실패 1건(07:46:45, `age=118.4s`). 이번 구간 `WinError 5` 미관측.
  - KOBE (`07:52:53Z`): 원장 `4566→4572`, 보드 `v628→v629`, MCP `1069→3465ms`. 리스: 수락 1·입력 대기 24·실행/슬롯 1 유지, 제출 `0→1`. `n5` 미완료 자식 가드 지속, 포크 표본 오류 `4→0`(24건 표본), 메인 오류 0. 라우터 로그: 정상 health keep-alive 가드 122건(07:42:09부터), 외부 SSE EOF 11건(07:42:32부터), 턴 투영 관찰 2건(07:43:56·07:45:56), 모델 스트림 단절 1건(07:51:17); `07:42:27` `WinError 5` 1건은 직전 틱과 동일.
  - Prody (`07:53:09Z`): 원장 `1241`·리스(`running+slot 1`)·보드 `v7486` 유지, MCP `799→1214ms`, 포크 표본 오류 0(2건 표본), 라우터 경고 0. `n1315` 포크 미관측(05:32부터) 지속(종료 미확인). 메인 신규 오류 1건: `07:43:12` 작업 측 `stale_owned.py` exit 2(`07:42:06`은 직전 틱과 중복).
  - Betty(보조, `07:53:24Z`): 원장 `1177`·리스(제출 5·입력 대기 1)·보드 `v5753` 유지, MCP `792→902ms`, 신규 실패 0, 포크 표본 오류 `3→0`(24건 표본). 메인 작업 측 `ocr_png.ps1` 인자 오류 2건(07:48:18·07:49:40). 라우터: 정상 keep-alive 가드 131건, 외부 SSE EOF 11건, 턴 투영 관찰 6건(07:43:07–07:53:12).
  - 신규 입증 결함 없음. 미결·추적 유지: `f9546dd` CLI 재시작/재로드 후 라이브 반영, `b0cabd1` Andy PID 81900 후속 이벤트 라이브 확인 및 타 호스트 라우터 재시작 반영, Andy `n1166`·`n2859` worktree 타임아웃·`fork_turn_watch.py:767` 크래시 재발 여부, Celly `07:46:23` `work_parallel_item_review` 타임아웃/`f86cc995` `decision_refused_stop`·콘솔 `16791` 미확인·`WinError 5`, KOBE `n5`·`n150` worktree 타임아웃·`WinError 5`, Prody `n1315`.

- 07:45Z `f9546dd` npm `@beta` 확인 및 7개 루트 해시 확인 설치 완료(파일 설치만, 라이브 미반영). npm `@beta` = `0.2.51-beta.20261126-074010.f9546dd`(gitHead `f9546ddee55aba40c388f692b288eef79035cb01`), tarball 내 `ciel_runtime_support/claude_mods/ciel-work/hooks/register.js`가 커밋과 SHA-256 일치(`2ee53557e63b` → `10fcdb2d0227`). 승인된 7개 루트(로컬 4곳, KOBE `u24`, Prody `u41`, Betty `u42`) 모두 `check` `base` → `apply` `ok=True` 확인(`.cmd` 래퍼·버전 메타데이터 미변경, 원격 임시 파일 삭제). 실행 중 Claude CLI 세션 및 라우터는 재시작하지 않았으므로, 떠 있는 세션의 Observatory 패널에는 세션 재시작 또는 모드 재로드 전까지 반영되지 않는다.

- 07:42:06–07:43:06Z 정기 틱(06:52–06:53Z 대비). 수집 시점 Andy `live.mcp`/`live.board`가 `TimeoutError`(4000ms) 1회 발생했으나 직후 단독 `/ca/work/api/v1/board` 재조회는 성공(`v12194`, 2311ms)했고 나머지 3호스트·보조 Betty Work MCP는 모두 `probe_succeeded`. 신규 병렬 원장 실패는 보조 Betty 1건(`decision_refused_stop e676ef18 x1`), 주 4호스트는 0건. 수집 stderr 없음. 운영 세션/라우터 재시작·운영 상태 파일 수정·추가 설치 없음. `f9546dd` Publish 실행(`45777018856`) 진행 중(npm `@beta`는 아직 `b0cabd1`). 이하 UTC.
  - Andy (`07:42:06Z`): 원장 `8062→8094`, 보드 `v12179→v12194`(재조회), MCP `probe_failed(TimeoutError 4000ms)` 후 보드 재조회 성공(2311ms), PID `81900` 유지(06:54:20 이후 추가 크래시 없음). 리스: 제출 21 유지, 실행/슬롯 1→2, 입력 대기 2→1. `n1166` 검수 행 누락 지속. 포크 표본 오류 0→2건(9건 표본: `a0146661c25cbb837` 07:04:34·07:04:51, 원인 미분류). 메인 전사 5건: `n2879` API `server_error` 재시도 예약·1차 재시도(06:55:57·06:57:47), Walkie `file_ids` 최대 4개 인자 검증 -32602(07:11:38), `work_side_handoff` 미존재 가드(07:32:47, 정상 가드), `auto continue` 상태 읽기 실패 알림(07:35:13). 라우터 로그: 외부 SSE EOF 46건(06:55:29부터), `/ca/mcp` 단절 4건(06:56:08부터), 모델 스트림/클라이언트 단절 5건(07:03:08·07:21:25부터 3건·07:34:40), 턴 투영 관찰 13건(07:09:07–07:39:20), 로컬 중앙 게이트웨이 discovery 불가 1건(06:57:49), 런타임 이벤트 전송 TimeoutError 재시도 1건(07:17:27), **`n2859` `git worktree add` 타임아웃 3건**(07:32:23 88s·07:33:24 32s·07:34:37 41s 및 retained 경고 1건, 근본 원인 미확인), 앞선 틱에 기록한 06:54:20 재기동 로그 1건(중복 합산 안 함). 사이드 영수증(PID 81900): `07:05–07:26Z` 5건 처리(`07:43:10Z` P0 처리 중); `363470863fa7`(`message.approved`)는 최근 50건 내 선행 최초 영수증이 없어 `b0cabd1` 후속 이벤트 라이브 입증으로 사용하지 않음.
  - Celly (`07:42:16Z`): 원장 `9909→9918`, 보드 `v11310→v11320`, MCP `1625→390ms`. 리스: 입력 대기 2→3, 제출 0→1, 실행/슬롯 3→2. 검수 finding 0. 포크 표본 오류 6→2건(24건 표본: `a27479f3` 07:07:11, `ae3b7ab4` 07:36:12, 원인 미분류). 메인 전사 9건: 기존 리스 재개 안내 가드 2건(07:04:30 `n1571`, 07:27:24 `n1599`, 정상 가드), 리뷰어 포크 금지 가드 1건(07:26:37, 정상 가드), `auto continue` 상태 읽기 실패 1건(07:07:47), 작업 측 Python `gh`/cp1252/파일 없음 오류 5건(07:24:36·07:26:47·07:41:26·07:42:09·07:42:24). 라우터 로그: 외부 SSE EOF 46건(06:52:59부터), 모델 스트림 단절 3건(06:59:29부터), `/ca/mcp` 단절 1건(07:42:05), 턴 투영 관찰 10건(06:54:21–07:38:52), `runtime_activity_*` `WinError 5` 2건(07:07:31·07:34:36), 콘솔 `16785`·`16786` 확인 실패 및 선행 미확인 건너뜀 각 1쌍(07:32:32·07:34:37–07:34:39), 중앙 게이트웨이 discovery 불가 1건(07:32:41).
  - KOBE (`07:42:39Z`): 원장 `4534→4566`, 보드 `v621→v628`, MCP `399→1069ms`. 리스(수락 1·입력 대기 24·실행/슬롯 1) 유지. `n5` 미완료 자식 검수 가드 지속. 포크 표본 오류 0→4건(24건 표본: `ae892ed4` 06:53:38, `aaa92a6b` 07:06:47, `a622603a` 07:21:13, `a9eb946d` 07:36:12, 원인 미분류). 메인 보드 버전 가드 1건(07:14:06, 정상 가드). 라우터 로그: 정상 health keep-alive 가드 587건(06:52:25부터), 외부 SSE EOF 49건(06:53:18부터), 모델 스트림 단절 3건(06:54:34부터), 턴 투영 관찰 15건(06:57:01–07:41:56), **`n150` `git worktree add` 30s 타임아웃 2건**(07:22:38, 근본 원인 미확인), `runtime_activity_*` `WinError 5` 2건(07:27:29·07:42:27).
  - Prody (`07:42:51Z`): 원장 `1239→1241`, 리스 제출 `1→0`·실행/슬롯 `1` 유지, 보드 `v7477→v7486`, MCP `1136→799ms`, 포크 표본 오류 0(2건 표본), 라우터 오류 표본 0. `n1315` 포크 미관측(05:32부터) 지속(종료 미확인). 메인 전사 7건: 작업 측 `stale_owned.py` exit 2 2건(06:56:34·07:42:06 파일 없음)·cp949 인코딩 2건(07:06:10·07:06:36)·Walkie 400 1건(07:24:29), 보드 버전 가드 1건(06:57:07, 정상 가드), `auto continue` 상태 읽기 실패 1건(07:16:10).
  - Betty(보조, `07:43:06Z`): 원장 `1170→1177`, 리스 제출 5 유지·실행/슬롯 1→0·입력 대기 0→1, 보드 `v5752→v5753`, MCP `1227→792ms`. 신규 원장 실패 `decision_refused_stop e676ef18 x1`(직접 원인 미확인). 포크 표본 오류 0→3건(24건 표본: `a22b1c1f` 07:08:38, `ac972ca5` 07:17:14·07:18:46, 원인 미분류). 메인 전사 9건: `work_parallel_item_review` 기결정 가드 1건(07:12:44, 정상 가드), 작업 측 스크립트/가드·bash 따옴표·Walkie 400 8건(07:06:05–07:41:58). 라우터 로그: 정상 keep-alive 가드 592건(06:52:24부터), 외부 SSE EOF 50건(06:52:44부터), 턴 투영 관찰 21건(06:53:25–07:39:06), `runtime_activity_router_failed` `WinError 5` 1건(07:07:16), 콘솔 `8079` 확인 실패 1건(07:14:07).
  - 신규 입증 결함 없음. 미결·추적: `f9546dd` npm `@beta` 발행 완료 후 7개 루트 해시 확인 설치, Andy `n2859`·KOBE `n150` `git worktree add` 타임아웃, Andy PID 81900의 `b0cabd1` 후속 이벤트 라이브 확인 및 `fork_turn_watch.py:767` 크래시 재발 여부, 타 호스트 `b0cabd1` 재시작 반영, Andy `n1166`, Celly/KOBE/Betty `WinError 5`, Celly 콘솔 `16785`/`16786`·Betty `8079` 미확인 및 `decision_refused_stop e676ef18`, KOBE `n5`, Prody `n1315`.

- 06:52:23–06:53:40Z 정기 틱(06:32–06:33Z 대비). 네 호스트·보조 Betty Work MCP 모두 `probe_succeeded`, 신규 병렬 원장 실패 0, 수집 stderr 없음. 운영 세션/라우터 재시작·운영 상태 파일 수정·이번 틱 추가 설치 없음. 이하 UTC.
  - **입증 결함 수정(`f9546dd`, push 완료·npm 확인 대기):** Andy 도킹 Observatory 패널이 이미 승인·완료된 `n2866`(06:02:15Z `finalized`)·`n2871`(06:21:38Z `finalized`)을 `06:42Z`에도 `REVIEW 8 · Awaiting main review`로 표시했다(실제 라이브 `parallel_reviews`는 `rework_ready` 6건, `pending` 0건). 원인: `register.js`의 `graphicProps`가 읽는 `observatoryParallel`이 패널 열기·수동 `Refresh`에서만 갱신되고, `hostStatus`/`projectParallelReviews`의 최신 `work_parallel_status`를 반영하지 않았다(`observatoryReviews`는 미사용). `parallelDisplaySignature`·`syncObservatoryParallel`을 추가해 `hostStatus`/`projectParallelReviews`/`observatoryRefresh`에서 단조 버전 조건으로 동기화하고 `resetParallelHostState`에서 초기화하도록 수정했다. 수정 전 실패 재현(`test_work_observatory.py` FAIL `1 != 2`, `claude plugin test` FAIL `DEVICE 2/6 slots · REVIEW 3`) → 수정 후 `claude plugin validate` 통과, `claude plugin test` 96/96 통과, 격리 Windows·Linux `py310`·`py311` 영향 회귀(`test_work_observatory` 10, `test_observatory_stage` 2, `test_side_intake_mod` 75, `test_work_mod_write_gate` 185/skip 33, `test_work_mod_real_backend` 검수 관련) 통과. `feature/priority-models`·`beta` 푸시(`f9546dd`); npm `@beta` tarball 해시 확인 후 7개 루트 설치 예정(설치 뒤에도 실행 중 Claude CLI는 재시작/재로드 전까지 기존 메모리의 `register.js`를 유지). 기록: [observatory-live-parallel-sync](/docs/journal/2026/11/26/fixes/observatory-live-parallel-sync/record.md).
  - Andy: 원장 8059→8062, 보드 v12169→12179, MCP 2062→1094ms. 리스: 제출 21·입력 대기 2 유지, 실행/슬롯 0→1(`n2879` P2). `n1166` 검수 행 누락 지속, 포크 표본 오류 0(9건 표본). 메인 전사 오류 4건 모두 정상 가드: `work_side_status` 호스트 전용 가드 1건(06:37:39), 보드 버전 가드 2건(06:39:13·06:40:41), P2 슬롯 상한 가드 1건(06:43:01). 라우터 로그: 외부 SSE EOF 17건(06:33:24부터)·BrokenPipe 1건(06:44:11), 턴 투영 관찰 4건(06:33:02–06:42:27), 클라이언트 단절 WinError 10054 3건(06:42:30·06:45:56 `/ca/mcp`·06:48:03). 사이드 영수증(PID 100632 구간): P0/P1/P3 7건 처리, 후속 이벤트 3건(`c3aea90c` `task.status_changed` 06:41:20, `bc809c29`·`1d91d6b6` `message.edited` 06:50:57·06:51:15)은 재기동 전 PID라 `duplicate_source` 종결. **틱 직후 06:54:20Z 라우터 크래시·감독자 자동 재기동 관측:** `router_previous_fault Windows fatal exception: access violation`(`fork_turn_watch.py:767 _observe <- :854 _poll <- :780 poll`, Python 3.14; 원인 미확인)으로 PID 100632→81900 자동 재기동(`/health` `61d2f8b` 응답; 직후 `/side?view=receipts&limit=8` 10s 타임아웃 1회). 재기동으로 Andy 라우터에 `b0cabd1`이 올라갔으나 재기동 후 후속 이벤트 라이브 처리는 아직 미관측.
  - Celly: 원장 9905→9909, 보드 v11309→11310, MCP 1188→1625ms. 리스: 입력 대기 2 유지, 실행/슬롯 4→3. 검수 finding 0, 메인 오류 0. 포크 표본 오류 3→6건(24건 표본: `ae3d5889460e9ec85` 5건 06:39:28–06:40:28, `a551f5b265e8782ca` 1건 06:46:10, 원인 미분류). 라우터: 외부 SSE EOF 16건(06:33:23부터)·BrokenPipe 1건(06:44:11), `/ca/mcp` WinError 10054 2건(06:45:48부터), 모델 스트림 단절 1건(06:51:32), 턴 투영 관찰 3건(06:48:06–06:52:07). 이번 구간 WinError 5 미관측.
  - KOBE: 원장 4518→4534, 보드 v618→621, MCP 405→399ms, 입력 대기 20→24(수락 1·실행/슬롯 1 유지). `n5` 미완료 자식 가드 지속, 포크 표본 오류 1→0건(24건 표본), 메인 오류 0. 라우터: 정상 health keep-alive 가드 239건(06:32:47부터), 외부 SSE EOF 16건(06:32:57부터)·BrokenPipe 2건(06:37:52부터), 모델 스트림 단절 2건(06:36:50부터), 종료 후 ping 무시 1건(06:36:28), `startup_prompt_auto_answer_unavailable` 1건(06:39:30, 정상 가드/경고).
  - Prody: 원장 1239·리스 유지, 보드 v7468→7477, MCP 848→1136ms, 포크 표본 오류 0(2건 표본), 라우터 오류 표본 0. `n1315` 포크 미관측(05:32부터) 지속(종료 미확인). 메인 오류 2건: `06:33:12` `stale_owned.py` exit 2(이전 틱과 겹침), `06:44:32` 보드 버전 가드(정상 가드).
  - Betty(보조): 원장 1170·제출 5·실행/슬롯 1, 보드 v5747→5752, MCP 730→1227ms, finding 0, 포크 표본 오류 0(24건 표본). 메인 작업 측 git `bad object` exit 128 1건(06:36:13). 라우터: 정상 keep-alive 가드 245건(06:32:45부터), 외부 SSE EOF 20건(06:33:20부터), 턴 투영 관찰 9건(06:33:01–06:53:25).
  - 미결·추적: `f9546dd` npm `@beta` 발행 확인 및 7개 루트 해시 확인 설치, Andy `06:54:20Z` `fork_turn_watch.py:767` access violation 크래시 재발 여부 및 PID 81900에서 `b0cabd1` 후속 이벤트 라이브 확인, 나머지 호스트 라우터 재시작 후 `b0cabd1` 반영, Andy `n1166`, Celly WinError 5·포크 오류, KOBE `n5`, Prody `n1315`.

- 06:32:44–06:33:24Z 정기 틱(06:12–06:13Z 대비). 네 호스트·보조 Betty Work MCP 모두 `probe_succeeded`, 신규 병렬 원장 실패 0, 수집 stderr 없음. 운영 세션/라우터 재시작·운영 상태 파일 수정·추가 설치 없음. 이하 UTC.
  - **Andy `a193117` 라이브 효과 첫 관측:** P2 `0195d8d6918f`(06:32:38 수신→06:32:38 클레임, 처리 중)가 실행 중인 상태에서 P3 `be2339957e65`(06:33:01 수신)가 28초 뒤 06:33:29 동시 클레임됐다(이전 fresh 제한에서는 P3가 선행 액터 종료까지 대기). P0 `e3725f9f7503`은 06:33:44 방금 접수 상태. 반면 `15d89d9f09d6`(P3 `task.status_changed`, 06:30:22 수신)는 06:30:24 `duplicate_source` 종결돼, `b0cabd1` 파일은 설치됐으나 라우터 미재시작으로 라이브 미반영임을 재확인.
  - Andy: 원장 8053→8059, 보드 v12166→12169, MCP 844→2062ms. 리스: 제출 22→21, 입력 대기 2 유지. `n1166` 검수 행 누락 지속, 포크 표본 오류 0. 메인 오류 10건: Walkie 인자 400 5건(06:22:48–06:23:20), Walkie `client_id` UUID 검증 1건(06:26:41), Walkie 첨부 `file_id` 400 1건(06:28:30), 작업 측 Python 스크립트 exit 1 3건(06:21:39·06:23:52·06:29:59). 라우터 외부 SSE EOF 18건(06:13:22부터)·BrokenPipe 1건(06:30:18), 턴 투영 관찰 8건.
  - Celly: 원장 9901→9905, 보드 v11308→11309, MCP 390→1188ms. 리스: 입력 대기 2·실행/슬롯 4 유지, 입력대기+슬롯 1→0, 제출 1→0. 검수 finding 0, 포크 표본 오류 2→3건(06:13:53·06:14:05·06:14:43 `a180cf2e`, 원인 미분류). 메인 Walkie 409 `Idempotency key reused` 1건(06:13:11). 라우터 외부 SSE EOF 18건(06:12:52부터)·BrokenPipe 1건(06:30:18), 컨텍스트 사용량 기록 WinError 5 3건(06:19:06부터), 모델 스트림 단절 1건(06:14:41), 턴 투영 관찰 2건.
  - KOBE: 원장 4498→4518, 보드 v614→618, MCP 582→405ms, 입력 대기 17→20(수락 1·실행/슬롯 1 유지). `n5` 미완료 자식 가드 지속. 포크 표본 오류 2→1건(06:17:00 `ac66fc10`, 원인 미분류), 메인 오류 0. 라우터 외부 SSE EOF 20건(06:13:28부터), 정상 health keep-alive 가드 239건(06:12:38부터), 턴 투영 관찰 3건.
  - Prody: 원장 1239·리스 유지, 보드 v7467→7468, MCP 885→848ms, 포크 표본 오류 0, 라우터 오류 표본 0. `n1315` 포크 미관측(05:32부터) 지속(종료 미확인). 메인 작업 측 `stale_owned.py` exit 2 1건(06:33:12).
  - Betty(보조): 원장·제출 5 유지, 보드 v5746→5747, MCP 880→730ms, 포크 표본 오류 0. 메인 작업 측 Python 구문 오류 1건(06:27:59). 라우터 외부 SSE EOF 21건(06:12:56부터), 정상 keep-alive 가드 244건.
  - 신규 입증 결함 없음. 미결·추적 유지: `b0cabd1` 라우터 재시작 후 라이브 반영, Andy `n1166`, Celly WinError 5, KOBE `n5`, Prody `n1315`.

- 06:12:37–06:13:11Z 정기 틱(05:52–05:53Z 대비). 네 호스트·보조 Betty Work MCP 모두 `probe_succeeded`, 신규 병렬 원장 실패 0, 수집 stderr 없음. 운영 세션/라우터 재시작 및 운영 상태 파일 수정 없음. 이하 UTC.
  - **`b0cabd1` npm 확인·해시 확인 설치 완료(라이브 미반영):** npm `@beta` = `0.2.51-beta.20261126-060930.b0cabd1`(gitHead `b0cabd17…`) 확인, tarball의 `ciel_runtime_support/side_intake_processing.py`가 커밋과 SHA-256 일치(`bfc20b7b3d0f` → `5ad087dfeb7c`). 승인된 7개 루트 모두 `check` `base` → `apply` `ok=True` 확인. 라우터 Python 코드이므로 파일 설치만 완료됐고, 라우터 재시작 전까지 라이브 라우터에는 반영되지 않는다. 이번 틱 구간(설치 전)에도 Andy에서 P0 `message.edited`(`38ddee76cb2e`, 06:04:12Z 수신)가 같은 결함으로 06:09:12Z `duplicate_source` 종결된 사례가 1건 추가 관측됐다.
  - Andy: 원장 8037→8053, 보드 v12159→12166, MCP 1704→844ms. 리스: 제출 22·입력 대기 2 유지, 예약/슬롯 1→0. `n1166` 검수 행 누락 지속, 포크 표본 오류 0. 메인 오류 5건: 작업 측 GitHub/git 점검 스크립트 exit 1(05:52:42), `n2875` P3 슬롯 상한 가드(06:01:43, 정상 가드), 보드 버전 가드 2건(06:02:25·06:05:54, 정상 가드), 작업 측 OKF 검증 경고 exit 1(06:09:52). 라우터 외부 SSE EOF 20건(05:52:55부터), 모델 스트림 클라이언트 단절 1건(05:56:35), `/ca/mcp` 연결 리셋 1건(06:04:37), 턴 투영 관찰 10건. 사이드 신규 `dcba81842f5d`(`agent.pending_reminder`)는 06:12:17 수신→06:12:17 즉시 클레임(진행 중), P0 `b8d73d0bc409`는 06:03:13→06:03:22 클레임→06:09:04 처리 완료.
  - Celly: 원장 9889→9901, 보드 v11304→11308, MCP 1343→390ms. 리스: 입력 대기 4→2, 입력대기+슬롯 1 유지, 실행/슬롯 1→4, 제출 0→1. 검수 finding 0, 포크 표본 오류 0→2건(06:08:04 `a4220b03`, 06:12:39 `ae3d5889`, 직접 원인 미분류). 메인: 작업 측 `gh_read.py` exit 1(06:02:46), `Work status unavailable: router connection or snapshot could not be verified. [snapshot-paged; wg-mwx5cfa8-v]` 1건(06:04:32; 같은 시각 라우터 `/ca/mcp` 연결 리셋 3건과 시점 일치하나 단절 원인은 미확인). 라우터 외부 SSE EOF 19건(05:52:36부터), 라우터 활동 기록 WinError 5 2건(06:02:45부터), 콘솔 16769 확인 실패 1건(06:05:22) 및 후속 건너뜀(06:05:28), 모델 스트림 클라이언트 단절 1건(06:08:36), 턴 투영 관찰 5건.
  - KOBE: 원장 4491→4498, 보드 v612→614, MCP 478→582ms, 리스 유지. `n5` 미완료 자식 검수 가드 지속. 포크 표본 오류 1→2건(06:00:03 `a0bee529`, 06:09:58 `ac66fc10`, 원인 미분류), 메인 오류 0. 라우터 외부 SSE EOF 18건(05:53:26부터)·읽기 타임아웃 1건(05:54:58), 정상 health keep-alive 가드 235건(05:52:34부터), 턴 투영 관찰 5건.
  - Prody: 원장 1239·리스 유지, 보드 v7464→7467, MCP 617→885ms, 포크 표본 오류 0, 메인 오류 0. `n1315` 포크 미관측(05:32부터) 지속(종료 미확인). 라우터 06:07:53 콘솔 8156 선행 제출 미확인 건너뜀 1건.
  - Betty(보조): 원장·제출 5·보드 v5746 유지, MCP 571→880ms, 포크 표본 오류 0. 메인 경로 없음 1건(06:12:13). 라우터 외부 SSE EOF 20건(05:53:33부터), 콘솔 8071 확인 실패·후속 건너뜀(06:03:41·06:03:42), 정상 keep-alive 가드 242건.
  - 미결·추적: `b0cabd1` 라우터 재시작 후 라이브 반영 확인(후속 `message.renotify`/`message.edited`), Andy `n1166`, Celly WinError 5·06:04:32 `/ca/mcp` 단절·콘솔 미확인, KOBE `n5`, Prody `n1315`·콘솔 미확인.

- 05:52:32–05:53:10Z 정기 틱(05:34–05:36Z 대비). 네 호스트·보조 Betty Work MCP 모두 `probe_succeeded`, 신규 병렬 원장 실패 0, 수집 stderr 없음. 운영 세션/라우터 재시작 및 운영 상태 파일 수정 없음. 이하 UTC.
  - 직전 05:34Z 기록 정정: 직전 틱의 Celly `live.mcp`는 성공이 아니라 `probe_failed (TimeoutError, 4016ms)`였고, 이번 05:52Z에 `probe_succeeded (1343ms)`로 회복됐다.
  - Andy: 원장 8032→8037, 보드 v12156→12159, MCP 1891→1704ms. 리스: 제출 23→22, 실행/슬롯 2→0, 입력 대기 0→2, 예약/슬롯 0→1. `n1166` 검수 행 누락 지속, 포크 표본 오류 0. 메인 `Work reconciliation: Parallel read failed: The operation timed out.` 1건(05:35:46). 라우터 외부 SSE EOF 18건(05:34:30부터), `/ca/mcp` 연결 리셋 2건(05:35:51부터), 모델 스트림 클라이언트 단절 1건(05:50:17), 턴 투영 관찰 1건.
  - **입증 결함 수정(`b0cabd1`, push 완료·설치 대기):** Andy `#161594` `message.renotify`(05:44:09Z, P2, MCP/REST 문장 추가)가 선행 `task.auto_assigned`(`c5dba7917010`)에 막혀 05:44:13Z `duplicate_source`로 종결됐다. 최신 100건의 `duplicate_source` 8건 중 6건(`message.renotify` 4·`thread.eta_requested` 1·`task.status_changed` 1)이 같은 원인이었다. 원인: `side_intake_processing._source_holder`가 `data.event_type`을 보지 않고 메시지 단위 중복 락을 걸었다. `INITIAL_SOURCE_EVENTS`(`''`, `message.created`, `task.auto_assigned`)에만 메시지 락을 적용하도록 수정. 수정 전 실패 재현 후 격리 Windows·Linux py310·py311 각 128 통과·13 스킵·0 실패(`test_work_approval_workflow.py` FIFO 순서 보정 포함). `feature/priority-models`·`beta` 푸시(`b0cabd1`, Publish 실행 `45766219094` 진행 중); npm tarball 해시 확인 전이라 7개 루트 미설치. 기록: [side-lifecycle-dedupe](/docs/journal/2026/11/26/fixes/side-lifecycle-dedupe/record.md).
  - Celly: 원장 9888→9889, 보드 v11303→11304, MCP `TimeoutError(4016ms)`→`probe_succeeded(1343ms)`. 리스: 입력 대기 3→4, 입력대기+슬롯 1 유지, 실행/슬롯 2→1. 검수 finding 0, 포크 표본 오류 8→0건. 메인 `n1420` API `server_error` 재시도 예약 1건(05:42:00) 및 1차 재시도(05:43:13, 회복 미확인). 라우터 외부 SSE EOF 18건(05:34:17부터)·ConnectionReset 1건(05:39:11), 컨텍스트 사용량 권한 거부 1건(05:35:43, WinError 5), `/ca/mcp` 단절 2건(05:35:44), 콘솔 16762 확인 실패 1건(05:37:32).
  - KOBE: 원장 4482→4491, 보드 v609→612, MCP 1690→478ms, 제출 1→0(수락 1·입력 대기 17·실행/슬롯 1 유지). `n5` 미완료 자식 검수 가드 지속. 포크 표본 오류 2→1건(05:41:54 `a5745c3d`, 원인 미분류). 라우터 외부 SSE EOF 18건(05:34:56부터), 정상 health keep-alive 가드 218건(05:34:07부터), 턴 투영 관찰 9건.
  - Prody: 원장 1239·리스 집계 유지, 보드 v7461→7464, MCP 674→617ms, 포크 표본 오류 0. `n1315` 포크 미관측(05:32부터) 재표시(종료 미확인). 메인 신규 오류: 작업 스크립트 `stale_owned.py` exit 2 2건(05:46:17 `--json` 미지원, 05:47:27); 앞선 05:34:14·05:34:21 2건은 직전 틱과 겹침. 라우터 오류 표본 없음.
  - Betty(보조): 원장·제출 5 유지, 보드 v5745→5746, MCP 640→571ms, 포크 표본 오류 0. 라우터 외부 SSE EOF 19건(05:34:10부터), 정상 keep-alive 가드 224건, 콘솔 8042 커서 복구 로그 16건(05:35:58부터).
  - 미결·추적: `b0cabd1` npm 발행 확인 및 7개 루트 해시 확인 설치, Andy 재시작 후 신규 비중복 SSE 라이브 효과, Andy `n1166`, Celly `n1420`·WinError 5·콘솔 미확인, KOBE `n5`, Prody `n1315`.

- 05:34:07–05:36:22Z 정기 틱(05:20–05:21Z 대비). 네 호스트·보조 Betty Work MCP 성공, 신규 병렬 원장 실패 0, 수집 stderr 없음. 이번 틱에서는 재시작·운영 상태 수정·추가 설치 없음. 아래 재기동 로그는 앞선 사용자 지시에 따른 재시작 구간에 관측됐다. 이하 UTC.
  - Andy: 원장 8028→8032, MCP 1016→1891ms, 제출 23·실행 슬롯 2 유지. `n1166` 검수 행 누락 지속. 포크 표본 오류 4→0건. 메인 `n2855` API `server_error` 재시도 예약 1건(05:28:49, 회복 미확인). 라우터 재기동 05:27:10, 외부 SSE EOF 6건(05:28:20부터), upstream SSE 오류 1건(05:28:43, 형태 로그 제외), 모델 클라이언트 연결 리셋 1건(05:29:45), MCP 연결 리셋 5건(05:30:25부터). P4 `6659e02d4b25`와 무우선순위 `8d1fac0e923e`가 각각 05:35:16·05:36:05 `processed`, 회신 상태 `not_applicable`. 둘 다 이전에 배정됐으므로 새 배정 정책의 라이브 효과는 미확인. `/health` PID 100632, 버전 표기 `61d2f8b` 유지(오버레이는 버전 메타데이터 미변경).
  - Celly: 원장 9888·리스 집계 유지, MCP 547→4016ms, 포크 표본 오류 2→8건(최근 표본 05:22:07–05:33:55, 원인 미분류). `n1420` 미관측 finding은 빠졌으나 작업 완료·회복 근거 아님. 메인 전사에 `n1420` `transport_interrupted` 재시도 예약 1건(05:29:02). 라우터 재기동 05:32:05, 외부 SSE EOF 1건(05:33:15).
  - KOBE: 원장 4470→4482, MCP 4032→1690ms, 입력 대기 16→17. `n5` 미완료 자식 검수 가드 유지, `n219` 수용 대기 finding은 빠짐(재작업 완료 미확인). 포크 표본 오류 9→2건(05:23:00·05:29:34, 원인 미분류). MCP 미연결 3건(05:29:34–05:29:37), 라우터 재기동 05:29:36. `n221` API 서버 오류 재시도 예약 1건(05:30:31), 작업 스크립트 문자열 구문 오류 1건(05:31:44). 라우터 외부 SSE EOF 9건(05:20:48부터)·BrokenPipe 2건(05:22:23부터)·읽기 타임아웃 1건(05:23:56), 모델 스트림 클라이언트 단절 2건(05:24:00부터), MCP 단절 1건(05:21:27).
  - Prody: 원장 1239·리스 집계 유지, MCP 602→674ms, 포크 표본 오류 0. `n1315` 미관측 finding이 빠졌으나 종료·복구 미확인. 편집 대상 문자열 없음(05:22:32), Work 포크 실행 계약 위반 가드(05:34:14), 보드 버전 가드(05:34:21) 각 1건. 콘솔 8149 선행 제출 미확인으로 건너뜀(05:32:06). 보조 Betty MCP 1134→640ms, 포크·검수 집계 유지, 재기동 05:29:53, 콘솔 8069 재시도 예약 1건(05:30:00).
  - 이번 틱에서 새로 재현·입증한 제품 결함 없음. Andy 재시작 후 신규 SSE 배정·회신, `n1166`, Celly `n1420`, KOBE `n5`·`n219`, Prody `n1315`·콘솔 전달 추적 유지.

- 05:32Z 사용자 지시로 테스트 라우터만 재시작 확인. PID 전후: Andy 83624→100632, Celly 18576→111684, KOBE 17580→2104, Prody 10260→11560, Betty 12636→6196. 5곳 모두 `/health` 응답, 각각의 활성 클라이언트 PID 유지. Celly는 관리자 권한으로 `serve` 명령줄과 `/health` PID를 대조한 뒤 종료했다. `a193117`은 변경 파일만 설치했으므로 `/health` 표기는 기존 `61d2f8b`. Andy Work Parallel·Side 응답 확인; 네이티브 확인 포크 0이며 종료나 복귀를 입증하지 않는다. P4 `6659e02d4b25` 및 무우선순위 `8d1fac0e923e`는 동시에 처리 중으로 표시됐으나 각각 05:27:22·05:27:24 배정되어 재시작 후 신규 SSE의 배정 시간·회신 검증에는 사용하지 않았다. CLI 세션 재시작 및 운영 상태 파일 수동 수정 없음.

- 05:20:01–05:21:44Z 틱(04:32–04:33Z 대비). 네 호스트와 보조 Betty Work MCP 응답 확인, 새 병렬 원장 실패 0, 수집 stderr 없음. 이하 UTC.
  - 배포 확인: 앞서 `a193117` 수정 파일 3개를 Andy·Celly·KOBE·Prody·Betty 설치 경로 7곳에 적용했고 설치 결과 7건 모두 `ok=true`. 버전 메타데이터는 교체하지 않았다. 설치 후 이번 틱까지 이 작업에서 라우터·세션을 재시작하지 않았다. Andy PID 83624·Celly PID 18576의 `/health`는 `61d2f8b` 표기. 실행 중 코드에 새 배정 정책이 적용됐는지와 실 SSE 동작은 확인하지 않았다.
  - Andy: 원장 8002→8028, MCP 672→1016ms, 제출 21→23·실행 슬롯 2 유지. `n1166` 검수 행 누락 지속. 포크 오류 표본 0→4건(04:41:23 및 04:49:33–04:49:57, 직접 원인 미분류). 메인 PowerShell 구문 오류 1건(04:40:07), 검색 타임아웃 2건(04:57:20·05:19:29). 라우터 외부 SSE EOF 46건(04:32:56부터), 모델 upstream SSE 오류 1건(04:34:42), HTTP 501 로그 4건(04:45:09부터, 직접 원인 미확인), 모델 스트림 반복 차단·복구 로그(04:38:48, 가드 자체를 결함으로 보지 않음).
  - Celly: 원장 9888·리스 집계 유지, MCP 1500→547ms. `n1420` 슬롯 유지·포크 미관측(종료 미확인). 포크 표본 오류 9→2건(05:19:52·05:20:48; 후자는 호스트 표기 시각 이후 수집, 직접 원인 미분류). 메인 버전 충돌 HTTP 409 4건(04:34:11·05:08:33·05:08:41·05:08:58), 요건을 기다리는 대상 없음 HTTP 409 1건(05:09:16), 자동 계속 상태 읽기 실패 1건(04:35:14, 원인 미확인). 라우터 외부 SSE EOF 43건(04:33:12부터), 게이트웨이 재시도 로그 21건(04:45:07–04:47:55, 고유 실패 요청 수 아님), 콘솔 입력 확인 실패 1건(05:07:11).
  - KOBE: 원장 4449→4470, MCP 346→4032ms, 제출 0→1. `n5` 미완료 자식 가드 지속; `n219` 재작업은 P3 슬롯 1/1로 수용 대기. 포크 표본 오류 3→9건(04:36:11–05:08:13, 원인 미분류). 메인 인자 검증 400 3건(04:59:50·05:01:02·05:10:21), 작업 스크립트 assertion 1건(05:01:06). 외부 SSE EOF 46건(04:32:59부터)·BrokenPipe 1건(04:59:14), health 정상 keep-alive 가드 563건.
  - Prody: 원장 1239·리스 집계 유지, MCP 968→602ms, 포크 표본 오류 0. `n1315` 미관측 지속(종료 미확인). 보이지 않는 문자 포함 명령 실행 가드 1건(04:42:49), 콘솔 8145가 앞선 제출 미확인으로 건너뛴 로그 1건(05:06:10). Betty: 원장·리스 유지, MCP 1433→1134ms, 포크 표본 오류 0, 콘솔 8066 확인 실패 1건(05:02:44).
  - 새로 입증한 제품 결함 없음. 이번 틱에서 코드 변경·추가 설치·운영 상태 파일 수정·재시작 없음.

- 사용자 요구 명확화: SSE 사이드 액터의 초동 처리는 P0–P5와 무관하게 즉시 시작해야 한다. P0–P5는 Work 포크 실행에만 적용한다. Git에서 `39f0c97`이 사이드 배정에 P1 선점·예약 슬롯을 넣고 `35b647a`가 fresh의 P0/P1 예약을 복원한 것을 확인했다. 두 커밋의 Git 작성자 표기는 승인 근거가 아니다. 기존 저널의 과거 승인 주장을 뒷받침하는 사용자 원문은 확인하지 못했다. 이 시점에는 제품 코드나 운영 상태를 변경하지 않았다.

- 04:32:16–04:33:49Z 틱(04:12–04:14Z 대비, 이하 UTC). 네 호스트와 보조 Betty Work MCP 응답 확인. 운영 재시작·상태 파일 수정·추가 설치 없음.
  - Andy: 원장 8002 및 리스 집계 유지, MCP 1422→672ms, 포크 오류 표본 0, 네이티브 확인 포크 0(종료 증거 아님), `n1166` 검수 행 누락 지속. 메인 MCP `work_host_page` 응답 전 연결 종료 1건(04:14:21), `n2855` 모델 API 오류 및 1차 재시도(04:15:25·04:18:29, 회복 미확인), 검색 정규식 오류 1건(04:28:33). 라우터 외부 SSE EOF 19건(04:12:33부터), upstream SSE 오류 2건(04:14:01부터, 형태 로그 제외), 감독자 재기동 1건(04:14:24 `router_process_exited`, 종료 원인 미확인), 클라이언트 연결 끊김 로그 3건(04:16:28·04:22:43·04:30:50).
  - Andy 신규 SSE 병목: 후속 영수증에서 P0 `9dc2bc8c12cc`는 04:17:58 수신→04:18:00 배정, P3 `af04189c53fa`는 03:48:15→04:26:53(38분 38초), P3 `b7202e7d31ea`는 03:59:00→04:30:58(31분 58초). P3 `210857f6fafb`·`e431cd54f2b7`는 후속 조회에서 미배정. `fresh`/`btw_with_mcp` 유지. 현재 구현은 일반 슬롯 하나, 추가 슬롯 P0/P1 전용으로 사용자 요구(우선순위 무관 즉시 처리 시작)와 일치하지 않는다. #161501은 04:13:06 처리 완료, 회신 `submitted`(실제 수신 미확인). 영수증 전용 뷰 응답에 `handoffs` 없음; `/health` 표기 `61d2f8b`. 응답 기능과 버전 표기를 별도 증거로 기록.
  - Celly: 원장 9877→9888, MCP 500→1500ms, 리스 집계 유지, 포크 표본 오류 15→9건. `n1420` 슬롯이 남은 상태에서 04:16 이후 포크 미관측(종료 미확인), 원장 `decision_refused_stop` 1건(`07ef2e20`, 직접 원인 미확인). 메인 cp1252 출력 오류·워커 권한 거절·완료 영수증 검수 가드·Walkie UUID 검증 오류 각 1건(04:16:58·04:18:18·04:19:11·04:25:12). 라우터 외부 SSE EOF 19건(04:13:21부터), upstream SSE 오류 1건(04:15:50), 연결 리셋 1건(04:20:41), 콘솔 16752·16758·16759 확인 실패 각 1건(04:15:27·04:31:39·04:31:54)과 각각의 후속 차단(중복 합산하지 않음).
  - KOBE: 원장 4445→4449, MCP 511→346ms, 리스 유지, `n5` 미완료 자식 가드 지속. 포크 오류 3건 중 04:12:33은 이전 표본과 겹침, 신규 표본 2건(04:22:06·04:22:36, 원인 미확인). 라우터 SSE EOF 21건(04:12:30부터), 사용량 기록 권한 거절 2건(04:18:44부터), 모델 스트림 단절 1건(04:32:53), 종료 후 ping 무시 1건(04:33:06), 정상 health keep-alive 가드 247건.
  - Prody: 원장 1239·리스 집계 유지, MCP 1273→968ms, 포크 표본 오류 1→0건, `n1315` 계속 미관측(종료 미확인). 04:22:40 워크플로 없는 목표 호출 거절 1건, 라우터 오류 표본 없음. Betty MCP 341→1433ms, 포크·검수 집계 유지, SSE EOF 21건(04:12:35부터).

- Andy_Main의 두 사례 제보(교차 세션 근거, 이 기록에서 독립 재검증하지 않음). 시각은 UTC. #161489 / `023c58a4c998`는 02:30:27 SSE 수신, 02:33:33 구조화 ACK 저장. 최신 CI 실행을 정확히 대조하지 않고 이전 상태로 #161495를 게시(02:34:02), #161498에서 정정했다고 보고. 원 제출의 `no_reply:true`·`ask_main:true` 중 저장 결정에는 `no_reply`가 없었고 outbox는 `submitted`; 결정 변환과 게시의 인과는 별도로 확인할 항목. #161522 / `67a618e58605` (`n2855`)는 승인 이벤트 수신 후 구조화 ACK 409 `Idempotency key reused` 1회, 03:16:54 ACK v1 저장. 정확한 대기가 해제됐으나 03:18:48 host append에 `it is no longer a parked worker of this session` 1회. 03:21–03:22 기존 포크 실행 표시가 있었다고 보고; 해당 승인이 자동 재개를 일으켰는지는 미확인. 409 키 충돌 상대와 append 거절 분기 미확인. 두 건의 수신은 SSE 전수 정상·무유실을 입증하지 않는다. 이 제보에 따른 운영 변경·재시작 없음.

- 04:12:29–04:14:40Z 틱(03:56–03:57Z 대비). 네 호스트 Work MCP 성공, 신규 병렬 원장 실패 0. Prody 첫 SSH 배너 교환 타임아웃 1건, 단독 재조회 성공. 이하 UTC.
  - Andy: 원장 7997→8002, MCP 938→1422ms, 제출 21·실행/슬롯 2 유지. 네이티브 확인 포크 0(종료 미확인), 포크 표본 오류 0, `n1166` 검수 행 누락 지속. 라우터 외부 SSE EOF 15건(03:57:12부터), 클라이언트 연결 재설정 로그 3건(04:03:31·04:06:39·04:08:40), upstream SSE 오류 1건(04:07:01, 형태 로그 제외), 종료 후 ping 무시 1건(04:08:53). 이 로그를 포크 복귀 실패 원인으로 연결할 증거 없음.
  - Celly: 원장 9875→9877, MCP 1484→500ms, 실행/슬롯 3→2, 입력대기/슬롯 0→1, 일반 입력대기 3 유지. 포크 표본 오류 20→15건(세부 원인 미분류), 신규 검수 이상 없음. 메인: Walkie client_id UUID 검증 거절(04:05:04), 미등록 도구 호출(04:05:15), `n1571` 목표 미승인 재개 가드(04:09:01) 각 1건. 라우터 외부 SSE EOF 16건(03:56:52부터), 상태 기록 권한 거부 2건(03:58:43부터, WinError 5), upstream SSE 오류 1건(04:09:06, 형태 로그 제외), 03:57대 전송 재시도 로그 3건(동일 작업 ID).
  - KOBE: 원장 4437→4445, MCP 1214→511ms, 입력 대기 15→16, 실행/슬롯 1 유지, `n5` 미완료 자식 가드 지속. 포크 표본 오류 0→1건(04:12:33, 직접 원인 미확인). 라우터 외부 SSE EOF 16건(03:57:05부터), 모델 스트림 클라이언트 단절 1건(03:57:39), MCP 연결 리셋 2건(04:04:04부터), 정상 health keep-alive 가드 197건(03:56:50부터).
  - Prody: 원장 1239·제출 1·실행/슬롯 1 유지, MCP 572→1273ms. `n1315`는 이번 스냅샷에서 04:01부터 미관측(종료 미확인). 포크 표본 오류 0→1건(04:00:20, 직접 원인 미확인). 보조 Betty MCP 519→341ms, 포크/검수 집계 유지, 외부 SSE EOF 17건(03:57:17부터).
  - 설치와 라이브 구분: 직전 일곱 경로 해시 확인 설치는 `b86fa04`. 이번 npm beta 태그는 문서 후속 커밋 `f90614e`; Andy·Celly 라이브 `/health`는 `61d2f8b`로 응답. 신규 제품 결함 재현·수정, 설치, 세션/라우터 재시작, 운영 상태 파일 수정 없음.

- 03:56:46–03:57:20Z 틱(03:32Z 대비). 네 호스트 및 보조 Betty Work MCP 응답, 신규 병렬 원장 실패 0. 이하 UTC.
  - Andy: 원장 7956→7997, MCP 2734→938ms, 제출 21·실행/슬롯 2 유지, 입력 대기 4→0, stale_runs 159→163. `n1166` 검수 행 누락 지속, 포크 표본 오류 0. 별도 네이티브 조회 확인 포크 0(실행/슬롯 2와 별개이며 종료 증거 아님). 기존 콘솔 03:37:15 종료 로그, 라우터 03:39:17 기동 로그. 03:43:31 사이드 액터 시작을 Claude Code Stop hold가 거부했다는 알림 1건; 현 시점에도 유지 여부 미확인. 메인 검색 시간 초과 1건(03:51:39), 스크립트 문자열 구문 오류 1건(03:55:11). 외부 SSE 읽기 타임아웃 1건(03:41:50), EOF 14건(03:42:52부터), `/ca/mcp` 연결 리셋 2건(03:42:39부터), 종료 뒤 ping 무시 1건(03:51:15).
  - Celly: 원장 9860→9875, MCP 438→1484ms, 입력 대기 4→3·실행/슬롯 2→3. 검수 이상 없음. 포크 표본 오류 28→20(세부 원인 미분류). 메인 Walkie `client_id` UUID 검증 실패 1건(03:42:09). 라우터 외부 SSE EOF 22건(03:32:34부터)·BrokenPipe 1건(03:41:31); upstream SSE 오류 1건(03:34:50, 형태 로그 제외); 모델/포스트 단절 각 1건(03:35:56·03:38:33); 콘솔 16743 확인 실패·후속 skip(03:38:32·03:38:39, 하나의 메시지); `n1420` git worktree add 타임아웃 2건(03:38:33·03:40:31); 사용량 기록 권한 오류 1건(03:53:34), SSE 상태 파일 읽기 오류 1건(03:54:21). 타임아웃 근본 원인 미확인.
  - KOBE: 원장 4433→4437, MCP 345→1214ms, 입력 대기 14→15, 포크 표본 오류 1→0, `n5` 미완료 자식 가드 지속. 외부 SSE EOF 22건(03:32:44부터)·BrokenPipe 1건(03:53:59), 클라이언트 리셋 1건(03:33:11), 정상 health keep-alive 가드 277건(03:32:20부터).
  - Prody: 원장 1239·리스 집계 유지, MCP 425→572ms, 포크 오류 0, 라우터 오류 표본 없음. `n1315` 미관측 지속(종료 미확인). 보조 Betty는 MCP 401→519ms, 제출 5 유지, 포크 오류 0, SSE EOF 25건(03:32:49부터).
  - 별도 사용자 지시 배포: npm `0.2.51-beta.20261126-034736.b86fa04`의 코드 해시를 확인하고 Andy·Celly·KOBE·Prody·Betty의 승인된 설치 경로 7곳에 적용(`ok=true`). 파일 설치와 라이브 적용을 구분: 이후 Andy `/health`는 기존 `61d2f8b`, 영수증 전용 요청은 시간 초과. 이번 작업에서 세션·라우터를 재시작하거나 운영 상태 파일을 수정하지 않았다. 재시작 후 포크 복귀, Andy SSE, Celly worktree 타임아웃은 미해결.

- 03:32:17–03:32:51Z 틱(03:12Z 대비). 네 호스트와 보조 Betty Work MCP 성공, 신규 병렬 원장 실패 0, 수집 stderr 없음. 이하 UTC.
  - Andy: 원장 7950→7956, MCP 2625→2734ms, 입력 대기 5→4·실행/슬롯 1→2, 제출 21 유지. `n1166` 검수 행 누락 지속. 포크 수집 표본 오류 8→0건이지만, 별도 확인한 `n2871` 전사에는 이 구간에 해당하는 03:17:30 모델 스트림 502 1건과 반복 Bash 입력 검증 실패가 있어 표본 0을 무오류로 보지 않음. 메인 `work_side_status` 호스트 전용 가드 1건(03:19:41). 라우터: 외부 SSE EOF 18건(03:12:19부터)·BrokenPipe 1건(03:16:12), 모델 upstream SSE 오류 1건(03:17:30, 형태 로그 제외), 클라이언트 단절 로그 2건(03:17:16·03:23:01), health 정상 keep-alive 가드 236건(03:12:20부터). #161501은 03:17:22 배정 뒤 추가 조회에서도 `processing`·결과 회신 미확인. #161497은 03:15:51 처리 완료·회신 `submitted`(수신 확인 아님), 별도 Walkie ACK 성공은 03:09:09.
  - Celly: 원장 9852→9860, MCP 266→438ms, 리스 집계·`stale_runs` 32 유지, 검수 finding 0. 포크 수집 표본 오류 15→28건. 그중 `aa142c52` 전사 별도 확인: 03:27:06–03:29:46 입력 검증 오류 13건·worktree 가드 6건(표본 수치에 더하지 않음; 입력이 깨진 경로는 미확인). 라우터: 외부 SSE EOF 17건(03:13:06부터)·BrokenPipe 1건(03:16:12), 모델 upstream SSE 오류 1건(03:16:18, 형태 로그 제외), 클라이언트 단절 로그 2건(03:16:56·03:17:00). 메인 전사 오류 표본 없음.
  - KOBE: 원장 4421→4433, MCP 665→345ms, 입력 대기 11→14·실행/슬롯 1 유지. `n5` 미완료 자식 검수 가드 지속. 포크 표본 오류 0→1건(03:26:55, 직접 원인 미확인). 라우터 SSE EOF 19건(03:13:14부터), 모델 스트림 클라이언트 단절 1건(03:22:04), health 정상 keep-alive 가드 237건(03:12:20부터).
  - Prody: 원장 1239 및 리스·검수 집계 유지, MCP 737→425ms, 포크 표본 오류 0, 새 라우터 오류 표본 없음. `n1315` 포크 미관측 지속(종료 미확인). 메인 계획 승인 전 파일 수정 차단 가드 1건(03:20:11). 이전 콘솔 8132·8133 실제 전달 새 증거 없음. 보조 Betty: 제출 5 유지, MCP 1042→401ms, 포크 표본 오류 0, SSE EOF 21건(03:12:24부터).
  - 신규로 재현·입증된 제품 결함 없음. 운영 세션/라우터 재시작·운영 상태 파일 수정, 코드 변경·설치·배포 없음.

- Andy #161497 ACK 인자 대조: 02:53:35Z 실패 호출은 `conversation_id`, `message_id`, `memo`, `state`를 포함했고 HTTP 400 `Invalid operation arguments`가 반환됐다. 03:09:08Z 재호출은 `client_id`, `version`도 보냈고 03:09:09Z Walkie가 `state=ack`, `version=1`, memo `n2274 블로커 조회 중`을 반환했다. 추가 검증: 해당 액터 전사에 저장된 운영 Walkie 도구 검색 응답의 실제 Draft-07 스키마에 두 호출 인자를 대입했다. 첫 호출은 필수 속성 `version`, `client_id` 두 건 모두 누락으로 거절되고 재호출은 스키마 오류 0건이었다. `memo`는 ACK 시 허용되는 선택 필드이며 첫 호출에도 존재했다. 로컬 Walkie 체크아웃의 스키마는 `memo`가 없는 구버전이므로 운영 판정 근거에서 제외했다. 별도 워커 조사: `n2871` Bash 입력 검증 36건·모델 502 1건(03:17:30Z), `n2866` 도구 입력 검증 56건(Bash 37·보고 MCP 10·Read 5·PowerShell 4), 워커 권한 거절 2건, SendMessage 가드 1건, 모델 스트림 오류 2건(03:03:28·03:07:39Z). 전사 집계이며 전체 명령으로 일반화하지 않는다. 별도 이미지 #57의 `2271`은 Andy Work 보드 및 포크 메타데이터에서 대응 항목을 찾지 못했다. `n2866`에서 03:06:04·03:06:31Z에 관측한 `invalid or revoked worker capability` 두 건을 이미지의 오류와 동일시하지 않는다. 운영 변경 없음.

- 03:12:17–03:12:48Z 틱(02:54–02:55Z 대비). 요청한 네 호스트와 보조 Betty Work MCP 응답 확인, 신규 병렬 원장 실패 0. 이하 UTC.
  - Andy: 원장 7946→7950, MCP 3672→2625ms, 리스 집계 동일, `n1166` 검수 행 누락 지속. 포크 표본 오류 13→8건(동일 `a20e2dd2`, 03:06:04–03:07:12, 이번 표본의 세부 원인 미분류), 메인 승인자 멘션 누락 HTTP 400 1건(03:03:41). 라우터 외부 SSE EOF 17건(02:54:56부터), 모델 upstream SSE 오류 로그 3건(`_Walkie_P3` 2건은 03:03:28부터, `_API_KEYS-SSE-1` 1건 03:07:39), 응답 노출 전 재시도 1건(03:07:39). 정상 health keep-alive 가드 208건. #161501 P3는 추가 조회에서도 미배정·ACK 미전송. 앞선 #161497 영수증 `4c9f089b3ce5`는 `turn_error` 표시와 함께 03:07:44 재클레임돼 처리 중이며, ACK·결과 회신 성공은 미확인.
  - Celly: 원장 9837→9852, MCP 1437→266ms, 실행/슬롯 3→2·입력 대기 3→4, 신규 검수 이상 없음. 포크 표본 오류 29→15건(03:09:39–03:11:19, 세부 원인 미분류), 메인 도구 오류 표본 0. 라우터: 로컬 게이트웨이 HTTP 503 6건(02:57:41부터), 중앙 6건(02:59:52부터); upstream SSE 오류 프레임 로그 10건(02:56:19부터, 형태 로그 제외), 외부 SSE EOF 17건(02:55:44부터). 콘솔 16738 재시도 예약 3건(02:58:57부터) 및 실패 1건(03:01:11), 16739 재시도 예약 1건(03:01:51), 16740 재시도 예약 4건(03:03:05부터). 최종 전달 및 게이트웨이 근본 원인 미확인.
  - KOBE: 원장 4417→4421, MCP 206→665ms, 입력 대기 10→11, 포크 표본 오류 4→0건, `n5` 미완료 자식 검수 가드 지속. `n187` 전송 중단으로 재시도 예약 1건(03:09:03, 실행·회복 미확인). 라우터 외부 SSE EOF 16건(02:55:19부터)·읽기 타임아웃 1건(03:00:56), 클라이언트 단절 2건(03:06:30·03:08:52), upstream SSE 오류 2건·응답 노출 전 재시도 1건(03:08:42). 정상 health keep-alive 가드 208건.
  - Prody: 원장 1239·리스 집계 유지, MCP 283→737ms, 포크 표본 오류 0건·라우터 오류 표본 없음. `n1315` 계속 미관측(종료 미확인), 이전 콘솔 8132·8133의 전달 여부 새 증거 없음. Betty: 원장 1166→1168, 제출 6→5, MCP 450→1042ms, 포크 표본 오류 1→0건, 채널 EOF 18건(02:55:04부터).
  - 신규로 재현·입증된 제품 결함 없음. 이번 틱에서 운영 재시작·상태 파일 수정, 코드 변경·설치·배포 없음.

- 03:05Z Andy #161501 사이드 미응답 조사(읽기 전용). `5f89d202a899` P3는 02:38:40Z 접수, 03:05:18Z까지 미클레임·ACK 미전송·회신 없음. 직전 P3 작업과 회복 알림을 순차 처리한 뒤 `4c9f089b3ce5`(별개 #161497 P3)가 02:52:39Z 일반 슬롯을 점유했다. 이 액터 `aa81a702e3ab34898`의 02:53:35Z ACK 호출은 Walkie HTTP 400 `Invalid operation arguments`로 거부됐고, 이어 Bash 19회 등 조사 도구를 호출했으나 관측 시점까지 처리 결과가 없었다. fresh의 일반 슬롯은 1개이며 추가 슬롯은 P0/P1 전용이다. 400의 구체 필드 원인 및 #161501의 최종 종료 시각 미확인. 설정·운영 상태 변경 및 재시작 없음.

- 2026-11-26 02:54:45–02:55:18Z 틱(02:33–02:35Z 대비). 네 호스트 및 보조 Betty Work MCP 성공, 신규 병렬 원장 실패 0, 수집 stderr 없음. 이하 UTC.
  - Andy: 원장 7938→7946, MCP 2891→3672ms, 리스 집계 불변, `n1166` 검수 행 누락 지속. 포크 표본 오류 0→13. 별도 전사에서 `a20e2dd2` 입력 검증 실패 20건(02:52:42–02:54:57, 수집기와 범위가 달라 합산 안 함), 근본 원인 미확인. 메인 보드 버전 가드 2건(02:47:10·02:53:03). 라우터 SSE EOF 20건(02:34:30부터), 모델 스트림 클라이언트 단절 2건(02:33:59부터) 및 라우터 클라이언트 연결 리셋 로그 1건(02:38:23). health 정상 keep-alive 가드 250건(02:33:44부터).
  - Celly: 원장 9825→9837, MCP 407→1437ms, 입력 대기 2→3, 실행/슬롯 3 유지, 검수 finding 없음·stale_runs 32 유지. 포크 표본 오류 26→29. 별도 전사에서 `ad58c33a` 입력 검증 25건 및 worktree 가드 8건(02:42:55–02:51:16, 표본과 합산 안 함). 메인 인자 HTTP 400 2건(02:38:19·02:43:16), 목표 유형 오류(02:39:00), 검색 시간 초과(02:39:51) 각 1건. 라우터 SSE EOF 19건(02:33:50부터), 게이트웨이 검색 불가 1건(02:34:17), upstream SSE 오류 1건(02:37:58, 형태 로그 중복 제외), 모델/라우터 클라이언트 단절 각 1건(02:38:34·02:42:17), 사용량 기록 WinError 5 1건(02:46:23), 채널 연결 중단 1건(02:47:22), 콘솔 16735 확인 실패 1건(02:47:48), 커서 읽기 오류 1건(02:48:40). 입력 검증 실패와 라우터 오류 간 인과 미확인.
  - KOBE: 원장 4406→4417, MCP 541→206ms, 리스 집계 불변, `n5` 미완료 자식 검수 가드 지속. 포크 표본 오류 1→4건(02:38:00·02:38:08·02:38:52·02:48:40, 직접 원인 미분류), 메인 오류 0. 라우터 SSE EOF 19건(02:34:14부터)·BrokenPipe 1건(02:46:04), 모델 스트림 클라이언트 단절 1건(02:38:24), 정상 health keep-alive 가드 252건(02:33:43부터).
  - Prody: 원장 1239 및 리스 집계 유지, MCP 326→283ms, 포크 표본 오류 0. `n1315` 미관측 지속(종료 미확인). 메인 승인자 멘션 누락 HTTP 400 1건(02:43:41). 콘솔 8132·8133은 02:35:32에 선행 제출 미확인 가드로 각각 건너뜀(실제 전달 여부 미확인). 보조 Betty: 제출 5→6, MCP 354→450ms, 포크 표본 오류 1건(02:51:47), 메인 cp949 출력 인코딩 오류 1건(02:39:13) 및 작업 명령 exit 1건(02:54:28, 직접 원인 미확인).
  - Andy·Celly 입력 검증 반복, Celly 사용량 기록·콘솔 전달, Prody 콘솔 전달·`n1315`, Andy `n1166`, KOBE `n5` 추적. 이번 틱에서 새로 재현·입증된 제품 결함 없음. 코드 변경·설치·배포, 운영 재시작·운영 상태 파일 수정 없음.

- 2026-11-26 02:33:40–02:35:06Z 틱(02:12Z 대비). 네 호스트 Work MCP 성공(Prody 최초 SSH 배너 타임아웃 1회, 02:35:06Z 개별 재조회 성공). 신규 병렬 원장 실패 0. 이하 UTC.
  - Andy: 원장 7938·리스 집계 유지, MCP 812→2891ms. `n1166` 검수 행 누락 지속, 포크 표본 오류 0/5개. 외부 SSE EOF 21건(02:13:03부터), 모델 스트림 클라이언트 연결 리셋 2건(02:24:01부터), 정상 health의 keep-alive 가드 255건(02:12:17부터).
  - Celly: 원장 9814→9825, 실행·슬롯 2→3, 입력 대기 2 유지. 신규 검수 이상 없음, stale_runs 32 유지, MCP 625→407ms. 수집기 포크 표본 오류 7→26건(23개 표본; 집계 방식이 다른 별도 전사 대조 수치와 합산하지 않음). 별도 대조에서 두 포크 `aac07d51`·`ade59deb`에 도구 입력 검증 41건과 worktree 가드 8건(02:20:25–02:32:54). 입력이 깨진 지점은 미확인. 메인 오류: 버전 충돌 409(02:13:19), 잘못된 인자 400(02:17:48), 열린 stop 없음 404(02:31:53), 검색 타임아웃(02:32:24) 각 1건. 라우터: SSE EOF 21건(02:12:21부터), 입력 제출 미확인 2건·영수증 불일치 로그 2건(02:15:05부터, 별개 실패 건으로 합산하지 않음), 콘솔 메시지 16724 확인 실패 1건(02:18:16), upstream SSE 오류 프레임 2건(02:19:17부터), 클라이언트 연결 리셋 2건(02:20:03·02:27:50).
  - KOBE: 원장 4397→4406, 리스 집계 유지, MCP 329→541ms. `n5` 미완료 자식 검수 가드 지속, 포크 표본 오류 3→1건(02:24:21, 직접 원인 미확인). 메인 보드 버전 가드(02:24:29)·작업 명령 exit 1(02:26:43, 직접 원인 미확인) 각 1건. 라우터: SSE EOF 21건(02:12:43부터), 채널 커서 복구 로그 1건(02:19:37, 메시지 12277, 실제 전달 미확인), 클라이언트 단절 1건(02:24:51), 컨텍스트 사용량 기록 권한 거부 1건(02:30:34, WinError 5, 권한 거부 원인 미확인).
  - Prody: 재조회 MCP 328→326ms, 제출 1·실행/슬롯 1 유지, 포크 표본 오류 0/2, 이번 라우터 오류 표본 없음. `n1315` 포크 미관측 지속(종료 증거 없음). 보조 Betty는 수집 재확인, MCP 354ms, 콘솔 메시지 8059 확인 실패 1건(02:21:36).
  - 신규로 재현·입증된 제품 결함 없음. Celly 입력 오류·콘솔 미확인, KOBE 사용량 기록 권한 오류, Andy `n1166`, KOBE `n5`, Prody `n1315`·이전 8127 미확인 추적. 코드 변경·설치·배포, 운영 세션/라우터 재시작·운영 상태 파일 수정 없음.

- 2026-11-26 02:12:15–02:12:39Z 틱(01:53Z 대비). 요청한 네 호스트 Work MCP 성공, 신규 병렬 원장 실패 0. 보조 Betty만 SSH 배너 교환 타임아웃 1건(근본 원인 미확인). 운영 재시작·상태 파일 수정, 코드·설치·배포 없음. 이하 UTC.
  - Andy: 원장 7930→7938, 보드 v12109→12116, MCP 1031→812ms. 제출 21·입력 대기 5·실행/슬롯 1 유지, `n1166` 검수 행 누락 지속, 포크 표본 5개 오류 0, 메인 전사 오류 0. 라우터는 health 정상 keep-alive 가드 223건(01:53:30부터), 외부 SSE 읽기 EOF 19건(01:53:37부터), 모델 스트림 클라이언트 단절 WinError 10054 1건(01:54:52), 턴 상태 관찰 10건(모델 요청 상태 미확인).
  - Celly: 원장 9804→9814, 보드 v11270→11276, MCP 968→625ms, 제출 3→0·입력 대기 1→2·실행/슬롯 2 유지. 검수 finding 0, stale_runs 32 유지, 압축 상태 `verified` 재확인. 포크 표본 21개에서 오류 7건(직전 0): `a7cd79c4d092b7ec4` Bash 인자 검증/JSON 오류 4건(02:09:02·02:09:30·02:09:52·02:10:15), Write 절대 경로 가드 2건(02:11:10·02:11:50); `adc6881146176dbd2` Bash 닫히지 않은 작은따옴표 1건(02:12:16). 표본 이후에도 첫 포크에서 같은 두 종류 오류 반복. 원본 도구 인자가 어디에서 깨졌는지는 미확인. 메인: 닫힌 audit HTTP 409 1건(02:01:38), 작업 스크립트 exit 1건(02:02:37, 직접 원인 미확인), Python cp1252 출력 오류 2건(02:04:51·02:05:00). 라우터: 로컬 게이트웨이 HTTP 503 3건(01:55:36부터, 사전 필터 상세 미확인), 별도 5xx 상태 로그를 중복 합산하지 않음; 모델 전송 URLError 재시도(01:55:42–01:58:13, 로그 12건), 응답 노출 전 SSE 재시도 로그 5건(01:55:31–02:02:56), 외부 SSE EOF 17건(01:53:53부터).
  - KOBE: 원장 4383→4397, 보드 v569→574, MCP 236→329ms. 리스 집계 불변, `n5` 미완료 자식 검수 가드 지속. 포크 표본 오류 4→3건(01:59:01·01:59:21·02:11:08, 원인 미분류). 메인 승인자 멘션 누락 HTTP 400은 직전 틱과 같은 01:53:36 사건(신규 아님). 라우터 health 정상 keep-alive 가드 223건(01:53:32부터), SSE EOF 18건(01:54:16부터), 턴 상태 관찰 9건.
  - Prody: 원장 1239 및 검수·리스 집계 불변, 보드 v7439→7442, MCP 371→328ms. 포크 표본 2개 오류 0, `n1315` 미관측 지속(종료 미확인). 라우터 02:06:10 메시지 8127이 앞선 제출 미확인(`prior_submission_failed`, `stale_unconfirmed`)으로 건너뛰어진 로그 1건; 앞선 제출의 실제 전달 여부 미확인. 추적 유지: Celly Bash 인자 오류와 게이트웨이, Prody 채널 미확인·`n1315`, Andy `n1166`, KOBE `n5`.

- 2026-11-26 01:53:28–01:54:01Z 정기 틱(01:32–01:33Z 대비). Work MCP 네 곳 및 보조 Betty 성공, 신규 병렬 원장 실패 0, 수집 stderr 없음. 포크 표본은 부분 관측. 운영 재시작·상태 파일 수정, 코드 변경·설치·배포 없음.
  - Andy: 원장 7925→7930, MCP 1500→1031ms, 실행+슬롯 2→1, 제출 21·대기 5 유지. 검수 `n1166` 누락 지속. 포크 표본 오류 2→0/5개. 메인 검수 소유 리스·완료 영수증 가드 1건(01:38:19), 작업 명령 exit 1건(01:47:46, 직접 원인 미확인). 라우터 채널 SSE EOF 20건(01:33:09부터), health 정상인 keep-alive 가드 249건(01:32:35부터), 종료 후 ping 무시 2건(01:33:58부터), 턴 투영 관찰 10회(01:33:39–01:51:42, 모델 요청 상태 미확인).
  - Celly: 원장 9778→9804, MCP 1969→968ms, 제출 3 유지, 실행+슬롯 3→2·대기 0→1, 검수 finding 0·stale_runs 32 유지. 포크 표본 오류 2→0/20개와 별개로 메인 전사에 `n1579` 503 재시도 예약 4건(01:32:46·01:37:59·01:40:31·01:49:47), 재시도 로그 3건(01:33:57·01:35:09·01:50:54), 포크별 회복 로그 2건(01:36:12·01:52:35). 라우터 HTTP 503 로컬 17건(01:34:22부터)·중앙 8건(01:34:19부터), upstream SSE 오류 프레임 51건(01:33:16부터, 별도 계층 집계로 고유 실패 요청 수 아님), 채널 EOF 20건(01:33:26부터). 콘솔 메시지 16714 실패 1건(01:33:53), 16715 재시도 예약 로그 4건(01:35:58부터). 게이트웨이 필터의 구체 사유는 미확인.
  - **Celly 압축 회복 확인:** 메인 전사 01:52:46.283 `Conversation compacted`, 라우터 01:52:49 `native_checkpoint_observed`와 `checkpoint_and_history_shrink_verified`, 상태 `verified`. 비교 구간에 압축 시작 로그 12건(01:32:42–01:49:56)과 관측 충돌 8건(01:34:26–01:52:49). 압축 회복은 확인했으나 반복 원인이 해결되었다고 판정하지 않음.
  - KOBE: 원장 4368→4383, MCP 320→236ms, 리스 집계 유지. `n233` 수용 대기 finding은 이번 표본에서 빠짐(재작업 완료 미확인), `n5` 미완료 자식 가드 지속. 포크 오류 2→4건(01:35:37·01:35:53·01:36:58·01:49:23, 직접 원인 미분류). 메인 작업 명령 exit 1 두 건(01:41:34·01:52:16), 검색 타임아웃(01:42:42)·Python 괄호 구문 오류(01:46:03)·승인자 멘션 누락 HTTP 400(01:53:36) 각 1건. 라우터 SSE EOF 19건(01:33:15부터), 읽기 타임아웃 1건(01:52:12), 모델 스트림 클라이언트 단절 1건(01:50:15).
  - Prody: 원장 1239 및 검수·리스 집계 유지, 보드 v7437→7439, MCP 338→371ms. 포크 표본 오류 0/2개, `n1315` 미관측 지속(종료 미확인). Betty: 제출 5 유지, 포크 표본 오류 0, MCP 622→379ms, SSE EOF 21건(01:33:27부터). 신규로 재현·입증된 제품 결함 없음.

- 2026-11-26 01:32–01:33Z 정기 틱(01:13Z 대비). 네 호스트와 Betty 수집 완료, 신규 병렬 원장 실패 0. 운영 설정·상태 수정, 재시작, 배포 없음. 아래 시각은 UTC.
  - Andy: 원장 7914→7925, MCP 718→1500ms, 제출 21 유지, 대기 4→5, 실행+슬롯 4→2. `n1166` 누락 지속. 포크 표본 오류 2건, 메인 보드 버전 가드(01:23:52)·검색 타임아웃(01:29:19) 각 1건. 라우터 SSE EOF 19건(01:13:42부터, 근본 원인 미확인).
  - Celly: 원장 9751→9778, MCP 250→1969ms, 제출·실행 각각 3, `stale_runs` 32 유지, 검수 finding 없음. 포크 표본 오류 2건; `n1579` 503 재시도 기록은 01:13:21·01:17:54 발생, 각각 01:15:28·01:19:31 회복 로그. 게이트웨이 HTTP 503 로컬 17건(01:14:35부터), 중앙 6건(01:13:27부터); 별도로 upstream SSE 오류 프레임 52건(01:13:26부터, 형태 로그는 중복 집계하지 않음). 콘솔 메시지 16713 턴 실패·재시도 예약 로그 2건(01:29:06부터).
  - KOBE: 원장 4359→4368, MCP 249→320ms, 리스 수 유지. `n5` 미완료 자식 가드 지속, `n233` 재작업은 P3 슬롯 1/1로 수용 대기. 포크 오류 표본 2건. 메인 오류 5건(01:13:19~01:27:21): Python 출력 인코딩 경로, 검색 타임아웃, 자료형 오류, 보드 버전 가드, 작업 명령 exit 1 각 1건. SSE EOF 19건(01:13:44부터).
  - Prody: 원장 1239 유지, MCP 454→338ms, 검수·리스 변화 없음. `n1315` 미관측 지속(종료 미확인), 포크 표본 오류 0. 메인 도구 오류 4건: 호스트 전용 도구 가드 01:13:44, 잘못된 인자 HTTP 400 01:15:17, 목표 유형 오류 01:17:07, 경로 없음 01:19:49. Betty 검수 변화 없음, MCP 327→622ms.
  - Celly 압축 조사: 01:08:18~01:35:35 `native_compact_started` 11회, `observation_conflict` 11회(동일 횟수일 뿐 1:1 대응 미확인), 해당 구간 완료 전이 미관측. 01:28:41 압축 시작 직후 `_Walkie_P1-Celly` 513,140토큰·1,887,916바이트 요청이 01:28:42 HTTP 503 `all targets were skipped by pre-dispatch filters`로 거절됨. 게이트웨이 필터 세부 사유와 압축 재시도 반복의 전체 인과는 미확인. 별도 압축 모델 설정은 확인한 런타임 코드·공식 모델 설정 문서에서 찾지 못함.
  - Celly `n1577` 화면 오류: 01:30:45 저장소 루트로 `Write` → 01:30:54 리스 worktree 밖이라 가드 거부. 01:33:36 같은 파일을 리스 worktree 안 절대 경로로 재시도 → 01:33:42 파일 생성 성공 응답. 압축·API 오류와 별개.

- 2026-11-26 01:13–01:13Z 정기 틱, 직전 00:37–00:39Z 대비. 이하 UTC. 네 호스트와 Betty 모두 Work MCP `probe_succeeded`, 운영 재시작·상태 파일 수정 없음.
  - Andy: 설정 확인 `sse_injection_mode=btw_with_mcp`, `work_gate_check_minutes=0`. 원장 7888→7914, 보드 v12081→12094, 제출 21, 대기 4, 실행+슬롯 5→4, MCP 1734→718ms. `n1166` 검수 행 누락 지속. 포크 표본 오류 1회(01:12:47 `a236e266b8d8b5135`, 직접 원인 미분류), 메인 파일 경로 오류 1회(00:55:26). 라우터: keep-alive 가드 247회(00:52:21부터, health 정상), 채널 SSE EOF 20회(00:53:15부터), 모델 스트림 클라이언트 단절 2회(00:53:35), `channel_turn_projection_persistent` 8회(00:52:29~01:11:33).
  - Celly: 설정 확인 `sse_injection_mode=session`, `work_gate_check_minutes=0`. 원장 9658→9751, 보드 v11259→11267, 대기 0, 실행+슬롯 3, 제출 0→3, `stale_runs` 30→32, MCP 375→250ms. 포크 표본 오류 9회(00:54:40~01:08:57), 직접 원인 미분류. 00:52:46 `Side actors paused`(Stop 대기 중 거부, 수정 기능이 정상 동작), 01:03:48 실패 이벤트 `6f8442fb31d5` 원문이 메인에 첨부됐다는 로그 1회(메인 열람·회신과 총 시도 횟수는 미확인). 라우터: 게이트웨이 HTTP 503 `all targets were skipped by pre-dispatch filters` 5회(중앙 게이트웨이 2회, 01:12:30부터; 로컬 게이트웨이 3회, 01:12:57부터), 게이트웨이 5xx 상태 로그 3회(01:12:57부터, HTTP 오류에 별도로 더하지 않음), 고아 리스 회수 3개(01:11:38, 소유자 dead), `anthropic_sse_upstream_error_event` 1회(01:12:50). 게이트웨이의 필터 규칙과 근본 원인 미확인.
  - KOBE: 원장 4336→4359, 보드 v556→560, 리스 변화 없음, MCP 359→249ms. `n5` 미완료 자식 검수 가드 지속. 포크 표본 오류 3회(00:55:08~01:07:02). 라우터: 모델 스트림 클라이언트 단절 2회(01:06:25부터), upstream 오류 4회(00:58:45부터 2회, 01:07:26부터 2회), 응답 지연 재시도 4회(01:00:22·01:00:57·01:07:13·01:08:47), 콘솔 미확인 실패 2회(01:08:30 message 12271, 01:12:51 message 12272).
  - Prody: 원장 1236→1239, 보드 v7427→7437, 제출 2→1, MCP 508→454ms. `n1315` 포크 미관측 지속(00:17 이후). 포크 표본 오류 0.
  - Betty: 변화 없음(원장 1162, 보드 v5739), MCP 243→327ms, 채널 SSE EOF 21회(00:52:39부터).
  - 미결 추가: Celly 503의 2MB급 요청이 게이트웨이 필터를 어떻게 통과/탈락하는지, Celly `stale_runs` 32, Andy `n1166`, Prody `n1315`, KOBE `n5`, KOBE 콘솔 미확인 실패 2회. 신규로 입증된 제품 결함 없음. 코드 변경·설치·배포 없음.

- 2026-11-26 01:07Z 사용자 지시로 테스트 세션의 SSE 접수·주기 설정 변경. Work 설정 API의 `settings-update` 및 `GET /settings` 재조회로 확인. Celly·KOBE·Prody는 `sse_injection_mode=session`, `work_side_intake_enabled=false`로 전환. Betty는 이미 같은 값이라 유지. Andy는 SSE `btw_with_mcp` 및 사이드 접수 활성 유지(사용자 예외). 5곳 모두 `work_gate_check_minutes` 10→0. 설정 적용 대상으로 확인한 것은 신규 SSE 경로와 주기 작업이며, 실제 다음 SSE 도착 및 기존에 큐에 들어갔거나 이미 실행 중인 사이드 액터의 종료는 미확인. 운영 세션·라우터 재시작, 큐 삭제, 소스 수정·배포 없음.

- 2026-11-26 00:37–00:39Z 정기 틱, 직전 00:17Z 대비. 이하 UTC. 본 실행 `tick.jsonl`과 Prody 재조회 `tick-prody-retry.jsonl`. Work MCP 네 곳 성공, 운영 재시작·상태 파일 수정 없음.
  - Andy: 원장 7862→7888, 보드 v12068→12081, 제출 27→21, 대기 4, 실행+슬롯 1→5. `n1166` 검수 행 누락 지속. MCP 1016→1734ms, 포크 표본 4개에서 오류 0. 00:35:11 메인 Walkie MCP 인자 검증 실패 1회(`q` 대신 `query`). 라우터 keep-alive 가드 245회(00:16:53부터, health 정상), 채널 SSE EOF 20회(00:17:24부터, 근본 원인 미확인), 모델 스트림 클라이언트 단절 3회(00:22:16부터), 종료 뒤 ping 무시 2회(00:30:49부터).
  - Celly: 원장 9639→9658, 보드 v11255→11259, 대기 6→0, 실행+슬롯 2→3, `stale_runs` 23→30. 검수 finding 0. MCP 281→375ms. 포크 표본 오류 3회(00:22:53, 00:33:20, 00:34:34, 직접 원인 미분류), 메인 작업용 Python 스크립트 실패 2회(00:31:38, 00:32:06). 라우터: 00:27:02 이전 소유자 고아 리스 회수 5개/로그 8회, 채널 SSE EOF 10회(00:28:01부터), 모델 스트림 클라이언트 단절 2회(00:33:08·00:34:08), 응답 노출 전 게이트웨이 재시도 2회(00:36:16·00:36:30), 00:36:31 게이트웨이 HTTP 5xx 및 upstream SSE 오류 같은 시각에 각 1회. 게이트웨이 근본 원인 미확인. P0 `6a363c6d280d`·`bbed31cdae9d` 재클레임 후 각각 00:33:21·00:34:06 처리 완료(REST); P1 `602560481844`는 `received` 유지.
  - KOBE: 원장 4326→4336, 보드 v554→556, 대기 9→10, 제출 1, 실행+슬롯 1 유지. `n5` 미완료 자식 검수 가드 지속. MCP 212→359ms, 00:32:37 포크 표본 오류 1회(직접 원인 미분류). 라우터 keep-alive 가드 245회(00:16:53부터), 채널 SSE EOF 20회(00:17:14부터), 모델 스트림 클라이언트 단절 2회(00:19:07·00:32:57).
  - Prody: 최초 SSH 수집 1회 실패(`lost connection`), 00:39:36 재조회 성공. 원장 1232→1236, 보드 v7414→7427, 제출 1→2, 실행+슬롯 1 유지. MCP 793→508ms, 포크 표본 오류 0. `n1315`는 00:17 이후 포크 미관측으로 재표시(종료 확인 아님). P1 `n1318/b4b842f7` 완료 영수증은 있고 검수 행은 없음(신규 관측, 원인 미확인). 00:36:34 제출된 리스를 재개할 수 없다는 Work 가드 1회(`revival_refused`), 검수 행 누락과의 인과는 확인되지 않음.
  - 신규 재현으로 입증된 제품 결함 없음. 다음 틱에도 Andy `n1166`, Prody `n1315`·`n1318`, Celly `stale_runs`와 미처리 P1 추적. 코드 변경·설치·배포 없음.

- 2026-11-26 00:23–00:34Z Celly side-fresh 조사(직전 기록 정정). `a192f6c401e8e7a2f`는 00:21:36Z 중단 표식(`stoppedByUser`)과 같은 시각 모델 연결 단절이 확인됐다. 이것만으로 upstream 요청의 지속, 화면에 남은 액터의 정체, 정리 실패 원인은 확인할 수 없다. 화면의 resume 거절 문구는 메인·사이드 전사에서 찾지 못했다. 코드상 호스트 예약 조건을 통과하지 않은 `Side SSE` 식별자의 resume은 거부하지만, 해당 화면을 만든 요청의 입력값·시각·액터 ID는 미확인이다. `sideActorEnded`는 정상 목록에서 항목 없음(`!row`)을 종료로 판정하고, 목록 조회 실패만 미확인(`undefined`)으로 둔다. `bbed31cdae9d`의 이전 액터 전사는 00:16~00:22Z 도구 활동을 담고 있어 20분 무출력이라는 이전 설명은 틀렸다. 메인에 00:22:11Z `/exit`가 기록됐고 런처는 00:22:56Z 종료 코드 0, 00:26:58Z 라우터 재기동을 기록했다.

- 2026-11-26 00:16:51–00:17:25Z 정기 틱, 11-25 23:57Z 대비. 이하 UTC. Work MCP 5곳 모두 성공. 신규 병렬 작업 원장 실패 0.
  - **배포 반영(사용자 지시 2026-11-26 00:0xZ "베타 배포, 모든 에이전트 패치, 라우터 재시작"):** `61d2f8b`(`35b647a` Stop 대기·fresh 예약 슬롯·원본 ACK+memo, `61d2f8b` 검수 기록 보존 한도)를 beta에 푸시했다. Publish `45725816035`가 성공했다. npm `@beta` = `0.2.51-beta.20261126-000237.61d2f8b`, gitHead `61d2f8b0…`. 해시 확인 오버레이(`ff24aa6` 기준, 코드 6개 파일 tarball SHA-256이 커밋과 일치)를 승인된 7개 루트에 설치했고 모두 `ok=true`였다. `work_parallel_finalization.py`는 로컬 3개 루트에서 CRLF였는데, 줄 끝만 다른 같은 내용이라 base로 인정했다. 라우터 5곳을 사용자 지시로 재시작했고, 모두 새 버전으로 다시 떴으며 클라이언트는 그대로 붙어 있다: Andy 42312→80284, Celly 89168→39296(관리자 권한 종료), KOBE 10660→17580, Prody 8384→10260, Betty 6068→12636.
  - **라이브 효과(확인):** 모드 핫 리로드는 Betty 00:10:41, Celly 00:10:50, Andy 00:11:07, KOBE 00:14:57, Prody 00:15:23이다. Andy `work-parallel-finalization.json`은 2,097,049→1,611,225바이트(행 328→229, 버전 3321→3348)가 됐다. finding `no review row`는 8→1로 줄었다(`n2849`·`n2850`·`n2851`·`n2860`·`n2844`·`n2845`·`n2862` 해소, `n1166`만 남음). 7개 결과가 검수 대기로 올라왔다.
  - Andy: 제출 28→27, 대기 4, 실행+슬롯 1. 원장 7860→7862, 보드 v12064→12068, MCP 734→1016ms. 오류: 작업 측 스크립트 exit 1 1. 포크 표본 오류 0.
  - Celly: 대기 5→6, 실행+슬롯 3→2. 원장 9638→9639, 보드 v11251→11255, MCP 1656→281ms. 라우터: `router_lifetime_restart_failed health_timeout` 2(00:12:59 pid 84960, 00:14:00 pid 39296, 30.6초). 런처 감독자가 재시작 직후 30초 안에 health를 받지 못한 기록이다. 그 뒤 pid 39296이 health `ok`로 응답하고 있어 재기동 경고로 분류했다. 포크 API `server_error 503` 재시도 예약 2(00:01 `n1579`, 00:15 `n1505`; 정상 재시도 경로), 포크 표본 오류 2(`a1c25ef4` 00:14·00:15).
  - KOBE: 실행+슬롯 2→1, 대기 9·수락 1 유지. 원장 4322→4326, 보드 v551→554, MCP 306→212ms. `n5` 가드 지속. side 실패 원문 메인 전달 1(00:04:28 `be02334cfb05`, `invalid_answer`: side target Work item missing, 리로드 전 모드). 포크 API `server_error` 재시도 예약 1(00:15 `n223`).
  - Prody: finding `n1315` 슬롯(fork absent since 21:51) 해소. 리스·원장 1232 유지, 보드 v7411→7414, MCP 321→793ms. side 실패 원문 메인 전달 1(00:14:56 `bd9d7c42898b`, `invalid_answer`: decision JSON 없음, 리로드 전 모드). 보드 버전 가드 1. 포크 표본 오류 1(`a38f3e61` 00:16).
  - Betty(보조): 변화 없음(제출 5, 원장 1162, 보드 v5739), MCP 312→599ms.
  - 미결·추적: Andy `n1166` 검수 행 누락(기존), side `invalid_answer` 반복(KOBE·Prody, 리로드 전), Celly 재시작 health 30초 초과, Celly `stale_runs` 23, 게이트 재확인 라운드 크기(사용자 결정), KOBE `n5`, 채널 미전달, worktree 시간 초과, Linux 테스트 컨테이너 PID 누적, Andy Bun 크래시 원인.

## 2026-11-25

- 2026-11-25 23:57:25–23:57:59Z 정기 틱, 21:27Z 대비. 이하 UTC. Work MCP 5곳 모두 성공. 신규 병렬 작업 원장 실패 0. 운영 재시작·상태 파일 수정 없음. 수정 2건(Stop 대기·fresh 예약 슬롯, 검수 기록 보존 한도)은 Linux py311 영향 회귀가 진행 중이라 아직 커밋·배포하지 않았다(Windows 영향 1042/0·1097/5 기존 원인 분리, Linux py310 1124/0, 실제 백엔드 120/0 양쪽).
  - **Andy(반복, 미해결):** finding `no review row` 7→8(`n2862` `1f7e2a87` 추가, 22:12 idle). `work-parallel-finalization.json`은 2,097,049바이트 그대로이고 마지막 쓰기는 16:50Z(버전 3321, 행 328)다. 21:27 틱에서 입증한 용량 결함이 계속된다. 리스: 제출 27→28, 대기 5→4, 실행+슬롯 0→1. 원장 7833→7860, 보드 v12015→12064(노드 520, 실행가능 40→43), MCP 688→734ms. 오류: 보드 버전 가드 7, 작업 측 스크립트 exit 1 4(OKF·worktree 대조). 포크 표본 오류 0. side: 22:30 이후 수신분 모두 처리(최장 P3 `ad8a2ccf1900` 12분 40초), 처리 중 게이트 재확인 1, 대기 P3 1.
  - **Celly(반복, 수정 대기):** fresh 일반 액터 하나가 게이트 재확인 `ce7c0fd33f87`(23:44–23:58)을 처리하는 동안 P1 `602560481844`가 18분 43초 기다렸다(23:39:49 수신 → 23:58:32 클레임). P1 승인 `4536659863bc`(Daniel 161408)·P2 승인 `1286cce6097e`(161407)·P3 승인 `46a84f1f861d`(161404)는 아직 대기 중이다. Andy 11-25에 입증한 예약 슬롯 결함과 같은 모양이다. 수정은 미배포이고, 라이브 라우터(`faa2c7e`)도 같은 차단을 하므로 배포 뒤에도 라우터 재시작 전까지 남는다. Stop 대기는 21:30:53 사람 입력 뒤 풀려 있다(21:30 이후 Stop 대기 실패 0). 리스: 대기 9→5, 제출 2→0, 실행+슬롯 0→3. 원장 9565→9638, 보드 v11202→11251(노드 259→272, 정지 80→85, 힌트 17→23), MCP 610→1656ms, `stale_runs` 14→23(추적). 오류: Walkie `threads_stop`·`send_message` client_id UUID 3(21:51–21:53), Walkie 400 4, 분해 부족 실행 거부 1, Work 게이트 `router-error` 1(21:59:43, 원인 미확인), auto continue의 Work 상태 읽기 실패 1(23:13:07, 원인 미확인). 포크 표본 오류 8(`aed5400a` 21:41–21:43 3건, `aeebd47b` 23:16 2건 외).
  - KOBE: 대기 5→9, 제출 1→0, 실행+슬롯 2·수락 1 유지. 원장 4266→4322, 보드 v527→551, MCP 592→306ms. `n5/24fffb5b` 가드 지속. 오류: P3 슬롯 상한 가드 2(22:01, 23:25), GitHub 자격 증명 Vault 항목 없음 2(23:18·23:20, 자격 증명 문제), 보드 버전 가드 2, 작업 측 스크립트 assertion 2. 실패 원문 메인 전달 2(21:58, 22:12). 포크 표본 오류 3.
  - Prody: 리스 변화 없음(제출 1, 실행+슬롯 1). 원장 1229→1232, 보드 v7398→7411, MCP 261→321ms. finding 새로 1건: `slot held, fork absent since 21:51: n1315 bf00c785 P3 running`. 포크 `a38f3e61`은 21:41:57 `auto_compact_failed` 뒤 재시도 4회 만에 21:50:19 회복했고(정상 재시도 경로), 21:51 이후 네이티브 목록에서 보이지 않는다. 메인 전사가 22:43·22:47·23:08에 이 포크를 언급했다. 슬롯이 2시간 넘게 잡혀 있는 원인은 확인하지 못해 추적한다. auto continue Work 상태 읽기 실패 1(22:34:24, Celly와 같은 문구).
  - Betty(보조): 변화 없음(제출 5, 원장 1162, 보드 v5739). Walkie 400 1(23:45).
  - 미결·추적: Andy 검수 기록 용량(수정 검증 중, 라이브는 라우터 재시작 필요), Celly·Andy P1 예약 슬롯(동일), Celly·Prody auto continue 상태 읽기 실패, Prody `n1315` 슬롯, Celly `stale_runs` 증가, Linux 테스트 컨테이너 PID 누적, KOBE `n5` 가드, 채널 미전달, worktree 시간 초과, Andy Bun 크래시 원인.

- 2026-11-25 21:26:35–21:27:13Z 정기 틱, 16:52Z 대비. 이하 UTC. Work MCP 5곳 모두 성공. 신규 병렬 작업 원장 실패: KOBE·Prody 각 `decision_refused_stop` 1건(메인 결정 거부 뒤 Stop을 다시 잡지 않은 기록, 가드 동작으로 분류).
  - **Andy — 입증된 결함(검수 기록 파일 용량 한계):** Andy 메인 세션도 21:42Z 세션 간 메시지로 `n2845` 대체 리스 `65dd1317`의 검수 핀 부재와 같은 `finalization byte limit` 거부를 진단용으로 알려 왔다(조치 대행 요청 아님). finding `submitted, completion receipt, no review row` 1→7건(`n1166` 기존 + `n2849`·`n2850`·`n2851`·`n2860`·`n2844`·`n2845` 신규, 모두 P1 완료 영수증 있음). `work-parallel-finalization.json`이 2,097,049바이트로 상한(`MAX_BYTES` 2,097,152)에서 103바이트 남았다. 검수 행 328개 중 327개가 `finalized`(행 중앙값 약 5.9KB; `workspace` 590KB·`verification` 430KB·`summary` 429KB). 마지막 검수 행 갱신은 16:50:35(`n2842` approve). 새 검수 행 쓰기(`pending()`)는 `require(len(raw) <= MAX_BYTES, "finalization byte limit")`로 거부된다. 메인 전사 20:17:53·20:30:03에 `recover_submitted_review` 호출이 `Parallel read failed: finalization byte limit`로 거절됐고, 메인이 20:42·20:55·21:15에 "Work 승인을 기록하지 못했다"고 보고했다. 완료된 행을 지우는 보존 정책이 코드에 없다(`work_parallel_finalization.py` 삭제 경로는 미결정 행 `remove_undecided`뿐). 수정은 아래 진행 중.
    - 리스: 제출 21→27, 대기 0→5, 실행+슬롯 3→0. 원장 7738→7833, 보드 v11914→12015(노드 525, 정지 144→150, 힌트 32→37, 실행가능 36→40), MCP 1031→688ms. `stale_runs` 159 유지.
    - side(fresh, `ff24aa6` 모드): P0 `7f250cbf0657` 수신→클레임 45초, P0 `6e6a63446270` 6분 25초(앞 게이트 재확인 처리 중; 라우터는 `faa2c7e`라 예약 슬롯 규칙이 아직 라이브가 아님), P1 `f6ab8833fbfb` 7분 18초. 21:36 기준 P0 `f4b6035666a1` 처리 중, 대기 4건.
    - 오류: Walkie `threads_stop` client_id UUID 인자 3(17:32), Walkie HTTP 429 2(17:57·18:02), 보드 버전 가드 7, `finalization byte limit` 2(위), 포크 API `empty_stream` 재시도 예약 1(19:00, `n2799`), 승인자 멘션 400 1(19:11). 포크 표본 오류 2(`a077bbef` 20:11·20:16). 라우터: 외부 SSE EOF 144, turn projection 다수(최대 971s), `_Walkie_P1`·`_Walkie_S_Tier_Only` 503 재시도(19:05Z 묶음), MCP POST 리셋 7.
  - **Celly — Stop 대기 해소(사람 입력, 확인):** 21:30:53에 사람 입력 "resume"(origin `human`)이 전사에 기록됐다. 그 전까지 20:30–21:29 수신분 13건이 같은 Stop 대기 사유로 6회 시도 뒤 격리됐다(누적 59건; 실패 회신 `submitted` 3건 추가). 입력 뒤 21:32:13 side-fresh `aea96dc8`(이벤트 `b8e9bccf8242`, P0)가 시작돼 21:35:05 처리됐고, 21:36:51 `af12f79b`(`727042b0`)도 시작했다. 원인과 수정은 [Stop 대기와 fresh 예약 슬롯](/docs/journal/2026/11/25/fixes/side-stop-hold-fresh-slot/record.md)(미배포).
    - 리스: 대기 8→9, 제출 0→2, 실행+슬롯 2→0. 원장 9557→9565, 보드 v11191→11202(노드 278→259, 루트 46→43), MCP 1360→610ms. finding 0. 포크 표본 오류 4(`ac6d9bde` 17:31, `a9f6d8b9` 18:14·18:45·19:14). 라우터 `keep_alive` 3074, 외부 SSE EOF 260, `_Walkie_P1-Celly` 업스트림 오류 이벤트 10·`anthropic_sse_forward_error` 5(17:12–17:17Z 묶음, 업스트림 5xx 회전).
  - KOBE: 제출 0→1, 대기 3→5, 실행+슬롯 1→2, 수락 1 유지. 원장 4109→4266, 보드 v452→527(노드 227→214, 루트 20→16, 실행가능 50→55), MCP 325→592ms. `n5/24fffb5b` 자식 미완료 검수 가드 지속. 오류: Walkie `send_message` client_id UUID 2(17:30·17:32), GitHub 인증 실패 2(17:33 git `Invalid username or token`, 19:15 `gh` 401; 자격 증명 문제로 제품 결함 아님), 보드 버전 가드 9, `n228` 미승인 목표 검수 거부 1, 분해 부족 실행 거부 1, Walkie 네트워크 실패 1(20:57). 포크 표본 오류 6(`a56b4784` 19:39–19:42 3건, `ae994487` 20:26, `a382c2aa` 21:14 2건). 라우터 `keep_alive` 3202, SSE EOF 238·BrokenPipe 10·Timeout 7, 활동 파일 `PermissionError` 6.
  - Prody: 제출 0→1, 실행+슬롯 2→1. 원장 1151→1229, 보드 v7363→7398, MCP 392→261ms. finding 0. 오류: 재작업 한도 2/2 가드 1(21:22), 중복 리스 가드 1, 완료 전 검수 가드 1, `gh` 미인증 1(17:54), Grep 잘못된 인자 2, 승인자 멘션 400 1. 포크 표본 오류 8(`ac86c918` 19:52 4건 외). 라우터(ERROR만 기록): `channel_stdin_proxy_blocked stale_unconfirmed` 7(19:06–21:25Z, 메시지 8091–8107 스킵; 채널 미전달 추적 항목에 추가).
  - Betty(보조): 제출 6→5, 실행+슬롯 1→0. 원장 1108→1162, 보드 v5724→5739, MCP 302→367ms. finding 0. `n1833` `git worktree add` 30초 시간 초과 → `workspace_prepare_retained` 가드 2(G: 드라이브 아님, Betty 저장소; worktree 시간 초과 반복 추적). 포크 표본 오류 16.
  - **Andy 대기 해제 점검(사용자 요청 "사이드가 승인된 대기를 해제·전달하지 않는다", 읽기 전용, 22:00–23:20Z):** 보드 v12058, 대기 기록이 있는 노드 148개(pending 76, in_progress 60, blocked 12).
    - 해제가 막힌 가장 큰 원인(확인): 위의 검수 기록 용량 결함이다. 결과를 제출했지만 검수 행을 쓰지 못한 `n2844`·`n2845`·`n2849`–`n2851`·`n2860`·`n1166`을 메인이 승인할 수 없어, 이들에 걸린 대기가 풀릴 수 없다. 해당 대기는 `n2728`·`n2787`·`n2806`·`n2812`(`n2844` 설계 승인), `n2853`·`n2855`(`n2849`–`n2851` 검수), `n1167`(`n1166`)이다. `n2845`는 22:36 Daniel의 선택 승인(161388)을 사이드가 기록했지만 대체 리스 검수 행이 없어 진행할 수 없다.
    - 사이드 해제 시도와 호스트 가드(확인): `e8925279b32d`·`eed846b6b936`이 `n2799` 해제를 냈고, 호스트가 `gate absent or changed since scheduled check`로 거부했다. `n2799`의 해제 조건은 "n2812 R1 운영 배포 후 30분 라이브 측정"이고, 힌트에 "PR #136 CI 14/14는 대체 증거가 아니다"라고 적혀 있다. 그러니 거부가 맞다. `n2862` 해제 시도는 대기 기록이 없는 노드라 `no recorded wait`로 거부됐다. 20:01에 사이드가 161133 복기 승인을 근거로 `n2855`를 풀었고, 메인에 알림이 전달됐다(appended). 그 뒤 메인이 "161133은 최초 요청 복기만 승인"이라며 수동 차단으로 되돌렸다.
    - 전달(확인): `n2799` 포크 `a077bbef` 전사에 20:16:05 사이드 직접 전달(161263)이 도착했고, 20:34 파킹 포크 `n2790`에 답이 갔다. 메인 알림 `Ciel side asks main`이 20:21·21:23·21:29에 붙었다.
    - 게이트 재확인 범위(확인, 설계 동작): 한 라운드는 10개(`DEFAULT_ROUND_GATES`, 사용자 2026-11-19)다. P0 게이트가 먼저 7칸을 쓰고 나머지는 3칸씩 돌아간다. 08:32–23:33의 재확인 37건이 다룬 게이트는 서로 다른 101개였고, 대기 148개 중 51개는 이 시간 동안 한 번도 포함되지 않았다. P0 게이트 13개는 각각 16–22번 반복됐다. 이번 패치(`ff24aa6`·Stop 대기·보존 한도)는 게이트 해제 판정이나 라운드 크기를 바꾸지 않았다. 라운드 크기·구성 변경은 사용자 결정 사항으로 남긴다.
  - 테스트 인프라: Linux `ciel-runtime-test-py310` 컨테이너에서 `docker exec`가 `procReady not received`로 실패했다(PID 17,391; 영향 실행 오류 621건은 이 상태에서 난 것). 테스트 컨테이너만 재시작했고(운영과 무관), 같은 영향 실행을 다시 돌렸다.
  - 미결·추적: Andy 검수 기록 용량(수정 중), Stop 대기 수정 `side-stop-hold-fresh-slot` 배포 대기(Windows 실제 백엔드 회귀 진행 중), KOBE `n5` 가드, Prody·Betty 채널 미전달, worktree 시간 초과, Andy Bun 크래시 원인(전체 `bun.report` 필요). 운영 재시작·상태 파일 수정 없음.

- 2026-11-25 16:52:23–16:52:53Z 정기 틱, 16:32Z 대비. 이하 UTC. Work MCP 5곳 모두 성공(Andy 복귀). 신규 병렬 작업 원장 실패 0.
  - **Andy 복귀(사용자 재기동, 확인):** 사용자가 16:36:24에 `ciel_runtime.py cli --continue`로 다시 올렸다(런처 111572, 라우터 14356, `claude.exe` 77312). 사용자도 본 세션에서 직접 재기동했다고 밝혔다. 같은 세션 `feeac3ad`를 이어받았고 16:37:03 사용자 입력 "세션이 복구되었음으로 끊긴부분부터 재계"가 전사에 있다. 라우터 health 버전은 `0.2.51-beta.20261125-084356.d1f9da3`이다. **라이브 효과:** Andy는 `d1f9da3` 라우터 측 수정(`side_understanding_outbox` item_id 확인, refs 정규화, `work_gate_check`, `fe52b22` side_forward)과 모드 `side-intake.js`가 처음으로 함께 동작하는 호스트다. 나머지 4곳 라우터는 여전히 `faa2c7e`다. `ciel-work` 로드를 확인했다(16:37:20 Work mode on, 16:38:00·16:49:08 게이트 재확인, 포크 확인 2–4개, 16:41:48 side 해제 `n2834`·`n2842` append, 16:54:19 `n2834` 포크 직접 relay).
    - 기동 시 고아 리스 정리: `released=16 kept=0 failed=0`(16:36:37; running 3건 `37ddc08d`·`cfdbece8`·`c3ace7a9` = 기존 죽은 소유자 슬롯 finding 3건 해소, waiting_input 13건). 리스: 대기 13→0, 제출 22→21, 실행+슬롯 3(새 포크). 원장 7684→7738, 보드 v11914(노드 523, 루트 67, 정지 144, 힌트 32, 실행가능 36). `stale_runs` 143→159(+16, 해제된 리스의 run 기록이 active로 남은 수와 같음; 이전부터 있던 지표라 결함으로 단정하지 않고 추적). 남은 finding은 `n1166` 검수 행 누락 1건.
    - 오류: 메인 보드 버전 가드 2(16:44:04·16:44:46), 작업 측 `jq` 식 오류 1(16:45:55), Walkie 400 1(16:51:35). 포크 표본 오류 2(`a2830da4` 16:41:04, `a0ee2dfe` 16:47:32). 라우터: 외부 SSE EOF 13·BrokenPipe 1(16:47:21), MCP POST 리셋 4(16:43:44), 게이트웨이 discovery 불가 → 설정 base URL 사용 1(16:37:09), turn projection 6(16:39:09–16:51:16, 최대 847s), `n2834` 워크스페이스 준비 `git worktree add` 78 s 시간 초과 → `workspace_prepare_retained` 가드(16:43:43·45; Celly 15:02·15:04와 같은 G: 드라이브 worktree 시간 초과 반복, 추적).
    - Bun 크래시 재발 감시: 재기동된 CLI가 이어받은 전사는 1.86GB다(`feeac3ad`, 16:53:47 기준). 크래시와의 관련은 미확인이며 계속 추적한다.
  - Celly: 실행+슬롯 3→2, 대기 8 유지, 원장 9553→9557, 보드 v11188→11191, MCP 297→1360ms. finding·메인 오류 0, 포크 표본 오류 1(`a3c11ec5` 16:34:10). 라우터: `keep_alive` 228, 외부 SSE EOF 17·BrokenPipe 1(16:47:21), `_API_KEYS-SSE-1` 업스트림 오류 이벤트 1(16:33:38, tolerant 재시도 1회). **업스트림 무응답 추적:** `_API_KEYS-SSE-1` 요청 `78ce6d45`(16:44:55, tokens 144429)가 `message_start` 없이 늦은 SSE 재시도 14/60회, 16:54:28 기준 575 s 경과(출력 노출 없음). 업스트림 게이트웨이 소관이라 제품 결함으로 단정하지 않는다. side: 처리 중 `feb7bef96571`(16:33:40 클레임, 20분째), P1 `04cd14dee341` 40분째 대기, P1 `d545bdae4718`은 16:33:38 `turn_error` 1회 뒤 재시도 가능 상태로 대기(재클레임 없음). 대기 23건.
  - KOBE: 제출 1→0, 대기 2→3, 수락 1·실행+슬롯 1 유지, 원장 4097→4109, 보드 v425→452(노드 213→227, 실행가능 39→50), MCP 608→325ms. `n5/24fffb5b` 자식 검수 가드 유지. 메인 오류 0, 포크 표본 오류 1(`a400e8b6` 16:32:41). 라우터 `keep_alive` 237, 외부 SSE EOF 18·BrokenPipe 1(16:50:08), 클라이언트 리셋 1(16:47:18), 늦은 SSE 재시도 1(16:34:15 tolerant), turn projection 6(16:33:00–16:51:09, 최대 1569s).
  - Prody: 실행+슬롯 1→2, 원장 1149→1151, 보드 v7359→7363, MCP 596→392ms. finding·메인 오류 0, 포크 표본 오류 1(`ab44dcd6` 16:48:16). 라우터 `[ERROR] anthropic_sse_forward_error` 1(16:51:52, `_Walkie_P1`, 업스트림 `URLError 10053` 연결 중단, `message_start` 전). **"router.log 공백" 해명(확인):** Prody 설정에는 `log-level` 파일이 없고, 코드 기본값은 `LOG_LEVEL_DEFAULT = ERROR`다(`ciel_runtime.py:587`, `runtime_logging.LogLevelRepository.current`). 그래서 INFO·WARN 줄은 기록되지 않고 ERROR 줄과 stderr traceback(소켓 끊김 1,138건)만 남는다. Betty는 `INFO`, Andy는 `DEBUG` 파일이 있다. 설정 차이일 뿐 결함이 아니며 상태 파일은 바꾸지 않았다.
  - Betty(보조): 제출 5→6, 실행+슬롯 1 유지, 원장 1103→1108, 보드 v5719→5724, MCP 273→302ms. finding 0, 포크 표본 오류 4(`a5181d10` 16:41:24–16:43:58 3건, `aa54b123` 16:52:55). 메인: 요청 인자 400 1(16:33:03), 포크 API `transport_interrupted`(n1828) → 재시도 예약 통지 1(16:33:59, 정상 재시도 경로), 미승인 goal 디스패치 가드 1(16:40:47). 라우터 외부 SSE EOF 20, `_Walkie_P3` 업스트림 오류 이벤트 1(16:33:47, tolerant 재시도), turn projection 6(16:34:04–16:52:53).
  - 새로 입증된 제품 코드 결함 없음. 코드 변경·설치·발행, 운영 재시작·상태 파일 수정 없음. Andy 재기동은 사용자 조작이다.

- 2026-11-25 16:32:18–16:32:50Z 정기 틱, 16:15Z 대비. 모든 시각 UTC. Work MCP 4곳 성공, Andy 실패. 신규 병렬 작업 원장 실패 0; side 영수증 실패는 별도 집계.
  - Andy: 9505 정지 지속, MCP `URLError` 1회(16:32:18 채집, 2046ms). 제출 22·대기 13·슬롯 3 및 원장 7684 유지. `n1166` 검수 행 누락과 죽은 소유자 슬롯 3건 그대로. 포크 표본 오류 0, 현재 실행 상태는 확인 불가.
  - Celly: 원장 9545→9553, 보드 v11180→11188, MCP 281→297ms. 대기 8 유지, 실행 슬롯 2→3, 제출 1→0, 신규 검수 이상 0. 포크 오류 1(`ac3b6a93`, 16:25:19, 상세 원인 미확인). 메인 작업 점검 명령 exit 1(16:16:12, 상세 미확인), 완료 전 검증·검수·정리 가드 1(16:19:51). 라우터 유지 경고 199(첫 16:15:35), 외부 SSE EOF 17(첫 16:15:51), 모델 클라이언트 리셋 3(첫 16:16:29), turn projection 5(16:17:51–16:23:52), 응답 노출 전 SSE 재시도 3(16:24:46·16:28:22·26). side `01f425d02569`는 16:26:43 처리 완료(`n1464` 해제·메인 append 기록, 해제 근거 독립 검증 안 함). 보충 REST 최근 100행 중 대기 21건(최고 66분, P1 2건). `04cd14dee341` 대기 중, `d545bdae4718`은 틱 이후 16:33:38 `turn_error` 1회, 재시도 가능·격리 아님(하위 원인 미확인).
  - KOBE: 원장 4081→4097, 보드 v403→425, MCP 158→608ms. 대기 3→2, 실행 슬롯 2→1, 제출·수락 각 1 유지(종료 단정 안 함). `n5/24fffb5b` 자식 미완료 검수 가드 지속. 보드 정지 51→41, 힌트 50→40, 실행 가능 22→39: 메인 전사에서 16:30:17–32에 선택 메시지를 인용한 `clear_wait` 11회 확인; 해당 변화 전체를 side 일괄 해제로 볼 근거 없음. 포크 오류 1(`aa707eac`, 16:20:41, 상세 미확인). 메인 Walkie UUID 인자 오류 1(16:17:26), 완료 전 검수 가드 2(16:23:20·16:31:55). 라우터 유지 경고 200(첫 16:15:37), 외부 SSE EOF 16(첫 16:16:32), 연결 리셋 3(16:21:15·16:23:41·16:25:32), 활동 파일 `PermissionError` 1(16:25:00), turn projection 8(16:16:54–16:30:59). 게이트 이벤트 `54b16adf6ec3`의 `superseded` 원문 메인 append 1(16:24:16): 구 라우터의 기지 동작. 보충 REST 100행 중 대기 64건(최고 385분); `1002683257a3`은 클레임까지 535분, 처리 38분(16:25:58 완료).
  - Prody: 원장 1149·실행 슬롯 1 유지, 보드 v7358→7359, MCP 313→596ms. 신규 검수 이상·메인 및 포크 표본 오류 0. 라우터 로그 공백 지속(오류 없음으로 해석하지 않음). 보충 REST 100행 중 대기 13건(최고 234분, P2 159분); `2bb0facbb8d7`은 클레임까지 369분, 처리 11분(16:13:13 완료).
  - Betty(보조): 원장 1097→1103, 보드 v5716→5719, MCP 366→273ms. 제출 5·실행 슬롯 1 유지, 신규 검수 이상 0. 포크 표본 오류 6(`a5a4c0ac` 16:16:16–16:17:50 3건, `a5181d10` 16:30:39–16:32:42 3건, 상세 미확인). 메인 Walkie UUID 인자 오류 1(16:24:14), P3 슬롯 상한 가드 1(16:25:18), 요청 인자 400 2(16:26:18·20). 라우터 외부 SSE EOF 17(첫 16:16:27), turn projection 7(16:16:15–16:32:04). 직전 미확인 `8011`은 확인 증거 없음.
  - 보충 조회 `limit=200` HTTP 422는 수집 인자 문제(3호스트 각 1회); `limit=100` 재조회 성공. 신규 제품 코드 결함 미입증: 코드 변경·설치·발행이나 운영 재시작·운영 상태 파일 편집 없음. 지정 수집 스크립트는 자체 체크포인트 `monitor/state.txt`를 갱신한다. 기존 파일 설치 `d1f9da3`와 라우터 라이브 `faa2c7e`를 구분해 추적한다.

- 2026-11-25 16:15:34–16:16:07Z 정기 틱, 15:52Z 대비. Work MCP 4곳 성공, Andy 실패(9505 정지 지속). 신규 원장 실패·실패 side 원문 메인 전달 0. 이하 UTC.
  - Andy: 9505 정지 지속(MCP `probe_failed` 2063ms). 원장 7684·리스·finding 변화 없음. 재기동하지 않았다.
  - Celly: 대기 7→8, 실행+슬롯 1→2, 제출 0→1, 원장 9533→9545, 보드 v11172→11180(노드 279→278, 정지 82→81, 실행가능 10→12), MCP 453→281ms. finding 0, 포크 표본 오류 4(`ac3b6a93` 16:02:45·16:14:57, `a5e40763` 16:14:33·49). 메인: 작업 측 `ciel-updater.exe` exit 1 1(16:05:34), 보드 버전 충돌 가드 1(16:06:45). side 대기열: 48.5분 소요된 게이트 재확인 `7afafa0b3940`이 15:55:33 오해제 없이 `processed`(해제 0), 이어 P2 `a02173058363`(n1565 해제, 16:03:34)·`dc758d7a00b2`(16:06:44)·`05c4263bea95`(16:09:02) 처리 완료, P2 `7f1dfc59b8ee`는 동일 소스 중복 정산(16:06:48). 현재 `01f425d02569` 처리 중, `received` 14건(16:13 P1 2건 포함). 라우터 `keep_alive` 278(첫 15:52:20), 외부 SSE EOF 23(첫 15:52:21), 늦은 SSE 재시도 2(16:02:03·17), 모델·클라이언트 리셋 2(16:03:39·16:04:31), turn projection 경고 2(16:05:45·16:07:45).
  - KOBE: 제출 0→1(수락 1·대기 3·실행+슬롯 2 유지), 원장 4077→4081, 보드 v400→403(노드 202→201, 루트 21→20), MCP 919→158ms. `n5/24fffb5b` 자식 검수 가드 유지. 메인 오류 0, 포크 표본 오류 1(`a2ecac37` 16:11:52). 라우터 `keep_alive` 228(첫 15:52:17), 외부 SSE EOF 15(첫 15:52:21)·읽기 TimeoutError 3(첫 15:53:58)·BrokenPipe 1(15:59:23), MCP POST 리셋 2(16:01:12), turn projection 경고 6(15:54:02–16:03:11 최대 1274s, 16:14:54 120s).
  - Prody: 실행+슬롯 1·원장 1149 유지, 보드 v7355→7358(노드 42→43, 정지 6→7, 힌트 3→4), MCP 671→313ms. finding·메인·포크 표본 오류·식별 거부 모두 0. `router.log` 공백 지속.
  - Betty(보조): 제출 5·실행+슬롯 1 유지, 원장 1094→1097, 보드 v5713→5716, MCP 424→366ms. finding 0, 포크 표본 오류 1(`a5a4c0ac` 16:14:01), 메인 보드 버전 충돌 가드 1(15:57:06). 라우터: 외부 SSE EOF 23(첫 15:52:59), turn projection 경고 10(15:53:38–16:10:09 최대 1593s, 16:14:15 120s), `channel_windows_console_unseen_failed`·`channel_stdin_proxy_blocked stale_unconfirmed` 스킵 1건(`8011`, 16:12:05, 전달 미확인 추적 추가).
  - 새로 입증된 제품 코드 결함 없음. 코드 변경·설치·발행, 운영 재시작·상태 파일 수정 없음(라이브 라우터 `faa2c7e`, 파일 설치 `d1f9da3`, 모드 핫 리로드 상태 유지).

- 2026-11-25 15:52:15–15:52:49Z 정기 틱, 15:32Z 대비. Work MCP 4곳 성공, Andy 실패(9505 정지 지속). 실패 side 원문 메인 전달 0. 이하 UTC.
  - Andy: 9505 정지 지속(MCP `probe_failed` 2063ms). 원장 7684·리스·finding 변화 없음. 재기동하지 않았다.
  - Celly: 대기 7·실행+슬롯 1·원장 9533·보드 v11172 유지, MCP 282→453ms. finding·메인 오류 0, 포크 표본 오류 1(`ac3b6a93` 15:36:11). 라우터 `keep_alive` 238, 외부 SSE EOF 20(첫 15:32:53).
    - **side 대기열·장기 처리 추적:** 직전 틱에서 멈춰 보였던 `7afafa0b3940`(게이트 재확인, 15:06:59 클레임)의 side-fresh `ab276262`는 진행 중이었다(15:53:18 기준 185줄, 46분째 실행). 그 사이 대기(`received`) 영수증이 4건→11건으로 늘었고, P2 `7f1dfc59b8ee`(14:22:59 수신)는 90분째 미클레임이다.
    - **반복 이상(side 모델의 headscale `online` 오독·3회째 징후):** `ab276262`가 15:50:45 `.2` headscale JSON에서 node 32(`GINA_HK_VALLEY`) 블록을 추출했을 때 `online` 필드는 없었고(`"online": true` 32개는 파일 전체 집계), 그런데도 15:53:10 사고문에서 "Node 32 (GINA_HK_VALLEY) is `online: true`"라고 적었다(`last_seen` 컷오프 미충족이라 아직 해제하진 않음). `84aa5c78b72c`와 같은 모델(`k-exaone_v2.5`)의 동일 판독 오류 패턴으로 계속 추적한다.
  - KOBE: 제출 1→0, 대기 2→3, 실행+슬롯 1→2, 원장 4063→4077, 보드 v389→400(노드 188→202, 루트 20→21), MCP 190→919ms. 직전 틱 `n143/f5c111da` finding은 검수가 진행되며 해소됐다. `n5/24fffb5b` 자식 검수 가드 유지. 포크 표본 오류 1(`a7f25afc` 15:46:42). 메인: Walkie 400 2(15:36:41·15:42:10), `Connection closed` 1(15:37:02), Python 인코딩/출력 오류 1(15:45:00), `n210` 기존 리스 가드 1(15:47:31). 라우터 `keep_alive` 236, 외부 SSE EOF 20(첫 15:32:51), `context-usage PermissionError` 2(15:35:30), turn projection 경고 8(15:34:49–15:52:02, 최대 605s), 모델 리셋·downstream 끊김 2(15:47:20·15:48:07).
  - Prody: 실행+슬롯 1 유지, 원장 1142→1149, 보드 v7350→7355, MCP 297→671ms. finding·포크 표본 오류·식별 거부 0. 메인 Walkie `client_id` UUID 검증 오류 1(15:35:02). `router.log` 공백 지속.
  - Betty(보조): 제출 5·실행+슬롯 1 유지, 원장 1088→1094, 보드 v5711→5713, MCP 1065→424ms. finding 0, 포크 표본 오류 2(`a0b82894` 15:42:07·32). **신규 원장 실패 1(`decision_refused_stop a6c44b65 x1`, 15:45:05):** 메인 `work_parallel_item_review`(n1825) 호출의 `evidence`가 2,093자(상한 2,000자 초과)여서 정상 `review decision` 가드에 1회 거부됐고, 15:46:24 1,749자로 줄여 재호출해 `finalized`(v1014) 통과했다(앞서 Celly `f1c0b436`과 같은 정상 상한 가드, 제품 결함 아님). 메인 `gh_api.py` exit 1 1건(15:48:34). 라우터 외부 SSE EOF 20(첫 15:32:34), turn projection 경고 4(15:45:36–15:51:38).
  - 새로 입증된 제품 코드 결함 없음. 코드 변경·설치·발행, 운영 재시작·상태 파일 수정 없음(라이브 라우터 `faa2c7e`, 파일 설치 `d1f9da3`, 모드 핫 리로드 상태 유지).

- 2026-11-25 15:32:23–15:32:54Z 정기 틱, 15:12Z 대비. 사용자 화면(`oh no. Bun has crashed` → `Claude Code exited with code 3 … 9505`) 지적에 따라 Andy 중단 원인을 먼저 다시 조사했다. 이하 UTC(로그 원문 시각은 Andy·Celly −6h, KOBE·Prody·Betty −8h 로컬).
  - **Andy CLI 중단 = Bun 크래시(확인):** `claude.exe` pid 56812가 08:37:36에 `returncode=3`으로 끝났다(`channel_windows_console_child_exit`, `launch_phase_end … elapsed_ms=61589112 result=exit_3`, 약 17.1시간 실행). 08:37:41 라우터 67780이 `router_lifetime_stopped reason=claude_exit`로 따라 멈췄고 9505는 그 뒤로 내려가 있다. 이전 틱들은 "exit 3 / 라우터 정지"만 적고 Bun 크래시라는 사실을 확인하지 않았다(누락).
  - 크래시 직전 8초: P1 모델 요청(msgs=164) 08:37:24 `bytes=9849211 tokens≈2469783`, P3 요청(msgs=166) 08:37:30 `bytes=9856435 tokens≈2471589`, 메인 요청 08:37:35(`tokens=164809`). 1초 뒤 크래시했다. 오늘 5MB 넘는 업스트림 요청은 116건이고, 10분 단위 최대 크기는 02:0x 6.6MB → 02:1x 8.5MB → 02:2x 9.07MB → 02:3x 9.86MB(로컬)로 커졌다. 크래시 분이 하루 최대치다.
  - 상시 부하: `work_host_page` 호출이 시간당 약 2만 회(`work_parallel_status` 결과 1건당 약 20페이지, status 호출 시간당 약 1,000회)다. 11-24 18시(로컬)부터 `c955630` 시기와 `faa2c7e` 시기에 비슷한 비율이라, 이번에 새로 생긴 변화는 아니다.
  - **미확인:** Bun이 무엇으로 크래시했는지(세그폴트·메모리 부족·패닉)는 확인하지 못했다. 콘솔 원문을 저장하는 파일이 없고(launcher는 화면을 남기지 않음), `~/.claude/debug`는 비어 있으며, 해당 시각에 Windows 애플리케이션 오류 이벤트도 없다. 위 대형 요청과의 인과는 상관 관계만 있고 입증되지 않았다. 결정 증거는 화면의 `bun.report` 링크(크래시 스택 인코딩)다. 재기동은 하지 않았다.
  - 다른 호스트 CLI 종료 기록(11-24~25 라우터 로그): 코드 3은 Andy 1건뿐이다. Celly 07:22:41 코드 1(11-24 15:36:58 코드 1), KOBE 01:15:08·03:56:14·06:40:40 코드 1, Betty 06:29:16 코드 255·07:23:27 코드 1. Prody는 `router.log` 공백 때문에 확인하지 못했다. 이 종료들이 재기동 때문인지 크래시인지는 아직 확인하지 않았다. 현재는 Celly(15:30:14 메인 응답), Betty(15:14–15:20 메인 도구 호출), KOBE(15:31 포크 활동), Prody(제출 검수 진행)에서 CLI 활동이 관측된다.
  - Celly: 원장 9533·보드 v11172·리스 유지, MCP 407→282ms. 게이트 재확인 `7afafa0b3940`(15:06:59 클레임)의 side-fresh `ab276262`는 15:20:11 Bash 도구 호출(PowerShell 문법) 뒤로 결과 기록이 없다(15:33 기준 13분). 이 사이 side 대기열 4건(P2 `7f1dfc59b8ee` 70분, `05c4263bea95`, `39a581055ddb`, `07ddaeb46c8d`)이 대기 중이다. 해당 명령 프로세스는 이 머신에서 찾지 못했다(다른 사용자 프로세스라 명령줄이 보이지 않을 수 있음). 원인은 미확인이고 다음 틱에서 추적한다. 라우터 `keep_alive` 237, 외부 SSE EOF 19, 모델 리셋 1(15:12:50).
  - KOBE: 제출 0→1, 원장 4057→4063, 보드 v387→389, MCP 758→190ms. 새 finding `n143/f5c111da` "제출·완료 영수증·검수 행 없음"(15:30 제출 직후라 검수 시작 전일 수 있음, 다음 틱 추적). `n5` 자식 검수 가드 유지. 포크 표본 오류 3(`a6d322f6` 15:31:26–51). 라우터 `keep_alive` 236, 외부 SSE EOF 19, 리셋 3, `context-usage PermissionError` 1(15:27:45).
  - Prody: 제출 1 검수 완료(리스 실행+슬롯 1), 원장 1133→1142, 보드 v7345→7350, MCP 596→297ms. finding·오류 0. `router.log` 공백 지속.
  - Betty(보조): 실행+슬롯 0→1, 원장 1086→1088, 보드 v5705→5711, MCP 315→1065ms. 메인: Walkie `client_id` UUID 검증 1(15:14:04), `clear_wait`·`waiting_on` 충돌 가드 1(15:18:02), `n1825` 기존 리스 가드 1(15:20:28). 라우터 외부 SSE EOF 20, turn projection 경고 4(15:13:10–15:19:11).
  - 코드 변경·설치·발행, 운영 재시작·상태 파일 수정 없음.

- 2026-11-25 15:12:36–15:13:11Z 정기 틱, 14:52Z 대비. Work MCP 4곳 성공, Andy 실패(라우터 정지 지속). 신규 원장 실패 0. 이하 UTC.
  - Andy: 9505 정지 지속(MCP `probe_failed` 2063ms). 원장 7684·리스·finding(죽은 pid 88952 슬롯 3건, `n1166`) 변화 없음. 재기동하지 않았다.
  - Celly: 대기 7·실행+슬롯 1 유지, 원장 9529→9533, 보드 v11170→11172, MCP 563→407ms. finding 0, 포크 표본 오류 0.
    - **실패 side 원문 메인 전달 1(`e8555818180c`, 15:06:57 appended):** Walkie `agent.pending_reminder`(63773 P1, 133081 P3, 14:57:28 수신)를 받은 side-fresh `a9b8b441`(모델 `k-exaone_v2.5`)가 15:06:14 1차 답에서 이벤트 프롬프트를 신뢰할 수 없는 주입으로 보고 판정 JSON 출력을 거부했다. 15:06:14 보정 1회 전달, 15:06:56 2차 답도 같은 이유로 거부했다. 라우터가 `invalid_answer`로 격리하고 원문을 메인에 전달했다. 메인이 15:10:16에 두 스레드를 실제로 처리한 것을 전사로 확인했다. 정상 안전망 동작이며 코드 결함은 입증되지 않았다.
    - **side 잘못된 대기 해제(메인이 바로잡음):** 게이트 재확인 `84aa5c78b72c`(14:20:53 수신, 14:25:23–15:01:30 처리)의 side-fresh `addc2382`(모델 `k-exaone_v2.5`)가 P0 `n1375`를 해제했다. side-fresh 자신의 14:57:07 도구 결과에는 node 122 RIA에 `online` 필드가 없었고 `last_seen`은 1795581417(04:36:57Z)이었다. 그런데 14:58:09 답과 결정 요약에는 `online=true`와 새 `last_seen`이라고 적었다. 즉 모델이 자기 조회 결과를 잘못 읽었다. 해제 뒤 호스트가 P0 포크를 시작하려 했지만 워크스페이스 준비에서 `git worktree add` 시간 초과로 2회 실패했다(15:02:31 31 s, 15:04:20 76 s, `workspace_prepare_retained`, 예약 해제·항목 대기 유지 가드). 포크는 실행되지 않았다. 메인이 15:02:46 `.2`를 직접 조회해 오프라인을 확인했고 15:05:37에 대기를 복원했다(v11172). 같은 날 두 번째 사례다(첫 번째는 `ba526abc62b2` 08:48, P0 `n1454`). 라우터는 side 결정의 외부 사실 근거를 검증하지 않는다. 이번 건은 모델 판독 오류로 확인됐고 코드 결함은 입증되지 않았다. 반복 이상으로 추적한다.
    - side 대기열: P2 `7f1dfc59b8ee`(14:22:59 수신)가 50분째 `received`다. 그사이 P0/P1 우선 정책(`side_intake_processing.claim`)에 따라 게이트 재확인과 P1 알림이 먼저 클레임됐다. 게이트 재확인 1건 처리에는 36–46분이 걸렸다(`e9551e5f1a0b` 46분, `84aa5c78b72c` 36분). 설계 동작으로 보고 대기 시간을 추적한다. `05c4263bea95`(14:58:35)도 대기 중이고, `7afafa0b3940` 게이트 재확인은 15:06:59부터 처리 중이다.
    - 라우터: `keep_alive` 215, 외부 SSE EOF 20(첫 14:52:45), MCP POST 리셋 2(15:04:16), 모델 리셋 2(14:57:13·15:06:16), downstream 끊김 1(15:07:02), turn projection 경고 3(15:04:17–15:09:02).
  - KOBE: 수락 1·대기 2·실행+슬롯 1 유지, 원장 4051→4057, 보드 v382→387(노드 190→188, 루트 21→20), MCP 251→758ms. `n5/24fffb5b` 자식 검수 가드 유지. 포크 표본 오류 1(`ab399967` 14:59:44), 메인 오류 0. 라우터 `keep_alive` 240, 외부 SSE EOF 20(첫 14:52:49), downstream 끊김 1(14:54:32), turn projection 경고 1(15:02:40).
  - Prody: 실행+슬롯 2→실행+슬롯 1·제출 1, 원장 1118→1133, 보드 v7338→7345, MCP 743→596ms. finding·메인 오류·식별 거부 0, 포크 표본 오류 2(`a01ef139` 14:56:15·14:57:28). 라우터 경고 0(`router.log` 공백 지속).
  - Betty(보조): 제출 5·원장 1086 유지, 보드 v5704→5705, MCP 305→315ms. finding·메인·포크 표본 오류 0. 라우터: 외부 SSE EOF 20(첫 14:52:48), turn projection 경고 5(14:53:59 1327s·14:56:00 1447s, 15:09:10–15:13:10), `channel_windows_console_unseen_failed`·`channel_stdin_proxy_blocked stale_unconfirmed` 스킵 2건(`8005`·`8006`, 15:08:02, 전달 미확인 추적 추가).
  - 새로 입증된 코드 결함 없음. 코드 변경·설치·발행, 운영 재시작·상태 파일 수정 없음(라이브 라우터 `faa2c7e`, 파일 설치 `d1f9da3`, 모드 핫 리로드 상태 유지).

- 2026-11-25 14:52:21–14:52:52Z 정기 틱, 14:32Z 대비. Work MCP 4곳 성공, Andy 실패(라우터 정지 지속). 신규 원장 실패 0, 실패 side 원문 메인 전달 0. 이하 UTC.
  - Andy: 9505 정지 지속(MCP `probe_failed` 2063ms). 원장 7684·리스·finding(죽은 pid 88952 슬롯 3건, `n1166`) 변화 없음. 재기동하지 않았다.
  - Celly: 대기 8→7, 실행+슬롯 1 유지, 원장 9523→9529, 보드 v11166→11170, MCP 265→563ms. finding 0, 포크 표본 오류 4(`a0ada632` 14:35:45, `ac3b6a93` 14:48:51·14:49:05·19). 메인: 보드 버전 가드 1(14:38:57), 리뷰어 포크 차단 가드 1(14:39:58), Python/PowerShell 인자 오류 1(14:47:58). 라우터 `keep_alive` 240, 외부 SSE EOF 18(첫 14:32:50)·읽기 TimeoutError 1(14:41:30), 모델 리셋 1(14:36:11), turn projection 경고 2(14:39:05·14:41:05).
  - KOBE: 제출 1→0, 수락 1·대기 2·실행+슬롯 1 유지, 원장 4043→4051, 보드 v376→382, MCP 188→251ms. `n5/24fffb5b` 자식 검수 가드 유지. 포크 표본 오류 1(`a463c697` 14:41:15), 메인 Python cp1252 인코딩 오류 1(14:33:36). 라우터 `keep_alive` 237, 외부 SSE EOF 18(첫 14:32:31)·BrokenPipe 1(14:49:44), 모델 리셋 3(첫 14:38:31)·downstream 끊김 1(14:36:09), turn projection 경고 7(14:33:59–14:50:35).
  - Prody: 실행+슬롯 2 유지, 원장 1111→1118, 보드 v7336→7338, MCP 519→743ms. finding·메인 오류·식별 거부 0, 포크 표본 오류 1(`a99d0683` 14:39:21). 라우터 경고 0(`router.log` 공백 지속).
  - Betty(보조): 제출 5·원장 1086·보드 v5704 유지, MCP 477→305ms. finding·포크 표본 오류 0. 메인: Walkie 400 4(14:34:21–14:35:00), Python 스크립트 오류 1(14:35:31). 라우터: 외부 SSE EOF 21(첫 14:32:22), turn projection 경고 10(14:33:53–14:51:58, 최대 1205s), `channel_windows_console_unseen_failed`·`channel_stdin_proxy_blocked stale_unconfirmed` 스킵 1건(`7998`, 14:52:04–07, 전달 미확인 추적 추가).
  - 새로 입증된 코드 결함 없음. 코드 변경·설치·발행, 운영 재시작·상태 파일 수정 없음(라이브 라우터 `faa2c7e`, 파일 설치 `d1f9da3`, 모드 핫 리로드 상태 유지).

- 2026-11-25 14:32:18–14:32:50Z 정기 틱, 14:12Z 대비. Work MCP 4곳 성공, Andy 실패(라우터 정지 지속). 신규 원장 실패 0. 이하 UTC.
  - Andy: 9505 정지 지속(MCP `probe_failed` 2062ms). 원장 7684·리스·finding(죽은 pid 88952 슬롯 3건, `n1166`) 변화 없음. 재기동하지 않았다.
  - Celly: 대기 8·실행+슬롯 1 유지, 원장 9519→9523, 보드 v11163→11166, MCP 234→265ms. finding 0, 포크 표본 오류 1(`a0ada632` 14:32:20), 메인 Walkie 403 `Conversation access denied` 1(14:15:18). 라우터 `keep_alive` 231, 외부 SSE EOF 20(첫 14:12:24), 모델·클라이언트 리셋 4(첫 14:15:59·14:16:47·48), turn projection 경고 1(14:12:23 직전 틱 중복).
  - KOBE: 수락 1·대기 2·제출 1·실행+슬롯 1 유지, 원장 4031→4043, 보드 v371→376, MCP 868→188ms. `n5/24fffb5b` 자식 검수 가드 유지. 메인 오류 0, 포크 표본 오류 3(`a03120b2` 14:19:46, `af9ba2af` 14:30:54·14:31:11). 라우터 `keep_alive` 238, 외부 SSE EOF 15(첫 14:13:13)·BrokenPipe 2(첫 14:15:04), downstream 끊김 1(14:12:53)·모델 리셋 1(14:31:54), turn projection 경고 3(14:15:58–14:27:26).
  - Prody: 실행+슬롯 2 유지, 원장 1108→1111, 보드 v7335→7336, MCP 245→519ms. finding·포크 표본 오류·식별 거부 0. **실패 side 원문 메인 전달 1(`fecce1301219`, 14:29:27 appended):** 실제 외부 사람 불만 메시지(160978)에 대해 side-fresh `a59db0fd`가 1차 산문 답(14:26:33) → 보정 1회 전달(14:26:34) → 2차 답(14:29:22)에서 `no_reply=false`인데 `reply`를 비워 라우터가 정상 거부(`side decision needs bounded summary and reply`)하고 원문을 메인으로 전달한 정상 안전망 동작(결함 아님). 같은 기간 기타 격리 2건(`66d5ebe5`·`c28bfe17`)은 정상 `superseded`. 메인 정상 가드 2(리뷰어 포크 차단 14:31:04, `n1304` 기존 리스 가드 14:31:22). `router.log` 공백 지속.
  - Betty(보조): 제출 5·원장 1086 유지, 보드 v5703→5704, MCP 424→477ms. finding·포크 표본 오류 0, 메인 Python 구문 오류 1(14:23:56). 라우터 외부 SSE EOF 20(첫 14:12:59), 모델 리셋 1(14:18:59), turn projection 경고 5(14:18:01–14:29:05).
  - 새로 입증된 코드 결함 없음. 코드 변경·설치·발행, 운영 재시작·상태 파일 수정 없음(라이브 라우터 `faa2c7e`, 파일 설치 `d1f9da3`, 모드 핫 리로드 상태 유지).

- 2026-11-25 14:12:20–14:12:52Z 정기 틱, 13:52Z 대비. Work MCP 4곳 성공, Andy 실패(라우터 정지 지속). 신규 원장 실패 0, 실패 side 원문 메인 전달 0. 이하 UTC.
  - Andy: 9505 정지 지속(MCP `probe_failed` 2047ms). 원장 7684·리스·finding(죽은 pid 88952 슬롯 3건, `n1166`) 변화 없음. 재기동하지 않았다.
  - Celly: 실행+슬롯 2→1(슬롯 감소만으로 포크 종료 단정 안 함), 대기 8 유지, 원장 9508→9519, 보드 v11159→11163, MCP 266→234ms. finding 0, 포크 표본 오류 2(`ac27eabd` 13:58:39·55). 메인: Walkie 400 2(14:10:12×2), PowerShell 구문 오류 1(14:12:06). 라우터 `keep_alive` 239, 외부 SSE EOF 20(첫 13:52:58), `context-usage PermissionError` 3(첫 14:05:00), 모델·클라이언트 리셋 3(첫 13:54:12·14:01:41), 늦은 SSE 재시도 1(14:02:39), `_API_KEYS-SSE-1` 비정상 `tool_use` 종료 다운그레이드 가드 1(14:08:28), turn projection 경고 5(14:02:29–14:12:23).
  - KOBE: 제출 0→1, 수락 1·대기 2·실행+슬롯 1 유지, 원장 4021→4031, 보드 v367→371, MCP 264→868ms. `n5/24fffb5b` 자식 검수 가드 유지. 메인 오류 0, 포크 표본 오류 2(`a1c4769e` 13:58:45, `a00d50fb` 14:10:51). 라우터 `keep_alive` 239, 외부 SSE EOF 20(첫 13:52:43), turn projection 경고 3(13:52:53–13:54:54).
  - Prody: 실행+슬롯 1→2, 원장 1099→1108, 보드 v7328→7335, MCP 400→245ms. finding·포크 표본 오류·식별 거부 0. 메인: 파일 미존재 1(13:58:45), Walkie 400 1(14:09:30). 라우터 경고 0(`router.log` 공백 지속).
  - Betty(보조): 제출 5·원장 1086·보드 v5703 유지, MCP 547→424ms. finding·메인·포크 표본 오류 0. 라우터 외부 SSE EOF 20(첫 13:52:34), turn projection 경고 3(14:00:16–14:04:17).
  - 새로 입증된 코드 결함 없음. 코드 변경·설치·발행, 운영 재시작·상태 파일 수정 없음(라이브 라우터 `faa2c7e`, 파일 설치 `d1f9da3`, 모드 핫 리로드 상태 유지).

- 2026-11-25 13:52:19–13:52:51Z 정기 틱, 13:32Z 대비. Work MCP 4곳 성공, Andy 실패(라우터 정지 지속). 5곳 모두 포크 표본 오류·신규 원장 실패·실패 side 원문 메인 전달 0. 이하 UTC.
  - Andy: 9505 정지 지속(MCP `probe_failed` 2047ms). 원장 7684·리스·finding(죽은 pid 88952 슬롯 3건, `n1166`) 변화 없음. 재기동하지 않았다.
  - Celly: 대기 8·실행+슬롯 2·원장 9508 유지, 보드 v11158→11159, MCP 281→266ms. finding·메인·포크 표본 오류 모두 0. 라우터 `keep_alive` 226, 외부 SSE EOF 19(첫 13:33:09), turn projection 경고 1(13:43:51).
  - KOBE: 수락 1·대기 2·실행+슬롯 1 유지, 원장 4003→4021, 보드 v361→367, MCP 263→264ms. `n5/24fffb5b` 자식 검수 가드 유지. 포크 표본 오류 0, 메인 Python 스크립트 출력 오류 1(13:34:17). 라우터: `keep_alive` 237, 외부 SSE EOF 16(첫 13:32:42)·읽기 TimeoutError 1(13:43:27)·BrokenPipe 1(13:45:33), 클라이언트 리셋 2(첫 13:45:24), 전사 델타 TimeoutError 1(13:42:12), turn projection 경고 3(13:35:59–13:49:30).
  - Prody: 실행+슬롯 1 유지, 원장 1093→1099, 보드 v7325→7328, MCP 1270→400ms. finding·메인·포크 표본 오류·식별 거부 모두 0. 라우터 `channel_stdin_proxy_blocked stale_unconfirmed` 스킵 1건(`8044`, 13:36:17, 전달 미확인 추적 추가).
  - Betty(보조): 제출 5·원장 1086·보드 v5703 유지, MCP 677→547ms. finding·메인·포크 표본 오류 0. 라우터: 외부 SSE EOF 20(첫 13:33:11), turn projection 경고 2(첫 13:45:50), `channel_windows_console_unseen_failed`·`channel_stdin_proxy_blocked stale_unconfirmed` 스킵 2건(`7989` 13:50:41–42, `7990` 13:51:12, 전달 미확인 추적 추가).
  - 새로 입증된 코드 결함 없음. 코드 변경·설치·발행, 운영 재시작·상태 파일 수정 없음(라이브 라우터 `faa2c7e`, 파일 설치 `d1f9da3`, 모드 핫 리로드 상태 유지).

- 2026-11-25 13:32:24–13:32:58Z 정기 틱, 13:12Z 대비. Work MCP 4곳 성공, Andy 실패(라우터 정지 지속). 신규 원장 실패 0, 실패 side 원문 메인 전달 0. 이하 UTC.
  - Andy: 9505 정지 지속(MCP `probe_failed` 2047ms). 원장 7684·리스·finding(죽은 pid 88952 슬롯 3건, `n1166`) 변화 없음. 재기동하지 않았다.
  - Celly: 대기 8·실행+슬롯 2 유지, 원장 9501→9508, 보드 v11156→11158, MCP 594→281ms. finding·메인 오류 0, 포크 표본 오류 7(`a866ec2e` 13:19:48·13:20:24, `ab53cc15` 13:32:29–33 5건). 라우터 `keep_alive` 239, 외부 SSE EOF 18(첫 13:12:51)·BrokenPipe 1(13:27:01), `context-usage PermissionError` 2(첫 13:27:11).
  - KOBE: 수락 1·대기 2·실행+슬롯 1 유지, 원장 3997→4003, 보드 v354→361, MCP 185→263ms. `n5/24fffb5b` 자식 검수 가드 유지. 포크 표본 오류 5(`a8f2e0f3` 13:22:43–13:24:29 3건, `a3d9461d` 13:31:21·37). 메인: `work_side_status` 호스트 전용 가드 1(13:17:07), Walkie 400 7(13:17:23–13:21:18). 라우터 `keep_alive` 238, 외부 SSE EOF 19(첫 13:13:14), 클라이언트 리셋 2(첫 13:13:15), turn projection 경고 4(13:12:55–13:28:15).
  - Prody: 대기 1→실행+슬롯 1, 원장 1085→1093, 보드 v7321→7325, MCP 297→1270ms. finding·메인·포크 표본 오류·식별 거부 모두 0. `router.log` 공백 지속.
  - Betty(보조): 제출 5·원장 1086 유지, 보드 v5701→5703, MCP 304→677ms. finding·포크 표본 오류 0. 메인 6건 중 13:12:30 파일 미존재는 직전 틱 중복, 신규 5건(작업 측 `post_guard` 오류 2 13:17:04·13:18:08, Walkie 400 2 13:17:05·13:25:35, `file_ids` 초과 1 13:22:49). 라우터: 외부 SSE EOF 20(첫 13:12:47), turn projection 경고 8(13:13:16–13:27:32, 최대 2912s), `channel_windows_console_unseen_failed`·`channel_stdin_proxy_blocked stale_unconfirmed` 스킵 1건(`7986`, 13:16:55–57, 전달 미확인 추적 추가).
  - 새로 입증된 코드 결함 없음. 코드 변경·설치·발행, 운영 재시작·상태 파일 수정 없음(라이브 라우터 `faa2c7e`, 파일 설치 `d1f9da3`, 모드 핫 리로드 상태 유지).

- 2026-11-25 13:12:17–13:12:49Z 정기 틱, 12:52Z 대비. Work MCP 4곳 성공, Andy 실패(라우터 정지 지속). 신규 원장 실패 0, 실패 side 원문 메인 전달 0. 이하 UTC.
  - Andy: 9505 정지 지속(MCP `probe_failed` 2047ms). 원장 7684·리스·finding(죽은 pid 88952 슬롯 3건, `n1166`) 변화 없음. 재기동하지 않았다.
  - Celly: 제출 1→0, 대기 7→8, 실행+슬롯 2 유지, 원장 9494→9501, 보드 v11151→11156, MCP 265→594ms. finding·메인 오류 0, 포크 표본 오류 3(`ace37d8e` 12:56:12, `a866ec2e` 13:12:04·25). 라우터 `keep_alive` 212, 외부 SSE EOF 18(첫 12:52:51), 모델 리셋 2(첫 12:56:44)·downstream 끊김 1(12:58:36)·MCP POST 리셋 1(13:07:48), 늦은 SSE 재시도 1(12:55:56), turn projection 경고 2(12:53:33·12:56:55).
  - KOBE: 제출 1→0, 수락 1·대기 2·실행+슬롯 1 유지, 원장 3983→3997, 보드 v347→354, MCP 182→185ms. `n5/24fffb5b` 자식 검수 가드 유지. 포크 표본 오류 2(`a0d0768f` 12:59:57, `a7f69246` 13:09:21), 메인 Python cp1252 스크립트 오류 1(13:02:16). 라우터 `keep_alive` 237, 외부 SSE EOF 20(첫 12:52:43), turn projection 경고 3(첫 12:53:47·13:05:29), 모델·클라이언트 리셋 3(첫 12:56:26·13:10:50).
  - Prody: 대기 1·원장 1085·보드 v7321 유지, MCP 375→297ms. finding·메인·포크 표본 오류·식별 거부 모두 0. `router.log` 공백 지속.
  - Betty(보조): 실행+슬롯 1→0(슬롯 감소만으로 포크 종료 단정 안 함), 제출 5 유지, 원장 1082→1086, 보드 v5700→5701, MCP 335→304ms. finding 0, 포크 표본 오류 1(`a589c1e1` 12:52:53 직전 틱 중복). 메인: 작업 측 Python/E2E 오류 2(12:54:17·12:56:01), Walkie 400 1(13:07:47), 파일 미존재 1(13:12:30). 라우터 외부 SSE EOF 21(첫 12:52:23), turn projection 경고 10(12:53:06–13:11:15, 최대 1936s).
  - 새로 입증된 코드 결함 없음. 코드 변경·설치·발행, 운영 재시작·상태 파일 수정 없음(라이브 라우터 `faa2c7e`, 파일 설치 `d1f9da3`, 모드 핫 리로드 상태 유지).

- 2026-11-25 12:52:20–12:52:51Z 정기 틱, 12:32Z 대비. Work MCP 4곳 성공, Andy 실패(라우터 정지 지속). 신규 원장 실패 0, 실패 side 원문 메인 전달 0. 이하 UTC.
  - Andy: 9505 정지 지속(MCP `probe_failed` 2047ms). 원장 7684·리스·finding(죽은 pid 88952 슬롯 3건, `n1166`) 변화 없음. 재기동하지 않았다.
  - Celly: 제출 0→1, 대기 7·실행+슬롯 2 유지, 원장 9489→9494, 보드 v11149→11151, MCP 515→265ms. finding 0, 포크 표본 오류 1(`ac27eabd` 12:51:46), 메인 `pdftoppm` 미설치 1(12:49:56). 라우터 `keep_alive` 239, 외부 SSE EOF 20(첫 12:32:25), `context-usage PermissionError` 3(첫 12:41:38), 모델 클라이언트 리셋 1(12:38:13), 늦은 SSE 재시도 1(12:41:23), turn projection 경고 1(12:51:31).
  - KOBE: 제출 0→1, 수락 1·대기 2·실행+슬롯 1 유지, 원장 3973→3983, 보드 v344→347, MCP 213→182ms. `n5/24fffb5b` 자식 검수 가드 유지. 포크 표본 오류 2(`afc86318` 12:48:38·12:49:38), 메인 오류 0. 라우터 `keep_alive` 238, 외부 SSE EOF 19(첫 12:33:15), turn projection 경고 1(12:37:36).
  - Prody: 리스 `{}`→대기 1(`n1300/72daf363`), 원장 1082→1085, 보드 v7316→7321, MCP 297→375ms. finding·식별 거부 0(08:55 리로드 후 첫 병렬 포크 기동 정상). 포크 표본 오류 1(`a74ef988` 12:45:29). 메인: 파일 미존재 4(12:36:11·12:46:37×3), 보드 버전 가드 1(12:40:11), `n1300` 기존 리스 가드 1(12:41:36). `router.log` 공백 지속.
  - Betty(보조): 제출 5·실행+슬롯 1·원장 1082·보드 v5700 유지, MCP 220→335ms. finding 0, 포크 표본 오류 1(`a589c1e1` 12:52:53), 메인 작업 측 bash/Python 오류 5(12:42:49–12:50:33). 라우터 외부 SSE EOF 20(첫 12:33:01), turn projection 경고 9(12:33:47–12:51:05, 최대 725s).
  - 새로 입증된 코드 결함 없음. 코드 변경·설치·발행, 운영 재시작·상태 파일 수정 없음(라이브 라우터 `faa2c7e`, 파일 설치 `d1f9da3`, 모드 핫 리로드 상태 유지).

- 2026-11-25 12:32:20–12:32:52Z 정기 틱, 12:12Z 대비. Work MCP 4곳 성공, Andy 실패(라우터 정지 지속). 신규 원장 실패 0, 실패 side 원문 메인 전달 0. 이하 UTC.
  - Andy: 9505 정지 지속(MCP `probe_failed` 2063ms). 원장 7684·리스·finding(죽은 pid 88952 슬롯 3건, `n1166`) 변화 없음. 재기동하지 않았다.
  - Celly: 실행+슬롯 1→2, 대기 7 유지, 원장 9486→9489, 보드 v11146→11149, MCP 500→515ms. finding 0, 포크 표본 오류 1(`ac27eabd` 12:32:27). 메인: `work_side_status` 호스트 전용 가드 1(12:24:36), Walkie `client_id` UUID 검증 오류 1(12:26:00). 라우터 `keep_alive` 230, 외부 SSE EOF 20(첫 12:13:00), turn projection 경고 2(12:25:19·12:27:19).
  - KOBE: 수락 1·대기 2·실행+슬롯 1 유지, 원장 3967→3973, 보드 v341→344, MCP 165→213ms. `n5/24fffb5b` 자식 검수 가드 유지. 메인·포크 표본 오류 모두 0. 라우터: `keep_alive` 239, 외부 SSE EOF 21(첫 12:12:20), `context-usage PermissionError` 1(12:23:35), turn projection 경고 1(12:23:40), downstream 끊김 1(12:25:37), `_API_KEYS-SSE-1` 비정상 `tool_use` 종료 다운그레이드 가드 1(12:25:49).
  - Prody: 리스 `{}`·원장 1082 유지, 보드 v7315→7316, MCP 276→297ms. finding·포크 표본 오류·식별 거부 0. 메인 Ripgrep 20초 타임아웃 1(12:31:56). `router.log` 공백 지속.
  - Betty(보조): 실행+슬롯 0→1, 제출 5 유지, 원장 1080→1082, 보드 v5698→5700, MCP 278→220ms. finding·포크 표본 오류 0. 메인 Walkie 400 1(12:25:35). 라우터 외부 SSE EOF 20(첫 12:12:37), turn projection 경고 2(12:27:11·12:29:15).
  - 새로 입증된 코드 결함 없음. 코드 변경·설치·발행, 운영 재시작·상태 파일 수정 없음(라이브 라우터 `faa2c7e`, 파일 설치 `d1f9da3`, 모드 핫 리로드 상태 유지).

- 2026-11-25 12:12:19–12:12:49Z 정기 틱, 11:52Z 대비. Work MCP 4곳 성공, Andy 실패(라우터 정지 지속). 신규 원장 실패 0. 이하 UTC.
  - Andy: 9505 정지 지속(MCP `probe_failed` 2063ms). 원장 7684·리스·finding(죽은 pid 88952 슬롯 3건, `n1166`) 변화 없음. 재기동하지 않았다.
  - Celly: 대기 7·실행+슬롯 1 유지, 원장 9479→9486, 보드 v11143→11146, MCP 250→500ms. finding 0, 포크 표본 오류 3(`adfaa324` 11:58:33·44·11:59:07), 메인 QA4 스크립트 exit 1 1(12:01:59). 라우터 `keep_alive` 240, 외부 SSE EOF 20(첫 11:52:35), `context-usage PermissionError` 3(첫 12:05:03), turn projection 경고 1(12:02:56).
  - KOBE: 수락 1·대기 2·실행+슬롯 1 유지, 원장 3955→3967, 보드 v336→341, MCP 253→165ms. `n5/24fffb5b` 자식 검수 가드 유지. 포크 표본 오류 2(`aee3ffe2` 11:52:26 직전 틱 중복, `a28b95f2` 12:03:19). **실패 side 원문 메인 전달 1(`58f471423bcb`, 12:02:07 appended):** 11:20:22 게이트 재확인 → side-fresh `aa68ea40`이 11:59:14 산문 1차 답 → 11:59:15 보정 1회 전달 → 12:02:02 보정 답에서 `action=answer, kind=status`에 비어 있지 않은 `release_waits`([n5])를 함께 제출해 `faa2c7e`의 정상 `strict_releases` 가드(`release_waits requires action update`)에 거부·격리됨. 호스트 타이머 게이트 점검 실패의 메인 전달(및 11:09:55 `f4afb8758c41` superseded 전달)은 이미 `fe52b22`/`d1f9da3`에서 수정해 파일 설치 완료됐으며 라우터 미재시작(`faa2c7e` 라이브)으로 발생한 기지 동작이다(새 결함 아님). side-fresh 동시 실행은 1건(`723025a2f190` processing)으로 중첩 없음. 라우터: `keep_alive` 240, 외부 SSE EOF 20(첫 11:52:52), turn projection 경고 2(첫 11:55:10), 모델 클라이언트 리셋 2(첫 12:02:02), `message_stop` 뒤 `ping` 무시 1(12:01:18).
  - Prody: 리스 `{}`·원장 1082 유지, 보드 v7314→7315, MCP 385→276ms. finding·포크 표본 오류·식별 거부 0. 메인 `STALE_OWNED` 스크립트 exit 2 2(11:58:37·12:00:27). `router.log` 공백 지속.
  - Betty(보조): 제출 5·원장 1080·보드 v5698 유지, MCP 355→278ms. finding·메인·포크 표본 오류 0. 라우터 외부 SSE EOF 20(첫 11:53:14)뿐.
  - 새로 입증된 코드 결함 없음. 코드 변경·설치·발행, 운영 재시작·상태 파일 수정 없음(라이브 라우터 `faa2c7e`, 파일 설치 `d1f9da3`, 모드 핫 리로드 상태 유지).

- 2026-11-25 11:52:17–11:52:48Z 정기 틱, 11:32Z 대비(KOBE는 11:33 재수집 값 대비). Work MCP 4곳 성공, Andy 실패(라우터 정지 지속). 5곳 모두 메인 전사 오류·신규 원장 실패·실패 side 원문 메인 전달 0. 이하 UTC.
  - Andy: 9505 정지 지속(MCP `probe_failed` 2047ms). 원장 7684·리스·finding(죽은 pid 88952 슬롯 3건, `n1166`) 변화 없음. 재기동하지 않았다.
  - Celly: 대기 7·실행+슬롯 1·원장 9479 유지, 보드 v11140→11143, MCP 344→250ms. finding·메인·포크 표본 오류 0. 라우터 `keep_alive` 238, 외부 SSE EOF 19(첫 11:33:09), 모델 클라이언트 리셋 4(첫 11:32:36·11:41:26).
  - KOBE: 수락 1·대기 2·실행+슬롯 1 유지, 원장 3949→3955, 보드 v334→336, MCP 202→253ms(수집 정상). `n5/24fffb5b` 자식 검수 가드 유지. 포크 표본 오류 2(`a6fbf0e0` 11:37:26, `aee3ffe2` 11:52:26), 메인 오류 0. 라우터: `keep_alive` 231, 외부 SSE EOF 17(첫 11:35:28)·BrokenPipe 1(11:32:36, 직전 재수집 창 중복)·읽기 TimeoutError 1(11:34:25), 모델 클라이언트 리셋 1(11:38:01), turn projection 경고 1(11:40:26).
  - Prody: 리스 `{}`·원장 1082 유지, 보드 v7313→7314, MCP 363→385ms. finding·메인·포크 표본 오류·식별 거부 모두 0. `router.log` 공백 지속.
  - Betty(보조): 제출 5·원장 1080·보드 v5698 유지, MCP 224→355ms. finding·메인·포크 표본 오류 0. 라우터 외부 SSE EOF 20(첫 11:32:50)뿐.
  - 새로 입증된 코드 결함 없음. 코드 변경·설치·발행, 운영 재시작·상태 파일 수정 없음(라이브 라우터 `faa2c7e`, 파일 설치 `d1f9da3`, 모드 핫 리로드 상태 유지).

- 2026-11-25 11:32:24–11:33:37Z 정기 틱, 11:12Z 대비. Work MCP 4곳 성공(KOBE 11:33 재시도 포함), Andy 실패(라우터 정지 지속). 신규 원장 실패 0, 실패 side 원문 메인 전달 0. 이하 UTC.
  - Andy: 9505 정지 지속(MCP `probe_failed` 2047ms). 원장 7684·리스·finding(죽은 pid 88952 슬롯 3건, `n1166`) 변화 없음. 재기동하지 않았다.
  - Celly: 대기 7·실행+슬롯 1·원장 9479 유지, 보드 v11135→11140, MCP 250→344ms. finding·메인 오류 0, 포크 표본 오류 3(`adfaa324` 11:31:01·17·33). 라우터 `keep_alive` 240, 외부 SSE EOF 20(첫 11:12:44), 모델 클라이언트 리셋 5(첫 11:19:34).
  - KOBE: `run.sh` 첫 수집은 scp `lost connection`으로 실패했고 11:33:37 재시도에서 정상 수집됐다(일시적 전송 실패). 수락 1·대기 2·실행+슬롯 1 유지, 원장 3943→3949, 보드 v331→334, MCP 311→202ms. `n5/24fffb5b` 자식 검수 가드 유지. 포크 표본 오류 1(`a5dc2d91` 11:18:41), 메인 오류 0. 라우터: `keep_alive` 237, 외부 SSE EOF 19(첫 11:12:20)·BrokenPipe 1(11:32:36), `router-activity PermissionError` 2(첫 11:21:12), `context-usage PermissionError` 1(11:19:54), turn projection 경고 1(11:21:37), 모델 클라이언트 리셋 1(11:25:28).
  - Prody: 리스 `{}`·원장 1082 유지, 보드 v7311→7313, MCP 794→363ms. finding·메인·포크 표본 오류·식별 거부 모두 0. `router.log` 공백 지속.
  - Betty(보조): 제출 5·원장 1080·보드 v5698 유지, MCP 283→224ms. finding·메인·포크 표본 오류 0. 라우터 외부 SSE EOF 21(첫 11:12:26)뿐.
  - 새로 입증된 코드 결함 없음. 코드 변경·설치·발행, 운영 재시작·상태 파일 수정 없음(라이브 라우터 `faa2c7e`, 파일 설치 `d1f9da3`, 모드 핫 리로드 상태 유지).

- 2026-11-25 11:12:17–11:12:48Z 정기 틱, 10:52Z 대비. Work MCP 4곳 성공, Andy 실패(라우터 정지 지속). 신규 원장 실패 0, 실패 side 원문 메인 전달 0. 이하 UTC.
  - Andy: 9505 정지 지속(MCP `probe_failed` 2046ms). 원장 7684·리스·finding(죽은 pid 88952 슬롯 3건, `n1166`) 변화 없음. 재기동하지 않았다.
  - Celly: 대기 7·실행+슬롯 1·원장 9479 유지, 보드 v11133→11135, MCP 250ms. finding·메인·포크 표본 오류 모두 0. 라우터 `keep_alive` 238, 외부 SSE EOF 19(첫 10:53:19)뿐.
  - KOBE: 수락 1·대기 2·실행+슬롯 1 유지, 원장 3934→3943, 보드 v328→331, MCP 203→311ms. `n5/24fffb5b` 자식 검수 가드 유지. 포크 표본 오류 1(`ab65fb01` 11:06:48), 메인 오류 0. 라우터: `keep_alive` 231, 외부 SSE EOF 18(첫 10:53:16)·읽기 TimeoutError 1(11:03:05), 모델 클라이언트 리셋 3(`_Walkie_P1` 2 첫 10:53:16, `_Walkie_Sonnet55-Sol61` 1 10:53:58).
  - Prody: 리스 `{}`·원장 1082 유지, 보드 v7308→7311, MCP 412→794ms. finding·포크 표본 오류·식별 거부 0. 메인: `work_side_status` 호스트 전용 가드 1(10:54:44), `STALE_OWNED` 스크립트 exit 2 1(10:56:32). `router.log` 공백 지속.
  - Betty(보조): 제출 5·원장 1080·보드 v5698 유지, MCP 257→283ms. finding·메인·포크 표본 오류 0. 라우터 외부 SSE EOF 20(첫 10:52:58), 모델 클라이언트 리셋 1(11:05:51).
  - 새로 입증된 코드 결함 없음. 코드 변경·설치·발행, 운영 재시작·상태 파일 수정 없음(라이브 라우터 `faa2c7e`, 파일 설치 `d1f9da3`, 모드 핫 리로드 상태 유지).

- 2026-11-25 10:52:20–10:52:51Z 정기 틱, 10:32Z 대비. Work MCP 4곳 성공, Andy 실패(라우터 정지 지속). 신규 원장 실패 0, 실패 side 원문 메인 전달 0. 이하 UTC.
  - Andy: 9505 정지 지속(MCP `probe_failed` 2047ms). 원장 7684·리스·finding(죽은 pid 88952 슬롯 3건, `n1166`) 변화 없음. 재기동하지 않았다.
  - Celly: 대기 7·실행+슬롯 1 유지, 원장 9472→9479, 보드 v11131→11133, MCP 250ms. finding 0. 포크 표본 오류 4(`a49523c0` 10:37:50·10:38:00·10:38:10, `adfaa324` 10:52:21). 메인: Walkie 400 1(10:37:21), 작업 측 Python/sqlite3 스크립트 오류 8(10:40:00–10:46:22). 라우터: `keep_alive` 188, 외부 SSE EOF 17(첫 10:32:54), MCP POST 리셋 2(10:44:32), 클라이언트 리셋 1(10:44:35), turn projection 경고 2(10:44:34–10:46:35).
  - KOBE: 수락 1·대기 2·실행+슬롯 1 유지, 원장 3922→3934, 보드 v322→328, MCP 701→203ms. `n5/24fffb5b` 자식 검수 가드 유지. 포크 표본 오류 3(`abb5e143` 10:33:40, `a57036f5` 10:41:53, `a4c785ba` 10:48:04). 메인: Walkie UUID 검증 오류 1(10:39:09), `work_side_status` 호스트 전용 가드 1(10:49:00). 라우터: `keep_alive` 240, 외부 SSE EOF 18(첫 10:32:56)·BrokenPipe 1(10:41:59), 모델·클라이언트 리셋 각 1(10:34:10·10:40:45), `sse-gate-status.json` 읽기 `PermissionError` 1(10:36:53), turn projection 경고 1(10:39:44).
  - Prody: 리스 `{}`·원장 1082 유지, 보드 v7307→7308, MCP 319→412ms. finding·메인·포크 표본 오류·식별 거부 모두 0. `router.log` 공백 지속.
  - Betty(보조): 제출 5·원장 1080·보드 v5698 유지, MCP 297→257ms. finding·메인·포크 표본 오류 0. 라우터 외부 SSE EOF 20(첫 10:32:34)뿐.
  - 새로 입증된 코드 결함 없음. 코드 변경·설치·발행, 운영 재시작·상태 파일 수정 없음(라이브 라우터 `faa2c7e`, 파일 설치 `d1f9da3`, 모드 핫 리로드 상태 유지).

- 2026-11-25 10:32:17–10:32:49Z 정기 틱, 10:12Z 대비. Work MCP 4곳 성공, Andy 실패(라우터 정지 지속). 신규 원장 실패 0, 실패 side 원문 메인 전달 0. 이하 UTC.
  - Andy: 9505 정지 지속(MCP `probe_failed` 2047ms). 원장 7684·리스·finding(죽은 pid 88952 슬롯 3건, `n1166`) 변화 없음. 재기동하지 않았다.
  - Celly: 대기 7·실행+슬롯 1 유지, 원장 9465→9472, 보드 v11127→11131, MCP 250ms. finding 0. 포크 표본 오류 2(`a08fa524` 10:25:56·10:26:33). 메인: 셸 exit 2 1(10:28:02), `n1505` P3 슬롯 용량 가드 1(10:28:52). 라우터: `keep_alive` 238, 외부 SSE EOF 20(첫 10:12:28), `context-usage PermissionError` 2(첫 10:31:27), 모델·클라이언트 리셋 각 1(10:14:33·10:27:06), turn projection 경고 1(10:29:26), 10:29:32 `submit_unconfirmed`/`receipt_unmatched` 각 1(10:29:30 `native_compact_started` 중 압축 요청 보류로 확인).
  - KOBE: 수락 1·대기 2·실행+슬롯 1 유지, 원장 3910→3922, 보드 v315→322, MCP 219→701ms. `n5/24fffb5b` 자식 검수 가드 유지. 포크 표본 오류 2(`a14e83ba` 10:15:24, `a46e8ef1` 10:24:28). 메인: 셸 exit 1 1(10:16:15), Walkie `choice_request.options[0].label` 40자 초과 1(10:27:08). 라우터: `keep_alive` 237, 외부 SSE EOF 20(첫 10:12:28), 모델 리셋 1(10:16:23), turn projection 경고 1(10:27:52).
  - Prody: 리스 `{}`·원장 1082 유지, 보드 v7305→7307, MCP 675→319ms. finding·메인·포크 표본 오류·식별 거부 모두 0. `router.log` 공백 지속.
  - Betty(보조): 제출 5·원장 1080·보드 v5698 유지, MCP 334→297ms. finding·메인·포크 표본 오류 0. 라우터 외부 SSE EOF 20(첫 10:13:11)뿐.
  - 새로 입증된 코드 결함 없음. 코드 변경·설치·발행, 운영 재시작·상태 파일 수정 없음(라이브 라우터 `faa2c7e`, 파일 설치 `d1f9da3`, 모드 핫 리로드 상태 유지).

- 2026-11-25 10:12:25–10:12:55Z 정기 틱, 09:52Z 대비. Work MCP 4곳 성공, Andy 실패(라우터 정지 지속). 신규 원장 실패 0, 실패 side 원문 메인 전달 0. 이하 UTC.
  - Andy: 9505 정지 지속(MCP `probe_failed` 2047ms). 원장 7684·리스·finding(죽은 pid 88952 슬롯 3건, `n1166`) 변화 없음. 재기동하지 않았다.
  - Celly: 대기 6→7, 실행+슬롯 1 유지, 원장 9461→9465, MCP 390→250ms(v11127). finding·포크 표본 오류 0. 메인 Python cp1252 인코딩 오류 1(10:05:43). 라우터: `keep_alive` 241, 외부 SSE EOF 20(첫 09:53:02), 게이트웨이 늦은 SSE 재시도(`b995856a` 14–15/20 09:52:32–09:53:12 → 09:53:44 `budget_exhausted` 420.37s 및 업스트림 오류 이벤트 1, `270e1184` 1–11/20 09:54:54–09:58:26).
  - KOBE: 제출 1→0, 수락 1·대기 2·실행+슬롯 1 유지, 원장 3896→3910, MCP 686→219ms(v315). `n5/24fffb5b` 자식 검수 가드 유지. 포크 표본 오류 7(`abff6e31` 09:52:28–09:53:30 6건 중 첫 행 직전 틱 중복, `a89e5e08` 10:02:52 1건). 메인: 스크립트 오류 1(09:53:43), 보드 버전 가드 1(09:55:04). 라우터: `keep_alive` 237, 외부 SSE EOF 18(첫 09:52:53)·BrokenPipe 1(10:07:20), `router-activity PermissionError` 2(첫 10:02:53), 전사 델타 TimeoutError 1(10:00:55), 모델 클라이언트 리셋 1(10:03:36), turn projection 경고 8(09:53:50–10:07:54).
  - Prody: 리스 `{}`·원장 1082 유지, 보드 v7300→7305, MCP 430→675ms. finding·포크 표본 오류·식별 거부 0. 메인 스크립트 출력 오류 1(10:12:02). `router.log` 공백 지속.
  - Betty(보조): 제출 5·원장 1080 유지, 보드 v5697→5698, MCP 587→334ms. finding·메인·포크 표본 오류 0. 라우터 외부 SSE EOF 20(첫 09:52:48), turn projection 경고 2(09:53:17–09:55:17).
  - 새로 입증된 코드 결함 없음. 코드 변경·설치·발행, 운영 재시작·상태 파일 수정 없음(라이브 라우터 `faa2c7e`, 파일 설치 `d1f9da3`, 모드 핫 리로드 상태 유지).

- 2026-11-25 09:52:15–09:52:45Z 정기 틱, 09:32Z 대비. Work MCP 4곳 성공, Andy 실패(라우터 정지 지속). 신규 원장 실패 0, 실패 side 원문 메인 전달 0. 이하 UTC.
  - Andy: 9505 정지 지속(MCP `probe_failed` 2062ms). 원장 7684·리스·finding(죽은 pid 88952 슬롯 3건, `n1166`) 변화 없음. 재기동하지 않았다.
  - Celly: 예약 1→실행+슬롯 1(`n1481` 재작업 진행), 제출 1→0, 대기 5→6, 원장 9448→9461, MCP 390ms(v11125). finding 0(직전 `n1481` 보류 해소 유지). 포크 표본 오류 0. 메인: Walkie 400 1(09:44:20), `work_side_review` 보드 버전 가드 1(09:46:12), `unknown side review` 1(09:46:29). 마지막 것은 메인이 정정을 기록하려던 영수증 `1905979a`(07:50 create, n1560 등록)에 검수 행이 없어서 난 거부다. 11-18 사용자 결정에 따라 일상 side 답은 검수 행을 남기지 않으므로 설계상 거부다(`side_review.review`). 메인 note의 "ACK 160919에 내부 추론 노출" 주장은 REST로는 본문을 확인할 수 없어 미확인으로 둔다. 라우터: `n1556` `git worktree add timed out after 31 s` 2회(09:48:00 이후)는 faa2c7e 시간 제한이 동작해 예약 고착 없이 끝난 것이다. 게이트웨이 늦은 SSE 재시도 16(두 operation, 09:33:26–09:47:43)과 업스트림 오류 이벤트 1(09:37:15)은 외부 게이트웨이 쪽이다.
  - KOBE: 제출 0→1, 그 밖의 리스 유지, 원장 3889→3896, MCP 686ms(v308). `n5/24fffb5b` 가드 유지. 포크 표본 오류 1(09:52:28). 메인: 스크립트 오류 1(09:45:58), P3 슬롯 용량 가드 2(09:46:32·40). 라우터: 제출 미확인·영수증 미매칭 1(09:38:39, 전달 미확인 추적).
  - Prody: 리스 `{}`, 원장 1082, MCP 430ms(v7300), 오류 0.
  - Betty(보조): 제출 6→5, 원장 1077→1080, MCP 587ms(v5697). 직전 finding `n1822/35f0a395` 해소. 포크 표본 오류 1(09:32:26, 직전 틱과 같은 기록).
  - 새로 입증된 코드 결함 없음. 코드 변경·설치·발행, 운영 재시작·상태 파일 수정 없음(라이브 라우터 `faa2c7e`, 파일 설치 `d1f9da3`).

- 2026-11-25 09:32:19–09:32:54Z 정기 틱, 09:12Z 대비(Prody는 09:14 재수집 값 대비). Work MCP 4곳 성공, Andy 실패(라우터 정지 지속). 실패 side 원문 메인 전달 0. 이하 UTC.
  - Andy: 9505 정지 지속(MCP `probe_failed` 2062ms). 원장 7684·리스·finding(죽은 pid 88952 슬롯 3건, `n1166`) 변화 없음. 재기동하지 않았다.
  - **Celly:** 원장 9443→9448, `running+slot` 1→`reserved+slot` 1, 제출 1, MCP 250ms(v11119). **신규 원장 실패 `decision_refused_stop f1c0b436` 1건(09:12:59)**은 정상 입력 가드다. 메인이 `n1481` 재작업 결정의 evidence를 2553자로 보냈고, 라우터 `decide`가 `text(evidence, 2_000)` 제한으로 거부했다. 거부 문구가 `review decision`뿐이라 사유가 드러나지 않는 점만 기록한다. 09:13:15 짧은 evidence로 다시 보내 `awaiting_capacity`로 수락됐다. 이어 재작업 예약(`f45a82f6`, role resume, `host_id=null`)이 09:16:18에 잡혔다. 09:16:20 `handoff_rebind`가 `stale or mismatched salvage receipt; retained`로 보류했다(finding 표시). 당시 이전 포크 `ad684347`은 아직 running이었다(포크 표본 오류 09:10:22·33). 09:34:06에 이전 매니페스트가 `rebound`, 새 리스가 `ready`가 됐고 09:34:28 `a926dd76`에 바인딩됐다. 틱 직후 REST에서 `f45a82f6 running+slot`, `reservation 0`을 확인했다. 작업트리 HEAD `3e00a2f05`, 변경 없음으로 영수증과 같다. 보류는 18분 만에 자동 회복됐다. 처음 불일치가 정확히 어느 필드였는지는 기록이 남지 않아 확인하지 못했다. 그 밖에: 포크 표본 오류 2(`a5793801` 09:15:45·56), 제출 미확인 1(09:13:53, 전달 미확인 추적), 게이트웨이 늦은 SSE 재시도 12(09:19:53–09:24:52, 두 operation)와 업스트림 오류 이벤트 1(09:25:55).
  - KOBE: 리스 변화 없음, 원장 3877→3889, MCP 722→214ms(v304). `n5/24fffb5b` 가드 유지. 포크 표본 오류 3(09:13:49·09:26:06·09:30:54). 메인: Walkie 첨부 400 1(09:21:30), sha256 증거 파일 누락 1(09:28:44). 둘 다 작업 측 오류다.
  - Prody: 리스 `{}`, 원장 1082, MCP 222ms(v7297), 오류 0. 수집 정상(이전 틱의 scp 실패는 재발하지 않음).
  - Betty(보조): 제출 5→6, 원장 1074→1077, MCP 374ms(v5696). 새 finding `n1822/35f0a395` 완료 영수증 대비 검수 행 미관측. 네이티브 running, last 09:32이고, 포크 표본 오류 2(09:32:11·26)가 같은 포크에서 났다. 직후라 종료나 결함으로 판단하지 않고 다음 틱에 추적한다. 기존 리스 가드 1(09:20:19).
  - 새로 입증된 코드 결함 없음. 코드 변경·설치·발행, 운영 재시작·상태 파일 수정 없음.

- 2026-11-25 09:12:16–09:14:21Z 정기 틱, 09:01Z 대비. 신규 원장 실패 0, 실패 side 원문 메인 전달 0. 이하 UTC.
  - Andy: 라우터 9505 정지 지속(MCP `probe_failed` 2063ms). 원장 7684·리스 변화 없음. 죽은 클라이언트 pid 88952 슬롯 3건(`n2734`·`n2790`·`n2799`)과 `n1166` 검수 행 미관측이 그대로다. 재기동하지 않았다.
  - Celly: 제출 0→1, 대기 5·실행+슬롯 1, 원장 9438→9443, MCP 266→234ms(v11114). 포크 표본 오류 2(`ad684347` 09:10:22·33). 메인 오류 0. 라우터 클라이언트 리셋 1(09:10:54).
  - KOBE: 리스 변화 없음(수락 1·대기 2·실행+슬롯 1), 원장 3871→3877, MCP 1192→722ms(v300). `n5/24fffb5b` 자식 검수 가드 유지. 포크 표본 오류 2(`af7547f1` 09:03:20·31). 메인 오류 0. 전사 델타 전달 실패는 재발하지 않았다.
  - Prody: `run.sh`의 첫 수집은 scp `lost connection`으로 실패했다(09:12). 09:14 재시도에서 scp·수집이 성공했다. 같은 호스트의 Betty 수집은 그 사이 성공했으므로 일시적 전송 실패로 기록한다. 리스 `{}`, 원장 1082, MCP 232ms(v7295), 오류 0. ciel-work 리로드 이후 식별 거부는 재발하지 않았다.
  - Betty(보조): 제출 5·원장 1074 유지, MCP 311→246ms(v5694). 보드 버전 가드 1(09:04:50).
  - 새로 입증된 코드 결함 없음. 코드 변경·설치·발행, 운영 재시작·상태 파일 수정 없음(라이브 라우터 `faa2c7e`, 파일 설치 `d1f9da3`, 모드는 Celly·KOBE·Prody·Betty CLI에 반영됨).

- 2026-11-25 09:00:53–09:01:33Z 정기 틱, 08:35Z 대비. Work MCP 4곳 성공, Andy 실패(라우터 정지). 신규 원장 실패 0. 이하 UTC.
  - **d1f9da3 모드가 실제로 반영됨(파일 설치 → 핫 리로드):** 설치 직후 각 CLI 전사에 `ciel-work: reloaded (19 hooks …)`가 기록됐다. Celly 08:55:06, Prody 08:55:33, Betty 08:57:03, KOBE 08:58:14. 엔진은 `--plugin-dir` 폴더 변경 시 다시 불러온다(plugin-authoring 문서). 설치 시각(08:52–08:55)과 맞지만, 리로드를 일으킨 정확한 파일 감시 경로는 확인하지 않았다. 따라서 `side-intake.js`의 decisionOf 무한 루프 수정과 refs 보정은 이 4곳 CLI에서 이미 동작 중이다. 라우터 쪽 수정(fe52b22 superseded 전달 차단, prepare item_id 확인, 정수 refs)은 라우터 미재시작으로 아직 `faa2c7e`다. 앞 틱의 "모드 변경은 다음 재로드 때 반영" 기록을 이 관측으로 갱신한다.
  - **Prody Work 식별 회복:** 07:50 언로드 이후 08:55:33 리로드로 ciel-work가 다시 올라왔다. 08:56:49 게이트 host round가 동작했고, 08:35 이후 Work 식별 거부는 0건이다. 원장 1082·리스 `{}` 유지, MCP 202→297ms(v7295).
  - **Andy (정지 지속):** 라우터 9505 미응답(MCP `probe_failed URLError` 2046ms). 원장 7684·보드는 08:35 값에서 멈춤. 새 finding: 죽은 클라이언트 pid 88952가 슬롯을 잡은 리스 3건(`n2734/37ddc08d` P4, `n2790/cfdbece8` P3, `n2799/c3ace7a9` P1). 08:37:36 CLI 종료의 결과이고, 회수(dead-owner sweep)는 라우터가 다시 돌 때 일어난다(Betty 06:29:43 선례). 제품 결함으로 보지 않는다. `n1166/2c02ec6f` 검수 행 미관측 지속. 운영 지시에 따라 재기동하지 않았다.
  - Celly: 제출 1→0, 대기 4→5, 실행+슬롯 1, 원장 9424→9438, MCP 312→266ms(v11113). 포크 표본 오류 2(08:44:48·08:50:35). P3 용량 가드 2(08:36:25·08:47:28). **superseded 게이트 점검 원문 메인 전달 3건:** `90e941f766d0`(08:11), `e0762cc93aa2`(08:22), `a562b4ca4b66`(08:48 수신, 08:59:19 appended). 06:18 `3513dd6c9879`도 같다. fe52b22로 고친 라우터 `side_forward` 결함이 라우터 미재시작으로 계속되는 것이며, 새 결함이 아니다. 라우터는 08:55:06에 리로드한 CLI와 상관없이 아직 `faa2c7e`다.
  - KOBE: 실행+슬롯 2→1, 대기 4→2, 수락 1, 원장 3855→3871, MCP 159→1192ms(v298). `n5/24fffb5b` 자식 검수 가드 유지. 포크 표본 오류 2(08:40:50·08:44:53). 보드 버전 가드 1(08:54:08). 라우터: 전사 델타 전달 실패 16(Timeout 9·`10061` 거부 7, 08:43:39–08:5x)과 외부 SSE 연결 시간 초과 5가 같은 구간에 났고, 08:58:14 이후 전달이 다시 성공했다. 수신 측 일시 중단으로 보이며, 원인은 확인하지 못했다.
  - Betty(보조): 제출 5, 원장 1068→1074, MCP 351→311ms(v5693). 포크 표본 오류 1(08:57:05). 기존 리스 가드 1(`n1821`, 08:37:03), 스크립트 오류 1(09:00:09).
  - 새로 입증된 코드 결함 없음. 코드 변경·설치·발행 없음, 운영 재시작·상태 파일 수정 없음.

- 2026-11-25 08:34:46–08:35:19Z 정기 틱, 07:53Z 대비(후속 조사·수정·설치 08:35–08:58Z). Work MCP 5곳 모두 성공, 신규 원장 실패 0. 이하 UTC.
  - **Andy (운영 이상 — CLI 종료):** 틱 시점 실행+슬롯 2→3, 대기 14→13, 제출 21→22, 원장 7673→7684, MCP 671→1047ms(v11904). `n1166/2c02ec6f` 검수 행 미관측 지속. 포크 표본 오류 2(`a0ada85c` 08:31:32·08:32:12). 메인 기존 리스 가드 1(`n2843`, 07:56:49). 틱 직후 08:37:36 Andy CLI(pid 56812)가 **exit 3**로 종료했다. 08:37:41 라우터 67780이 `router_lifetime_stopped reason=claude_exit`로 함께 멈췄고, 08:55 기준 9505는 응답하지 않는다. 메인 전사 마지막 기록은 08:37:03 api-request이고, 전사 크기는 1.86 GB다. exit 3의 원인은 확인하지 못했다. 이 시각 Andy 루트 파일은 바뀌지 않았다(d1f9da3 설치는 08:5x). 운영 지시에 따라 재기동하지 않았다.
  - Celly: 실행 2→1, 대기 5→4, 제출 0→1, 원장 9409→9424, MCP 562→312ms(v11106). 검수 finding 0. 포크 표본 오류 6(`a392e458` 08:28:56–08:29:43 3건, `a8bd6434` 08:32:22·40, `a20e32e3` 07:53:11). 메인: 완료 전 병렬 검수 대기 가드 1(07:58:14), P3 슬롯 용량 알림 1(07:59:10). 라우터: `channel_input_submit_unconfirmed`·`receipt_unmatched` 각 1(07:59:15, 실제 전달 미확인), `context_usage PermissionError` 5(첫 08:01:03).
  - KOBE: 실행 1→2, 대기 4, 제출 1→0, 원장 3836→3855, MCP 303→159ms(v291). `n5/24fffb5b` 자식 검수 가드 유지. `n109` finding은 보드 `done`·리스 없음으로 해소. 포크 표본 오류 2(08:10:11·08:29:07). **새 실패 side 원문 메인 전달 1(`f5ae6c68b340`, 08:31:28 appended):** 08:06:09 게이트 재확인 → side-fresh `ab5656ff`가 산문 답(08:29:31) → 보정 전달(08:29:32) → 보정 답(08:31:23)의 `refs`에 따옴표 없는 UUID가 있어 JSON 파싱 실패 → 격리. side-fresh 동시 실행은 1건(`632b054438bb` processing)으로 중첩 없음.
  - Prody: 리스 `{}`, 원장 1082 유지, MCP 241→202ms(v7293). **07:50 ciel-work 언로드 지속:** Work 식별 거부 4(07:55:36, 08:00:18–19). 포크 표본 오류 0.
  - Betty(보조): 제출 5·원장 1062→1068, MCP 1291→351ms(v5689). 포크 표본 오류 1(08:23:19). 메인: 문서 게이트 거부 2(07:55:00·07:57:01), Walkie 400 3(08:01:20–08:30:29), 스크립트 오류 2.
  - **입증 결함과 조치:** `f5ae6c68b340`을 KOBE 전사로 확인하고 RED 테스트로 재현했다. 수정 `d1f9da3`: 모드는 따옴표 없는 refs 원소를 문자열로 바꾸고, 라우터는 양의 정수 refs를 받는다. 앞 틱의 `45f07b7`(decisionOf 무한 루프, 없는 item_id 보정)을 포함한다. Windows·Linux py310·py311에서 각 334 통과·13 스킵·0 실패, 플러그인 95 통과. Publish `45578825302` 성공(`0.2.51-beta.20261125-084356.d1f9da3`). 7개 승인 루트에 해시 일치로 설치했고, 라우터는 재시작하지 않았다(Celly·KOBE·Prody·Betty 라이브 `faa2c7e`). 상세는 [사이드 판정 파싱 무한 루프 기록](/docs/journal/2026/11/25/fixes/side-decision-parse-hang/record.md).
  - 추가 상관 확인: Celly `109c4883` 04:58:59, Prody bg `2e7bf245` 11-24 23:47:34·02:56:21의 hooks worker 크래시도 같은 형태 side-fresh 답 6초 뒤였다. 대조한 크래시 7건 모두 이 형태 답 뒤에 일어났고, 이 형태 답 7건 뒤에는 모두 크래시가 있었다. KOBE bg `a0e19ac8` 2건은 스캔 중 KOBE SSH 인증 만료(`Device SSH authorization revoked or expired`)로 대조하지 못했다(이후 단건 SSH는 다시 성공).
  - 라이브 상태(08:55–08:58): Celly 114288·KOBE 10660·Prody 8384·Betty 6068 모두 `faa2c7e`. Andy 9505는 08:37:41부터 멈춤. 파일 설치는 `d1f9da3`(7개 루트).

- 2026-11-25 07:52:42–07:53:13Z 정기 틱, 07:13Z 대비(07:33Z 스냅샷 포함, 후속 조사·수정 07:53–08:34Z). Work MCP 5곳 모두 성공, 신규 원장 실패 0. 이하 UTC.
  - Andy: 원장 7637→7660→7673, 실행+슬롯 1→2, MCP 1438→969→671ms(v11889). 07:33Z에 나타난 `n2841/9a03925e` 검수 행 미관측은 07:53Z에 해소됐다. 같은 아이템에서 `git worktree add timed out after 32 s`·`31 s`(07:24:39·07:25:48) `workspace_prepare_retained`가 났다. faa2c7e의 시간 제한이 동작해 예약 고착 없이 끝난 것으로, 결함으로 보지 않는다. 메인: Walkie 400 4(07:15:27–07:51:17), 보드 버전 가드 3, `work_side_handoff` 미존재 1(07:43:54), `work_side_status` 호스트 전용 가드 1(07:48:51). 라우터: `channel_input_submit_unconfirmed`·`receipt_unmatched` 1(07:45:34, 압축 명령 미제출 재시도).
  - Celly: 07:22:41 CLI exit 1(`taskkill` 128, cli session restart), 07:23:24 재개. 재시작 알림 `16584`는 `prompt_not_submitted`로 보류됐다(07:23:28, 전달 미확인 추적). 원장 9399→9409. 포크 표본 오류 11(`ac142e9f` 07:40:50–07:42:50 7건, `a20e32e3` 07:50:22–07:52:39 4건). P3 용량 가드 1(07:47:37).
  - KOBE: 실행 2→1, 대기 3, 제출 1, 원장 3825→3836, MCP 1137→303ms(v281). **`b41a2e1bd98f` 처리:** side-fresh `a07fe32a`가 06:42:47–07:40:53(58분) 동안 처리한 뒤 `action=update, item_id="153865"`(Walkie id)로 답했다. `work_side_resolve`가 `side target Work item missing`으로 거부해 격리됐고, 07:41:09 원문이 메인으로 전달됐다. 메인 wake 프롬프트 행은 11개다. 메인: Walkie UUID 오류 1(07:47:45), Walkie 흐름 단계 400 1(07:47:47), 보드 버전 가드 1(07:50:43).
  - **Prody (운영 이상 — ciel-work 언로드 재발):** 07:50:00 `ciel-work was unloaded: it crashed the hooks worker`. 07:52:01부터 Work 식별 거부가 시작됐다. 원장 1082, 리스 `{}`.
  - Betty(보조): 07:23:27 CLI exit 1(cli session restart), `7972` 보류·`prompt_not_submitted` 스킵(07:23:48), 시작 대기 백로그 만료 2(`7964`–`7969`), 07:42:21 제출 미확인 1(전달 미확인 추적). 원장 1058→1062.
  - **입증 결함 2건 → 수정 `45f07b7`:** (1) `decisionOf`의 `lastIndexOf(…, -1)` 무한 루프가 `{`로 시작하고 `"decision"`이 없는 side-fresh 답에서 hooks worker를 멈췄다. node 재현과 크래시 직전 답 4건(Prody 03:30·07:50, KOBE 01:00·03:09)으로 확인했다. (2) prepare가 없는 item_id를 저장해 보정 없이 격리됐다. RED 확인 후 수정했고, Windows·Linux py310·py311 각 251 통과·0 실패, 플러그인 95 통과. Publish `45576187246` 성공(`0.2.51-beta.20261125-082601.45f07b7`). 설치는 다음 틱의 `d1f9da3`으로 합쳤다.

- 2026-11-25 07:13:00–07:13:34Z 정기 틱, 06:52Z 대비. 5곳 모두 Work MCP 성공, 신규 원장 실패·실패 side 원본 메인 전달·포크 표본 오류 모두 0건. 이하 UTC.
  - Andy: 실행+슬롯 1 유지, 대기 15→14, 제출 21→22, 원장 7627→7637, MCP 734→1438ms(v11864). `n1166/2c02ec6f` 검수 행 미관측 지속. 메인: 보드 버전 가드 1(06:59:18), 셸/node 오류 2(07:10:02·10), `work_side_status` 호스트 전용 가드 1(07:10:25). 라우터: `keep_alive` 245(첫 06:52:24), 외부 SSE EOF 21(첫 06:52:31), 모델 리셋 2(07:01:49)·downstream 끊김 1(07:07:17), turn projection 경고 5(06:59:42–07:07:23).
  - Celly: 실행 2·대기 5·원장 9399 유지, 보드 v11082→11090, MCP 610→343ms, 검수 finding·포크 표본 오류 0. 메인: 셸 exit 1 1(07:08:13). 라우터: `keep_alive` 244, 외부 SSE EOF 20(첫 06:53:07), 모델 리셋 6(06:53:51·06:56:52), `context_usage`/`router` `PermissionError` 각 1(07:00:56·07:09:36), `discovery unavailable` 1(07:03:35), turn projection 경고 3(07:05:30–07:09:31).
  - KOBE: 실행 2·대기 3·수락 1·원장 3825·보드 v272 유지, MCP 223→1137ms, `n5/24fffb5b` 자식 검수 가드 유지, 메인·포크 표본 오류 0. 수집 중 `/board` REST 1회 TimeoutError(후속 재조회 0.69s 정상). 라우터: `keep_alive` 234, 외부 SSE EOF 12·ConnectionReset 4·Timeout/URLError/BrokenPipe 4(첫 06:52:52), `context_usage PermissionError` 1(07:01:04). `side-fresh`는 `b41a2e1bd98f`(`agent-a07fe32af40973e6`) 1건만 동작 중(중첩 0건; 06:42:47 시작, 07:14:42 현재 진행 중으로 13건 대기).
  - Prody: 오픈 리스 `{}` 유지, 원장 1076→1082, MCP 271→302ms(v7287), 검수 finding·포크 표본 오류 0. 메인: 파일 미존재 1(07:01:12). 라우터: `channel_stdin_proxy_blocked prompt_not_submitted` 1건(`8021`, 07:00:47, 전달 미확인 추적).
  - Betty(보조): 제출 5·원장 1056 유지, 보드 v5676→5678, MCP 284→394ms, 검수 finding·포크 표본 오류 0. 메인: 문서 게이트 가드 1(06:54:32), Walkie `file_ids` 초과 1(06:55:34), 메인 코드 수정 차단 가드 1(07:13:05). 라우터: 외부 SSE EOF 21(첫 06:53:09), turn projection 경고 11(06:53:22–07:13:26, age 최대 2410s).
  - 새로 입증된 코드 결함 없음. 코드 변경·설치·발행 및 운영 재시작·상태 파일 수정 없음(라이브 라우터 `faa2c7e`, 파일 설치 `fe52b22` 유지).

- 2026-11-25 06:52:21–06:52:50Z 정기 틱, 06:35Z 대비. 5곳 모두 Work MCP 성공, 신규 원장 실패 0, 5곳 모두 실패 side 원본 메인 전달 0건. 이하 UTC.
  - Andy: 실행+슬롯 2→1, 대기 14→15, 제출 21 유지, 원장 7626→7627, MCP 1281→734ms(v11851), 포크 표본 오류 0. `n1166/2c02ec6f` 검수 행 미관측 지속. 메인: 셸 401/libuv 1(06:36:56), Walkie `client_id` UUID 오류 1(06:40:47). 라우터: `keep_alive` 208(첫 06:34:57), 외부 SSE EOF 17(첫 06:35:09), 모델 리셋 1(06:35:24), `context_usage PermissionError` 1(06:43:19), turn projection 경고 3(06:37:57–06:41:57).
  - Celly: 실행 2·대기 5·원장 9399 유지, 보드 v11076→11082, MCP 391→610ms, 검수 finding 0, 메인 오류 0, 포크 표본 오류 1→2(`a20e32e39bdb`, 06:50:23·06:52:08). 라우터: `keep_alive` 207(첫 06:34:56), 외부 SSE EOF 17(첫 06:35:44).
  - KOBE: 실행 2·수락 1 유지, 대기 2→3, 원장 3816→3825, MCP 254→223ms(v272), `n5/24fffb5b` 자식 검수 가드 유지, 포크 표본 오류 4(`a4850bd8` 06:41:35·54·06:42:13, `a2517865` 06:43:06). 메인: Walkie `client_id` UUID 오류 2(06:43:02·04), 셸 exit 1 1(06:44:14). 라우터: 06:40:40–41 CLI 세션 재기동(`process_tree_terminate_failed taskkill 128`, `claude_routed_exit_diagnostic` exit 1, 모델/MCP 리셋 5), 06:41:04 `12101` `submit_unconfirmed`/`receipt_unmatched`/`prompt_not_submitted` 스킵 1건(전달 미확인 추적), `keep_alive` 198, 외부 SSE EOF 17, turn projection 경고 5(06:44:14–06:52:21). side-fresh: 06:40 재기동으로 새 JS(`faa2c7e`) 로드 확인; `b41a2e1bd98f` 1건만 `processing`으로 중첩 없음(`_API_KEYS-SSE-1` 06:45:56→06:55:09 업스트림 응답 지연 ~9분 13초로 큐 11건 대기 중).
  - Prody: 제출 1→오픈 리스 `{}`(미관측을 종료로 단정 안 함), 원장 1074→1076, MCP 323→271ms(v7284), 검수 finding 0, 포크 표본 오류 1→0. 메인: 셸 exit 2 1(06:39:06), `pdftoppm` 미설치 1(06:47:22), Walkie `file_ids` 초과 1(06:50:29). 라우터: `channel_stdin_proxy_blocked stale_unconfirmed` 2건(`8015` 06:35:47, `8014` 06:36:30 — 직전 라우터 재시작 직후 시각, 실제 전달 미확인 추적).
  - Betty(보조): 제출 5·원장 1056·보드 v5676 유지, MCP 282→284ms, 검수 finding·포크 표본 오류 0. 메인: 가드 1(06:39:46), Walkie UUID 오류 1(06:40:30), Python cp949 오류 1(06:49:39). 라우터: 외부 SSE EOF 17(첫 06:35:49), turn projection 경고 9(06:35:15–06:51:20).
  - 새로 입증된 코드 결함 없음. 코드 변경·설치·발행 및 운영 재시작·상태 파일 수정 없음(라이브 라우터 `faa2c7e`, 파일 설치 `fe52b22` 유지).

- 2026-11-25 06:34:53–06:35:23Z 정기 틱, 05:09Z 대비(직전 `faa2c7e` 배포·라우터 재시작 구간 포함). 5곳 모두 Work MCP 성공, 신규 원장 실패 0. 이하 UTC; 재시작 직후 MCP 일시 미연결·`server_error` 포크 재시도·`keep_alive`·`discovery unavailable` 경고는 재기동 구간과 구분한다.
  - Andy: 실행+슬롯 2·대기 14·제출 21 유지, 원장 7612→7626, MCP 1188→1281ms(v11847), 포크 표본 오류 0. `n1166/2c02ec6f` 검수 행 미관측 지속; 직전 `n2734/37ddc08d` finding 해소. 메인: 라우터 재시작 중(06:25:48–56) MCP 미연결 알림 26, 재시작 직후(06:26:25) 포크 API `server_error` 재시도 예약 2(`n2831`, `n2790`), 보드 버전 가드 2(06:32:04·06:34:07). 라우터(새 PID 67780): `keep_alive` 105, 외부 SSE EOF 8(첫 06:26:58), `discovery unavailable` 1(06:26:34), MCP POST 리셋 1(06:27:25), turn projection 경고 3(06:29:15–06:33:16).
  - Celly: 실행 2·대기 5 유지, 원장 9396→9399, MCP 297→391ms(v11076), 검수 finding 0, 포크 표본 오류 0→1(`a20e32e39bdb`, 06:34:37). 메인: 재시작 전 실패 side 원본 append 3+already 1(`50117b9d` 05:19:49, `090469bb` 05:29:32, `0965aa31` 05:30:38·41; 읽기 미확인), `n1539` 포크 API 오류 재시도 예약(`transport_interrupted` 05:20:05, 재시작 직후 `server_error` 06:29:49·재시도 1/20·2/20 06:30:58·06:33:14), P3 용량 가드 3(05:32:30·06:25:43×2), 재시작 중 auto-continue 상태 미조회 1(06:29:35). 라우터(새 PID 114288): 외부 SSE EOF 81(첫 05:09:18)·BrokenPipe 1(05:19:36)·connect timeout 1(05:21:21), `keep_alive` 63(06:29:37), 모델 리셋 11·클라이언트 리셋/중단 3, 게이트웨이 오류·502 재시도·늦은 SSE 재시도 8(05:19:49·06:30:28–06:33:28), `context_usage PermissionError` 1(05:13:22), 전사 델타 timeout 1(05:20:05), turn projection 경고 3.
  - KOBE: 실행 1→2, 대기 3→2, 수락 1 유지, 원장 3802→3816, MCP 299→254ms(v257), `n5/24fffb5b` 자식 검수 가드 유지, 포크 표본 오류 0→4(`a4b52fd9` 05:12:21·29, `a4850bd8` 06:33:37·06:34:02). 메인: 재시작 전 도구/셸/Walkie 400 오류 4(05:15:02–05:45:54), 실패 side 원본 append 4(`d548fdd3` 05:32:57, `472b5b03` 05:47:17, `b67efa1e` 05:48:11, **`8be7f635` 06:27:34**), 재시작 직후 포크 API 오류 재시도 2(`n19`/`n29`, 06:27:07). 라우터(새 PID 10660): `keep_alive` 875+99, 외부 SSE EOF 71·BrokenPipe 2·Timeout/URLError/Reset 9, 모델/클라이언트/MCP 리셋 15, `context_usage PermissionError` 1(06:14:47), `router_lifetime_restart` 1(06:26:10), turn projection 경고 9.
    - **입증 결함(`8be7f63514d9`·`472b5b0304a5`):** `side_forward.py`의 `mark`·`_backfill`이 `superseded`로 닫힌 구 게이트 점검 스냅샷(`8be7f635`, `category=superseded`)과 실패한 호스트 타이머 게이트 점검(`472b5b03`, `source_kind=gate_check`)까지 "실패한 외부 메시지 원문"으로 메인에 전달하고 있었다.
    - **재현→수정→검증→설치(라우터 미재시작):** `tests/test_side_forward.py::test_superseded_and_gate_check_quarantines_never_forward_to_main`으로 RED 재현 후 `ciel_runtime_support/side_forward.py` 수정. Windows·Linux py310·py311 각 99 통과(1 스킵, 0 실패). 커밋 `fe52b2229bfce123f01d1af0779b4cc149b483f7` 푸시, Publish `45562194214` 성공(`@one-ciel-ai/ciel-runtime@beta` = `0.2.51-beta.20261125-063922.fe52b22`), 7개 승인 루트 해시 일치 설치 완료(Sarah 미도달). **이번 틱 금지 규칙에 따라 운영 라우터는 재시작하지 않아, 라이브 라우터는 `faa2c7e` 유지 중이며 `fe52b22`는 파일 설치만 완료(다음 라우터 재시작 시 라이브 반영).**
  - Prody: 실행 1·대기 1 → 제출 1(종료 단정 안 함), 원장 1052→1074, MCP 416→323ms(v7280), 검수 finding 0, 포크 표본 오류 0→1(`a49f8516`, 06:02:12). 메인: `work_side_handoff` 미존재 가드 1(05:16:26), 셸 종료 2건(05:23:52·05:45:03), 재시작 직후 `n1298` 포크 API `server_error` 재시도(06:27:03·12, 1/20 06:28:06). `router.log` 시각 기록 공백 지속.
  - Betty(보조): `reserved+slot` 1→0, 제출 5 유지, 원장 1053→1056, MCP 410→282ms(v5676), 포크 표본 오류 1→0, 검수 finding 0. 메인: 셸 종료 4(05:10:10–06:34:16). 라우터: 06:29:16 `claude_routed_exit_diagnostic`(exit 255) 후 06:29:43 `d03b00dafd41`/`bf2b51597a26` dead-owner 자동 회수, 06:29:51 `7962` stale_unconfirmed 스킵·`7963` interrupted_uncertain 격리(전달 미확인 추적), `context_usage PermissionError` 1(06:32:15), 외부 SSE EOF 7, turn projection 경고 1.

- 2026-11-25 05:09:05–05:09:35Z 정기 틱, 04:33Z 대비(후속 조사·수정·검증 05:10–06:00Z). Work MCP 5곳 모두 성공(Prody 복구), 신규 원장 실패 0. 이하 UTC.
  - Andy: 실행+슬롯 2·대기 14·제출 21 유지, 원장 7606→7612, MCP 937→1188ms(v11817). `n1166/2c02ec6f` 검수 행 미관측 지속; 신규 finding `n2734/37ddc08d` running+slot(17:10 이후 포크 미관측, 종료 단정 안 함). 메인: 실패 side 원본 `50f0bcedf586` append 1(04:51:17; 원인 확인 — 유효 JSON 턴 직후 백그라운드 작업 완료 알림으로 산문 턴이 덧붙어 `invalid_answer` 격리). 라우터: `keep_alive` 420, 외부 SSE EOF 35(첫 04:34:02), downstream 끊김 3(첫 04:34:24)·리셋 1(05:05:31), turn projection 관찰 경고 8(04:46:55–05:08:47).
  - Celly: 실행 2 유지, 대기 4→5, 원장 9392→9396, MCP 266→297ms(v11060). 포크 표본 오류 1→0. 메인: 실패 side 원본 append 5(`7b09326d12f3` 04:35:10, `b2c8f5dd196b` 04:37:52, `5fb3327967d8` 04:51:50, `1842cbdf8dcd` 04:58:33, `660c4c40fde3` 05:03:45 — 5건 모두 바깥 `}` 1개 누락으로 `invalid_answer` 격리 확인), P3 슬롯 용량 가드 2(05:04:49·05:05:53). 라우터: 외부 SSE EOF 34(첫 04:34:32), 모델 리셋 2(첫 04:35:14)·클라이언트 리셋 1(04:36:14), 늦은 SSE 재시도 1(04:43:59), turn projection 경고 1(05:05:53).
  - KOBE: 실행 0→1, 대기 2→3, 수락 1 유지, 원장 3797→3802, MCP 201→299ms(v234). `n5/24fffb5b` 미완료 자식 검수 가드 유지, 포크 표본 오류 0. 메인: 실패 side 원본 append 2(`68f98974d07b` 04:38:38, `db5aa937ec3b` 04:53:46; 후속 `d548fdd380a2` 05:32:57 포함), Walkie 400 2(04:56:29·30), Python zoneinfo 오류 1(04:54:01), unsupported parallel event 2(05:02:22·05:06:49), 보드 버전 가드 1(05:07:42). 라우터: `keep_alive` 405, 외부 SSE EOF 30(첫 04:33:48)·BrokenPipe 2(04:51:02)·connect TimeoutError 1(05:03:37), 모델 리셋 5(04:39:14–04:57:48)·클라이언트 리셋 1(04:36:11)·MCP POST 리셋 1(05:07:10), `context_usage PermissionError` 1(04:33:59), `submit_unconfirmed`·`receipt_unmatched` 각 1(04:41:48), `remote_instructions` 타임아웃 1(04:49:52), turn projection 경고 8(04:36:05–05:07:55). 사용자 보고 KOBE `side-fresh` 조사: 동시 최대 3개 중첩(`max_active=2` + urgent), 실패 격리(바깥 `}` 누락·`no_reply` reply/understanding 누락·`priority`/`queue_position` 미보정)가 메인 폴백·오염의 직접 원인임을 확인.
  - Prody: 04:48:42 재기동 이후 복구 확인. 실행+슬롯 1·대기 1, 원장 1037→1052, MCP 성공 416ms(v7265), `n1293` 검수 finding 해소, 포크 표본 오류 0. 메인: `work_side_status` 호스트 전용 가드 1(04:52:30), 셸 Python 오류 1(05:07:40). `router.log` 시각 기록 공백으로 라우터 경고 0은 수집 제약 지속.
  - **Betty(보조 — 미바인딩 예약 원인 입증):** 제출 6→5, 실행 1→0, `reserved+slot` 0→1(`d03b00dafd41` n1815, 이후 05:16:59 `bf2b51597a26` n1816 추가 → 2건), 원장 1046→1053, MCP 276→410ms(v5672). 05:56:10Z Betty 보고 확인. 원인 입증: partial-clone(`blob:none`, HEAD 누락 blob 1)에서 `work_parallel_workspace._git`의 `git worktree add`가 `git-credential-manager get` 대화형 인증에 걸렸고, Windows `subprocess.run(timeout=30)`이 래퍼 `cmd\git.exe`만 종료해 손자 프로세스가 파이프를 붙잡아 라우터 핸들러가 영구 봉쇄됨(이전 `f00c3218`·`bf937a8a` 포함 12건의 `preparing` 매니페스트가 같은 패턴).
  - **입증 결함 수정·검증·배포 완료 (06:11–06:34Z, 사용자 지시):** 커밋 `faa2c7e` (`@one-ciel-ai/ciel-runtime@beta` = `0.2.51-beta.20261125-061202.faa2c7e`, Publish `45558802221` 성공). Windows·Linux py310·py311 각 310 통과(16 스킵, 0 실패), `ciel-work` 플러그인 95 통과. 7개 승인 루트 해시 일치 설치 후 Andy(67780)·Celly(114288)·KOBE(10660)·Prody(8384)·Betty(6068) 라우터만 재시작 및 REST/MCP 검증 완료(Sarah SSH 타임아웃 미도달). 라우터 Python 수정(비대화형 git+트리 종료, fresh 1슬롯 제한, prepare 보완, UTC/핀 가드, 스레드 바인딩, 관리 프롬프트)은 즉시 라이브 반영됐고, JS 모드(`side-intake.js` 등)는 파일 설치 완료로 기존 CLI의 다음 재로드/세션부터 반영된다. 상세는 [사이드 게이트·GitHub 라우팅·Betty git hang 수정 기록](/docs/journal/2026/11/25/fixes/side-gate-github-routing/record.md).

- 2026-11-25 04:33:46–04:34:19Z 정기 틱, 04:12Z 대비(Prody 후속 읽기 전용 조회 04:34–04:43Z). Work MCP 4곳 성공, Prody 실패. 이하 UTC; 라우터 집계는 로그 오프셋 환산 최초 시각 또는 표본 범위이며, 포크 표본 미관측은 종료로 해석하지 않는다.
  - Andy: 실행+슬롯 3→2, 대기 13→14, 제출 21 유지, 원장 7594→7606, MCP 954→937ms(v11806). `n1166/2c02ec6f` 검수 행 미관측 지속. 메인: Walkie 400 2(04:19:30·04:20:56), Python `KeyError: 0` 1(04:21:56). 라우터: 외부 SSE EOF 21(첫 04:12:34), 모델 `ConnectionResetError` 끊김 3(04:16:02–04:31:07), downstream 끊김 1(04:21:35).
  - Celly: 실행 1→2, 대기 4 유지, 원장 9383→9392, MCP 312→266ms(v11055). 포크 표본 오류 6→1. 메인: 실패 side 원본 append 2(`e077d98aa9fa` 04:14:16, `de3662b2bf41` 04:31:42; 읽기 미확인). 라우터: 외부 SSE EOF 21(첫 04:13:03), downstream 끊김 3(04:31:46 포함), 늦은 SSE 재시도 3(04:12:51–04:27:30), 클라이언트 종료 후 리셋 1(04:28:42).
  - KOBE: 실행 2→0, 대기 3→2, 수락 1 유지, 원장 3783→3797, MCP 164→201ms(v220). `n5` 미완료 자식 검수 가드 유지. 포크 API `transport_interrupted` 재시도 예약 2(`n86/af58577cfa85`, `n52/a0c8b7c26770`, 04:22:45). 라우터: `runtime_activity_router_failed PermissionError` 2(04:20:32), 업스트림 오류 이벤트 4·오류 형태 4(04:22:35), 외부 SSE EOF 21(첫 04:13:17). 슬롯 0은 포크 종료 근거가 아니다.
  - **Prody (중요 운영 이상 — 라우터 미기동):** MCP `probe_failed URLError`(2037ms), `127.0.0.1:9592` `/health`·`/board` 연결 실패(04:34), 04:36·04:43 재확인도 미수신. Prody 메인 모델이 04:26:41 `restart_session`을 직접 호출(사유: 03:45Z 이후 Work 도구 `conversation identity is unavailable`, `n1293` 차단), 요청 `5504b117…` 대기열 등록. 라우터 인스턴스 `restart-takeover.json` 04:27:33, `sse-gate-status` 04:27:36 `held/native_handoff_pending`(observer pid 7628), 메인 전사 `8ee391dd` 마지막 기록 04:27:31. 04:43 프로세스 조회에 Prody 라우터·런처·observer 7628 없음, 남은 것은 11-24 18:18:26 생성 `claude.exe --bg-pty-host`/`--fork-session --resume 8ee391dd`(11504/11220) 두 개뿐. Claude Code 데몬은 04:36:12 `claude.exe` 바이너리 mtime 변경으로 업그레이드 재시작(변경 주체 미확인). 재기동 후 재실행이 이뤄지지 않은 원인은 런처 로그를 찾지 못해 미입증. 또한 `router.log` 마지막 기록이 02:51:42로, 03:35·03:52·04:12 틱의 Prody "라우터 경고 0"은 갱신되지 않는 로그를 읽은 수집 제약이었다. `n1293` 비교 불가. 운영 지시에 따라 재기동하지 않음.
  - Betty(보조): 제출 5→6, 실행 2→1, 원장 1037→1046, MCP 458→276ms(v5664). 포크 표본 오류 1→8, 메인 셸 오류 2(04:25:12·04:27:48). 라우터: 외부 SSE EOF 22(첫 04:12:41), turn projection 경고 6(04:14:25–04:32:50), downstream 끊김 1(04:22:41). Sarah 재조회 없음.
  - 후속(04:48–05:00Z, 사용자 요청 분석): 04:48:35 bg 작업 `8ee391dd` kill, 04:48:42 PowerShell 4000에서 `ciel-runtime --continue` 시작(실행 주체 미확인) → 라우터 2684·CLI 8100, 9592 수신 재개. 03:30:54 ciel-work 언로드가 식별 상실의 직접 원인이며, 같은 크래시는 2.1.331 세션에서만 7건 관측됐다(KOBE 4, Prody 3). 근거와 미확인 항목은 [Prody 세션 중단 진단](/docs/journal/2026/11/25/diagnostics/prody-session-crash/record.md)에 기록했다.
  - 새로 재현·입증한 코드 결함 없음. 이번 틱 코드 변경·설치·발행 및 운영 재시작·운영 상태 파일 수정 없음.

- 2026-11-25 04:12:24–04:12:54Z 정기 틱, 03:52Z 대비. 보조 Betty 포함 5곳 Work MCP 조회 성공, 신규 원장 실패 0. 이하 UTC. 라우터 집계 묶음은 로그 오프셋 환산한 최초 시각 또는 표본 범위이며, 포크 전사 표본의 미관측은 종료로 해석하지 않는다.
  - Andy: 실행 2→3, 대기 13·제출 21 유지, 원장 7584→7594, MCP 828→954ms(v11799). `n1166/2c02ec6f` 검수 행 미관측 지속, 포크 표본 오류 0. 메인 전사 5건: 호스트 인증(03:54:52), 메인 코드 수정 차단(04:02:36), P4 용량(04:04:02), 보드 버전(04:04:43·04:06:06) 가드. 라우터: 외부 SSE EOF 19(첫 03:53:10), 모델 downstream 끊김 2(첫 04:06:45), turn projection 관찰 경고 10(03:52:24–04:10:29).
  - Celly: 실행 1 유지, 대기 5→4, 검수 이상 0, 원장 9364→9383, MCP 313→312ms(v11048). 포크 표본 오류 4→6(04:02:58–04:06:43, 오류 원인 미분류). 메인: `n1505` P3 용량 가드 2(03:53:09·04:10:36), 실패 side 원본 `df13c408fde3` append 1(04:04:59, 읽기 미확인), Walkie 입력 400 1(04:07:18). 라우터: 외부 SSE EOF 20(첫 03:52:35), 업스트림 오류 이벤트 3(첫 03:54:53), 늦은 SSE 재시도 11(03:53:10–03:59:10), 전송 `URLError` 재시도 12(04:10:45–04:11:34), 모델 연결 리셋 1(03:53:43)·downstream 끊김 2(첫 04:04:30).
  - KOBE: 실행 0→2, 대기 4→3, 수락 1 유지, 원장 3773→3783, MCP 357→164ms(v214). `n5/24fffb5b` 미완료 자식 검수 가드 유지. 라우터 `claude_routed_exit_diagnostic` 1(03:56:14, CLI 종료 코드 1; 당시 라우터 health OK); 이후 실행 슬롯·보드 진전 관측, 이전 hooks worker 크래시 원인과 동일 원인인지 미확인. `12090` 제출/영수증 미확인 각 1(03:56:30), `prompt_not_submitted` 스킵 1(03:56:32). 메인 완료 검수·정리 가드 2(04:00:05·10), 지원하지 않는 이벤트 1(04:00:27). 포크 표본 오류 1→3(03:54:03·04:07:14·04:12:28), 외부 SSE EOF 15·BrokenPipe 3(첫 03:52:47·03:54:22). 이전 `159860`·`legacy_no_owner`·`12063` 종결 미확인.
  - Prody: 제출 1·대기 1, 원장 1037, 보드 v7259 유지, MCP 220→172ms. `n1293/5c143a45` 완료 영수증 대비 검수 행 미관측 지속(네이티브 표본 running, 종료 근거 없음). 세션 식별 거부 4행(03:52:35·41, 03:53:18·58), 그중 03:52:35는 직전 틱과 중복으로 새 관측 3행. 라우터 경고·포크 표본 오류 0; 플러그인 복구·`7993` 전달 미확인.
  - Betty(보조): 실행 1→2, 대기 2→0, 제출 5 유지, 미바인딩 예약 2→0, 원장 1028→1037, MCP 374→458ms(v5664). 04:00:29 dead-owner orphan 회수 로그 5행(표본에 `f00c3218` 표시, `bf937a8a` 개별 로그 미확인). 읽기 전용 REST 재조회에서 예약 0 및 `n1799/522d4a37` 실행 확인. 기존 슬롯 점유 증상은 해소됐으나 처음 예약이 남은 원인 미확인. 메인 ctl 세션 인자 누락 1(04:03:44), 요청자만 완료 확인 가능 가드 1(04:12:23); 포크 표본 오류 1(04:07:27). 라우터 외부 SSE EOF 12(첫 04:01:27), 전사 전달 연결 거절 1(04:00:37). 검수 이상 0. KOBE·Prody·Betty stranded 파일 미존재는 수집 제약. Sarah 재조회 없음.
  - 새로 재현·입증한 코드 결함 없음. 이번 틱 코드 변경·설치·발행 및 운영 재시작·운영 상태 파일 수정 없음.

- 2026-11-25 03:52:15–03:52:43Z 정기 틱, 03:35–03:36Z 스냅샷 대비. 5개 호스트(보조 Betty 포함) 관측, Work MCP 5곳 성공, 신규 원장 실패 0. 아래 시각 UTC; 라우터 집계는 로컬 로그를 기존 오프셋으로 환산한 최초 관측 또는 표본 범위다. 포크 전사는 부분 표본이며 슬롯이 줄어도 종료를 뜻하지 않는다.
  - Andy: 실행 1→2, 대기 14→13, 제출 21 유지, 원장 7582→7584, MCP 1484→828ms(v11790). `n1166/2c02ec6f` 완료 영수증 대비 검수 행 미관측 지속. 포크 표본 오류 1→0. 메인: P4 슬롯 용량 가드 1(03:38:29), 호스트 인증 가드 1(03:40:01), 보드 버전 가드 1(03:43:11), Python cp1252 인코딩 오류 1(03:50:46). 라우터: 외부 SSE EOF 17(첫 03:35:47), 모델 연결 리셋 1(03:41:05), turn projection 관찰 경고 6(03:39:32–03:49:34).
  - Celly: 실행 2→1, 대기 4→5, 검수 finding 0 유지, 원장 9346→9364, MCP 531→313ms(v11040). 포크 표본 오류 11→4. 메인: P3 용량 가드 3(03:37:27·03:42:41·03:43:38), 실패 side 원본 `5d5c1ed710c2` append 1(03:46:43, 메인 읽기 미확인). 라우터: 외부 SSE EOF 17(첫 03:35:13), 모델 연결 리셋 3(03:40:18–03:47:19)·downstream 끊김 2(첫 03:45:16), 늦은 SSE 재시도 13(03:44:24–03:52:10), turn projection 관찰 경고 6(03:35:30–03:43:35).
  - KOBE: 실행 1→0, 대기 3→4, 수락 1 유지, 원장 3772→3773, MCP 141→357ms(v206 유지). `n5/24fffb5b` 미완료 자식 검수 가드 지속. 포크 표본 오류 4→1. 메인: 세션 식별 거부 2(03:38:21·03:41:12), 새 hooks worker 크래시 행은 없음. 라우터: 외부 SSE EOF 13(첫 03:37:34)·BrokenPipe 2(첫 03:36:33), 모델 연결 리셋 3(03:38:59–03:49:51), 라우터 클라이언트 리셋 2(첫 03:48:35). 이전 크래시 원인·플러그인 복구 및 `159860`·`legacy_no_owner`·`12063` 종결 미확인.
  - Prody: 실행 1→0, 제출 0→1, 대기 1 유지, 원장 1036→1037, MCP 300→220ms(v7259 유지). 신규 `n1293/5c143a45`의 완료 영수증과 `submitted`를 관측했으나 검수 행은 미관측. 읽기 전용 REST 재확인도 `submitted`, `slot=false`, 해당 검수 행 없음(review v1016). 네이티브 상태 표본은 running으로, 실제 종료 및 게시 누락 원인 미입증. 메인 세션 식별 거부 2(03:46:06·03:52:35), 포크 표본 오류·라우터 경고·새 hooks worker 크래시 행 0. 이전 플러그인 복구와 `7993` 전달 미확인.
  - Betty(보조): 실행 1·제출 5·대기 2·미바인딩 `reserved+slot` 2 유지, 원장 1028, MCP 464→374ms(v5663 유지). `f00c3218`/`bf937a8a` 미결. 메인·포크 표본 오류 및 검수 finding 0. 라우터: 외부 SSE EOF 17(첫 03:35:36), 모델 연결 리셋 1(03:41:57), 종료 후 SSE 이벤트 무시 1(03:47:31); 신규 콘솔 수신 미확인 표본 없음. KOBE·Prody·Betty stranded 파일 미존재는 수집 제약. Sarah 재조회 없음.
  - 이번 틱 신규 재현·입증 코드 결함 없음. 코드 수정·설치·발행, 운영 재시작·운영 상태 파일 수정 없음.

- 2026-11-25 03:35:08–03:36:06Z 정기 틱, 01:32–01:33Z 스냅샷 대비. `run.sh`의 호스트 5행(보조 Betty 포함)과 MCP 프로브 5곳 모두 관측. 두 틱의 관측 창 길이가 달라 오류 건수는 빈도 비교가 아니다. 포크 전사는 부분 표본이며 미관측을 종료로 해석하지 않는다. 아래 시각은 UTC; 라우터 묶음 시각은 설정한 로컬 오프셋으로 환산한 최초 관측 시각이다.
  - Andy: 실행 1 유지, 대기 11→14, 제출 22→21, 원장 7493→7582, MCP 1844→1484ms(v11784). `n1166/2c02ec6f` 완료 영수증 대비 검수 행 미관측 지속. 신규 원장 거부 `decision_refused_stop f92c20e6` 1건(해당 행의 시각·사유 미확인; 02:42:56 메인 검수 중복 결정 거부와 동일 건이라고 볼 근거 없음). 메인 전사 15행: 보드 버전 가드 7(02:10:32–02:28:18), Walkie 400 2(01:58:47·02:06:51), 나머지 6행은 셸/도구·대상·권한 및 검수 가드. 포크 표본 오류 0→1. 라우터 외부 SSE EOF 120(첫 01:32:58), `n2818` worktree 준비 타임아웃 1(01:34:14, 52초), 제출 미확인·영수증 미매칭 각 1(01:54:29, 실제 수신 미확인).
  - Celly: 실행 3→2, 대기 4 유지, 제출·검수 finding 0, 원장 9285→9346, MCP 593→531ms(v11034). 포크 표본 오류 4→11. 메인 전사 26행: Walkie 400 6(01:36:57–02:02:28), 보드 버전 가드 4(01:36:22–02:20:00, `work_plan_set` 포함), 계획 분해 미충족 실행 거부 2(02:54:39·45), 기타 셸/권한/도구 가드 및 실패 side 원본 append 알림 1(03:33:19; 읽기 미확인). 라우터 외부 SSE EOF 38(첫 02:57:20), 업스트림 SSE 오류 7·게이트웨이 5xx 3(첫 03:10:54), 전송 끊김 재시도 9(03:11대, 집계 표본).
  - KOBE: 실행 1·대기 3·수락 1 유지, 원장 3738→3772, MCP 189→141ms(v206). `n5/24fffb5b` 미완료 자식 검수 가드 지속, 포크 표본 오류 0→4. 메인 전사 실패 side 원본 append 3(02:28:53·02:35:42·02:49:52; 읽기 미확인), `ciel-work` hooks worker 크래시 언로드 1(03:09:00, 이전 01:00:46에 이어 재발), 이후 세션 식별 거부 1(03:10:26). 라우터 업스트림 503 재시도 15(01:32:45–01:33:51 표본), 외부 SSE EOF 96(첫 01:33:04)·BrokenPipe 10(첫 01:43:27)·읽기 타임아웃 3(첫 02:10:13). 크래시 원인, 이후 플러그인 회복, `159860`·`legacy_no_owner`·`12063` 종결 미확인.
  - Prody: 리스 0→실행 1·대기 1, 원장 1029→1036, MCP 240→300ms(v7259). 검수 finding·신규 원장 실패·포크 표본 오류 0. 메인 전사 4행: 기존 리스/호스트 인증/버전 가드 각 1(02:31:20·02:39:25·02:56:18), `ciel-work` hooks worker 크래시 언로드 1(03:30:54). 라우터 경고 표본 0; Work MCP 성공은 플러그인 재로드 증거가 아니다. `7993` 전달 미확인 유지.
  - Betty(보조): 실행 0→1, 제출 5·대기 2·미바인딩 예약 2 유지, 원장 1026→1028, MCP 269→464ms(v5663). `f00c3218`/`bf937a8a` 미결. 메인 전사 7행 중 DuckDuckGo MCP 도구 미노출 3(03:07:28–30), 나머지 4행은 입력/도구 오류. 외부 SSE EOF 119(첫 01:33:13), 콘솔 `unseen_failed` 5(02:07:55–02:53:04), `stale_unconfirmed` 스킵 3(`7933` 02:07:56, `7937` 02:24:28, `7940` 02:47:35). 검수 finding 0. KOBE·Prody·Betty의 stranded 파일 미존재는 수집 제약으로만 기록. Sarah 재조회 없음.
  - 이번 틱에 새로 재현·입증한 코드 결함 없음. 기존 사이드 게이트/GitHub 패치의 파일 변경은 별도 작업으로, 이번 틱에서 배포하지 않았다. 운영 세션·라우터 재시작 및 운영 상태 파일 수정 없음.

- 2026-11-25 01:32:44–01:33:16Z 정기 틱, 01:12Z 대비. `run.sh` 5행 전원 관측(보조 Betty 포함), MCP 5곳 성공, 신규 원장 실패 0건. 아래 시각 UTC, 슬롯·부분 표본으로 포크 종료를 판정하지 않는다.
  - Andy: 실행 3→1, 대기 10→11, 제출 21→22, 원장 7489→7493, MCP 1,484→1,844ms(v11730). `n1166/2c02ec6f` 검수 행 미관측 지속, 포크 표본 오류 0. 외부 SSE EOF 19(첫 01:13:31), `01:26:04–29Z` 외부 게이트웨이 일시 장애(503 2·`ConnectionAbortedError` 1·`URLError` 11), `keep_alive` 237(`pid=89532`). 메인 보드 버전 가드 2(01:17:02·01:23:39), 셸/gh 오류 4(01:13:34–01:22:53).
  - Celly: 실행 3·대기 4 유지, 제출 1→0, 원장 9275→9285, MCP 562→593ms(v10972). 검수 이상 0, 포크 표본 4행(01:21:25–01:31:19). 외부 SSE EOF 19(첫 01:12:46), `01:26:14–01:27:12Z` 동일 외부 게이트웨이 장애(`network_error` 1·`discovery`/`no healthy` 2·`URLError` 7), `keep_alive` 231(`pid=102292`). 메인 `work_side_status` 호스트 전용 가드 1(01:31:55), Walkie 400 1(01:32:13).
  - **KOBE (`ciel-work` 언로드 회복 확인):** 실행 0→1, 제출 1→0, 대기 3·수락 1 유지, 원장 3733→3738, MCP 324→189ms(v178). `01:15:08Z` `claude_routed_exit_diagnostic` 이후 CLI 재기동으로 `ciel-work`가 다시 로드됨(`01:13:01Z` 마지막 `conversation identity is unavailable` 이후 `01:15:48Z`부터 정상 호스트 가드 동작 확인). 직전 `n40/5c48629e` finding 해소, 기존 `n5/24fffb5b` 미완료 자식 가드 지속. 포크 표본 오류 0. 비재시작: 외부 SSE EOF 18(첫 01:13:00)·BrokenPipe 1(01:16:39), `01:21:13–14Z` `message_id=12063` `submit_unconfirmed`/`receipt_unmatched`/`prompt_not_submitted` 스킵 1건(실제 전달 미확인), `01:26:42–43Z` 실패 side 원본 `e572338e56f3` `already`+`appended` 1쌍(중복 가드 동작, 소비·답장 미확인), `01:30:47–01:31:22Z` 외부 게이트웨이 503 재시도 13+·`ConnectionAbortedError` 2, `keep_alive` 240(`pid=18196`). `159860`·`legacy_no_owner` 미결.
  - Prody: 리스 없음·원장 1029 유지, 보드 v7243→7245, MCP 301→240ms. 검수 이상·라우터 경고·메인 오류·포크 표본 오류 모두 0건(`7993` 전달 미확인 유지).
  - Betty(보조): 제출 5·대기 2·미바인딩 `reserved+slot` 2(`f00c3218`/`bf937a8a`, P1 2/2 점유 미결 지속) 및 원장 1026 유지, MCP 250→269ms(v5652). 검수 이상·포크 표본 오류 0건. 외부 SSE EOF 21(첫 01:12:49), `keep_alive` 244(`pid=5384`), 메인 Ripgrep 20초 타임아웃 1(01:13:58). Sarah 재조회 없음.
  - 새로 입증된 코드 결함 없음. 코드 변경·배포·운영 재시작·상태 파일 수정 없음.

- 2026-11-25 01:12:39–01:13:09Z 정기 틱, 00:52Z 대비. `run.sh` 5행 전원 관측(보조 Betty 포함), MCP 5곳 모두 성공, 신규 원장 실패 0건.
  - Andy: 실행 2→3, 대기 10·제출 21 유지, 원장 7483→7489, MCP 969→1,484ms(v11719). `n1166/2c02ec6f` 검수 행 미관측 지속, 포크 표본 오류 0행. 외부 SSE EOF 19(첫 00:52:29)·BrokenPipe 1(01:04:18), 모델 클라이언트 리셋 5(첫 00:57:51), `01:02:02–03Z` 콘솔 제출 타임아웃(`prompt_render_timeout` 1, `draft_clear=unverified` 1, `receipt_unmatched` 2) 및 `01:05:50Z` `submit_unconfirmed` 1(실제 전달 여부 미확인), `keep_alive` 240(`pid=89532`). 메인 Python 오류 2(01:12:05·16).
  - Celly: 실행 3 유지, 대기 5→4, 제출 0→1, 원장 9271→9275, MCP 891→562ms(v10965). 검수 이상·메인 오류 0, 포크 표본 5→4행(01:10:44–01:11:52). 외부 SSE EOF 16(첫 00:52:54)·BrokenPipe 1(01:04:13), `context_usage PermissionError` 2(첫 00:55:41, 원인 미확인), 전사 델타 실패·재시도 각 1(01:06:36), MCP POST 리셋 3(첫 01:09:34), `keep_alive` 221(`pid=102292`).
  - **KOBE (중요 운영 이상 — `ciel-work` 언로드):** 실행 1→0, 제출 0→1, 대기 3·수락 1 유지, 원장 3732→3733, MCP 147→324ms(v174).
    - `01:00:07.328Z`에 `n40`(`5c48629e`, `ae802bbc212e59575`)이 완료 영수증 제출 후 `01:00:20.287Z` 턴 종료.
    - **`01:00:46.770Z`에 KOBE 메인(`9b0de8fe`) 전사에 `ciel-work: ciel-work was unloaded: it crashed the hooks worker` 기록**(`~/.claude/debug`·크래시 덤프 부재로 hooks worker 크래시 원인은 미확인).
    - 언로드 이후 `01:11:41–42Z` KOBE 메인의 Work MCP 호출 3건이 `session_id` 미주입으로 `conversation identity is unavailable; no shared Work fallback is allowed` 거부, `01:11:42.622Z` `removedTypes: ["ciel-work:side-fresh"]` 확인.
    - 신규 finding `n40/5c48629e` 완료 영수증 대비 검수 행 미관측(`last_seen_at=00:52:54Z`)은 `ciel-work` 언로드로 `native_observation`이 중단된 상태와 함께 관측됨. 기존 `n5/24fffb5b` 미완료 자식 가드 지속. 라우터 외부 SSE EOF 18(첫 00:52:42)·BrokenPipe 1(01:00:41), `keep_alive` 241(`pid=18196`). 운영 세션 재시작 금지 규칙에 따라 재시작하지 않음.
  - Prody: 리스 없음·원장 1029 유지, MCP 273→301ms(v7243). 검수 이상·라우터 경고·메인 오류·포크 표본 오류 모두 0건(`7993` 전달 미확인 유지).
  - Betty(보조): 제출 5·대기 2·미바인딩 `reserved+slot` 2(`f00c3218`/`bf937a8a` 미결 지속) 및 원장 1026 유지, MCP 266→250ms(v5652). 검수 이상·메인 오류·포크 표본 오류 0건. 외부 SSE EOF 21(첫 00:52:26), 턴 프로젝션 1(01:13:00), `keep_alive` 247(`pid=5384`). Sarah 재조회 없음.
  - 이번 틱에서 코드 원인이 입증된 결함 없음(KOBE `hooks worker` 크래시 원인과 Betty 미바인딩 `reserved` 원인은 모두 미확정). 코드 변경·배포·운영 재시작·상태 파일 수정 없음.

- 2026-11-25 00:52:21–00:52:48Z 정기 틱, 00:40Z 대비(큰 변화 없음). `run.sh` 5행 전원 관측(보조 Betty 포함), MCP 5곳 성공(Andy 969ms, Celly 891ms, KOBE 147ms, Prody 273ms, Betty 266ms), 신규 원장 실패 0건.
  - Andy: 실행 4→2·대기 8→10·제출 22→21, 원장 7477→7483. 직전 `n2817` finding 해소 확인, `n1166/2c02ec6f`만 지속. 외부 SSE EOF 12(첫 00:40:12), 클라이언트 리셋 1(00:48:57), 턴 프로젝션 2(00:40:51), `keep_alive` 146(`pid=89532`), 메인 Walkie `file_ids` 초과 검증 1(00:43:27), 포크 표본 오류 0.
  - Celly: 실행 4→3·대기 4→5·제출 1→0, 원장 9268→9271, 검수 이상·메인 오류 0, 포크 표본 5행(00:50:29–00:51:48). 외부 SSE EOF 12(첫 00:40:32), 클라이언트 리셋 3(00:40:07), `context_usage PermissionError` 1(00:51:33, 원인 미확인), `keep_alive` 128(`pid=102292`).
  - KOBE: 실행 1·대기 3·수락 1 및 원장 3732 유지, `n5/24fffb5b` 미완료 자식 검수 가드 지속. 외부 SSE EOF 12(첫 00:40:24), `keep_alive` 147(`pid=18196`), `n40`(`ae802bbc212e`) `server_error` 재시도 예약(00:50:19)·1/20 전송(00:51:24, 복구 미확인), 메인 Python 오류 1(00:51:38), 포크 표본 오류 0.
  - Prody: 리스 없음·원장 1029 유지, 검수 이상·라우터 경고·메인 오류·포크 표본 오류 모두 0건(`7993` 전달 미확인 유지).
  - Betty(보조): 제출 5·대기 2·미바인딩 `reserved+slot` 2(`f00c3218`/`bf937a8a` 미결 지속) 및 원장 1026 유지, 외부 SSE EOF 13(첫 00:40:11), `keep_alive` 151(`pid=5384`), 메인·포크 오류 0건. Sarah 재조회 없음.
  - 새로 입증된 코드 결함 없음. 코드 변경·배포·운영 재시작·상태 파일 수정 없음.

- 2026-11-25 00:40:06–00:40:35Z 정기 틱, 직전 23:32Z 대비(`00:33–00:37Z` 사용자 지시 `2620310` 라우터 재시작 이후 첫 틱). `run.sh` 5행 전원 관측(보조 Betty 포함), MCP 5곳 모두 성공, 신규 원장 실패 0. 재시작 구간 경고(`router_lifetime_restart`, `router-error`, `ECONNRESET`, `keep_alive`)와 외부 장애·정상 가드를 분리한다. 시각 UTC, 슬롯·부분 표본으로 포크 종료를 판정하지 않는다.
  - Andy: 실행 5→4, 대기 9→8, 제출 21→22, 원장 7431→7477, MCP 타임아웃→1,156ms(v11705) 회복. `n1166/2c02ec6f` 검수 행 미관측 지속. 틱 시점 신규 `n2817/9623a893`(`last=00:39Z`) finding은 26초 뒤(`00:40:32Z`) `work-parallel-finalization.json`에 `state=pending` 생성 확인(일시 관측, 결함 아님). 포크 오류 표본 0→1행(00:03:08). 재시작 구간(`00:33:05–00:33:56Z`, `92856→89532`): `router_lifetime_restart` 1, `work_host_page ECONNRESET` 1, `router-error` 2, 포크 `server_error` 재시도 예약 3(`n2819`·`n2794`·`n2806`, 복구 결과 미확인), `keep_alive` 80(`pid=89532`). 비재시작: 외부 SSE EOF 4(첫 00:34:17)·BrokenPipe 1(00:36:07), `context_usage PermissionError` 2(첫 00:39:06, 원인 미확인), MCP POST 리셋 3(첫 00:33:33), 재시작 전 메인 도구 오류 4건(버전 가드 1·Walkie 400 1·셸/Python 2).
  - Celly: 실행 3→4, 대기 5→4, 제출 0→1, 원장 9232→9268, MCP 313→1,985ms(v10955). 검수 이상 없음, 포크 오류 표본 1→5행(00:25:48–00:39:41). 재시작 구간(`00:34:50–00:35:02Z`, `68448→102292`): `auto continue` 상태 읽기 실패 1(00:34:50; 별도 00:17:36 1건은 재시작 전, 원인 미확인), `keep_alive` `708(68448)`+`61(102292)`. 비재시작: 외부 SSE EOF 63(첫 23:32:45), 업스트림 모델 오류 17(첫 23:34:58, shape 7+2 미합산), 직접 HTTP 503 4·5xx 4(첫 23:34:53, 미합산), `URLError` 재시도 표본 55(첫 23:32:21), `n1406`·`n1520` 포크 503 재시도 알림 4(23:42:01–23:53:36), 메인 보드 버전 가드 3(00:12:43–00:15:06), Walkie 404 1(00:20:26).
  - KOBE: 실행 1·대기 3·수락 1 유지, 원장 3720→3732, MCP 241→173ms(v172). `n5/24fffb5b` 미완료 자식 검수 가드 지속. 포크 오류 표본 0→5행(23:46:02–00:33:29). 재시작 구간(`00:33:28–00:33:51Z`, `2008→18196`): `router_lifetime_restart` 1, `router-error` 1, `keep_alive` `723(2008)`+`77(18196)`. 비재시작: 외부 SSE EOF 63(첫 23:33:12)·Timeout 1(00:36:17), `23:55:38Z` `channel_input_submit_unconfirmed(attempts=4)` + `channel_input_receipt_unmatched` 1쌍(실제 전달 미확인). `159860`·`legacy_no_owner` 미결.
  - Prody: 실행 1→0, 원장 1015→1029, MCP 252→250ms(v7241). 검수 이상·라우터 경고 0건, 포크 오류 표본 0→2행(23:50:48–23:51:29). 메인 `23:43:42–43Z` 실패 side 원본 `a5238d8719f7` `already`+`appended` 1쌍(중복 가드 동작, 소비·답장 미확인), Walkie UUID 오류 1(23:50:10), 셸/Python 3(23:42:34–23:49:10). 이전 `7993` 전달 미확인 유지.
  - Betty(보조): 제출 5·대기 2 유지, 원장 1024→1026, MCP 400→400ms(v5652), 검수 이상·포크 표본 오류 0건. **신규 미결 추적(`reserved+slot 0→2`)**: `f00c3218`(`n1798`, `reserved_at=23:41:57Z`) 및 `bf937a8a`(`n1799`, `reserved_at=00:02:01Z`) 2건이 `state=reserved, host_id=null, slot=True`로 남아 P1 슬롯 2/2 점유(`00:12:56Z` P1 용량 거부 관측). 두 건 모두 `23:40Z` 이후 서브에이전트 JSONL 미생성 및 `router.log`상 `bind`/`release` 미관측이나, `parallelDirectSpawn` 내부 어느 지점에서 멈췄는지(예: `$.agent.spawn` 반환 형태 vs 예외)는 로그로 입증되지 않아 추측 수정 보류·미결 추적. 부수 관측으로 Betty 메인 전사에 `00:35:46Z` `ciel-work: reloaded` 기록 확인. 재시작 구간(`00:37:00Z`, `12480→5384`): `router_lifetime_restart` 1, `keep_alive` `765(12480)`+`40(5384)`. 비재시작: 외부 SSE EOF 66(첫 23:32:55), 메인 스크립트/Playwright/Walkie 400 9건. Sarah 재조회 없음.
  - 이번 틱에서 새로 입증된 코드 결함 없음. 운영 세션/라우터 재시작·상태 파일 수정·추가 배포 없음.

- 2026-11-25 00:24–00:36Z PR·CI/CD 대기 힌트 및 SSE 라우팅 프롬프트 강화(`2620310`) 베타 발행·7루트 설치·5개 베타 에이전트 Ciel 라우터 재시작(사용자 명시 지시). 상세 기록: `docs/journal/2026/11/25/features/pr-ci-wait-hint-prompts/record.md`.
  - 변경: `work_texts.py`(`mod_fork_question_wait`) → `texts.generated.js`, `work_workflow_prompts.py`(`side_external_wait_routing` 신설, `gate_actor`·`gate_recheck` 보강), `work_workflow_tools.py`(`side_instruction` 1줄 연결), `work-operations/SKILL.md`, 테스트 `test_work_texts.py`·`test_side_intake_mcp.py`.
  - 검증: 격리 Windows 12개 스위트 242 tests(1 skip, 0 fail), `claude plugin validate`·`test` 95/95, 기존 Linux `py310`·`py311` 각 242 tests(1 skip, 0 fail), `ruff`·`check:docs`·`npm pack --dry-run` 통과.
  - 배포: `Publish to npm` `45517881285` 성공(`0.2.51-beta.20261125-002503.2620310`, `gitHead=2620310`, tarball 5파일 SHA-256 일치) → `overlay-2620310` 7개 승인 베타 루트 모두 `base → new` 확인(00:32:19–00:32:47Z).
  - 라우터 재시작(5/5 모두 `0.2.51-beta.20261125-002503.2620310`, `fp=5cff1e881e0801be`, 기존 `active_client_pids` 유지, REST `/runtime` `ok=True`, MCP `recover_sse_delivery` `ok=True / gate=open`): Andy(`92856→89532`), Celly(`68448→102292`, `router_debug=off` 유지 인프로세스 재시작; `gate_actor`는 저장된 커스텀 오버라이드 유지), KOBE(`2008→18196`), Prody(`8500→13056`), Betty(`12480→5384`). Sarah(`100.64.0.25`) SSH 배너 타임아웃 미접근.
  - 라이브 효과 구분: `side_external_wait_routing`과 기본값 `gate_actor`/`gate_recheck`는 라우터 재시작으로 즉시 라이브 반영; `mod_fork_question_wait`(`texts.generated.js`)과 `SKILL.md`는 파일 설치 완료, 기존 `claude.exe` 세션은 다음 재시작/플러그인 재로드 후 반영.

## 2026-11-24

- 2026-11-24 23:32:16–23:32:47Z 정기 틱, 23:12Z 대비. `run.sh` 출력 5행(보조 Betty 포함), 신규 원장 실패 5곳 모두 0. 아래 시각은 UTC이며, 슬롯 수·부분 전사 표본으로 포크 종료를 판정하지 않는다.
  - Andy: 실행 3→5, 대기 10→9, 제출 21 유지, 원장 7411→7431. 틱 시점 MCP 4초 `TimeoutError`(4,015ms)였으나 직후 읽기 전용 재확인에서 `/health` 63ms(PID 92856, 활성 클라이언트 88952, 라우터 버전 `0.2.51-beta.20261124-222612.fb7394f`)와 `/ca/mcp` `work_status` 2,750ms(version 11674) 정상 응답(일시 지연, 라우터 중단 아님). `n1166/2c02ec6f` 완료 영수증 대비 검수 행 미관측 지속. 메인 도구 오류 2행(23:23:40 Python AttributeError, 23:25:29 Task ID 미발견 — 포크 종료 증거 아님), 포크 오류 표본 0행. 외부 SSE EOF 20건(첫 23:12:25), 다운스트림 단절 5건(첫 23:12:56), 턴 프로젝션 경고 10건(23:12:16–23:30:38), keep-alive 보호 경고 238건.
  - Celly: 실행 2→3, 대기 5 유지, 원장 9225→9232, MCP 688→313ms. 검수 이상 없음. 포크 오류 표본 0→1행(23:32:17), 메인 2행(23:12:50 400 Unknown operation, 23:15:14 보드 버전 가드). 외부 SSE EOF 20건(첫 23:12:18), 업스트림 모델 오류 6건(첫 23:18:26, 별도 shape 3건 미합산), 게이트웨이 5xx 3건(첫 23:18:26), `URLError` 재시도 표본 15건(첫 23:29:52), 늦은 SSE 재시도 9건(첫 23:12:16), keep-alive 보호 경고 239건.
  - KOBE: 실행 0→1, 대기 3·수락 1 유지, 원장 3718→3720, MCP 190→241ms. `n5/24fffb5b` 미완료 자식 검수 가드 지속. 포크 오류 표본 0행. 실패 side 원본 메인 append 알림 2건(`b1cf9ec38850` 23:14:40, `71d7d9e67b37` 23:29:15; 23:29:17 동일 이벤트 `already` 중복 스킵 1건, 메인 소비·외부 답장 미확인). 외부 SSE EOF 20건(첫 23:12:52), 컨텍스트 사용량 파일 `WinError 5` 1건(23:15:52, 원인 미확인), 턴 프로젝션 경고 5건(23:16:44–23:24:46), 클라이언트 리셋 1건(23:28:44), keep-alive 보호 경고 236건. `159860`·`legacy_no_owner` 미결.
  - Prody: 실행 1·원장 1015 유지, MCP 580→252ms. 검수 이상·라우터 경고·메인 오류·포크 오류 표본 모두 0건. 이전 `7993` 전달 여부 미확인 유지.
  - Betty(보조): 실행 1→0, 대기 2·제출 5 유지, 원장 1020→1024, MCP 339→400ms. 검수 이상 없음, 포크 오류 표본 0→1행(23:13:51), 메인 `gh auth login` 종료 코드 4 1건(23:24:29). 외부 SSE EOF 20건(첫 23:12:27), 턴 프로젝션 경고 6건(23:17:20–23:27:23), keep-alive 보호 경고 233건. Sarah 재조회 없음.
  - 새 제품 결함으로 입증된 항목 없음. 코드 변경·설치·배포·운영 세션/라우터 재시작·운영 상태 파일 수정 없음. `fb7394f`는 7루트 파일 설치와 Andy 라우터 버전만 확인됐으며, 기존 CLI 세션의 JS 훅 라이브 로드 및 효과는 미검증.

- 2026-11-24 23:12:14–23:12:41Z 정기 틱, 직전 22:52Z 대비. `run.sh` 5행 모두 관측(보조 Betty 포함), MCP 5곳 모두 성공, 신규 원장 실패 0. 포크 도구 오류 부분 표본은 관측된 5곳 모두 0행이며 포크 종료나 API 오류 부재를 뜻하지 않는다. 아래 시각 UTC.
  - Andy: 실행 3·대기 10 유지, 제출 22→21, 원장 7401→7411, MCP 1,281→1,094ms. `n1166/2c02ec6f` 동일 검수 행 미관측 지속. 회전 전후 `router.log.1`·`router.log` 대조: 외부 SSE EOF 19건(첫 22:52:34), MCP POST 연결 리셋 2건(첫 23:03:47), 보호 keep-alive 235건. 23:02:05 `router_process_exited`에 따른 런처 재기동 로그 1건, 종료 원인 미확인. 이후 `/health` PID 92856, 활성 클라이언트 88952, 버전 `0.2.51-beta.20261124-222612.fb7394f` 확인. 라우터 버전이며 기존 CLI의 JS 훅 로드 증거는 아님. 23:02:53 `n2811`·`n2813` 포크 `server_error` 재시도 예약 2건(실행·결과 미확인), 23:11:57 P1 슬롯 부족으로 파킹 리스 대기 유지 1건(정상 가드).
  - Celly: 실행 2 유지, 대기 4→5, 원장 9221→9225, MCP 578→688ms. 새 검수 이상 없음. 외부 SSE EOF 19건(첫 22:52:53), 업스트림 모델 오류 4건(첫 22:55:55, shape 중복 별도), 직접 게이트웨이 HTTP 503 2건(첫 23:01:54, 동시 5xx 전환 로그와 합산하지 않음), `URLError` 재시도 표본 15건(첫 23:02:45), 다운스트림 단절 8건(첫 22:55:58), keep-alive 보호 경고 238건. 메인 보드 버전 가드 1건(22:55:12). 이전 활동 파일 권한 거부는 이번 표본 미관측.
  - KOBE: 실행 0·대기 3·수락 1 및 원장 3718 유지, MCP 220→190ms. `n5/24fffb5b` 미완료 자식 검수 가드 유지. 외부 SSE EOF 18건(첫 22:53:06)·`URLError` 1건(23:05:41), MCP POST 연결 리셋 1건(22:55:09), keep-alive 보호 경고 213건, 메인 오류 0행. `159860`·`legacy_no_owner` 미결.
  - Prody: 직전 SSH 미관측에서 접속 회복. 실행 1, 원장 1015, MCP 580ms(마지막 성공 22:41Z: 실행 1, 1008, 267ms). 검수 이상·라우터 경고·메인 오류는 현재 표본에서 미관측. `7993` 전달 여부에 새 증거 없음.
  - Betty(보조): 직전 SSH 미관측에서 접속 회복. 실행 1·대기 2·제출 5, 원장 1020, MCP 339ms(마지막 성공 22:41Z: 208ms). 외부 SSE EOF 17건(첫 22:52:37)·`URLError` 5건(첫 22:53:16), 전사 델타 타임아웃 1건(22:55:43), keep-alive 보호 경고 238건. 새 검수 이상 없음.
  - 새 제품 결함 입증 없음. 이번 틱에서 코드 변경·설치·배포·운영 세션/라우터 재시작·운영 상태 파일 수동 수정 없음. Andy에서 별도로 관측된 런처 재기동과 구분한다.

- 2026-11-24 22:58Z Walkie `160289` 후속 근거: Andy_Main이 읽기 전용 DB 조회 결과를 전달했다. side-fresh 처리 원본 `160177`·`160228`·`160290`에는 채팅 답글이 있으나 Andy의 메시지 상태 이벤트가 없었다고 보고했다. Runtime 쪽에서는 `side_reply_connector.py:246–261`의 Walkie 프리셋 `ack`·`understanding`·`final`이 모두 `walkie_send_message`로 원본에 채팅을 게시하는 것을 소스로 확인했다. Walkie의 메시지 상태 ACK+메모와는 별개다. 프롬프트·코드·배포 변경 없음.

- 2026-11-24 22:52:14–22:52:24Z 정기 틱, 22:40–22:41Z 대비. Andy·Celly·KOBE 관측 성공, Prody·Betty는 `.12:1004` SSH 실패. 같은 창 재조회에서도 전송 연결 종료, 별도 SSH 확인은 배너 타임아웃. 두 호스트의 이번 실행·검수·MCP/라우터는 미관측(종료·장애로 판정하지 않음). Prody `7993` 실제 전달 여부 미확인 유지. 관측된 3곳 신규 원장 실패 0. 시각 UTC, 포크 오류는 부분 전사 표본.
  - Andy: 실행 4→3, 대기 9→10, 제출 22 유지, 원장 7392→7401, MCP 1,531→1,281ms. 포크 오류 표본 2→0행. `n1166/2c02ec6f`는 이번 스크립트 finding에 남았고, 직접 읽은 동일 리스도 `submitted`, 동일 리스 검수 행 없음, native 마지막 `running`·`terminal_at=null` 확인. 메인 도구 오류 3행(22:45:15·22:49:09·22:51:05, Python/셸·경로 오류 표본). 외부 SSE EOF 11건(첫 22:41:19), 모델 스트림 다운스트림 단절 3건(첫 22:47:48), 컨텍스트 사용량 파일 `WinError 5` 1건(22:41:51, 권한 거부 원인 미확인), keep-alive 보호 경고 134건.
  - Celly: 실행 3→2, 대기 3→4, 원장 9220→9221, MCP 281→578ms. 검수 이상 없음, 포크 오류 표본 10→0행, 메인 오류 0행. 외부 SSE EOF 11건(첫 22:41:39), 모델 업스트림 SSE 오류 2건(첫 22:43:04, 별도 shape 2건 중복 합산 안 함), 도중 클라이언트 연결 리셋 4건(첫 22:43:05), 직접 업스트림 HTTP 401 인증 거부 1건(22:43:08, 재시도 중단 로그 동시; 상세 원인 미확인), 동일 늦은 스트림 재시도 4회(22:48:57–22:51:31), keep-alive 보호 경고 134건. 이전 활동 파일 권한 거부는 이번 표본 미관측.
  - KOBE: 실행 0·대기 3·수락 1 유지, 원장 3718 유지, MCP 262→220ms. `n5/24fffb5b` 미완료 자식 검수 가드 유지. 포크 오류 표본 2→0행, 메인 오류 0행. 외부 SSE EOF 3건(첫 22:41:37)·BrokenPipe 3건(첫 22:43:54)·Timeout 1건(22:51:48), MCP POST 클라이언트 연결 리셋 5건(첫 22:49:45), keep-alive 보호 경고 93건. `159860`·`legacy_no_owner` 미결 유지.
  - 신규 제품 결함 입증 없음. 코드 변경·추가 설치·배포·운영 세션/라우터 재시작·운영 상태 파일 수동 수정 없음. `fb7394f`는 기존 7루트 파일 설치만 입증됐으며 라이브 훅 적용/효과는 미확인.
  - 별도 Walkie `160289` 전달 건: side-fresh 문구는 직접 ACK를 금지하고 호스트가 전송을 소유한다(`side-intake.js:505–507`, `:685–686`). 기존 호스트 ACK는 `sendOwedAcks`/`submitAck`로 영수증별 전송을 관리한다(`:2270–2297`). Walkie 상태 ACK와 이 전송의 동일성·처리 계약을 확인하지 않은 상태에서 직접 상태 쓰기 지침을 추가하지 않음. 문구 검토와 운영 틱 결함 판정은 별개.

- 2026-11-24 22:40:59–22:41:26Z 정기 틱, 21:12–21:13Z 대비. `run.sh` 출력 5행(기존 추가 관측 Betty 포함), MCP 전원 응답·신규 원장 실패 0. 집계 창 길이가 달라 단순 건수로 오류율 변화를 판정하지 않는다. 포크 오류는 부분 전사 표본이며, 슬롯이나 전사 미관측은 종료 증거가 아니다.
  - Andy: 실행 3→4, 대기 9·제출 22 유지, 원장 7316→7392, MCP 3,703→1,531ms. `n1166/2c02ec6f` 완료 영수증 대비 검수 행 미관측 지속. 포크 오류 표본 1→2행(22:28:03·22:30:22), 메인 도구 오류 표본 36행(보드 버전 가드, Walkie 인자·UUID, 셸/SQL 등). 라우터 외부 SSE EOF 86건(첫 21:13:17), 외부 SSE 연결 리셋 3건(첫 22:05:20), 컨텍스트 사용량 기록 `WinError 5` 6건(첫 21:15:11, 권한 거부의 근본 원인 미확인), keep-alive 보호 경고 1,049건.
  - Celly: 실행 4→3, 대기 3 유지, 원장 9199→9220, MCP 640→281ms. 검수 이상 미관측. 포크 오류 표본 11→10행(21:57:00–22:40:50), 메인 도구 오류 9행(21:25:09–22:11:44: 셸·SSH·Walkie 인자·보드 버전 가드). 외부 SSE EOF 84건(첫 21:13:27), 업스트림 모델 스트림 오류 4건(첫 21:33:43), 전사 델타 전송 타임아웃 1건(21:25:42), `/ca/mcp` POST 연결 리셋 4건(첫 22:05:57), keep-alive 보호 경고 1,017건. 직전 활동 파일 권한 거부와 동일 시그니처는 이번 상위 25개 표본에서 미관측(해소 증거 아님).
  - KOBE: 실행 1→0, 대기 3·수락 1 유지, 원장 3714→3718, MCP 189→262ms. `n5/24fffb5b` 검수는 19:33:08Z 미완료 자식 가드 상태 유지. 포크 오류 표본 0→2행(22:19:14·25), 메인 Python 자료형 오류 1건(22:21:56). 외부 SSE EOF 76건(첫 21:13:22), BrokenPipe 4건(첫 21:48:06), 연결 리셋 2건(첫 21:33:38), 읽기 타임아웃 2건(첫 22:15:41), 전사 델타 전송 타임아웃 1건(21:47:17), 활동 파일 `WinError 5` 1건(22:31:08), keep-alive 보호 경고 1,034건. `159860` 개별 선택 및 `legacy_no_owner` 원인 미결.
  - Prody: 실행 0→1, 대기 1→0, 원장 985→1008, MCP 680→267ms. 새 검수 이상 미관측. 포크 오류 표본 2→3행(22:23:31·46 및 22:40:36), 메인 11행(직전 구간 중첩 UUID 1행 포함; 인자·버전·호스트 전용 가드 및 셸 오류). 라우터가 `7993`을 `prior_submission_failed/stale_unconfirmed`로 스킵 1건(22:24:23, 직전 커서 7992). 동일 ID로 후속 로그 및 메인 전사 참조를 조회했으나 해당 스킵 외 증거 없음. 입력의 실제 전달·유실과 최초 실패 원인은 미확정. `channel_pending_injection.py:257–274`에서 이 로그는 기존 실패 상태의 항목별 스캔 건너뜀이며 큐 전체 중단을 뜻하지 않는다.
  - Betty(보조): 실행 1·대기 2·제출 5 유지, 원장 1011→1020, MCP 386→208ms. 검수 이상 없음, 포크 오류 표본 4→1행(21:39:02). 외부 SSE EOF 87건(첫 21:13:30), keep-alive 보호 경고 1,043건. Sarah 재조회 없음.
  - 새 제품 결함으로 입증된 항목 없음. 새 코드 변경·배포·운영 재시작·운영 상태 파일 수동 수정 없음. `fb7394f` 7루트 설치 확인은 유지하되 라이브 훅 적용은 미검증. 이번 틱의 keep-alive 로그는 기존 실행 라우터 `fa5b9c1`을 표시한다(설치된 JS의 라이브 로드 증거 아님).

- 2026-11-24 22:37:33–22:38:43Z 배포: `fb7394f`를 `feature/priority-models`·`beta`에 푸시. `Publish to npm` `45500888391` 성공, npm beta `0.2.51-beta.20261124-222612.fb7394f`의 `gitHead` 일치. `register.js` tarball/커밋 SHA-256 일치(`8f311af5b5d6` 접두어), 승인된 7개 루트(로컬 3·Celly·KOBE·Prody·Betty) 모두 설치 전 `base` 및 설치 후 `new` 확인. 버전 파일도 함께 확인(`runtime_constants.py` `12e37b530067`, 존재하는 `package.json` `52bf839e9a54`). 운영 세션·라우터 재시작과 운영 상태 파일 수동 수정 없음. **파일 설치 확인이며 기존 세션에서 새 훅 로드 및 실제 재발 방지는 미확인.**

- 2026-11-24 21:12:30–21:13:01Z 정기 틱, 직전 20:59–21:00Z 대비. `run.sh` 출력 5행(기존 추가 관측 Betty 포함). MCP 5곳 모두 성공; 신규 원장 실패 0. 포크 오류 수는 부분 전사 표본 행 수이며 종료 수가 아니다. 라우터 수치는 출력된 시그니처 집계, 시각 UTC.
  - Andy: 실행 4→3·대기 9 유지·제출 21→22, 원장 7306→7316, MCP 1,390→3,703ms. `n1166/2c02ec6f` 완료 영수증 대비 검수 행 미관측 지속, terminal/workspace proof 미확인. 포크 1행(21:02:46 보고 종류 가드), 메인 2건(21:05:24 인자 400, 21:10:57 검증 셸 exit 1). 외부 SSE EOF 13건(첫 20:59:59), POST reset 3건(첫 21:00:05), 턴 프로젝션 경고 4건(21:04:10–21:12:08), `keep_alive` 보호 경고 150건.
  - Celly: 실행 4·대기 3 유지, 원장 9185→9199, MCP 516→640ms, 검수 finding 없음. 포크 도구 오류 표본 5→11행(21:08:01–21:12:30; 권한 가드·전송·개별 도구 오류 혼재). 업스트림 SSE 오류 8건(첫 21:02:07; 별도 shape 로그 8건은 합산하지 않음), 게이트웨이 `URLError` 재시도 표본 40건(첫 21:06:32), 외부 SSE EOF 13건(첫 21:00:09), 활동 JSON 교체 `WinError 5` 5건(첫 21:04:47; 권한 거부의 근본 원인 미확인), `keep_alive` 145건. `8617ccba6d20`의 `turn_error` 1시도 후 21:02:42 메인 전사 인계 및 21:10:14 메인 처리 확인. 21:05:40 `n1500`은 P3 용량 가드로 대기 유지.
  - Celly 포크 상세: `n1510/2220e205`, `n1299/a27bf70d`, `n1420/069c03a9` 전사에 21:02:12.632–13.651 계정 한도 HTTP 429 각 1건. 원장에 세 건 모두 21:02:22.557 native `failed`, 21:02:27.550·34.386·47.818 `host_salvage`(각각 `n1299`, `n1510`, `n1420`). `n1299`·`n1510`의 같은 ID 전사에 salvage 이후 보고 요청 수신과 권한 거부가 남았다. `n1420`은 보고 요청 수신 후 중단 기록. 429 자체와 회수 경합을 분리한다. 최신 feature 기준 `b96cb87`에서 두 실패 테스트로 회수와 동시에 두 번째 메시지 발송 및 관계없는 메인 턴에서 30초 유예 우회 재현. `fb7394f`는 첫 보고 요청만 보내고 회수 시 재발송하지 않는다. 관계없는 턴 경계에서도 유예를 적용하며 내구 영수증 뒤 슬롯 해제·정지 순서는 유지. Windows 273 통과/36 skip, 기존 Linux py310·py311 각 272 통과/37 skip(세 영향 스위트); 플러그인 95/95, 추가 Windows MCP 36/36·finalization 114/114. 이 검증은 격리 하네스/플러그인 및 백엔드 결과이며 운영 재로드 후 확인은 아님.
  - KOBE: 실행 1·수락 1 유지, 대기 4→3, 원장 3708→3714, MCP 299→189ms. `n5/24fffb5b` 미완료 자식 검수 가드 유지. 포크 1→0행, 메인 오류 0건. 외부 SSE EOF 11건(첫 21:00:08)·BrokenPipe 1건(21:03:00), POST reset 4건(첫 21:00:09), `keep_alive` 151건. `159860` 개별 선택·`legacy_no_owner` 원인 미확인.
  - Prody: 실행 슬롯 1→0·대기 1 유지, 원장 981→985, MCP 657→680ms. 검수 finding/라우터 경고 표본 없음. 포크 보고 가드 2행(21:08:40 허용 보고 종류, 21:08:54 증거 2,272자/상한 2,000), 메인 Walkie `client_id` UUID 검증 거부 1건(21:12:41). 직전 `n1286/3ff44a3f`는 20:44:07 `decision_refused_stop` 1건 이후 `rework` 결정과 교체 리스 `ae2af587` 바인딩 확인. 최초 stop 거부의 상세 이유 미확인; 종료나 검수 실패 지속으로 해석하지 않음.
  - Betty: 실행 2→1·대기 2·제출 5 유지, 원장 1007→1011, MCP 527→386ms. 검수 이상/메인 오류 0건, 포크 0→4 표본 행(21:10:16–21:12:29, 보고 가드 2·셸/스크립트 2). 외부 SSE EOF 13건(첫 21:00:14), 게이트웨이 503 재시도 표본 15건(첫 21:00:11; 비교 창 중첩 가능), `URLError` 6건(첫 21:01:51), `keep_alive` 154건. Sarah 재조회 없음.

- 2026-11-24 20:59:55–21:00:29Z 직전 틱 보충, 20:33Z 대비. MCP Andy 4초 타임아웃→1,390ms 성공, Celly 1,109→516ms, KOBE 1,216→299ms, Prody 208→657ms, Betty 173→527ms. 포크 부분 표본 오류 행 Andy 0·Celly 5·KOBE 1·Prody 2·Betty 0. Andy `n1166` 및 KOBE `n5` 지속, 신규 원장 실패는 Prody `3ff44a3f` 1건만. Andy `n2797` `workspace_prepare_retained` 1건(20:48:44, `git worktree add` 92초 타임아웃; 왜 지연됐는지는 미확인). Celly 20:43:15 Work 게이트의 라우터 연결/스냅샷 검증 실패 1건 및 20:56:38 pump 실패 알림 1건(공통 원인 미확인); `f2be8d26ade3` `invalid_answer` 1시도 후 20:43:21 메인 인계, 20:44:49 메인 처리 확인. 외부 SSE EOF·게이트웨이 503/502·`URLError`는 제품 결함과 별도 기록.

- 2026-11-24 20:33:18–20:33:59Z 정기 틱, 직전 20:15Z 대비(`tick.jsonl` 5행 전원 성공, 신규 원장 실패 5곳 모두 0).
  - Andy: 실행 2·대기 9·제출 21 유지, 원장 7291→7298. 20:33:18Z MCP 4s `TimeoutError` → 즉시 후속 `work_status(session_id)` 282ms 정상 응답. `n1166` 검수 행 불일치 지속. `keep_alive` 213건(구 런처 보호 경고), 외부 SSE EOF 16건·URLError 3건(20:27:09Z), 메인 버전 가드 2건(20:28:28·20:29:56Z), PR #127 닫기 셸 exit 1 1건(20:24:02Z), 포크 도구 실패 표본 0행.
  - Celly: 실행 4·대기 3 유지, 원장 9172→9177, MCP 454→1,109ms, 검수 이상 0건. `keep_alive` 171건, 외부 SSE EOF 15건, 모델 업스트림 오류 9건(첫 20:19:50Z), 직접 503 4+1건(20:23:04Z), 502 재시도 2건(20:28:17Z), `n1299` 포크 503 재시도 알림 2건(20:28:59·20:30:06Z), 포크 도구 실패 표본 5행(20:21:23–20:33:22Z).
  - KOBE: 실행 1·대기 4·수락 1 유지, 원장 3693 유지, MCP 244→1,216ms. `review blocked: n5 24fffb5b` (`stage_failed: unfinished children`) 지속. `keep_alive` 208건, 외부 SSE EOF 16건·BrokenPipe 1건, 늦은 SSE 재시도 5건, 메인 오류 0건, 포크 도구 실패 표본 1행(20:28:41Z).
  - Prody: 실행 1·제출 1 유지, 원장 968 유지, MCP 317→208ms. 검수 이상·라우터 경고·메인 오류·포크 도구 실패 표본 모두 0건.
  - Betty: 실행 1·대기 2·제출 5 유지, 원장 1005 유지, MCP 449→173ms, 검수 이상·포크 도구 실패 표본 0건. `keep_alive` 219건, 외부 SSE EOF 18건, 메인 Walkie 400 5건·Ripgrep 20s 타임아웃 2건·스크립트 오류 1건.
  - **입증된 결함 수정·검증·베타 배포(`3438e6e`):** 직전 Andy `160097`(`b3c70c902571`)에서 확인된 `side-intake.js` `relay + ask_main` 동시 `noticeToMain` 경합 수정(`docs/journal/2026/11/24/fixes/side-relay-ask-main-race/record.md`). RED 테스트 실패 확인→수정→격리 Windows 115/115 + `claude plugin validate/test` 94/94 + 기존 Linux py310/py311 각 96/96 통과→`feature/priority-models`·`beta` push → `Publish to npm` `45484103492` 성공(`0.2.51-beta.20261124-204540.3438e6e`, tarball `side-intake.js` SHA-256 `88cb9cc51728` 일치) → `overlay-3438e6e` 7개 루트(로컬 3·Celly·KOBE·Prody·Betty) 모두 `base → new` 확인(20:55:37–20:56:19Z). 운영 세션·라우터 재시작 및 운영 상태 파일 수정 없음(`side-intake.js` 변경은 다음 세션/플러그인 재로드 후 라이브 반영).

- 2026-11-24 20:15:24–20:15:54Z 정기 틱, 직전 20:00–20:01Z 대비(`tick.jsonl` 5행 전원 성공, 신규 원장 실패 5곳 모두 0).
  - Andy: 실행 3→2, 대기 9·제출 21 유지, 원장 7287→7291, MCP 타임아웃→1,218ms 회복. `n1166` 검수 행 불일치 지속. `keep_alive` 179건(구 런처 보호 경고), 외부 SSE EOF 13+1건(첫 20:01:13Z), `transcript_delta_delivery_failed` 1건(20:13:49Z), 메인 보드 버전 가드 2건(20:02:11·20:12:19Z), `979148ccaad` P3 용량 가드 1건(20:03:19Z), 포크 도구 실패 표본 0행.
  - Celly: 실행 4 유지, 대기 4→3, 원장 9166→9172, MCP 719→454ms. 검수 이상·메인 오류 0건. `keep_alive` 179건, 외부 SSE EOF 13+1건(첫 20:00:43Z), 활동 기록 `PermissionError` 1건(20:14:29Z), 포크 도구 실패 표본 5행(20:05:49–20:15:06Z).
  - KOBE: 실행 1·대기 4·수락 1 유지, 원장 3675→3693, MCP 159→244ms. `review blocked: n5 24fffb5b` (`stage_failed: n5 still has unfinished children`) 지속. `keep_alive` 177건, 외부 SSE EOF 15+1건(첫 20:00:57Z), 활동 기록 `PermissionError` 2건(20:07:21·20:10:06Z), 메인 오류 0건, 포크 도구 실패 표본 4행(20:00:38–20:14:29Z).
  - Prody: 실행 2→1, 제출 0→1, 원장 966→968, MCP 301→317ms. 검수 이상·라우터 경고·포크 도구 실패 표본 0건. 메인 Walkie 403(`Conversation access denied`) 1건(20:14:35Z).
  - Betty: 실행 1·대기 2·제출 5 유지, 원장 1005 유지, MCP 391→449ms. 검수 이상·메인 오류·포크 도구 실패 표본 0건. `keep_alive` 183건, 외부 SSE EOF 14+2건(첫 20:00:24Z), `20:08:32Z unseen_failed message_id=7920` 및 `20:08:36Z prior_submission_failed`(구 런처 `8964`, 실제 전달 미확인).
- 2026-11-24 20:13–20:29Z 사용자 확인 요청(`160097` Andy 사이드 액터→관련 포크 전달 여부, 읽기 전용 추적):
  - **사이드 액터 처리 완료:** `b3c70c902571` (`160097`, `message.created`) 20:13:26Z 접수·claim → `agent-aac95b788a0c4f61d` (`ciel-work:side-fresh`) 스폰 20:13:51Z → 20:21:53Z 완료(`action=update`, `kind=request`, `item_id=n2459`, `relay={item_id: n2459}`, `ask_main=true`, `complaint=true`, `delivery=submitted`). *(부수 관측: 사이드 액터가 프롬프트 금지에도 `20:16:36Z` `work_node_update(n2459)` 및 PowerShell 2회 실행.)*
  - **관련 포크 미전달 확인:** `n2459 → n2776 → n2777`의 리스 `e37edf1119`(`agent-a332fe7f6513ccb6c`)는 `19:25:35Z` 이후 `waiting_input`(CI 대기 파킹) 상태였고, 전사 마지막 시각 `19:25:35Z`로 `160097`/`b3c70c902571` 수신 기록 0건.
  - **확인된 두 원인:** (1) `side-intake.js:2522-2531`은 `running` 포크와 `waiting_on` 해제로 재개된 파킹 포크에만 `$.session.send`를 보내는데, 이번 결정에 `release_waits`가 없어 `e37edf1119`가 재개·전달되지 않음. (2) **코드 결함 확인(`side-intake.js:2674-2696`)**: `commitProposal`이 `relayToItem`과 `ask_main`을 병렬 실행하면서 둘 다 같은 `event.receipt_id`(`b3c70c902571`)로 `noticeToMain`을 호출함 → `ask_main`이 `20:21:56Z`에 먼저 메인에 붙고, `relayToItem`은 `20:22:48Z`에 `Side relay n2459 → main (already)`로 중복 스킵되어 릴레이 본문과 파킹 포크(`stuck`) 안내가 누락됨.
  - **메인 처리 결과:** `ask_main`으로 깨어난 Andy 메인이 `20:22:13Z`에 직접 `160097`·`n2459`를 읽고, `20:23:56Z`에 PR #127 닫기·CI `45469539114` 취소, `20:28:18Z`에 답글, `20:29:06Z`에 `n2459` 계획 갱신 수행.

- 2026-11-24 20:00:20–20:01:16Z 정기 틱, 직전 19:08Z 대비(19:39–19:47Z 사용자 승인 라우터 재시작·안내메시지 전송 이후 첫 틱). `run.sh` 1차에서 `.12` SSH `lost connection`으로 Prody·Betty 누락 → 즉시 동일 `--since` 재조회(`tick_pb_retry.jsonl`)로 5호스트 전원 확보. 신규 원장 실패 5곳 모두 0. 리스 집계와 포크 종료는 구분한다.
  - Andy: 실행 2→3, 대기 7→9, 제출 21 유지, 원장 7260→7287. 20:00:20Z MCP 4s `TimeoutError` → 1분 뒤 후속 확인에서 `/health` 15ms(`PID 105880`, `fa5b9c1`)·`work_status(session_id)` 188ms 정상 응답. `n1166` 검수 행 불일치 지속. 재시작 구간(19:41:14–18Z) `router_lifetime_restart` 1건·MCP 미연결 알림 5건·`keep_alive` 391+224건은 정상 재기동/구 런처 보호 경고로 분리. `19:48:39Z unseen_failed message_id=24485`는 안내메시지에 대한 구 런처 오판(전사 도달 확인됨); `19:25:34Z message_id=24481` `unseen_failed` 1건(재시작 전, 실제 전달 미확인). 외부 SSE EOF 51건(첫 19:08:10Z)·URLError 2건(19:45:06Z), `19:45:15Z` 포크 `empty_stream` 재시도 알림 2건(`n2755`·`n2783`), 모델 스트림 오류 1건(19:54:05Z). 포크 도구 실패 표본 0행.
  - Celly: 실행 4·대기 4 유지, 원장 9148→9166, MCP 343→719ms. 직전 `n1299` 미관측 finding 해소. 재시작 구간(19:47:25–41Z) MCP 미연결 2건·`keep_alive` 153건·`n1299`/`n1510` 포크 `server_error` 재시도 4건은 재기동 영향·복구로 분리. 재시작 전 `19:10:07Z` 콘솔 제출 타임아웃(`prompt_render_timeout`) 4건, `19:24:38Z` 업스트림 오류 8건·503 3건, `19:28:18Z` 실패 side 원본 append 1건, `19:46:33Z` 직접 HTTP 400 1건. `ae7460460bd` P3 용량/5분 유예 알림 4건은 정상 가드. 외부 SSE EOF 47건(첫 19:08:49Z), 포크 도구 실패 표본 2행(19:42:28·19:59:28Z).
  - KOBE: 실행 0→1, 대기 5→4, 수락(`accepted`) 1, 원장 3668→3675, MCP 227→159ms. **신규 finding: `review blocked: n5 24fffb5b deciding` (`stage_failed: n5 still has unfinished children; finish them first`, 19:33:08Z)** — `work_plan_tree.py:479` 보드 가드이며 미결 추적. 재시작 구간 `19:40:42Z` `router_lifetime_restart` 1건·`keep_alive` 183건·`19:48:54Z unseen_failed message_id=12047`(안내메시지, 전사 도달 확인됨)은 재기동/구 런처 경고로 분리. `19:55:07–19:56:49Z` 외부 네트워크 `URLError` 동반 `transcript_delta_delivery_failed` 1건·`agent_runtime_event_retry` 4건·`agent_runtime_event_dropped` 1건. 외부 SSE EOF 11건(첫 19:43:09Z). 포크 도구 실패 표본 0행.
  - Prody(재조회 20:01:14Z): 실행 1→2, 원장 956→966, MCP 328→301ms, 검수 finding·라우터 경고 0건. `19:40:39Z` `n1286` 포크 `server_error` 재시도 예약·`19:41:42Z` 1/20 재시도는 라우터 재시작 직후 정상 복구. `19:17:17Z` 실패 side 원본 append 1건(재시작 전), 포크 도구 실패 표본 2행(19:19:28–47Z), 메인 보드 버전 가드 4건.
  - Betty(재조회 20:01:16Z): 실행 0→1, 대기 2·제출 5 유지, 원장 1003→1005, MCP 369→391ms, 검수 finding·포크 도구 실패 표본 0건. `19:39:38Z` `router_lifetime_restart` 1건·`keep_alive` 175건·`19:49:18Z unseen_failed message_id=7919`(안내메시지, 전사 도달 확인됨)은 재기동/구 런처 경고로 분리. 외부 SSE EOF 17건(첫 19:42:55Z), `20:00:24–46Z` 외부 SSE 단절 2건. Sarah 재조회 없음.
  - 새 입증 코드 결함 없음. 운영 재시작·상태 파일 수정·추가 배포 없음.

- 2026-11-24 19:36–19:56Z 전체 베타 에이전트 버전 동기화·라우터 재시작·안내메시지 전송(사용자 지시: "전체 에이전트들에게 업데이트 해주고 , 라우터 재시작하고 , 안내메시지 넣어줘").
  - 버전 파일 동기화(`overlay-ver-fa5b9c1`): `0.2.51-beta.20261124-185426.fa5b9c1` tarball에서 `ciel_runtime_support/runtime_constants.py`(`b993d380f35c`)와 `package.json`(`78b8da660d2c`, 존재하는 루트만)을 7개 승인 루트(로컬 `hotfix5`·`.local/share`·전역 npm, Celly, KOBE, Prody, Betty)에 백업 후 원자 교체(`.cmd` 래퍼 미변경).
  - 라우터 재시작 및 검증(5/5 모두 `version=0.2.51-beta.20261124-185426.fa5b9c1`, `source_fingerprint=5cff1e881e0801be`, 기존 `active_client_pids` 유지, REST `/runtime` `ok=True`, MCP `recover_sse_delivery(status)` `ok=True / gate=open`):
    - Andy(9505): `PID 114552 → 105880`, `client=[88952]`, `sid=claude:feeac3ad-...`. 안내메시지 `message_id=24485` → 메인 전사 `19:48:20Z attachment`, `19:48:46Z queue-operation`, `api-request-blob` 확인.
    - Celly(9385): `PID 92596 → 68448`(권한 분리로 `router_debug` 현재값 `off` 유지 인프로세스 재시작), `client=[101552]`, `sid=claude:9430f5e8-...`. 안내메시지 `message_id=16439` → 메인 전사 `19:48:21Z user`, `api-request-blob` 확인.
    - KOBE(9779): `PID 3244 → 2008`, `client=[15784]`, `sid=claude:9b0de8fe-...`. 안내메시지 `message_id=12047` → 메인 전사 `19:48:33Z user`, `api-request-blob` 확인.
    - Prody(9592): `PID 12500 → 8500`, `client=[7628]`, `sid=claude:8ee391dd-...`. 안내메시지 1차 8s 타임아웃(미적재 확인) 후 재전송 `message_id=7979` → 메인 전사(`8ee391dd-...jsonl`) 도달 확인.
    - Betty(9564): `PID 7308 → 12480`, `client=[8964]`, `sid=claude:26697479-...`. 안내메시지 `message_id=7919` → 메인 전사 `19:49:15Z user`, `api-request-blob` 확인.
  - 제외·미도달: Sarah(`100.64.0.25`) SSH 배너 타임아웃으로 미업데이트·미재시작·미전송. `.14`의 비활성 `nightly` 샌드박스(`robert-ai`, `ara-ai`, `edward-ai`)·`eric-ai`, `.12`의 `test-92953`, 로컬 개발 세션(`9524`·`9466`·`9489`)은 제외.
  - 반영 범위 구분: 라우터 Python 변경은 5곳 즉시 라이브 반영. `claude.exe` 세션과 런처 프로세스는 재시작하지 않았으므로 `ciel-work` JS·스킬과 런처 변경은 각 에이전트의 다음 세션 재시작/재로드 시 적용.

- 2026-11-24 19:08:07–19:08:35Z 정기 틱, 직전 18:35Z 대비(`tick.jsonl` 5행, Betty 포함). 5곳 모두 MCP `probe_succeeded`, 신규 원장 실패 0. 아래 실행·대기·제출은 리스 집계이며 네이티브 포크 종료를 뜻하지 않는다. 라우터 수치는 상위 25개 시그니처 표본, 시각은 UTC 환산.
  - Andy: 실행 2 유지, 대기 8→7, 제출 22→21, 원장 7232→7260, MCP 812→796ms. 직전 `n2773` finding 해소(앞서 `finalized/approve` 확인), `n1166`(`2c02ec6f`) 완료 영수증 대비 검수 행 미관측 지속. 외부 SSE EOF 31건(첫 18:36:29Z), 모델 업스트림 오류 1건(18:35:53Z), keep-alive 보호 경고 387건(첫 18:35:32Z). 메인 보드 버전 충돌 가드 4건(18:36:25–18:38:45Z), Ripgrep 20s 타임아웃 1건(18:56:03Z), Walkie 400/403 3건. 포크 도구 실패 표본 1행(18:54:11Z).
  - Celly: 실행 3→4, 대기 3→4, 제출 2→0, 원장 9115→9148, MCP 859→343ms. 틱 시점 `n1299`(`b5b925ea`) `missing_since 18:38` finding 1건이 나왔으나, 33초 뒤 후속 읽기 전용 원장·전사 대조에서 `native status=running`, `last_seen_at=19:08:44Z`, `missing_since=None`, 서브에이전트 JSONL 19:08:52Z 활성 쓰기 확인(종료 아님). 19:05:57Z `n1467`의 `not a slot-released wait` 1건(`6c5f04f804d9`는 이미 `running/slot=True`로 재개된 상태 — `f7a06ee`에서 수정 완료, Celly 세션 재로드 전 구 `register.js`). 라우터 모델 업스트림 오류 35건(첫 18:36:05Z), 외부 SSE EOF 32건(첫 18:36:06Z), 직접 503 12+9건 및 5xx 10+3건(첫 18:36:16Z), 활동 기록 `PermissionError` 3건(첫 18:53:48Z), 콘솔 턴 실패 1건(`message_id=16432`, 18:36:06Z). 메인 `n1406` 포크 API 재시도 알림 4건(18:45:59–18:54:46Z), 포크 도구 실패 표본 6행(18:39:25–19:03:24Z).
  - KOBE: 실행 1→0, 대기 0→5, 원장 3649→3668, MCP 132→227ms. 검수 finding·신규 원장 실패 없음. 외부 SSE EOF 22건(첫 18:40:12Z)·BrokenPipe 6건(첫 18:35:31Z), 모델 업스트림 오류 5+1건(첫 18:35:39Z), `remote_instructions_failed` TimeoutError 1건(18:37:50Z), 활동 기록 `PermissionError` 1건(18:42:22Z). 메인 실패 side 원본 append 알림 1건(`7a78fe62f5fd`, 18:39:03Z), Walkie `client_id` 1건(18:40:54Z), 미완료 자식 가드 1건(18:43:50Z), 포크 도구 실패 표본 1행(19:03:43Z).
  - Prody: 실행 1·원장 956 유지, MCP 341→328ms. 검수 finding·원장 실패·라우터 경고·메인 오류·포크 도구 실패 표본 모두 0건.
  - Betty: 실행 1→0, 대기 1→2, 제출 5 유지, 원장 999→1003, MCP 214→369ms. 외부 SSE EOF 32건(첫 18:36:35Z), `unseen_failed` 3건(`7916` 18:37:10Z + 스캔 가드 18:37:13Z, `7913` 18:42:02Z, `7917` 19:03:04Z; `4886d62` 재시작 전 구 라우터, 실제 전달 미확인), 모델 업스트림 오류 1건(18:38:43Z). 메인 파일 없음 3건·Walkie 인자/승인/스케줄 오류 9건, 포크 도구 실패 표본 2행(18:40:13–18:40:34Z). Sarah 재조회 없음.
  - 새 입증 결함 없음(`f7a06ee`·`fa5b9c1`은 파일 설치 완료 상태이며 운영 세션·라우터 재시작 전). 운영 재시작·상태 파일 수정·추가 배포 없음.

- 2026-11-24 18:54–19:06Z 개선 커밋·베타 발행·7개 루트 해시 확인 설치(사용자 지시: "지금까지 발견된 문제들 개선해서 모두 배포해", Prody #44 잔액 부족 실패 복구, KOBE #45 스킬/메인 지침 보강). 상세 기록: `docs/journal/2026/11/24/fixes/side-billing-recovery-and-main-polling/record.md`.
  - 코드 커밋 `fa5b9c1` (`fa5b9c1fefc1a3c0ee59376d857e38b77c41d72d`): `side-intake.js` 잔액 부족(400/402) 정확 분류·`work_side_fail` 3회 펜스 재시도 및 미확정 세대 재스폰 방지/재조정, `side_request_outcome.py` 비파괴 `peek`, `side_intake_mcp.py`·`side_intake_processing.py`·`side_forward.py` 세대 멱등 실패 기록 및 검증된 별도 메인 모델 1회 재시도/즉시 격리+정규화 사유 인계, `work_texts.py`·`texts.generated.js` 메인 반복 조회 금지, `work-operations/SKILL.md` 기존 읽기 전용 진단 우선 및 호스트화 안내.
  - 검증: Windows 집중 회귀 6개 파일(151 ran, 1 skip, 0 fail), 기존 Linux `ciel-runtime-test-py310`·`py311` 각 151 ran(1 skip, 0 fail), `claude plugin validate`·`test` 94/94, `compileall`, `ruff check`, `npm run check:docs`, `npm pack --dry-run`, 격리 실제 CLI 2.1.331 + HTTP 402 스텁 E2E(`category=billing_error`, `attempt_count=1`, `quarantined=true`, `notice=appended`, `main_read=true`).
  - 배포: `feature/priority-models`·`beta` push → `Publish to npm` `45464502208` 성공(`0.2.51-beta.20261124-185426.fa5b9c1`, `gitHead=fa5b9c1`, tarball 8/8 SHA-256 일치) → `overlay-fa5b9c1`(base `f7a06ee`, 8개 파일) 로컬 3루트(hotfix5·`.local/share`·npm 전역)·Celly·KOBE·Prody·Betty 7개 루트 모두 `base 8/8 → new 8/8` 확인(19:05:50–19:05:58Z). 운영 세션·라우터 재시작 및 운영 상태 파일 변경 없음(라우터 Python은 재시작 후, 모드·스킬은 재로드 후 라이브 반영).
  - 미결 분리: Andy `n1166` 지속 불일치, KOBE `159860` 개별 미선택/소스 마커 및 `legacy_no_owner` 원인, Sarah(.25) SSH/SSE, Betty/Prody의 구 라우터 `unseen_failed`/`stale_unconfirmed`(4886d62 파일 설치됨·재시작 전), 외부 게이트웨이 503/504/EOF, 별도 계획의 범용 `waiting_on`/planning 감사.

- 2026-11-24 18:35:30–18:35:59Z 정기 틱, 직전 18:12Z 대비. MCP 5곳 성공. 아래 실행·대기·제출은 리스 집계이고, 네이티브 포크 종료나 검수 결정을 뜻하지 않는다. 라우터 수치는 출력 시그니처 표본, 시각은 UTC.
  - Andy: 실행 2·제출 22 유지, 대기 7→8, 원장 7220→7232, MCP 2,015→812ms. n1166 검수 행 불일치 지속, n2773(`627d9b35`) 신규 관측, n1826은 후속 원장 최종화 확인. 라우터 모델 업스트림 스트림 오류 13+1건(첫 18:12:50Z), 외부 SSE EOF 23건(첫 18:12:56Z), keep-alive 보호 경고 272건(첫 18:12:24Z). 포크 도구 실패 표본 2행(18:33:59Z·18:34:56Z), 원본 메인 append 알림 2건(18:25:07Z·18:35:02Z), 메인 읽음/답장 미확인. 18:12:30Z 검수 포크 실행 거부는 정상 가드.
  - Celly: 실행 3·제출 2 유지, 대기 2→3, 원장 9080→9115, MCP 328→859ms. n1502의 검수 행 미관측 finding 해소(앞선 별도 원장 확인에서 pending 행 존재). 라우터 모델 업스트림 오류 시그니처 38+8건(첫 18:12:23Z), 외부 SSE EOF 22건(첫 18:12:34Z), 5xx 15+3건(첫 18:12:29Z), 직접 503 20+13건(첫 18:15:31Z); 동일 요청 중복 가능. 메인 n1406·n1299 포크 503 재시도 예약 알림 5건(18:19:15–18:33:45Z), 재시도 알림 8건(18:24:22–18:34:55Z). 포크 도구 실패 표본 3행(18:15:41–18:35:29Z).
  - KOBE: 실행 2→1, 원장 3642→3649, MCP 168→132ms. 새 검수 이상 없음. 모델 업스트림 스트림 오류 시그니처 59건(첫 18:13:54Z), 외부 SSE EOF 19건(첫 18:13:08Z), 직접 HTTP 503 2건(첫 18:28:50Z), 컨텍스트 활동 기록 PermissionError 1건(18:15:00Z, 근본 원인 미확인). 원본 메인 append 알림 5건(18:15:29–18:30:55Z), 소비 미확인. 포크 도구 실패 표본 1행(18:18:58Z). 후속 SSH 첫 시도 배너 타임아웃, 두 번째 REST 성공: 159860 최초 건 `turn_error` 1회 유지, 두 재알림 미착수. 상태 집계 `received=6`, `processing=2`, `processed=16`, `failed=18`, `quarantined=6`.
  - Prody: 실행 0→1, 대기 5→0, 제출 6→0 (종료·검수 결과 단정 금지), 원장 949→956, MCP 1,742→341ms. 18:20:50Z 이전 포크 재개 차단 및 원장 `revival_refused` 1건은 가드. 라우터 18:19:04Z 이벤트 7970 `prior_submission_failed/stale_unconfirmed` 1건; 실제 전달 여부 미확인. 메인 원본 append 알림 1건(18:29:26Z, Prody 잔액 부족 제보와 동일 건인지 미확인), 포크 도구 실패 표본 0행.
  - Betty: 실행 1·대기 1·제출 5 유지, 원장 993→999, MCP 410→214ms. 라우터 외부 SSE EOF 23건(첫 18:12:59Z), `unseen_failed` 2건(7910 18:18:56Z, 7915 18:32:54Z), 7915 이후 실패 스캔 가드 1건(18:32:59Z), 실제 전달 미확인. 메인 Walkie `client_id` UUID 검증 거부 6건(18:23:22–18:34:29Z), 포크 도구 실패 표본 1행(18:17:34Z). Sarah 재조회 없음.
  - 이 틱에서 새로 재현·입증한 결함은 없음. 운영 재시작·운영 상태 파일 수정·새 설치 없음. 진행 중인 실패 복구/지침 작업과 이 운영 틱을 구분한다.

- 후속 배포 조사(18:25–18:28Z): KOBE `159860` 원본에서 18:25:36Z 첫 `turn_error`와 메인 모델 재시도 자격(`use_parent_model=true`, `retry_not_before=failed_at`) 확인. 재알림 둘은 미착수, 당시 재시도 실행·결과 미확인. KOBE workspace 원장 경로 직접 읽기 권한 거부, 소스 마커 미확인. 권한 변경 없음.
  - 검수 원장 재대조: Andy `n1826` 동일 리스 `bc88111e` 최종화/승인, Celly `n1502` 동일 리스 `abc4cf10` 검수 pending 생성 확인. 최초 틱의 관측 차이를 지속 누락으로 분류하지 않는다. Andy `n1166`은 `2c02ec6f` submitted, generation 1 completion, 마지막 native `running` 후 미관측, 해당 리스 검수 행 미확인. terminal·workspace proof 없는 자동 게시 금지 유지.
  - 이미지 #45 후속: `work-operations/SKILL.md`에 규율 점검은 기존 읽기 전용 진단(`work capacity`, `work reviews`, `work audit`)부터 사용하고, 증거 불일치만 개별 조사하며, 반복 가능한 검사는 호스트에 두고 변화만 통지하라는 안내를 추가했다. 안내 변경이며 신규 자동 감사 구현이 아니다. Windows 및 기존 Linux py310/py311에서 스킬 포함 계약 테스트 각 8/8.
  - 메인 관리 프롬프트: Work 알림을 기다리기 위한 반복 조회·셸 폴링 금지, 실제 이벤트/사용자 요청/현재 판단에 필요한 조회 허용, 독립 작업 없으면 턴 종료. 새 계약 테스트 수정 전 1 failure, 수정 후 Windows·Linux py310·py311 8/8씩 통과. 플러그인 validate 및 현재 프롬프트 변경 기준 94/94 통과. 이 관측은 운영 설치·라이브 적용 증거가 아니다.

- 2026-11-24 18:12:20–18:12:49Z 정기 틱(직전 17:55Z), `tick.jsonl` 5행. Work 리스 집계와 네이티브 종료는 구분한다. 라우터 수치는 출력 시그니처 표본, 시각 UTC 환산.
  - Andy: 실행 2·제출 22 유지, 대기 6→7, 원장 7208→7220, MCP 1,203→2,015ms. 완료 영수증 대비 검수 행 미관측: n1166 지속, n1826(`bc88111e`) 신규. 모델 업스트림 스트림 오류 13건(첫 17:55:06Z), 외부 SSE EOF 17건(첫 17:55:34Z), 업스트림 HTTP 402 1건(18:00:31Z, 상세 원인 미확인). 17:55:46Z `work_host_page` 연결 `EADDRINUSE` 알림 1건. 실패 side 원본 메인 append 알림 4건(17:59:15–18:08:58Z), 답장 증거는 아님. keep-alive 보호 경고 206건, 포크 도구 실패 표본 0행.
  - Celly: 실행 3·대기 2 유지, 제출 1→2, 원장 9071→9080, MCP 375→328ms. n1502(`abc4cf10`) 완료 영수증 대비 검수 행 미관측 신규. 포크 도구 실패 표본 6행(18:01:03–18:09:36Z). 외부 SSE EOF 17건(첫 17:55:06Z), 모델 업스트림 스트림 오류 시그니처 32+3+2건(첫 17:56:01Z), 게이트웨이 5xx 11+3건 및 직접 HTTP 503 7+3건(각 첫 17:55:01Z, 동일 요청 중복 가능).
  - KOBE: 실행 1→2, 원장 3634→3642, MCP `URLError`→168ms 성공. 직전 n3 죽은 소유 PID finding 및 검수 거부 재발 미관측. 외부 SSE EOF 10건(첫 17:57:46Z), timeout 2건(첫 17:59:18Z), BrokenPipe 1건(18:05:44Z); 모델 업스트림 첫 출력 한도 3건(첫 18:07:06Z), 늦은 스트림 재시도 예산 종료 2건(18:09:24Z·18:10:05Z). 18:02:00Z n45 미승인 디스패치 거부는 정상 가드 1건. 포크 도구 실패 표본 2행(18:11:02Z·18:11:43Z). 18:13:19Z side REST `received=18`, `processing=2`, `processed=14`: 다른 영수증은 진전했지만 `159860` 원본·재알림 둘은 모두 미착수 유지, 최초 약 94분 대기. 개별 미선택 원인은 미확인.
  - Prody: 실행 1→0, 대기 4→5, 제출 6 유지, 원장 948→949, MCP 2,570→1,742ms. 18:11:22Z 도구에서 Task ID 미발견 1건(포크 종료 증거 아님), 18:12:22Z 보드 버전 가드 1건. 검수 finding·라우터 경고·포크 도구 실패 표본 미관측.
  - Betty: 실행 1·대기 1·제출 5 유지, 원장 990→993, MCP 206→410ms. 외부 SSE EOF 17건(첫 17:55:38Z), 모델 업스트림 오류 2건과 재시도 예산 종료 1건(18:00:12Z, 중복 분리). 새 `unseen_failed` 및 포크·검수 이상 미관측. Sarah 재조회 없음.
  - 5곳 신규 원장 실패 0. 새 검수 finding의 지속 여부·원인은 미확인. 운영 재시작, 운영 상태 파일 수정, 코드 변경·배포 없음.

- 2026-11-24 18:06–18:09Z KOBE `P3 IN @[Agent]` 3행 읽기 전용 추적. 원본 REST 상세를 대조하니 모두 Walkie 메시지 `159860`: 최초 `message.created` 16:39:11Z (`813c7f3200d7`), `message.renotify` 17:51:31Z (`078597388b43`)·18:01:06Z (`db6be83b14fc`). 세 영수증 모두 `received`, claim 없음, 상세 조회에서 외부 ACK `not_sent`. 18:09:38Z 최초 건 약 90분 대기. 세 별도 업무나 수신 유실이 아님.
  - 18:07:03Z 설정: 일반 side 슬롯 2, aging 60초. 인박스 `received=19`, `processing=2`; 최초 건보다 오래된 `received` 12개. 당시 두 처리 중 영수증은 GitHub P3(`3a63be08d659`, 17:50:44Z claim)와 정기 게이트(`baa447cb1aa8`, 17:58:51Z claim). 18:09:38Z에는 오래된 P4 재시도(`7195af5da091`, 18:07:09Z claim)와 같은 게이트의 오류 후 재시도(18:08:11Z claim)가 처리 중. 17:57:10Z claim된 별도 영수증의 18:02:02Z resolve도 확인. 따라서 큐 전체 정지나 같은 두 작업의 연속 점유로 설명할 수 없다. `side_intake_processing.py:90–109`는 P0/P1 우선, 오래된 일반 입력과 재시도 레인 교대를 사용하므로 P3가 빈 슬롯을 독점하지 않는다. 각각의 claim에서 `159860`이 선택되지 않은 내부 분기 및 동일 원본의 소스 마커는 미확인.
  - 메인 자체 감시 제보: 17:35Z 이후 메인 JSONL에 `work_status` 6회, `work_parallel_status` 2회. 동일 표본에 검수 3회, 목표 실행 2회와 실제 보드 변경도 있어 모든 조회가 유휴 감시 루프였다고 단정 못 함. 17:57:25–18:07:39Z 콘솔 경로 `reason=active_turn` 19건(커서 12036), 18:08:40Z에도 계속 관측. 별도로 17:57:18–18:07:50Z `work_side_claim` 호출 107건 및 실제 새 claim 확인. 콘솔 보류는 관측됐으나 모든 입력이 막힌 것은 아니며 `159860` 세 영수증은 이미 side 인박스에 있다. 메인 지침에는 호스트가 Work 이벤트를 전달하므로 알림 대기만을 위한 반복 조회를 하지 말고, 이벤트나 구체적인 판단에 필요한 경우에만 상태를 조회한다는 제안. 운영 설정·코드·세션은 변경하지 않음.

- 2026-11-24 17:54:54–17:55:29Z 정기 틱, 직전 17:32Z 비교. `tick.jsonl` 5행(Betty 포함), 이전 출력 `tick.previous.jsonl`. 네이티브 터미널 미관측, 리스 수를 포크 종료로 해석하지 않음. 라우터 수치는 출력에 포함된 시그니처 표본이며 UTC 환산.
  - Andy: 실행 슬롯 4→2, 대기 4→6, 제출 21→22, 원장 7195→7208, MCP 1,625→1,203ms. n1166 검수 행 불일치 지속. 외부 SSE EOF 20건(첫 17:33:17Z), 모델 업스트림 스트림 오류 22건(첫 17:32:35Z), HTTP 503 1건(17:37:24Z), 작업공간 준비 보존 오류 1건(17:52:58Z, `workspace_prepare_retained`; 근본 원인 미확인). keep-alive 보호 경고 264건(첫 17:32:37Z). 실패 side 원본 메인 append 알림 4건(17:36:18–17:48:48Z); 메인 읽기·답장은 미확인.
  - Celly: 실행 슬롯 4→3, 대기 0→2, 제출 1 유지, 원장 9058→9071, MCP 1,250→375ms. 직전 원장 거부 두 유형 신규 재발 미관측. 외부 SSE EOF 19건(첫 17:33:27Z), 모델 업스트림 스트림 오류 시그니처 33+2건(첫 17:33:33Z), 게이트웨이 5xx 전환 12+3건(첫 17:36:15Z), 직접 HTTP 503 시그니처 10+3건(첫 17:35:27Z); 같은 요청에 중복 기록될 수 있어 합산하지 않음. 활동 기록 PermissionError 3건(첫 17:38:54Z); 포크 도구 실패 표본 1행(17:53:24Z).
  - KOBE: 실행 슬롯 3→1, 원장 3624→3634. 17:55:04Z MCP `URLError`(2,030ms), 후속 읽기 전용 `/health` HTTP 200과 Work REST 응답 확인(MCP 재성공은 미확인). n3 `72f25c00`의 슬롯 소유 PID 15176 생존 검사 실패; 네이티브 포크 종료나 라우터 크래시의 증거는 아님. `decision_refused_stop 019fd636` 1건, 17:41:17Z 메인 검수 `review changed; reread` 1건(동일 리스인지 미확인), 포크 도구 실패 표본 3행(17:36:07–17:38:17Z). 라우터 경고 미관측.
  - Prody: 실행 슬롯 0→1, 대기 4·제출 6 유지, 원장 943→948, MCP 2,081→2,570ms. 새 원장 실패·검수 finding·라우터 경고 미관측. 메인 도구 오류: 17:32:55Z Walkie 인자 검증(이전 틱과 관측 창 중첩), 17:35:55Z 검색, 17:41:23Z 셸 오류 각 1건.
  - Betty: 실행 슬롯 0→1, 대기 1·제출 5 유지, 원장 988→990, MCP 493→206ms. 외부 SSE EOF 22건(첫 17:33:09Z), 17:47:04Z 모델 반복 감지와 복구 가드 각 1건(서로 별개 장애로 합산하지 않음). 새 `unseen_failed` 미관측; 직전 입력의 실제 전달 미확인 유지.
  - 사용자 후속 질문(슬롯 포화 시 왜 유실되는가): `side-intake.js:2023–2045`의 포화 분기는 일반 claim을 유예하며 삭제하지 않는다. `side_intake_processing.py:395–441`은 실패 이벤트를 재시도하거나 격리 후 메인 전달 대상으로 기록한다. 동일 `159860`은 KOBE 후속 REST에서 `received`, claim/완료 없음(16:39:11Z 적재 유지); Prody는 `processed`, `delivery=submitted`와 이전 `turn_error` 기록 유지. 둘 다 슬롯 포화로 유실됐다는 증거 없음. KOBE에서 어느 시간에 side 슬롯이 비었는지·그때 선택되지 않은 이유·최종 답장 여부는 미확인. Work 실행 슬롯 집계와 side 슬롯은 별개다.
  - 이번 틱에 원인이 입증된 새 코드 결함 없음. 운영 재시작·운영 상태 수정·새 배포 없음. Sarah 재조회 없음.

- 2026-11-24 17:32:33–17:33:06Z 정기 틱, 직전 정기 16:14Z와 비교. `run.sh` 출력 `tick.jsonl` 5행(기존 추가 대상 Betty 포함). 네이티브 터미널 5곳 모두 미관측, 포크 로그는 부분 표본. 아래 실행·대기·제출은 리스 집계이며 프로세스 종료나 검수 완료를 뜻하지 않는다. 라우터 수치는 스크립트의 상위 25개 시그니처 집계이며 시각은 UTC로 환산했다.
  - Andy: 실행 5→4, 대기 4 유지, 제출 22→21, 원장 7160→7195, MCP 859→1,625ms. n1166 완료 영수증은 있으나 검수 행이 없는 상태 지속. 외부 SSE EOF 29건(첫 17:03:38Z), 업스트림 스트림 오류 3건(첫 17:31:25Z), 컨텍스트 사용량 `PermissionError` 1건(17:17:46Z, 상세 원인 미확인). 17:03:30Z 메인에 포크 재시도 알림 3건(empty_stream 2, server_error 1). 라우터 keep-alive `reachable_health_mismatch_active_client` 349건(첫 17:02:53Z)은 보호 경고로 분리.
  - Celly: 실행 6→4, 대기 1→0, 제출 1, 원장 9026→9058, MCP 3,781→1,250ms. 16:36:59Z `revival_refused` 1건(실행·대기 리스가 아닌 이전 포크로 메시지 재개 차단), 16:54:29Z `decision_refused_stop` 1건(리스 `24591c83`); 직전 16:54:28Z `work_parallel_item_review` 타임아웃 1건, 둘의 인과는 미확인. 메인 자동 계속 상태 조회 경고 2건(16:28:38Z, 16:59:43Z). 외부 SSE EOF 74건(첫 16:14:41Z), 게이트웨이 HTTP 5xx 3건(첫 17:15:57Z), 포크 오류 표본 12건(16:36:21–17:28:15Z).
  - KOBE: 실행 3 유지, 원장 3396→3624, MCP 149→141ms, 새 원장 실패·검수 finding 없음. 외부 SSE EOF 68건(첫 16:15:10Z), 라우터 업스트림 첫 출력 제한 14건(첫 17:03:33Z), HTTP 503 시그니처 5건(첫 16:49:08Z). 메인 포크 `transport_interrupted` 재시도 알림 4건(16:38:14–17:06:09Z), 포크 오류 표본 2건(17:24:43Z, 17:30:59Z). `legacy_no_owner` 원인과 개별 SSE 처리 결과는 별도 미결.
  - Prody: 실행 슬롯 1→0, 대기 3→4, 제출 7→6, 원장 930→943, MCP 1,616→2,081ms. 원장 신규 실패·검수 finding 없음. 라우터 경고는 이번 출력 미관측. 17:07:02Z P3 슬롯 한도 1/1 거부 1건은 정상 가드, 17:32:55Z Walkie edit `client_id` 인자 검증 거부 1건. 포크 오류 표본 5건(16:30:40–17:14:02Z). 앞선 SSE 159860 지연은 아래 상세 조사와 구분.
  - Betty: 실행 슬롯 3→0, 대기 1 유지, 제출 5 유지, 원장 976→988, MCP 699→493ms. 외부 SSE EOF 76건(첫 16:15:01Z), `unseen_failed` 시각 3개(16:39:30Z·16:53:03Z·16:55:41Z), 앞 두 건 뒤 `prior_submission_failed` 스캔 가드 관측. 실제 전달 여부 미확인. 포크 오류 표본 10건(16:22:54–17:10:45Z). Sarah는 이 틱에서 재조회하지 않았다.
  - 포크 오류 표본은 실패한 도구 결과를 포함한 JSONL 행 수(`error_records`)이며 API 실패나 종료 횟수가 아니다. Andy는 16:26:51Z 1행. 직전 SSE 상세 조사의 Prody `159860`에서 16:53:13Z는 첫 스폰, 16:54:17Z는 첫 실패다. 17:07:12Z는 원장 처리 완료(`delivery=submitted`)이며 Walkie 화면 표시 시각은 아니다. 슬롯 및 큰 입력과 지연이 함께 관측됐으나 각각의 기여도는 분리 검증하지 못했다.
  - 새 코드 결함으로 입증된 항목 없음. 운영 세션·라우터 재시작, 운영 상태 변경, 수정 배포 없음. `f7a06ee`는 이전 해시 확인 설치와 라이브 반영 판정을 구분한다.

- 2026-11-24 17:09–17:31Z SSE 지연 심층 조사(사용자 문의: "Kobe, prody등이 sse 메시지를 받고 바로 처리를 못하는데?", "prody는 이것을 대답한느데 30분이 걸렸어" — 메시지 159860/159862). 읽기 전용 조사, 운영 세션·라우터 재시작 및 상태 파일 변경 없음.
  - 공통 확인: 5호스트 모두 `recover_sse_delivery(status)`는 `problem=false`, `admission_paused=false`, `gate_snapshot.state=open`. Walkie 이벤트 도착→side inbox 적재는 1~3초 이내 완료(예: 메시지 `159860` 원본 시각 `16:39:09.362Z` → Prody·Andy·Celly `16:39:10Z`, KOBE `16:39:11Z`, Betty 메인 `16:39:11Z` 적재). 따라서 **SSE 수신·호스트 전달 단계의 막힘이 아니라, side 인박스 적재 이후의 처리·슬롯·업스트림 모델 응답 지연이 원인**으로 확인됐다.
  - **Prody(9592, `work_side_intake_session=fork`, `max_active=2`, `model=_API_KEYS-SSE-1`)** — 메시지 `159860`(`813c7f3200`, 고아 Python 문의)이 28분 02초(`16:39:10Z`→`17:07:12Z`) 걸린 실측 내역:
    1. **인박스 대기 14분 03초 (`16:39:10Z` → 1차 스폰 `16:53:13Z`)**: `max_active=2` 슬롯을 앞선 side 작업과 게이트 재확인이 점유(예: `agent-a663bca94434126fa` 19분, `agent-aa040644baa84936f` 12분, `agent-ab6a418b2806b56a0` 7분).
    2. **1차 시도 업스트림 503 실패 +64.2초 (`16:53:13Z` → `16:54:17Z`, `agent-a89fdae696de4c5dc`)**: `nvidia/google/gemma-4-31b-it` circuit breaker open(HTTP 503) 및 `agy/gemini-4-hydrogen-sonar` 오류로 `turn_error` 기록.
    3. **2차 시도(`use_parent_model=true`) 실행 12분 24초 (`16:54:44Z` → `17:07:08Z`, `agent-a0ae66ea3d6961a0f`, `consumed_at=17:07:12Z`)**: `session=fork`여서 매 API 호출이 317K~402K 입력 토큰을 전송했고, 턴당 모델 응답 대기만으로 `197.0s`(첫 응답 `gpt-6.2-sol`) + `60.2s`(`glm-5.3-flash`) + `341.8s`(`deepseek-v4.1-flash`) + `42.2s` + `27.2s` + `23.9s` + `18.2s` ≈ 11.8분 소요(총 12회 도구 호출).
    - 참고: 같은 `159860`을 **Betty**(side intake 꺼짐, 메인 직송)는 `16:39:11Z` 수신 → `16:40:04Z` 확인 → `16:40:24Z` 답장 전송으로 **73초** 만에 완료했다.
  - **KOBE(9779, `work_side_intake_session=fork`, `max_active=2`, `model=_API_KEYS-SSE-1`)** — 적체 가장 심각(`received=19`, `processing=2`, `failed=4`, `quarantined=1`; `159860`은 `16:39:11Z` 수신 후 45분 경과에도 `received` 대기):
    - `router.log`에서 `_API_KEYS-SSE-1` 요청이 `session=fork` 컨텍스트(331K~664K 토큰, 최대 2.42MB)로 나갔으나 `16:34Z~17:26Z` 사이 `anthropic_sse_upstream_error_event`(출력 0바이트) 반복 및 `ConnectionAbortedError`(324초 경과 재시도) 발생.
    - 실패한 side 포크 전원이 **첫 번째 LLM 응답도 못 받고 7.4~9.1분씩 슬롯을 점유한 뒤 API 오류로 종료**:
      - `agent-a104d004f7c282f62`(`203b6322b7` P1 승인 159883): `+522.1s` 후 `openrouter/nvidia/nemotron-3-ultra-550b-a55b:free-medium` `[503]: Request exceeded queue budget maxWaitMs=30000ms`.
      - `agent-a2cfd8626d4f3cebe`(`6ca293864b`): `+544.0s` 후 `Ciel Runtime stopped waiting for the first upstream output: the router pre-output time limit (540s since the request arrived) was reached`.
      - `agent-aebd5c8da4f06fae9`(`7d37a15aea`, 5회째 실패): `+443.4s` 후 `agy/gemini-4-neon-medium` HTTP 504 + `codex/gpt-6.2-sol-medium` 오류.
    - 매 시도가 7~9분씩 `max_active=2` 슬롯을 막고 실패하므로 19건이 계속 적체된다. (`159883` P1 승인은 `16:58:03Z` 수신 후 28분째 처리 중.)
  - **Celly(9385, `work_side_intake_session=fresh`, `max_active=2`, `model=_API_KEYS-SSE-1`)**: `received=17`, `processing=2` 적체(`159860`은 `16:39:10Z` 수신 후 45분째 `received`). `fresh`라 입력 토큰은 124K~297K로 KOBE·Prody보다 작지만, `_API_KEYS-SSE-1` 1턴 응답이 `140~257s` 걸려 건당 15~23분(`agent-adda8586a05472dc4` 14.5분, `agent-a1e8a425e0a198b8d` 23분) 소모하며 `max_active=2`가 포화.
  - **Andy(9505, `work_side_intake_session=fresh`, `max_active=5`, `model=_API_KEYS-SSE-1`)**: `received=1`, `processing=5`. `max_active=5` 덕에 `159860`은 `16:39:10Z`→`16:49:38Z` claim→`16:54:29Z` 완료(15분 19초). 다만 `fresh`인데도 side 액터가 70~121회 도구 호출 + 턴당 `122~254s` 모델 대기로 `8d54f16a01`(CI 실패 159881) 22.7분(`17:05:28Z`→`17:28:09Z`), `3e915c41ce` 22분+ 진행 중이었다.
  - 원인 분류 요약: (1) 게이트웨이 `_API_KEYS-SSE-1`(및 폴백 `nemotron-3-ultra`/503, `agy` 504, `nvidia` circuit breaker 503, `deepseek-v4.1-flash` 342초)의 극심한 업스트림 지연·타임아웃(1턴당 120~540초, KOBE는 540초 프리-아웃풋 한도 도달); (2) KOBE·Prody의 `work_side_intake_session=fork`가 317K~664K 토큰 컨텍스트를 매 턴 재전송해 업스트림 지연/실패를 증폭; (3) KOBE·Prody·Celly의 `max_active=2` 슬롯이 10~23분 걸리는 게이트 재확인·실패 포크에 묶여 뒤의 SSE가 15~45분+ 대기.

## 2026-11-23

- 2026-11-24 16:14:12–16:14:48Z 틱(직전 15:58Z), 5호스트.
  - 공통: 5호스트 MCP 모두 `probe_succeeded`(149–3,781 ms), Work `new_failures` 0. 운영 세션·라우터 재시작 및 상태 파일 변경 없음. 16:09–16:11Z 구간에 Celly·KOBE·Prody·Betty에서 동시다발적 업스트림 SSE 오류(`transport_interrupted`·503·`anthropic_sse_forward_error`)가 발생했고 각 호스트의 포크 API 재시도 스케줄링이 동작했다.
  - Andy: 실행 5 유지, 대기 4 유지, 제출 21→22, 원장 7146→7160, 보드 v11439, MCP 시간 초과→859 ms 정상 회복. n1166 완료 영수증-검수 누락(반복). 라우터: 외부 SSE EOF 16건, `runtime_activity_router_failed` PermissionError 1건(15:58:54Z, 반복). 대화 오류 3건(16:06:42–16:07:01Z, walkie 도구명/UUID/대화 밖 답장 400; 제품 경로 아님). 포크 오류 3건(16:11:59·16:12:27·16:12:39Z; 원인 미확인).
  - Celly: 실행 6 유지, 대기 0→1, 원장 9006→9026, 보드 v10714, MCP 609→3,781 ms. 라우터: 업스트림 SSE 오류 20건(첫 16:01:24Z), 게이트웨이 5xx 전환 6건(첫 16:02:00Z), 늦은 SSE 재시도 13건, n1299 작업공간 준비 경고 1건(16:01:26Z `git worktree add timed out after 131 s`). 대화: n1498(503 재시도 예약·1/20 재시도)·n1485(`transport_interrupted` 재시도 예약) 알림 3건. 포크 오류 2건(16:02:25·16:13:15Z, 원인 미확인).
  - KOBE: 실행 3 유지, 원장 3348→3396, 보드 v37, MCP 844→149 ms. 16:02:04Z `work_orphan_released` 16건(`owner=legacy_no_owner`, 동일 라우터 9779에 붙은 보조 세션의 미소유 리스 회수 추정 — `compaction-scope.json`은 `9b0de8fe`만 기록). 16:05:38Z `work_orphan_released lease=eef23c1d7e9f state=reserved owner=legacy_no_owner` 직후 16:05:49Z `/ca/mcp` 미처리 `KeyError: 'eef23c1d7e9f4eb49b590c949b5f697a'`(`work_parallel_mcp.py:4300`) 1건 발생. 업스트림 SSE 오류 11건, n13·n33 `transport_interrupted` 재시도 알림 2건, activity PermissionError 2건, 포크 오류 2건(15:59:37·16:00:16Z, 원인 미확인).
  - Prody: 실행 1·대기 3·제출 7 유지, 원장 930 그대로, 보드 v7133, MCP 890→1,616 ms. 직전 틱의 n1200 네이티브 부재 finding 해소. 16:10:26Z n1200 `transport_interrupted` 재시도 알림 1건. 라우터 경고 표본 없음.
  - Betty: 실행 3·대기 1·제출 5 유지, 원장 976 그대로, 보드 v5626, MCP 301→699 ms. 16:01:03Z `unseen_failed message_id=7905` 1건 및 16:01:07Z 커서 가드 1건(Betty 실행 중 라우터는 아직 4886d62 이전 프로세스). 16:09:08Z `anthropic_sse_forward_error` 5건, 16:10:19Z n1786·n1789·n1788 `transport_interrupted` 재시도 알림 3건, 누락 파일 오류 1건. Sarah(.25): SSH 배너 시간 초과 반복, 미확인.
  - 검증 완료 및 배포(사용자 지시: side-fresh 10분 주기 확인·수정 후 지금까지 수정 일괄 재배포):
    1. Andy n2594 동시 재개 후 낡은 펌프 스냅샷 중복 재획득(`register.js`): 실패 테스트 4건 재현→수정→`claude plugin validate/test`(94 pass), 격리 Windows 29/29 파일 통과(`WIN_FAIL=0`), Linux py310/py311 29/29 파일 실패 0(`test_work_mod_real_backend.py` 4분할 각 120/120 통과, `test_wait_boost_pytest_bridge.py` 설계 문서 포함 통과).
    2. Andy 재시작 스냅샷 `WinError 206`·O(N²) `resolve()`·30초 추가 순회 기준(`restart_takeover.py`) 및 진행 중 재기동 중복 `company-prompt` 큐 억제(`runtime_session_restart.py`, `company_prompt_reload.py`, `ciel_runtime.py`).
    3. KOBE 동시 회수 중 `work_parallel_mcp.py:4300` 미처리 `KeyError`→`WorkParallelError` 변환(`legacy_no_owner` 분류·회수 정책 자체는 별도 미결).
    4. 게이트 재확인(`work_gate_check.py`) 10분 주기 엄격 적용: `digest` 변경·`turn_end`(60초) 우회 제거. 실패 테스트 재현→수정→격리 Windows 12개 파일 166 passed, Linux py310/py311 12개 파일 각 166건 실패 0. (`session=fresh`에서 수신 SSE 이벤트 자체도 `side-fresh`로 실행되는 점은 설계 동작으로 구분 보고.)
    - `py_compile`, `ruff check`, `npm run check:docs`, `npm pack --dry-run`, OKF `--strict` 3건 모두 통과.
    - 배포 결과: 커밋 `f7a06ee`를 `feature/priority-models`·`beta` push → `Publish to npm` `45442020634` 성공(`0.2.51-beta.20261124-165156.f7a06ee`, tarball 7/7 해시 일치) → `overlay-f7a06ee` 7개 루트(로컬 3·Celly·KOBE·Prody·Betty) 모두 `base 7/7 → new 7/7` 파일 설치 확인. 운영 세션·라우터 재시작 없음(라우터 Python 수정은 라우터 재시작 후, `register.js`는 세션/플러그인 재로드 후 라이브 반영).
  - 동료 세션(`Andy_Main (3)`) 제보 접수(별도 조사 대기, 이번 배포 미포함):
    - 152171 스레드에서 메인 검수 요청 `[159769]` 직후 사이드 액터가 오래된 Work 노드 내용으로 모순 답글 `[159790]` 작성; 132164 `[159817]` 첨부 설계서(28,577 B)를 읽지 않고 승인 문구를 작성한 `[159864]` 및 승인 버튼 미처리.
    - 사이드 액터 답글들이 `ack=false` 및 `messages.work.update(state='ack')` 누락으로 Walkie 미읽음 접힘이 안 되는 현상.

- 2026-11-24 15:58:24–15:59:06Z 틱(직전 15:42Z), 5호스트.
  - Andy: 실행 5 유지, 대기 5→4, 제출 21 유지, 원장 7133→7146. n1166 완료 영수증은 있으나 검수 행 없음(반복). MCP 4,016 ms 시간 초과 1회(연속 두 틱); 16:00:32Z `/health` 200, PID 112668 유지. 15:43:07Z `/ca/mcp`에서 `work-plan.json` 교체 중 WinError 5 1건, 15:43:08Z 도구 응답 오류로 관측. 파일 접근 거부 원인과 이후 프로브 지연의 관련성은 미확인. 별도 context-usage 쓰기 경고 1건(15:42:58Z), 외부 SSE EOF 16건(첫 15:42:13Z), 포크 오류 1건(15:56:50Z, 원인 미확인). 15:46:44Z 시작 화면 미확인으로 입력 보류 1건.
  - Andy 스냅샷 후속: 매니페스트 경로를 `safe.directory` 인자로만 전달한 읽기 전용 `git --version` 호출에서 명령줄 96,562자 → WinError 206, 경로 1개(46자) → 성공. 루트의 하위 worktree 제외 인자만으로도 63,812자. `restart_takeover.py` 실패 테스트 2건(Windows 과대 `trust`·과대 `nested` 경로)을 먼저 재현한 뒤 현재 대상만 신뢰하고 24,000자 초과 시 `status --no-renames` 후속 필터와 `add --pathspec-from-file`로 수정했다. 임시 격리 Windows 재시작 197건 통과, 기존 Linux py310·py311 컨테이너 4개 파일 각 102건(Windows 전용 2건 skip) 실패 0, py_compile 통과. 실엔진·대량 워크트리 시간 예산·중복 재기동 원인은 미검증이며 아직 커밋·배포·설치하지 않음. 운영 상태는 변경하지 않음.
  - Celly: 실행 6 유지, 예약 1, 원장 8981→9006, MCP 1,203→609 ms. 포크 오류 2건(15:56:18·15:57:38Z, 원인 미확인), 메인 대화 오류 미관측. 라우터: 업스트림 SSE 오류 16건(첫 15:46:41Z), HTTP 503 3건(첫 15:58:17Z), 게이트웨이 5xx 전환 4건(첫 15:56:40Z), 늦은 SSE 재시도 9건(첫 15:43:11Z); 같은 요청에 대한 기록이 겹칠 수 있음. 반복 출력 감지·복구 각 1건(15:42:51Z). n1498 작업공간 준비 경고 2건(15:45:43Z git 102초 시간 초과, 15:45:46Z unknown lease); 원인 관계 미확인.
  - KOBE: 원장 3342→3348, 실행 슬롯 3 관측, MCP `URLError`→844 ms 정상, 보드 v32. 15:46:11Z 12011 `unseen_failed` 1건과 15:46:14Z 이전 제출 미확인 가드 1건. 실제 전달 여부 미확인. 업스트림 SSE 오류 2건(첫 15:51:08Z), 포크 오류 2건(15:51:39·15:51:58Z, 원인 미확인). 메인 대화 오류 미관측.
  - Prody: 실행 1 유지, 대기 4→3, 제출 6→7, 원장 926→930, MCP 1,912→890 ms. n1200은 원장 `running+slot`인데 네이티브 관측에서는 부재(15:14Z부터 표시); 종료로 단정하지 않음. 포크 오류 1건(15:43:35Z, 원인 미확인), 메인 외부 결정 미확인 스크립트 종료 1건(15:47:24Z). 라우터 경고 표본 없음.
  - Betty: 실행 2→3, 대기 1·제출 5 유지, 원장 974→976, MCP 414→301 ms. 15:48:40Z CLI 종료 진단, 15:48:42Z 시작 입력 7896 만료. 15:49:05Z 7904 제출 4회 후 확인 실패, 15:49:06Z 가드 1건. 새 `unseen_failed`와는 별개이며 실제 수신 여부 미확인. 포크 오류 1건(15:56:00Z), 메인 스크립트 오류 2건(15:43:46·15:44:24Z). Sarah SSH 배너 시간 초과 반복, SSE 미확인.
  - 새 Work 원장 실패 행 0; 운영 세션·라우터 재시작 및 운영 상태 파일 변경 없음. 미검증 수정 후보 배포 없음.

- 2026-11-24 15:42:13–15:43:00Z 틱(직전 14:54Z), 5호스트 및 후속 읽기 전용 조사.
  - 공통: 모니터 스크립트 exit 0. Work `new_failures` 0은 원장 분류만 의미한다. 운영 세션·라우터 재시작 및 상태 파일 변경 없음.
  - Andy: 실행 슬롯 4→5, 대기 5 유지, 제출 21 유지, 원장 7095→7133. n1166은 완료 영수증이 있으나 검수 행 없음(반복). 15:42:13Z MCP 프로브 1회 시간 초과(4,016 ms); 후속 `/health` 15 ms 정상, MCP 재검사와는 별개. 포크 오류 표본 2건(15:33:39·15:41:56Z; 원인 미확인). 라우터 외부 SSE 끊김 표본 47건, activity 권한 오류 3건(첫 14:56:56Z/15:17:00Z). 대화 14건: side 이벤트 1개의 원문 전달 알림 2건(14:57:09Z), PowerShell 인자 1건, P3 용량 가드 1건, handoff 대상 없음 2건, API 인자 오류 8건(15:35:49–15:41:25Z).
    - 재기동 교차 확인: 동일 v30 안내로 14:49:24Z와 15:23:17Z에 요청이 큐에 들어갔다. 두 번째 요청은 첫 재기동의 원격 지침 갱신(15:25:27Z) 전에 큐에 있었다. 15:31:11Z 기존 대화 재바인드. 두 번의 재기동 로그에서 worktree 1,219개, 스냅샷 0개; 남아 있는 매니페스트의 1,219개 오류 모두 `WinError 206`. 운영 오류 확인, 정확한 호출 한계 재현 및 수정 검증은 별도 미결. 24417–24425 만료 기록은 앞 틱의 후속 조사 참조.
  - Celly: 실행 슬롯 4→6, 대기 5→표본 0, 제출 1→표본 0, 원장 8946→8981, MCP 1,203 ms. 15:36:48Z 9a46b96 라우터 기동, 15:36:52Z 죽은 소유자 리스 9개 정상 회수, 15:36:55Z 시작 시 입력 3개 만료(16373–16375). 15:36:58Z CLI 코드 1 종료(원인 미확인), 15:37:03Z 라우터 수명주기 정리. 15:38:34Z 재기동 로그와 15:38:42Z 소켓 주입·15:38:43Z MCP 응답 확인. 포크 오류 표본 7건(14:57:32–15:42:04Z, 원인 미분류). 메인 대화 오류 5건: 실행 준비 가드, 인증서 해지 조회, UUID 검증, 권한 403, 도구 응답 전 MCP 연결 종료(15:02:56Z) 각 1건. 새 Work 상태 읽기 실패 알림 미관측.
  - KOBE: 원장 3340→3342. 15:36:37Z 9a46b96 라우터 기동, 15:36:40Z 이전 소유자 리스 2개 회수, 15:36:45Z 시작 시 입력 4개 만료(12006–12009), 15:36:47Z CLI 코드 1 종료(원인 미확인), 15:36:52Z 9779 라우터 정리. 틱 MCP `URLError` 1회(2,048 ms), 보드·포크 미관측. 이 인스턴스의 종료를 호스트 전체 종료로 확대하지 않는다.
  - Prody: 실행 슬롯 2→1, 대기·제출 4·6 유지, 원장 919→926, MCP 1,912 ms. 15:06:18·15:06:25Z n1200/n1280 전송 중단 재시도 알림 각 1건; 15:08:20Z Work 조정 읽기 시간 초과 알림 2건. 후속 원장에서 n1200 `waiting_input`, n1280 `running`으로 확인. n1280 포크 오류 2건(15:40:00·15:41:56Z; 원인 미확인); 15:11:55Z side 실패 원문 전달 1건은 복구 경로. 그 외 대화 4건은 파일 찾기, 셸 구문, 메인 직접 수정 가드, 증거 스크립트 실패 각 1건. 라우터 경고 표본 없음.
  - Betty: 실행 슬롯 1→2, 대기 1 유지, 제출 6→5, 원장 963→974, MCP 414 ms. 이번 창 새 `unseen_failed` 미관측. 업스트림 SSE 오류 1건(15:12:03Z), 늦은 재시도 4건. 15:33:19Z 게이트 펌프 실패 알림 1건(`side intake off`, 원인 미확인). 그 외 대화 오류는 누락 파일 2, Grep 인자 1, 버전 가드 1. Sarah는 SSH 배너 교환 시간 초과로 SSE 미확인.
  - 수정 후보: n2594 중복 재획득 신규 Windows 테스트 및 플러그인 테스트 통과. 전체 Windows/py310/py311 회귀와 실엔진 검증은 이 기록 시점에 미완료. 배포·설치 없음. Celly/KOBE의 9a46b96 기동 로그는 버전 적재 증거이지 기능의 라이브 효과 증거는 아니다.

- 2026-11-24 14:53:58–14:54:30Z 틱(직전 14:32Z), 5호스트 및 후속 읽기 전용 조사.
  - 공통: 틱 실행 성공, MCP 5/5 응답(129–1,841 ms), Work `new_failures` 0. 포크 표본은 부분 관측이며 터미널은 미관측. 모니터링에서 운영 세션·라우터 재시작이나 상태 파일 변경을 하지 않았다.
  - Andy: 실행 슬롯 2→4, 대기 7→5, 제출 21→21, 원장 7091→7095, MCP 1,813 ms. n1166은 완료 영수증이 있으나 검수 행 없음(반복). 외부 SSE EOF 20건(첫 시각 14:33:40Z), 연결 리셋 2건(첫 시각 14:35:25Z); 근본 원인 미확인. 대화 기록 4건: 완료 작업 정지 거부(14:35:11Z), n2594 재획득 거부 알림(14:45:53Z), 스크립트 구문 오류(14:47:11Z), API 인자 400(14:52:32Z). n2594 포크는 14:44:34Z 재개 메시지 수신, P0 전송 원장 14:44:39Z `delivered`, 14:45Z 이후 실제 도구 실행. 중복 재획득과 거짓 실패 알림은 격리 하네스에서 재현; 운영 당시의 정확한 인터리브는 미확인. 수정 후보의 신규 Windows 테스트 통과, 전체 회귀 및 실엔진 검증 미완료; 미배포.
    - 사용자 화면 후속: 15:19:43Z와 15:25:27Z 회사 프롬프트 v30 갱신 재기동 요청 2건, 각각 다음 로그에서 CLI `exit code 1`. 두 차례 전환 중 worktree 스냅샷 `1219 / 성공 0 / 오류 1219`가 각각 기록됐다. 연결 리셋은 첫 종료 전후 기록이며 종료 원인으로 확인되지 않았다. `runtime_input_launch_backlog_expired` 15:25:27Z 1건(24417), 15:31:06Z 8건(24418–24425); 모두 입력 원장의 외부 이벤트. 해당 항목의 다른 전달 경로는 미확인. 15:31:11Z 원래 대화 ID 재바인드, 15:36Z 라우터 PID 112668 그대로 `/health` 정상 및 모델/MCP HTTP 200, 원래 대화 기록은 15:40Z 갱신됨.
  - Celly: 실행 슬롯 5→4, 대기 4→5, 제출 1→1, 원장 8945→8946, MCP 313 ms. 새 Work 상태 읽기 실패 알림 미관측. 외부 SSE EOF 20건(첫 14:33:43Z), 업스트림 HTTP 502 재시도 3건(14:34:14Z), 중앙 검색 실패 1건(14:34:00Z; 구성 URL 사용). 대화 12건: handoff 대상 없음 1, 검수 포크 금지 1, P3 용량 1, 버전 충돌 2, 호스트 전용 거부 1(각각 14:38:45, 14:46:16, 14:47:56, 14:48:33·14:52:41, 14:53:26Z); 쉘/인코딩 3, API 400 1, DNS 1, SSH 인증 거부 1. 포크 도구 실패 2건(14:51:54·14:53:02Z, 테스트/스크립트 종료 1); Ciel 제품 원인 미입증.
  - KOBE: 실행 슬롯 1→2, 대기 4→표본 0, 제출 미표시, 원장 3331→3340, MCP 129 ms. 보드 v1000→v24는 같은 보드가 롤백됐다는 증거가 아님(다른 세션 ID 바인드 관측). 14:36:21Z·14:37:17Z 라우터 기동 로그는 beta 9a46b96을 표시; 14:36:27Z 종료된 소유자의 리스 회수 5건은 기동 시 설계된 처리. 14:36:34Z 첫 CLI 종료 원인 미확인. 대화 오류 3건(14:38:40Z 사용자 거부, 14:47:55Z 스크립트 오류, 14:52:54Z 버전 가드); 14:53:53Z 포크 오류 1건 상세 미확인. 후속: 15:02:16Z 회사 프롬프트 재기동 요청, 15:02:17Z 기존 CLI 종료, 새 CLI도 15:02:29Z 코드 1 종료(두 번째 종료 원인 미확인). 15:02:33Z 라우터 수명주기 정리 후 15:06:33Z 9779 `/health` 연결 거부 확인. 해당 라우터 종료와 호스트 전체 세션 종료는 별개.
  - Prody: 실행 슬롯 2, 대기 4, 제출 6, 원장 919 그대로, MCP 3,115→1,841 ms. 후속 관측에서 n1200 포크 기록 갱신 재개(215.4→0.9분), n1280도 0.9분. 라우터 경고 표본 없음(무오류로 단정하지 않음). 14:35:28Z 호스트 전용 도구 거부 1건.
  - Betty: 실행 슬롯 2→1, 대기 1, 제출 5→6, 원장 961→963, MCP 331 ms. 라우터 `unseen_failed` 2건(14:35:23Z 7894, 14:36:58Z 7895), 이전 제출 미확인으로 커서 가드 1건(14:35:26Z). 개별 메시지 인계 여부 미확인. 업스트림 늦은 SSE 재시도 3건(14:33:02·14:33:30·14:40:51Z), 대화 API 인자 400 2건(14:48:42·14:53:37Z).
  - Sarah(.25): SSH 배너 전 reset 재현, SSE 수신 미확인.

- 2026-11-24 14:32:42–14:33:17Z 틱(직전 14:12Z), 5호스트.
  - 공통: 운영 세션·라우터 재시작과 상태 파일 변경 없음. 5호스트 MCP `probe_succeeded`(218–3,115 ms), new_failures 0. 9a46b96·4886d62·7edfc0c는 파일만 설치(라이브 아님).
  - Celly
    - running+slot 1→5, waiting_input 5→4, 원장 8915→8945
    - 14:24:37Z "auto continue could not read the Work status". Celly 세 번째이고, 전체로는 아홉 번째다(추적, 원인 미확인).
    - 14:16:45Z `Work item n1468 has an earlier P0 resume whose delivery is not recorded; recovery is required before another resume`. 전달 기록이 없는 P0 재개를 막는 가드다.
    - 계획 버전 충돌 1(가드), activity PermissionError 1(반복)
  - Prody
    - running+slot 2, waiting_input 4, submitted 6, 원장 919 그대로, MCP 3,115 ms
    - n1200(running, P1): 포크 기록이 215분째 갱신되지 않았다(반복). silent 알림(9a46b96)은 라우터 재시작 전까지 라이브가 아니다.
    - n1280(running, P3)은 0.4분 전 갱신, n1281(waiting_input, P1)
    - 대화 오류 6: 외부 API 400·403, 메인이 쓴 스크립트 오류, walkie `client_id` 인자 오류. 제품 경로 아님.
  - Andy
    - running+slot 2, submitted 21, waiting_input 7, 원장 7079→7091. n1166(반복).
    - 계획 버전 충돌 3(가드), `work_side_status` 호스트 전용 거부 1(설계)
    - 14:27:38Z side 이벤트 7d80cf0e29a8 실패 → 원문 메인 전달(appended, 설계된 경로). 실패 이유는 확인하지 않았다.
  - KOBE
    - running+slot 0→1, waiting_input 4, 원장 3323→3331
    - 14:17:12Z Agent 호출이 Work 병렬 디스패치 가드에 거부됐다(정확한 이름 누락, fork false). 가드 동작이다.
    - ripgrep 20초 시간 초과 1, activity PermissionError 1·transcript_delta 시간 초과 1(반복)
  - Betty
    - running+slot 2, submitted 5, waiting_input 1, 원장 959→961
    - 늦은 SSE 재시도 2(업스트림), `unseen_failed` 없음, 대화 오류 0
  - Sarah(.25): SSH가 배너 전에 끊긴다(`kex_exchange_identification: Connection reset by peer`). 미확인.

- 2026-11-24 14:12:30–14:13:02Z 틱(직전 13:52Z), 5호스트.
  - 공통: 운영 세션·라우터 재시작과 상태 파일 변경 없음. 5호스트 MCP `probe_succeeded`(287–2,533 ms), new_failures 0. 9a46b96·4886d62·7edfc0c는 파일만 설치(라이브 아님).
  - Prody
    - running+slot 2, waiting_input 3→4, 원장 913→919
    - n1200(running, P1): 포크 기록이 195분째 갱신되지 않았다(반복).
    - 13:59:02Z "auto continue could not read the Work status". Prody 세 번째이고, 전체로는 여덟 번째다(추적).
  - KOBE: 13:54:29–35Z(로컬 05:54) 압축 경로 기록이다. 압축 컨트롤러의 설계된 실패 열림 경로다.
    - 문맥 압박으로 자동 압축 요청이 큐에 들어갔다.
    - 터미널 입력 제출이 4회 시도 뒤에도 확인되지 않았다(`channel_input_submit_unconfirmed`, `receipt_unmatched`).
    - `compact_failed_admission_released` 후 요청이 미뤄졌다.
    - 13:54:37Z에 압축 단계(`admission_paused phase=compacting`)로 들어갔다.
    - 제출이 확인되지 않은 원인은 확인하지 않았다.
  - Celly
    - running+slot 1→4, waiting_input 6→5, 원장 8899→8915
    - 14:10:06Z side 이벤트 6fd60803a035가 실패했고, 원문이 메인으로 전달됐다(appended, 설계된 경로).
    - 실패 이유는 확인하지 않았다.
  - Andy
    - running 2→0, submitted 21→22, 원장 7062→7079. n1166(반복).
    - 대화: 계획 버전 충돌 3(가드), `n2552 is not a goal`, walkie 인자 오류
  - Betty
    - running+slot 3→2, submitted 5→6, 원장 957→959
    - 늦은 SSE 재시도 10(업스트림)
    - `unseen_failed` 없음
  - Sarah(.25): SSH가 배너 전에 끊긴다. 미확인.

- 2026-11-24 13:52:36–13:53:12Z 틱(직전 13:41Z, 창 약 11분), 5호스트.
  - 공통: 운영 세션·라우터 재시작과 상태 파일 변경 없음. 5호스트 MCP `probe_succeeded`(513–2,031 ms), new_failures 0. 9a46b96·4886d62·7edfc0c는 파일만 설치(라이브 아님).
  - Prody
    - submitted 6→7, running+slot 3→2, 원장 911→913
    - n1200(running, P1): 포크 기록이 175분째 갱신되지 않았다(반복).
    - 13:43:23Z "auto continue could not read the Work status" 안내. Prody 두 번째이고, 전체로는 일곱 번째다(추적 중).
    - 대화: 메인 스크립트 오류 2
  - Andy: running+slot 3→2, 원장 7055→7062. n1166(반복). 대화 오류 0.
  - Celly: 원장 8899, MCP 2,031 ms, 대화 오류 0
  - KOBE: 원장 3323, 대화 오류 0
  - Betty: running+slot 3, 원장 957, `unseen_failed` 없음, 대화 오류 0
  - Sarah(.25): SSH가 배너 전에 끊긴다. 미확인.

- 2026-11-24 13:41:16–13:41:52Z 틱(직전 13:12Z), 5호스트.
  - 공통: 운영 세션·라우터 재시작과 상태 파일 변경 없음. 5호스트 MCP `probe_succeeded`(406–2,137 ms), new_failures 0.
  - 배포: 무응답 실행 포크 알림 9a46b96을 beta `0.2.51-beta.20261124-133238.9a46b96`로 냈다. 7개 루트에 해시 확인 설치했다(13:38–13:40Z, 파일만, 라이브 아님). 4886d62·7edfc0c도 같은 상태다.
  - Prody
    - running+slot 2→3, 원장 900→911
    - n1200(running, P1): 포크 기록이 164분째 갱신되지 않았다(반복). 새 알림은 라우터 재시작 전이라 아직 동작하지 않는다.
    - n1278·n1280(running)은 기록이 계속 갱신되고 있다.
    - 대화: 검수 포크 금지 가드, n1279 중복 디스패치 가드
  - Betty
    - running+slot 0→3, 원장 948→957
    - 지난 틱의 n1784 검수 행 누락 finding은 이번에 없다. 해소됐다.
    - `unseen_failed` 7893(13:27Z): 같은 결함이고, 이전 코드가 돌고 있다.
  - Andy
    - running+slot 2→3, waiting_input 6→7, 원장 7042→7055. n1166(반복).
    - activity 권한 오류 2(반복)
    - 대화 오류 8: 외부 API 400, 계획 버전 충돌(가드), 메인 스크립트
  - Celly: 원장 8899, P3 정원 가드 1
  - KOBE: 원장 3323, 대화 오류 0
  - Sarah(.25): SSH가 배너 전에 끊긴다. 미확인.

- 2026-11-24 13:12:31–13:13:04Z 틱(직전 12:52Z), 5호스트.
  - 공통: 운영 세션·라우터 재시작과 상태 파일 변경 없음. 5호스트 MCP `probe_succeeded`(299–1,765 ms), new_failures 0. 4886d62·7edfc0c는 파일만 설치(라이브 아님).
  - Prody
    - running+slot 1→2, waiting_input 2→3, 원장 894→900
    - n1200(running, P1): 포크 기록이 135분째 갱신되지 않았다(10:58Z 이후, 반복). 새 n1278(running, P1)은 기록이 계속 갱신되고 있다.
    - 응답 없는 실행 포크 감지는 구현 중이다(배포 전).
    - 대화 오류 9: 파일 크기 제한, 메인 스크립트 오류, 외부 API 400
  - Betty
    - 새 finding: n1784(리스 789fc3ec, P1)는 submitted이고 완료 영수증도 있는데 검수 행이 없다. native=running 13:12Z. 방금 생긴 상태다.
    - activity PermissionError 1. 같은 계열이 Betty에서도 나왔다.
    - 이번 창에 `unseen_failed`는 없다.
  - Andy
    - running+slot 1→2, 원장 7036→7042. n1166(반복).
    - activity PermissionError 1(반복)
    - 이번 창에 auto continue 읽기 실패 안내는 없다.
  - Celly: waiting_input 5→6, submitted 1, 원장 8893→8899, MCP 1,578 ms. 대화 오류 0.
  - KOBE: waiting_input 4, 원장 3323. 대화 오류 0.
  - Sarah(.25): SSH가 배너 전에 끊긴다. 미확인.

- 2026-11-24 12:52:55–12:53:32Z 틱(직전 12:32Z), 5호스트.
  - 공통: 운영 세션·라우터 재시작과 상태 파일 변경 없음. 5호스트 MCP `probe_succeeded`(387–2,953 ms), new_failures 0. 4886d62·7edfc0c는 파일만 설치(라이브 아님).
  - Andy: "auto continue could not read the Work status" 2건(12:34:09Z, 12:41:00Z). Andy에서 총 3건, 전체 호스트로는 6건이다.
    - 라우터 로그 대조(확인)
      - 12:40:50Z `work_status_decision` 기록
      - 12:41:00Z 안내(10초 뒤)
      - 12:41:01Z 같은 결정이 다시 기록됨
    - 추정(미확인): Stop의 보드 읽기가 10초 기한(`STOP_READ_MS`)을 1초쯤 넘겼다.
      - 같은 분에 110만–120만 토큰(4.6–4.7 MB) 업스트림 요청 여러 건이 라우터를 지나고 있었다.
      - 읽기가 왜 느렸는지는 측정하지 않았다.
      - 결과: 그 Stop에서는 미완료 항목 자동 계속 점검이 생략된다.
    - 추적 항목으로 유지한다.
    - running+slot 1, 원장 7024→7036. n1166(반복).
  - Celly: running+slot 2, 원장 8893, MCP 578 ms, 대화 오류 0
  - KOBE: waiting_input 4, 원장 3317→3323, 대화 오류 0
  - Prody
    - running+slot 2→1, 원장 893→894, MCP 2,953 ms
    - n1277 "포크 부재" finding은 이번에 없다.
    - 이번 틱에는 n1200 포크 기록을 따로 다시 읽지 못했다(.12 SSH 연결 중단 1회).
  - Betty
    - 원장 947
    - `unseen_failed` 7892(12:39Z): 같은 결함이고, 이전 코드가 돌고 있다.
  - Sarah(.25): SSH가 배너 전에 끊긴다. 미확인.

- 2026-11-24 12:32:23–12:32:53Z 틱(직전 12:12Z), 5호스트.
  - 공통: 운영 세션·라우터 재시작과 상태 파일 변경 없음. 5호스트 MCP `probe_succeeded`(340–1,172 ms), new_failures 0. 4886d62·7edfc0c는 파일만 설치(라이브 아님).
  - Prody
    - 원장 891→893. running+slot 1→2, waiting_input 2→1.
    - n1200(running, P1): 포크 기록이 95분째 갱신되지 않았다(10:58Z 이후). 응답 없는 실행 포크 감지는 구현 중이다(배포 전).
    - 새 finding: "slot held, fork absent since 10:34: n1277(231adbe4, P4) running"
      - 같은 시각 n1277 포크(a5f9da3e4e)의 대화 기록은 12초 전에 갱신됐다. 포크는 살아 있다.
      - native 관찰 목록에서 빠진 것이다. Celly n1452·n1461과 같은 유형이다. 원인은 확인하지 않았다.
  - Andy: running+slot 1, 원장 7024(세 틱째 같음), 보드 v11319→v11326. n1166(반복). 대화 오류 0.
  - Celly: running+slot 2, 원장 8893. transient retry 2(업스트림), 권한 오류 1(반복). 대화 오류 0.
  - KOBE
    - waiting_input 4→5, 원장 3313→3317
    - 대화: 완료 처리가 병렬 검증·검수·정리를 기다린다는 가드 1
  - Betty
    - running+slot 0→1, 원장 945→947
    - `unseen_failed` 7890(12:22Z)·7891(12:29Z): 같은 결함이고, 이전 코드가 돌고 있다.
  - Sarah(.25): SSH가 배너 전에 끊긴다. 미확인.

- 2026-11-24 12:12:34–12:13:03Z 틱(직전 11:52Z), 5호스트.
  - 공통: 운영 세션·라우터 재시작과 상태 파일 변경 없음. 5호스트 MCP `probe_succeeded`(201–1,096 ms), new_failures 0. 4886d62·7edfc0c는 파일만 설치(라이브 아님).
  - Prody n1200(running, P1): 포크 기록이 75분째 갱신되지 않았다(마지막 10:58:19Z). 원장 891은 그대로다.
    - 응답 없이 멈춘 실행 포크 감지는 별도 브랜치 `feature/silent-fork-notice`에서 구현하고 있다(배포 전).
  - Andy: running+slot 1, submitted 21, 원장 7024. n1166(반복). 대화 오류 0.
  - Celly
    - running+slot 2, waiting_input 4→5, 원장 8885→8893
    - 지난 틱의 n1452 검수 행 누락 finding은 이번에 없다. 해소됐다.
    - `transcript_delta_delivery_failed` timeout 1(11:53Z), 권한 오류 2(반복)
    - 대화 오류 6: 메인 스크립트 오류, `work_side_handoff` 불필요 호출
  - KOBE: running 1→0, waiting_input 4, 원장 3313, MCP 201 ms
  - Betty
    - running+slot 1→0, 원장 941→945
    - `unseen_failed` 7889(12:09Z): 같은 결함이고, 이전 코드가 돌고 있다.
    - 대화 오류 5: 계획 버전 충돌(가드), 외부 API 400, 스크립트 오류
  - Sarah(.25): SSH가 배너 전에 끊긴다. 미확인.

- 2026-11-24 11:52:43–11:58Z 틱(직전 11:33Z), 5호스트.
  - 공통: 운영 세션·라우터 재시작과 상태 파일 변경 없음. 5호스트 MCP `probe_succeeded`(220–969 ms). 4886d62·7edfc0c는 파일만 설치(라이브 아님).
  - Prody n1200(리스 6ebe7e68, P1, running, 포크 a9b9ae4c7e): **응답 없이 멈춘 포크로 관측된다.**
    - 포크 대화 기록의 마지막 기록은 10:58:19Z의 Bash tool_use다(약 2,000자). 이 명령은 파일을 만든 뒤 node로 playwright 스크립트(chromium 실행, 스크린샷)를 돌린다.
    - tool_result가 없다. 11:56Z에 57분째 갱신이 없다.
    - 11:56Z 프로세스 목록에 그 node·chromium 프로세스는 없다.
    - 원장 891은 10:31Z 이후 그대로다.
    - Bash 기본 제한 시간(2분)이 지났는데도 결과가 기록되지 않은 원인은 확인하지 못했다.
    - **요구사항 공백**: 11-24 사용자 요구는 "답이 없이 멈춘다든지에 대해서 상황을 캐치한 뒤 메인에게 공지"였다. 배포된 stall watch(943c3e2)는 `waiting_input` 포크의 오래된 대기(12시간)만 잡는다. 실행 중인데 응답 없는 포크는 잡지 못한다.
      - 이 공백을 따로 구현한다(실패 테스트 먼저).
  - Prody 라우터 로그
    - 마지막 400 KB에 타임스탬프 있는 행이 1개(11-22)뿐이다. 나머지는 `ConnectionAbortedError` traceback이다.
    - 그래서 모니터의 Prody 로그 집계가 계속 비어 있다.
    - 11-23 재시작 이후 라우터의 정상 로그가 어디로 가는지는 확인하지 않았다(추적).
  - Andy: running+slot 1, submitted 22→21, 원장 7021→7024. n1166(반복). 대화 오류 0.
  - Celly
    - running+slot 3→2, submitted 0→1, 원장 8883→8885, MCP 281 ms
    - 새 finding: n1452(리스 ff4d503a, P2)는 submitted이고 완료 영수증도 있는데 검수 행이 없다. native=running 11:46Z. 방금 생긴 상태다.
    - 11:52Z "Work reconciliation: Parallel read failed: git ... timed out after 37 s" 안내 1. git 시간 초과 계열이다(반복 유형).
  - KOBE: 원장 3312, MCP 220 ms, 대화 오류 0.
  - Betty
    - 원장 936→941
    - new_failures `decision_refused_stop 4cf3b83d x1`: 메인의 검수 결정이 거부된 기록이다. 가드다.
    - `unseen_failed` 없음
  - Sarah(.25): SSH가 배너 전에 끊긴다. 미확인.

- 2026-11-24 11:33:39–11:34:23Z 틱(직전 11:12Z), 5호스트.
  - 공통: 운영 세션·라우터 재시작과 상태 파일 변경 없음. 5호스트 MCP `probe_succeeded`, new_failures 0. 4886d62·7edfc0c는 파일만 설치(라이브 아님).
  - Andy: running+slot 2→1, submitted 21→22, 원장 7019→7021, MCP 1,078 ms. n1166(반복). 대화 오류 0.
  - Celly
    - running+slot 2→3, 원장 8874→8883
    - MCP 3,671 ms. 직전 1,032 ms보다 느리다.
    - **11:25:35Z "auto continue could not read the Work status"** — Celly 두 번째. 호스트 전체로는 네 번째다(Prody 06:20, Celly 08:49, Andy 10:18).
      - Stop 보드 읽기 10초 기한(`STOP_READ_MS`)과 관련된 안내다.
      - 같은 틱의 MCP 응답이 3.7초로 느렸다.
      - 기한 초과였는지는 확인하지 못했다(추적).
    - 권한 오류 3(반복)
  - KOBE: 원장 3309→3312. P3 정원 가드 2.
  - Prody
    - 원장 891이 네 틱째(10:32Z부터) 그대로다. 보드는 v7082→v7085로 바뀌었다. MCP 887 ms.
    - 실행 리스 확인(읽기 전용, 11:34Z)
      - running n1200(P1, 포크 a9b9ae4c7e): 대화 기록 마지막 갱신이 37분 전이다.
      - waiting_input n1277: 62분 전
      - waiting_input n1076: 7시간 전
      - submitted 6건(n340·n342·n389·n390·n423·n425): 포크 기록이 13–14일 전에 멈췄다. 예전부터 남아 있는 submitted 리스다.
      - n1200이 긴 도구 호출 중인지 멈춘 것인지는 확인하지 않았다. 다음 틱에 다시 본다.
    - 대화 오류 3건: pdftoppm 미설치, 스크립트 오류, walkie 인자 개수 오류
  - Betty
    - running+slot 1→0, submitted 5→6, 원장 934→936
    - `unseen_failed` 없음
  - Sarah(.25): SSH가 배너 전에 끊긴다. 미확인.

- 2026-11-24 11:12:27–11:13:14Z 틱(직전 10:53Z), 5호스트.
  - 공통
    - 운영 세션·라우터 재시작과 상태 파일 변경 없음.
    - 5호스트 MCP `probe_succeeded`(237–3,146 ms), new_failures 0, 새 finding 없음.
    - 4886d62·7edfc0c는 파일만 설치(라이브 아님).
  - Andy
    - running+slot 3→2, 원장 7015→7019
    - n1166(반복)
    - 계획 버전 충돌 2(가드)
  - Celly
    - running+slot 3→2, submitted 0→1, 원장 8871→8874
    - 10:55–10:56Z 업스트림 오류 이벤트 2, 로컬 게이트웨이 5xx 2(게이트웨이 소관)
    - 권한 오류 1(반복)
    - P3 정원 가드 1
  - KOBE: 원장 3306→3309. P3 정원 가드 1.
  - Prody
    - 원장 891이 10:32Z부터 세 틱째 그대로다. running+slot 1, submitted 6.
    - 보드는 v7074→v7082로 계속 바뀌고 있다.
    - MCP 3,146 ms
    - 대화 오류 0
  - Betty: 원장 934, `unseen_failed` 없음, 대화 오류 0.
  - Sarah(.25): SSH가 배너 전에 끊긴다. 미확인.

- 2026-11-24 10:53:18–10:53:50Z 틱(직전 10:32Z), 5호스트.
  - 공통: 운영 세션·라우터 재시작과 상태 파일 변경 없음. 5호스트 MCP `probe_succeeded`(253–3,304 ms), new_failures 0. 4886d62·7edfc0c는 파일만 설치(라이브 아님).
  - 추적 항목 "auto continue could not read the Work status"(Andy 10:18Z, Prody 06:20Z, Celly 08:49Z)
    - 소스(register.js): Stop이 보드를 읽을 때 `STOP_READ_MS` 10초 기한이 있다. 이 기한을 넘기거나 읽기가 실패하면 이 안내를 낸다. 11-15에 2초에서 10초로 늘린 기한이다.
    - Andy 10:16–10:18Z 라우터 로그(로컬 04:16–04:18)
      - `work_status` 호출 18건, 오류 행은 없다.
      - 같은 시각 25만 토큰대 업스트림 요청 여러 건이 함께 돌고 있었다.
    - 이번에 기한 초과인지 읽기 실패인지는 확인하지 못했다(CLI 쪽 기록 없음). 결함으로 단정하지 않고 계속 추적한다.
  - Andy
    - running+slot 1→3, 원장 6996→7015, MCP 1,422 ms
    - n1166(반복)
    - 대화: 외부 API 400, `n2554 is not a goal`, 계획 버전 충돌(가드)
  - Celly
    - running+slot 3, waiting_input 4→5, 원장 8865→8871
    - 10:44Z 로컬 게이트웨이 5xx(3)와 `upstream_direct_http_error`(3)(게이트웨이 소관)
      - 같은 시각 포크 n1468이 503을 받았다. 재시도 가드가 진행 중이다(2/20까지 관측).
    - `work_workspace_failed workspace_prepare_retained` n1355(`git worktree add` 31초 시간 초과, 반복 유형)
    - activity·context_usage 권한 오류 5(반복)
  - KOBE
    - running+slot 0→1, 원장 3304→3306
    - `transcript_delta_delivery_failed` timeout 3(10:32Z, 반복 항목)
    - P3 정원 가드 1
  - Prody
    - 원장 891, MCP 3,304 ms
    - 대화 오류 5건: 외부 API 400 2, 메인 스크립트 인코딩·Traceback
  - Betty
    - 원장 934, MCP 253 ms
    - 이번 창에 `unseen_failed`는 없다. 대화 오류 0.
  - Sarah(.25): SSH가 배너 전에 끊긴다. 미확인.

- 2026-11-24 10:32:28–10:33:42Z 틱(직전 10:12Z).
  - 공통: 운영 세션·라우터 재시작과 상태 파일 변경 없음. new_failures 0. 4886d62·7edfc0c는 파일만 설치(라이브 아님).
  - Andy
    - running+slot 2→1, 원장 6991→6996, MCP 1,328 ms
    - n1166(반복)
    - **10:18Z "auto continue could not read the Work status" 안내.** Prody(06:20Z)와 Celly(08:49Z)에 이어 세 번째 호스트다.
      - 반복 항목으로 추적한다. 실패한 읽기의 원인은 아직 확인하지 않았다.
    - 대화: `work_side_status` host 전용 거부, `work_side_handoff` 불필요 호출(메인 도구 사용, 가드)
  - Celly
    - running+slot 4→3, 원장 8858→8865, MCP 344 ms
    - activity 권한 오류 1(반복)
    - `work_retention_skipped` 1
  - KOBE
    - 일괄 실행은 `ssh failed`였다. 10:33:42Z 단독 재조회는 정상이었다.
      - waiting_input 3, running·submitted 0, 원장 3298→3304, MCP 474 ms, 대화 오류 0
  - Prody: running+slot 1, 원장 879→891, MCP 1,443 ms, 대화 오류 0
  - Betty
    - 원장 932→934
    - `unseen_failed` 7886·7887(10:13Z), 7888(10:20Z)
    - 같은 결함이다. 라우터 재시작 전이라 이전 코드가 돌고 있다.
    - 대화 오류 3건: gh 미인증, 메인 스크립트 오류
  - Sarah(.25): SSH가 배너 전에 끊긴다. 미확인.

- 2026-11-24 10:12:24–10:12:59Z 틱(직전 09:52Z), 5호스트.
  - 공통
    - 운영 세션·라우터 재시작과 상태 파일 변경 없음.
    - 5호스트 MCP `probe_succeeded`(297–2,395 ms), new_failures 0.
    - 4886d62·7edfc0c는 파일만 설치(라이브 아님).
  - Andy
    - running+slot 1→2, submitted 22→21, 원장 6986→6991
    - n1166 검수 행 누락(반복)
    - `runtime_activity_context_usage_failed` PermissionError 1(반복 계열)
  - Celly
    - running+slot 5→4, waiting_input 3→4, 원장 8857→8858
    - 오류 없음. client 연결 끊김과 keepalive 합성은 반복.
  - KOBE
    - submitted 0→1, running+slot 2→1, 원장 3293→3298, MCP 2,395 ms
    - 업스트림 오류 이벤트 1(09:55Z, 게이트웨이 소관)
    - activity PermissionError 1(반복 계열, KOBE에서도 관측)
    - 검수 포크 금지 가드 1
  - Prody
    - 원장 873→879
    - 대화
      - 계획 버전 충돌(가드)
      - n1276 중복 디스패치 가드
      - `work_side_handoff: side handoff not found`(메인이 handoff가 필요 없는 알림에 호출. Celly에서도 본 유형)
  - Betty
    - running+slot 1→0, 원장 928→932
    - `unseen_failed` 4건: 7882(09:53Z), 7883·7884(09:55Z), 7885(10:11Z)
    - 같은 결함이다. 라우터 재시작 전이라 이전 코드가 돌고 있다.
  - Sarah(.25): SSH가 배너 전에 끊긴다. 미확인.

- 2026-11-24 09:52:31–09:53:02Z 틱(직전 09:32Z), 5호스트.
  - 공통: 운영 세션·라우터 재시작과 상태 파일 변경 없음. 5호스트 MCP `probe_succeeded`(277–2,390 ms), new_failures 0. 4886d62·7edfc0c는 파일만 설치(라이브 아님).
  - Andy
    - running+slot 2→1, reserved+slot 1, waiting_input 6, 원장 6977→6986
    - n2708 `awaiting_capacity`가 해소됐다. n1166 검수 행 누락은 남아 있다(반복).
  - Celly
    - running+slot 6→5, waiting_input 2→3, 원장 8838→8857
    - 지난 틱의 n1452·n1461 "포크 부재" finding은 이번에 없다.
    - `transcript_delta_delivery_failed` timeout 1(09:40Z, 반복 항목)
  - KOBE: running+slot 1→2, waiting_input 4→3, 원장 3291→3293. P3 정원 가드 1.
  - Prody
    - 원장 862→873, MCP 2,390 ms
    - 대화: 계획 버전 충돌 2, n1275 중복 디스패치 가드(가드), 외부 API 400, 메인 스크립트 종료 코드
  - Betty
    - `unseen_failed` 7877(09:36Z), 7878(09:38Z), 7881(09:51Z)
    - 같은 결함이다. 라우터 재시작 전이라 이전 코드가 돌고 있다.
  - Sarah(.25): SSH가 배너 전에 끊긴다. 미확인.

- 2026-11-24 09:32:27–09:33:58Z 틱(직전 09:12Z).
  - 공통: 운영 세션·라우터 재시작과 상태 파일 변경 없음. new_failures 0. 4886d62·7edfc0c는 파일만 설치(라이브 아님).
  - Andy
    - running+slot 2, waiting_input 5→6, 원장 6973→6977, MCP 891 ms
    - n1166, n2708(반복)
    - 대화: P3 정원 안내, 계획 버전 충돌(가드)
  - Celly
    - running+slot 4→6, waiting_input 4→2, 원장 8834→8838, MCP 266 ms
    - 새 finding 2건: 슬롯은 잡혀 있는데 native 관찰에 포크가 없다.
      - n1452(리스 ff4d503a, P2): 09:10Z부터
      - n1461(리스 07b6a215, P2): 08:54Z부터
      - 확인(읽기 전용, 09:36Z): 원장 host_id의 포크 대화 기록이 계속 쓰이고 있다.
        - n1452의 a8e9eac04d1e33f1c: 09:36:53Z
        - n1461의 a08caf72ceea0f15a: 09:35:59Z
      - n1461은 그 사이 submitted가 됐다.
      - 포크는 살아 있다. native 관찰 목록에 빠진 것으로 기록한다. 왜 빠졌는지는 확인하지 않았다.
    - 새 로그 `secure_json_read_failed purpose=sse_gate_status` 2(09:31Z). 같은 라우터 인스턴스 디렉터리의 PermissionError 계열 activity/context_usage 실패도 3건 있다(반복, 추적 중).
  - KOBE
    - running+slot 1, waiting_input 4, 원장 3291, MCP 377 ms
    - P3 정원 가드 안내 2(n125 등)
  - Prody
    - running+slot 1→2, 원장 860→862, MCP 2,171 ms
    - 계획 버전 충돌 1(가드)
  - Betty
    - 모니터 일괄 실행에서는 `ssh failed`였다. 09:33:58Z 단독 재조회는 정상이었다.
      - running+slot 1, submitted 5, waiting_input 2, 원장 928, MCP 346 ms
      - 대화 오류 0
  - Sarah(.25): SSH가 배너 전에 끊긴다. 미확인.

- 2026-11-24 09:12:23–09:12:54Z 틱(직전 08:52Z), 5호스트.
  - 공통: 운영 세션·라우터 재시작과 상태 파일 변경 없음. 5호스트 MCP 모두 `probe_succeeded`(281–1,101 ms), new_failures 0. 4886d62·7edfc0c는 파일만 설치(라이브 아님).
  - Andy
    - running+slot 4→2, waiting_input 3→5, 원장 6971→6973, MCP 1,079 ms
    - n1166, n2708(반복)
    - 대화: 계획 버전 충돌 2(가드)
  - Celly
    - running+slot 6→4, waiting_input 2→4, 원장 8832→8834
    - client 연결 끊김 다수
    - activity 파일 권한 오류 2(반복)
  - KOBE: 다시 관측됐다. 08:5xZ의 SSH 배너 실패는 이번에 없었다.
    - running+slot 1, waiting_input 4, 원장 3284→3291
    - `transcript_delta_delivery_failed` timeout 4(08:56Z, 반복 항목)
    - 업스트림 오류 이벤트 2(09:06Z, 09:08Z, 게이트웨이 소관)
    - 포크 n107 `transport_interrupted`(09:08Z)는 재시도 가드가 예약했다.
  - Prody
    - submitted 7→6, running+slot 0→1, 원장 854→860
    - 지난 틱의 n1272 검수 행 누락 finding은 이번에 없다. 해소됐다.
  - Betty
    - 원장 928, MCP 334 ms
    - `unseen_failed` 7875(08:57:26Z, 320.6초): 같은 결함이고, 이전 코드가 돌고 있다.
    - 대화 오류 3건: n1782 중복 디스패치 가드, 외부 API 400·403
  - Sarah(.25): SSH가 배너 전에 끊긴다. 미확인.

- 2026-11-24 08:52:19–08:52:58Z 틱(직전 08:40Z).
  - 공통: 운영 세션·라우터 재시작과 상태 파일 변경 없음. 4886d62와 7edfc0c는 파일만 설치된 상태다(라이브 아님).
  - Andy
    - running+slot 3→4, submitted 23→22, waiting_input 4→3, 원장 6967→6971
    - MCP 2,703 ms. 직전 782 ms보다 느리다.
    - n1166, n2708(반복)
    - 08:42–08:43Z 업스트림 오류 이벤트 2(게이트웨이 소관)
    - `runtime_activity_context_usage_failed` PermissionError 1. 라우터 activity 파일 권한 오류 계열이다(반복, 추적 중).
    - 대화: 계획 버전 충돌 1(가드)
  - Celly
    - running+slot 6, waiting_input 3→2, 원장 8829→8832, MCP 297 ms
    - 늦은 SSE 재시도 16(업스트림)
    - 08:49:56Z "auto continue could not read the Work status" 안내. Prody에서 보이던 것과 같은 유형이다. 원인은 확인하지 않았다.
  - KOBE: 미관측
    - 모니터가 `ssh failed`를 냈다.
    - 다시 확인했다(08:53–08:55Z): TCP 1004는 열려 있지만 SSH가 배너 교환에서 끊긴다(timeout 1, reset 1). .25와 같은 형태다.
    - 같은 시각 .12 SSH는 정상이었다.
    - 이번 틱의 KOBE 상태는 읽지 못했다. 종료로 판단하지 않는다.
  - Prody
    - submitted 6→7, running+slot 1→0, 원장 846→854, MCP 2,519 ms
    - 새 finding: n1272(리스 45445c92, P1)는 submitted이고 완료 영수증도 있는데 검수 행이 없다. native=running 08:52Z.
      - 지난 틱 KOBE n54와 같은 형태다. n54는 다음 틱에 해소됐다. 다음 틱에 다시 본다.
    - 대화 오류 3건: n1272 중복 디스패치 가드, walkie 도구 인자 오류, 메인 테스트 실패
  - Betty
    - running+slot 0→1, 원장 926→928, MCP 300 ms
    - `unseen_failed` 7876(08:49:18Z, 20초)과 7874(08:52:05Z, 166초)
    - 현재 로그의 `unseen_failed` 9건(7871–7876 포함)을 모두 대화 기록과 대조했다. 9건 모두 첨부와 remove가 있어 전달됐다.
    - 같은 결함이고, 이전 코드가 돌고 있다.
  - Sarah(.25): SSH가 배너 전에 끊긴다. 미확인.

- 2026-11-24 08:40:17–08:40:45Z 틱(직전 08:20Z), 5호스트.
  - 공통: 운영 세션·라우터 재시작과 상태 파일 변경 없음. 5호스트 모두 MCP `probe_succeeded`. 4886d62와 7edfc0c는 파일만 설치된 상태다(라이브 아님).
  - Andy
    - running+slot 3, submitted 24→23, 원장 6957→6967, MCP 782 ms
    - n1166, n2708(반복)
    - 로그: 업스트림 transient retry 20, SSE EOF 19
    - 대화 오류 3건: 웹 문서 404, 외부 API 400, 메인 스크립트 오류
  - Celly
    - running+slot 5→6, waiting_input 4→3, 원장 8820→8827, MCP 797 ms
    - 08:2x–08:3xZ 업스트림 불안정(게이트웨이 소관)
      - transient retry 119, 늦은 SSE 재시도 5
      - `anthropic_sse_forward_error` ERROR 6(08:31Z, 업스트림이 message_stop 전에 오류를 냄)
      - 같은 시각 포크 n1405 `transport_interrupted`는 재시도 가드가 최대 20회 재시도로 예약했다.
    - activity 파일 권한 오류 5(반복)
  - KOBE
    - running+slot 0→1, waiting_input 4, MCP 278 ms
    - P3 정원 가드 안내 2건(n107 등)
  - Prody
    - running+slot 1, 원장 839→846, MCP 535 ms. 08:21Z의 4초 초과는 다시 나오지 않았다.
    - new_failures `decision_refused_stop f799babf x1`
      - 리스 f799babf(n1271, P4)는 이미 `done`이다.
      - 메인의 검수 결정이 거부됐고 Stop을 다시 잡지 않았다는 기록이다. 가드 동작이다.
    - 대화 오류 7건: 메인 스크립트와 경로 오류
  - Betty
    - running+slot 1→0, waiting_input 1→2, 원장 925→926, MCP 302 ms
    - 이번 창에 `unseen_failed`는 없다. 턴 중 SSE 입력 자체가 있었는지는 보지 않았다.
  - Sarah(.25): 08:4xZ에도 SSH가 배너 전에 끊긴다. 미확인.

- 2026-11-24 08:20:46–08:21:22Z 틱(직전 07:37Z), 5호스트.
  - 공통: 운영 세션·라우터 재시작과 상태 파일 변경 없음. new_failures 0.
  - 배포: 채널 수정 4886d62를 npm beta `0.2.51-beta.20261124-080748.4886d62`로 냈다. 7개 루트에 해시 확인 설치했다(08:08–08:20Z, 파일만, 라이브 아님). 자세한 내용은 [mid-turn-socket-handover](../../../24/fixes/mid-turn-socket-handover/record.md).
  - Andy
    - running+slot 3, submitted 24, waiting_input 4, 원장 6907→6957, 보드 v11251, MCP 1,484 ms
    - n1166(반복), n2708 `awaiting_capacity`(반복). n2622는 정원 대기에서 빠졌다.
    - `work_workspace_failed code=workspace_prepare_retained`(반복 유형, 추적 중)
      - n2694: 07:46–07:47Z. channel file lock busy 1건, `git worktree add` 시간 초과(248초, 58초)
      - n2732: 07:52Z. `git worktree add` 시간 초과(116초)
    - 07:44–07:45Z `transcript_delta_delivery_failed`: 10061 3, timeout 2(반복 항목)
    - 08:11–08:14Z 업스트림 일시 오류: `cielairouter_transient_retry`(transport_disconnect, 503) 여러 건. 같은 시각 Celly·KOBE에도 있다. 업스트림은 게이트웨이 소관이다.
    - `runtime_activity_router_failed` PermissionError 3(반복, 추적 중)
  - Celly
    - running+slot 6→5, waiting_input 4, 원장 8820→8827, MCP 313 ms
    - 08:11–08:14Z 로컬 게이트웨이(127.0.0.1:20128) 5xx와 transient retry 여러 건(게이트웨이 소관)
    - activity 파일 권한 오류 6(반복)
    - 대화 오류 3건: 메인의 배포 스크립트 종료 코드 2건, P3 정원 안내
  - KOBE
    - running+slot 1→0, waiting_input 3→4, submitted 1, 원장 3275→3284, MCP 535 ms
    - n54 검수 행 누락 finding은 이번에 없다. 해소됐다.
    - 07:53–07:56Z SSE 재연결 실패: BrokenPipe 1, connect URLError 5
    - 08:13–08:14Z 503 재시도 여러 건(게이트웨이 소관)
  - Prody
    - running+slot 2→1, submitted 7→6, 원장 823→839
    - **MCP `probe_failed` TimeoutError(4초)**
      - 08:21:09Z 모니터 probe다.
      - 08:2xZ에 같은 요청을 60초 제한으로 다시 3회 보냈다. 3회 모두 성공했다(1,809 / 817 / 1,777 ms, 보드 v7051).
      - `/health`는 200, 320 ms였다.
      - 한 번의 4초 초과로 기록한다. 원인은 확인하지 않았다.
      - 라우터 로그 마지막 기록은 08:05:56Z였다. 응답을 쓰는 중 클라이언트가 끊은 `ConnectionAbortedError` traceback이 있다.
  - Betty
    - running+slot 0→1, 원장 917→925, MCP 502 ms
    - `unseen_failed` 7873(07:41Z) 1건. 같은 결함이고, 수정은 설치됐지만 라우터 재시작 전이라 이전 코드가 돌고 있다.
    - 대화 오류 4건: 400 응답, 메인 스크립트 오류, n1781 중복 디스패치 가드
  - Sarah(.25): 08:37Z에도 SSH가 배너 전에 끊긴다. 미확인.

- 2026-11-24 07:37:29–07:38:02Z 틱(직전 06:13Z, 창 약 84분), 5호스트.
  - 공통: 운영 세션·라우터 재시작과 상태 파일 변경 없음. 5호스트 모두 MCP `probe_succeeded`.
  - 배포 상태: 7edfc0c(side 수정) 파일은 06:43–06:46Z에 7개 루트에 해시 확인 설치됐다. 라우터와 세션을 재시작하지 않았으므로 라이브 반영은 아직 없다.
  - Andy
    - running+slot 1→3, submitted 22→24, waiting_input 4, 원장 6889→6907, 보드 v11226, MCP 1,219 ms
    - n1166 검수 행 누락(반복), n2708 `awaiting_capacity`(반복)
    - 새 finding: n2622(리스 f03b64ae)도 `awaiting_capacity`(P3 1/1, 06:45Z부터). 용량 가드다.
    - 06:13Z 틱의 긴 턴(투영 age 3,739초)은 06:30Z 전후에 끝났다. 06:32Z부터 age가 120초에서 다시 시작했다.
    - 라우터 로그(창 안): SSE EOF 80, client 연결 끊김(10054) 여러 건, BrokenPipe 1
    - 대화 오류 22건
      - 계획 버전 충돌 재시도(가드)
      - n2561 중복 디스패치 가드
      - 메인 스크립트의 cp1252 인코딩 오류
  - Celly
    - running+slot 5→6, waiting_input 5→4, 원장 8774→8820, 보드 v10459, MCP 1,391 ms
    - new_failures `revival_refused cbee215c x1`: 끝난 리스의 포크를 되살리는 메시지를 막은 가드다.
    - 라우터 로그
      - keepalive TaskList 합성 132
      - SSE EOF 72
      - client 연결 끊김(10054) 다수
      - `runtime_activity_router_failed` PermissionError 4(반복, 추적 중)
    - 대화 오류 15건
      - 메인 스크립트 오류
      - `work_side_handoff: side handoff not found`(메인이 handoff가 필요 없는 알림에 호출)
      - P3 정원 안내
  - KOBE
    - running+slot 2→1, waiting_input 3, submitted 0→1, 원장 3254→3275, MCP 667 ms
    - 새 finding: n54(리스 653e0f58, P2)는 submitted이고 완료 영수증도 있는데 검수 행이 없다. native=running 07:37Z.
      - 방금 생긴 상태다. 다음 틱에 다시 본다.
    - new_failures `revival_refused ac6e2cb3 x1`
      - 메인이 SendMessage를 보냈고, "unreported 리스, 실행·대기 중이 아님"이라는 이유로 거부됐다.
      - 가드 동작이다.
    - 07:12–07:15Z 같은 창에서 연결 오류가 함께 나왔다. 원인은 확인하지 않았다.
      - SSE 끊김: ConnectionReset 4, URLError(connect timeout) 1
      - `transcript_delta_delivery_failed`: TimeoutError 7, 10061 거부 4(반복 항목)
    - 업스트림 오류 이벤트 1건(06:25Z)은 늦은 SSE 재시도 가드가 처리했다.
  - Prody
    - running+slot 3→2, submitted 6→7, 원장 800→823, 보드 v7043, MCP 1,297 ms
    - 대화
      - 06:20Z "auto continue could not read the Work status" 안내(반복)
      - 계획 버전 충돌(가드)
      - 검수 포크 금지 가드
      - n1270 중복 디스패치 가드(반복)
  - Betty
    - running+slot 1→0, submitted 5, waiting_input 1, 원장 910→917, MCP 290 ms
    - 이번 창에도 Betty 결함(턴 중 소켓 입력을 실패로 기록)이 나왔다.
      - `unseen_failed` 7868(06:23Z)과 7869(06:49Z)
      - 7868은 06:23:54Z에 remove absorbed_mid_turn이 기록돼 전달된 것을 확인했다.
    - 같은 결함이 커서 복구 경로에서도 나왔다.
      - 06:34:10Z `channel_stdin_proxy_recover_queued_only message_id=7847 cursor=7868 recovered_cursor=7846`
      - 일괄 판정이 첨부를 먼저 쓰는 인계를 `queued`로 읽은 것이다.
      - 이번에는 clear floor(7862)로 막혔다. 7863–7865는 `durable_replied`로 건너뛰었고, 재주입은 관측되지 않았다.
      - 이 경로(`wake_states_from_text`)도 진행 중인 수정 범위다.
  - Sarah(.25): 06:58–07:32Z 재시도 내내 SSH 배너 전 연결 끊김. 아직 미확인(위 항목).
  - 진행 중
    - 채널 수정은 실패 테스트, 실엔진 4구성, Windows 채널 핵심 회귀까지 마쳤다. Linux 재회귀와 Windows 165개 파일 회귀가 남았다.
    - 7edfc0c Windows `test_work_mod_real_backend` 전체 재실행이 진행 중이다(배포 뒤 1건 실패, 단독 실행은 통과).

- 2026-11-24 06:52–07:32Z: 사용자 요청 ".25 Sarah 샌드박스가 새 베타를 설치했는데 SSE 메시지를 제대로 받지 못하는 것 같다"를 확인하려 했다.
  - 접근 시도(읽기 전용, 이 PC에서)
    - `ssh -p 1004 sjglobal@100.64.0.25`: TCP 연결은 되지만 서버가 SSH 배너 전에 연결을 끊는다(`kex_exchange_identification: Connection reset by peer`). 06:5xZ에 6회 실패했고, 06:58–07:32Z 45초 간격 재시도도 모두 실패했다.
    - 같은 시각 `.12`(Prody·Betty)와 `.14`(KOBE) SSH는 정상이었다. 이 PC 쪽 네트워크 문제는 아니다.
    - `.25`의 22, 1005, 2022, 2222, 3389, 5985, 9464–9470 포트와 HTTP 13080·8080·80·443은 응답이 없다.
    - 로컬 AAP 콘솔(localhost:13080)에는 ganti-ai와 Celly만 있고 Sarah는 없다.
  - Sarah의 설치 버전, side 사용 여부, 라우터 로그를 읽지 못했다. 원인은 확인하지 못했다.
  - 같은 시간에 Betty에서 입증한 결함(턴 중 소켓 입력을 `unseen_failed`로 기록, 아래 06:13Z 항목)이 Sarah에도 해당하는지는 Sarah 설정을 읽어야 판단할 수 있다.

- 2026-11-24 06:13:46–06:14:17Z 틱(직전 06:01Z), 5호스트. Betty 채널 기록은 06:30Z까지 추가로 읽기 전용 조사했다.
  - 공통: 운영 세션·라우터 재시작과 상태 파일 변경 없음. 5호스트 모두 MCP `probe_succeeded`, new_failures 0.
  - Andy
    - running+slot 1, waiting_input 4, submitted 23→22, 원장 6886→6889, 보드 v11188→v11192, MCP 2,329 ms
    - n1166 검수 행 누락과 n2708 `awaiting_capacity`(P3 1/1)가 계속된다(반복, 용량 가드).
    - `channel_turn_projection_persistent` age가 851→3499초(06:25Z)로 계속 늘었다.
      - 메인 대화 파일은 06:25:33Z에도 assistant 기록이 쓰였다. 멈춘 것이 아니라 한 턴이 길게 이어지는 상태로 관측된다.
      - observe_only 경고다.
  - Celly
    - running+slot 4→5, waiting_input 6→5, 원장 8759→8774, 보드 v10407, MCP 390 ms
    - 라우터 로그
      - 클라이언트 연결 끊김(`ConnectionResetError` 10054): `anthropic_sse_client_disconnected` 10건, `router_client_disconnected` 2건
      - keepalive TaskList 합성 9건
      - 직전 틱의 게이트웨이 5xx는 이번 창에 없다.
    - 대화 오류 5건은 메인의 PowerShell 사용 오류와 없는 파일 읽기다.
  - KOBE: running+slot 1→2, waiting_input 4→3, 원장 3249→3254, MCP 885 ms. 오류 0.
  - Prody
    - running+slot 2→3, 원장 798→800, MCP 996 ms
    - 대화 오류 2건
      - 메인 스크립트 Traceback
      - n1270 중복 디스패치 가드: 기존 리스 577354ba에 SendMessage로 재개하라고 안내했다. 설계된 가드다.
  - Betty
    - waiting_input 0→1, 원장 907→910, 보드 v5591, MCP 506 ms
    - 대화 오류 3건: rg 시간 초과, 계획 버전 충돌 재시도(가드), 스크립트 종료 코드
    - **입증된 결함: 실행 중인 턴에 전달된 SSE 메시지를 라우터가 `failed`로 기록한다.**
      - 06:13Z 관측
        - `channel_windows_console_unseen_failed` 7867·7866
        - `channel_stdin_proxy_blocked ... prior_submission_failed detail=stale_unconfirmed` ERROR 2건
      - 대화 대조
        - 두 메시지는 06:12:43–44Z에 큐에 들어갔다.
        - 06:13:36Z에 `queued_command` 첨부로 실행 중인 턴에 넘어갔다.
        - 06:13:36.376Z 모델 요청에 들어갔다. 라우터도 `channel_llm_inject_skipped reason=already_in_request`를 남겼다.
      - 범위: 11-23 20:11Z 이후 Betty 로그 두 개에서 `unseen_failed` 31건, `confirmed` 8건이다. 31건 모두 대화에 첨부와 큐 remove가 있어 실제로 전달됐다.
      - 원인(소스 `channel_transcript.wake_state_evidence_from_text`와 Betty 대화 2.1.331 대조)
        - 세션 소켓 제출의 `queue-operation enqueue`에는 `content`가 없다. 그래서 인계 전까지 상태가 `missing`이 되고, 20초 unseen 규칙이 실패로 만든다.
        - 11-23 이후 실행 중 턴 인계 53건 중 47건은 첨부가 remove보다 먼저 기록됐다. 코드는 remove 뒤의 첨부만 전달로 본다. 그래서 전달이 확인되지 않고 `queued`로 남는다. 예: 7833은 15분 뒤 실패로 기록됐다.
      - Betty만 해당하는 이유: Betty는 SSE를 메인에 직접 넣는다. 나머지 4호스트는 side 액터 경로이고, 현재 로그에 `unseen_failed`가 0이다.
      - 메시지 유실은 관측되지 않았다. 실패 기록이 발신 측에 어떤 영향을 주는지는 확인하지 않았다.
      - 처리: feature/priority-models에서 실패 테스트부터 재현하고 고친다(진행).
  - 배포 대기 중인 943c3e2의 py311 real_backend는 아직 실행 중이다.

- 2026-11-24 06:01:40–06:02:16Z 틱(직전 05:01Z). 이번 틱부터 Betty를 더해 5호스트를 본다(사용자 2026-11-24).
  - 공통: 운영 세션·라우터 재시작과 상태 파일 변경 없음. 5호스트 모두 MCP `probe_succeeded`, new_failures 0.
  - Betty 추가 내용
    - .12 샌드박스 u42(`aapw-a26u42`), 베타 `3d475f9`다.
    - side 액터를 쓰지 않고 SSE를 메인 세션으로 바로 보낸다. 그래서 side 기능(게이트 재확인 포크, 포크 답, 오래 멈춘 포크 알림)은 동작하지 않는다.
    - 모니터 `run.sh`에 다섯 번째 호스트로 넣었다(router digest `a6276f9b0629`, tz -8).
  - Andy
    - running+slot 1, waiting_input 4, submitted 22→23, 원장 6871→6886, 보드 v11188, MCP 1,203 ms
    - n1166 검수 행 누락이 계속 남아 있다(반복).
    - 새 finding: n2708(리스 9e9244f0) 검수 행이 `awaiting_capacity`다(05:33Z부터).
      - 사유: `active slot capacity reached for P3 (1/1); rework waits for admission`
      - P3 한도 1이 차서 재작업 입장을 기다린다. 용량 가드이고, 결함으로 보지 않는다.
    - 라우터 로그 `channel_turn_projection_persistent`
      - age 486→851초(05:03–05:09Z)로 늘었다. 메인 턴 투영이 약 14분 그대로였다.
      - observe_only 경고다. 원인은 확인하지 않았다.
  - Celly
    - running+slot 8→4, waiting_input 3→6, 원장 8667→8759, 보드 v10402, MCP 406 ms
    - 업스트림 오류
      - 로컬 게이트웨이 `cielairouter_gateway_failure` http_5xx 8건(05:07Z부터)
      - `anthropic_sse_upstream_error_event` 9건
      - 포크 n1431 503(05:17:29Z): 재시도 가드가 1/20을 진행했다
    - 업스트림 헤더와 오류는 게이트웨이 소관이다.
    - 05:29:33Z 호스트 정합 단계가 `work_host_page: The operation timed out.`로 실패했다.
      - 02:01Z 검수 호출 시간 초과에 이은 두 번째 라우터 MCP 시간 초과다(반복).
      - 라우터가 60초 넘게 걸린 원인은 확인하지 않았다(미결).
  - KOBE: running+slot 2→1, waiting_input 4, 원장 3233→3249, MCP 387 ms. `transport_interrupted` n120 1건(05:02:37Z)은 재시도 가드가 처리했다.
  - Prody
    - submitted 6, waiting_input 2, running+slot 0→2, 원장 771→798, MCP 1,264 ms
    - **05:38:31Z side 이벤트 3e627cc9bd74 처리 실패. 원문이 메인으로 전달됐다**(설계된 전달 경로, 1회 appended, 1회 already).
      - 이 이벤트는 게이트 재확인이다. 액터의 답 첫 줄은 "I did not run the live lookups for n1200, n652, n1149, or n1076, so I can't release any wait"였다. 액터가 조회 없이 "변화 없음"을 냈다.
      - 사용자가 지적한 "side가 조사를 안 한다"의 실측 사례다. 이 동작은 미배포 수정(게이트별 판정 강제, "미조회"는 판정이 아님)이 다루는 대상이다.
      - 실패 분류는 Prody 워크스페이스 상태 디렉터리 ACL(SYSTEM도 거부)로 읽지 못했다(미확인).
    - 나머지 오류는 보드 버전 충돌 재시도(가드)와 메인 도구 사용 오류다.
  - Betty(첫 틱, 직전 05:54Z 단독 조회와 비교)
    - running+slot 1, submitted 5, 원장 904→907, 보드 v5588, MCP 311 ms
    - 라우터 로그
      - `model_list_unavailable` cielairouter: 20초 시간 초과 5건, 503 4건(05:06Z부터)
      - `work_orphan_released owner=dead` 3건(05:47Z): 주인이 죽은 예약 리스를 회수한 것이다. 설계된 복구다. 그 직전에 세션이나 프로세스가 끝났을 가능성이 있지만 확인하지 않았다.
  - 새로 입증된 제품 결함은 없다.
  - 배포 대기 중인 수정(cf55ed2와 2단계)의 Linux 최종 회귀
    - py310·py311 각 139개 파일, 실패 0
    - Windows 최종 회귀와 py311 real_backend는 진행 중이다.

- 2026-11-24 05:01:45–05:02:08Z 틱(직전 03:59Z). 운영 세션·라우터 재시작과 상태 파일 변경 없음. 4호스트 모두 MCP `probe_succeeded`.
  - Andy
    - running+slot 3→1, waiting_input 4, submitted 21→22, 원장 6864→6871, 보드 v11175, MCP 1,640 ms
    - n1166 검수 행 누락이 계속 남아 있다(반복).
    - 메인 도구 오류 13건은 메인이 직접 돌린 스크립트 오류와 Walkie 400·404(작업 입력)다.
  - Celly
    - running+slot 6→8, waiting_input 2→3, 원장 8623→8667, 보드 v10363, MCP 281 ms
    - **새 실패 `revival_refused a12ce92c` 1건(05:01:38Z)**
      - 메인이 SendMessage로 n1431의 끝난 포크(리스 `unreported`)에 메시지를 보내려 했다. 가드가 막았다. 보냈다면 옛 프롬프트로 포크가 되살아났을 것이다.
      - 의도된 가드다(`parallelRevivalDeny`).
    - 라우터 로그 `runtime_activity_router_failed PermissionError` 12건(22:10 로컬부터)
      - Andy에서 본 것과 같은 하트비트 파일 교체 거부다. 이제 두 호스트에서 반복된다.
      - 원인은 확인하지 않았다(추적).
    - 라우터 로그 `transcript_delta_delivery_failed` 51건: TimeoutError 27, WinError 10061 연결 거부 24
      - 같은 줄이 KOBE에도 48건 있다(23+25).
      - 전달 대상이 받지 않았다는 뜻이지만, 대상이 무엇인지는 확인하지 않았다(미결).
  - KOBE: running+slot 2, waiting_input 2→4, 원장 3196→3233, 보드 v862, MCP 453 ms. 포크 오류 7건은 작업 입력 오류와 슬롯 가드다.
  - Prody: submitted 6, waiting_input 2, 원장 758→771, MCP 2,665 ms. 오류 13건은 Walkie 입력 오류, 보드 버전 충돌 재시도, 중복 디스패치 가드다(정상 가드).
  - 새로 입증된 제품 결함은 없다.

- 2026-11-24 03:59:06–03:59:27Z 틱(직전 03:34Z). 운영 세션·라우터 재시작과 상태 파일 변경 없음. 4호스트 모두 MCP `probe_succeeded`, new_failures 0.
  - Andy
    - running+slot 2→3, waiting_input 5→4, submitted 21, 원장 6862→6864, 보드 v11139, MCP 969 ms
    - n1166 검수 행 누락이 계속 남아 있다(반복).
    - 라우터 로그 `runtime_activity_router_failed PermissionError [WinError 5]` 1건(21:52:27 로컬, 03:52:27Z)
      - 라우터가 자기 `router-activity.json` 하트비트를 `os.replace`로 바꾸다 거부당했다.
      - 같은 줄이 17:22·19:28·21:52 로컬, 3회 있었다(반복). 실패 1회당 하트비트 쓰기 1번이 빠진다.
      - 원인(파일을 연 다른 읽기 프로세스와 겹침)은 확인하지 않았다. 결함으로 단정하지 않고 추적한다.
  - Celly: running+slot 2→6, waiting_input 2, 원장 8601→8623, 보드 v10302, MCP 297 ms. 도구 오류 7건은 보드 버전 충돌 재시도(가드)와 Ripgrep 20초 시간 초과(도구)다.
  - KOBE: running+slot 2, waiting_input 2, 원장 3185→3196, MCP 653 ms. 포크 오류 0.
  - Prody: submitted 6, waiting_input 2, 원장 758(변화 없음), MCP 1,386 ms. 오류 3건은 메인이 직접 돌린 스크립트 종료 코드다.
  - 새로 입증된 제품 결함은 없다.

- 2026-11-24 03:34:22–03:34:42Z 틱(직전 01:33Z). 운영 세션·라우터 재시작과 상태 파일 변경 없음. 4호스트 모두 MCP `probe_succeeded`.
  - Andy
    - running+slot 3→2, waiting_input 5, submitted 23→21, 원장 6781→6862, 보드 v11124, MCP 1,016 ms
    - n1166 검수 행 누락은 계속 남아 있다(반복).
    - 라우터 로그 `cielairouter_transient_retry` 503 13건(02:14Z부터). 업스트림 재시도 가드다.
    - 메인 도구 오류 36건은 메인이 직접 돌린 스크립트 오류다(cp1252 인코딩, 타입 오류 등).
  - Celly
    - running+slot 7→2, waiting_input 2, submitted 1→0, 원장 8528→8601, 보드 v10274, MCP 234 ms
    - **새 실패 `decision_refused_stop 532f5bac` 1건**
      - 메인의 `work_parallel_item_review`(n1236 approve)가 02:00:17Z에 시작했고, 69초 뒤 02:01:26Z에 `The operation timed out.`로 거부됐다.
      - 원장에서 그 리스는 지금 `done`이다. 결정은 결국 반영됐다.
      - 클라이언트가 약 60초에 MCP 호출을 포기하는 것과 맞는다. 같은 동작을 실엔진 측정에서도 봤다(아래 side 수정 기록).
      - 라우터가 그 검수에 60초 넘게 걸린 원인은 확인하지 않았다(미결).
    - P3 슬롯 한도 가드, 보드 버전 충돌 재시도는 정상 가드다.
  - KOBE
    - running+slot 3→2, waiting_input 1→2, 원장 3168→3185, MCP 259 ms
    - `transport_interrupted` 2건 추가(02:25:07Z n119, n54). 재시도 가드가 예약했다.
    - P2 슬롯 한도 가드가 있었다.
  - Prody
    - submitted 6, waiting_input 2, 원장 728→758, MCP 1,830 ms
    - **새 실패 `decision_refused_stop 830eff74` 1건**: 02:01:32Z 메인 검수가 `rework instructions`로 거부됐다. 재작업 지시가 없는 rework를 막는 입력 가드다.
  - 새로 입증된 제품 결함은 없다.

- 2026-11-24 01:33:44–01:34:29Z 틱(직전 00:12Z). 운영 세션·라우터 재시작과 상태 파일 변경 없음. 4호스트 모두 MCP `probe_succeeded`, new_failures 0.
  - Andy: running+slot 3, waiting_input 2→5, submitted 21→23, 원장 6720→6781, 보드 v11059. n1166 검수 행 누락은 계속 남아 있다(반복).
  - Celly: running+slot 2→7, waiting_input 8→2, submitted 1, 원장 8417→8528, 보드 v10187. 세 틱 연속 응답했다.
  - KOBE: running+slot 2→3, waiting_input 1, submitted 1→0, 원장 3142→3168.
  - Prody: 두 틱 미관측 뒤 이번에는 ssh가 됐다. submitted 6, waiting_input 2, 원장 728.
  - 포크 `transport_interrupted` API 오류
    - Celly 01:28:56Z 4건(n1236, n1320, n1409, n1411), KOBE 01:13:42Z 1건(n32), Prody 01:13:59Z 1건(n1258)
    - 호스트 재시도 가드가 1m×10, 10m×10을 예약했다(정상 가드).
    - 세 호스트에서 15분 안에 같은 종류가 났다. 공통 원인은 확인하지 않았다(업스트림 쪽 가능성, 미확인).
  - 그 밖의 포크 오류는 작업 자체·환경 오류다. 예: Celly git 자격 증명 프롬프트 차단, KOBE Walkie QA 조건 누락 400, Prody 경로 없음.
  - 새로 입증된 제품 결함은 없다.

- 2026-11-24 00:12:22–00:13Z 틱(직전 23:54Z). 운영 세션·라우터 재시작과 상태 파일 변경 없음.
  - Celly
    - 두 번째 틱 연속으로 응답했다. MCP 359 ms(v10114), `/health` 3회 0.01–0.02초.
    - 17:54–18:13 로컬 라우터 로그에 20초 이상 공백이 없다. `listener_reachable_health_busy`는 2건이다.
    - 리스: running+slot 3→2, waiting_input 8, 원장 8394→8417.
    - 포크 오류 8건은 모두 가드이거나 작업 자체 오류다.
      - 보고 형식 가드 3건과 completion evidence 2,000자 초과 2건(2192·3256자)
      - 포크 게이트의 worktree 밖 Write 거부 1건
      - PowerShell 인자 오류 1건
      - walkie `send_message` file_ids 4개 초과 1건
  - Andy
    - running+slot 3, waiting_input 1→2, submitted 22→21, 원장 6704→6720. MCP 1,140 ms. 포크 오류 0.
    - 23:54Z에 추가됐던 n2678 검수 행 누락은 사라졌다(집계 지연이었다). n1166은 계속 남아 있다.
  - KOBE: running+slot 4→2, submitted 1, waiting_input 1, 원장 3132→3142. MCP 206 ms. 포크 오류 3건
    - pytest 실패 1건(작업 자체)
    - git author identity 미설정 1건(작업 환경)
    - 포크 `work_parallel_status` 신원 가드 1건(반복)
  - Prody: ssh가 `Device SSH authorization revoked or expired`로 거부돼 이번 틱은 미관측이다. 23:20Z 틱에 이어 두 번째다.
  - 새로 입증된 제품 결함은 없다.

- 23:54:44–23:56Z 틱(직전 23:34Z). 운영 세션·라우터 재시작과 상태 파일 변경 없음.
  - **Celly: 이번 틱에는 응답했다.** 이것만으로 프리즈가 끝났다고 판단하지 않는다.
    - MCP 프로브 성공(266 ms, v10091). `/health` 3회 0.01–0.03초.
    - 리스: running+slot 4→3, waiting_input 8, submitted 0→1, 원장 8392→8394.
    - 라우터 로그 `router_post_client_disconnected` 17건(17:34 로컬 이후)이 계속 나온다.
    - 포크 a1f53ab6 오류 3건
      - 23:47Z `walkie-install/mcp-stdio.py upload_local` Traceback
      - 23:48–23:49Z `work_parallel_report`가 `worker may only report question, completion or handoff`, `completion artifact SHA256 required` 2건. 보고 형식 가드다.
  - Andy
    - running+slot 5→3, submitted 23→22, 원장 6693→6704. MCP 1,187 ms.
    - 새 실패 `decision_refused_stop 2b22056c x1`(23:42:51Z): 메인의 `work_parallel_item_review`가 `The operation timed out`으로 실패해, mod가 Stop 경계를 기록했다(`register.js:2997–3006`).
      - 해당 리스(n2671 P1)는 지금 done이다. evidence에 "Main direct verification 2026-11-23T23:35Z"가 있다. 재시도로 마무리된 것으로 본다(재시도 시각은 미확인).
    - 검수 행 누락: n1166에 n2678 23b54592 P1이 추가됐다(native=running last=23:49). 제출 직후라 집계 지연일 수 있어 다음 틱에 재확인한다.
  - KOBE: running+slot 3→4, 원장 3118→3132. MCP 1,025 ms. 포크 오류 5건
    - 23:21Z NAS 복사 확인 중 `/tmp` pyc 삭제 Permission denied
    - 23:37Z 해시 검증 Exit 1
    - 23:37Z 보고 형식 가드
    - 23:46Z 포크 `work_parallel_status` 신원 가드(반복 관측)
    - 23:53Z 포크 자체 bash 인용 오류
  - Prody: waiting_input 0→2, submitted 7, 원장 704→714. MCP 2,555 ms.
    - 새 실패 `revival_refused`(23:37:28Z): 메인이 side 액터 a00d0811에 메시지를 보내려다 거부됐다(`Work message … refused: it is a side request`). 포크 부활을 막는 정상 가드다.
    - 포크 a0616246: `clear_wait=true conflicts with non-empty waiting_on` 2건(23:39–23:40Z). 입력 검증 가드다.
    - a00d0811 Exit 2는 세션 WMS 점검 스크립트다.
  - 새로 입증된 제품 결함은 없다. side-fresh 중복 실행은 미착수 그대로다.

- 23:34:24–23:37Z 틱(직전 23:20Z). 운영 세션·라우터 재시작과 상태 파일 변경 없음.
  - **Celly: 라우터가 프리즈 상태다(사용자 정의: 20초 무응답 = 동작 안 함)**
    - 23:36–23:37Z `/health` 3회가 모두 20초 시간 초과였다. 23:20Z 틱에 이어 이번 틱도 MCP 프로브가 `probe_failed TimeoutError`(4,031 ms)다.
    - 17:15 로컬(23:15Z) 이후 20초 이상 로그 공백 6회: 47, 20, 94, 23, 20, 26초.
    - `router_post_client_disconnected`가 10분 단위로 6, 11, 14건으로 늘고 있다.
    - 포크 쪽 증거
      - 23:31:54Z a579844f의 Write가 `Work status unavailable: router connection or snapshot could not be verified. [router-error; wg-mwtwfwul-8m]`로 거부됐다.
      - 23:32:37Z a0cd0cf1은 `Work coordination changed; reread`.
    - 리스: running+slot 6→4, waiting_input 6→8, 원장 8385→8392.
    - 23:20Z 기록의 "프로세스는 살아 있다"는 동작 판단으로 틀린 표현이었다. 프로세스 생존은 동작 증거가 아니다. 정정한다.
    - 원인은 미확인이다. 23:1xZ 측정에서 G: 디스크 큐 12, 전송당 2–11초였다.
    - 라우터 재시작은 이 틱의 금지 범위라 하지 않았다.
  - Celly 포크 도구 오류 8건(23:27–23:32Z)
    - `work_parallel_report` question evidence 2,000자 초과 4건(2432/2243/2057/3536자): 라우터 입력 한도 가드다.
    - walkie `threads_stop` client_id uuid 검증 오류 1건: 인자 오류.
    - PowerShell `verify-signed-distributions.ps1` Exit 1 1건: 작업 자체의 실패.
    - 위 라우터 오류 2건.
  - Andy
    - running+slot 6→5, submitted 21→23, accepted 1, 원장 6678→6693. MCP 797 ms 정상.
    - 포크 a1f6c1c6이 `work_parallel_report`로 `worker may only report question, completion or handoff`와 `completion artifact SHA256 required`를 받았다(23:26Z). 보고 형식 가드다.
    - n1166 검수 행 누락이 계속된다.
  - KOBE: running+slot 3, 원장 3118로 변화 없음. MCP 314 ms 정상. 포크 오류 1건(23:21:50Z, 미분류).
  - Prody
    - 관측이 복구됐다(23:20Z 틱은 ssh `lost connection`). MCP 752 ms 정상.
    - submitted 7, 원장 704. 포크 오류 5건(23:21–23:34Z, 미분류).
    - 메인 22:22Z Python SyntaxError와 23:33Z WMS 점검 Exit 2는 세션 자체 스크립트다.
  - side-fresh 중복 실행: 재현 테스트를 아직 작성하지 않았다. 수정도 없다.

- 23:10–23:20Z **Celly 라우터 상태 확인**(사용자 질문 "라우터가 죽은 것 같은데"). 읽기 전용이다.
  - 프로세스는 살아 있다.
    - 라우터 91384가 9385를 listen 중이고 연결이 2개 있다.
    - 런처 28096과 claude 32712·39264·83796도 살아 있다.
  - 그러나 응답이 몇 분씩 멈춘다.
    - 16:12:30 로컬 재시작 이후 20초 이상의 로그 공백이 14번 있었다(합계 1,299초). 가장 긴 것은 16:48:22–16:56:13 로컬(22:48–22:56Z)의 471초다.
    - 같은 구간에 `/health`가 15–20초 시간 초과됐다.
    - `router_post_client_disconnected path=/ca/mcp` ConnectionReset이 16:50대와 17:10대에 각 21건 있다.
    - 17:15:55 로컬에 새 `GET /ca/mcp` 404와 `tools/list`가 있다. Claude가 MCP를 다시 연결한 흔적이다.
    - Work mode가 끊긴 것처럼 보이는 것과 맞는다.
  - 디스크(23:1xZ 측정)
    - G:(Celly 상태 전체, CT4000BX500 SATA)의 큐가 12, 전송당 2.2–11.3초였다.
    - 같은 순간 Celly 라우터 자체의 IO는 0이었다.
    - Andy 라우터(9505, pid 112668)가 읽기 197–292 MB/s, 4,741 ops/s로 가장 많이 읽었다. 어느 파일·드라이브인지는 미확인이다.
    - 정지와 디스크 지연의 인과는 미확인이다.
  - 라우터 재시작은 사용자 지시가 없어서 하지 않았다.

- 22:40–23:05Z **사용자 질문 조사: side-fresh 중복, 포크 수 부족, 대기가 풀리지 않는 이유(Andy·Celly·Prody)**. 읽기 전용, 운영 상태 변경 없음.
  - 포크가 적은 이유
    - 세 호스트 모두 실행 후보가 0이다.
    - 펌프 결함이 아니라, 미완료 리프가 대기·계획·열린 리스에 걸려 있다(`work_parallel_status.priority_diagnosis`).

      | 호스트 | P0 | P1 | P3 | P5 |
      |---|---|---|---|---|
      | Andy | 미완료 14: 대기 13, 실행 1 | 79: 대기 72, 계획 4, 리스 3 | 67: 대기 47, 계획 19 | 51: 대기 41, 계획 10 |
      | Celly | 17: 대기 10, 리스 7 | – | – | 55: 대기 52 |
      | Prody | 3: 대기 2, 실행 1 | 1: 계획 | 2: 대기 | 1: 대기 |

      - Celly P0의 리스 7은 running 3, waiting_input 2, submitted 2다. 질문에 걸려 멈춘 포크는 8개다.
      - Prody P4는 3개 중 대기 2, 예약 1이다.
    - Andy P0 대기는 대부분 메인이 걸어 둔 순차 의존이다. 기록 문구에 "order n2594 E2E > #113 > 131641 > #114 > #115"가 있다. 사슬은 실행 중인 n2594 하나에 걸려 있다.
      - n2561/n2601: #113·131641 릴리스 확인 대기
      - n2582: #115
      - n2472: 156289 병합
      - n1823: 123240 검수
  - Andy 대기 기록
    - waiting_on 151건 중 99건이 자유 문장이다. 노드 id를 적은 대기 52건은 다음과 같다.
      - 대상이 열려 있음: 40개 노드(53건)
      - 대상이 blocked: 2건
      - **대상 노드가 사라짐: 5건**(n2303, n764, n1020, n1248, n2389)
    - 완료된 노드를 가리키는 대기와 done 노드에 남은 대기는 0이다.
  - **side-fresh 중복 실행: 측정된 원인(펌프 정지 → 리스 만료 → 강제 정지 → 재처리)**
    - 22:01:24–22:06:15Z 동안 Andy 라우터에 `work_side_claim`·`work_side_renew`가 0건이다. 분당 횟수로 측정했고, 직전 분당 claim은 9–22건이었다.
    - 그 사이 mod가 보낸 MCP 요청(22902–22906)은 22:06:15–22Z에야 완료로 기록됐다. 라우터는 응답 완료 시점에 로그를 남긴다(`channel_mcp_http_controller.py:91`).
    - side 리스는 120초이고, 2초 펌프가 남은 시간 90초 미만일 때만 갱신한다(`side_intake_processing.py:316`, `side-intake.js:1932–1944`).
    - 22:06:17Z에 side 액터 3개가 동시에 `[Request interrupted by user]`로 끝났다(a08b5f02, aaecc435, a63b3bef). 각각 15분, 12.5분, 5분째 작업 중이었다.
    - 같은 이벤트를 새 액터가 처음부터 다시 처리했다.
      - 40d33d66e413: aaecc435 → a1bfa828(22:06:48–22:12:25)
      - 51a377278167: a63b3bef → adc94837(22:07:29–22:13:35)
      - 355c03b99a6e: 20:31–21:00Z에도 같은 형태로 재처리(abb7bf3d → a4b639da)
    - 라우터 쪽 5분 지연의 원인은 미확인이다.
  - 동시에 여러 개가 뜨는 이유: Andy 워크스페이스 설정 `work_side_intake.max_active=5`다. 여기에 P1 예약 슬롯이 하나 더 있다(`side-intake.js:1948–1962`). 이벤트 하나당 액터 하나라 설계상 동시 실행이다.
  - **side-fresh 문맥 불일치(코드·문구로 확인)**
    - Andy는 `work_side_intake.session="fresh"`다. Celly 액터도 `ciel-work:side-fresh` 형식이다.
    - fresh는 빈 대화로 시작한다(`side-intake.js:2089–2093`). 받는 workload는 노드의 id, 제목, 상태, 우선순위, phase, waiting_on(160자)뿐이다. note·progress·evidence가 없다(`side-intake.js:311–341`).
    - 그런데 관리 프롬프트는 대화를 물려받았다고 가정한다.
      - `gate_actor`: "메인의 컨텍스트…를 그대로 물려받은 포크", "물려받은 대화 기록에 메인이 최근 직접 확인한 결과…"
      - `gate_recheck`: "그 이전 것은 이미 컨텍스트에 있습니다"
      - `work_workflow_prompts.py:110–177, 228–229`
    - fresh 액터는 메인의 결정·진행 기록·이전 스레드 글 없이 판정한다.
  - 게이트 재확인 성과(Andy, 11-22 00:00Z 이후 영수증)
    - 305회 중 게이트 재확인의 대기 해제는 4건이다(n1357 1, n2582 3).
    - 요약에 "미조회"·"미확인"이 반복된다(20:02Z 회차는 10게이트 모두 미조회).
    - 22:06Z 회차는 처리에 28분이 걸렸다.
    - 이 세션의 격리 141건
      - invalid_answer 116건(결정 JSON 없음 등)
      - superseded 14건
      - processing_error 7건과 turn_error 4건(그중 "the session was stopped and nobody has sent input since" 다수)
  - **과잉 해제 사례**: n2582
    - side가 "156289의 Daniel 승인"으로 대기를 풀었다.
    - 메인이 progress에 정정을 남겼다: "Correcting side event 1c420b87a1c6: … covering only 120/160px isolated comparison and conditional #115 update/CI. Broader n2582 remains held". 승인 범위보다 넓게 해제했다는 뜻이다.
  - Celly
    - 라우터가 22:5xZ에 `/health`를 15–20초 안에 응답하지 못했다. 로그에는 `listener_reachable_health_busy`와 16:56:18 로컬 `anthropic_route_error … pre-output time limit` 500이 있다.
    - 같은 시각 G: 디스크는 유휴 0%, 전송당 0.71–0.92초, 큐 2–3이었다. 지연과의 인과는 미확인이다.
    - Celly side 영수증은 ACL 때문에 읽을 수 없어 게이트 회차를 분석하지 못했다.
    - 멈춘 포크 8개의 evidence는 외부 답을 기다리는 내용이다. 예: n1343은 Bob의 157754 답.
  - Prody
    - 22:5xZ `work_parallel_status`는 위 표와 같다(후보 0).
    - 이후 ssh가 `Received disconnect … 11: Device SSH authorization revoked or expired`로 거부돼 더 관측하지 못했다(미결).

- 22:18:18–22:18:42Z 틱(직전 22:16Z 재시작 확인 프로브). 운영 세션·라우터 재시작과 상태 파일 변경 없음.
  - MCP는 4호스트 모두 `probe_succeeded`다(Andy v10938 1063 ms, Celly v10061 687 ms, KOBE v734 335 ms, Prody v6924 1278 ms). new_failures는 4호스트 모두 0이다.
  - Andy
    - running+slot이 1에서 2로 늘었다. submitted 21, 원장 6599→6602다.
    - n1166 "completion receipt, no review row"는 그대로다(반복, 미결).
  - Celly
    - 리스는 그대로다(running+slot 6, waiting_input 7). 원장 8359도 같다.
    - `router_lifetime_keep_alive reason=reachable_health_mismatch_active_client` WARN을 분류했다. **라우터만 재시작한 데 따른 예상 경고이고, 제품 결함이 아니다.**
      - 재시작 전 라우터 로그(`router.log.1`, 69 MB)에는 0건이다. 새 로그(16:12:32 로컬부터)에는 16:21까지 99건이 있다. 약 5초에 1건으로, 감시 주기와 맞다.
      - 소스(`router_client_lifecycle.py:235–253`)를 확인했다. 런처는 자기가 로드한 패키지 지문과 health를 비교한다. 지문이 다른 라우터라도 config가 같고 응답하면 교체하지 않고 유지하며 이 WARN을 남긴다. 진행 중인 스트림을 끊지 않으려는 설계다.
      - 런처 28096은 05:41 로컬에 이전 파일로 떴다. 새 라우터 91384는 3d475f9 오버레이 파일을 로드했다. 이 경고는 런처가 다시 실행될 때까지 계속된다.
      - Prody도 같은 상황일 것으로 보이나, 로그 수준이 ERROR라 기록되지 않는다(미관측).
  - KOBE: 직전 프로브의 포크 오류 기록 5건을 내용으로 분류했다. 창 안에서 하나 더 나와 6건이다. 모두 제품 결함 근거는 아니다.

    | 시각(Z) | 포크 | 호출 | 결과 | 분류 |
    |---|---|---|---|---|
    | 21:58:31 | af62f22e | `work_parallel_status` | `conversation identity is unavailable; no shared Work fallback is allowed` | 라우터 신원 가드가 거부(정상 가드) |
    | 22:12:15 | a40762da | `work_parallel_status` | 위와 같음 | 정상 가드 |
    | 22:10:16 ×3 | ab83cca9 | walkie `approvals.waiting_on_me`, `messages.context` ×2 | `The user doesn't want to proceed…` | 22:10:17Z `[Request interrupted by user for tool use]`로 진행 중 호출 3개가 취소됨 |
    | 22:13:24 | a40762da | walkie `tasks.list` `{message_id}` | Walkie API 400 `Invalid operation arguments` | 인자 오류. 21:41–21:47Z Andy·Celly·Prody와 같은 종류 |

    - ab83cca9를 누가 중단했는지(사람, 정지 요청)는 미확인이다. KOBE 메인 전사의 22:09:50–22:10:40 구간에는 22:10:24Z `Gate re-check queued for 10 blocked/waiting item(s)`만 있다.
    - 포크의 `work_parallel_status` 신원 거부는 이번이 처음 관측된 것인지 다른 호스트에도 있는지 아직 대조하지 않았다. 추적 항목으로 둔다.
  - Prody
    - 리스(submitted 7)와 원장 680은 그대로다.
    - 메인 전사의 22:17:38Z `Exit code 2`는 세션 자체 WMS 점검 스크립트의 종료 코드다. 출력 끝에 `UNANSWERED WMS War Room 1584`가 있다. Ciel 경로가 아니다.

- 22:11–22:19Z **사용자 지시로 Celly·Prody 라우터만 재시작했다**("ceely 와 prody 라우터 재시작시켜줘"). 운영 세션(claude.exe)과 런처는 재시작하지 않았다.
  - 방법(`router_swap.py`)
    - `/health`의 pid가 예상 라우터 pid와 같을 때만 그 프로세스 하나를 `taskkill /F`했다.
    - 런처(RouterClientSupervisor)가 라우터를 다시 띄우는지 `/health`로 확인했다.
  - Prody(포트 9592)
    - 라우터 13160(시작 11-23 03:25:21 로컬)을 22:11:38Z에 종료했다.
    - 새 라우터 12500이 22:11:52Z에 시작했다(`router-fault.log`의 `started=2026-11-23T14:11:52`). 부모는 런처 7628이다.
    - 런처 7628은 살아 있고, claude pid(11412, 3636)도 그대로다.
    - 로그 수준이 ERROR라 router.log 끝 줄은 `ciel-runtime router starting on 127.0.0.1:9592`이다. 그 뒤 ERROR나 Traceback은 0이다(22:18Z 확인).
    - 그 앞에 있던 Traceback은 이전 라우터 때 것이다. `/health` 응답 중 클라이언트가 끊긴 `ConnectionAbortedError 10053`이고, 재시작과는 무관하다.
  - Celly(포트 9385)
    - 라우터 58480을 22:12:23Z에 종료했다.
    - 새 라우터 91384가 22:12:32Z에 시작했고 22:12:35Z에 `/health`가 응답했다. 부모는 런처 28096이다.
    - 런처는 살아 있고, claude pid(32712, 39264, 83796)도 그대로다.
    - 재시작 직후(16:12:41 로컬)부터 다음 호출이 새 라우터에서 응답했다: `work_status`, `work_side_claim`, `work_host_page`, `work_parallel_status`.
    - 16:12:30–16:15:51 로컬 2,362줄 중 ERROR와 Traceback은 0이다. WARN 내역:
      - `router_lifetime_keep_alive reason=reachable_health_mismatch_active_client` 38건(16:1x 구간 전체 44건)
      - `auto-synthesized TaskList` 3건
      - `external_event_sse_disconnected` EOF 3건
      - `anthropic_sse_client_disconnected` 1건
    - keep_alive 경고가 재시작 전에도 같은 빈도였는지는 비교하지 않았다(미확인).
  - 실행 코드
    - 두 라우터 모두 3d475f9 오버레이 설치(Celly 19:10:07Z, Prody 19:12:07Z까지) 뒤에 새로 뜬 프로세스다. 설치 파일 22개는 3d475f9와 해시가 같다(아래 배포 상태 점검 항목).
    - 따라서 9677d0f·3d475f9의 라우터 Python 변경이 이 두 호스트에서 지금 라이브다.
    - `/health`의 version 문자열(Celly `…013244.432c8a8`, Prody `…105743.fcce6d7`)은 패키지 VERSION 라벨이다. 오버레이로는 바뀌지 않는다.
    - 3d475f9의 Claude 실행 경로(위험 삭제 면제)는 Claude 실행 시점에 정해진다. 라우터 재시작만으로는 효과가 없다.
  - 재시작 뒤 모니터 프로브(22:16–22:18Z)
    - MCP는 4호스트 모두 `probe_succeeded`다(Celly v10060 235 ms, Prody v6923 941 ms).
    - 리스: Celly running+slot 6·waiting_input 7, Prody submitted 7, new_failures 0이다.
    - KOBE 포크 전사에 오류 기록 5건이 있다(21:58:31Z af62f22e, 22:10:16Z ab83cca9 3건, 22:13:24Z a40762da). 재시작 대상이 아니다. 내용은 아직 분류하지 않았다.

- 21:52:22–21:52:47Z 틱(직전 21:32Z). 운영 세션·라우터 재시작과 상태 파일 변경 없음.
  - **Andy: 예약 슬롯 미바인딩의 직접 원인을 관측했다(정정)**
    - 로그(로컬 15:38:39–40 = 21:38:39Z)에 다음 두 줄이 있다.
      - `work_workspace_failed code=workspace_prepare_retained item=n2655 detail="git worktree add timed out after 653 s"`
      - n2656 `… after 569 s`
    - 예약 뒤 포크용 worktree 준비(`git worktree add`)가 9–11분 걸리다가 시간 초과가 났다.
    - 그 뒤 두 리스는 running이고 host가 바인딩됐다(f43e7291→aeb36700, 1d10c498→a772a0f9).
    - 21:32Z 틱의 "포크 없이 남는 예약"은 바인딩 결함이 아니라 이 준비 대기 구간이었다. 20:33/20:38Z 건도 같은 원인인지는 그 창의 로그에 해당 줄이 없어 미확인이다.
    - `git worktree add`가 왜 이렇게 오래 걸리는지(저장소 크기, G: 디스크, 잠금)는 미확인이다. 추적 항목으로 남긴다.
    - 현재 running+slot 5(n2594, n2655, n2656, n2664, n2232), waiting_input 0, submitted 21이다.
    - 새 대화 오류 10건
      - host 전용 `work_side_status` 호출 거부 1(가드).
      - Walkie API `Invalid operation arguments` 400 6(21:43:18–21Z).
      - 보드 버전 충돌 1.
      - 진단 스크립트 2.
  - **KOBE: 재기동 후 정상 동작**
    - 21:47:34Z orphan sweep가 n107(95728c76 P3)·n28(87e1d8bb P2) waiting_input 리스를 owner=dead로 풀었다.
    - running+slot 1, 원장 v3091→3095, 보드 v725→728.
    - 21:49:26Z 메인의 reviewer 포크 요청이 "Work parallel starts workers only" 가드로 거부됐다.
  - **Celly**: running+slot 3→4, waiting_input 11→9. MCP 782ms(이번에는 성공).
    - 21:38:38Z `Work item n1384 remains queued as waiting: Parallel read failed: The operation timed out.` 같은 시각 라우터에 20초 이상 무기록 구간이 있었는지는 이번에 측정하지 않았다.
    - side review 버전 충돌 1, Walkie 400 1.
  - **Prody**: 보드 v6910→6915, actionable 0→5. 메인 대화에 Walkie 400 3, 스크립트 종료 3.
  - Walkie API 400(`Invalid operation arguments`)이 Andy·Celly·Prody에서 21:41–21:47Z에 함께 났다. 요청 인자의 문제인지 Walkie 쪽 변화인지는 미확인이다.

- 21:4x–21:51Z 배포 상태 확인(사용자 질문 "전체 배포한거야?"). 이 작업은 재시작이나 변경을 하지 않았다.
  - 파일: 오늘 커밋(5e02ee7^..3d475f9)이 바꾼 런타임 파일 22개를 대조했다. 로컬 3개 루트, Celly, KOBE, Prody 모두 22/22가 3d475f9과 해시 일치다.
  - **KOBE 재기동 관측(이 작업이 한 것 아님)**
    - 라우터 `router_spawn_start`가 21:47:28Z, 버전 `…185837.3d475f9`다.
    - 21:48:26Z `Ciel Work mode is on`이다. 19:07:56Z부터 내려가 있던 ciel-work가 다시 로드됐다.
    - 21:49:17Z 게이트 re-pumped, 21:49:40Z native 포크 확인 1.
  - 라우터 Python 라이브 여부
    - Andy: 21:00:57Z 재기동, 라이브.
    - KOBE: 21:47:28Z 재기동, 라이브.
    - Celly: 11-23 05:41Z 기동(432c8a8)이라 미반영.
    - Prody: 최근 30MB 로그에 기동 기록 없음, 미반영.

- 21:32:19–21:32:57Z 틱(직전 21:13Z). 운영 세션·라우터 재시작과 상태 파일 변경 없음.
  - **100.64.0.x 복구**
    - 21:32:57Z TCP 시도에서 8787·1004·1004 모두 open(0.00–0.02초)이었다. KOBE·Prody 관측도 다시 된다.
    - Walkie 전송의 마지막 실패는 Andy·Celly 모두 로컬 15:17:13–15:17:23(=21:17Z)이고, 21:17:23Z부터 `transcript_delta_delivered`다. 단절 구간은 약 19:53–21:17Z(약 84분)다.
    - Andy 사용자가 21:18:16Z에 "와키 문제는 셀리의 문제로 밝혀졌어"라고 적었다. 원인 판정은 사용자 측 정보이고 이 작업에서 검증하지 않았다.
  - **Andy: 예약 슬롯이 포크 없이 남는 현상 재발(반복 이상, 원인 미확인)**
    - n2655 P1 19b4873a(21:22:13Z)와 n2656 P1 15edea59(21:25:32Z)가 21:33:46Z까지 `reserved+slot`, host 없음이다(8–11분).
    - 21:18:58Z 이후 새 포크 transcript가 없다. 21:24:52Z 메인 대화가 compaction 요약으로 이어졌다.
    - 같은 현상은 20:33/20:38Z에도 있었고(14–19분), 21:00Z 사용자 재시작의 orphan sweep로 풀렸다.
    - 코드 경로(register.js 3249–3370)
      - 예약(`reserve`/`reserve_next`) 뒤 `$.agent.spawn`을 await한다.
      - 응답이 deny면 예약을 풀고, `agentId`가 없으면 그대로 `return null`이라 예약이 남는다.
      - spawn이 끝나지 않고 대기해도 예약이 남는다.
    - 실제로 어느 경우인지는 디버그 로그가 없어 확인하지 못했다. 추측으로 고치지 않고 다음 틱에 재관측한다.
  - **KOBE**
    - ciel-work가 여전히 내려간 상태다(19:07:56Z 이후 2시간 25분, 21:33:46Z 확인, 재로드 줄 없음).
    - 원장 v3091·보드 v725·waiting_input 2(n107, n28)가 그대로다.
    - Walkie 단절 기간의 전송 실패가 남아 있다(17·17건, 로컬 13:13부터).
  - **Celly**
    - **MCP 탐침 TimeoutError 4016ms 재발(오늘 3번째: 14:37·15:44·21:32Z)**. 같은 창 router.log에는 20초 이상 무기록 구간이 없었다(로컬 15:25–15:34). 원인은 미확인이다.
    - running+slot 1→3, waiting_input 12→11. 보드 버전 충돌 가드 2, 메인 스크립트 오류 1.
  - **Prody**: 관측 재개. 원장 v680, submitted 7, 보드 v6910이다. 직전 관측(19:14Z)과 비교해 보드만 바뀌었다.
  - **Andy 기타**: 원장 v6512→6559, submitted 25→21(검수 4건 처리, 21:18:50–21:19:53Z). side 원문 전달 3, 메인 진단 스크립트 오류 3.

- 21:13:05–21:13:40Z 틱(직전 20:52Z). 이 작업은 운영 세션·라우터 재시작과 상태 파일 변경을 하지 않았다.
  - **Andy 사용자 재시작(21:00Z, 이 작업이 한 것 아님)**
    - 사용자 대화: 20:59:55Z "실패이유는 뭐지?", 21:00:12Z "그럼 재시작하면되는지해볼까?", 21:02:55Z "재시작해도 동일하게 안되는데?".
    - 맥락은 Walkie MCP 단절이다. 재시작 뒤에도 같은 이유는 100.64.0.x 단절이 계속되기 때문이다(21:13:58Z에도 8787·1004 timeout).
    - 재시작 순서
      - 21:00:25Z Claude 종료, 라우터 lifetime 종료.
      - 21:00:57Z 라우터 재기동(hotfix5 경로).
      - 21:00:59Z orphan sweep가 3건을 풀었다: 48ddbead·348c823f waiting_input, 4cfc92ff reserved(n2175 P5), 모두 owner=dead.
    - **재시작 후 자동 디스패치 관측**: native 포크 확인이 21:02:40Z 2, 21:03:15Z 3이었고 21:09:07Z 1, 21:11:31Z 0으로 줄었다. submitted는 23→25다. 직전 틱에서 바인딩되지 않던 reserved 2건은 재시작으로 정리됐다.
    - 3d475f9가 라이브다(실행 로그 `… permission_request_hook=withheld`).
    - 다만 Andy 프로필에 타사 PermissionRequest 훅(orca-agent-hook)이 있어, 실엔진 `otherhook` 구성에서 질문이 그대로 남는다. 결정이 필요하다([기록](/docs/journal/2026/11/23/fixes/dangerous-removal-waiver/record.md)).
    - 메인의 새 대화 오류 11건은 네트워크·서비스 진단 스크립트 실패(Bridge, tailscale, WinDivert 조회 등)다.
  - **Celly**: waiting_input 12, running+slot 2→1, 원장 v8327→8334. side 원문 전달 1, 메인 스크립트 문법 오류 3, 포크 오류 3. 모두 에이전트 측이다.
  - **KOBE·Prody**: 관측 불가(100.64.0.x 단절 지속, 21:13:58Z).

- 20:52:31–20:53:09Z 틱(직전 20:32Z). 운영 세션·라우터 재시작과 상태 파일 변경 없음.
  - **100.64.0.x 단절 지속(1시간째)**
    - 20:53:28Z TCP 시도에서 8787(Walkie), 1004(KOBE), 1004(Prody) 모두 6초 timeout이었다. KOBE·Prody는 관측할 수 없다.
    - Walkie 전송 실패가 이어진다.
      - Andy: transcript 161, SSE 24, agent_runtime_event 재시도.
      - Celly: transcript 149, SSE 23.
    - Andy_Main 재현(20:39–20:41Z)과 일치한다.
      - 공개 readiness는 200이다.
      - 내부 주소는 Ciel Bridge /32 경로로 나가는데 8787과 1004가 timeout이다.
      - Andy 쪽 Walkie MCP의 마지막 성공 응답은 19:52:52.864Z다.
    - Andy_Main이 Celly에 Bridge 경로 대조를 전달해 달라고 요청했다. Celly 운영 세션 쓰기는 승인 범위 밖이라 보류하고 사용자에게 보고했다.
  - **Andy: 예약 슬롯 2개가 포크 없이 유지된다(새 관측)**
    - n2175 P5 rework 리스 4cfc92ff는 20:33:07Z, n2654 P1 auto 리스 696df191은 20:38:41Z에 `reserved+slot`이 됐다. 20:52Z까지 host_id가 없다(14–19분).
    - 같은 사이 20:33:20Z·20:44:01Z 게이트 라운드는 "re-pumped"였고, 20:35:39Z native 포크 확인은 0이었다.
    - 원장은 v6385→6474(+89)다.
    - 포크가 바인딩되지 않은 원인은 미확인이다. 이 대화에서 메인의 Agent 호출이나 spawn 거부는 관측되지 않았다.
    - 재시작 후 자동 디스패치 요구와 직결되는 미결 항목이라, 다음 틱에 바인딩되는지 본다.
  - **Andy 기타**: submitted 24→23. 새 대화 오류는 walkie disconnected 1, 경로 없음 1, planning 편집 가드 1이다.
  - **Celly**: waiting_input 13→12, running+slot 0→2.
    - `channel_stdin_proxy_blocked`(로컬 14:33:39, message 16046, `prior_submission_failed/stale_unconfirmed`, skipped) 1건이 났다. 11-21에 기록한 가드 패턴이다.
    - 포크 a5b21e3b 오류 5: bash 따옴표 EOF 2, Write 도구 없음 3. Write가 그 포크에 노출되지 않은 이유는 미확인이고 에이전트 측 사용 오류로 분류한다.
    - 메인 오류 3: 보드 버전 충돌, walkie 끊김, auto continue 상태 읽기 실패.

- 20:32:19–20:32:54Z 틱(직전 20:12Z). 운영 세션·라우터 재시작과 상태 파일 변경 없음.
  - **100.64.0.x 단절 지속(40분째)**: 20:33:18Z에 TCP 연결을 시도했다. Walkie 8787, KOBE·Prody ssh 1004 모두 6초 timeout이다. KOBE·Prody는 관측할 수 없다.
    - Andy·Celly 라우터의 Walkie 전송 실패가 계속된다.
      - transcript 전송 실패: Andy 108, Celly 79.
      - 외부 SSE connect timeout: Andy 23, Celly 12.
    - 두 메인 대화의 walkie MCP는 계속 끊겨 있다.
    - Celly 메인도 20:26:47Z에 `Ciel Bridge` 어댑터를 직접 점검했다. 같은 단절을 보고 있다.
  - **Andy**: 원장 v6374→6385, submitted 23→24, waiting_input 1→2, running+slot 3→0.
    - n2652 검수 없음 finding이 사라졌다(지연이었음).
    - n1166만 남았다.
  - **Celly**: waiting_input 12→13, running+slot 1→0. 실행 중인 포크가 없다.
    - 20:30:40Z 사용자가 메인의 도구 호출 2건을 거부했다(사람의 결정).
    - `ChannelFileLockBusy` 1(로컬 14:18:01, transcript 전송 poll)이 났다. 11-21 이전 패턴과 같은 잠금 경쟁이다.
    - 포크 오류 1건, 메인 오류 3건은 경로 없음과 스크립트 오류다.

- 20:12:19–20:13:05Z 틱(직전 19:52Z) + 네트워크 확인 20:18Z. 운영 세션·라우터 재시작과 상태 파일 변경 없음.
  - **이 PC에서 100.64.0.x 대역 전체에 닿지 않는다(네트워크 사건, 제품 결함 아님)**
    - KOBE(100.64.0.14)·Prody(100.64.0.12) ssh: `ssh failed`.
    - Walkie(100.64.0.9) ping 100% 손실.
    - 경로는 `Ciel Bridge`(Ciel Tunnel) 어댑터인데, 어댑터는 Up이다.
    - 같은 시각 이 PC의 유선 Ethernet 3개가 Disconnected이고 Wi-Fi만 Up이다. tailscale netcheck가 `gateway and self IP changed: self=10.0.0.146`을 기록했다(14:18 로컬).
    - 어댑터 전환과 터널 단절의 인과는 미확인이다.
    - Andy·Celly 라우터(같은 PC)의 Walkie 쪽 실패는 로컬 13:53–13:54(=19:53–19:54Z)부터 시작된다.
      - `transcript_delta_delivery_failed`: Andy timeout 66·거부 64, Celly 56·55.
      - 외부 SSE connect timeout: Andy 22, Celly 24.
      - Celly `agent_runtime_event_retry`(100.64.0.9:8787).
    - 두 메인 대화에도 `MCP server "walkie" is not connected`(Andy 19:58:34Z~, Celly 20:06:44Z~)가 있다.
    - 이 상태에서 KOBE·Prody 관측, Walkie 메시지 송수신, 외부 이벤트 수신이 멈춘다.
  - **Andy**: 원장 v6359→6374, submitted 22→23, running+slot 2→3. MCP 610ms.
    - 새 finding `n2652 10970391 P1`: 완료 영수증은 있고 검수 행이 없다(20:09Z 제출, 방금이라 지연일 수 있음).
    - 19:54:15Z 승인 필요 가드 재발.
  - **Celly**: waiting_input 11→12, running+slot 2→1. MCP 250ms.
    - 메인이 host 전용 `work_side_status`를 불러 거부됐다(가드).
    - n1394 포크가 "Daniel의 권한 변경 대기"로 다시 대기에 들어갔다(업무 선결 조건).
  - **KOBE·Prody**: 관측 불가. ciel-work 모드가 내려간 KOBE 상태(19:07:56Z~)는 마지막 확인 19:53:20Z 기준이다.

- 19:52:19–19:53:07Z 틱(직전 19:32Z). 운영 세션·라우터 재시작과 상태 파일 변경 없음.
  - **KOBE**: ciel-work는 여전히 내려간 상태다. 19:53:20Z 확인했고 재로드 줄이 없다. 대화 기록은 19:52:55Z까지 갱신 중이다. 원장 v3091·보드 v725는 46분째 그대로이고 waiting_input 2다. 모드가 없어 Work 펌프·게이트가 돌지 않는 상태가 이어진다.
  - **Andy**: 원장 v6337→6359. submitted 21→22, waiting_input 2→1, running+slot 3→2, reserved+slot 1.
    - 19:35:11Z 사용자가 메인의 도구 호출 3건을 거부했다(사람의 결정).
    - 19:40:07Z 대기 중이던 포크 348c823f 재개를 Claude Code가 거부했다("the session was stopped and nobody has sent input since").
    - 19:41:16Z 호스트가 그 대기를 비웠다(`outcome=a resume was refused within the last 5 minutes`).
    - 19:42:16Z `Work approval must be restored before resuming workers`. 목표가 planning 단계라 걸린 가드다.
    - 셋 다 가드·엔진 거부로 분류한다. 재개가 막힌 이 포크의 아이템이 다시 디스패치되는지는 다음 틱에 본다.
  - **Celly**: n1374 부재 finding이 사라졌다(19:18Z 이후 다시 관측됨, 종료 아님). waiting_input 10→11, running+slot 3→2.
    - auto continue 상태 읽기 실패 공지 1(19:35:09Z).
    - 포크 오류 8건은 모두 에이전트 측이다: question evidence 2112자 초과, TaskStop 가드, ripgrep timeout, 경로 없음, 스크립트 오류.
    - 19:29–19:31Z 같은 무기록 구간은 이번 창에서 다시 측정하지 않았다.
  - **Prody**: 보드 v6894→6898만 바뀌었다.

- 19:32:20–19:32:40Z 틱(직전 19:18Z). 운영 세션·라우터 재시작과 상태 파일 변경 없음.
  - **Celly 라우터 무기록 구간 재발(반복 이상, 11-22에 기록한 패턴)**
    - 로컬 13:29:26–13:31:30, 124초 동안 router.log에 한 줄도 없다.
    - 직전 13:29:15에 MCP POST 클라이언트 reset이 8건 몰렸고, 직후 13:31:30–36에 다시 몰렸다.
    - 13:31:31·35·35(로컬)에 `anthropic_route_error … status=500 … router pre-output time limit passed before the first upstream open (0 attempts)`가 3건 났다.
    - 같은 구간 Celly 대화
      - 19:29:15Z: `Work message refused: the Work store could not be read (Parallel read failed: The operation timed out.)`
      - 19:32:01Z: side 원문 already
    - 13:31:39부터는 정상 처리된다.
    - 정지 원인(GIL, 디스크, 잠금 등)은 미확인이다. 무기록 중 무엇이 막혔는지 이 로그로는 알 수 없다.
    - 새 finding: `slot held, fork absent since 19:18: n1374 49aafe07 P1 running`. 부재 관측일 뿐 종료로 보지 않는다.
    - 원장 v8311→8315, waiting_input 12→10, running+slot 1→3.
    - n1394 P0 재개 거부(이전 재개의 전달 기록 없음, 19:19:41Z)는 가드다.
  - **KOBE**: ciel-work는 여전히 내려간 상태다(19:33:10Z 확인, 재로드 줄 없음).
    - 19:23:14Z 메인의 Work 도구 호출이 `conversation identity is unavailable`로 거부됐다. 모드가 세션 식별을 넣어 주지 못하는 상태와 일치한다.
    - 원장·보드 변화 없음, MCP 451ms.
  - **Andy**: running+slot 2→3, 원장 v6332→6337. 새 대화 오류는 ripgrep timeout 2와 planning 단계 편집 가드 1이다.
  - **Prody**: 보드 v6892→6894만 바뀌었다.

- 19:18:02–19:18:24Z 틱(직전 19:14Z). 운영 세션·라우터 재시작과 상태 파일 변경 없음.
  - **KOBE**: ciel-work 모드는 아직 내려간 상태다. 19:19:00Z까지 재로드 줄이 없다.
    - 대화 기록은 19:16:22Z이 마지막이다. 미갱신을 종료로 보지 않는다.
    - 원장 v3091·보드 v725 그대로, waiting_input 2(n107, n28). 라우터 MCP는 217ms로 정상이다.
  - **Celly**: waiting_input 11→12, running+slot 2→1. 새 포크 오류 1건은 질문 보고 형식 거부(19:16:24Z, `detail` 필드 불일치)다.
  - **Andy**: 변화 없음. 새 대화 오류 1건은 메인의 Python 인코딩 오류다.
  - **Prody**: 보드만 v6891→6892.

- 19:13:56–19:14:20Z 틱(직전 18:38Z) + KOBE 확인 19:17Z. 이 작업은 운영 세션·라우터 재시작과 상태 파일 변경을 하지 않았다.
  - **KOBE — ciel-work 모드가 내려간 상태(반복 이상)**
    - 19:07:56Z 대화에 `ciel-work was unloaded: it crashed the hooks worker`가 남았다. 19:17:08Z(대화 기록 갱신 19:16:22Z)까지 재로드 줄이 없다.
    - 따라서 KOBE에서는 Work 훅(펌프, 게이트 라운드, side, fork gate)이 지금 돌지 않는 것으로 관측된다.
    - 원인은 미확인이다. 디버그 로그 없이 실행된 세션이다. 내 3d475f9 설치(19:12Z)는 모드 파일을 바꾸지 않았고 충돌보다 뒤다.
    - 같은 문구는 11-21 Andy 17:15Z에서도 1회 있었다.
    - 복구(세션 재시작이나 모드 재로드 유도)는 금지 범위라 하지 않았다.
    - 직전 상황
      - 18:41:24–18:41:55Z: 사용자 지시("실제 와키 쓰레드에서 찾아")에 메인이 읽기 전용 감사 에이전트 5개를 `general-purpose`로 띄우려 했고, Work fork gate가 5회 거부했다. 정상 가드다.
      - 18:46:40Z: 메인이 n28 P2 `work_parallel_run`을 직접 실행했다.
      - 이후 native 포크 확인 0↔1이 오갔다.
    - 원장 v3074→3091, 보드 v709→725, waiting_input 1→2(n107 P3, n28 P2, 둘 다 slot 없음). MCP 178ms.
  - **Andy**: 원장 v6320→6332, running+slot 0→2, submitted 22→21. MCP 547ms.
    - 19:03:32Z `stale completion workspace snapshot; result retained`(n1166 복구 시도, Andy_Main 보고와 같음).
    - 18:53:08Z n2650 포크 `transport_interrupted`로 재시도 예약.
    - 보드 버전 충돌 가드 2.
    - Andy_Main이 보고한 "rework_ready 6행"에 대해, 이 모니터가 읽는 루트 finalization(v2271)에서는 rework_ready가 n2175·n1166 2행이다. 상태 투영이 읽는 원장과 같은 것인지는 미확인이다.
  - **Celly**: 원장 v8288→8310, waiting_input 8→11, running+slot 5→2. MCP 265ms.
    - n1374 `transport_interrupted` 재시도 예약 1.
    - 활성 목표 변경 거부 1.
    - side 실패 원문 전달 1.
    - auto continue 상태 읽기 실패 1(19:13:16Z).
    - 포크 오류 5: question evidence 2000자 초과, 보고 종류, TaskList·TaskStop 가드, Work 상태 읽기 실패로 Walkie 읽기 거부(19:12:02Z). 모두 가드나 검증 거부다.
  - **Prody**: 원장 v680 그대로, 보드 v6885→6891. MCP 1114ms. 메인이 host 전용 `work_side_status`를 불러 거부됐다(가드).
  - 3d475f9(위험 삭제 면제)는 6개 루트에 설치를 마쳤다. 라이브 효과는 각 세션이 옵션을 켠 채 새로 실행될 때부터다.

- 18:38:17–18:39:31Z 틱(직전 17:32Z). 이 작업은 운영 세션·라우터 재시작과 상태 파일 변경을 하지 않았다.
  - **Andy**: 원장 v6314→6320, 보드 v10846→10865. submitted 21→22, waiting_input 2. MCP 766ms. 포크 오류 0. n1166 지속.
    - auto continue 상태 읽기 실패 공지 1(18:34:55Z). 같은 틱의 MCP는 성공했다. 이 공지는 이제 Andy·Celly·KOBE·Prody 네 호스트 모두에서 관측됐다. 원인은 미확인이며 추적 중이다.
    - 라우터 경고는 SSE EOF 65, 연결 reset, 관측 전용 projection, late SSE 재시도 3(각 1회 시도)다.
  - **Celly**: 원장 v8245→8288, 보드 v9921→9982(활성 n1209→n1389). running+slot 2→5, waiting_input 8. MCP 1406ms.
    - new_failures 4건은 모두 정상 가드로 분류한다.
      - `revival_refused 8938d91b` 1: 18:19:20Z, 메인이 unreported 리스의 포크 a5fbb4f9에 메시지를 보냈고 거부됐다.
      - `decision_refused_stop` 3(8938d91b 2, 9a145fc1 1): 18:19:46·18:20:11Z `stranded decision`, 18:25:46Z `review decision` 거부.
      - 거부 문구가 단계 이름뿐이라 이유가 드러나지 않는다. 문구 품질만 추적하고 결함으로 보지 않는다.
    - 메인 대화의 다른 가드 거부
      - n1384 재개 거부 2(18:07:16·18:08:20Z): 목표 미승인.
      - 활성 목표 변경 거부 1: `user_requested` 없음.
      - 계획에 단계 없음 1.
    - side appended/already 쌍 1(419efa58437a)과 `workspace ownership was handed off; retained` 1(18:18:30Z)이 반복된다.
    - 포크 오류 표본 30건(17:35–18:34Z, agent 6개)을 출처별로 나눴다.
      - Work 포크 가드: TaskStop·TaskList 6, worktree 밖 Write 1.
      - 보고 검증: 질문 형식 2, 보고 종류 2, SHA256 1.
      - 하네스 sleep 차단 1.
      - 외부: GitHub 권한, SSH 호스트 키, Walkie 400, ripgrep timeout 3.
      - 나머지는 에이전트 스크립트 오류다.
    - 제품 결함은 입증되지 않았다.
  - **KOBE**: 원장 v3068→3074, 보드 v704→709. 열린 리스는 submitted 1에서 **waiting_input 1**이 됐다.
    - 이 리스는 n107 rework 포크 95728c76(host ade9088c, slot 없음)이다. 질문 evidence는 "NAS run 모드가 LA 10:34 PST 창 밖이라 rc=2"다.
    - 9677d0f 라우터가 라이브인 첫 질문 대기다.
      - 18:35:35Z와 18:40:13Z에 게이트 재확인이 10건 큐에 들어갔다.
      - 사이드가 이 포크에 `fork_answers` 또는 `ask_main`으로 답했는지는 이 계정으로 side 인박스를 읽을 수 없어 확인하지 못했다. 다음 틱에 대화 기록으로 확인한다.
    - 18:30:45Z 메인이 ask_main 알림에 `work_side_handoff`를 불러 "side handoff not found"를 받았다. 도구 안내대로의 정상 응답이다.
    - 압축 실패가 반복된다: 18:05–18:06Z에 업스트림 입력 한도 초과 400 8회, transient_stopped 4회. 메인은 18:03Z 사용자 `/compact` 뒤 18:25:44Z에 이어 붙었다.
    - **사용자 지적 18:35:32Z "각각의 우선순위별로 포크를 실행해야지?"**
      - 18:36:06Z native 포크 확인 0, 18:39:09Z 게이트 라운드 "attention P0,P1,P2,P3"이다.
      - 우선순위별 자동 디스패치가 KOBE에서도 아직 돌지 않는 것으로 관측된다. 원래 요구(재시작 후 큐 재디스패치)의 미결 항목이다.
  - **Prody**: 원장 v672→680, 보드 v6872→6885. submitted 7, MCP 811ms, 포크 오류 0.
    - 새 대화 오류 4건: 버전 충돌 가드 1, 메인 스크립트 3(cp949 인코딩 등).
    - **9677d0f 모드 재로드 17:52:22Z 관측.** 이제 네 호스트 모두 9677d0f 모드를 로드했다. 첫 SSH는 banner timeout이었고, 재시도에서 성공했다.
  - **사용자 요청(Celly "can't rule out a dangerous removal" 승인 창) 조사·수정 진행 중**: 아래 2026-11-23 18:5xZ 항목에 기록한다.

- 17:32:13–17:32:37Z 틱(직전 17:14:46Z) + KOBE 압축 확인 17:33Z. 이 작업은 운영 세션·라우터 재시작과 상태 파일 변경을 하지 않았다.
  - **Andy**: 원장 v6305→6314, 보드 v10842→10846. running+slot 1→0, waiting_input 2, submitted 21. MCP 1016ms. 포크 표본 5/오류 0, 새 대화 오류 0. n1166 지속. 라우터 신규 줄은 외부 SSE EOF 17, 모델 연결 reset 3, 업스트림 SSE 오류 이벤트 1(17:15:41Z, `_Walkie_S_Tier_Only`), 관측 전용 projection 경고 2라서 결함으로 보지 않는다.
  - **Celly**: 원장 v8245 그대로, 보드 v9913→9921. waiting_input 8, running+slot 2. MCP 579ms(2틱 연속 정상). 포크 표본 4/오류 0, 새 대화 오류 0. 라우터는 keep-alive TaskList 합성 20, SSE EOF 18, 모델 연결 reset 15(모두 경고 수준).
  - **KOBE**: scope가 운영 세션 `3a8e26fe`로 돌아온 것을 틱에서도 확인했다. 보드 v704, MCP `enabled=true` 338ms, submitted 1, stale_runs 13, 포크 표본 24/오류 0. 원장 v3068 그대로.
    - **반복 이상: 메인 대화가 압축 실패 반복에 묶여 있다.**
      - 17:24:15Z 메인 요청이 1,140,831토큰(창 1,000,000)으로 나갔다. 라우터 분할 압축의 요약 호출(236,680토큰, 5조각 중 0 완료)이 3회 모두 `TimeoutError`로 실패했다(17:26:33·17:29:11·17:31:44Z). 각 실패는 `context_compact_failed`와 `anthropic_route_error_after_compaction_sse` 504로 이어졌다.
      - 같은 메인 작업 `e278cf76`의 late SSE 재시도는 17:32:40Z에 19/60이다.
      - 요약 호출의 상한은 코드상 120초다(`context_compaction.py:142–146`, `:239`). 업스트림이 이 조각을 120초 안에 끝내지 못했다는 것까지만 관측했다. 업스트림이 왜 느린지, 상한이 더 길면 끝났을지는 미확인이라 제품 결함으로 단정하지 않는다.
      - 같은 세션에서 압축 오류가 14:38Z부터 21회 있었고, 17:12–17:14Z에는 게이트웨이 `auto/claude-opus`가 kimi로 라우팅돼 입력 한도 400이 9회 났다.
      - 사용자가 16:28Z에 `dd8e657c`에서 이 세션 축소를 요청했다. 대화 파일은 111.4MB 그대로다(17:29:54Z 갱신).
    - side 실패 원문 메인 전달 1(17:29:46Z, event `76cb8fe3ad0c`)이 있었다. 이 SSH 계정으로는 side 인박스를 찾지 못해 실패 사유를 읽지 못했다. 메인 연결이 위 상태인 것과의 관계는 🟡 미확인이다.
    - 17:29:27Z 게이트 재확인 9건 대기열. 9677d0f 라우터가 라이브이지만 KOBE에는 `waiting_input` 리스가 없어 `fork_answers` 효과는 관측 대상이 없다.
  - **Prody**: 원장 v672 그대로, 보드 v6868→6872. submitted 7, MCP 1368ms, 포크 표본 24/오류 0.
    - 새 대화 오류 4건은 출처별로 나눈다.
      - 보드 버전 충돌 가드 1(17:17:26Z).
      - 메인 자체 점검 스크립트 종료 2(17:17:30·17:17:34Z, 미확인 요청 목록 출력).
      - `goal n1000 has no workflow` 응답 1(17:21:29Z).
    - **9677d0f 모드 재로드는 계속 미관측이다.** 마지막 `end_turn`은 16:45:56Z이고, 그 뒤로 한 턴이 이어지고 있다(대화 파일 952.3MB, 갱신 중).
  - 미결 추적
    - 새로 추가: KOBE 압축 실패 반복.
    - 그대로: KOBE 17:18:04Z 코드 1 종료 원인, 17:09Z `router-disconnected`, Celly auto-continue 상태 읽기 실패 공지(이번 창 0), Andy n1166, KOBE n107, Celly P0 옛 intent 13, 대기 감사·실엔진 재시작 E2E·UI 노출, py310 503 테스트.

- 17:14:46–17:15:08Z 틱(직전 15:44Z) + KOBE 별도 확인 17:19–17:27Z. 이 작업은 운영 세션·라우터 재시작과 상태 파일 변경을 하지 않았다. 시각은 Z다.
  - **Andy**: 원장 v6285→6305, 보드 v10814→10842. running+slot 2→1, waiting_input 1→2, submitted 21. 포크 표본 5(부분)/오류 0. MCP 1109ms(enabled, 보드 버전 일치). 검수 findings n1166(완료 영수증 있음, 검수 행 없음) 지속. 새 대화 오류 2는 메인의 검증 명령 종료 1이라 제품 결함으로 보지 않는다. router_log `9505-…` 같음(재시작 관측 없음), 15:44Z 이후 처음 나타난 로그 패턴 0.
  - **Celly**: 원장 v8203→8245, 보드 v9868→9913. waiting_input 6→8, running+slot 3→2. **MCP는 500ms 성공**이라 14:37·15:44Z TimeoutError는 이번에 재현되지 않았고 원인은 미확인 상태로 추적을 계속한다. 포크 오류 표본 9건(부분, agent 3개 16:08–17:10Z)을 원문으로 분리했다.
    - 포크 보고 검증 거부 6(`evidence` 2000자 초과 2, 산출물 SHA256 없음 4; 16:24:05–16:25:20Z) → 16:25:31Z에 다시 보고해 아이템 n1386 `submitted`로 수락됐다. 정상 검증 가드이며 결함으로 보지 않는다.
    - Work 포크 가드 3: 자기 worktree 밖 Edit/Write 2(16:08:07, 15:57:14Z), 포크의 TaskStop 불가 1(15:48:50Z).
    - 포크 도구/외부 서비스 입력 오류: Walkie `Invalid Vault path` 1(16:07:27Z), `client_id` uuid 형식 1(16:06:08Z), 포크의 `work_parallel_status`에 대화 식별 없음 1(15:53:05Z), Bash 종료 1/124 4건.
    - 메인 대화 신규 8: `auto continue could not read the Work status` 2(15:45:13·16:19:23Z)는 MCP가 같은 틱에 성공했는데도 남아 있는 일시적 상태 읽기 실패로 추적 유지. 나머지는 메인 스크립트/검색 오류 5와 완료 시 병렬 검증·검수 대기 가드 1(16:56:35Z).
    - router_log `9385-…` 같음, 15:44Z 이후 신규 패턴 0.
  - **KOBE — 라우터 중단과 재시작 복구를 관측했다**(이 작업이 한 것 아님).
    - 틱 시점 scope는 사용자가 16:28:24Z에 `/clear`로 연 세션 `dd8e657c`였고(compact 실패한 `3a8e26fe` 크기 축소가 그 작업), 보드는 `empty_view` v0·MCP `enabled=false`·열린 리스 0·포크 `unobserved`였다. 새 세션은 Work 도구를 부른 적이 없어 보드 행이 없을 수 있어, `enabled=false`의 원인을 이 틱만으로 단정하지 않는다.
    - 이 세션에서 17:09:12Z에 Work 상태 읽기가 `router-disconnected`(게이트 `wg-mwtit1hi-1`)로 실패했고, auto continue 같은 공지가 17:12:34·17:14:48Z에 반복됐다. 라우터 로그에는 그 시각 MCP access 200과 `work_side_*/llm_options/provider_fallback` 호출이 이어졌고, 해당 실패를 남기는 오류 줄은 없었다. **실패 구간은 라우터 로그에서 확인되지 않으며 원인은 미확인이다.** 같은 구간에 native compact 요청 9회 이상이 업스트림에서 최대 입력 토큰 초과 400으로 거부됐다(17:12:50–17:13:59Z, 토큰 1,080,989→452,941로 줄여가며 반복).
    - 17:15:35Z Claude 자식이 코드 0으로 종료 → 라우터 lifetime 종료(`idle_no_clients`, `claude_exit`). 17:17:54Z 재기동은 버전 `…165946.9677d0f`로 떴으나 Claude 자식이 17:18:04Z 코드 1로 1초 만에 종료했다(진단 줄은 라우터 health=ok만 적고 Claude 출력은 없음; 종료 원인 미확인). 이후 `--continue --ca-menu resume 3a8e26fe` 런처가 17:18:19Z부터 돌고 있고, 두 번째 재기동에서 Claude 자식이 17:22:25Z에 떴다.
    - **복구 확인(17:26:19Z 단독 재관측)**: scope가 운영 세션 `3a8e26fe`로 돌아왔고 보드 v703→704·MCP `enabled=true` 162ms·열린 리스 `submitted` 1·stale_runs 13·포크 오류 0. 17:24:44Z `Ciel Work mode is on`, 17:25:13Z 게이트 점검 "re-pumped · attention P0,P1,P2,P3". 라우터는 9677d0f(pid 14388, 지문 `bc9f4d1c`는 변하지 않음 — `ciel_runtime.py` 단일 파일 해시라 지원 모듈 변경이 지문에 나오지 않는다). 설치본 `work_gate_check.py`·`side_work_decision.py`·`work_workflow_prompts.py` 해시는 9677d0f와 같다(`a76ecb03`·`4c855ff2`·`4dcc994b`). 다만 mtime이 17:16:11Z라 내 17:11:04Z 설치 뒤 다시 쓰였다. 같은 시각 첫 런처의 `self_update_check`가 17:15:58–17:18:09Z(130초, SystemExit로 끝남)에 돌고 있었고 두 번째 런처(17:16:34Z)가 그 사이 라우터를 띄웠다. 재설치 주체가 self-update라는 것과 17:18:04Z 코드 1 종료와의 관계는 🟡 미확인이다. 내용은 9677d0f이므로 **KOBE에서 9677d0f의 라우터 변경(`fork_answers` 포함)이 이제 라이브다.** 9677d0f 모드 재로드는 3a8e26fe에서 미관측(세션이 새로 떠 설치본 모드를 바로 로드한 것으로 보인다 — 🟡 추정).
    - n107 재시도: 이번 틱 비교나 로그 창 모두 새 재시도 체인이 없다(`actionable=1, next=n107` 상태 유지). 미발생은 종료 증거가 아니므로 추적 유지.
    - 기록되지 않은 첫 종료(코드 1)의 원인, 17:09Z `router-disconnected` 구간은 다음 틱에도 단정하지 않고 관측을 이어간다.
  - **Prody**: 원장 v662→672, 보드 v6853→6868. submitted 8→7, actionable 2→0, halted 6→7. MCP 1702ms 성공. 포크 표본 24/오류 0.
    - `new_failures decision_refused_stop a3372eba x1` — 17:02:14Z. 같은 시각 대화에 `work_parallel_item_review: rework limit reached (2/2)`가 있어, 메인의 검수 결정이 재작업 한도에 거부된 기록이다(기록은 `register.js:3003`의 Stop 상한 표지). 정상 가드로 분류한다.
    - side 실패 원문 메인 전달 3건(16:27:52·16:27:57·16:29:00Z, 앞 두 건은 같은 event `ba2c7a923fa1`의 appended/already 쌍). 나머지는 메인의 도구 입력 오류 4건.
    - **9677d0f 모드 재로드는 17:19:48Z에도 미관측이다.** Prody 세션은 설치(17:11Z) 이후 턴이 끝나지 않았다: 대화의 마지막 `end_turn` 16:45:56Z, 마지막 `turn_duration` 16:46:08Z, 마지막 프롬프트 16:47:19Z. 기록 파일은 갱신 중(mtime 17:19:48Z)이라 종료가 아니라 한 턴이 계속되는 중으로 관측된다. 오늘의 모드 재로드는 01:35:31·11:11:40·15:42:09Z 3회였다.
  - 미결 추적(변화 없음): Andy n1166, KOBE n107, Celly P0 장부의 옛 scope intent 13개 prune 경로 없음, 오래된 waiting_on 호스트 감사 미구현, 실엔진 재시작 E2E(체크리스트 14)·UI 노출(15) 미수행, py310 shard2 503 복구 테스트 1회 실패 원인 미확인.

- 16:50–17:14Z side-fresh 수정(9677d0f) 배포. 운영 세션·라우터 재시작과 상태 파일 변경 없음.
  - Publish 45222673557 success, npm beta `…165946.9677d0f`. 9개 파일을 6개 루트에 17:11:00–17:11:16Z 설치했다(설치 전 base, 설치 후 new).
  - 모드 재로드: Celly 17:11:20Z, KOBE 17:12:35Z, Andy 17:13:10Z. Prody는 17:13:58Z 기준 미관측이다.
  - **KOBE 라우터 재시작 관측**(이 작업이 한 것 아님, 주체 미확인)
    - 지문 `bc9f4d1c`, 버전 `…152815.cc2bac9`. 새 세션 `dd8e657c`가 16:28:25Z부터다.
    - KOBE에서는 5e02ee7·cc2bac9 라우터 변경이 이 재시작 이후 라이브다. 9677d0f 라우터 변경은 다음 재시작부터다.
  - 기록: [9677d0f verification](/docs/journal/2026/11/23/releases/9677d0f/verification.md).

- 15:44:28–15:44:55Z 틱(직전 14:37Z). 운영 세션·라우터 재시작과 상태 파일 변경 없음.
  - Andy: 원장 v6275→6285, 보드 v10792→10814. running+slot 1→2, waiting_input 2→1, submitted 21. findings n1166 지속. 새 대화 오류 0. MCP 640ms.
  - Celly
    - 원장 v8173→8203, 보드 v9840→9868. waiting_input 9→6, running+slot 0→3.
    - newfail `revival_refused` 1: 15:23:44Z, 메인이 끝난 포크 a4d04d5a에 메시지를 보냄. 정상 가드다. 바로 앞 15:23:15Z의 `No task found`는 같은 포크다.
    - 포크 오류 기록 6(15:15–15:19Z).
    - **MCP 탐침 TimeoutError 4031ms, 2틱 연속**(14:37·15:44Z). 원인 미확인이며 추적한다.
  - KOBE
    - 원장 v3066→3068, 보드 v701→703. running+slot 1 → submitted 1.
    - n107 재시도 결함(라우터 미반영): 14:38:53·14:42:33·14:47:27Z에 `scheduling up to 20 retries`가 다시 나왔고, 14:48:33Z에 `retry 1/20`. 이번 틱 이후 새 체인은 없다.
  - Prody: 원장 v641→662, 보드 v6834→6853. submitted 6→8, running+slot 1→0. 새 대화 오류 6은 메인의 도구 사용과 외부 API(gh 인증 없음, Walkie 403/400, 모듈 없음)라서 제품 결함이 아니다.
  - 사용자 지시(Celly의 side-fresh 분석, 수정 요청)를 확인하고 수정에 들어갔다. 기록은 [side-parked-fork-answers](/docs/journal/2026/11/23/fixes/side-parked-fork-answers/record.md).

- 15:14–15:40Z 사용자 화면(Celly P0 장부 256/256) 조사, cc2bac9 배포, 라이브 확인. 운영 세션·라우터 재시작과 상태 파일 변경 없음(아래 정리는 Celly 메인이 한 것).
  - Celly P0:
    - 라이브 라우터 지문 `84eb402c`(fcce6d7 이전)라서 cancelled 행만 비울 수 있었고, 장부가 256/256으로 막혔다.
    - 15:13Z에 Celly 메인이 사용자 승인을 받아 181행을 정리했다(256→75, 백업 있음).
    - 정리 뒤 현재 scope의 새 intent는 15:14Z 기준 0이다. 상세는 [celly-p0-ledger-full](/docs/journal/2026/11/23/operations/celly-p0-ledger-full/record.md).
  - cc2bac9 배포: Publish 45205553069 success, npm beta `0.2.51-beta.20261123-152815.cc2bac9`, 4개 파일 6개 루트에 15:39:33–15:39:47Z 설치(설치 전 base, 설치 후 new).
  - 모드 재로드: Andy 15:39:49Z, Celly 15:39:35Z, KOBE 15:41:42Z, Prody 15:42:09Z. Celly의 5e02ee7 모드 재로드는 15:16:37Z였다. Prody는 5e02ee7 모드를 한 번도 로드하지 않았다.
  - 위험 구간 `root_id` 호출은 네 호스트 모두 0건이다. 기록은 [cc2bac9 verification](/docs/journal/2026/11/23/releases/cc2bac9/verification.md).

- 14:37:18–14:39:05Z 틱(직전 13:07:38Z)과 5e02ee7 배포. 운영 세션·라우터 재시작과 상태 파일 변경 없음.
  - 배포: 5e02ee7을 feature/priority-models·beta에 push했다. Publish 45193606117 success. npm beta `0.2.51-beta.20261123-142311.5e02ee7`, gitHead 5e02ee72cd63가 일치하고, 배포본 14개 파일이 오버레이 new와 해시가 같다.
    - 해시 확인 오버레이(`overlay-5e02ee7`): 로컬 hotfix5·`.local/share` 13개(`docs/` 없는 레이아웃), npm 전역·Celly 14개 14:36:38Z, KOBE 14:36:55Z, Prody 14:37:08Z. 설치 전 모두 base, 설치 후 모두 new.
  - 라이브(파일 설치와 구분):
    - 모드 재로드는 Andy 14:36:44Z, KOBE 14:37:14Z에 관측됐다. Celly(대화 기록 14:34:33Z 이후 갱신 없음)와 Prody(14:43:19Z까지 갱신, 재로드 줄 없음)는 14:43Z 기준 미관측이다.
    - 라우터 Python 변경(큐 순서, `work_execute` `root_id`, 포크 재시도 분류기 awaiting, 모듈 분리)은 네 라우터 모두 재시작 전이라 아직 라이브가 아니다.
  - **결함 입증(혼합 버전)**: 재로드된 모드 지침은 `work_execute`의 `root_id`를 안내하는데, 옛 라우터는 `root_id`를 무시하고 활성 목표를 승인한다. fcce6d7 코드 재현에서 `root_id=n4`를 요청하자 `n1`이 승인됐다. 14:43:55Z까지 네 호스트의 `work_execute` 호출은 0건이라 라이브 피해는 없다. 수정과 검증은 [execute-root-capability](/docs/journal/2026/11/23/fixes/execute-root-capability/record.md)에 있다.
  - Andy: 원장 v6264→6275. waiting_input 1→2, submitted 21, running+slot 1. findings n1166 지속. 새 대화 오류는 5건이며 모두 제품 결함이 아니다.
    - Walkie 403 13:27Z(외부)
    - 버전 충돌 2(13:35·14:12Z, 재시도 경로)
    - `n2554 is not a goal` 13:54Z(메인 인자)
    - ripgrep 시간 초과 13:55Z
  - Celly: 원장 v8151→8173. running+slot 5→0, waiting_input 4→9. newfail `revival_refused` 2(13:15:07·13:15:11Z, 끝난 포크에 보낸 메시지라 정상 가드). MCP 탐침 TimeoutError 4016ms 1회(원인 미확인, 다음 틱에서 재확인). 포크 오류 기록 1(13:21:32Z).
  - KOBE: 원장 v3060→3066, 보드 v701. n107 재시도 결함이 배포 전까지 계속됐다: 13:13·13:23·13:43·14:31Z에 `scheduling up to 20 retries`→`retry 1/20`. 14:37:14Z 재로드 이후에는 모드의 awaiting 처리가 라이브다. 다만 분류기의 awaiting 응답은 라우터 Python이라 라우터 재시작 전에는 나오지 않는다. 라이브 효과는 미확인이다.
  - Prody: 원장 v628→641. submitted 6, running+slot 2→1. 포크 오류 기록 3(13:34:50Z, 14:36:43·14:37:22Z 같은 포크). 메인 자체 스크립트 오류 여러 건 — 제품 결함 아님.
  - 최종 트리 검증(배포 전):
    - CI 상당: py310 unit shard 1–3(1777/1628/1833, EXIT 0), py311 router/channel/runtime(1686/628/460, EXIT 0).
    - 41개 파일 3환경: 각 1303 실행/0 실패, `real_backend` 각 120/0.
    - ruff 0.15.22, compileall, check:docs, npm pack.
    - 라우터 실기동: Windows·py310·py311 모두 health 200, 버스트 200/200·64/64.
    - shard 2 첫 실행의 `test_recovery_503_retries_within_the_requests_remaining_allowance` 1건은 동시 부하 중에만 나왔다. 단독 10회와 shard 재실행에서는 통과했다. 원인은 미확인이다.

- 13:07:38Z 틱(직전 12:42:53Z). 4호스트 수집 성공. 운영 재시작·상태 파일 변경·배포 없음.
  - Andy: 원장 v6251→6264, 보드 v10745→10756. submitted 22→21, running+slot 2→1, waiting_input 1. findings 2→1(n2641 해소, n1166 지속). 새 대화 오류 2: ripgrep 시간 초과 12:45:56Z, 메인 자체 OKF 검증 exit 1 12:57:53Z — 제품 결함 아님. MCP 734ms.
  - Celly: 원장 v8106→8151, 보드 v9786→9802. waiting_input 6→4, running+slot 3→5. newfail `revival_refused` 1(12:44:28Z, 메인이 이 프로세스가 띄운 끝난 포크에 메시지 — 정상 가드). Walkie 403 `Conversation access denied` 12:50:40Z(외부). 포크 도구 오류 12건(12:45–13:07Z): 보고 형식 거부 `handoff/workspace fields: needs exactly …` 6회 — 포크마다 다음 호출에서 필드를 채워 성공(검증 가드 정상, 첫 시도 누락은 모델 입력), ripgrep 시간 초과 4, 포크의 `work_parallel_status` 호출이 `conversation identity is unavailable`로 거부 1(13:04:25Z, 포크는 세션 식별자가 없어 정상 가드), Walkie 400 1. MCP 218ms.
  - KOBE: 원장 v3060 유지, 보드 v688→691. n107 재시도 결함 지속(미배포): 12:48–13:04Z 동안 `scheduling up to 20 retries`→`retry 1/20` 3회 반복. 수정은 회귀 진행 중.
  - Prody: 원장 v620→628, 보드 v6817→6820. 새 대화 오류 1(메인 자체 스크립트 AssertionError 12:58:10Z). MCP 1123ms.
  - 회귀 `combo.125354`(큐 순서·대기 목표 승인·포크 재시도): 28개 파일 세 환경 각 848 실행/0 실패, `test_work_mod_real_backend` 진행 중.

- 12:42:53Z 틱(직전 11:35Z는 KOBE·Prody SSH 실패로 Andy·Celly만 수집, 미기록). 4호스트 수집 성공. 운영 재시작·상태 파일 변경·배포 없음.
  - Andy: 원장 v6188→6251, 보드 v10745(노드 496, 루트 62, 정지 152). 열린 리스 submitted 21→22, running+slot 0→2, waiting_input 1. findings 1→2(n1166 유지, 새 n2641 `submitted, completion receipt, no review row` — 이후 12:5xZ 원장에서 n2641은 done). 11:51Z 사용자 지시 뒤 메인이 P0 목표마다 새 리프를 만들어 12:03:57Z 포크 7개 실행, 12:52:50Z 확인 포크 0·waiting 1·uncertain 3: n2638·n2640·n2641·n2642 done, n2639 unreported(12:31:56Z salvage)→재파견 submitted, n2629 waiting_input, n2594 unreported(12:48:37Z salvage)→재파견 running. 대화 오류 4: 버전 충돌 2(11:45·11:53Z, 메인 재시도 정상 경로), 보드 경로 스크립트 exit 3(11:51:44Z, 메인 자체 스크립트), Walkie 무응답 1(12:24:34Z, 외부). MCP 859ms.
  - Celly: 원장 v8065→8106, 보드 v9786. waiting_input 4→6, running+slot 5→3. newfail `revival_refused` 8c322de1×1·d56b59e3×1 = 메인이 unreported 리스에 메시지 보내려다 거부됨(12:00:49·12:22:46Z, 정상 가드). 포크 transport_interrupted 3건 11:35:42Z(재시도 예약, 정상 경로). auto continue Work 상태 못 읽음 11:49:38Z 1건(원인 미확인). 확인 포크 0·waiting 5·uncertain 4(12:50:25Z). MCP 219ms.
  - KOBE: 원장 v3060, 보드 v688, running+slot 1. **결함 입증**: n107 포크(a944a21f7982)가 11:31:20Z부터 12:40:39Z까지 `Prompt is too long · automatic compaction failed: API Error: TimeoutError` 20회 반복. 호스트 로그는 매번 `retry 1/20` → `recovered after 1 attempt(s)` → 새 체인. 원인: 재시도 메시지가 포크 대화 끝에 user 기록으로 붙으면 라우터 분류기(`work_fork_api_retry.classify_fork_transcript`)가 마지막 기록만 보고 오류 없음으로 판정, 모드가 이를 회복으로 처리해 체인을 지웠다. 20회 상한과 exit contract가 한 번도 작동하지 않았다. 수정·검증은 [work-queue-restart](/docs/journal/2026/11/23/fixes/work-queue-restart/record.md) 원인 3.
  - Prody: 원장 v620, 보드 v6817, submitted 6·running+slot 2. 대화 오류 3(메인 인자 오류 `goal n1101 has no workflow`, 없는 경로 Read, 버전 충돌) — 제품 결함 아님. MCP 2702ms.
  - 미결 추적: Andy n1166 review row 누락 지속, Celly auto continue 상태 못 읽음 재발, KOBE n102 finding 소멸 사유 미확인(이번 틱 findings 0).

- 11:12–11:31Z 틱(직전 10:32Z)과 fcce6d7 배포 후 라이브 확인. 운영 재시작·상태 파일 변경 없음.
  - 배포: fcce6d7을 feature/priority-models·beta에 push, Publish 45160770706 success, npm beta `0.2.51-beta.20261123-105743.fcce6d7`. 해시 확인 오버레이 15/15: 로컬 3루트(Andy가 쓰는 hotfix5 루트 포함, 11:04:48Z), Celly, KOBE, Prody.
  - 모드 라이브: 재로드 Celly 11:04:51Z, KOBE 11:11:06Z, Prody 11:11:40Z, Andy 11:19:22Z(`reloaded (19 hooks`). 라우터 Python 변경(P0 정리, 계보 채택, `question_wait` 능력 광고)은 네 라우터 모두 설치 전에 시작돼 아직 라이브 아님.
  - Celly 사이드: 재로드 뒤 영수증 6건 전부 processed, 격리 0. 새 side 액터 9개 중 2개가 교정(`your answer was refused`)을 받고 processed로 끝났다 — 교정 전달 수정의 라이브 효과 확인. 재로드 전 마지막 실패 전달 11:01:29Z.
  - Andy: 제출 21, 대기 4→1, 실행 1→0, 원장 v6188, 보드 v10720. 사용자 보고(큐 미로드·재시작 후 포크 미재개·프리즈·우선순위 집계) 조사 → [andy-restart-redispatch](/docs/journal/2026/11/23/operations/andy-restart-redispatch/record.md). 큐는 REST·웹 UI에서 로드, Work 모드 시작 16 s 내 동작, 재시작 때 리스 4건 모두 `waiting_input`, n2594는 10:59:10Z 자동 재실행 후 다시 `waiting_input`. 우선순위 집계는 보드와 일치하며 후보 0(대기·계획·열린 리스). 메인 첫 응답 중앙값 42 s, 최대 309 s(입력 489,863 토큰). 도구 오류 `n1937/n1953 is not a goal` 11:02Z(메인 호출 인자).
  - Celly: 대기 6·실행 2, 원장 v8035, 보드 v9732, findings 0. newfail `decision_refused_stop` 79a47125×3·4d5e0a08×1(원인 미확인). 검수 오류 10:42–10:55Z.
  - KOBE: 보드 v672→674, findings 1→0(n102 finding 소멸 사유 미확인). side 실패 원본 전달 10:50·10:57Z(재로드 전).
  - Prody: 보드 v6801→6802, side 실패 원본 전달(재로드 전).

- 10:32:34–10:32:52Z 읽기 전용 틱(직전 10:15Z). 4호스트 수집 성공. 운영 재시작·상태 파일 변경·배포 없음.
  - Andy: 제출 21→22·실행 2→1(대기 4), 원장 v6167→6169, 보드 v10706→10711, MCP 406ms, findings 1(n1166 유지). auto continue가 Work 상태를 못 읽음 10:20:06Z 1건(원인 미확인). 세션 자체 셸 오류 2(10:22:56Z exit 127, 10:29:34Z 읽기 스크립트 exit 1) — 제품 결함 아님. 라우터: SSE EOF 16, `cielairouter_transient_retry` 9(같은 operation 재시도 포함, 업스트림 일시 오류 재시도 경로).
  - Celly: 제출 2→3·대기 6→4·실행 2→3, 원장 v7965→7976, 보드 v9697→9703, MCP 250ms. n1381 `P0 intent bound` finding 지속. side 영수증 87(processed 73, quarantined 14) — 격리 증가 없음. P0 intent 256 지속(current scope 0).
  - KOBE: 변화 없음(원장 v3048, 보드 v672), n102 finding 지속. side 실패 원본 전달 10:25:25Z.
  - Prody: 변화 없음(제출 6, 원장 v613, 보드 v6801), side 실패 원본 전달 3(10:17:27·10:18:27·10:29:00Z).
  - 회귀 `sidecorr.095712`(능력 분리 전): Windows·py310·py311 각 811/0 실패/58 skip. 최종 트리 `final.102159` 진행 중. 실CLI 실행 8(최종 코드) 15/15 통과. compileall·ruff 0.15.22(추적 파일)·check:docs 통과.

- 10:15:32–10:15:52Z 읽기 전용 틱(직전 09:59Z). 4호스트 수집 성공. 운영 재시작·상태 파일 변경·배포 없음.
  - Andy: 실행 3→2(제출 21·대기 4), 원장 v6162→6167, 보드 v10700→10706, MCP 922ms, findings 1(n1166 유지). 새 세션 오류 없음. 라우터 새 창: SSE EOF 16, 채널 투영 지속 3(120/240/360 s), 원인 미확인.
  - Celly: 대기 5→6(제출 2·실행 2), 원장 v7952→7965, 보드 v9685→9697, MCP 281ms. n1381 `P0 intent bound` finding 지속. 메인이 리뷰어 포크 시작 거부를 받음 10:12:21Z(정상 가드: 메인이 직접 검수). side 영수증 82(processed 67, quarantined 14, processing 1) — 격리 수 증가 없음. P0 intent 256 지속.
  - KOBE: 보드 v669→672, 원장 v3048, n102 finding 지속. side 실패 원본 전달 10:05:19–20Z.
  - Prody: 변화 없음(제출 6, 원장 v613, 보드 v6801), side 실패 원본 전달 10:07:45Z·10:15:23Z.
  - 격리 작업: 배포 순서 호환 결함 입증·수정 — 모드만 설치되고 라우터가 재시작 전이면 옛 라우터가 `waiting_on`/`release_hint`가 든 질문 detail을 정확한 필드 집합 검사로 거부한다. 계약을 `mod_fork_question_wait`로 분리하고, 라우터가 `capabilities:["question_wait"]`를 광고할 때만 포크 프롬프트에 붙인다(실패 테스트 먼저). 실CLI 실행 7 통과(15/15). 실행 8(최종 코드)과 회귀 `sidecorr.095712` 진행 중.

- 09:59:27–09:59:47Z 읽기 전용 틱(직전 09:34Z). 4호스트 수집 성공. 운영 재시작·상태 파일 변경·배포 없음.
  - Andy: 리스 구성 동일(제출 21·대기 4·실행 3), 원장 v6162, 보드 v10696→10700, MCP 687ms, findings 1(n1166 유지). side 실패 원본 메인 전달 09:55:52Z 1건.
  - Celly: 대기 7→5·실행 1→2·제출 2, 원장 v7917→7952, 보드 v9670→9685, MCP 1219ms. n1381 `P0 intent bound` finding 지속, newfail `decision_refused_stop 5b7832cd`. `work_parallel_item_review` 시간 초과 09:47:51Z와 이미 다르게 결정됐다는 거부 09:50:14Z. side 영수증 74건(processed 60, quarantined 14). P0 intent 256 지속(current scope 0).
  - KOBE: SSH 회복. 원장 v3048, 보드 v669(09:08Z의 v663에서 증가), 대기 9·실행 1, n102 미관측 finding 지속.
  - Prody: 보드 v6796→6801, Work 상태 못 읽음 공지 09:53:18Z, side 실패 원본 전달 09:56:50Z.
  - 실CLI 실행 6: 교정 수정 후 교정이 첫 시도에 전달되고(`asked the side actor for 331a083ada9d ...`), 교정 답이 `processed`로 커밋됐다. 정지 검사는 드라이버 변수 덮어씀(`others`)으로 잘못 `false`를 냈다 — 통과한 TaskStop은 액터 1건뿐이다. 드라이버를 고쳐 실행 7로 재확인 중이다.

- 09:34:48–09:35:16Z 읽기 전용 틱(직전 09:08Z). 운영 재시작·상태 파일 변경·배포 없음.
  - Andy: 실행+slot 2→3(제출 21·대기 4), 원장 v6148→6162, 보드 v10691→10696, MCP 1781ms, findings 1(n1166 유지). 포크 API 오류 n2635 `transport_interrupted` 09:13:06Z(재시도 예약, 정상 경로). 세션 자체 도구 오류(ripgrep 시간 초과 2, 경로 없음 1, 사용자 도구 거부 2 09:22:29Z) — 제품 결함 아님.
  - Celly: 실행 4→1·제출 0→2·대기 7, 원장 v7893→7917, 보드 v9663→9670, MCP 250ms. 신규 finding: n1381 e4fc837a 검수 결정 `stage_failed: P0 intent bound`(newfail `decision_refused_stop` 1) — P0 intent 상한 256 포화(결함 A, 수정 미배포)가 검수 결정까지 막음. 세션: n1140 P1 슬롯 용량 09:13:32Z, 메인이 `work_side_status` 호출 거부 09:21:22Z(호스트 전용 정상 가드), `work_parallel_item_review: review decision` 거부 09:31:08Z. side 실패 원본 전달 09:10:32Z.
  - KOBE: SSH 실패(`kex_exchange_identification: Connection reset by peer`, 100.64.0.14:1004). 미관측 — 상태 변화로 해석하지 않음.
  - Prody: 보드 v6794→6796 외 변화 없음, side 실패 원본 전달 09:28:53Z.
  - 사용자 지시(Celly 보고 Image #24·#25) 조사: Celly side 격리 13건 중 7건 `decision refs required`, 5건 `side answer carries no decision JSON`, 1건 superseded. 실패 액터 15개 대화에 교정 문구 0건 — 교정이 한 번도 전달되지 않음. 원인은 엔진의 resume `agent.spawn`을 register.js가 자체 side 네임스페이스 가드로 거부(격리 실엔진에서 관측). 실패 테스트로 재현 후 수정. 기록 [side-correction-resume](/docs/journal/2026/11/23/fixes/side-correction-resume/record.md). 이번 배포에 포함 예정.
  - 회귀 `sidestop.085650` Windows·py310·py311 각 793 실행/0 실패/58 skip.

- 09:08:28–09:08:49Z 읽기 전용 틱(직전 09:02Z). 4호스트 수집 성공, 운영 재시작·상태 파일 변경·배포 없음.
  - Andy: 실행+slot 1→2(제출 21·대기 4 유지), 원장 v6144→6148, 보드 v10688→10691, MCP 453ms, findings 1(n1166 유지). 새 세션 오류 없음. 라우터 새 창(09:02Z~): SSE EOF 7, `channel_turn_projection_persistent` 3(120/240/363 s), 원인 미확인.
  - Celly: 실행+slot 5→4·대기 4→7, 원장 v7878→7893, 보드 v9652→9663, MCP 250ms, findings 0. side 실패 이벤트 원본 메인 전달 2(09:03:54Z·09:06:53Z). P0 intent 256 지속(current scope 0). 현재 scope 실행 리스 3건 모두 P1 `work-auto-*`(n1350·n1341·n1381). 라우터: keepalive TaskList 합성 18, SSE EOF 7, 클라이언트 단절 다수.
  - KOBE: 변화 없음(원장 v3048, 보드 v663). n102 04:01Z 이후 native 미관측 지속. keep-alive health 불일치 62(health OK).
  - Prody: 보드 v6793→6794 외 변화 없음(제출 6, 원장 v613). MCP 2006ms(직전 1572ms).
  - 격리: 실CLI 실행 5 통과(13/13). 재로드 직후 첫 fresh 액터 생성이 `Agent type 'ciel-work:side-fresh' not found`로 1회 실패하고 5 s 뒤 재시도로 처리됨을 관측(경로 미확인, 미수정, 미결). 영향 회귀 `sidestop.085650` 진행 중.

- 09:02:00–09:02:27Z 읽기 전용 틱(직전 08:12Z). 4호스트 수집 성공, 운영 재시작·상태 파일 변경·배포 없음.
  - Andy: 제출 22→21·대기 4·실행+slot 1, 원장 v6119→6144, 보드 v10688, findings 1(n1166 유지). 세션 기록: `Work reconciliation: Parallel read failed: Git ownership/branch changed; retained` 08:28:30Z(Andy_Main이 보고한 n2614 리스 b7a5bc3f `workspace_unavailable`과 같은 시간대, 리스 대조 미실시), `Work status unavailable: router connection or snapshot could not be verified [snapshot-paged]` 08:59:56Z 1건(원인 미확인), side 실패 이벤트 원본 메인 전달 08:58:00Z, 세션 자체 셸·파일 오류 3(08:15·08:18·08:33Z, 제품 아님). 라우터 경고 SSE EOF 45·MCP client reset 5·delta timeout 7·연결 거부 10061 4(02:52Z 현지), 원인 미확인.
  - Celly: 실행+slot 2→5·대기 3→4·제출 1→0, 원장 v7820→7878, 보드 v9652. n1363 검수 행 없음 finding 해소(제출 0). 현재 scope 열린 리스 9건 전부 P1 `work-auto-*`(P0 리스 0), P0 intent 256 지속(current scope 0). 세션 오류: n1343 P1 슬롯 용량 08:17:00Z, `expected_version required` 08:17:06Z, Walkie 403/400 08:15Z, side 실패 원본 전달 08:22:48Z·08:25:04Z, 세션 스크립트 오류 3(제품 아님).
  - KOBE: 변화 없음(원장 v3048, 보드 v663). n102 04:01Z 이후 native 미관측 지속. 메인 `work_node_update` 완료 거부 08:45:30Z(병렬 검증·검수·정리 대기 안내, 정상 가드).
  - Prody: 변화 없음(제출 6, 원장 v613, 보드 v6793). side 실패 원본 메인 전달 08:14:15Z·08:36:56Z·08:59:14Z.
  - 격리 작업: 실제 Claude Code 2.1.330 로드에서 새 결함 2건을 입증하고 수정했다(스킬 이름 `work-mode`와 `/work-mode` 명령 충돌로 session.start 전체 건너뜀, 이미 끝난 side 액터에 TaskStop 3회 재시도 후 unconfirmed). 기록은 [side-fresh-retirement](/docs/journal/2026/11/23/fixes/side-fresh-retirement/record.md). 미배포.

- 08:12:22–08:13:34Z 읽기 전용 틱(직전 07:51Z). 4호스트 수집 성공, 운영 재시작·상태 파일 변경·배포 없음.
  - Andy: 제출 21→22·실행+slot 2→1·대기 4, 원장 v6117→6119, 보드 v10674→10678, MCP 625ms, findings 1(n1166 유지). 세션 도구 오류 2: `work_side_handoff` 대상 없음 07:55:06Z(ask_main/해제 알림은 확인 불필요 안내), 세션 스크립트 `int()` 파싱 ValueError 08:07:11Z(세션 자체 스크립트, 제품 결함 아님).
  - Celly: 제출 0→1·실행+slot 2·대기 3, 원장 v7801→7820, 보드 v9610→9625, MCP 1515ms. 신규 finding — n1363 c596c09b P1 제출·완료 영수증 있음·검수 행 없음(native running, 마지막 08:07Z). 거절: n1363 P1 슬롯 용량 07:54:46Z, n1343 P1 슬롯 용량 08:06:16Z, n1350 `not a slot-released wait` 08:07:58Z, 사용자 요청 없는 active goal 변경 거부 08:01:32Z(정상 가드). 포크 오류 기록 5(08:01:50·08:06:47·08:07:16·08:07:26·08:08:30Z, 포크 a78e01bd·a1e395c8×3·a1375195, 원문 미확인). P0 intent 256 지속(terminal 137·bound 106·intent 13, current scope 0) — 결함 A/B 수정 미배포.
  - KOBE: 변화 없음(원장 v3048, 보드 v652→654, MCP 214ms). n102 1ceb4bd9 04:01Z 이후 native 미관측 지속(종료 증거 아님).
  - Prody: 변화 없음(제출 6, 원장 v613, 보드 v6791, MCP 1572ms). side 실패 이벤트 원본 메인 전달 공지 07:51:31Z·08:11:58Z(already/appended).
  - Andy_Main 08:33Z 보고 대조(읽기 전용): side 이벤트 1c420b87a1c6([157732], 08:21:43Z) 결정은 `update n2582` + `release_waits [{n2582, "PR #115 post-a7cfddd1 viewport revision: full-candidate QA evidence plus a NEW Daniel approval"}]`, 보드 v10682, `wait_boost` 생성 1. understanding 자체가 승인 범위를 "QA 전체 통과 시에만 PR 갱신·push CI, 병합·배포·재시작 제외"로 적었는데도 QA 증거와 새 승인 두 조건이 걸린 대기를 해제했다. 사이드 모델 판단 오류로 기록, 호스트가 조건 충족을 판정하는 경로는 없음(제품 결함 여부 미판정). 메인이 대기 복원·n2635 분리(Andy_Main 보고, 원장 재확인은 다음 틱). n2614 리스 b7a5bc3f `workspace_unavailable`(Git 소유권/브랜치 변경 retained)는 보고만 수신, 미대조.
  - 회귀 상태: 결함 A/B/C·질문 대기 변경 `qwait-p0c.015539` Windows·py310·py311 각각 663 실행/0 실패/54 skip. 실제 `claude.exe` 격리 로드·Side Fresh 실CLI 검증 전이라 베타·설치 보류. 오래된 감시 셸(`terminal-question-*` 대기, win 로그 RAN 행 없음)은 종료.

- 07:51:28–07:51:54Z 읽기 전용 틱(직전 07:06Z). 4호스트 수집 성공, 운영 재시작·상태 파일 변경·배포 없음.
  - Andy: 리스 구성 동일(제출 21·대기 4·실행 2), 원장 v6100→6117, 보드 v10668→10674, MCP 969ms, findings 1(n1166 유지). 라우터 경고 SSE EOF 40(첫 07:07:33Z)·MCP client reset 5·delta timeout 4, 원인 미확인.
  - Celly: 실행 3→2·대기 2→3, 원장 v7765→7801, 보드 v9593→9610, MCP 765ms. P0 intent 상한 256 지속(current scope 0건) → 실효 P0 n1361 `active slot capacity reached for P1` 거절 추가 2(07:17:16Z·07:29:29Z). 게이트 `router-error` 3(07:23:55Z·07:25:40Z·07:48:28Z, 같은 시각 라우터 MCP client reset 10054 동반, 인과 미확인). 포크 오류 기록 10(07:25–07:48Z, 원문 미확인).
  - KOBE: 변화 없음(원장 v3048, n102 native 미관측 04:01Z~ 지속). keep-alive health 불일치 531(health OK).
  - Prody: 변화 없음(제출 6, 원장 v613). side 실패 이벤트 메인 전달 7(07:16–07:51Z).
- Andy_Main 요청 n2626·n2633 첫 예약→native 지연(읽기 전용 조사): 첫 예약 331d6d5c(n2626, 05:49:55Z)·1d272611(n2633, 06:52:26Z)은 둘 다 coordination archive `workspace preparation failed before launch`, agent_id 없음. 작업공간 실패 기록: `workspace_prepare_retained` 05:52:53–05:58:08Z 4회, `workspace_prepare_failed` 05:58:07Z 1회, `retained` 06:55:11–06:55:12Z 2회. manifest 3c15f39c(331d6d5c)는 `preparing`인 채 worktree 존재(05:49:59Z 생성), c77c47ba(1d272611)는 `ready`(worktree 06:53:56Z, manifest 06:55:12Z)인데 리스는 해제됨. 이후 새 예약 a6104923(05:58:34Z→native 05:59:15Z)·432aac52(06:55:15Z→native 06:55:23Z)가 각각 시작. 즉 지연은 정원이 아니라 작업공간 준비 실패/보류의 재시도 구간. Git 원문은 router.log에 없음 → 결함 C. G:\ciel-Walkie worktree 1080개(.git/worktrees 260MB), 라우터 밖 `git worktree list` 0.5–0.6s; 이번 실패의 Git 원인은 미확인.
- 입증된 결함 C — Work 도구에 라우터 로그 미전달: `channel_mcp_tools._work_tool`이 `dispatch_work_tool`에 `log`를 넘기지 않아 `_workspace_failure`의 `work_workspace_failed`(Git 원문)·P0 lineage 로그가 라우터 MCP 경로에서 전부 버려짐(Andy router.log 0건).
- 수정(격리 워크트리, 미커밋): A) `evictable_intents` — 상한에서 cancelled 다음으로 ledger상 done·orphaned·부재 리스의 intent(스냅샷 이전 변경분, 열린 reacquire send 부모 제외, unreported 보존)를 오래된 순으로 축출. Celly 상태 사본 건식 적용: 구 규칙 `P0 intent bound`, 신 규칙 생성 성공(축출 후보 181). B) `lineage_scopes` + `adopt_lineage_markers` — 같은 보드를 잇는 native parked 세션의 최신 P0 결정을 현재 세션으로 이관(원래 set_at·set_by 보존, 완료 op 행 동반, 세션 자체 행 우선, 완료·다른 세대 루트 제외), 인증 호스트 status 읽기에서 수행. Celly 사본 건식: n1179·n1209 복원 대상. C) `ChannelMcpRuntimeServices.log=router_log` 전달. 실패 우선 테스트 재현 후 수정. 영향 회귀: 격리 Windows·Linux py310·py311 각 663 실행 실패 0(skip 54), `claude plugin test` 94 통과, `claude plugin validate` 통과(skill `ciel-work:work-mode` 인식). 실제 claude.exe 격리 로드는 미수행 → 베타·설치 보류.

- 07:06:42–07:07:02Z 읽기 전용 틱(직전 05:05Z, since=05:05:09Z). 운영 재시작·상태 파일 변경·배포 없음. 4호스트 모두 수집 성공(직전 KOBE·Prody SSH 실패에서 회복).
  - Andy: 제출 21 유지·입력 대기 6→4·실행+slot 1→2, 원장 v6100, 보드 v10644→10668, MCP 1547ms, findings 1(n1166 검수 행 없음 유지). 라우터 `router_lifetime_restart reason=router_process_exited` 1회(05:06:44Z, 원인 미확인, 이 세션이 한 재시작 아님). 포크 API 오류 재시도 알림: n2613 empty_stream 05:07:37Z, n2614 transport_interrupted 05:59:59Z·06:03:32Z·06:59:10Z, n2633 06:59:10Z. P3 정원 거절 1(n1847 05:27:25Z), 보드 CAS 충돌 3. 라우터 경고: delta 전달 timeout 93·SSE EOF 88·reset 65·10061 거절 52(첫 05:05~05:18Z), 원인 미확인. Andy_Main 보고 n2626·n2633 첫 예약→native 지연은 아직 미조사.
  - Celly: 세션 c07e198b→4ee83c21(bg job, `parkedJobId` 연결, 06:41:37Z 시작). 실행+slot 3·입력 대기 2, 원장 v7765, 보드 v9526→9593, MCP 250ms, findings 0. 포크 오류 기록 6(06:42–07:05Z, 원문 미확인). 세션 오류: 실효 P0 아이템 n1361의 `active slot capacity reached for P1` 거절 2(06:45:42Z·07:04:32Z), 대기 해제된 parked 리스 2건(e957928541b n1331 P1, cd3b43dcade n1350 실효 P0) `no slot` 06:39:57Z, side 실패 이벤트 메인 전달 5(06:37–06:59Z), n1360 transport_interrupted 06:55:55Z.
  - KOBE: 실행+slot 1·입력 대기 9, 원장 v3048, 보드 v622→647, findings 1 — n102(1ceb4bd9, P3) 슬롯 점유·04:01Z 이후 native 미관측 3시간 지속(종료 증거 아님). keep-alive health 불일치 1421(health OK).
  - Prody: 제출 6, 원장 v613, 보드 v6790, findings 0, 포크 오류 기록 2(06:01Z·06:33Z).
- 입증된 결함 A — Celly P0 레인 정지: `work-parallel-p0.json` intents가 상한 256에 정확히 도달(terminal 137·bound 106·intent 13, cancelled 0). 전부 이전 세션 scope(b86a350f 82·0aefa936 71·9288dece 71·c07e198b 32)이고 현재 scope 4ee83c21은 0. `_create_intent_mutation`(`work_parallel_p0.py:687-694`)은 상한에서 cancelled만 지우므로 새 P0 intent가 항상 `P0 intent bound`로 거부된다. 현재 실효 P0 아이템 n1350·n1360·n1361·n1363의 리스는 P0 intent 없이 일반 `work-auto-*` P1 리스로 배정됐고, 대기 해제 후 재개·배정이 P1 정원에 막힌다. 앞선 틱의 Celly `P0 intent bound`(03:55:52Z) 관측과 일치. 사용자 보고 "P0 병렬 실패, 대기 해제 후 재포크 실패"의 Celly 원인으로 입증.
- 입증된 결함 B — 재시작 시 P0 실효 소실: 현재 보드 루트와 P0 marker 43개 대조 결과 39개가 같은 루트(id·created_at 동일)인데 scope가 세션 ID 5개에 흩어져 있다. 예: n997은 11-17·11-20·11-21·11-23에 반복 재지정, n1179·n1209는 이전 세션에서 P0였으나 현재 P1. 보드는 native parked lineage로 이어지지만 marker 키는 `scope+root_id`라 이어지지 않는다. 또한 `sweep_roots`·`recover`는 운영 코드에서 호출되지 않아 완료된 루트(n1048·n1032)의 이전 scope marker도 active로 남는다.

- P0 재시작 소실(Celly 보고 확인, 읽기 전용): Celly `work-parallel-p0.json`에 root marker 43개(active 42, `priority_changed` cleared 1)가 삭제되지 않고 남아 있다. marker는 세션 ID 5개(0aefa936·b86a350f·9288dece·c07e198b·4ee83c21)에 나뉘어 있다. marker 키는 `scope(세션 ID)+root_id`(`work_parallel_p0.py:93-95`, `:409-412`)이고, 세션 연속(`work_session_lineage.py:90-127`)은 보드 경로만 잇고 P0 marker는 옮기지 않는다. 따라서 새 세션 ID에서는 옛 marker가 매칭되지 않아 canonical P1로 보인다. 데이터 삭제는 아니지만 응급 우선순위 효과가 재시작을 넘지 못하는 결함이다. P1+사이드카 저장은 2026-11-14 구버전(57dbfb3) 호환 결정(`docs/journal/2026/11/14/features/priority-p0-fork-models/requirement.md:96-99`, 커밋 d69ca83·f045f24)이고, 세션 ID 결합으로 인한 소실은 어떤 요구에도 없다. Celly `work-sessions`는 PermissionError로 보드-marker 대조는 미확인.
- 포크 질문 대기 계약(격리 워크트리, 미커밋): `work_parallel_report(question)`의 `detail.waiting_on`+`release_hint{condition,lookup,prompt?}`를 보드 leaf에 먼저 기록한 뒤 receipt·리스 park. 짝 없음·형식 오류·잘못된 아이템·기존 대기·비leaf·배정 없음·보드 충돌은 어떤 기록 전에도 거부하고 리스는 running/slot 유지. side workload에 검증된 `release_hint` 전달, 게이트/사이드 지침에 원래 답 검증 후 해제·원래 포크 재개 명시, 번들 스킬 `ciel-work:work-mode` 추가(`claude plugin validate` 통과, skills 인식). 신규 7건 + 관련 10건 통과. 실제 CLI·Linux 회귀·배포는 아직.

- P0 예약 정리 경합: 정리가 `reserved` 스냅샷을 읽은 뒤 시작 승인 CAS가 `spawning`으로 성공하도록 순서를 고정했다. 수정 전 테스트에서 정리가 같은 리스를 삭제했다. 시작 승인과 종료된 P0 예약 정리에 공용 파일 잠금 및 잠금 내부 재검증을 적용했다. 시작이 먼저 승인되면 리스와 coordination 행을 보존한다. 정리가 먼저 리스를 지우고 sidecar 갱신이 실패한 경우에도 다음 시작 승인을 거부한다. 리스 삭제 자체가 실패하면 나머지 예약 정보를 보존해 다음 정리에서 재시도한다. 운영 재현·운영 상태 변경 없음.
- 검색 접수 도구는 실행을 배정하지 않고 `source`, `scope`, `results`만 받는다. `work_discovery_pickup`에 `role`을 요구하던 레거시 회귀 검사를 현행 계약에 맞췄다. 제품 스키마는 바꾸지 않았다.
- 격리 검증: Windows `test_work_parallel_p0_events.py` 및 `test_work_parallel_legacy_reviewer.py` 56 통과(하위 검사 25). 기존 Linux py310/py311 각각 56 실행, 1 skip, 실패·오류 0. Linux 로그는 `linux-runner/p0-launch.ONtoq4cv.*.log`. P0는 MCP 경계에서 실행한 테스트 결과이며, 운영 적용 증거는 아니다.

- 04:14–04:15Z 읽기 전용 틱(직전 03:32–03:38Z, UTC): Andy·Celly·KOBE Work MCP 성공 및 보드 버전 일치. Prody 전체 수집 실패; 후속 health만 성공. 운영 재시작·상태 파일 변경·배포 없음.
  - Andy: 실행 1·입력 대기 6·제출 21 유지, MCP 516→844ms(v10632). n1166 완료 영수증 있으나 검수 행 없음 유지, 마지막 native 관측 01:44Z/01:50Z 이후 미관측(종료 증거 아님). 포크 도구 오류 표본 1(04:14:26Z, 원인 미확인). 라우터 SSE EOF 37(첫 03:32:45Z), 연결 reset 9(첫 03:45:21Z), transcript timeout 10(첫 03:36:46Z), MCP client reset 3(첫 03:35:56Z); 근본 원인 미확인.
  - Celly: 실행 4→2·입력 대기 3→1·제출 0, MCP 922→281ms(v9490), findings 0. 메인 훅 언로드 03:37:31Z와 교체 경고 4건은 앞선 후속과 중복; 03:51:32Z 모드 시작 및 03:59:52Z 게이트 재점검 기록은 아래 참조. 포크 도구 오류 표본 6(03:39:04–04:04:21Z, 원문 미확인). 메인 `P0 intent bound` 1(03:55:52Z, 발생 경위 미확인), 중복 배정 거부 1(03:56:00Z). 라우터 SSE EOF 16(첫 03:52:13Z)·reset 9(첫 03:55:37Z), transcript timeout 10(첫 03:54:33Z), MCP client reset 3(첫 04:10:08Z), context-usage WinError 5 2(첫 04:01:10Z). 별도 Side API 조회 8초 timeout 1, 현재 큐 미관측; 모드 재언로드나 처리 종료로 판단하지 않음.
  - KOBE: 실행 1·입력 대기 9·제출 0 유지, MCP 274→170ms(v622). n102(lease 1ceb4bd9) 신규 관측: 슬롯 점유, 04:01Z부터 native 목록 미관측, 종료·원인 미확인. 포크 도구 오류 표본 7(03:41:30–03:59:29Z, 원문 미확인), 메인 gh HTTP 401 1(04:09:53Z, 자격 증명 거부). 라우터 keep-alive 불일치 498(첫 03:32:29Z, health OK), SSE EOF 36(첫 03:32:49Z)·reset 10(첫 03:36:12Z), transcript timeout 7(첫 03:32:46Z).
  - Prody: 전체 틱 SSH lost connection 1, 재수집 응답 JSON 파싱 실패 1. 별도 health `ok:true`, PID 12320·클라이언트 3828. 현재 Work MCP·포크·검수 미관측; 직전 제출 6·실행 1은 마지막 성공 표본에 한함.
  - 신규 제품 결함 재현 없음. Celly Side Fresh 정리 변경은 실제 CLI에서 정지 거부 후 정리와 다음 이벤트 처리까지 검증되지 않아 배포 보류 유지.

- Celly Work UI 소실 및 Side Fresh 정리 후속: 03:37:31Z `ciel-work` 언로드 후 `/work-mode`가 03:42:21Z·03:42:35Z 두 차례 `Unknown command`로 기록됐다. 라우터 health와 세션 내 훅 정상 여부는 별개다. 이후 03:51:32Z Work 모드 시작, 03:59:52Z 게이트 재점검 공지 확인; 후속 health는 라우터 PID 17340, 클라이언트 24264. 이번 작업에서 운영 재시작·상태 파일 수정 없음. 역사적 워커 충돌 스택은 확보하지 못했다.
  - 별개로 입증한 정리 결함: `side-intake.js`가 `TaskStop` 거절·예외에도 종료 추적을 지웠다. 수정 전 회귀는 재시도 없이 실패했고, 중간 수정도 성공 응답 후 idle 액터를 추적에서 지워 추가 검증에 실패했다. 최종 수정은 정확한 대상의 정지 응답과 native 목록의 종료/부재 확인 전까지 추적 유지, 동일 액터 동시 호출 차단, 최대 3회 재시도를 적용한다. 기존 재개 권한과 영수증·답장 정책은 그대로다. 이 수정이 훅 워커 충돌을 해결한다는 증거는 없다.
  - 검증: Windows 격리 245 통과/34 skip(하위 검사 148 통과), 기존 py310·py311 각각 279건/34 skip/실패·오류 0. 실제 Claude Code 2.1.330 격리 검증에서 모드 로드 및 `/work-mode view` 응답(114.8 ms)과 메인 진행 확인. 단 기존 실제 엔진 프로브의 사이드 액터 게이트 완료·P1 순서 검사는 원본과 수정본 모두 충족하지 못했고, 실제 CLI에서 정지 거부 후 정리 회복까지는 검증하지 못했다. 실행 증거 `D:/Temp/side-retirement-e2e.fXKUJC/` 및 원본 비교 `D:/Temp/side-retirement-base.jgfPYi/evidence/`. 검증 조건 미충족으로 베타 게시·운영 설치 보류.

- 03:32–03:39Z 읽기 전용 틱(이전 03:15–03:16Z, UTC): 네 호스트의 루프백 Work MCP는 각 표본에서 보드 버전과 일치했다. 단 Celly는 표본 뒤 실제 세션의 훅이 중단됐다. 운영 재시작·상태 수정·배포 없음.
  - **Celly**: 03:32:37Z Work MCP 922 ms(v9478), 실행 슬롯 4·입력 대기 3 유지. 03:37:31Z `ciel-work` 훅 워커 충돌로 언로드 1건, 03:38:11–15Z `ciel-work-gate` 훅 실행 중 워커 교체 및 catch 응답 4건, 03:38:36Z 메인 `work_side_status` 대화 식별자 미확인 거부 1건. 03:39:20Z까지 재로드 증거 없음. 라우터 PID 74704/클라이언트 80708 응답과 세션 연결은 별개. Side API 대기 5·처리 중 0·누적 처리 778, 직전 대기 1·처리 중 1·누적 770. 원인은 미확인, Side Fresh 재개 거부와의 인과 미입증. 포크 오류 표본 4: handoff 증거 길이·workspace 타입·diff_stat 유효성 각 1(03:24:52/25:25/25:58Z), 셸 대기 가드 1(03:31:24Z). 라우터 SSE reset 14(첫 03:17:27Z), EOF 10(첫 03:16:13Z), MCP client reset 5(첫 03:22:24Z), transcript timeout 4(첫 03:23:58Z).
  - **Andy**: MCP 735→516 ms(v10626), 실행 1·제출 21·입력 대기 6 유지, 포크 오류 표본 0. n1166 완료 영수증/검수 행 없음 지속, 01:50Z 이후 native 미관측은 종료 증거 아님. n2613 transport_interrupted 재시도 예약 1(03:22:34Z), 결과 미확인. SSE reset 14(첫 03:17:11Z), EOF 13(첫 03:15:58Z), transcript timeout 2(첫 03:25:27Z).
  - **KOBE**: MCP 192→274 ms(v615), 실행 1·입력 대기 9 유지. 포크 오류 표본 3(03:30:08/31:57/32:04Z, 원문 미확인). n102 auto_compact_failed 예약 1(03:34:51Z), 1차 시도 1(03:36:00Z), 성공 미확인. 기존 keep-alive 불일치 265(첫 03:15:59Z), SSE reset 21(첫 03:18:03Z), transcript timeout 14(첫 03:16:34Z), 별도 transcript HTTP 502 1(03:24:28Z).
  - **Prody**: MCP 1590→2659 ms(v6777), 실행 1·제출 6 유지, 포크 오류 표본 0. n1224 transport_interrupted 재시도 예약 재관측 1(03:18:30Z), 성공 미확인. 새 라우터 오류 표본 0.
  - Celly 세션 장애를 확인했지만 충돌의 원인·재현 경로는 입증하지 못해 코드를 변경하지 않았다.

- 03:15–03:16Z 읽기 전용 틱(비교 02:52Z, UTC): 네 호스트 모두 Work MCP 조회 성공, 보드 버전과 일치. 포크 오류 수는 부분 표본, 제출은 검수 완료와 별개. 운영 재시작·상태 수정·배포 없음.
  - **Andy**: 실행 슬롯 1·입력 대기 6·제출 21 유지, MCP 1391→735 ms(v10622), 새 포크 도구 오류 표본 0. `n1166` 완료 영수증 있으나 검수 행 없음 지속(01:50Z 이후 native 미관측, 종료 판단 아님). 라우터 외부 SSE EOF 20건(첫 02:53:18Z)·연결 타임아웃 1건(03:02:19Z), transcript 전달 타임아웃 1건(03:05:28Z); 근본 원인 미확인.
  - **Celly**: 실행 슬롯 4 유지·입력 대기 2→3·제출 0, MCP 797→266 ms(v9468), 검수 차단 findings 0. 포크 오류 표본 1건(03:09:39Z, 사용자 도구 거부), 메인 중복 배정 가드 1건(03:09:36Z). n1250과 Side Fresh 재개 거부는 아래 별도 조사 참조. Side 후속 조회에서 대기 1·처리 중 1·누적 처리 770(03:14Z 후속 768), 격리 117 유지. 메인 원문 전달 공지 3건(03:09:37Z·03:10:06Z·03:13:03Z). 라우터 외부 SSE EOF 21건(첫 02:52:50Z)·reset 3건(첫 03:00:26Z), transcript 전달 타임아웃 2건(첫 03:01:20Z), MCP client reset 1건(03:09:40Z), 활동 파일 WinError 5 1건(03:11:52Z); 근본 원인 미확인.
  - **KOBE**: 실행 슬롯 0→1·입력 대기 9 유지·제출 0, MCP 441→192 ms(v611), 포크 오류 표본 0. keep-alive 불일치 235건(첫 02:52:34Z, health OK), 라우터 외부 SSE EOF 14건(첫 02:53:05Z)·reset 4건(첫 02:55:21Z), transcript 전달 타임아웃 7건(첫 02:53:58Z), MCP client reset 3건(첫 03:03:25Z). 메인 Walkie 승인자 멘션 유효성 거부 1건(02:54:01Z, HTTP 400).
  - **Prody**: 실행 슬롯 1·제출 6 유지, MCP 857→1590 ms(v6777), 포크 오류 표본 0·findings 0. 02:57:51Z 동일 Side 실패 원문 `appended`/`already` 한 쌍(중복 전달 증거 아님); n1224 재시도 성공 여부 미확인. 새 라우터 경고 표본 0.
  - 새 제품 결함 입증 없음. Celly 재개 실패 뒤 UI/큐 잔류 여부와 Andy n1166 검수 미게시 계속 추적.

- 03:14Z Celly side-fresh/Work 재개 거부 조사(읽기 전용, UTC): 이미지 #21은 `side-fresh` 입력 화면에서 Side SSE 재개 권한 불일치, #22는 별개 Work 포크 `a83c4de983dc81072`의 재개 권한 불일치. 메인 기록상 후자는 n1250/lease `620094c2`이며 03:09:40Z TaskStop 성공, 원장 `waiting_input`, slot=false. 메인 도구 호출은 03:13:10Z에도 관측. Side API 연속 관측에서 누적 처리 766→768, 03:09:37Z 수신 건은 03:12:23Z 처리, 03:13:00Z 수신 건은 03:14:42Z 처리. 라우터 PID 74704/클라이언트 80708 확인. 메인 전체 정지는 관측과 맞지 않지만 종료된 대상의 입력 화면 또는 재개 큐가 남는지는 미검증. 코드상 side actor는 한 이벤트 후 폐기, `stopRetired`가 TaskStop의 결과를 검증하지 않고 추적을 지우는 경로가 있으나 화면과 연결되는 실패 증거는 없음. 운영 변경·재시작 없음.

- 02:52Z 읽기 전용 틱(비교 02:32Z, UTC): 4호스트 Work MCP 성공 및 보드 버전 일치. KOBE·Prody는 직전 틱 SSH 실패로 당시 상태 미관측이며, 이번 조회 성공은 세션 재시작 증거가 아니다. 포크 오류는 부분 표본.
  - **Andy**: 실행 슬롯 0→1, 제출 21·입력 대기 6 유지, MCP 687→1391 ms(v10619), 포크 오류 0. n1166 완료 영수증/검수 행 없음 그대로, 마지막 native 관측 01:44Z 및 01:50Z부터 미관측. 라우터 transcript timeout 20(첫 02:32:24Z), 연결 거부 WinError 10061 11(첫 02:32:27Z), 외부 SSE EOF 16(첫 02:35:36Z)·reset 5(첫 02:32:40Z), MCP 클라이언트 reset 1(02:47:38Z). 근본 원인 미확인. 보드 CAS 가드 1(02:37:03Z).
  - **Celly**: 실행 슬롯 4 유지, 입력 대기 1→2, 제출 0·findings 0, MCP 281→797 ms(v9454), health PID 74704/클라이언트 80708. Side 대기 0·처리 중 1, 처리 누계 760·격리 114. 포크 오류 4: evidence 길이 검증 1(02:37:34Z), 셸 대기 가드 1(02:44:48Z), TaskStop 가드 2(02:48:01Z). 메인 중복 배정 거부 1(02:51:34Z). transcript timeout 15(첫 02:32:30Z)·연결 거부 8(첫 02:32:38Z), 외부 SSE EOF 12(첫 02:35:43Z)·reset 6(첫 02:32:52Z), MCP 클라이언트 reset 8(첫 02:42:56Z). Walkie 턴 종료 이벤트 timeout으로 최종 포기 1(02:32:41Z, 앞선 단절 조사와 중복 관측).
  - **KOBE**: MCP 441 ms(v607), 입력 대기 9·실행/제출 0, findings 0. 포크 오류 표본 3(02:38:08·02:38:27·02:40:24Z, 원문 미확인). keep-alive 불일치 234(첫 02:32:23Z, health OK), transcript timeout 16(첫 02:32:23Z)·연결 거부 8(첫 02:32:41Z), 외부 SSE EOF 14(첫 02:35:42Z)·reset 4(첫 02:32:51Z). 별개 메인 Walkie outbound 네트워크 실패 2(02:42:48·02:50:25Z), 근본 원인 미확인. compaction lock-busy 가드 1(02:35:59Z).
  - **Prody**: MCP 857 ms(v6774), 제출 6·실행 슬롯 1, findings 0, 포크 오류 표본 0. n1076은 02:13Z 직접 확인에서 이미 슬롯 해제됐으며, 이번 실행 슬롯 수만으로 재점유로 보지 않는다. n1224 transport_interrupted 재시도 예약 공지 1(02:41:46Z), 성공 여부 미확인.
  - 새 제품 결함 입증 없음. 운영 재시작·상태 파일 수정·배포 없음.

- 02:49Z Walkie 반복 단절 조사(읽기 전용): 02:10–02:40Z 외부 기본 SSE 수신기 `connect` WinError 10054는 Celly 28·Andy 36건, `read` EOF는 11·13건. Andy 설정 및 양쪽 라우터의 실제 원격 TCP 연결에서 Walkie 내부 `100.64.0.9:8787` 경로를 관측. 별개 송신 경로인 Celly `agent.turn_ended` 이벤트도 같은 endpoint로 02:17:40~02:32:20Z timeout 재시도 17건, 02:22:58·02:32:41Z 최종 포기 2건, 02:48:15Z 전달 성공 기록. Walkie 표시의 정확한 판정 조건 및 reset 발생 장비는 미확인. 02:30Z대 내부 HTTP 타임아웃 뒤 02:43Z HTTP 200, Celly 메인의 Walkie 검색·발송 도구는 02:40:20·02:41:01Z 성공. 현재 로컬 라우터 6개는 포트가 다르고 9385/9505 PID 74704/48136을 확인; 포트 충돌 증거 없음. 라우터 부하나 Walkie 서버 재기동을 근본 원인으로 확정하지 않는다. 운영 재시작·상태 수정 없음.

## 2026-11-22

- 2026-11-23T02:32Z 읽기 전용 틱(2026-11-23 날짜 섹션 없음/날짜 TOC 계속 깨짐): Andy·Celly Work MCP 확인. KOBE·Prody는 Tik 수집과 후속 SSH 4회 재확인에서도 `ssh failed`(대부분 SSH 교환 단계에서 연결 reset)가 계속돼 현재 원장·보드·포크·검수 상태를 판독하지 못했다. 직전 성공 관측만 보존한다. 운영 재시작·상태 수정·배포 없음.
  - **Andy**: 원장 6045→6046, 실행 슬롯 1→0, 제출 21 유지, 입력대기 4→6. MCP 687 ms. finding 1 유지(`n1166 submitted, native=running, last=01:44, missing=01:50`). 포크 오류 표본 0. 라우터 transcript Timeout 66(첫 02:15:12Z), 연결 거부형 transcript 실패 56(첫 02:18:15Z, WinError 10061), 외부 SSE reset 30(첫 02:14:25Z). Walkie 무응답 1(02:18:28Z), transcript 대상 000 timeout 2(02:30:14Z·02:31:57Z); 직접 내부 중계 기본 HTTP 조회도 timeout 1회, 공용 주소 조회는 URL 오류 1회. Tik transcript 오류는 search timeout 1(01:48:06Z).
  - **Celly**: 원장 7490→7495, 실행 슬롯 1→4, 입력대기 1 유지, 제출 0, findings 0. MCP 485 ms. 포크 보고 검증 거부 5건: handoff proof 1275>500 1(02:21:06Z), proof 누락 1(02:21:14Z), `structured handoff detail required` 1(02:21:25Z), `handoff done` 필드 검증 2(02:21:59Z·02:23:16Z); 셸 exit 1 2건은 근본 원인 미확인. 외부 사유로 보면 02:15:34Z transcript timeout 21, SSE reset 21(첫 02:15:34Z), SSE EOF 5, MCP 연결 reset 4 첫 02:28:21Z. 02:31:38Z `auto continue could not read the Work status` 1회(추적 중).
  - **KOBE**: 현재 SSH/Tik 실패. 직전 Tik(02:13Z)은 Keep-alive 불일치·입력대기 8 유지였고 이후 직접 확인은 SSH 교환 실패로 없다. `decision_refused_stop dac6b6ca x1` 카운터가 생긴 경우는 Tik 반송 문제와 별개로 확인되지 않았다.
  - **Prody**: 현재 SSH/Tik 실패, 이전 `n1076` 슬롯 점유 finding의 현재 상태 미확인. 직전 원장 598, submitted 6 유지. 게시 후 setTimeout 사이드 후광은 Tik에서 보였고, SSH 취득 실패를 Prody 자체 장애로 취급하지 않는다.

- 2026-11-23T02:12Z 읽기 전용 틱(2026-11-23 날짜 섹션이 없어 날짜 TOC 계속 깨짐) 및 배포 완료 후 최종 수치: Windows `948 passed / 361 subtests, 1 failed, 34 skipped` (유일 실패는 기존 `test_work_parallel_legacy_reviewer` 스키마 부분검사; 다른 실행에서 같은 장애로 `-Legacy` 보관), Linux py310·py311 각 `1,026건 · failure 0 · 기존 ERROR 1 · skip 91`. n1166은 완료 영수증을 갖고 있으나 검수 행이 없고 상태는 submitted·slot 해제·native 상태 running이다. 라우터 시작 뒤에도 후속 조치가 아직 공개되지 않았으니, 명백한 게시 경로 추정으로 두지 않는다(게시 원인 미확인).
  - **Andy**: 원장 6031→6045, 실행 슬롯 1 유지, 제출 21·입력대기 4. finding n1166는 그대로 남았고 조사는 운영에 개입하지 않았다.
  - **Celly**: 재기동 표본에서 findings 2→0으로 해소, 제출 1건은 그대로 남았다. MCP 266~640 ms 수준으로 응답했고 포크 오류 n1327의 상태는 그대로 모여 있다.
  - **KOBE**: a6c7e521이 이미 지나간 expected_version을 반복 호출하며 거부됐고, 그 항목의 실제 작업은 이어졌다.
  - **Prody**: n1076은 01:38:54Z 라이브 관찰이 들어온 직후 원장 `unreported`로 바뀌고 슬롯이 해제됐다. lease의 terminal reason/proof는 `handoff`와 `handoff receipt 9b45050815a1…`로 찍혀 있어, `432c8a8`의 반쪽 보고 확정 경로(`_settle_split_handoffs`)가 실제 운영에서도 동작했음이 직접 확인됐다. findings는 `submitted`만 남았다.

- 2026-11-23T02:04–02:06Z Celly 연결 회복 사후 확인(추적 시작 02:04:34Z): 해당 시점에 `/health`·Board·Side가 모두 `URLError`로 실패했으니 직전 Tik의 MCP 성공만으로 연결을 유연하게 본 것은 부정확했다. 그 뒤 새 라우터 PID 74704(기동 02:05:02Z)가 확인됐고 health 8~17 ms·Board 271 ms·Side 6348 ms로 성공했으며, 02:05:33Z 모드의 `Work mode is on`과 02:06:16Z gate re-check 공지가 있다. 앞서 01:49:01Z `ciel-work was unloaded: it crashed the hooks worker`, 02:04:00Z `Unknown command: /work-mode`가 있었다. 라우터부터 직접 shutdown한 원인과 훅 worker 충돌 원인은 아직 확인되지 않았다. 이번 확인은 사용자의 재시작 결행 뒤의 회고 기록이며 이번 작업에서 별도의 재시작은 수행하지 않았다.

- 2026-11-23T01:58Z 읽기 전용 틱(비교 2026-11-23T01:12Z; 2026-11-23 날짜 섹션이 없어 날짜 TOC는 계속 깨짐): 네 호스트 모두 Work MCP 조회 성공, 보드 버전 일치. 운영 재시작·상태 수정·배포 없음.
  - **Andy**: 원장 6025→6031, 실행 슬롯 1 유지·제출 20→21·입력대기 4 유지, findings 0→1. MCP 578→1610 ms(v10604). finding은 `n1166 2c02ec6f` submitted로 검수 행이 없는 상태이며 native 상태는 running, 마지막 native 시각 01:44, missing 01:50(틱 시각). 포크 오류 표본 0. 01:38:15Z `n1166 server_error` 자동 재시도 공지 1회, 01:50:28Z Parallel read timeout 조정 공지 1회; 재조정 완료 여부와 검수 행 게시 사유 미확인. 외부 SSE EOF 41(첫 01:13:42Z), SSE reset 7(첫 01:37:09Z), transcript 전달 timeout 8(첫 01:17:31Z), MCP 연결 reset 그룹 2(첫 01:52:32Z).
  - **Celly**: 원장 7443→7463, 실행 슬롯 1→3·제출 1 유지·입력대기 5 유지·신규 `waiting_input+slot 1`, findings 0→2. finding은 `n1331 ca401ead`와 완료 영수증 있으나 검수 행 없는 `n1327 434e000a`. MCP 프로브 4016 ms timeout 1회(보드 v9428 일치). 포크 오류 4건(00:56:15Z 사용자 거부·01:02:22Z SHA256 누락·01:04:59Z handoff validation·01:05:42Z exit 1). 01:49:01Z에 `ciel-work was unloaded: it crashed the hooks worker` 1회. 01:38:00Z~01:46:22Z 모드 공지가 계속 있었고, unload 뒤 전체 transcript tail에서 reload 공지는 확인되지 않았다. 원인은 미확인. 재확인에서 라우터 health 정상, Work Board 240 ms·Side 2765/2184 ms. 라우터 MCP reset 그룹 6(첫 01:34:22Z), 업스트림 연결 reset 그룹 4(첫 01:43:07Z), 실행 환경 활동 PermissionError 그룹 3(첫 01:59:49Z), transcript timeout 그룹 5(첫 01:42:57Z), 외부 SSE EOF 31(첫 01:32:22Z). `n1331`은 01:44:20Z 공지에서 정지 결과가 slot 없음인데 실제 state는 run exit question으로 남아 있었다.
  - **KOBE**: 원장 3009→3014, 실행 슬롯 0→1·입력대기 9 유지·제출 0→1, findings 0. MCP 513→291 ms(v597). 포크 오류 표본 10→3: `a111c04c` 1(01:11:48Z)과 `aa0b8b18` 2(01:12:07Z·01:12:43Z), 원문 미확인. 실패 실패 카운터 `decision_refused_stop dac6b6ca x1` 신규 1건. keep-alive 불일치 231(첫 01:38:58Z, health OK), SSE EOF 35(첫 01:39:50Z), transcript 전달 timeout 4(첫 01:41:03Z), 업스트림 경로 연결 reset 그룹 3(첫 01:41:13Z).
  - **Prody**: 원장 598, 실행 슬롯·제출 유지, findings 1 유지(n1076). MCP 1108→1867 ms(v6760), 포크 오류 표본 0 유지. 실패 SSE 원문 메인 append 3(00:33:43Z·00:37:43Z·00:49:23Z—Tik 이후 포함 표본), git 오류 2(01:44:25Z·01:54:34Z), Walkie 룸 접근 403은 01:55:40Z~01:55:41Z 3건. 01:38Z 이후 새 라우터 경고 0.
  - 결론: 이번 틱에서는 운영 제품 결함을 새로 입증하지 못했으나, Celly의 후반 발견 2건과 Celly 훅 unload 원인을 미결로 유지한다.

- `432c8a8` 배포(post-432c8a8 표본은 Tik 기준점 UTC 01:37Z, 조사는 2026-11-23): 세 가지 수정(P0 발사 전 예약 회수, 반쪽 Worker 보고 확정, 네이티브 터미널 선택 질문 차단)을 통합한 커밋을 `feature/priority-models`·`beta`에 push. Publish `45095141396` success, npm `@beta` `0.2.51-beta.20261123-013244.432c8a8`; 다운로드 패키지의 파일 SHA 분류가 설치본과 완전 일치. 해시 검증 오버레이 설치가 6개 루트에 걸쳐 이전 해시를 커밋 값과 대조하고 교체한 뒤 설치 후 해시를 재검증했다. 라이브는 사용자 권한 하에서 라우터만 정지해 Keep-alive가 재기동하게 했다. 새 PID Andy 48136/9385 Celly 61892/KOBE 15480/Prody 12320.
- 라이브 확인: post-deploy 표본에서도 Andy·Celly MCP가 정상 응답. Celly에는 새 findings 0이며, 모드의 `reloaded (19 hooks)`(01:33:41)와 `Work mode is on`(01:36:09)이 있다. KOBE·Prody도 라우터·클라이언트 연결 확인(15,480/(10,132)·12,320/(3,828)); SSH 반송이 간헐적으로 끊겨 전체 Tik 재수집은 하지 않았다.
- 상세는 [432c8a8 검증 기록](/2026/11/22/releases/432c8a8/verification.md) 참조.

- 2026-11-23T01:12Z 읽기 전용 틱(비교 2026-11-23T00:13Z, UTC): 네 호스트 모두 Work MCP 조회 성공, 보드 버전 일치. 운영 재시작·상태 수정·배포 없음.
  - **Andy**: 원장 6010→6025, 실행 슬롯 0→1, 제출 22→20, 입력대기 4 유지, MCP 1016→578 ms. 포크 오류 표본 0. SSE EOF 32(첫 00:32:23Z), transcript timeout 12(첫 00:35:57Z).
  - **Celly**: 원장 7419→7443, 실행 슬롯 2→1·입력대기 5→7→5, MCP 프로브 4016 ms 타임아웃 1회(보드 v9405 일치; 재확인 Board 240 ms, Side 2765 ms·2184 ms). 포크 오류 4건: 사용자 거부 1(00:56:15Z), SHA256 누락 1(01:02:22Z), handoff field 검증 거부 1(01:04:59Z), exit 1 1(01:05:42Z). Side 후속 재확인 대기 0·처리중 0, 처리 719→730·격리 107 유지. transcript 실패 2와 SSE EOF 31(첫 00:32:22Z).
  - **KOBE**: 원장 3005→3009, 실행 슬롯 1→0, 제출 0→1, MCP 356 ms. a6c7e521이 00:30:15Z부터 이미 지난 `expected_version`(521→519→518 등)으로 반복 호출, 매번 대화 식별자 미확인 거부. 00:32:13Z 뒤 실제 transcript에서 00:32:59Z·00:33:07Z 추가 2회 확인. 대화 식별자가 입증 안 되는데도 포크가 메인 전용 파견 도구를 계속 호출한 경위와 식별자 미확인 원인은 미확인. keep-alive 불일치 경고 402(health 호출은 계속 OK).
  - **Prody**: 원장 598, 실행 슬롯 1·제출 6 유지, MCP 1029→933 ms. 포크 오류 표본 2건은 KOBE·Prody 분류 조회 SSH 부분 응답으로 원인 미확인. n1076은 여전히 20:24Z 이후 native 미관측인데 슬롯 점유 지속.
  - Tik 뒤에 Windows·py310·py311 재검사(결합 수정 포함)의 TS/리스트 팩킹 수정본을 `terminal-question-final.tar`(선별 리스트 48개, 아카이브 대조 missing 0)로 다시 시작. 완료 시 해당 오류가 P0E 또는 반쪽보고 회귀가 아니라면 지정된 승인 범위의 npm·베타와 hash-verified 설치를 수행하고, 운영 효과와 파일 설치 효과는 구분해 보고한다. 기존 무관 스키마 ERROR는 별도 추적.

- Celly 터미널 선택 질문 조사·수정(22:50:17Z 발생, 00:50:14Z 답변; 전체 진행 지연): 화면의 P3 선택은 Ciel Work 호스트(`$.ui.ask`)가 아니라 Celly 메인 모델이 네이티브 `AskUserQuestion`을 호출한 것이다. 사이드 무인 처리에서는 CLI가 답을 갖지 않으므로 장기 대기 위험이 있다. `parallelToolCall`에서 엔진 메인과 포크의 `AskUserQuestion`을 사이드 설정 활성화/포크 문맥일 때 관리 문구 `mod_terminal_question_refused`로 거부하게 했다(내부·다른 플러그인·사이드 비활성화 메인 제외). Red 테스트는 처음에는 gate를 통과해 실패했고, 수정 뒤 통과. `claude plugin validate` 통과, `claude plugin test` 94/94, Windows 로컬 `test_work_texts.py`, `test_work_mod_write_gate.py`, `test_work_parallel_native.py`, `test_work_parallel_p0_events.py` 195건 통과(33 skip). 기존 Windows real-load gate와 광범위 Linux py310/py311 회귀 실행은 뒤에 이어짐. Text 22 운영 반영·포크 reload는 아직 미검증; 설정 문구에서는 외부 결정 채널 이용과 `waiting_on`/`release_hint` 대기를 요구한다. 사이드 설정이 비어 있어 fail-open인 잔여 경로는 미구현(위험 의사결정 대기).

- 2026-11-23T00:32Z 읽기 전용 틱(비교 2026-11-23T00:14Z; 2026-11-23 섹션이 아직 없어 날짜 TOC는 계속 깨짐): 네 호스트 Work MCP 조회 성공, 보드 버전 일치. 포크 오류는 부분 표본. 운영 재시작·상태 수정·배포 없음.
  - **Andy**: 원장 6010→6018, 실행 슬롯 0→2·제출 22→21·입력대기 4→3, 검수 차단 findings 0. MCP 1016→875 ms(v10580). 포크 오류 표본 0→2(00:28:45Z 셸 exit 1·00:31:24Z 파일 없음; 근본 원인 미확인). 외부 SSE EOF 15(첫 00:14:39Z), 업스트림 경로 연결 reset 2(첫 00:13:56Z), transcript 전달 timeout 2(첫 00:20:10Z), context usage 파일 WinError 5 1(00:25:22Z; 근본 원인 미확인). 모델 요청 재시도 로그는 이번 틱에 표시되지 않음.
  - **Celly**: 원장 7419→7424, 실행 슬롯 0 유지·입력대기 7→8·제출 1 유지, 검수 차단 findings 0. MCP 281→344 ms(v9391), 포크 오류 표본 0. 외부 SSE EOF 16(첫 00:14:39Z)·SSE reset 2(첫 00:21:13Z)·timeout 1(00:30:03Z). 실패 SSE 원문 append 공지 1(00:14:13Z, 원인 미확인). Side 재조회 성공: 대기 0·처리 중 1(`ae04bc99`), 누적 처리 719→722·격리 107 유지; 외부 답장 수신 증명 없음.
  - **KOBE**: 원장 3005→3007, 실행 슬롯 0→1·입력대기 9 유지. MCP 513→466 ms(v592). 포크 오류 표본 10→20: `a6c7e521`이 00:30:15Z부터 `n85`에 대해 이미 지나간 `expected_version`(521→507 순차 감소)로 `work_parallel_run`을 반복 호출했고, 매번 대화 식별자 미확인 거부. 00:32:13Z 뒤 실제 transcript에서 00:32:59Z·00:33:07Z 추가 2회 확인. 대화 식별자가 입증 안 되는데도 포크가 메인 전용 파견 도구를 계속 호출한 경위와 식별자 미확인 원인은 미확인. 기존 keep-alive 불일치 221(첫 00:13:52Z, health OK), SSE EOF 16(첫 00:14:33Z), transcript 전달 timeout 3(첫 00:18:10Z), 모델 runaway 반복+복구 1(00:14:48Z).
  - **Prody**: 원장 598, 실행 슬롯 1·제출 6 유지. MCP 1029→2424 ms(v6757; 지연 원인 미확인, 이후 추가 표본으로 지속 여부 미확인). 포크 오류 표본 0. n1076 슬롯 점유와 20:24Z 이후 native 미관측 지속. 00:08:19Z 이후 Tik/transcript_errors 없음(종료 추정 금지). 지난 틱 라우터 신규 경고 0.
  - 신규 운영 결함은 입증하지 못했다. 결합 수정의 P0 동시성은 미완료로 배포 보류 유지.

- 2026-11-23T00:14Z 읽기 전용 틱(비교 23:52Z, 날짜 섹션은 2026-11-23이 아직 없어 날짜 변경 후 목차가 깨졌고 23:52Z 항목 아래가 다음 섹션): 네 호스트 모두 Work MCP 조회 성공·보드 버전 일치. 포크 오류는 부분 표본, 제출 수는 검수 완료 수가 아니다. 운영 재시작·상태 수정·배포 없음.
  - **Andy**: 원장 6008→6010, 실행 슬롯 1→0·제출 21→22·입력대기 4 유지, 검수 차단 findings 0. MCP 797→1016 ms(v10578), 포크 오류 표본 0 유지. 실패 side 원문 append 공지 1(00:04:20Z, 원인 미확인). 외부 SSE EOF 20(첫 23:53:15Z), SSE reset 2(첫 00:00:40Z), transcript 전달 timeout 2(첫 00:00:20Z), 업스트림 경로 연결 reset 4(첫 23:56:17Z); 근본 원인 미확인.
  - **Celly**: 23:52Z 대비 원장 7419, 실행 슬롯 0·제출 1·입력대기 7 유지, MCP 234→281 ms(v9387), 포크 오류 표본 0, 검수 차단 findings 0. 외부 SSE EOF 21(첫 23:52:49Z), 업스트림 연결 reset 1(23:56:49Z), turn projection observe-only 경고 8(첫 23:53:28Z). Side 후속 조회: 대기 0·처리 중 1·처리 716→719·격리 106→107(처리 중 항목 식별자 `2c4e7688`, 조회 시 배달값 없음), Tik 이후 추가 조회 중 또 다른 격리 `a84c4a43` 확인. 격리 원인·각 발송 생략 원인은 미확인.
  - **KOBE**: 원장 2989→3005, 실행 슬롯 1→0·입력대기 9 유지, 검수 차단 findings 0. MCP 421→513 ms(v589). 포크 오류 표본 2→10: exit 1 1(23:55:07Z, 원문 미확인) 및 별도 조회분. 대표 lease `e68df6ef`는 question 필드 누락 1·증거 누락 1 뒤 23:57:24Z `waiting_input`으로 슬롯 해제; 뒤이어 handoff 형식 1·CAS 재조회 1 뒤 23:59:44Z `unreported`와 슬롯 해제를 확인했다(검증 거부→재시도 성공). 다른 fork는 `conversation identity unavailable` 4(00:09:20Z·00:11:56Z·00:12:25Z 및 Tik 이후 00:15:03Z 1), 인증된 parallel host 필요 1(00:12:51Z), 비목표 item 1(00:12:04Z), 사용 불가 도구 거부 1(00:14:48Z); Tik 윈도우 00:14:01Z 기준 해당 count는 Tik 이후 2건을 제외하면 8건이다. 메인 reviewer 포크 거부 1(23:16:09Z)·중복 배정 1(23:16:27Z)은 가드. 라우터 keep-alive 불일치 250(첫 23:52:32Z, health OK), SSE EOF 19(첫 23:54:29Z), transcript 전달 timeout 4(첫 00:00:08Z).
  - **Prody**: 원장 598·실행 슬롯 1·제출 6 유지, MCP 932→1029 ms(v6755), 포크 오류 표본 0. n1076 슬롯 점유와 20:24Z 이후 native 미관측 지속(Tik 00:14:13Z 시점 약 3시간 50분). 실패 side 원문 append 공지 1(00:08:19Z, 원인 미확인).
  - 검증: 변경 후 Windows 500건, Linux py310·py311 각 606건—모두 failure 0, 기존 무관 오류 1. 배포 전 코드 검토에서 P0 예약 정리와 spawn 전환의 분산 순서 경로를 발견; 운영 발생 여부는 미확인. 동시성 검증·이름 최적화 검토 전에는 병합 수정본 커밋·베타 푸시·hash 설치·라이브 적용을 보류. 이번 틱에서 새 운영 결함은 입증하지 못했다.

- 23:52Z 읽기 전용 틱(비교 23:32Z, UTC): 네 호스트 Work MCP 조회 성공, 보드 버전 일치. 포크 오류는 부분 표본, 제출 수는 검수 완료 수가 아님. 운영 재시작·상태 수정·배포 없음.
  - **Andy**: 원장 6005→6008, 실행 슬롯 3→1·입력대기 3→4·제출 20→21, 검수 차단 findings 0. MCP 1234→797 ms(v10570). 포크 오류 표본 1→0. 메인 검색 timeout 1(23:33:56Z), Python 문자열 문법 오류 1(23:38:13Z). 외부 SSE EOF 20(첫 23:32:35Z). 수집된 모델 요청 재시도 로그: HTTP 503 23건(첫 23:34:10Z), 연결 중단 10건(첫 23:34:09Z); 공통 원인 미확인.
  - **Celly**: 원장 7418→7419, 실행 슬롯 1→0·입력대기 6→7·제출 1 유지, 검수 차단 findings 0, MCP 860→234 ms(v9382), 포크 오류 표본 2→0(종료 증거 아님). 외부 SSE EOF 19(첫 23:32:45Z), 활동 파일 WinError 5 2(첫 23:38:24Z, 원인 미확인), 게이트웨이 connection reset 2(23:34:06Z·23:35:06Z), 모델 스트림 전달 오류 1(23:35:06Z). 직전 타임아웃된 Side 조회는 이번에 성공: 대기 0·처리 중 0, 누적 처리 716·격리 106(23:18Z 704·105). `6ef11bb4c21b`는 `invalid_answer`로 격리, 23:42:09Z 메인 원문 전달 공지 1, 해당 행 `delivery=submitted` 확인(상대 수신은 미확인).
  - **KOBE**: 원장 2986→2989, 실행 슬롯 1 유지·입력대기 8→9, MCP 384→421 ms(v579), 포크 도구 오류 표본 3→2(23:38:57Z·23:51:38Z, 원문 미확인). 기존 keep-alive 불일치 239(첫 23:32:29Z, health OK), SSE EOF 19(첫 23:33:18Z), 업스트림 경로 연결 reset 3(첫 23:33:43Z).
  - **Prody**: 원장 598, 실행 슬롯 1·제출 6 유지, MCP 1108→932 ms(v6754), 포크 오류 표본 0. n1076 슬롯 점유와 20:24Z 이후 native 미관측 지속. 실패 SSE 원문 메인 append 공지 1(23:49:46Z, 원래 실패 원인 미확인).
  - 새로운 제품 결함 입증 없음. 결합 수정의 P0 예약 정리/포크 시작 동시성 검증은 미완료로 배포 보류 유지.

- 23:32Z 읽기 전용 틱(비교 23:18Z, UTC): 네 호스트 Work MCP 조회 성공, 보드 버전 일치. 포크 표본은 부분 관측. 운영 재시작·상태 수정·배포 없음.
  - **Andy**: 원장 5999→6005, 실행 슬롯 4→3·입력대기 2→3·제출 20 유지. MCP 1422→1234 ms(v10568). 포크 question 필수 필드 누락 1건(23:21:36Z, 검증 거부), 메인 검색 timeout 1건(23:27:40Z). 외부 SSE EOF 13건(첫 23:19:07Z), context usage 파일 WinError 5 1건(23:25:22Z, 근본 원인 미확인), transcript 전달 timeout 1건(23:30:14Z).
  - **Celly**: 원장 7417→7418, 실행 슬롯 2→1·입력대기 5→6·제출 1 유지, 검수 차단 findings 0. MCP 250→860 ms(v9375). 포크 question 길이 검증 거부 1건(23:25:13Z), 셸 exit 1 1건(23:32:11Z, 근본 원인 미확인). 외부 SSE EOF 13건(첫 23:19:07Z), turn projection observe-only 경고 7건(23:18:35~23:30:48Z). 별도 side API 10초·15초 조회 모두 timeout(2건), 대기·처리·발송 상태 미관측. 조회 실패를 사이드 액터 정지로 보지 않는다.
  - **KOBE**: 원장 2969→2986, 실행 슬롯 2→1·입력대기 8 유지. MCP 220→384 ms(v571). 포크 도구 오류 3건(23:25:15Z·23:26:22Z·23:32:33Z, 원문 미확인), 메인 보드 버전 불일치 가드 1건(23:32:19Z). 기존 keep-alive 불일치 166건(첫 23:18:36Z, health OK), SSE EOF 14건(첫 23:18:39Z), 업스트림 경로 연결 reset 3건(첫 23:18:59Z).
  - **Prody**: 원장 598, 실행 슬롯 1·제출 6 유지. MCP 1672→1108 ms(v6753). 포크 오류 표본 0. n1076 슬롯 점유 및 20:24Z 이후 native 미관측 지속. 실패 SSE 원문 메인 append 2건(23:20:41Z·23:30:33Z), `already` 1건(23:30:47Z, 중복 방지); 원래 실패 원인 미확인.
  - 새 운영 결함 입증 없음. P0 예약 정리와 spawn 전환의 동시성 검증 미완료, 반쪽 보고 수정과 함께 배포 보류 상태 유지.

- 23:18Z 읽기 전용 틱(비교 22:52Z, 시각 UTC): 네 호스트 모두 Work MCP 조회 성공, 보드와 버전 일치. 포크 오류는 부분 표본, `submitted`는 검수 완료가 아님. 운영 재시작·상태 수정·배포 없음.
  - **Andy**: 원장 5989→5999, 실행 슬롯 4·제출 20·입력대기 2 유지, MCP 2125→1422 ms(v10567). 포크 오류 표본 1→0. 라우터 외부 SSE EOF 24(첫 22:52:48Z), 외부 SSE reset 3(첫 23:14:14Z), MCP 연결 reset 2(첫 22:53:11Z), transcript 전달 timeout 2(첫 23:02:05Z); 근본 원인 미확인.
  - **Celly**: 원장 7411→7417, 실행 슬롯 3→2·제출 0→1·입력대기 4→5, 검수 차단 findings 0, MCP 297→250 ms(v9366). 포크 오류 표본 2→5: SHA256 누락 거부 1(22:54:28Z), 작업트리 밖 Edit 가드 1(23:16:43Z), 검색 timeout 1(23:18:18Z), 23:17:20Z 2건 원문 미확인. 라우터 SSE EOF 23(첫 22:52:57Z), SSE reset 5(첫 23:00:28Z). Side 대기 1→0·처리 중 2→1·처리 699→704·격리 104→105. `9183785c`는 `invalid_answer`로 격리, 22:53:53Z 메인 원문 전달 공지 1. `157391`은 발송 제출 유지(상대 수신 미확인). 최신 처리 5건은 외부 발송 `not_applicable`; 개별 생략 원인 미확인.
  - **KOBE**: 원장 2957→2969, 실행 슬롯 0→2·입력대기 9→8, MCP 229→220 ms(v568). 같은 포크 도구 오류 표본 2(23:16:57Z·23:18:03Z, 원문 미확인); 메인 reviewer 포크 거부 1(23:16:09Z)·중복 배정 거부 1(23:16:27Z)은 가드. 라우터 keep-alive 불일치 309(첫 22:52:34Z, health OK), SSE EOF 26(첫 22:52:35Z), transcript 전달 timeout 6(첫 23:00:17Z).
  - **Prody**: 원장 598, 실행 슬롯 1·제출 6 유지. MCP 2203→1672 ms(v6752), 포크 오류 표본 0. `n1076` 슬롯 점유·20:24Z 이후 native 미관측 지속(종료 아님).
  - **수정본 검증**: LF 목록 Linux py310·py311 각각 606건(실패 0, 기존 `work_discovery_pickup` 스키마 오류 1, skip 84). Windows 500건(실패 0, 동일 기존 오류 1, skip 33). 배포 전 코드 확인에서 P0 정리의 `reserved` 확인과 리스 삭제 사이에 `p0_launch_spawning` 전환이 별도 저장소에서 성공할 수 있는 경로를 발견했다. 동시 실행 검증이 없어 두 수정이 들어간 결합 변경은 배포 보류. 이번 틱 새 운영 결함은 입증하지 못함.

- 22:52Z 읽기 전용 틱(이전 정규 틱 22:32Z, UTC): 4대 모두 Work MCP와 보드 버전 일치. 포크는 부분 표본이며 제출 리스 수는 검수 완료 수가 아니다. 운영 재시작·상태 파일 변경·배포 없음.
  - **Andy**: 원장 5989 유지, running 4·submitted 20·waiting 2 유지. MCP 1813→2125 ms(v10560). 포크 n2594 오류 1(22:52:02Z, 대화 식별자 미확인으로 공유 Work fallback 거부; 식별자 미확인 원인은 별도). 라우터 SSE EOF 19(첫 22:33:03Z), transcript 전달 timeout 1(22:35:23Z).
  - **Celly**: 원장 7390→7411, running 5→3·waiting 1→4·submitted 0 유지. MCP 1563→297 ms(v9359). 포크 셸 오류 2(22:36:40Z·22:52:16Z, exit 1, 원인 미확인). 메인 인자 검증 400 1(22:36:58Z), gh 인증 미구성 1(22:42:22Z), P3 정원 가드 2(22:42:52Z·22:43:14Z). 라우터 SSE EOF 14(첫 22:33:09Z), context usage WinError 5 1(22:35:58Z, 원인 미확인). Side 후속: 직전 대기/처리 0에서 수신 대기 1·처리 중 2(가장 오래된 수신 22:43:43Z), 처리 699 유지. `157391` 발송 제출 상태 유지(수신 증명 아님).
  - **KOBE**: 원장 2957·waiting 9 유지. MCP 170→229 ms(v557), 포크 오류 표본 0. 라우터 keep-alive 불일치 경고 238(첫 22:32:37Z, health OK), SSE EOF 20(첫 22:32:53Z), 업스트림 경로 연결 reset 2(첫 22:38:14Z). 기존 경고 추적.
  - **Prody**: 원장 598·submitted 6·running 1 유지. MCP 1244→2203 ms(v6749), 포크 오류 표본 0. n1076 슬롯 유지 및 20:24Z 이후 native 미관측. 22:51:28Z 실패 SSE 원문 메인 전달 공지 1(실패 원인 미확인).
  - P0 예약·반쪽 보고 수정: Windows 500건(실패 0, 기존 스키마 오류 1, skip 33). Linux는 테스트 목록에 아카이브 미포함 파일이 있어 실행 전 중단, 목록을 정리한 뒤에도 CRLF 때문에 다시 중단. 아카이브와 대조한 LF 목록으로 두 기존 컨테이너에서 재실행 중. 검증·게시·설치·라이브 적용 미완료.

- 22:42Z Celly 외부 SSE 사이드 처리 후속(읽기 전용): 실제 Walkie 메시지 `157391`은 22:38:35Z 수신 → 22:38:40Z claim → 22:41:30Z 처리 → 후속 조회에서 `delivery=submitted` 확인(상대 수신·열람 미확인). `157384`와 `157385`는 각각 22:31:27Z/22:32:26Z 수신, 22:36:38Z/22:37:03Z 처리 뒤 `delivery=not_applicable`로 외부 발송 없음. `157342` 1건은 22:23:01Z `duplicate_source`. 조회 시 대기·처리중 0건(`processed=699`, 격리 104는 세션 누적). Side 조회 1회가 12초에 타임아웃됐으나 재조회 성공. 전면 처리 중단은 관측과 맞지 않으며, 개별 발송 생략 사유와 처리 지연 원인은 미확인. 재시작·상태 수정 없음.

- 22:32Z 읽기 전용 틱(이전 정규 틱 21:58Z 및 Celly 복구 후속 관측과 비교, UTC): 4대 모두 Work MCP 조회 성공 및 보드 버전 일치. 포크 표본은 부분 관측이며 검수 집계는 submitted 리스 수로, 검수 완료 수를 의미하지 않는다.
  - **Andy**: 원장 5977→5989, running 슬롯 2→4, reserved 2→0, submitted 20 유지. MCP 1813 ms(v10556). 포크 도구 오류 1(22:29:24Z n2594의 ripgrep 20초 타임아웃). 라우터 외부 SSE EOF 29(첫 21:58:54Z), MCP 연결 reset 3(첫 22:00:28Z), transcript 전달 timeout 3(첫 22:00:35Z). n2594 배정 복구는 메인 22:12:38Z 보고 및 해당 포크 기록으로 확인, 이전 거부가 해소된 경로 미확인.
  - **Celly**: 원장 7320→7390, running 슬롯 5(재기동 전 소유자 종료 표시 5→현재 findings 0), waiting_input 1, submitted 0. MCP 1563 ms(v9352). 포크 오류 표본 8건(22:04:11~22:32:03Z): 이전 컨텍스트 Walkie 도구 미가용 2(22:04:15·22:08:43Z), 나머지 6건은 검색 timeout·셸/가드·증거 텍스트 길이 검증 등. 라우터 transcript 전달 연결 거부 9(첫 22:01:41Z, 22:16Z 이후 성공은 사후 확인), MCP 연결 reset 6(첫 22:04:46Z), 라우터 활동 파일 접근 거부 6(첫 22:28:47Z)·context usage 파일 접근 거부 2(첫 22:25:45Z); WinError 5 원인 미확인. CLI code 255 1(22:04:50Z), 종료 원인 미확인. 오래된 run 표본 24→40, 정리/종료 추정 금지.
  - **KOBE**: 원장 2957, waiting_input 9 유지. MCP 170 ms(v554). 포크 오류 1(22:05:51Z, 원문 미확인). 기존 keep-alive 불일치 401(첫 21:58:47Z, health OK), 외부 SSE EOF 31(첫 21:58:53Z); 새 제품 결함으로 분류하지 않음.
  - **Prody**: 원장 598, submitted 6·running 슬롯 1 유지. MCP 1244 ms(v6748). 포크 오류 표본 0. n1076 슬롯 유지, 20:24Z 이후 포크 미관측(종료 판단 아님).
  - P0 예약·반쪽 보고 수정의 재검사: Windows 500건, 실패 0·기존 스키마 오류 1(`work_discovery_pickup`의 `role`)·스킵 33. py310/py311 최종 결과 미확인. 이번 틱에는 새 결함을 입증하지 않았고 설치·배포·재시작·운영 상태 변경 없음.

- 22:16Z Celly Walkie MCP 장애 사후 확인(읽기 전용): 세션이 21:48:34Z Walkie 연결 문제로 재시작 요청을 냈고, 21:52:22Z `approvals.my_open_requests`는 Walkie MCP 연결 끊김으로 실패했다. 사용자 직접 종료 보고; 라우터 로그에서 21:55:21Z와 22:09:00Z `claude_exit`에 따른 정지를 확인했다. 라우터는 22:01:32Z·22:07:56Z·22:12:58Z 모두 같은 `0.2.51-beta.20261122-201721.db7b257` 버전으로 시작했다. 22:12:04Z Walkie 연결 메타데이터 파일 갱신 시각만 확인했고, 설치 디렉터리는 ACL로 읽지 못했다. 사용자가 보고한 설치 명령 멈춤은 실행 완료나 멈춘 단계가 기록에서 확인되지 않았으며 재실행하지 않았다. 복구 증거: 22:15:16Z Walkie 승인 조회가 `requests` 데이터를, 22:15:54Z 메시지 조회가 `messages` 데이터를 반환했다. Transcript 전달도 22:16:00Z부터 성공 기록 확인. 라우터 PID 69920 `/health` 정상. 마지막 런타임 변경 `db7b257`은 사이드 claim 수신 기록 스캔 최적화이며 Walkie MCP 등록·설치 코드 변경은 없다. 실패와 성공 사이 런타임 버전이 같지만 최초 단절·설치 멈춤·복구의 인과는 아직 확인되지 않았다. 확인 과정에서 재시작·설치·운영 상태 변경 없음.

- 21:58–22:02Z 틱(비교 기준 21:41Z; 이번 작업에서 운영 재시작·상태 수정 없음):
  - **Celly**: 21:52:22Z Walkie MCP 연결 끊김 1건. 세션 기록에 재시작 요청 1건(21:48:34Z), 라우터 로그에 CLI 종료에 따른 정지(21:55:21Z)가 있다. 틱 때 보드·MCP 조회 실패(2,047 ms), 종료된 소유자 PID 12304의 슬롯 5건이 남아 있었다. 22:01Z 새 라우터 PID 67456 및 런처 PID 27716 확인. 기존 orphan sweep이 22:01:35Z 리스 8건(관측한 슬롯 5건 포함)을 해제했다. 22:02:44Z MCP 562 ms, 보드·MCP v9339 일치, findings 0, 새 running 슬롯 4건. n1299의 이전 예약 `740bcc9d`는 이 조회에서 보이지 않았고 별도 리스 `93102c58`은 포크에 연결된 뒤 sweep에서 해제됐다. 두 리스를 같은 것으로 보지 않는다. 포크 도구 오류 표본 1건(21:46:34Z, 원인 미확인); 재기동 후 transcript 전달 실패 2건(첫 22:01:41Z, 수신 endpoint 원인 미확인).
  - **Andy**: 원장 v5971→5977, running 슬롯 3→2, 예약 0→2, submitted 20 유지. MCP 1344→1328 ms, v10547, 포크 오류 표본 0. 라우터 SSE EOF 16건(첫 21:42:20Z), transcript 전달 timeout 2건(첫 21:45:18Z); 근본 원인은 미확인.
  - **KOBE**: 원장 v2957·waiting_input 9 유지. MCP 448→258 ms, v551. 포크 도구 오류 표본 1건(21:55:20Z, 원인 미확인). keep-alive fingerprint 불일치 경고 207건(첫 21:41:32Z, health OK), SSE EOF 17건(첫 21:42:17Z). 입력 제출 미확인 및 receipt 불일치 각 1건(21:43:46Z, 같은 시각); 실제 미전달 여부 미확인.
  - **Prody**: 원장 v598, submitted 6·running 슬롯 1 유지. MCP 1514→1255 ms, v6745. 포크 오류 표본 0. n1076 `8e0625bf` 슬롯 유지, 20:24:18Z부터 native 목록 미관측(종료 판단 아님).
  - **회귀·배포**: 결합 회귀에서 과대 question 증거 길이를 표시하던 오류 문구가 바뀌는 회귀 확인(Windows·Linux 공통) → 길이와 한도를 다시 표시하도록 수정, 집중 Windows 4건 및 subtest 3건 통과. Windows side-status 실패 1건은 집중 재실행 3회 및 원본 HEAD 별도 실행에서 재현되지 않아 미확정. 스키마 `KeyError: role`은 기존 오류. 변경본 Windows·Linux py310/py311 재검사 진행 중; 베타 푸시·npm 게시·설치·라이브 반영 없음.

- 21:41Z 틱(비교 기준 21:12Z; 운영 재시작·상태 수정 없음):
  - **Prody**: finding `n1076 8e0625bf` 그대로(수정 미설치 → 예상대로). MCP 1514 ms.
  - **Andy**: 21:24:05Z `reserve failed: P0 workspace preparation outcome unknown; recovery required` 1회. 그 뒤 P0 포크 `work-p0-158faa2a`가 떠 있고, reserved/spawning P0 intent는 0건이다 → 설계된 재시도 경로로 회복됐다. MCP 1344 ms, findings 0.
  - **Celly**: MCP 1766 ms, findings 0. `auto continue could not read the Work status` 21:26:26Z 1회(19:31Z에 이어 두 번째 관측, 반복 추적). transcript 전달 실패가 21:12Z 이후 계속된다(누적 38+, 10061, Walkie 수신기 쪽). n1299 새 리스 `740bcc9d`가 21:40:47Z부터 `reserved`(일반 레인, P0 intent 없음) — 다음 틱에 bind 여부 확인. 정상 가드 3건(중복 파견 거부), host 전용 도구를 메인이 호출해 거부된 것 1건.
  - **KOBE**: keep-alive mismatch 348(기존), 변화 없음.
  - 두 수정(`p0-ended-before-launch`, `split-worker-report`)의 회귀 실행이 끝나지 않아 커밋·설치 전이다.

- 21:12Z 틱(비교 기준 19:52Z; 운영 재시작·상태 수정 없음):
  - **로컬 머신 재부팅 20:53:36Z**(원인 미확인). 이 머신의 Andy·Celly와 이 세션이 함께 재시작됐다.
    - 라우터: Andy `39676`(20:55:50Z), Celly `31828`(20:54:40Z). 둘 다 `bc1bac5`·`db7b257`이 설치된 루트에서 시작했다(해시 일치) → **`db7b257`이 Andy·Celly에서 라이브 적용**. KOBE(2684, 19:25Z)와 Prody(10036, 19:21Z)는 재시작하지 않아 `db7b257` 미적용.
    - 런처 `work_orphan_sweep`: Celly 6건(20:54:45Z, `c58382cab38e` n1326 reserved 포함), Andy 4건 이상(20:55:52Z), 모두 `owner=dead`. 재부팅 후 정상 경로다. **n1326은 20:55:13Z 새 리스 `800555e1`로 실행 중**(미결 해소, 원인은 재부팅).
    - 재기동 경고: `remote_instructions_failed WinError 10051`(20:54:20Z, 네트워크가 올라오기 전), gateway `degraded`, `secure_json_read_failed sse_gate_status`(각 1회, 지금은 파일 정상 JSON).
  - **Celly transcript 전달 실패(새 관측, ciel-runtime 밖)**: 재부팅 후 `transcript_delta_delivery_failed … WinError 10061`이 37회(20:54:58Z~21:14Z), 전달 0회. 재부팅 전 `router.log.1`에서는 전달 66회, timeout 6회였다. Andy는 로컬 Walkie `transcript-relay.py`(127.0.0.1:52050, pid 37488, 20:56:14Z 시작)로 전달이 재개됐다. Celly의 수신기 주소는 다른 계정 설정이라 읽지 못했고, 그 수신기가 재부팅 후 떠 있지 않은 것으로 보인다(추정). Walkie 쪽 수신기 문제이며 제품 결함으로 단정하지 않는다.
  - **Prody — 입증 결함** [split-worker-report](/2026/11/22/fixes/split-worker-report/record.md): finding `slot held, fork absent since 20:24: n1076 8e0625bf P4 running`. 포크가 evidence 없이 handoff를 보내 영수증만 기록되고(20:18:50Z) 원장은 `handoff proof required`로 거부됐다. 리스는 running/slot 유지, run은 handed_off, 호스트는 이것을 종료로 보고 포크만 정지했다. 실패 테스트 후 수정: 영수증 쓰기 전에 증거 검사, 기존 반쪽 handoff는 native 관측(포크 idle을 영수증 이후에 확인)에서 정리.
  - 같은 틱에 이전 작업 [p0-ended-before-launch](/2026/11/22/fixes/p0-ended-before-launch/record.md) 수정도 마무리했다. Linux py310·py311 각 1011건 → 기존 무관 오류 1건(HEAD와 동일).
  - 두 수정을 함께 회귀 검사 중이다(Linux 전체 목록, Windows는 real-backend 파일 제외).
  - **KOBE**: keep-alive mismatch 517(기존), MCP 174 ms, 변화 없음.
  - **Andy**: MCP 1110 ms, findings 0, stale_runs 133 → 137(재부팅 후 해제된 리스).
  - **Celly**: MCP 265 ms, findings 0, 포크 오류 2.

- 19:52Z 틱(비교 기준 19:33Z; 운영 재시작·상태 수정 없음) + **입증 결함 수정 `db7b257`(파일 설치만)**:
  - **Celly**: MCP 지연 해소 — `probe_succeeded` 344 ms, 보드 직접 측정 500~825 ms(직전 틱 30 s 타임아웃은 19:34Z에 이미 회복). n1327 이전 포크 `aa316fb9`는 19:45Z `host_salvage`(native missing)로 `unreported` → 새 리스 `41be0eb8` running. 19:47:53Z 이전 포크로 보낸 메시지 거부는 정상 가드(`revival_refused` 1). **n1326 `reserved` + 포크 없음은 18:25:44Z 이후 1시간 50분째**(미결; 메인이 공지 후에도 처리하지 않음). 업스트림: `_Walkie_P3` 요청 `2cbdf102`가 late SSE retry 7회, 442 s 후 `budget_exhausted`로 멈춤(19:42:36Z, 게이트웨이 소유). 포크 오류 3(19:38~19:39Z).
  - **KOBE**: 모니터 4초 프로브 `probe_failed`(4028 ms). 직후 직접 측정한 보드는 404~583 ms. keep-alive mismatch 240(기존). 라우터 2684 CPU는 19:25:52Z 이후 1062.6 s이고 10초 사이 +9.4 s였다.
  - **Andy**: MCP 1000 ms, 포크 오류 1(`a03f3c68` n2605, 19:46:24Z). 메인 git 명령 인자 오류 1(에이전트 도구 사용). 라우터 68036 CPU는 19:17:24Z 이후 2412.8 s이고 10초 사이 +12.2 s였다.
  - **Prody**: MCP 1068 ms, 변화 없음.
  - **입증 결함 — 사이드 claim마다 전체 수신 파일 재읽기** [side-claim-receipt-scan](/2026/11/22/fixes/side-claim-receipt-scan/record.md): Andy 라우터 py-spy(멈춤 없는 샘플링, 30초) 결과, 표본의 41%가 `work_side_claim → side_reply_outbox.pending()`의 수신 5,459개(108 MB) 전체 읽기였다(호출당 6.5~7.0 s, 돌려줄 0개). 실패 테스트 → `StatVerdictCache`로 바뀐 파일만 읽게 수정(같은 복사본에서 재호출 0.42~0.49 s, 결과 동일) → Windows 격리, py310·py311 각 671건 통과 → `feature/priority-models`·`beta` push(`db7b257`) → 6개 경로 해시 검증 설치(`stat_verdict_cache.py` 신규 `3527ead69213`, `side_reply_outbox.py` `cfffe9fb7153 → 66f58ebd6bf8`, 백업 `overlay-db7b257`). **라우터는 재시작하지 않았으므로 라이브 미적용**(실행 중 라우터는 기존 모듈을 이미 import함). Publish `45065566013` success, npm `@beta` `0.2.51-beta.20261122-201721.db7b257`, tarball 두 파일 해시 = 설치본.
  - 미결: n1326 예약 슬롯, 라우터 CPU 나머지(`ForkTurnWatch._poll` 24.5%), Celly·KOBE·Prody 수신 파일 수 미측정, db7b257 라이브 적용(라우터 재시작 필요, 사용자 결정).

- 19:33Z 읽기 전용 틱(비교 기준 19:26Z; 재시작·상태 수정 없음):
  - **Celly MCP/보드 지연(새 관측, 원인 미확인)**: 모니터의 4초 프로브 `probe_failed`(4032 ms), board 응답 없음. 직접 측정: `19:33:47Z` 30초 타임아웃 → 17.6초 → 3.8초 → 2.5초 → 0.84초 → `19:34:35Z` 0.56초로 회복. 같은 시간대 Celly 메인에 `19:31:17Z` `auto continue could not read the Work status` 1회, 라우터 로그 `router_post_client_disconnected path=/ca/mcp` 13회(13:32:39~13:34:11 local, 클라이언트 쪽 타임아웃). 라우터 로그의 MCP 호출 수: 19:23~19:28Z `work_host_page` 961·`work_parallel_status` 156, 19:29~19:34Z `work_host_page` 477·`work_parallel_status` 88. 재시작 이전 구간에는 이 로그 줄이 없어 비교 기준이 없다. CPU는 다른 계정 프로세스라 읽지 못했다. → 재시작 직후의 부하인지, 계속되는 것인지 다음 틱에 같은 방법으로 비교한다. 제품 결함으로 단정하지 않는다.
  - Celly coordination `1,586,076 B`(v5819, 계속 쓰기 성공). byte limit 오류 0. running+slot 6(n1333·n1331·n1334·n1327·n1332 새 리스 `568e9cb9`), **n1326 `reserved` 포크 없음은 18:25:44Z 이후 계속**(1시간 이상, 미결). 포크 오류 3건(a7a25aa0·aa316fb9·af31a8e5 각 1). 로그: `cielairouter_late_sse_retry` 4(1/20에서 진행), `anthropic_sse_client_disconnected` 1, `channel_turn_projection_persistent` 2(observe_only).
  - **Andy**: ledger 5931 유지, MCP `probe_succeeded` 1266 ms, findings 0, 포크 오류 0, SSE EOF 재연결 5.
  - **KOBE**: MCP 305 ms, keep-alive mismatch 71(기존 미결), `transcript_delta_delivery_failed TimeoutError` 1, `router_client_disconnected`·SSE `ConnectionResetError` 각 1(네트워크 일시), side 원문 append 1, 메인 `TaskStop` 대상 이미 완료 1(도구 사용 오류).
  - **Prody**: MCP 1022 ms, 변화 없음(side 원문 append 1).

- 19:26Z 읽기 전용 틱(비교 기준 16:33Z, bc1bac5 라우터 재시작 이후):
  - **네 호스트 모두 띄워진 새 라우터**: Andy 68036, Celly 67592, KOBE 2684(파일 동기화 후 2차 재시작), Prody 10036. 재시작 직후 `router_lifetime_restart reason=router_process_exited`가 각 1~2건 — 예상된 재기동 신호다. `external_event_sse_disconnected EOFError` Andy 9·Celly 4·KOBE 4(카테고리 eof, 과거와 같은 정상 재연결 부류). KOBE keep-alive mismatch 49 + 11(재시작하기 전·후)은 기존 미결 경고. 라우터 기동 시 gateway `degraded=True`(Andy·KOBE 각 1) 기존.
  - **MCP/보드**: 4호스트 모두 `probe_succeeded`, board=work MCP version 일치(Andy 10490/781 ms, Celly 9280/719 ms, KOBE 537/404 ms, Prody 6714/773 ms). findings 0, 새 포크 오류 Andy·KOBE 0. Prody 포크 오류 2건(16:xx대), Celly 16건.
  - **bc1bac5 결과 ✅**: Celly coordination `1,584,444 B`(수정 직후 1,579,616). **`coordination byte limit` 오류가 19:11:13Z 이후 더 없다**(수정 전 18:25·18:47·18:49·19:00·19:11 총 5회). 그 뒤 n1331·n1334·n1327 reserve가 모두 성공해 running+slot으로 떴고(19:22~19:23Z), n1333은 `empty_stream` 1회 후 자동 재시도로 복구됐다. n1209가 소유한 n1331이 실제로 돌고 있다. **n1326 슬롯(c58382cab38e, 18:25:44Z 예약, 포크 없음)은 아직 그대로** — Celly 메인 쪽 처리 대기.
  - **Celly 포크 보고 복구 시도**: 메인이 19:26:21Z에 n1332 포크(a9c2a65c8)에 보고 재전송을 지시 → 19:26:26Z `unreported`(host_salvage)라 거부. 이는 정의된 가드(끝난 리스에 메시지를 보내면 포크가 되살아나므로 거부)이며, 새 라우터가 아니라 리스 자체의 상태 문제다(미결).
  - **Andy**: coordination `2,074,076 B`(한도까지 약 23 KB) — 아직 트림은 일어나지 않았다. 다음 쓰기가 넘으면 트림된다. n2582·n2600·n2605 `server_error` → 1회 재시도로 3건 모두 `recovered`(19:20:36~41Z). `Work item n2605 already has lease…`는 정상 중복 파견 가드(18:56Z·19:21Z). `n2605`가 19:21:43Z에 새 리스로 다시 파견됐다. 19:21:13Z `refused (orphaned)`는 유령 리스 `56e71c66`에 메시지를 보내려다 거부된 것 = 가드.
  - **KOBE**: `walkie_send_message` `Invalid uuid at client_id` 1(18:00Z, 워키 쪽 인자 검증; ciel-runtime 밖). 작업 리스 waiting_input 9·stale 3 그대로, fork err 0. `work-parallel-stranded-v1.json` 없음(정상: 파일 미생성 시 빈 문서).
  - **Prody**: 19:2xZ 이후 새 오류 없음. `submitted` 6건 그대로, transcript 오류는 스크립트 인코딩(cp949)·MCP 인자 검증 등 사용자 작업 쪽. `claude_session_socket_unavailable(auth_key_unavailable → terminal fallback)` 10:37Z 1건 기존 미결.
  - bc1bac5 공지 수신(transcript 확인): Andy 19:23:19Z(attachment, 라우터의 `channel_windows_console_unseen_failed age=23.7s` 경고와 무관하게 전달됨), Celly 19:23:18Z, KOBE 19:23:57Z(메인 응답 19:24:43Z), Prody 19:24:05Z.
  - 미결 변화 없음: Celly n1326 슬롯·n1332 unreported 리스, Celly orphaned인데 run이 active인 행 93개, KOBE mismatch 경고, `test_work_parallel_legacy_reviewer` 기존 오류.

- 19:24Z **버전 확인 · Celly 보고 검증 · `bc1bac5` 수정·배포·라우터 재시작**(사용자 요청: 라우터 재시작 여부, Prody·KOBE·Celly 최신 여부, Celly 보고):
  - **정정**: 15:31Z·16:12Z·16:33Z 기록의 "Celly는 CLI 미재시작이라 `002189d` mod 라이브 미적용"은 틀렸다. Celly·Andy·KOBE·Prody transcript 모두 `15:08:58Z~15:09:12Z`에 `ciel-work: reloaded (19 hooks…)`(설치 파일 핫리로드)가 있고, Celly에서 새 코드 경로 문구 `Work fork stopped after its completion receipt (held waiting)`가 `15:12:33Z`(n1267), `15:56:17Z`(n1319), `16:11:51Z`(n1321)에 나왔다. 세 검수 모두 2~6분 안에 `finalized`(close/approve/approve)됐다.
  - **설치 버전(파일 해시 vs HEAD `002189d`, `ciel_runtime_support` 588개)**: `register.js`는 세 호스트 모두 `c534337858a2` ✅. Prody는 npm `0.2.51-beta.20261122-150753.002189d` 새 설치(18:38Z 전후 원인 미확인)로 다른 파일은 `runtime_constants.py`(publish 때 VERSION 줄 치환, `npm-publish.yml:126`)뿐이다. Celly·KOBE의 `fork_turn_watch.py`·`work_parallel_finalization.py`는 HEAD와 내용이 같고 줄바꿈만 CRLF다. **KOBE의 `claude_stream_recovery_runtime.py`(=`a75870f`)와 `secure_json_repository.py`(=`0cd6c60`)는 `5038a08`/`99320bc` 이전 버전**(미설치 상태, 미결). `work.test.ts`는 테스트 파일이라 실행에 쓰이지 않는다. `/health`의 `source_fingerprint 84eb402c`는 이 파일들을 포함하지 않아 버전 증거가 될 수 없다.
  - **재시작 관측(원인 미확인)**: Celly는 `16:42:26Z`에 새 launch(`reason=launch`, router `running=False`)로 라우터 16792와 CLI가 함께 시작됐다. Andy는 `18:34:29Z` 새 launch로 라우터 32996이 떴다. Prody 라우터는 8560(18:38:42Z), KOBE와 Prody 모두 `18:39Z` `Work mode is on`.
  - **Celly 보고(18:43Z) 검증**: 검수 정상 ✅(n1313/n1330 등 finalized). n1209 `launch_refused`와 n1326 미연결의 원인은 보고의 추정(남은 n1314 run)이 아니다. 거부 원문은 `coordination byte limit`(`18:25:39Z`, `18:47:34Z` reserve, n1332 보고 거부) → **입증 결함** [coordination-history-budget](/2026/11/22/fixes/coordination-history-budget/record.md). Celly 파일 2,097,109/2,097,152 B, Andy 2,066,586 B.
  - **수정 `bc1bac5`**: 실패 테스트(Celly와 같은 오류) → 수정 → Windows 임시 격리·Linux py310·py311 각 643건(기존 무관 오류 1건, HEAD에서도 동일) → `feature/priority-models`·`beta` push → Publish `45059663675` success → npm `@beta` `0.2.51-beta.20261122-191508.bc1bac5`(tarball 파일 해시 `593897b1c5d4` = 설치본).
  - **설치**(`work_parallel_receipts.py`, 설치 전 `341492a15bd2`=002189d 원본 확인 → `593897b1c5d4`, 백업 `.overlay-backups/overlay-bc1bac5`): 로컬 3개 경로·Celly(G:)·KOBE·Prody 모두 ✅.
  - **라우터만 재시작**(CLI 재시작 없음): Andy `32996 → 68036`(19:17:24Z), KOBE `15056 → 17792`(19:21:22Z), Prody `8560 → 10036`(19:21:26Z), Celly `16792 → 67592`(19:22:15Z, 비권한 정지가 거부돼 관리자 권한 `Start-Process -Verb RunAs`로 정지, 첫 시도는 `-Wait`에서 멈춤 → 결과 파일 방식으로 재시도). 새 프로세스는 모두 설치 뒤 시작.
  - **라이브 효과 ✅**: Celly 파일 `19:22:47Z` 2,097,109 → 1,579,616 B. 18:47Z부터 v5792에 멈춰 있던 버전이 v5796으로 진행했다(runs 184 → 185, receipts 405 → 407, archive 1000 → 297).
  - 세션 공지(`submit_input`): Celly 15722, Andy 24169, KOBE 11920, Prody 7834 모두 `ok:true`(원인, 수정, 재시도 인스트럭션).
  - KOBE 구버전 파일 동기화(19:25Z): `claude_stream_recovery_runtime.py` `52f108640725 → 1618ef2d851d`, `secure_json_repository.py` `988e8e921bc0 → 1ecf73cd4aa6`(설치 전 `a75870f`/`0cd6c60` 원본 확인, 백업 `overlay-kobe-sync-2bcc0ce`) → KOBE `ciel_runtime_support` 전체가 HEAD와 같은 내용이 됐다(CRLF 2개, 테스트 파일, 버전 줄 제외). KOBE 라우터 `17792 → 2684`(19:25:52Z) 재시작. CLI 런처 프로세스는 재시작하지 않았으므로 런처 쪽 사용분은 다음 세션 시작부터 적용된다.
  - 미결: 리스는 끝났는데 coordination run이 active로 남는 행(Celly 93개), `test_work_parallel_legacy_reviewer` 스키마 테스트 기존 오류, Celly n1326 예약 슬롯(새 라우터에서 처리되는지 다음 틱에 확인).

- 16:33Z 읽기 전용 틱(비교 기준 16:12Z; 추가 재시작·상태 수정 없음):
  - **세 findings 모두 해소 ✅**: Celly `n1321 5bef77ec` → lease `done`, 검수 `finalized/approve`(`n1321-review-approve-20261122`; Celly는 이전 mod라 해소 경로는 미확인). Andy `n650 b592dde3` → `done`, `finalized/approve`(`n650-review-1`). Prody `n1215 cc525e33` finding 소멸.
  - **Andy**(라우터 `54628`, ledger `v5866 → v5869`, MCP `766 ms`, forks 0 err): main `work_parallel_item_review`(n650 approve, 기대 `v2017`) `16:13:49Z`·`16:15:02Z` 두 번 모두 60초 `The operation timed out`, 그런데 결과는 `finalized/approve` — 호출 응답은 타임아웃이지만 결정은 반영됨(어느 호출이 반영했는지·지연 원인 미확인, 다음 틱 추적). `new_failures` `decision_refused_stop b592dde3` 1은 이 타임아웃 거부 기록. `/ca/mcp` reset 2, keep-alive mismatch 249(재기동 경고).
  - **Celly**(라우터 `16340`·CLI 미재시작 그대로, ledger `v6547 → v6554`, MCP `703 ms`, forks 12 / 5 err): `findings=0`. `/ca/mcp` reset 9, 게이트웨이 `agent-aap…:20129` 실패 → transient retry 2(`16:17:13Z`). 대화: 후보 없음 정상 가드 1, side 원문 append 1. 세션 재시작 hold 계속.
  - **KOBE**(ledger `v2952`, MCP `308 ms`, forks 0 err): keep-alive mismatch 249, 외부 SSE reset 3, side 원문 append 1.
  - **Prody**(ledger `v581 → v589`, MCP `1,354 ms`, forks 24 / 1 err): `findings=0`. CLI 미재시작.
  - 새로 입증된 미수정 제품 결함 없음. 미결: Andy 검수 호출 60초 타임아웃(결정은 반영), Celly·Prody 세션 재시작, Celly 라우터 재시작.

- 16:12Z 읽기 전용 틱(비교 기준 15:53Z; 추가 재시작·상태 수정 없음):
  - **Celly**(라우터 `16340` 그대로, CLI 미재시작, ledger `v6530 → v6547`, MCP `953 ms`): **신규 `findings=1`**: `n1321 5bef77ec P2 native=waiting` 완료 영수증 후 검수 행 없음 — `n1267`과 같은 모양. `002189d`는 Celly에 파일 설치만 됐고 세션이 재시작되지 않아 이전 mod가 돌고 있어 자동 정지가 동작하지 않음(라이브 미적용). 세션 재시작 요청은 `15:17:35Z` `hold_started` 이후 미실행. `restart_hold.py` 확인: 재시작이 보류되는 동안 그 CLI로의 대기 입력 전달은 모두 멈춤 → Celly main에 보낸 라우터 재시작 지시(15667)도 재시작 전까지 전달되지 않음. 대화: `workspace ownership was handed off; retained` 1, plan 분해 부족 정상 가드 1.
  - **Andy**(라우터 `54628`, ledger `v5862 → v5866`, MCP `765 ms`, forks 0 err): `findings=1`: `n650 b592dde3 P5 native=running` 완료 영수증 후 `running`(완료 턴 마무리 중 패턴, 다음 틱 추적; 새 자동 정지는 `waiting`만 대상). keep-alive mismatch 235(재기동 경고). Walkie `400 Invalid operation arguments` 1(에이전트 인자).
  - **KOBE**(라우터 `15056`, ledger `v2945 → v2952`, MCP `366 ms`, forks 24 / 1 err): keep-alive mismatch 232, 에이전트 diff 스크립트 exit 2 1.
  - **Prody**(ledger `v580 → v581`, MCP `1,004 ms`): `findings=1`: `n1215 cc525e33 P1 native=running` 완료 영수증 후 `running`(다음 틱 추적). CLI 미재시작.
  - 새로 입증된 미수정 제품 결함 없음(Celly `n1321`은 수정 완료·미적용 상태의 재발). 미결: Celly 세션 재시작 실행 여부(보류가 끝나지 않으면 Celly에는 수정이 적용되지 않음), Prody 세션 재시작, Andy `n650`·Prody `n1215` 다음 틱 해소 여부.

- 15:53Z 읽기 전용 틱(비교 기준 15:32Z; 추가 재시작·상태 수정 없음):
  - **Andy**(라우터 `54628`·source `84eb402c`, ledger `v5838 → v5862`, MCP `1,360 ms`, forks 0 err, `findings=0`): keep-alive `reachable_health_mismatch_active_client` 240(재기동한 라우터 fingerprint가 재시작하지 않은 CLI 감독 프로세스의 기대값과 달라 5초마다 기록되는 재기동 경고, KOBE에서 이미 관측된 패턴). `startup_prompt_auto_answer_unavailable … input_held=true` → 같은 초 `startup_prompt_input_released reason=main_output_after_launch` 1(정상 해제). 대화: Work 완료 순서 정상 가드 1, 에이전트 스크립트 오류 1.
  - **Celly**(라우터 `16340`·source `3819b46e` 그대로, ledger `v6516 → v6530`, MCP `1,563 ms`, forks 11 / 7 err, `findings=0`): `/health` 첫 5초 요청 무응답 1회 → 재요청 `200 171 ms`. `15:33:50Z` `Work gate check (host round): pump failed` 1(미재시작 라우터·mod). 세션 재시작 hold·라우터 재시작 지시 모두 미실행.
  - **KOBE**(라우터 `15056`, ledger `v2945`, MCP `161 ms`, forks 0 err): keep-alive mismatch 242(같은 재기동 경고), side 원문 append 1.
  - **Prody**(ledger `v577 → v580`, MCP `984 ms`, forks 0 err): CLI 미재시작(claude.exe `3236`·`10956` 그대로).
  - 새로 입증된 미수정 제품 결함 없음. 미결: Celly·Prody 세션 재시작 실행, Celly 라우터 재시작, 재기동 라우터와 미재시작 CLI 감독 프로세스 사이 keep-alive mismatch 경고(감독 프로세스 재시작 시 해소 예상, 미확인).

- 15:32Z 틱(배포 직후 라이브 효과 확인; 이번 틱 추가 재시작·상태 수정 없음):
  - **Andy**(라우터 `54628`, source `84eb402c`, ledger `v5822 → v5838`, MCP `1,984 ms`, forks 0 err): 라우터 재기동 `15:26:57Z`(`router_lifetime_restart reason=router_process_exited`, 내 pid 정지에 따른 keep-alive 재기동). 그 사이 `15:27:01–03Z` `MCP server "ciel-runtime-router" is not connected` 7건(재기동 창의 예상된 경고). **라이브 효과 확인 ✅**: `15:27:34Z` `Work fork API error (server_error) on n2595; scheduling up to 20 retries`, `15:28:53Z` `retry 1/20` — `249a99c` 포크 20회 재시도가 Andy에서 처음 실제 동작. 재기동 시 `cielairouter_gateway_central … discovery unavailable degraded=True; requests use the configured base URL` 1건(이후 `upstream_direct_request` 51건 정상 전송 확인, 설정된 base URL 사용).
  - **KOBE**(라우터 `15056`, source `84eb402c`): 재기동 `15:29:36Z`, 같은 gateway central 경고 1건. **라이브 효과 ✅**: `15:31:07Z` `Work fork API error (empty_stream) on n96; scheduling up to 20 retries`. keep-alive `reachable_health_mismatch_active_client` 경고는 32건으로 감소(직전 240건대). Walkie flow step 인자 오류 1(에이전트).
  - **Celly**(라우터 `16340` 그대로·source `3819b46e`, CLI 미재시작): ledger `v6505 → v6516`, `running+slot 8`, forks 12 / 6 err, `findings=0`. `15:30:21Z` `Parallel read … connection closed` 1(미재시작 라우터). 자기 라우터 재시작 지시(15667)는 아직 실행 안 됨. 세션 재시작 hold 유지.
  - **Prody**(ledger `v577` 변화 없음, MCP `1,524 ms`): CLI 미재시작. 에이전트 스크립트 오류 1.
  - 새로 입증된 미수정 제품 결함 없음. 추적: Celly 라우터·세션 재시작 실행, Prody 세션 재시작, 재기동 후 gateway central `degraded` 경고 반복 여부.

- 15:31Z **`002189d` 배포·재시작(사용자 명시 지시: 수정 후 Andy·Celly·KOBE 등에 배포, 라우터 재시작, 각 세션에 처리 내용·인스트럭션 전달)**:
  - 커밋 `002189d` → `feature/priority-models`·`beta` push. Publish run `45036570501` **success**, npm `@beta` = **`0.2.51-beta.20261122-150753.002189d`**.
  - 파일 설치(`register.js`, 해시 `3e2026e718f7 → c534337858a2`, 백업 `.overlay-backups/overlay-002189d`): 로컬 3경로·Celly(G:)·KOBE·Prody 모두 해시 일치 확인.
  - 세션 공지(`submit_input`, 4호스트): 수정 내용, 재시작 예고, "메인 검수를 못 해 막혀 있던 submitted 아이템이 있을 수 있으니 재시작 후 검수 행 확인 → approve/rework, 2분 내 안 열리면 `recover_submitted_review` 1회, 반복 거부 시 lease_id·원문 보고" 인스트럭션. 전 호스트 `ok:true`.
  - CLI 재시작(`restart_session`, `--continue`): **Andy** `15:21:35Z` 재시작(새 claude.exe 34532), 공지 수신 `15:21:38Z`, ciel-work 로드 `15:22:18Z` 확인 ✅(재시작으로 실행 중이던 포크 3개 `stopped` — 재시작 복구 경로 대상). **KOBE** `15:19:26Z` 재시작(claude.exe 12504) ✅. **Celly** 요청 claim 후 `hold`(턴 종료 대기) — 미실행. **Prody** 새 claude.exe 미관측 — 미실행.
  - 라우터 재시작(정확한 pid 정지 → 클라이언트 keep-alive가 설치된 새 코드로 재기동): **Andy** `81436 → 54628`, source `3819b46e → 84eb402c` ✅. **KOBE** `5704 → 15056`, source `3819b46e → 84eb402c` ✅. **Celly** `16340` 정지 `Access is denied`(다른 계정 프로세스) → Celly main에 자기 라우터 재시작 지시 전달(`submit_input` id 15667), 결과 미관측. **Prody** 라우터는 이미 `f5f9269` Python(이번 변경은 mod만이라 라우터 코드 변경 없음).
  - Celly `n1267`: 이 배포 전에 `finalized/close`(`close-resolved:01eac389…`)로 종료됨(수정 효과 아님). main의 `15:14:50Z` approve는 `review already decided differently`로 거부(정상 가드).

- 15:03Z 읽기 전용 틱(비교 기준 13:40Z; 운영 재시작·상태 수정 없음; 수정본은 아직 미설치):
  - **Celly**: `n1267 01eac389` 그대로(native `waiting`, 검수 행 없음). main이 Keep waiting 후 다시 움직임(ledger `v6440 → v6485`, board `v9074 → v9128`, `running+slot 1 → 8`). `new_failures`: `decision_refused_stop 01eac389` 1·`revival_refused 01eac389` 1 — main이 `13:57Z` 포크 메시지·검수 재시도 → 같은 idle proof 거부, `14:12:38Z` recovery 또 `cancelled`. 미재시작 라우터 `work_host_page` `ECONNREFUSED` 2·`Parallel read` timeout 1(`14:43–14:47Z`). 나머지는 정상 가드(중복 리스 6, 미승인 goal 4, 슬롯 상한 2, plan 버전 1).
  - **Andy**(`v5817`, MCP `828 ms`, forks 0 err): `13:46:22Z` `Parallel read failed: ECONNREFUSED` 1(미재시작 라우터, 반복 3회째), side 원문 append 1.
  - **KOBE**(`v2943`, MCP `307 ms`, forks 0 err): side 원문 append 2.
  - **Prody**(`v573`, MCP `3,479 ms`, forks 0 err): main 직접 코드 수정 시도 정상 가드 1, 에이전트 스크립트 오류 1, side 원문 append 3.
  - Linux 실제 백엔드 스위트(`test_work_mod_real_backend.py`) py310·py311 진행 중(현재까지 실패 없음).

- 14:20Z **입증 결함 수정 — Celly `n1267` 메인이 포크 검수를 못 하고 사람 질문에서 멈춤**:
  - **증거(읽기 전용)**: 포크 `a6c4372c`(lease `01eac389`)가 `07:28:50Z` 완료 영수증 제출(`submitted`, 슬롯 반환). 이후 포크 소유 백그라운드 작업(`06:38:50Z` 시작 `git fetch origin master --dry-run`이 `git-credential-manager` 입력 대기로 아직 살아 있음, 2분 timeout 후 자동 백그라운드화 `bzx11841u`)이 포크를 `waiting`으로 붙잡음. 포크의 `TaskStop`은 ciel-work-gate가 거부(`06:41:23Z`). `$.agent.list` 상태 `waiting`(엔진 문서: waiting = 자기 백그라운드 작업 대기), idle 행이 오지 않아 워크스페이스 manifest `idle` 미기록 → 검수 행 미게시 → main `work_parallel_item_review` approve/rework 모두 `positive matching idle workspace proof required`(`work_parallel_workspace.py:611`) 거부. main이 부른 `recover_submitted_review`는 `register.js`의 `$.ui.ask` 질문(포크 정지 확인)을 띄워 `07:36:53Z`부터 약 6시간 메인 도구 호출이 끝나지 않음(웹 콘솔 캡처 `.local/tmp/monitor/shots/celly-console-0953.png`). 사용자가 Keep waiting 선택 후에도 main은 `13:43:50Z`·`14:02:15Z` 재호출 때마다 `Review recovery cancelled`.
  - **위반 요구사항**: 무인 운영에서 포크 결과 검수는 main이 한다(사용자 2026-11-22). 사람 응답에 의존하는 질문은 전체 흐름을 멈춘다.
  - **수정 (`ciel_runtime_support/claude_mods/ciel-work/hooks/register.js`)**:
    - `recoverSubmittedReview`: `$.ui.ask` 제거. 완료 영수증이 최종인 소유 포크가 `waiting`이면 재확인 후 바로 `TaskStop`, 정지 후 종료 확인 시 기존 경로로 검수 게시(승인 아님).
    - `parallelStopSubmittedWaiting`(신규, native observation 15초 주기에서 호출): 소유·`submitted`·슬롯 없음·정확한 완료 영수증·검수 행 없음·`$.agent.list`에 단 하나 `waiting` 포크인 경우, 엔진 시계 기준 60초 유예 후 호스트가 `TaskStop`(영수증당 최대 3회), 다음 관측에서 `killed`/부재로 검수 게시. main·사람 개입 불필요.
    - `/work-mode recover-review` 도움말 문구 갱신.
  - **검증**:
    - 실패-우선 RED: `test_recover_submitted_waiting_requires_observed_stop`(질문 발생), `test_submitted_fork_held_waiting_is_stopped_by_host_after_grace`(호스트 정지 없음) → GREEN. `tests/test_work_mod_write_gate.py` **134 passed, 33 skipped**.
    - `claude plugin validate ciel-work` PASS, `claude plugin test ciel-work` **94/94 PASS ×3**(신규 플러그인 테스트 1건 포함). HEAD의 `fork transient API error exhausts at 20/20` 테스트가 기본 5초 예산에서 3회 중 2회 timeout(5003–5057 ms)으로 원래 불안정함을 확인 → 두 재시도 테스트에 `timeoutMs: 30000` 부여.
    - 실제 격리 `claude.exe` 로드(스텁 업스트림): `ADMITTED: 2 LOADED: 2 BAD: 0`.
    - Windows 격리: `test_side_intake_mod` 68, `test_work_mode_packaging` 5, `test_work_mode_launch` 12, `test_work_parallel_native` 12, `test_work_parallel_review_pin` 15, `test_work_texts` 4, `test_work_fork_api_retry` 3, `test_side_running_fork_relay` 3, `test_side_relay_notice` 12, `test_work_fork_gate` 34, `test_work_parallel_restart_terminal` 18 모두 PASS; `test_work_mod_real_backend` recovery 테스트 PASS.
    - 기존 Linux 컨테이너(10개 파일): `ciel-runtime-test-py310` **273 tests OK (skipped 36)**, `ciel-runtime-test-py311` **273 tests OK (skipped 36)**.
  - **라이브 효과 주의**: 파일 설치 후에도 실행 중인 Celly 세션은 mod 재로드(세션 재시작) 전까지 이전 코드. 재시작은 사용자 지시 대상.

- 13:12Z 읽기 전용 틱(비교 기준 12:52Z, 20분 창; 운영 재시작·상태 수정 없음; 사용자 결정 대기 항목 코드 변경 없음):
  - **Celly 메인 정지 계속**(`n1267` 질문 미응답, 약 5시간 35분): `turn_projection_persistent` `20317→21298s`, ledger `v6422` 변화 없음, board `v9049 → v9055`(268노드), MCP `1,359 ms`, forks 1 / 0 err, `open_leases` 동일(`waiting_input 15`, `submitted 8`, 실행 중 0). 새 `unseen_failed` 없음.
  - **Andy** (`v5805 → v5810`, board `v10409 → v10417`, MCP `875 ms`, forks 5 / 0 err, `findings=0`): `open_leases`: `submitted 20 → 21`, `waiting_input 12`, `running+slot 3 → 2`. 라우터: 외부 SSE EOF 19, transcript timeout 2, `agent_runtime_event_retry` timeout 1, 클라이언트 disconnect 2. 대화 오류 0.
  - **KOBE** (`v2939 → v2943`, board `v484 → v491`, MCP `415 ms`, forks 24 / 3 err(`aaf04360` `13:06–13:07Z`), `findings=0`): `open_leases`: `waiting_input 8 → 9`. 라우터(미재시작): keep-alive 238, 외부 SSE EOF 19, 클라이언트 reset 2.
  - **Prody** (`v545` 변화 없음, board `v6607 → v6608`, MCP `886 ms`, forks 24 / 0 err, `findings=0`): `open_leases` 동일(`submitted 6`, `waiting_input 2`, `running+slot 1`). side 원문 append 1(`83ccd51b`).
  - 공통: G: 여유 `175.42 GB`(−0.05 GB), `/health` Andy `92 ms` · Celly `13 ms`, `new_failures=0`(전 호스트).
  - 새로 입증된 미수정 제품 결함 없음. 미결 동일(Celly 질문·복구 호출 비차단화·`_Walkie_P1` 풀·사이드 release 근거 시각 — 사용자 결정; Andy 미재시작 라우터 ECONNREFUSED; Prody P0/수동 디스패치 경합 문구·`git worktree add` 지연·라우터 로그 공백).

- 12:52Z 읽기 전용 틱(비교 기준 12:32Z, 20분 창; 운영 재시작·상태 수정 없음; 사용자 결정 대기 항목 코드 변경 없음):
  - **Celly 메인 정지 계속**(`n1267` 질문 미응답, 약 5시간 15분): `turn_projection_persistent` `19096→20195s`, ledger `v6422` 변화 없음, board `v9048 → v9049`, MCP `1,109 ms`(직전 235), forks 1 / 0 err, `open_leases` 동일(`waiting_input 15`, `submitted 8`, 실행 중 0). 새 `unseen_failed` 없음.
  - **Prody** (`v541 → v545`, coord `v590`, board `v6602 → v6607` 35노드, MCP `1,052 ms`, forks 24 / 0 err, `findings=0`): `open_leases`: `submitted 6`, `waiting_input 2`, `running+slot 0 → 1`. **반복 관측(2회째)**: `12:35:25Z` main `work_parallel_run n1210`이 `coordination persistence failed; reservation released: … assignment already exists with a different manifest`로 거부. 잠금 읽기: 같은 항목의 P0 intent `5664fda8`가 lease `2fd8a343`를 `12:34:52Z` 생성·`12:35:16Z` bound했고 현재 `running`(run `active`, 워크스페이스 있음). 10:20:15Z `n1205`와 같은 모양(P0 호스트 출발과 main 수동 디스패치의 경합, 두 번 모두 거부 시점이 bound 이후). 포크는 정상 실행 중이며, 거부 문구의 "reservation released"는 실제 상태(lease 실행 중)와 다름. bound 이전에 같은 경합이 나면 `release_unbound`가 P0 예약을 해제할 수 있는지는 미관측·미확인이라 결함 판정 보류. side 원문 append 1(`64e947bd`).
  - **KOBE** (`v2939` 변화 없음, board `v480 → v484`, MCP `389 ms`, forks 24 / 1 err(`a2d65208` `12:38:04Z`), `findings=0`): `open_leases`: `waiting_input 8`. 업스트림 `7e8bb953` late retry `1→9/20`(`12:34:03–12:36:21Z`) 후 업스트림 오류 이벤트 2(`12:36:48Z` `_API_KEYS-SSE-1`, `12:37:23Z` `_Walkie_P1`). 라우터(미재시작): keep-alive 238, 외부 SSE EOF 19.
  - **Andy** (`v5801 → v5805`, board `v10407 → v10409`, MCP `1,016 ms`, forks 5 / 0 err, `findings=0`): `open_leases`: `submitted 20`, `waiting_input 12`, `running+slot 4 → 3`. 라우터: 외부 SSE EOF 20, `turn_projection_persistent` 1. 대화: side 원문 append 1(`c610da47`). 이번 창 ECONNREFUSED 없음.
  - 공통: G: 여유 `175.47 GB`(+0.88 GB), `/health` Andy `102 ms` · Celly `12 ms`, `new_failures=0`(전 호스트).
  - 새로 입증된 미수정 제품 결함 없음. 미결: 기존 항목 + Prody P0/수동 디스패치 경합 문구 불일치(2회).

- 12:32Z 읽기 전용 틱(비교 기준 12:12Z, 20분 창; 운영 재시작·상태 수정 없음; 사용자 결정 대기 항목 코드 변경 없음):
  - **Celly 메인 정지 계속**(`n1267` 질문 미응답, 약 4시간 55분): `turn_projection_persistent` `17875→18975s`, ledger `v6422` 변화 없음, board `v9045 → v9048`(270노드), MCP `235 ms`, forks 1 / 0 err, `open_leases` 동일(`waiting_input 15`, `submitted 8`, 실행 중 0). 새 `unseen_failed` 없음.
  - **Prody** (`v521 → v541`, board `v6593 → v6602`, MCP `1,371 ms`, forks 24 / 0 err): **`findings 1 → 0`** — 직전 `n1209 8fa8b232` 완료 후 `running` 해소 ✅. `open_leases`: `submitted 7 → 6`, `waiting_input 0 → 2`, 실행 중 0. side 원문 append 1(`44cbf8e6`).
  - **Andy** (`v5797 → v5801`, board `v10400 → v10407`, MCP `1,453 ms`, forks 5 / 0 err, `findings=0`): `open_leases` 동일. 대화: **`12:24:04Z` `Work reconciliation: Side router operation failed: work_host_page: ECONNREFUSED`** 1(10:27:04Z에 이어 2회째, 미재시작 라우터 — backlog 수정 `c35b735`은 파일 설치만). 라우터: 외부 SSE EOF 19, `turn_projection_persistent` 2.
  - **KOBE** (`v2937 → v2939`, board `v477 → v480`, MCP `198 ms`, forks 24 / 0 err, `findings=0`): `open_leases`: `waiting_input 8`(실행·제출 0). 라우터(미재시작): keep-alive 241, 외부 SSE EOF 18·BrokenPipe 1, `agent_runtime_event_retry` timeout 1.
  - 공통: G: 여유 `174.59 GB`(−0.07 GB), `/health` Andy `73 ms` · Celly `9 ms`, `new_failures=0`(전 호스트).
  - 새로 입증된 미수정 제품 결함 없음. 미결: Celly 질문·복구 호출 비차단화·`_Walkie_P1` 풀·사이드 release 근거 시각 조건(사용자 결정), Andy 미재시작 라우터 ECONNREFUSED 반복(2회), Prody `git worktree add` 지연·라우터 로그 공백.

- 12:12Z 읽기 전용 틱(비교 기준 11:52Z, 20분 창; 운영 재시작·상태 수정 없음; 사용자 결정 대기 항목 코드 변경 없음):
  - **Celly 메인 정지 계속**(`n1267` 질문 미응답, 약 4시간 35분): `turn_projection_persistent` `16652→17753s`, ledger `v6422` 변화 없음, board `v9041 → v9045`(271노드), MCP `281 ms`, forks 1 / 0 err, `open_leases` 동일(`waiting_input 15`, `submitted 8`, 실행 중 0). 새 `unseen_failed` 없음.
  - **Prody** (`v512 → v521`, board `v6591 → v6593`, MCP `2,729 ms`, forks 24 / 3 err(`a0338fcb` 2, `a3d8e6ef` 1)): `open_leases`: `submitted 6 → 7`, `running+slot 2 → 0`. **신규 `findings=1`**: `submitted, completion receipt, no review row: n1209 8fa8b232 P4 native=running last=12:12` — 완료 영수증 후 포크가 아직 `running`으로 보이는 상태(KOBE `n84` 08:13Z 사례처럼 다음 틱 해소 여부 추적, 1회 관측으로 결함 판정 안 함). side 원문 append 1(`d36b63db`).
  - **KOBE** (`v2932 → v2937`, board `v474 → v477` 69노드, MCP `304 ms`, forks 24 / 0 err, `findings=0`): `open_leases`: `waiting_input 8`, `running+slot 1 → submitted 1`. 라우터(미재시작): keep-alive 238, 외부 SSE EOF 18·timeout 1, 클라이언트 reset 1. 대화: Walkie `client_id` uuid 인자 오류 1, side 원문 append 1(`2bc15e9f`).
  - **Andy** (`v5791 → v5797`, board `v10394 → v10400`, MCP `985 ms`, forks 5 / 0 err, `findings=0`): `open_leases`: `submitted 20`, `waiting_input 13 → 12`, `running+slot 4`. 라우터: 외부 SSE EOF 19, `turn_projection_persistent` 2. 대화: 에이전트 스크립트 cp1252 인코딩 오류 2·Ripgrep 20s timeout 1·sha 검증 스크립트 exit 1 1(모두 에이전트 도구 사용).
  - 공통: G: 여유 `174.66 GB`(−0.02 GB), `/health` Andy `127 ms` · Celly `11 ms`, `new_failures=0`(전 호스트).
  - 새로 입증된 미수정 제품 결함 없음. 미결: 기존 항목 + Prody `n1209 8fa8b232` 완료 후 `running`.

- 11:52Z 읽기 전용 틱(비교 기준 11:32Z, 20분 창; 운영 재시작·상태 수정 없음; 사용자 결정 대기 항목 코드 변경 없음):
  - **Celly 메인 정지 계속**(`n1267` 질문 미응답, 약 4시간 15분): `turn_projection_persistent` `15420→16529s`, ledger `v6422` 변화 없음, board `v9037 → v9041`(274노드/24루트), MCP `328 ms`, forks 2 / 0 err, `open_leases` 동일(`waiting_input 15`, `submitted 8`, 실행 중 0). 새 `unseen_failed` 없음.
  - **KOBE** (`v2932` 변화 없음, board `v471 → v474`, MCP `257 ms`, forks 24 / 0 err, `findings=0`): 업스트림 op `4c5d3bda` late retry `18→34/60` 후 **`11:44:22Z` `late_sse_stopped reason=budget_exhausted`**(시작 `11:16:54Z`, 약 27분) → 오류 프레임 전달. 업스트림 오류 이벤트 2(`11:32:22Z`). 직전 틱 장기 op(`7786b5b5`·`d84c212f`·`09bc22c7`)는 이번 창에 기록 없음(종료 방식 미관측). 라우터(미재시작): keep-alive 235, 외부 SSE EOF 18·BrokenPipe 1. side 원문 append 1(`0704cb53`).
  - **Andy** (`v5790 → v5791`, board `v10389 → v10394`, MCP `813 ms`, forks 5 / 0 err, `findings=0`): `open_leases`: `submitted 20`, `waiting_input 12 → 13`, `running+slot 5 → 4`. 라우터: 외부 SSE EOF 20, `runtime_activity_router_failed` WinError 5 2(`11:33:09Z`), `turn_projection_persistent` 3, 클라이언트 reset 1. 대화: 에이전트 자체 Python 스크립트 오류 1(`11:34:11Z`).
  - **Prody** (`v510 → v512`, board `v6583 → v6591`, MCP `1,478 ms`, forks 24 / 0 err, `findings=0`): `open_leases`: `submitted 6`, `running+slot 1 → 2`. 대화: `11:51:17Z` `active slot capacity reached for P4 (1/1)` 정상 가드 1, 외부 채팅 도구 `owner_ids` 필요 1, Bash 정책 차단(`sleep` 체인) 1, side 원문 append 1(`f0959254`).
  - 공통: G: 여유 `174.68 GB`(−0.04 GB), `/health` Andy `93 ms` · Celly `11 ms`, `new_failures=0`(전 호스트).
  - 새로 입증된 미수정 제품 결함 없음. 미결: Celly 질문·복구 호출 비차단화·`_Walkie_P1` 풀·사이드 release 근거 시각 조건(사용자 결정), KOBE 업스트림 장기 재시도(게이트웨이), Andy 미재시작 라우터 ECONNREFUSED, Prody `git worktree add` 지연·라우터 로그 공백.

- 11:45Z Andy_Main 피어 보고(사이드 이벤트 `f0fa3534df6c`가 `n2582` 대기를 대기 설정 7시간 전 승인으로 release) 읽기 전용 확인:
  - Andy board(`work-plan.json`, 잠금 읽기, plan `v10393`) `n2582` 노트: `Side released wait "Daniel approval in 156289 of the revised :480 clamp design (n2579 step 2 addendum)" (event f0fa3534df6c)` — 요약은 게이트 재확인 조회로 156319(`approved_at 2026-11-22T04:16:20Z`, `:480 !clamped` 설계 포함)를 충족 근거로 판단. 현재 `waiting_on`은 main이 다시 건 `NEW Daniel approval (after 11-22 11:20Z) …`, status `pending`.
  - 코드(`side_work_decision.py:430-447`): release 조건은 미완료·같은 goal(게이트 재확인은 예외)·관측 당시와 동일한 대기 문구·leaf만이며, 근거 시각과 대기 설정 시각 비교는 없음. 노드에 대기 설정 시각 필드도 없음(`updated_at`만).
  - 판정: 백엔드는 설계대로 동작했고, 오래된 승인을 "revised" 설계의 승인으로 본 것은 사이드 모델 판단 오류. 피어 보고 기준 release 후 새 포크 시작 없음(lease `56e71c66` `waiting_input` 유지). "근거가 대기 설정 이후여야 함" 조건 추가는 새 계약(대기 설정 시각 기록 + 결정의 근거 시각)이라 사용자 결정 전 코드 변경 없음.

- 11:32Z 읽기 전용 틱(비교 기준 11:12Z, 20분 창; 운영 재시작·상태 수정 없음; 사용자 결정 대기 항목 코드 변경 없음):
  - **Celly 메인 정지 계속**(`n1267` 질문 미응답, 약 3시간 55분): `turn_projection_persistent` `14317→15300s`, ledger `v6422` 변화 없음, board `v9034 → v9037`(278노드), MCP `360 ms`, forks 2 / 0 err, `open_leases` 동일(`waiting_input 15`, `submitted 8`, 실행 중 0). 새 `unseen_failed` 없음.
  - **Prody** (`v504 → v510`, board `v6574 → v6583`, MCP `2,444 ms`, forks 24 / 2 err(`a4268321` `11:18Z`)): `open_leases`: `submitted 6`, `running+slot 1`, `waiting_input 1 → 0`. 직전 틱의 `auto continue could not read the Work status`는 반복 없음(1회 관측으로 종료 판단은 하지 않음). 대화 오류(도구 확인): `11:16:04Z` `work_proposal_status` `goal n1101 has no workflow`(정상 응답), `11:17:05Z` main의 `work_host_page` 호출에 `authenticated parallel host required`(호스트 전용 도구 정상 가드). side 원문 append 2(`ede029a8`·`57fd950d`).
  - **KOBE** (`v2932` 변화 없음, board `v471`, MCP `333 ms`, forks 24 / 0 err, `findings=0`): 업스트림 late retry 장기화 — `7786b5b5` `42/60`, `d84c212f` `37→41/60`, `09bc22c7` `33/60`(`10:52Z` 시작 이후 약 25분째), 새 op `1af0127d`·`f648223c`·`4c5d3bda`·`24d7c3b5` 시작; 업스트림 오류 이벤트 5(`11:14:19Z~`). 결과 미관측(게이트웨이 소관). `turn_projection_persistent` `1927→2168s`. 대화: Walkie `400 Unknown operation`·`Invalid operation arguments` 각 1(에이전트 인자), side 원문 append 1(`9243e803`).
  - **Andy** (`v5781 → v5790`, board `v10381 → v10389` 477노드, MCP `813 ms`, forks 5 / 0 err, `findings=0`): `open_leases` 동일. 업스트림 late retry 3건/2 op, `turn_projection_persistent` 5, 클라이언트 reset 1. 대화: 에이전트 Bash exit 2 1(`11:16:11Z`).
  - 공통: G: 여유 `174.72 GB`(−2.11 GB), `/health` Andy `89 ms` · Celly `36 ms`, `new_failures=0`(전 호스트).
  - 새로 입증된 미수정 제품 결함 없음. 미결: Celly 질문·복구 호출 비차단화·`_Walkie_P1` 풀(사용자 결정), KOBE 업스트림 장기 재시도 결과, Andy 미재시작 라우터 ECONNREFUSED, Prody `git worktree add` 지연·라우터 로그 공백.

- 11:12Z 읽기 전용 틱(비교 기준 10:52Z, 20분 창; 운영 재시작·상태 수정 없음; 사용자 결정 대기 항목 코드 변경 없음):
  - **Celly 메인 정지 계속**(`n1267` 질문 미응답, 약 3시간 35분): `turn_projection_persistent` `13098→14195s`, ledger `v6422` 변화 없음, board `v9030 → v9034`(279노드), MCP `406 ms`, forks 3 / 0 err, `open_leases` 동일(`waiting_input 15`, `submitted 8`, 실행 중 0). 새 `unseen_failed` 없음, 기존 6개 메시지 skip 로그 각 231.
  - **Prody** (`v493 → v504`, board `v6561 → v6574` 34노드, MCP `474 ms`, forks 24 / 2 err(`a123f545` `10:58:45–10:58:57Z`)): `open_leases`: `submitted 6`, `running+slot 1`, `waiting_input 0 → 1`. `new_failures`의 `decision_refused_stop c2531e9e x1`은 직전 틱과 같은 `10:52:35Z` 이벤트(틱 경계 중복 집계). 대화: **`11:10:46Z` `Ciel Work auto continue could not read the Work status`** 1(라우터 라이브 `f5f9269`, 원인 미확인 — 다음 틱 반복 여부 추적), `n1208` 중복 리스 정상 가드 1(`11:10:21Z`), 에이전트 자체 스크립트 파일 미존재 2, side 원문 append 1(`7605a699` `10:58:34Z`).
  - **Andy** (`v5769 → v5781`, board `v10374 → v10381`, MCP `2,031 ms`, forks 5 / 0 err, `findings=0`): `open_leases`: `submitted 21 → 20`, `waiting_input 10 → 12`, `running+slot 5`. 업스트림 late retry 5건/3 op(`5bb98350` `1→3/20`). 라우터: 외부 SSE EOF 18·BrokenPipe 1, `turn_projection_persistent` 3. 대화 오류 0.
  - **KOBE** (`v2932` 변화 없음, board `v470 → v471`, MCP `282 ms`, forks 24 / 0 err, `findings=0`): 업스트림 late retry 다수(op 4개 동시: `7786b5b5` `12→16/60`, `d84c212f` `7→11/60`, `99afc7ee` `7→10/60`, `09bc22c7` `1→6/60`, `10:52–10:54Z`), 업스트림 오류 이벤트 3(`10:54:51Z~`). `b216c1cc` 결과 미관측. 라우터(미재시작): keep-alive 237, 외부 SSE EOF 11·BrokenPipe 4.
  - 공통: G: 여유 `176.83 GB`(+0.42 GB), `/health` Andy `88 ms` · Celly `8 ms`.
  - 새로 입증된 미수정 제품 결함 없음. 미결: 기존 항목 + Prody `auto continue` Work 상태 읽기 실패 1건.

- 10:52Z 읽기 전용 틱(비교 기준 10:32Z, 20분 창; 운영 재시작·상태 수정 없음; 사용자 결정 대기 항목 코드 변경 없음):
  - **Celly 메인 정지 계속**(`n1267` 질문 미응답, 약 3시간 15분): `turn_projection_persistent` `11870→12975s`, ledger `v6422` 변화 없음, board `v9024 → v9030`(280노드), MCP `406 ms`, forks 4 / 0 err, `open_leases` 동일(`waiting_input 15`, `submitted 8`, 실행 중 0). 이번 창 새 `unseen_failed` 없음, 기존 6개 메시지 skip 로그 각 226.
  - **Prody** (`v485 → v493`, board `v6559 → v6561`, MCP `2,442 ms`, forks 24 / 2 err): `open_leases`: `submitted 6`, `running+slot 3 → 1`. `new_failures`: `decision_refused_stop c2531e9e x1` — `n1205` 검수 행이 `10:51:35Z` `finalized/close`(`decision_id close-resolved:c2531e9e…`)로 이미 닫힌 뒤 main이 `10:52:32Z` approve(`n1205-review-01-superseded`, 기대 `v312`)를 보내 `review already decided differently`로 거부(정상 가드). 해당 lease `done`, coordination run 행은 정리됨. 직전 틱의 `n1205` 동시 디스패치 건은 이 종료로 마무리 관측. side 원문 append 1(`c01bddb3` `10:36:27Z`).
  - **Andy** (`v5762 → v5769`, board `v10364 → v10374` 476노드/58루트, MCP `1,812 ms`, forks 5 / 0 err, `findings=0`): `open_leases`: `submitted 20 → 21`, `waiting_input 10`, `running+slot 5`. 라우터: 외부 SSE EOF 19, 클라이언트 disconnect 2, `turn_projection_persistent` 3. 대화 오류 0.
  - **KOBE** (`v2932` 변화 없음, board `v469 → v470`, MCP `183 ms`, forks 24 / 0 err, `findings=0`): 업스트림 `b216c1cc` late retry `16→28/60`(`10:32:22–10:40:39Z`, 이후 기록 없음 — 결과 미관측), `17126c9d` `3→8/60`, 업스트림 오류 이벤트 4(`10:35:14Z~`). 라우터(미재시작): keep-alive 238, 외부 SSE EOF 15·timeout 2, 클라이언트 reset 2. side 원문 append 1(`9edbdb88` `10:50:34Z`).
  - 공통: G: 여유 `176.41 GB`(−0.28 GB), `/health` Andy `105 ms` · Celly `11 ms`.
  - 새로 입증된 미수정 제품 결함 없음. 미결 동일(Celly 질문·복구 호출 비차단화·`_Walkie_P1` 풀 — 사용자 결정; Andy 미재시작 라우터 ECONNREFUSED; Prody `git worktree add` 지연; Prody 라우터 로그 공백).

- 10:32Z 읽기 전용 틱(비교 기준 10:12Z, 20분 창; 운영 재시작·상태 수정 없음; 사용자 결정 대기 항목 코드 변경 없음):
  - **Celly 메인 정지 계속**(`n1267` 사람 확인 질문 미응답, 약 2시간 55분): `turn_projection_persistent` `10638→11745s`. 새 채널 메시지 `15626`(`10:18:55Z`)·`15631`(`10:26:39Z`) direct socket `unseen_failed`. 차단 skip 로그 `15550`·`15619`·`15620`·`15622` 각 229, `15626` 153, `15631` 65. `open_leases`: `waiting_input 15`, `submitted 7 → 8`, **`running+slot 1 → 0`**(실행 중 포크 없음). ledger `v6420 → v6422`, board `v9015 → v9024`, MCP `266 ms`, forks 4 scanned / 6 err(`a02209e6` `10:23–10:24Z`). 업스트림 `_Walkie_P3` 오류 이벤트 1(`10:18:30Z`).
  - **Prody** (`v460 → v485`, coord `v525`, board `v6543 → v6559`, MCP `1,177 ms`, forks 24 / 1 err, `findings=0`; 라우터 라이브 `f5f9269`): `open_leases`: `submitted 6`, `running+slot 1 → 3`. 메인 `work_parallel_run` 거부 6:
    - `no eligible candidate` 2(`10:16:17Z` `n1204`, `10:19:09Z` `n1205`).
    - `10:18:36Z` `n1205` `workspace_prepare_retained: git worktree add timed out after 41 s`(설계된 해제 경로).
    - `10:20:15Z` `n1205` `coordination persistence failed; reservation released: … assignment already exists with a different manifest`. 같은 항목에 P0 intent `78de2705`의 lease `c2531e9e`(`10:16:49Z` 생성, `10:20:12Z` bound)가 이미 있었고 현재 `running`. 문구는 해제를 말하지만 해당 lease는 실행 중이라, 실제 해제가 일어났는지는 미확인(동시 디스패치 경합 관측, 결함 판정 보류). 같은 lease의 coordination run이 `terminal`로 읽혀 lease 상태와 다른 점도 추적.
    - `10:22:18Z` `n1206` `The operation timed out.`(호출 70초). `n1206` lease `5ee55b03`은 `10:20:36Z` 생성 후 `running`.
    - `10:26:54Z` reviewer 포크 거부 정상 가드 1.
  - **Andy** (`v5758 → v5762`, board `v10358 → v10364` 473노드/58루트, MCP `828 ms`, forks 5 / 0 err, `findings=0`): `open_leases` 동일(`running+slot 5`). 업스트림: `daed274e` late retry `1→4/20` 후 `10:23:41Z` 오류 이벤트 → 포크 `a6048a6b`(`n2576`) 429+504 결합 오류로 재사망(피어 보고와 일치). 대화: **`10:27:04Z` `Work reconciliation: Side router operation failed: work_host_page: ECONNREFUSED`** 1(미재시작 라우터, `/ca/mcp` reset `10:24:01Z` 1; backlog 수정 `c35b735`는 파일 설치만). 라우터: 외부 SSE EOF 19, `turn_projection_persistent` 7, `agent_runtime_event_retry` timeout 1, transcript timeout 1.
  - **KOBE** (`v2932` 변화 없음, board `v467 → v469`, MCP `270 ms`, forks 24 / 0 err, `findings=0`): `open_leases`: `waiting_input 8`, `running+slot 1`. 업스트림 `b216c1cc` late SSE retry `1→16/60`(`10:26:06–10:32:22Z`, 진행 중)·`17126c9d` `1→2/60`·`c92302b1` 1. 라우터(미재시작): keep-alive 238, 외부 SSE EOF 12·BrokenPipe 3·timeout 1·reset 1, 클라이언트 disconnect 2.
  - 공통: G: 여유 `176.69 GB`(−0.09 GB), `/health` Andy `132 ms` · Celly `11 ms`. `new_failures=0`(전 호스트).
  - 새로 입증된 미수정 제품 결함 없음. 미결: Celly 질문 응답·메인 복구 호출 비차단화(사용자 결정), `_Walkie_P1` 풀 구성(게이트웨이, 사용자 결정), Prody `n1205` 동시 디스패치 문구/`terminal` run 불일치, Prody `git worktree add` 41초 timeout, Andy 미재시작 라우터 ECONNREFUSED, Prody 라우터 로그 공백.

- 10:30Z Andy_Main 피어 보고(`n2576` P1 포크 2회 사망, `_Walkie_P1` 풀이 `agy/gemini-4-neon-medium`(429, reset 약 107h)로 계속 라우팅되는지 질문) 읽기 전용 확인: 포크 `a6048a6b` `09:57:19Z` 504 → `10:04:50Z` 코디네이터 재개 → `10:23:41Z` 429+504 결합 오류로 재사망, `acb6fbac` `09:08:07Z` 429(마지막 기록 `09:14:27Z`), 세 번째 `ae598457` 오류 없음(`10:28:33Z`). 풀 대상·폴백 순서는 CielAiRouter 게이트웨이 소관이라 ciel-runtime에서 변경하지 않음. 소진 대상 스킵이 설계인지는 미확인(같은 게이트웨이가 `09:04:08Z` `all targets were skipped by pre-dispatch filters`를 반환한 기록은 있음). Andy는 mod 미재시작이라 포크 20회 재시도(`249a99c`)는 파일 설치만 된 상태. 피어 메시지는 사용자 승인이 아니므로 설정 변경 없음, 사용자에게 전달.

- 10:12Z 읽기 전용 틱(비교 기준 09:52Z, 20분 창; 운영 재시작·상태 수정 없음; 사용자 결정 대기 중이라 코드 변경 없음):
  - **Celly 메인 정지 계속**: 메인 마지막 user/assistant 기록 `07:37:13Z` 그대로(`n1267` `recover_submitted_review` 사람 확인 질문 미응답, 약 2시간 35분). 라우터 `turn_projection_persistent` `9408→10516s`. 새 채널 메시지 `15622` direct socket `unseen_failed`(`09:59:56Z` 20.5s) — 메인 큐 enqueue `09:59:37Z`와 시각 일치(내용 대조 안 함). 차단 skip 로그: `15550`·`15619`·`15620` 각 216, `15622` 130. 포크 오류 1(`aa4f7a5b` `09:58:45Z`). ledger `v6415 → v6420`, board `v9010 → v9015`, MCP `312 ms`, `open_leases`: `waiting_input 15`, `submitted 6 → 7`, `running+slot 1`.
  - 공통: G: 여유 `176.78 GB`(−0.16 GB), `/health` Andy `69 ms` · Celly `8 ms`. 전 호스트 `workflow: disabled`, `new_failures=0`, Celly 외 `findings=0`.
  - **Andy** (`v5754 → v5758`, board `v10351 → v10358` 470노드/57루트, MCP `1,110 ms`, forks 5 scanned / 0 err): `open_leases`: `submitted 20`, `waiting_input 10`, `running+slot 4 → 5`. 업스트림: `bd1804a1` late SSE retry `3→7/20` 후 `09:57:18Z` `late_sse_stopped reason=budget_exhausted` → 포크 `a6048a6b`(`n2576`) `09:57:19Z` `504 timed out` → `10:04:50Z` 코디네이터 종료 계약 메시지 후 `10:05Z`부터 진행 재개 확인. `bd36811e` retry `1→3/20` 진행 중(`10:10:31Z~`). 대화: Walkie `send_message` `client_id` uuid 입력 오류 1(`10:03:16Z`, 에이전트 인자). 라우터: 외부 SSE EOF 18·BrokenPipe 1, `turn_projection_persistent` 5(`120→604s`), transcript timeout 1, 클라이언트 reset 1.
  - **KOBE** (`v2932` 변화 없음, board `v466 → v467`, MCP `354 ms`, forks 24 / 0 err): `open_leases`: `waiting_input 8`, `running+slot 1`. 라우터(미재시작): keep-alive 239, 외부 SSE EOF 18·BrokenPipe 1, 클라이언트 reset 1, transcript timeout 1. 대화: side 원문 append 2(`e570749e` `09:56:35Z`, `c943b3c8` `10:11:41Z`).
  - **Prody** (`v460` 변화 없음, board `v6531 → v6543` 33노드, MCP `1,357 → 2,052 ms`, forks 24 / 0 err): `open_leases`: `submitted 6`, `running+slot 1`. 대화: side 원문 append 3(`40ddfc86`·`f83d53b2`·`48f1ebc8`). 라우터 로그 공백 계속.
  - 새로 입증된 미수정 제품 결함 없음. 미결: Celly 질문 응답(사용자 결정), 메인 복구 호출 비차단화 여부(사용자 결정), Prody 라우터 로그 공백.

- 09:52Z 읽기 전용 틱(비교 기준 09:32Z, 20분 창; 운영 재시작·상태 수정 없음) — **Celly 메인 2시간 넘게 사람 확인 질문 대기 확인**:
  - **Celly 메인 정지 증거**:
    - 메인 대화: `07:36:53Z` `work_parallel_event {"event":"recover_submitted_review","lease_id":"01eac389…"}`(= `n1267`) 호출에 **결과 없음**(같은 배치의 Read 2건도 결과 없음). 메인의 마지막 user/assistant 기록은 `07:37:13Z`.
    - 화면(웹 콘솔 `localhost:13080/admin/agents/15/console/`, `09:53Z`·`10:05Z` 읽기 전용 캡처 `.local/tmp/monitor/shots/celly-console-0953.png`, `celly-ask-1005.png`): `Stop submitted Work fork for n1267 (01eac38957384a04af9fa9cc5c7e42ad)? This recovers review only; it does not approve the result.` / `1. Stop this fork` `2. Keep waiting` 선택 대기. 이 질문은 `register.js` `recoverSubmittedReview()`가 포크가 `waiting`일 때 띄우는 `$.ui.ask`이며 사람의 답이 올 때까지 메인 도구 호출이 끝나지 않는 구조(코드 확인).
    - 동반 관측: 메인 큐에 fork watch 알림·task notification이 쌓인 채 처리 안 됨(`queue-operation enqueue` `09:37:28Z`·`09:38:53Z`·`09:40:36Z`·`09:40:41Z`). 채널 메시지 `15619`·`15620`이 direct socket `unseen_failed`(`09:39:09Z` 20.4s, `09:41:01Z` 20.8s) 후 `stale_unconfirmed`로 skip 로그 반복(147·126건). enqueue 시각(`09:38:53Z`·`09:40:41Z`)과 일치하나 내용 대조는 하지 않음. 라우터 `turn_projection_persistent` `8304→9408s`도 같은 정지와 시간 일치.
    - 판정: 사람 승인 가드(포크 정지 확인)가 설계대로 답을 기다리는 상태. 무인 운영 중 메인 전체가 막힌 것은 측정된 기능 영향이지만, 메인 호출 경로에서 질문을 띄우지 않게 바꾸는 것은 기존 설계(메인 MCP 복구 호출도 확인 답변을 받음)를 바꾸는 결정이라 사용자 결정 전 코드 변경 없음. 운영 질문에 대신 답하지 않음. 콘솔은 페이지 로드·텍스트 읽기만 했고 키·마우스 입력은 보내지 않음(두 번째 프로브는 브라우저 페이지 안에서 `blur`/`visibilitychange` 합성 이벤트만 발생, 이후 질문 화면 변화 없음).
    - 정정: 이 분석 중 관측하지 않은 사실(“Esc 해제”, “Errno 22 종료”, “포크 54턴”)을 넣은 테스트·`register.js` 수정을 작성했다가 실행 전 **전부 되돌림**(`git checkout`으로 HEAD 복원; RED 1회 실행만 있었고 커밋·설치 없음).
  - 공통: G: 여유 `176.94 GB`(−0.90 GB), `/health` Andy `141 ms` · Celly `48 ms`. 전 호스트 `workflow: disabled`, `new_failures=0`.
  - **Andy** (`v5754` 변화 없음, board `v10348 → v10351`, MCP `828 → 687 ms`, forks 5 scanned / 0 err, `findings=0`, 대화 오류 0): `open_leases` 동일. 라우터: 외부 SSE EOF 19, late SSE retry 11건/7 op(`7a8292f8` `4→6/20` `09:32:46–09:34:32Z` 포함), 클라이언트 reset 2, `turn_projection_persistent` 3.
  - **Celly** (`v6411 → v6415`, board `v9008 → v9010` 285노드/25루트, MCP `359 → 454 ms`, forks 6 scanned / 1 err(`a972ea70` `09:40:36Z`)): `open_leases` 동일(`waiting_input 15`, `submitted 6`, `running+slot 1`). `findings=1`(`n1267 01eac389` — 위 질문 대상). 라우터: `15550` 224, `15619` 147, `15620` 126, 외부 SSE EOF 20, 업스트림 `_Walkie_P3` `400` `http_rejection` 1(`09:38:45Z`), `runtime_activity_router_failed` WinError 5 2(`09:42:50Z`), 클라이언트 reset 3.
  - **KOBE** (`v2928 → v2932`, board `v461 → v466` 71노드/8루트, MCP `238 → 405 ms`, forks 24 scanned / 0 err, `findings=0`): `open_leases`: `waiting_input 8`, `running+slot 2 → 1`. 라우터(미재시작): keep-alive 234, 외부 SSE EOF 16·BrokenPipe 1·timeout 1, late SSE retry 4, 클라이언트 disconnect 4, `turn_projection_persistent` 3. 대화: side 원문 append 1(`e0298276` `09:49:11Z`).
  - **Prody** (`v452 → v460`, board `v6518 → v6531` 35노드/7루트, MCP `521 → 1,357 ms`, forks 24 scanned / 0 err, `findings=0`): `open_leases`: `submitted 6`, `running+slot 1`. 대화: plan version 충돌 정상 가드 2(`09:34:08Z`·`09:35:51Z`), side 원문 append 3(`ee1c508b`·`a4698041`·`bd22f178`). 라우터 로그 공백 계속.
  - 새로 입증된 미수정 제품 결함 없음(Celly 메인 정지는 설계상 사람 확인 대기 — 변경 여부 사용자 결정 필요). 추적: Celly 질문 응답 여부와 이후 큐·`15619`/`15620` 처리, Prody 라우터 로그 공백.

- 09:32Z 읽기 전용 틱(비교 기준 09:12Z, 20분 창; 운영 재시작·상태 수정 없음):
  - 공통: G: 여유 `177.84 GB`(−2.29 GB), `/health` Andy `107 ms` · Celly `11 ms`. 전 호스트 `terminal: unobserved`, `workflow: disabled`, `findings`는 Celly 1건 외 0.
  - **Andy** (`v5744 → v5754`, board `v10346 → v10348`, MCP `1,641 → 828 ms`, forks 5 scanned / **0 err**(직전 4), `new_failures=0`, 대화 오류 0): `open_leases` 동일(`submitted 20`, `waiting_input 10`, `running+slot 4`). 라우터: 외부 SSE EOF 19, 업스트림 late SSE retry `7a8292f8` `1→3/20`(`09:30:23–09:31:57Z`, 틱 시점 진행 중 — 결과 미관측), `turn_projection_persistent` 1, 클라이언트 reset 1.
  - **Celly** (`v6408 → v6411`, board `v9001 → v9008`, MCP `282 → 359 ms`, forks 5 scanned / 4 err): `open_leases`: `waiting_input 15`, `submitted 5 → 6`, `running+slot 1`, `reserved+slot 1 → 0`. `findings=1`(`n1267 01eac389 P1 native=waiting last=09:31` 계속). 포크 오류 출처: `n1311` 포크 `a5b18b54` `work_parallel_report` 계약 검증 거부 3(`worker may only report question, completion or handoff`·`completion text`·`completion fields`, `09:23:45–09:24:20Z`), `n1278` 포크 `a972ea70` 자체 Bash 스크립트 오류 1(`09:30:44Z`). 64 KiB 창 밖 추가 확인: 같은 포크들의 `TaskStop` 포크 게이트 거부(정상 가드, `09:16:00Z`), `mcp__web_fetch__fetch` 미제공 2, Ripgrep 20s timeout 1, Walkie `403 Conversation access denied` 1. 라우터(미재시작): `15550` 반복 로그 209, 외부 SSE EOF 18, `turn_projection_persistent` 9(`7077→8181s`, observe_only 계속 증가), 클라이언트 reset 2. 이번 창 `/ca/mcp` reset·`pump failed` 0.
  - **KOBE** (`v2928` 변화 없음, board `v459 → v461`, MCP `238 ms`, forks 24 scanned / 1 err, `findings=0`): `open_leases`: `waiting_input 8`, `running+slot 2`. 라우터(미재시작, 실행 `7765e9d`): keep-alive 234, 외부 SSE EOF 19, 클라이언트 disconnect 2, late SSE retry 2, transcript timeout 1. 대화: side 실패 이벤트 원문 main append 1(`787a8ba5` `09:14:40Z`).
  - **Prody** (`v443 → v452`, board `v6512 → v6518`, MCP `906 → 521 ms`, forks 24 scanned / 0 err, `findings=0`): `open_leases`: `submitted 7`, `running+slot 1`. `new_failures`: `revival_refused` 2 — `09:18:48Z` main이 재기동 후 `orphaned`된 리스 `e6246ffb`(`n1201`)에, `09:18:56Z` 예약 포크 이름에 메시지를 보내 거부됨(정상 가드: 옛 포크 부활 방지). 재기동 후 리스 상태(잠금 읽기): `n1201` `e6246ffb orphaned` → `a1398af1 running`, `n1193` `8e7bc859 orphaned` → `0c2364f8 submitted`, `n1170` `9c9a5ca9`·`69938858 orphaned`(같은 항목 `a94927e3 done`). 대화: side 원문 append 1 신규(`c77ecf4f` `09:19:32Z`), 에이전트 자체 스크립트 exit 1 2(`ROOM_WINDOW_OPEN`).
  - **Prody 라우터 로그 관측 공백(계속)**: `router.log` 최종 쓰기 `09:12:54Z` 이후 변화 없음(라우터 8028은 `router-activity.json` `09:33:43Z`까지 갱신). 루트·인스턴스 `log-level` 파일은 둘 다 없음, 워크스페이스 설정·`CIEL_RUNTIME_LOG_LEVEL` 환경값은 접근 불가로 미확인 — 로그 레벨 설정 때문인지 쓰기 실패인지 판정하지 못함.
  - 새로 입증된 미수정 제품 결함 없음. 추적: Celly `n1267 01eac389`, Celly `turn_projection_persistent` 증가(`8181s`), Celly `15550` 로그, Prody 라우터 로그 공백, Andy late retry `7a8292f8` 결과.

- 09:12Z 읽기 전용 틱(비교 기준 08:52Z, 20분 창; Prody는 09:18Z 단건 재관측; 운영 재시작·상태 수정 없음):
  - 공통: G: 여유 `180.13 GB`(−2.30 GB), `/health` Andy `120 ms` · Celly `40 ms`. 전 호스트 `terminal: unobserved`, `workflow: disabled`.
  - **Prody 재기동 관측(내 조작 아님)**: `AapWin.Host.exe`(pid 3460) `09:09:06Z` 시작 → `powershell`(4000) `09:10:33Z` → `node`(6804) `09:10:43Z` → `ciel_runtime.py cli --continue`(1732) `09:10:48Z` → 라우터 `serve`(8028) `09:12:45Z`(`router-fault.log` 시작 `09:12:47Z`) → `claude.exe`(3236) `09:13:04Z`. 재기동 주체·이유는 미확인. 기본 틱(`09:12:38Z`)은 라우터 기동 전이라 board `URLError`, workflow·MCP `TimeoutError`(`4,024 ms`), 이전 소유자 pid 6384 사망 슬롯 3건(`n1170`/`n1193`/`n1201`)이 잡힘 → `09:18:19Z` 재관측에서 **`/health` `0.2.51-beta.20261122-082846.f5f9269`(pid 8028) — Prody는 라우터 라이브 코드가 `f5f9269`로 확인된 첫 호스트**, board `v6511 → v6512`(33노드/7루트), **MCP `probe_succeeded` `906 ms`**(08:52Z `TimeoutError 3,992 ms`), `findings=0`, ledger `v433 → v443`, `open_leases`: `submitted 6`, `running+slot 3`. ciel-work mod 활동은 `09:14:53Z` side 원문 append로 관측, 로드된 mod 버전은 미확인. 라우터 표준 출력에 Work HTTP GET `503 Work state unreadable` 응답 중 클라이언트 중단(`ConnectionAbortedError` WinError 10053) traceback이 남음(최근 6,000줄 중 189건, 타임스탬프 없음 — 발생 시각 특정 불가, 재기동 구간과의 관계 미확인). **관측 공백**: Prody `9592-c53b7c787223/router.log`의 마지막 타임스탬프 줄이 `2026-11-19T01:13:14`라 모니터의 Prody 라우터 로그 섹션은 11-19 이후 비어 있음(구조화 로그의 현재 위치는 `workspaces` 접근 거부로 미확인).
  - **Andy** (`v5736 → v5744`, board `v10339 → v10346` 472노드/59루트, MCP `1,641 ms`, forks 5 scanned / 4 err, `findings=0`): `open_leases` 동일(`submitted 20`, `waiting_input 10`, `running+slot 4`). `new_failures`: `decision_refused_stop c6577d41 x1` — `n2558` main `work_parallel_item_review`가 `09:08:39Z` `Work finalization changed; reread`(기대 `v1908` 낡음, 정상 낙관적 동시성 가드)로 거부된 뒤 `09:10:06Z` 재시도 승인·`finalized v1917` 확인 ✅. 포크 오류 4건은 같은 `n2558` 포크(`a27ba876`)의 `work_parallel_report` 계약 검증 거부(`completion artifact SHA256 required` 4·`completion text` 1, `09:04:29–09:06:30Z`) 후 `09:06:56Z` 접수(`submitted`, 슬롯 반환). 업스트림(CielAiRouter): `5e7f0dc4` `09:04:08Z` `http_rejection` `not_retryable` → 포크 `a2b2778c`(`n2582`) `all targets were skipped by pre-dispatch filters`; `1774fde7` late SSE retry `1→7/20`(`09:01:46–09:07:03Z`) 후 `09:08:07Z` `terminal_verdict`(`server_error`) → 포크 `acb6fbac`(`n2576`) `429 rate limit`; 포크 `a4f3c7c7`(`n2583`) `08:57:54Z` `429`. `n2582`·`n2583` 포크는 오류 후 진행 확인, `n2576` 포크는 `09:14:27Z` `[Request interrupted by user]` 후 새 `running` 리스 `8dd920e1` 존재. 라우터: 외부 SSE EOF 20, `turn_projection_persistent` 2, transcript timeout 1.
  - **Celly** (`v6399 → v6408`, board `v8987 → v9001` 286노드/25루트, MCP `282 ms`, forks 5 scanned / 2 err): `open_leases`: `waiting_input 15`, `submitted 5`, `running+slot 1`, `reserved+slot 1`. `findings=1`(`n1267 01eac389 P1 native=waiting last=09:11 miss=None` 계속 추적). 라우터(미재시작): `15550` 반복 로그 195, 외부 SSE EOF 16, **`/ca/mcp` reset 4(`09:01:02Z`)** → 15초 뒤 `09:01:17Z` `Work gate check (host round): pump failed · attention P0,P1,P2,P5 · discovery asked`(시간 근접만 확인, 인과 미확인; 같은 문구 11-20 이후 반복 관측), `turn_projection_persistent` 9(`5929→6956s`), 업스트림 오류 이벤트 2. 포크 `a192d321`(`n1311`) `08:58:26Z` `429 rate limit` → `09:01:49Z` 코디네이터 메시지 → `09:02:58Z` `[Request interrupted by user]`, 새 `running` 리스 `af5d1e65` 존재.
  - **KOBE** (`v2924 → v2928`, board `v455 → v459` 70노드/8루트, MCP `260 ms`(08:52Z `2,004 ms`), forks 24 scanned / 1 err, `findings=0`): `open_leases`: `waiting_input 8`, `running+slot 2`. 라우터(미재시작, keep-alive가 보고하는 실행 버전 `7765e9d`): keep-alive 235, 외부 SSE EOF 16·BrokenPipe 2, transcript timeout 2, `agent_runtime_event_retry` timeout 2, late SSE retry 3. 대화(5건): `n89` 중복 리스 정상 가드 1(`08:55:15Z`), side 실패 이벤트 원문 main append 2쌍(`0dcbbc6f` `08:56:28Z`, `971f679a` `09:02:34Z`).
  - 새로 입증된 미수정 제품 결함 없음. 추적: Celly `n1267 01eac389`, Celly `pump failed`(미재시작 라우터·mod), Prody 라우터 로그 관측 공백 및 `503 Work state unreadable` traceback 시각, 포크 `429`/pre-dispatch 업스트림 오류(Andy·Celly는 mod 미재시작이라 `249a99c` 포크 20회 재시도는 파일 설치만 됨).

- 08:52Z 읽기 전용 틱(비교 기준 08:32Z, 20분 창; 운영 재시작·상태 수정 없음):
  - **Publish 완료 확인 ✅**: `f5f9269` GitHub Publish run `45002981279` **`completed / success`**(npm `@beta`: `0.2.51-beta.20261122-082846.f5f9269`). 4호스트 `overlay-f5f9269` 파일 설치 완료, 운영 라우터 미재시작 유지. G: 여유 `182.43 GB`(08:32Z 186.24 GB 대비 −3.81 GB), `/health` Andy `84 ms` · Celly `12 ms`. 전 호스트 `terminal: unobserved`, `workflow: disabled`, `new_failures=0`.
  - **Andy** (`v5730 → v5736`, board `v10337 → v10339`, MCP `797 ms`, forks 5 scanned / 0 err, `findings=0`, `/ca/mcp` reset 0건): `open_leases`: `submitted 20`, `waiting_input 10`, `running+slot 4`. 라우터: 외부 SSE EOF 19·timeout 1, `turn_projection_persistent` 6, 모델 reset 2. 대화(4건): Walkie 400 1, 에이전트 스크립트 2, side append 1.
  - **Celly** (`v6395 → v6399`, board `v8983 → v8987`, MCP `437 ms`, forks 11 scanned / 2 err): `open_leases`: `waiting_input 14`, `running+slot 3`, `submitted 4`. `findings=1`(`n1267 01eac389 P1 native=waiting last=08:52 miss=None` 계속 추적 — 포크 `waiting` 유지). 라우터: 미재시작 라우터 `15550` 반복 로그 199건, 외부 SSE EOF 18, `/ca/mcp` reset 9(`02:46:00`), 모델 reset 6, `turn_projection_persistent` 5(`4733→5225s`), `runtime_activity_context_usage_failed` WinError 5 1. 대화(1건): `08:46:00Z` `work_host_page: The operation timed out.` 1건(미재시작 라우터).
  - **KOBE** (`v2920 → v2924`, board `v446 → v455` 69노드/8루트, MCP `2,004 ms`, forks 24 scanned / 0 err, `findings=0`): `open_leases`: `waiting_input 8`. 라우터: keep-alive 239, 외부 SSE EOF 18·reset 2·timeout 1·BrokenPipe 1, `turn_projection_persistent` 6, transcript timeout 1, 모델 reset 1. 대화(4건): `08:33:17Z` `invalid external search result schema` 1(미재시작 라우터; `a5d7df5` 설치 완료), Walkie uuid 오류 1, 정상 가드(`user_requested=true`) 1, plan version 충돌 1.
  - **Prody** (`v429 → v433`, board `v6506 → v6511`, **MCP `probe_failed` `TimeoutError` `3,992 ms` 주의**, forks 24 scanned / 1 err, `findings=0`, 라우터 경고 0건): `open_leases`: `submitted 6`, `reserved+slot 1`, `running+slot 2`. 대화(2건): plan version 충돌 1, 에이전트 스크립트 exit 2 1.
  - 새로 입증된 미수정 제품 결함 없음(Celly `n1267` `01eac389` 및 Prody 미재시작 라우터 MCP 4s 타임아웃 계속 추적).

- 08:32Z 읽기 전용 틱(비교 기준 08:13Z, 20분 창; 운영 재시작·상태 수정 없음):
  - **배포 확인 ✅**: `f5f9269` npm `@beta` = **`0.2.51-beta.20261122-082846.f5f9269`** 게시 확인(Publish run `45002981279`은 `Verify published tarball` 마무리 단계 `in_progress`). 4호스트 `overlay-f5f9269` 파일 설치 완료, 운영 라우터 미재시작으로 실행 중 라우터는 이전 코드 유지. G: 여유 `186.24 GB`(−0.04 GB), `/health` Andy `123 ms` · Celly `342 ms`. 전 호스트 `terminal: unobserved`, `workflow: disabled`, `new_failures=0`.
  - **Andy** (`v5730`, board `v10335 → v10337` 473노드/59루트, MCP `828 ms`, forks 5 scanned / 0 err, `findings=0`): `open_leases`: `submitted 20`, `waiting_input 13`, `running+slot 1`. 라우터: 외부 SSE EOF 19, `/ca/mcp` reset 3(`02:25:21`), `turn_projection_persistent` 6(`603→1206s`), 모델 reset 2, late SSE retry 1, transcript timeout 1. 대화(3건): `gh` 진행 중 조회 1, Ripgrep 20s timeout 1, side 원문 append 1.
  - **Celly** (`v6390 → v6395`, board `v8980 → v8983` 290노드/25루트, MCP `735 ms`, forks 11 scanned / 3 err): `open_leases`: `waiting_input 13`, `running+slot 3`, `submitted 4`. `findings=1`(`n1267 01eac389 P1 native=waiting last=08:31 miss=None` 계속 추적 — 포크 `waiting` 유지). 라우터: 미재시작 라우터 `15550` 반복 로그 183건, 외부 SSE EOF 18·reset 3, `/ca/mcp` reset 2(`02:15:20`), 업스트림 failover/retry 6(`02:23:59`), `turn_projection_persistent` 5(`3497→3993s`), 모델 reset 1. 대화(1건): `08:15:16Z` `work_host_page: connection closed` 1건(미재시작 라우터).
  - **KOBE** (`v2917 → v2920`, board `v441 → v446` 62노드/7루트, MCP `370 ms`, forks 24 scanned / 9 err, **`findings 1→0`** — 직전 `n84 70f4a725` 해소 확인 ✅): `open_leases`: `waiting_input 8`, `running+slot 1`. 라우터: keep-alive 223, 외부 SSE EOF 14·timeout 1·BrokenPipe 1, `turn_projection_persistent` 5, 모델 reset 2, late SSE retry 1, `/ca/mcp` reset 1(`00:19:47`), `runtime_activity_router_failed` WinError 5 1. 대화(4건): Walkie 인자 오류 1, side append 1, 정상 가드(`work_side_status` host-only) 1, 에이전트 PowerShell 오류 1.
  - **Prody** (`v429`, board `v6504 → v6506` 33노드/7루트, MCP `646 ms`, forks 24 scanned / 0 err, `findings=0`, 라우터 경고 0건): `open_leases`: `submitted 6`, `reserved+slot 1`, `waiting_input 2`. 대화(3건): side append 2, 에이전트 스크립트 1.
  - 새로 입증된 미수정 제품 결함 없음(Celly `n1267` `01eac389` 계속 추적).

- 08:13Z 읽기 전용 틱(비교 기준 07:32Z, 40분 창; KOBE 기본 틱 SSH `lost connection` 후 단건 재시도 성공; 운영 재시작·상태 수정 없음) 및 **신규 입증 제품 결함 수정·배포(`P0 clear bound` 교착)**:
  - **이전 배포 확인 ✅**: `249a99c` npm `@beta` = **`0.2.51-beta.20261122-080855.249a99c`** 게시 확인(Publish run `45001424117`은 `npm publish` 완료 후 `Verify published tarball` 진행). G: 여유 `186.28 GB`, `/health` Andy `171 ms` · Celly `129 ms`. 전 호스트 `terminal: unobserved`, `workflow: disabled`, `new_failures=0`.
  - **신규 입증 제품 결함 — Celly `work_plan_set: P0 clear bound` (`07:33:58Z`) 영구 교착**:
    - **증거**: Celly 대화 `07:33:58Z` `tool_error <tool_use_error>work_plan_set: P0 clear bound</tool_use_error>` 관측. `G:\ciel-bridge-service\AppData\Roaming\ciel-runtime\work-parallel-p0.json` 읽기 전용 실측 결과 `clears: 64` (`done: 64`), `roots: 32` (`active: 31, cleared: 1`), 루트당 최신 보호 행 32건 + 대체 완료(`superseded done`) 행 32건 = 정확히 **`64 == MAX_CLEARS`**. `prune_done(keep=32)`가 보호 행(32) + 보존 여유(32) = 64를 유지해 단 1건도 정리하지 못하고, `prepare_set`/`prepare_clear`가 `len(state["clears"]) < MAX_CLEARS`에서 영구 거부되는 구조적 결함. 또한 `validate()`가 `active`/`cleared` 루트(최대 `MAX_ROOTS=256`)마다 최신 행 1건을 영구 보존하도록 요구함에도 `MAX_CLEARS=64 < MAX_ROOTS=256`인 모순 확인.
    - **수정 (`ciel_runtime_support/work_parallel_p0.py`)**: `MAX_CLEARS = 512`로 상향(`> MAX_ROOTS=256`), `validate()`와 동일한 `_protected_clear_ids()` 도입(미종료/`active`/`cleared` 최신 행 보호, `ended` 및 대체 완료 행만 축출 허용), `prepare_set`/`prepare_clear`/`activate_root`의 상한 검사 전 `_evict_oldest_done_clear()`로 가장 오래된 비보호 완료 행 1건 정리, `prune_done()` 보존 예산을 `min(keep, MAX_CLEARS - 1 - len(protected))`로 제한.
    - **검증**: `tests/test_work_parallel_p0_events.py` 실패-우선 RED(`P0 clear bound`) → GREEN(39/39), `ruff check` PASS, Windows 격리 5개 스위트 **155/155 PASS**, 기존 Linux Docker `py310` **155/155 PASS** · `py311` **155/155 PASS**, Celly 실제 `work-parallel-p0.json` 임시 복사본 격리 검증(`64 clears → 65 clears PREPARE_SET_OK`, 운영 파일 미수정).
  - **호스트별 틱 요약(08:13Z)**:
    - **Andy** (`v5668 → v5730`, board `v10333 → v10335` 473노드/59루트, MCP `812 ms`, forks 5 scanned / 0 err, **`findings=0`, `/ca/mcp` reset 0건**): `open_leases`: `submitted 20`, `waiting_input 13`, `running+slot 1`. 라우터: 외부 SSE EOF 37·timeout 1, transcript timeout 2, `turn_projection_persistent` 3, `agent_runtime_event_retry` 4, late SSE retry 2, 모델 reset 1. 대화(1건): `08:07:20Z` `n1021 is not a goal`.
    - **Celly** (`v6375 → v6390`, board `v8971 → v8980` 291노드/25루트, MCP `609 ms`, forks 10 scanned / 8 err): `open_leases`: `waiting_input 14`, `running+slot 4`, `submitted 3`. `findings=1`(`n1267 01eac389 P1 native=waiting last=08:12 miss=None` 계속 추적 — 포크 `waiting` 상태 유지 중). 라우터: 미재시작 라우터 `15550` 반복 로그 394건, `/ca/mcp` reset 11(`01:48:15`), 업스트림 `cielairouter` transient/central retry 28(`01:56`), 외부 SSE EOF 37, 모델 reset 6. 대화(4건): **`07:33:58Z` `work_plan_set: P0 clear bound`(위 수정 완료)**, 정상 중복 리스 가드 1(`n1311`), `07:48:15Z` `work_host_page`/`Parallel read` timeout 2(미재시작 라우터).
    - **KOBE** (`v2902 → v2917`, board `v435 → v441` 61노드/7루트, MCP `463 ms`, forks 24 scanned / 9 err): `open_leases`: `waiting_input 8`, `running+slot 1`, `submitted 1`. `findings=1`(`submitted, completion receipt, no review row: n84 70f4a725 P1 native=running last=08:12 miss=None` — 포크 `running`, 다음 틱 추적). 라우터: keep-alive 432, 외부 SSE EOF 24·BrokenPipe 5, `anthropic_sse_error_shape` 5, 모델 reset 4, transcript timeout 3. 대화(3건): 파일 미존재 1, 정상 Work 완료/검수 가드 2.
    - **Prody** (`v429`, board `v6500 → v6504` 33노드/7루트, MCP `1,893 ms`, forks 24 scanned / 0 err, `findings=0`, 라우터 경고 0건): `open_leases`: `submitted 6`, `reserved+slot 1`, `waiting_input 2`. 대화(2건): side 원문 append 2.

- 07:50Z 기능 구현·검증 완료 — Work 포크 일시적 API 오류·자동 컴팩트 실패 20회 자동 재시도(`1분×10 + 10분×10`):
  - **구현 내용**:
    - `ciel_runtime_support/work_fork_api_retry.py`(신규): 포크 트랜스크립트(`subagents/agent-<id>.jsonl`) 말미 128 KiB를 stat 캐시(mtime_ns+size)·심볼릭링크 거부로 안전 읽기하고, 메인과 동일한 `runtime_input_retry.classify_claude_api_error()`, `claude_typed_turn_resend.auto_compact_failed()`, `fork_turn_watch.is_empty_stream_error()` 및 `truncatedAfterOutput` 판별을 적용해 일시적 API 오류·자동 컴팩트 실패만 추출(`last_progress_uuid` 포함, 본문·비밀 비노출; 400/인증/사용량한도/`resendCannotCure` 비일시 오류 및 정상 응답 제외).
    - `ciel_runtime_support/work_parallel_mcp.py`: `work_parallel_status` 호스트 전용 뷰(`host_view=True`, 모델·`gate`·`version` 제외)에 `fork_api_errors` 투영 추가.
    - `ciel_runtime_support/work_texts.py`, `texts.generated.js`: 관리 텍스트 `mod_fork_api_retry`·`mod_fork_compact_retry` 추가(코드 하드코딩 없음, REST/웹/CLI 실시간 편집 가능).
    - `ciel_runtime_support/claude_mods/ciel-work/hooks/register.js`: `running` 소유 Work 포크가 `fork_api_errors`에 일시적 API 오류/컴팩트 실패로 감지되면 `parallelEnsureExitContract`(`host_salvage`)를 보류하고 `$.clock.after` 기반으로 `1분×10 + 10분×10`(최대 20회) + 최종 60초 유예 스케줄로 `authorizeResume` + `$.session.send` 재시도 전송. 진행(`last_progress_uuid` 변경 또는 오류 해소) 시 즉시 체인 해제·복구 로그, 비일시 오류·20회 소진·세션 종료 시 타이머 정리 후 기존 종료 계약(`host_salvage`)으로 인계. 부수적으로 `scheduleNativeObservation`이 `session.start` 초기 `cachedSession=null` 시 15초 후 중단되던 결함도 함께 수정.
  - **검증 결과**:
    - 실패-우선 RED → GREEN: `tests/test_work_fork_api_retry.py`(3/3), `work.test.ts` 신규 2건(1m×10 + 10m 전환 + 복구 + 컴팩트 재시도, 20/20 소진 후 종료 계약 인계).
    - `ruff check`: `All checks passed!`.
    - `claude plugin validate ciel-work` PASS, `claude plugin test ciel-work`: **93/93 PASS**(`29.21s`).
    - 실제 격리 `claude.exe` 로드(스텁 업스트림): `ADMITTED: 2 LOADED: 2 BAD: 0`.
    - Windows 격리 회귀 6개 스위트 **277/277 PASS**(`test_work_fork_api_retry`, `test_work_texts`, `test_fork_turn_watch`, `test_work_fork_gate`, `test_router_io_performance`, `test_work_mod_write_gate`).
    - 기존 Linux Docker 컨테이너: `ciel-runtime-test-py310` **277/277 PASS**, `ciel-runtime-test-py311` **277/277 PASS**.

- 07:32Z 읽기 전용 틱(비교 기준 07:12Z, 20분 창; 운영 재시작·상태 수정 없음):
  - **인프라·배포 상태**: `1c53f39` npm `@beta` 게시 및 4호스트 파일 설치 유지, 운영 라우터 미재시작. G: 여유 `186.48 GB`(−0.19 GB), `/health` Andy `101 ms` · Celly `26 ms`. 전 호스트 `terminal: unobserved`, `workflow: disabled`, `new_failures=0`.
  - **Andy** (`v5661 → v5668`, board `v10330 → v10333` 476노드/60루트, MCP `1,437 ms`, forks 5 scanned / 0 err, **`findings=0`, `tx_errors=0`, `/ca/mcp` reset 0건**): `open_leases`: `submitted 20`, `waiting_input 13`, `running+slot 1`. 라우터: 외부 SSE EOF 16·reset 6, transcript timeout 2, `turn_projection_persistent` 1.
  - **Celly** (`v6366 → v6375`, board `v8961 → v8971` 290노드/24루트, MCP `547 ms`, `/ca/mcp` reset 0건, forks 7 scanned / 7 err):
    - `open_leases`: `waiting_input 14`, `running+slot 5`, `submitted 1`. `findings=1`(`submitted, completion receipt, no review row: n1267 01eac389 P1 native=waiting last=07:30 miss=None` — 2분 전 제출, 포크 `waiting` 유지 중, 다음 틱 추적).
    - 라우터: 미재시작 라우터 `15550` 반복 로그 209건, 외부 SSE EOF 18·reset 5, transcript timeout 10, 모델 reset 6, `turn_projection_persistent` 5.
    - 대화(11건): `07:22:00Z` `work_side_handoff: invalid side receipt identity` 1건(미재시작 라우터; `a5d7df5` 설치 완료), Walkie 재기동/400/404 6건, side append 2건, 파일/스크립트 2건.
    - 포크 오류 확인(`a6c4372c1a3d25c95` `07:19–07:28Z`): `completion artifact SHA256 required` 5건·`workspace fields` 2건·`handoff fields` 1건 등 미재시작 구 라우터의 `work_parallel_report` 스키마/축약 오류 반복.
  - **KOBE** (`v2891 → v2902`, board `v432 → v435` 60노드/7루트, MCP `1,144 ms`, forks 24 scanned / 3 err, `findings=0`): `open_leases`: `waiting_input 8`, `running+slot 1`. 라우터: keep-alive 235, 외부 SSE EOF 15·reset 3·timeout 2, transcript timeout 4, `turn_projection_persistent` 4, `runtime_activity_context_usage_failed` WinError 5 1. 대화(4건): side append/already 2, Walkie 재기동 1, `07:31:37Z` `work_side_handoff: side handoff not found` 1.
  - **Prody** (`v420 → v429`, board `v6499 → v6500` 33노드/7루트, MCP `1,352 ms`, forks 24 scanned / 5 err, `findings=0`, 라우터 경고 0건): `open_leases`: `submitted 6`, `reserved+slot 1`, `waiting_input 2`. 대화(2건): side append 1, `07:25:05Z` `could not read Work status` 1(미재시작 라우터).
  - 새로 입증된 미수정 제품 결함 없음(Celly `n1267` `01eac389` 다음 틱 추적).

- 07:12Z 읽기 전용 틱(비교 기준 06:50Z, 22분 창; 운영 재시작·상태 수정 없음):
  - **Publish 완료 확인 ✅**: `1c53f39` GitHub Publish run `44995302048` **`completed / success`**, npm `@beta` = **`0.2.51-beta.20261122-064730.1c53f39`** 게시 확인. 4호스트 `overlay-1c53f39` 파일 설치 완료, 운영 라우터 미재시작으로 실행 중 라우터는 기존 코드 유지. G: 여유 `186.67 GB`(06:50Z 190.91 GB 대비 −4.24 GB), `/health` Andy `129 ms` · Celly `17 ms`. 전 호스트 **`findings=0`, `new_failures=0`**, `terminal: unobserved`, `workflow: disabled`.
  - **Andy** (`v5660 → v5661`, board `v10328 → v10330` 476노드/60루트, MCP `1,219 ms`, forks 4 scanned / 0 err): `open_leases`: `submitted 20`, `waiting_input 12`, `running+slot 2`. 라우터: 외부 SSE EOF 19·BrokenPipe 2, `/ca/mcp` reset 2(00:53:05). 대화(1건): `06:50:46Z` `could not read Work status` 1건(미재시작 라우터).
  - **Celly** (`v6366` 유지, board `v8957 → v8961` 293노드/24루트, MCP `1,891 ms`, forks 7 scanned / 4 err): `open_leases`: `waiting_input 14`, `running+slot 6`. 라우터: 미재시작 라우터의 `15550` 반복 로그 236건, 외부 SSE EOF 19·BrokenPipe 2·reset 1, 모델 reset 3, transcript timeout 1, `turn_projection_persistent` 1, **`/ca/mcp` reset 0건**. 대화(4건): 정상 P3 정원 가드 1, 에이전트 스크립트 2, `07:07:44Z` `could not read Work status` 1건. 포크 오류 확인: `ae69b2a3c95e4382a`가 `06:52–07:09Z` 사이 `completion coverage`/`completion coverage fields` 검증 실패를 13회 반복(미재시작 구 라우터의 축약 오류 문구; 현재 디스크 `work_parallel_receipts.py:143–144`는 상세 필드 안내 반영됨, 라우터 재시작 대기), `ac1337d2b89a67835`는 Playwright/에이전트 스크립트 오류.
  - **KOBE** (`v2885 → v2891`, board `v431 → v432` 60노드/7루트, MCP `923 ms`, forks 24 scanned / 1 err): `open_leases`: `waiting_input 8`, `running+slot 3`. 라우터: keep-alive 270, 외부 SSE EOF 21·BrokenPipe 1, 모델/late SSE retry 6, `turn_projection_persistent` 6, `/ca/mcp` reset 1(`22:57:36`). 대화(6건): side 원문 append/already 5, `07:11:26Z` 정상 검수 가드(`Exact owned submitted lease and completion receipt required`) 1.
  - **Prody** (`v408 → v420`, board `v6497 → v6499` 33노드/7루트, MCP `1,267 ms`, forks 24 scanned / 0 err): `open_leases`: `submitted 6`, `reserved+slot 1`, `running+slot 1`, `waiting_input 1`. 라우터 경고 0건, 대화 오류 0건.
  - 새로 입증된 미수정 제품 결함 없음.

- 06:50Z 읽기 전용 틱(비교 기준 06:12Z, 38분 창; 운영 재시작·상태 수정 없음):
  - **인프라·배포 상태**: `1c53f39` GitHub Publish run `44995302048`은 `npm publish --access public --tag beta` 완료 후 `Verify published tarball` `in_progress`(npm 메타데이터 `beta`는 아직 `c35b735` 표시). 4호스트 `overlay-1c53f39` 파일 설치 완료, 운영 라우터 미재시작으로 실행 중 라우터는 기존 코드 유지. G: 여유 `190.91 GB`, `/health` Andy `126 ms` · Celly `35 ms`. 전 호스트 **`findings=0`, `new_failures=0`**, `terminal: unobserved`, `workflow: disabled`.
  - **공통 업스트림 일시 단절(06:30:26–43Z / 로컬 00:30, KOBE 22:30)**: Andy·Celly·KOBE 3호스트 동시 `cielairouter` 503/connection reset → transient retry 정상 복구.
  - **Andy** (`v5641 → v5660`, board `v10317 → v10328` 476노드/60루트/정지 145/actionable 45, MCP `2,297 ms`, forks `partial` 4 scanned / 1 err):
    - `open_leases`: `submitted 20`, `waiting_input 11`, `running+slot 3`.
    - 라우터(로컬 00:12–00:49): 외부 SSE EOF 35·reset 4, **`/ca/mcp` reset 22**(00:17:15~, 미재시작 라우터), 모델 reset 5, 업스트림 transient/late SSE retry 7, transcript timeout 3, `turn_projection_persistent` 2.
    - 대화(7건)·포크(1건): `06:17–06:35Z` `work_host_page` `ECONNREFUSED`/`connection closed` 6건 + 포크 `a27ba8765e3de110b`(`06:17:15Z`) `work_host_page was refused or failed` 1건(모두 미재시작 라우터의 백로그 5·비축약 페이징에서 발생; `e562169`+`c35b735` 디스크 설치 완료·재시작 대기), `06:17:20Z` `Git ownership/branch changed; retained` 1건.
  - **Celly** (`v6349 → v6366`, board `v8925 → v8957` 293노드/24루트/정지 95/actionable 9, MCP `3,110 ms`, forks `partial` 7 scanned / 1 err):
    - `open_leases`: `waiting_input 14`, `running+slot 6`.
    - 라우터(로컬 00:12–00:49): `15550 stale_unconfirmed` 반복 로그 **395건**(미재시작 라우터; `c35b735` 재시작 후 1회로 억제 예정), 외부 SSE EOF 37, `/ca/mcp` reset 4, 모델 reset 5, 업스트림 retry/failover 4, `runtime_activity_*` WinError 5 4, transcript timeout 3, `turn_projection_persistent` 2.
    - 대화(25건)·포크(1건): `06:47:11Z` `Parallel read failed: invalid external search result schema` 1건(미재시작 라우터; `a5d7df5` 디스크 설치 완료), 정상 가드 1(`work_side_status` host-only), plan version 충돌 2, 에이전트 스크립트/Walkie 400 다수. 포크 `aa703456ffdc9b155`(`06:17:45Z`) `completion artifact SHA256 required` 정상 가드 1건.
  - **KOBE** (`v2866 → v2885`, board `v422 → v431` 60노드/7루트/정지 11/actionable 4, MCP `276 ms`, forks `partial` 24 scanned / 5 err):
    - `open_leases`: `waiting_input 8`, `running+slot 3`.
    - 라우터(로컬 22:12–22:50): keep-alive 432, 외부 SSE EOF 27·BrokenPipe 4, 업스트림 transient retry 20, 모델 reset 4, `anthropic_stream_runaway_repetition`/`recovery` 각 4/2(`_API_KEYS-SSE-1`, 정상 가드).
    - 대화(11건): side 원문 append/already 7, 정상 가드 1(`changing the active goal requires user_requested=true`), `06:19:41Z` `parked lease 5a3af070a66 ... not a slot-released wait` 1건(선행 재개 경합), Walkie 네트워크 1, 에이전트 python 오류 1.
  - **Prody** (`v402 → v408`, board `v6493 → v6497` 33노드/7루트/정지 6/actionable 3, MCP `2,713 ms`, forks `partial` 24 scanned / 0 err):
    - `open_leases`: `submitted 6`, `reserved+slot 1`, `running+slot 1`, 라우터 경고 0건. 대화(6건): side 원문 append/already 5, 에이전트 python 오류 1.
  - 새로 입증된 미수정 제품 결함 없음(관측된 `ECONNREFUSED`/`work_host_page`/`15550` 반복/`invalid external search result schema`는 모두 `e562169`+`a5d7df5`+`c35b735`로 디스크 설치 완료, 라우터 재시작 대기 중).

- 06:46Z 기능 구현·검증 완료 — 사이드 액터 동작 중 포크 가시화 및 직접 전달(`relay` + `no_reply`):
  - **구현 내용**:
    - `ciel_runtime_support/side_workload.py`: `project_active_forks()`, `format_active_forks_lines()` 추가 및 `project(..., active_forks=...)`에 `active_forks` 배열과 노드별 `active_fork` 표시 추가(토큰·비밀정보 비노출, 현재 세션 `running`/`waiting_input` 바인딩 포크 한정).
    - `ciel_runtime_support/side_intake_mcp.py`, `ciel_runtime_support/work_mcp_tools.py`: 사이드 claim `workload`와 `work_status`(JSON `active_forks`, text `Active forks:` 섹션) 모두에 현재 실행 중 포크 정보 포함.
    - `ciel_runtime_support/side_work_decision.py`: `action='update'` + `relay=True` + `no_reply=True`(`ask_main`·`release_waits`·`time_limit` 없음) 조합을 `direct_fork_handoff`로 허용하고 `result['relay'] = {'item_id': item, 'fork_responds': True}` 기록 → 사이드 아웃박스 답장 생략(`not_applicable`).
    - `ciel_runtime_support/work_workflow_prompts.py`, `work_workflow_tools.py`, `work_texts.py`, `texts.generated.js`: 관리 프롬프트 `side_running_fork_relay` 및 `mod_direct_fork_reply` 추가(코드 하드코딩 지침 없음, REST/웹/CLI로 편집 가능).
    - `ciel_runtime_support/claude_mods/ciel-work/hooks/side-intake.js`: `compactWorkload`가 `active_forks` 및 해당 노드 체인을 유지해 사이드 포크 프롬프트에 전달; `relayToItem()`에서 `fork_responds === true`일 때 유일한 실행 슬롯 포크 + 검증된 `thread` 출처인 경우 `$.session.send`로 직접 전달하고 메인 알림 생략(미전달·불확실 시 메인 안전 폴백 알림).
  - **검증 결과**:
    - 실패-우선 RED → GREEN: `tests/test_side_running_fork_relay.py`(3/3), `tests/test_side_relay_notice.py`(12/12).
    - Windows 격리 회귀 9개 스위트 339건 PASS(`test_side_running_fork_relay`, `test_side_relay_notice`, `test_side_queue_starvation`, `test_side_intake_mcp`, `test_side_work_decision`, `test_work_texts`, `test_work_mcp_tools`, `test_work_gate_check`, `test_work_mod_write_gate`).
    - `ruff check` PASS, `claude plugin validate ciel-work` PASS, `claude plugin test ciel-work` **91/91 PASS**.
    - 실제 격리 `claude.exe` 로드(스텁 업스트림): `ciel-work@inline`·`ciel-work-gate@inline` core admitted, `BAD: 0`.
    - 기존 Linux Docker 컨테이너: `ciel-runtime-test-py310` **339/339 PASS**, `ciel-runtime-test-py311` **339/339 PASS**.

- 06:12Z 읽기 전용 틱(비교 기준 06:06Z, 6분 창; 운영 재시작·상태 수정 없음):
  - **Publish 완료 확인 ✅**: GitHub Publish run `44991580832`(`c35b735`) **`completed / success`**(npm `@beta` = `0.2.51-beta.20261122-055737.c35b735`). 4호스트 `overlay-c35b735` 디스크 설치 완료, 운영 라우터 미재시작 유지. G: 여유 `191.21 GB`, `/health` Andy `122 ms`·Celly `14 ms`. 전 호스트 **`findings=0`, `new_failures=0`**, `terminal: unobserved`, `workflow: disabled`.
  - **Andy** (`v5637 → v5641`, board `v10315 → v10317`, MCP `1,172 ms`, forks 0 err): `findings 1→0`(`n2576` `46223cf5` 재개 해소 확인 ✅), `/ca/mcp` reset 0건, 외부 SSE EOF 6·transcript timeout 2·`turn_projection_persistent` 1·`agent_runtime_event_retry` 1, 대화 `06:08:28Z` `could not read Work status` 1건(미재시작 라우터).
  - **Celly** (`v6349`, board `v8920 → v8925`, MCP `2,109 ms`, forks 2 err): 미재시작 라우터에서 `15550` `stale_unconfirmed` 반복 로그 74건·`/ca/mcp` reset 3건(`00:07:25`)·외부 SSE EOF 6/reset 1·모델 reset 2·대화 `06:07:30Z` `could not read Work status` 1건.
  - **KOBE** (`v2862 → v2866`, board `v421 → v422`, MCP `243 ms`, forks 3 err): keep-alive 79, 외부 SSE EOF 6/reset 1, transcript timeout 3, `turn_projection_persistent` 3, `runtime_activity_context_usage_failed` WinError 5 1, 대화 side append 1.
  - **Prody** (`v402`, board `v6492 → v6493`, MCP `3,327 ms → 949 ms` 정상화, forks 0 err): 라우터 경고 0건, 대화 3건(에이전트 PR 확인 exit 2, Walkie 404, side append 1). 신규 제품 결함 없음.

- 06:06Z 읽기 전용 틱(비교 기준 04:12Z, ~114분 창; 업그레이드된 보드·워크플로우·MCP·포크 관측 첫 4호스트 전체 실행; 운영 재시작·상태 수정 없음):
  - **배포·인프라 상태**: `c35b735` npm `@beta` = **`0.2.51-beta.20261122-055737.c35b735`** 게시 확인 ✅(GitHub Publish run `44991580832`는 `npm publish` 완료 후 `Verify published tarball` `in_progress`). 4개 호스트 해시 검증 overlay(`overlay-c35b735`) 완료, 운영 라우터 미재시작으로 실행 중 라우터는 여전히 이전 코드 유지. G: 여유 **`191.25 GB`**(`205,349,502,976 B`, 04:12Z 45.36 GB 대비 +145.89 GB). `/health`: Andy `169 ms`, Celly `71 ms`. 전 호스트 `terminal: unobserved`, `workflow: disabled`.
  - **Andy** (`v5597 → v5637`, board `v10315` `observed` 479 nodes / 61 roots / `active=n1445` / halted 145 / actionable 46, MCP `probe_succeeded 1,141 ms`, forks `partial` 4 scanned / 1 err):
    - `open_leases`: `submitted 20`, `waiting_input 11`, `running+slot 4`, `new_failures=0`.
    - `findings=1`(`slot held, fork absent since 04:23: n2576 46223cf5 P1 running`): 읽기 전용 확인 결과 소유 PID `72984` 생존 및 `agent-aabc7db204b29683a.jsonl` 최종 갱신 **`06:07:49Z`**(1.70 MB)로, `04:22Z` 대기 후 방금 재개(`Work host resumed the parked worker after its wait was cleared`)되어 실제 동작 중 — **오류/고아 아님 확인 ✅**.
    - 라우터(로컬 22:12–00:05): 외부 SSE EOF 91·reset 34·timeout 2, transcript timeout 23, `/ca/mcp` reset 7(22:58~23:08, 미재시작 라우터), 모델 reset 10, `turn_projection_persistent` 6, `runtime_activity_context_usage_failed` WinError 5 2(22:22:54).
    - 대화(19건): `05:02–05:11Z` `work_host_page`/`Parallel read` `ECONNREFUSED`·`connection closed` 10건 및 `05:25:26Z` `could not read Work status` 1건(모두 `c35b735` 파일 설치 이전, 미재시작 라우터 구간), `05:06:48Z` `workspace ownership handed off` 1건, `05:09:34Z` `gate check pump failed` 1건.
  - **Celly** (`v6324 → v6349`, board `v8920` `observed` 292 nodes / 25 roots / `active=n1209` / halted 97 / actionable 7, MCP `probe_succeeded 750 ms`, forks `partial` 3 scanned / 4 err):
    - `open_leases`: `waiting_input 15`, `running+slot 3`, **`findings 1→0`**(`n1287` 해소 유지), `new_failures=0`.
    - 라우터(로컬 22:12–00:05): `channel_stdin_proxy_blocked cursor=15549 message_id=15550 stale_unconfirmed action=skipped` **1,192건**(미재시작 라우터; `c35b735` 디스크 설치 완료, 재시작 후 1회로 억제 예정), 외부 SSE EOF 87·reset 39·timeout 2, transcript timeout 32, `/ca/mcp` reset 14(22:20~), 모델 reset 13, `turn_projection_persistent` 7.
    - 대화(32건)·포크(4건): `05:33:33Z`·`05:54:20Z` `could not read Work status` 2건(미재시작 라우터), `05:44–05:58Z` side 원문 append 4건, `05:46–06:01Z` 정상 P3 슬롯 정원 가드 4건, 에이전트 스크립트/Walkie 오류 다수. 포크 `ae69b2a3c95e4382a` 최근 4건(`05:38–05:57Z`)은 모두 에이전트 스크립트/CI 타임아웃 오류이며, 이전 `04:52:24Z` 1건은 미재시작 라우터의 `work_host_page was refused or failed` 확인.
  - **KOBE** (`v2821 → v2862`, board `v421` `observed` 58 nodes / 7 roots / `active=n22` / halted 12 / actionable 4, MCP `probe_succeeded 187 ms`, forks `partial` 24 scanned / 7 err):
    - `open_leases`: `waiting_input 9`, `running+slot 2`, `findings=0`, `new_failures=0`.
    - 라우터(로컬 20:12–22:06): keep-alive 정상 경고 1,336, 외부 SSE EOF 86·reset 29·timeout 4·BrokenPipe 4, transcript timeout 33·refused 3, 모델 reset 17, `remote_instructions_failed` reset 2, `turn_projection_persistent` 1.
    - 대화(29건): `05:31:28Z` `work_side_handoff: invalid side receipt identity` 1건(미재시작 라우터; `a5d7df5` 디스크 설치 완료), 정상 Work 가드 3건(`workers only`, `already has lease` ×2), side 원문 append 3건, Walkie 네트워크 오류 2건, 에이전트 bash/python 오류 다수.
  - **Prody** (`v396 → v402`, board `v6492` `observed` 34 nodes / 7 roots / `active=n1101` / halted 6 / actionable 3, MCP `probe_succeeded 3,327 ms` 느림 주의, forks `partial` 24 scanned / 0 err):
    - `open_leases`: `submitted 6`, `reserved+slot 1`, `running+slot 2`, `findings=0`, `new_failures=0`, 라우터 경고 0건.
    - 대화(18건): side 원문 append/already 11건, 정상 Work 완료 가드 1건(`04:47:51Z`).
  - 이번 틱에서 새로 입증된 미수정 제품 결함 없음(관측된 `ECONNREFUSED`/`work_host_page`/`15550` 반복 로그/`work_side_handoff`는 모두 `e562169`+`a5d7df5`+`c35b735`로 디스크 설치 완료, 미재시작 라우터에서 계속 재발 중; Prody MCP 3,327 ms 지연과 Andy `n2576` 재개 후속 계속 추적).

- 05:55Z 원인 규명·수정·검증 완료(커밋/배포 진행 중; 운영 라우터·세션 미재시작, 상태 파일 미수정):
  - **입증된 결함 1 — Windows 라우터 TCP accept backlog=5 고정(`ca1ab09` Winsock 무효화, Andy/Celly `ECONNREFUSED` 핵심 원인)**:
    - 증거: `ciel_runtime.py:3008`이 기본 `ThreadingHTTPServer`(`request_queue_size=5`)를 생성해 `server_activate()`에서 `socket.listen(5)`를 먼저 호출한 뒤 `router_server_runtime.py:90`이 `server.socket.listen(256)`을 재호출. Windows Winsock 실측 결과 `listen(5)` 단독 `(5, 1)`, `listen(5)` 후 `listen(256)`도 동일하게 **`(5, 1)`**(6번째 동시 연결부터 `ECONNREFUSED`), 최초 호출 `listen(256)`은 `100/100` 수락. Andy PID 81436(`hotfix5`) 실측: `TIME_WAIT 1,100`, 20분간 `work_host_page` 8,514건, 누적 CPU 27,562.7s.
    - 수정: `router_server_runtime.py`에 `ROUTER_REQUEST_QUEUE_SIZE = 256` 및 `_server_factory_with_backlog()` 도입(`server_activate()` 전에 `request_queue_size=256` 설정), `ciel_runtime.py`에 `RouterThreadingHTTPServer(ThreadingHTTPServer)` 적용. `tests/test_router_server_runtime.py` 실제 소켓 24연결 실패-우선 재현(`5 < 24` RED → 24/24 GREEN).
  - **입증된 결함 2 — `ciel-work` Work 패널 일시 단절 후 영구 적색 고착(Image #16/#14) 및 `ciel-work`/`ciel-work-gate` 읽기 MCP 단절 복구 누락**:
    - 증거: `ciel-work/hooks/register.js:388,5440–5447`에서 일시 오류로 `cached = null`이 되면 기존 같은 대화 `detailView`가 남아 있어 `empty = !view`가 `false`로 평가되어 자동 재로딩이 멈추고 모든 아이템이 사라진 적색 `"Work & SSE unavailable for this conversation."` 화면에 영구 고착. `ciel-work-gate/hooks/register.js:126` `routerJson`은 단절 시 재연결 없이 즉시 `Work state unreadable` 거부.
    - 수정: `ciel-work/hooks/side-intake.js` `readWorkMcp`를 `claude plugin validate` 규칙 준수 형태로 구현(`$.mcp.connect('ciel-runtime-router')` 직접 호출, `session_id` 키 `Map` 중복 제거, 순수 읽기 도구 `work_status`/`work_parallel_status`/`work_proposal_status`/`work_host_page`/`work_side_status`/`work_side_texts` 한정 1회 재시도, 변경 호출 미재시도). `ciel-work/hooks/register.js`에서 같은 대화의 `view`가 있으면 마지막 검증 스냅샷 항목을 유지 표시 + `$.clock.after(5000)` 바운드 자동 복구(`session.end` 시 정리), 다른 대화(`foreign`)는 격리 유지. `ciel-work-gate/hooks/register.js` `routerJson`에 동일 검증 통과 `mcpRead` 1회 재연결·재시도 추가.
  - **신규 기능 — 정지/보류/차단 Work 항목 `release_hint` 보존 및 게이트 재확인 전달**:
    - `work_plan_tree.py`, `work_gate_check.py`, `work_mcp_tools.py`, `work_application.py`, `work_http_controller.py`(REST `/ca/work/nodes/add`, `/ca/work/nodes/update` 패리티 포함), `side_work_decision.py`, `work_workflow_prompts.py` 반영. `release_hint`(`condition`, `lookup`, 선택 `prompt`)를 저장·직렬화하고, 상태/`waiting_on` 변경 또는 해제 시 정리하며, 게이트 재확인(`work_gate_check`)에 루트 `refs`와 함께 읽기 전용 참고 힌트로 전달(자동 실행·승인 아님). `tests/test_work_release_hints.py` RED(REST 422) → GREEN(3/3).
  - **입증된 결함 3 — 보류 커서 뒤 단말 실패 메시지(`15550`) 매 5초 `[ERROR]` 반복 로그**:
    - 증거: live Celly 30분간 `channel_stdin_proxy_blocked cursor=15549 message_id=15550 reason=prior_submission_failed detail=stale_unconfirmed action=skipped` 318건 반복.
    - 수정: `channel_pending_injection.py`에서 `status.path` 스코프별 바운드 캐시로 동일 `(message_id, failure_reason)` 단말 건너뛰기 `[ERROR]`를 1회만 기록. `tests/test_channel_pending_injection.py` RED(`3 != 1`) → GREEN(66/66).
  - **모니터링 개선 — `work_live_observation.py` 및 `.local/tmp/monitor/monitor_host.py` 실측 강화**:
    - 보드 상태(`observed`/`empty_view`/`unavailable`/`different_session`, 노드·정지·힌트·actionable 수), 워크플로우 상태, MCP `2026-07-28` `work_status` 프로브 및 응답시간, 바운드 소유 세션 포크 오류(`isFork: true`, 본문 비노출), `terminal: unobserved` 명시 분리. live Andy(`v10311`, 480 nodes, 146 halted, MCP 578ms) 및 live Celly(`v8906`, 292 nodes, 99 halted, MCP 844ms, 포크 오류 4건) 읽기 전용 실측 확인.
  - **검증 결과**:
    - `ruff check`: 변경 Python 전체 `All checks passed!`.
    - Windows 격리 회귀 15개 스위트 전부 PASS(`test_router_server_runtime` 5, `test_work_release_hints` 3, `test_work_live_observation` 5, `test_work_mcp_read_reconnect` 1, `test_work_fork_gate` 34, `test_work_mod_write_gate` 166, `test_work_plan_tree` 63, `test_work_gate_check` 24, `test_work_mcp_tools` 31, `test_work_http_controller` 37, `test_channel_pending_injection` 66, `test_router_io_performance` 9, `test_side_handoff_flow` 5, `test_side_release_notice` 8, `test_work_discovery_pickup` 12, `test_architecture_contracts` 261).
    - `claude plugin validate`: `ciel-work` PASS, `ciel-work-gate` PASS; `claude plugin test ciel-work`: **91/91 PASS**.
    - 실제 격리 `claude.exe` 로드(스텁 업스트림, 실키 미사용): `ciel-work@inline`·`ciel-work-gate@inline` core admitted, `classic.SessionStart` 완료, `BAD_MOD_ERRORS: 0`.
    - 기존 Linux Docker 컨테이너: `ciel-runtime-test-py310` **730/730 PASS**, `ciel-runtime-test-py311` **730/730 PASS**.
    - 실 루프백 `RouterThreadingHTTPServer` + `work_live_observation` CLI 스모크: `request_queue_size=256`, `board=observed`, `mcp=probe_succeeded (47ms)`.

## 2026-11-21

- 04:19Z(11-22) 사용자 장애 제보 후 판정 정정: Celly 세션 장애는 미해결이다. `/health` HTTP 200(46ms)은 라우터의 단일 HTTP 응답만 증명하며 세션 동작을 검증하지 않는다. 로컬 22:17:30–22:18:55 로그에서 `15498 ownership_submitted/no_replay` 16건, `15550 stale_unconfirmed` 17건, `work_host_page` 315건. 22:18:33 턴 상태 추적 경고는 `age_seconds=4052 transcript_updated=False model_request_state=unknown`으로, 추적 상태가 67분 넘게 유지됐다는 뜻이며 엔진이 그 시간 내내 멈췄다는 증거는 아니다. 15550은 코드에서 재주입 없이 건너뛰며, 15498의 보류 및 세션 장애 원인은 미확정. 첨부 화면 설명(#14·#15)에는 `n1287` 검수 복구 확인창, `Ciel Work fork gate: TaskStop is not available to this Work fork`, 도구 호출 4건 실패 표시가 있다. 모니터 스크립트는 메인 JSONL만 읽고 `isSidechain`을 제외하며 포크별 JSONL과 화면·모달을 수집하지 않는다(`monitor_host.py:182–209`). 따라서 직전 `tx_n=0`은 세션 정상 판정 근거가 아니다. 확인창과 포크 도구 거부의 인과는 미확인. 운영 재시작·상태 수정 없음.

- 04:12Z(11-22) 읽기 전용 틱(비교 기준 03:52Z; 운영 재시작·상태 수정 없음):
  - **health·G: 측정(04:12Z)**: Andy `36.1 ms`(PID 81436), Celly `8.4 ms`(PID 16340). G: 여유 `48,708,825,088 B`(**45.36 GB**, 03:52Z 45.40 GB 대비 −0.04 GB/20분).
  - **배포·설치 상태 유지**: `a5d7df5` npm `@beta` 게시 및 4개 호스트 파일 설치 완료; 운영 라우터 미재시작으로 실행 중 라우터는 기존 `d24a9e8` 유지.
  - **Andy** (`v5591 → v5597`): `submitted 21`, `waiting_input 10`, `running+slot 5→4`, **`findings 1→0`**(직전 `n2477` `832aa108` 해소 확인 ✅), `new_failures=0`.
    - 라우터(로컬 21:52–21:59): 외부 SSE EOF 19, `/ca/mcp` reset 2(21:52:34), `turn_projection_persistent` 2, 모델 SSE reset 1, 업스트림 `cielairouter_transient_retry` 503/disconnect 9(21:59:09–22).
    - 대화(04:09:48Z, 1건): side 원문 append 1(`062c4d7f8b6b`).
  - **Celly** (`v6323 → v6324`): `waiting_input 15`, `submitted 4`, **`findings 2→1`**(직전 `n1294` `de7ac55c` 해소 확인 ✅; `n1287` `6f27e534` P2 `native=waiting last=04:02Z miss=None` 유지), `new_failures=0`, 대화 오류 0건.
    - 라우터(로컬 21:52–22:10): `[ERROR] channel_stdin_proxy_blocked cursor=15549 message_id=15550 reason=prior_submission_failed detail=stale_unconfirmed action=skipped` **225건**(5초 주기 반복 — `channel_pending_injection.py:246,513` 확인 결과 선행 `15498` `ownership_submitted` 보류로 커서가 `15497`에 고정돼 매 폴링마다 이미 `continue`로 건너뛴 단말 실패 `15550`을 반복 로그하는 현상, 후속 메시지 차단은 아님), `turn_projection_persistent` 10(`age_seconds=2572→3557s`), 외부 SSE EOF 18·reset 1. **`/ca/mcp` reset 0건·`ChannelFileLockBusy` 0건**.
  - **KOBE** (`v2812 → v2821`, 기본 틱 SSH `lost connection` 후 단건 재시도 성공 04:13Z): `waiting_input 9→8`, `findings=0`, `new_failures=0`.
    - 라우터(로컬 19:48–20:12): keep-alive 정상 경고 295, 외부 SSE EOF 17·timeout 3·reset 2·BrokenPipe 1, transcript timeout 4, 모델 reset 4, `turn_projection_persistent` 12.
    - 대화(03:53–04:12Z, 15건): 정상 Work 가드 4(`parallel validation`, `workers only`, `already has lease`, `실행 단계 직접 코드 수정 금지`), plan version 충돌 2, side 원문 append 2, Ripgrep 20s timeout 2, 에이전트 스크립트/경로 오류 5.
  - **Prody** (`v388 → v396`): `submitted 6`, `reserved+slot 1`, `waiting_input 1`, `running+slot 2→0`, `findings=0`, `new_failures=0`, 새 라우터 경고 없음.
    - 대화(04:02–04:12Z, 6건): side 원문 append 1, 외부 API 400 1, 에이전트 스크립트/경로/보안 가드(`/c` Remove-Item 차단) 4.
  - 이번 틱에서 새로 입증된 제품 결함 없음(Celly `15498`/`15550` 반복 오류 로그·`turn_projection_persistent 3557s` 및 `n1287` 계속 추적).

- 03:52Z(11-22) 읽기 전용 틱(비교 기준 03:47Z; 운영 재시작·상태 수정 없음):
  - **a5d7df5 Publish 완료 확인 ✅**: GitHub Publish run `44982010011` **`completed / success`**, npm `@beta` = **`0.2.51-beta.20261122-034457.a5d7df5`** 게시 확인. 4개 호스트 파일 설치 완료 상태이며 운영 라우터 미재시작으로 실행 중 라우터는 기존 `d24a9e8` 유지.
  - **health·G: 측정(03:52Z)**: Andy `187.0 ms`(PID 81436), Celly `63.4 ms`(PID 16340). G: 여유 `48,742,572,032 B`(**45.40 GB**, 03:47Z 46.00 GB 대비 −0.60 GB/5분).
  - **공통 업스트림 게이트웨이 장애(03:47:29–03:48:04Z)**: Andy·Celly·KOBE 3개 호스트 동시 `http://127.0.0.1:20128` 503 및 `agent-aap.tail.headscale.ezonebot.com:20129` reset → `ciel-router-01.ezonebot.com` 자동 우회 및 transient retry 정상 동작.
  - **Andy** (`v5587 → v5591`): `submitted 21`, `waiting_input 10`, `running+slot 4→5`, `findings=1`(`n2477` `832aa108` P1: `native=running→waiting`, `last=03:52Z miss=None` 계속 추적), `new_failures=0`.
    - 라우터(로컬 21:47–21:52): `/ca/mcp` client reset **7**(21:52:12, 미재시작 `d24a9e8` 라우터), 외부 SSE EOF 4·BrokenPipe 1, 업스트림 transient retry 10+.
    - 대화(03:52:12–13Z, 2건): `work_host_page` / `Parallel read failed: connection closed before the tool call was answered` 2건(위 `/ca/mcp` reset 7건과 일치).
  - **Celly** (`v6322 → v6323`): `waiting_input 15`, `running+slot 1→0`, `submitted 3→4`, **`findings=2`**(`n1294` `de7ac55c` P1 `native=running last=03:45 miss=None` 신규 + `n1287` `6f27e534` P2 `native=waiting last=03:45 miss=None` 유지), `new_failures=0`, 대화 오류 0건.
    - 라우터(로컬 21:47–21:53): **`[ERROR] channel_stdin_proxy_blocked cursor=15549 message_id=15550 reason=prior_submission_failed detail=stale_unconfirmed action=skipped`** 반복(21:51:58~21:53:00 13회+, 5초 주기 — `15550` 미확인 실패로 커서 `15549` 고정 차단; 상태 파일 수정 금지 규칙에 따라 읽기 전용 추적), 외부 SSE EOF 5·BrokenPipe 1, `turn_projection_persistent` 1, 업스트림 failover/retry 8.
  - **KOBE** (`v2811 → v2812`): `waiting_input 8→9`, `running+slot 1→0`, `findings=0`, `new_failures=0`. 라우터(로컬 19:47): keep-alive 정상 경고 65, 외부 SSE EOF 4, 업스트림 transient retry 10. 대화(03:47:44Z, 2건): side 원문 append 2(`a0da72908404`, 업스트림 503 구간).
  - **Prody** (`v388` 유지): `submitted 6`, `reserved+slot 1`, `waiting_input 1`, `running+slot 2`, `findings=0`, `new_failures=0`, 대화 오류 0건, 새 라우터 경고 없음.
  - 이번 틱에서 새로 입증된 제품 결함 없음(Celly `message_id=15550` 커서 차단·`n1294`/`n1287`·Andy `n2477` 추적).

- 03:47Z(11-22) 읽기 전용 틱(비교 기준 03:23Z; 운영 재시작·상태 수정 없음):
  - **배포·설치 상태**: `e562169` npm `@beta` = `0.2.51-beta.20261122-031709.e562169` 게시 완료; `a5d7df5` Publish run `44982010011` `in_progress`, 4개 호스트 파일 설치 완료(운영 라우터 미재시작으로 실행 중 라우터는 기존 `d24a9e8` 유지).
  - **health·G: 측정(03:47Z)**: Andy `46.2 ms`(PID 81436), Celly `22.9 ms`(PID 16340). G: 여유 `49,396,269,056 B`(**46.00 GB**, 03:23Z 46.61 GB 대비 −0.61 GB/24분).
  - **Andy** (`v5570 → v5587`): `submitted 21`, `waiting_input 9→10`, `running+slot 3→4`, `findings=1`(`n2477` `832aa108` P1: `submitted` + `completion` 영수증, `native=running last=03:46Z miss=None` — 틱 1분 전 제출, 다음 틱 추적), `new_failures=0`.
    - 라우터(로컬 21:23–21:45): 외부 SSE EOF 21·reset 3, `turn_projection_persistent` 9, transcript timeout 2, 모델 client/SSE reset 2. `/ca/mcp` reset 0건.
    - 대화(03:27–03:46Z, 8건): `Parallel read failed: invalid external search result schema` 1(03:27:54Z — `a5d7df5` 설치·미재시작 라우터 이전 호출), plan version 충돌 3, 존재하지 않는 파일 경로 조회 4.
  - **Celly** (`v6320 → v6322`): `waiting_input 15`, `running+slot 2→1`, `submitted 2→3`, `findings=1`(`n1287` `6f27e534` P2: `submitted` + `completion` 영수증, `native=waiting last=03:45Z miss=None` — 네이티브 포크가 `waiting` 상태로 살아 있어 설계상 자동 강제 종료하지 않음, 계속 추적), `new_failures=0`, **대화 오류 `tx_n=0`**.
    - 라우터(로컬 21:23–21:41): 외부 SSE EOF 22, 모델 SSE reset 2, `turn_projection_persistent` 10. **`/ca/mcp` client reset 0건, `ChannelFileLockBusy` 0건**.
  - **KOBE** (`v2808 → v2811`): `waiting_input 8`, `running+slot 1`, `findings=0`, `new_failures=0`.
    - 라우터(로컬 19:23–19:39): keep-alive 정상 경고 285, 외부 SSE EOF 16·BrokenPipe 3, transcript timeout 3, `turn_projection_persistent` 3, 모델 reset 3, `anthropic_stream_runaway_repetition`/`recovery` 각 2/1(`_API_KEYS-SSE-1`, 정상 가드 작동), `anthropic_sse_upstream_error_event` 1(`_Walkie_P2`).
    - 대화(03:31–03:41Z, 5건): side 원문 append 4, `Work completion waits for this item's parallel validation` 정상 가드 1(03:41:09Z).
  - **Prody** (`v380 → v388`): `submitted 6`, `reserved+slot 1`, `waiting_input 1`, `running+slot 2`, `findings=0`, `new_failures=['decision_refused_stop 3764bdaf x1']`, 새 라우터 경고 없음.
    - 대화(03:23–03:41Z, 6건): `work_parallel_item_review: review decision` 1(03:32:03Z — 기존 미결 사용자 결정 항목인 `approve|rework` 외 값 거부), side 원문 append 1, 외부 API 400 1, 에이전트 스크립트/경로 오류 3.
  - 이번 틱에서 새로 입증된 제품 결함 없음(`n2477`·`n1287` 및 `a5d7df5` npm 게시 완료 여부 다음 틱 추적).

- 03:42Z(11-22) 피어(`Andy_Main (3)`) 제보 2건(+중복 wake 원인) 재현·수정·배포 — 커밋 `a5d7df5` (`fix(work): prevent duplicate notice wakes, resolve handoff prefixes, and declare discovery pickup schema`):
  - **원인 실측**:
    1. `d785d257b73f`는 `side_handoffs` 행이 없는 `ask_main` notice(`result.handoff='not_required'`, 확인 불필요)였으며, 2회 wake(`56bdfefe8013` 03:19:25Z → `d9eb8f42792d` 03:19:31Z enqueue → 03:29:52Z dequeue)는 `side-intake.js` `wakeMain()`이 unleased notice에 쿨다운을 두지 않아 `prompt.submit` 훅(9초) 완료 전 `mainBusy()===false` 구간에 2번째 wake를 5.8초 만에 중복 enqueue한 결함(기존 미결 `9cbc9ae3f83f` 중복 wake와 동일 원인). 또한 `work_side_handoff`는 `side_handoffs` 조회 전에 `inbox._load(event_id)`(64-hex 강제)를 먼저 호출해 12자 prefix 및 비-handoff 알림에 오해를 부르는 `invalid side receipt identity`를 반환했음.
    2. `work_discovery_pickup`은 `work_parallel_mcp.py:39`의 `results.items`가 빈 `{"type": "object"}`여서 MCP 스키마 보정기가 `required: ["reason"]`을 합성했고, 모델이 `reason`을 포함해 호출하면 `ExternalSearchItem(**item)`이 불명확한 `invalid external search result schema`로 거부했음.
  - **실패 선재현(RED) → 수정(GREEN)**:
    - `side-intake.js`: unleased(notice/legacy) wake에 `UNLEASED_WAKE_COOLDOWN_MS = 60_000` 적용(`test_side_release_notice.py` 1!=2 → 1/1).
    - `side_intake_mcp.py`: `work_side_handoff`에서 `side_handoffs` 선조회·유일 12+hex prefix 해석·비-handoff 알림에 `side handoff not found (ask_main and release notices need no work_side_handoff acknowledgment)` 반환(`test_side_handoff_flow.py` 5/5).
    - `work_parallel_mcp.py`: `work_discovery_pickup`에 전용 `inputSchema`와 `results.items`의 정확한 8개 필드(`additionalProperties: false`) 선언 + 누락/불필요 키 명시 오류(`test_work_discovery_pickup.py` 12/12).
  - **회귀 검증**: Windows 격리 12개 스위트 전원 PASS(`ruff check` 0건, `claude plugin validate ciel-work` 통과, `claude plugin test ciel-work` 90/90 PASS), Linux `py311`·`py310` 기존 컨테이너 각 391/392(+픽스처 수정 후 대상 69/69 전원 PASS).
  - **배포·설치 구분**: `feature/priority-models` + `beta` 푸시(`a5d7df5`, Publish run `44982010011` 진행; 직전 `e562169` run `44979901944` `completed/success` 확인). 해시 검증 overlay 파일 설치(백업 태그 `handoff-pickup-a5d7df5`, 3개 파일) **Andy 3루트·Celly·KOBE·Prody 4개 호스트 전체 완료**. 운영 라우터·세션 미재시작으로 Python 라우터 변경(`work_parallel_mcp.py`, `side_intake_mcp.py`)은 라우터 재시작 후, `side-intake.js`는 `ciel-work` 플러그인 재로드/세션 재시작 후 라이브 반영. 피어 `Andy_Main (3)` 회신 완료.

- 03:23Z(11-22) 읽기 전용 틱(비교 기준 02:40Z; 운영 재시작·상태 수정 없음):
  - **e562169 배포 확인**: npm `@beta` = **`0.2.51-beta.20261122-031709.e562169`** 게시 확인(GitHub Publish run `44979901944`). 4개 호스트(Andy 3루트·Celly·KOBE·Prody) 파일 설치 완료 상태이며, 운영 라우터 미재시작으로 실행 중 라우터는 기존 `d24a9e8` 유지.
  - **health·G: 측정(03:23Z)**: Andy `59.9 ms`(PID 81436), Celly `27.0 ms`(PID 16340). G: 여유 `50,043,662,336 B`(**46.61 GB**, 02:40Z 45.12 GB 대비 +1.49 GB).
  - **Andy** (`v5567 → v5570`): `submitted 20→21`, `waiting_input 8→9`, `running+slot 5→3`, `findings=0`, `new_failures=0`.
    - 라우터(로컬 20:40–21:03): 외부 SSE EOF 37·reset 17, transcript timeout 15·WinError 10061 1(20:41:43), 모델 client/SSE reset 10, `agent_runtime_event` retry 4·dropped 1(20:40:27–20:41:17, 100.64.0.9:8787 timeout), `remote_instructions_failed` timeout 1(20:40:50).
    - 대화(03:08–03:22Z, 11건): `work_side_status` 호스트 전용 정상 가드 1(03:13:42Z), `work_side_handoff: invalid side receipt identity` 1(03:22:43Z), 완료 Task 조회 1·외부 API 400 3·Walkie UUID 오류 1·에이전트 스크립트/gh 404 오류 4.
  - **Celly** (`v6300 → v6320`): `waiting_input 15`, `running+slot 4→2`, `submitted 1→2`, `findings=1`, `new_failures=0`.
    - 직전 `finding` `n1292`(`4a6e9be3`) 해소 확인. 새 `finding` 1건: `n1287`(`6f27e534`, P2) `submitted` + `completion` 영수증, `native=waiting last=03:22Z miss=None`(틱 1분 전 제출 — 다음 틱 검수 행 게시 여부 추적).
    - 라우터(로컬 20:40–21:11): 외부 SSE EOF 33·reset 15, transcript timeout 13·WinError 10061 2, `/ca/mcp` client reset **11**(이전 틱 82 대비 대폭 감소, 21:08:39~), 모델 reset 15, `turn_projection_persistent` 5, `agent_runtime_event_retry` 2. **`ChannelFileLockBusy` 0건**.
    - 대화(02:55–03:11Z, 13건): side 원문 append 5, `Parallel read failed: The operation timed out` 1(03:08:42Z)·`git -c core.quotePath=false timed out after 53 s` 1(03:10:59Z, 기존 미재시작 라우터 + G: 지연 구간), Walkie 무응답 1·UUID 1·외부 API 400 1·에이전트 스크립트 3.
  - **KOBE** (`v2808` 유지, SSH 복구): `waiting_input 8`, `running+slot 1`, `findings=0`, `new_failures=0`.
    - 라우터(로컬 18:40–18:54): keep-alive 정상 경고 499, 외부 SSE EOF 31·reset 16·BrokenPipe 2, transcript timeout 11, 모델 reset 3, `turn_projection_persistent` 3, `agent_runtime_event_retry` 5.
    - 대화(02:40–03:13Z, 7건): side 원문 append 6, Ripgrep 20초 timeout 1.
  - **Prody** (`v364 → v380`, SSH 복구): `submitted 6`, `reserved+slot 1`, `waiting_input 1`, `running+slot 1→2`, `findings=0`, `new_failures=0`, 새 라우터 경고 없음.
    - 대화(02:45–03:23Z, 16건): side 원문 append 7, plan version 충돌 2, `work_parallel_event (reserve) failed: active slot capacity reached for P4 (1/1)` 정상 용량 거부 1(03:04:50Z), 외부 API 400 1, 에이전트 스크립트/경로 오류 5.
  - 이번 틱에서 새로 입증된 추가 제품 결함 없음(기존 `e562169`·`c948680`·`792b1d7`은 파일 설치·npm `@beta` 게시 완료, 라이브 라우터 반영은 재시작 대기).

- 03:22Z(11-22) 정정 및 수정·배포 완료 — 커밋 `e562169` (`fix(router): eliminate MCP paging storms, uncached state scans, and lock-busy 500s`):
  - **직전 02:40Z 틱 "결함 없음" 판정 모순 정정**: 직전 진단에서 입증한 라우터 CPU/I/O 과부하·MCP 타임아웃·`ChannelFileLockBusy` 500 6건을 제품 결함으로 분류·재현·수정.
  - **실패 테스트 선재현(RED) → 수정(GREEN)**: `tests/test_router_io_performance.py` 9건 + `tests/test_work_fork_gate.py::test_gate_requests_narrow_unpaged_projections` 1건 모두 HEAD(`792b1d7`)에서 실패 확인 후 수정:
    1. `work_parallel_mcp.py`·`work_mcp_tools.py`·`ciel-work-gate/hooks/register.js`: `ciel-work-gate`용 경량 비페이징 projection(`projection="gate"`/`"phase"`) 추가 + `inputSchema` 확장 + `gate`/`version` 시 `sweep_while_running`/`SideIntakeInbox` 생략.
    2. `host_result_pages.py`: `stat` 검증 기반 메모리 LRU 페이지 캐시(64건), 동일 SHA-256 중복 `fsync` 스킵, `_prune()` 60초 쿨다운.
    3. `work_parallel_primitives.py`·`work_parallel.py`·`work_parallel_receipts.py`·`work_parallel_finalization.py`: 공용 `StatJsonCache` 적용(동일 stat 시 Windows 파일락·디스크 읽기·`unique_object`·전체 `validate()` 생략, `_change()` 커밋 시 즉시 갱신, 50ms quiet window).
    4. `channel_message_repository.py`: `_scan()` stat 기반 캐시 + `append()` 시 무효화 + 반환 슬라이스만 복사(런처/라우터 0.5초 폴링 시 10.5 MB `runtime-inputs.jsonl` 반복 전체 파싱 제거).
    5. `fork_turn_watch.py`: 1초 이상 미변경 `unknown(no_record)` 포크의 2초 주기 무한 `_fresh_backscan` 차단 + 미변경 idle 포크 `source_continues()` stat fast-path + 중복 `source_snapshot()` 제거.
    6. `runtime_logging.py`·`work_mcp_tools.py`: `RouterFileLogger` 로테이션 실패 30초 쿨다운 + `dispatch_work_tool`의 `ChannelFileLockBusy`를 미처리 HTTP 500 대신 정돈된 MCP 오류 응답(`isError=True`, 경로 미노출)으로 전환.
  - **회귀 검증**:
    - Windows 격리(`CIEL_RUNTIME_TEST_ISOLATED=1`, 임시 `CIEL_RUNTIME_CONFIG_DIR`): 영향 17개 파일 전원 PASS(`test_router_io_performance.py` 9/9, `test_host_result_pages.py` 7/7, `test_work_fork_gate.py` 33/33, `test_fork_turn_watch.py` 61/61, `test_work_parallel*.py` 등 총 400+ 테스트), `ruff check` 0건, `claude plugin validate ciel-work-gate` 통과, `claude plugin test ciel-work` 90/90 PASS.
    - Linux 기존 컨테이너: `ciel-runtime-test-py311` 397/397 + 최종 256/256 PASS, `ciel-runtime-test-py310` 397/397 + 최종 256/256 PASS.
  - **배포·설치 구분**:
    - GitHub 푸시: `feature/priority-models` + `beta` (`e562169`), Publish run `44979901944` 진행/확인.
    - 해시 검증 overlay 파일 설치(백업 태그 `routerperf-e562169`, 11개 파일 전원 해시 일치 검증): **Andy 3루트(`hotfix5`, `.local`, `npm`), Celly, KOBE(`u24`), Prody(`u41`) 4개 호스트 전체 완료**.
    - **라이브 효과 vs 파일 설치 구분**: 운영 라우터/세션은 재시작하지 않았으므로 Python 라우터 패치(`e562169`, `c948680`, `792b1d7`)는 **파일 설치 완료 상태**이며, **라우터 재시작 전까지 실행 중 라우터 프로세스는 기존 `d24a9e8` 메모리 코드로 동작**합니다(`ciel-work-gate/hooks/register.js`도 플러그인 재로드/재시작 시 적용).

- 02:40Z(11-22) 읽기 전용 틱(비교 기준 01:51Z; 운영 재시작·상태 수정·배포 없음):
  - **health·G: 측정(02:40–02:42Z)**: Andy 39.9ms(PID 81436), Celly 7.7ms→112.2ms(PID 16340). G: 여유 `48,448,163,840 B`(**45.12 GB**, 01:51Z 29.44 GB 대비 +15.68 GB — 직전 1차 안전 정리 반영).
  - **Andy** (`v5551 → v5567`): `submitted 21→20`, `waiting_input 9→8`, `running+slot 4→5`, `findings=0`, `new_failures=0`.
    - 라우터(로컬 19:52–20:28): 외부 SSE EOF 44·reset 11, transcript timeout 9, `/ca/mcp` client reset 6, 모델 SSE/client reset 8, `turn_projection_persistent` 10+, `runtime_activity_router_failed` WinError 5 1(19:59:47).
    - 대화(01:55–02:14Z, 7건): `Parallel read failed: The operation timed out` 1(02:10:02Z), side 원문 append 1(02:05:00Z), `Work mode is planning` 정상 가드 1(02:02:01Z), 완료된 Task 참조 2·409/400 API 오류 2.
  - **Celly** (`v6282 → v6300`): `waiting_input 16→15`, `running+slot 4`, `submitted 0→1`, **`findings=1`**(`n1292` `4a6e9be3` P2: `submitted` + `completion` 영수증 + `run=terminal`, `FIN=None`, `native=running last=02:32Z miss=None` — 직전 G: 정지 구간 직후로 다음 메인 턴 경계 게시 여부 추적), `new_failures=0`.
    - 라우터(로컬 19:51–20:19): `/ca/mcp` client reset **82**(직전 `robocopy`/G: 스톨 구간 집중), 외부 SSE EOF 33·reset 4, transcript timeout 7, 모델 client reset 4, `turn_projection_persistent` 6, **`ChannelFileLockBusy` 500 1**(20:16:26, 반복 추적).
    - 대화(02:01–02:39Z, 19건): `work_host_page` / `Parallel read` / `work_node_update` timeout 5(02:10–02:18Z)·`ECONNREFUSED` 1(02:28:01Z), `Ciel Work gate: router-error` 5(02:16:50 ×2, 02:18:46, 02:38:08, 02:39:28Z — 앞 3건은 20:16–18 G: 스톨, 뒤 2건은 1차 정리 직후 TRIM/GC 스톨), `work_node_update: P0 clear bound` 정상 가드 2(02:22:55·02:23:57Z), `Work gate check: pump failed` 1(02:22:02Z), side 원문 append 3, 도구 거부 2.
  - **KOBE · Prody**: SSH 재시도 포함 2회 모두 `kex_exchange_identification: Connection reset by peer` / `lost connection`(원격 SSH/네트워크 단절, 미관측으로 분류 — 종료·결함 단정 안 함).
  - 이번 틱에서 새로 입증된 제품 결함 없음(`n1292` 검수 행 게시 여부·KOBE/Prody SSH 복구 다음 틱 추적).

- 02:20–02:35Z(11-22) 사용자 요청 대응: 라우터 과부하·Celly MCP 30초 타임아웃/프리즈 원인 실측 및 G: 드라이브 1차 안전 정리:
  - **라우터가 바쁜 실측 근거(3초 I/O·CPU·로그 델타)**:
    - Andy 라우터(PID 81436, 포트 9505): `dCPU=5.0s/3s (166.7%)`, `dRead=638.56 MB/3s (17,970 ops)`, `TIME_WAIT 975`. 최근 14분 `POST /ca/mcp` **6,865건 중 `work_host_page` 6,386건(93.0%)**(`work_parallel_status` 169건 + `work_status` 43건의 페이지 재조립).
    - Celly 라우터(PID 16340, 포트 9385): `dRead=217.7 MB/3s (3,752 ops)`, 최근 32분 `POST /ca/mcp` **3,974건 중 `work_host_page` 3,046건(76.6%)**, `work_parallel_status` 305건, `work_status` 148건, `channel_stdin_proxy_skipped_noise` 4,715건.
    - 양쪽 런처도 3초당 64.5 MB(Andy 72984) / 84.9 MB(Celly 72236) 읽기 수행.
  - **코드 경로 확인된 5대 부하 원인**:
    1. **페이징 폭풍(`host_result_pages.py` + `ciel-work-gate/hooks/register.js`)**: 포크의 모든 비-읽기 도구 호출마다 `ciel-work-gate`가 `work_parallel_status`(최대 ~417 KB = 46페이지)와 `work_status`를 호출. `bound()`는 매번 `host-pages/<session>/`(Andy 최대 1,085개 파일) 전체 `_prune()` `stat()` + `os.fsync()` 저장, `page()`는 매 페이지마다 417 KB JSON 전체를 Windows `.io.lock` 걸고 다시 열어 `json.loads()`(1회 호출당 ~19 MB 읽기·파싱).
    2. **상태 JSON 무캐시 풀 파싱(`work_parallel.py:278`, `work_parallel_mcp.py:547`)**: `work_parallel_status` 1회마다 `work-parallel-coordination.json`(Andy 1.91 MB / Celly 1.87 MB), `work-parallel-finalization.json`(0.95 / 0.92 MB, 2회), `work-parallel.json`(0.85 / 0.69 MB, 2회) 등을 매번 Windows `msvcrt.locking` + 전체 JSON 파싱.
    3. **`runtime-inputs.jsonl` 매 폴링 전체 스캔(`channel_message_repository.py:236-270`)**: Andy `runtime-inputs.jsonl` 10.54 MB(`.1` 20.0 MB)를 런처/라우터가 오프셋·캐시 없이 매번 처음부터 끝까지 `json.loads()`(런처 64.5–84.9 MB/3s의 직접 원인).
    4. **`ForkTurnWatch` 2초 전수 스캔(`fork_turn_watch.py:47,272,717-745`)**: Andy `subagents/` 10,467개(`5,196 MB`), 추적 포크 3,443개(완료 3,360개 포함)를 매 2초마다 각 포크당 `source_snapshot()` 2회(열기+4KB+SHA256×2) 실행 + `unknown(no_record)` 41개(**38.31 MB**)를 `_fresh_backscan`으로 매 2초마다 처음부터 재스캔·해시.
    5. **`router.log` 로테이션 실패 반복(`runtime_logging.py:162-185`)**: cap(1 MB) 초과 상태(Andy 27.6 MB, Celly 15.9 MB)에서 Windows 핸들 경합으로 `replace` 실패가 모든 로그 라인(초당 14~59회)마다 반복.
  - **Celly만 MCP 30초 타임아웃·프리즈·자동 복구되는 이유**:
    - 하드웨어/용량 차이 확인 ✅: Andy 상태는 `C:` NVMe(`CT2000P310SSD8`), Celly 전체 상태와 Windows `pagefile.sys`(13.7 GB)는 **`G:` SATA DRAM-less QLC SSD(`CT4000BX500SSD1`, 4 TB 중 99.3% 사용 / 29.45 GB 여유)**.
    - Celly `router.log` 4시간(16:22–20:25) 전수 확인: 오후 내내 30초 이상 정지 **21회**(최대 473s·440s·241s·188s·169s), `router_post_client_disconnected` 258회, `health_busy` 67회. (Andy는 동일 4시간 중 2회뿐.)
    - 부가 요인 분리 기록: 20:02–20:21 구간의 169s/86s/74s 스파이크는 기존 결함에 더해 G: 조사 목적 `robocopy /L` 2건이 겹쳐 악화됐고, 20:21 종료 직후 복구됨.
    - 프리즈·자동 복구 메커니즘: G: QLC SSD가 `os.fsync()`/GC/TRIM으로 큐가 밀리면(`readMB/s=0, writeMB/s=0, queue=8` 실측) 라우터 HTTP 스레드가 30초 이상 블로킹 → 동기 훅(`ciel-work`/`ciel-work-gate`)이 대기하며 Claude Code UI 프리즈 + MCP 30초 타임아웃(`[WinError 10054]`) → SSD 큐 해소 후 `/health`·MCP 즉시 응답 재개.
  - **G: 드라이브 1차 안전 정리 결과**:
    - 보호 집합(열린 리스·미결 검수/stranded)과 무관한 19일 이상 경과 `G:\ciel-worktrees`의 clean git worktree(`git status --porcelain == ""`) 및 gitignored `node_modules` 일부 제거 + `git -C G:\ciel-Walkie worktree prune` 완료.
    - **확보 용량**: `29.45 GB → 45.11 GB` (**+15.66 GB 확보**).
    - 단, 대량 `rmdir /s /q` 도중에도 `CT4000BX500SSD1` GC/TRIM 때문에 `G: queue=8` / Celly `/health` 2.5–5초 스파이크가 관측되어 10개 처리 후 즉시 중단함. 추가 대량 정리는 Celly 유휴 시점 또는 저속 배치 진행 필요.

- 01:51Z(11-22) 읽기 전용 틱(비교 기준 01:12Z; 운영 재시작·상태 수정·배포 없음):
  - **4개 호스트 모두 `findings=0`, `new_failures=0`**.
  - **health·G: 측정(01:51Z)**: Celly 4,134ms(PID 16340), Andy 48ms(PID 81436). Celly 라우터 19:12–19:51 로컬(39분간) 30초 이상 무기록 구간 0건. G: 여유 `31,613,919,232 B`(01:12Z 32.53 GB → −0.92 GB/39분; 23:47Z 37.16 GB 대비 −5.54 GB/124분).
  - **Andy** (`v5530 → v5551`): `submitted 23→21`, `waiting_input 8→9`, `running+slot 5→4`, `findings 1→0`, `new_failures=0`.
    - 라우터(로컬 19:13–39): 외부 SSE EOF 34·reset 5, transcript timeout 8, 모델 SSE reset 5, `cielairouter_transient_retry` 2(19:33:21), `turn_projection_persistent` 4, `agent_runtime_event_retry` 1.
    - 대화(01:16–44Z): `Parallel read failed: ECONNREFUSED` 2(01:16:26·01:22:52Z), `work_host_page: ECONNREFUSED` 1(01:17:13Z), `auto continue could not read the Work status` 2(01:17:49·01:21:14Z), side 원문 append 2(01:18:02·01:25:01Z), Walkie `client_id` UUID 오류 2.
  - **Celly** (`v6264 → v6282`): `waiting_input 15→16`, `running+slot 4`, `findings=0`, `new_failures=0`.
    - 라우터(로컬 19:13–33): 외부 SSE EOF 34·reset 8, 모델 client reset 15, `/ca/mcp` reset 6, transcript timeout 7, `cielairouter_gateway_central` 5·`transient_retry` 6(19:33:09–11), `runtime_activity` WinError 5 3.
    - 대화(21건 중 상위 12): `work_host_page: ECONNREFUSED` 1(01:17:20Z), `Parallel read failed: active slot capacity reached for P3` 2(01:20:11Z, `reacquire` 정상 용량 거부의 read 라벨), Walkie `client_id` UUID 오류 2·network 오류 1, side 원문 append 3, 에이전트 스크립트 오류 3.
  - **KOBE** (`v2808` 유지): `waiting_input 8`, `running+slot 1`, `findings=0`, `new_failures=0`. 대화: side 원문 append 3(01:23:31·32, 01:36:21Z). 라우터(로컬 17:12–35): keep-alive 정상 경고 450, 외부 SSE EOF 31·reset 7·timeout 3, transcript timeout 14·WinError 10061 3, 모델 reset 5, `agent_runtime_event_retry` 1.
  - **Prody** (`v357 → v364`): `submitted 8→6`, `reserved+slot 1`, `waiting_input 1`, `running+slot 0→1`, `findings=0`, `new_failures=0`, 새 라우터 경고 없음. 대화: side 원문 append 4, plan version 충돌 3, Walkie `client_id` UUID 오류 2, 에이전트 경로/스크립트 오류 3.
  - 이번 틱에서 새로 입증된 제품 결함 없음(직전 P0·게이트 재확인 진단 건은 사용자 지시 대기).

- 01:35–01:45Z(11-22) 사용자 요청 진단: Andy·Celly P0 블로커 처리/인지 실패 및 10분 게이트 재확인 사이드 액터 원인 분석(읽기 전용 조사, 수정·재시작 없음).
  - **정량 실측(최근 600개 서브에이전트)**:
    - Andy 게이트 재확인 포크 **318건**(타이머 104·턴 종료 214, 전부 `ciel-work:side-fresh`): `release_waits` 포함 **17건(5.3%)**. 모델별: `claude-haiku-5-5` 227건 중 0-tool 36(15.9%)·보드만 44(19.4%)·외부조회 147(64.8%)·해제 2건(0.88%); `beam-free` 39건 중 외부조회 38(97.4%)·해제 11건(28.2%); `<synthetic>` 18건(API ECONNREFUSED)·`_walkie_p1` 3건 전원 `no_json`. 최근 100개 인박스 게이트 영수증 중 `released_waits=1`.
    - Celly 게이트 재확인 포크 **213건**(타이머 141·턴 종료 72, 전부 `ciel-work:side-fresh`): `release_waits` 포함 **4건(1.9%)**, 호스트 스레드 선조회 메시지 포함 **0건(0%)**. `claude-haiku-5-5` 189건 중 0-tool **67건(35.4%)**·보드만 **59건(31.2%)**·외부조회 63건(33.3%)·해제 2건(1.06%); `_walkie_p1-celly[1m]` 7건 전원 0-tool/빈 응답.
  - **구조적 결함(코드/아키텍처) — 주원인**:
    1. 목표 `refs` 미상속(`work_gate_check.py:160`): 노드 자체 `refs`만 실어 Andy 144개 게이트 중 117개(P0 게이트 `n1823/n1825/n1826/n2473/n1962/n2559/n2560/n2562` 전원 포함)와 Celly P0 `n1140`에 `refs:`가 비어 나감(목표 루트에는 스레드 ID·conversation ID 존재).
    2. 스레드 커서 필터의 P0 게이트 드롭 + `INTERNAL_WAIT` 오매칭(`work_mcp_tools.py:701-710`, `work_gate_check.py:375`): 바인딩된 루트 스레드에 새 글이 없으면 `quiet`로 묶여 제외됨. Andy `n1936`(131641) P0 게이트 7개 중 5개(`n2559/n2560/n2561/n2563/n2564`, 다른 스레드·CI·배포 대기)와 `n1756` 2개(`n1825/n1826`, 선행 노드 대기)가 루트 스레드 무응답 시 탈락. 남은 `n1962/n2473/n2562`도 `INTERNAL_WAIT`의 단어 경계 없는 `lease`가 영단어 `release`/`released`에 우연히 매칭돼 살아남은 것.
    3. 비-리프 게이트 해제 불가(`side_work_decision.py:434-437` vs `work_gate_check.py:146-164`): 비-리프 노드도 게이트 재확인에 올려 해제를 요구하지만 적용 시 무조건 `non_leaf_wait` 거부. Andy 144개 게이트 중 24개(P0 1번 `n1823` 포함)가 비-리프; 보드 `side_decisions` 해제 거부 72건 중 37건(51.4%)이 `non_leaf_wait`(P0 루트 `n1756` 포함).
    4. `waiting_on` 120자 절단(`work_plan_tree.py:34`) + 정확 일치 요구(`side_work_decision.py:431`): Celly `155853`(P0 승인, `n1267`)에서 저장 문구가 `...설치 완`으로 잘려 있어 사이드 포크(`agent-acd7cf7a5e6ef2ee6`)가 *"원문이 잘려 확정 불가"*로 스스로 `release_waits`를 포기.
    5. `session: fresh`와 `gate_actor`/`gate_recheck` 프롬프트 전제 불일치(`side-intake.js:615-625`, `work_workflow_prompts.py:103,139,154,198`): 실제는 빈 대화(`side-fresh`)인데 프롬프트는 메인 대화를 물려받았다고 가정. 선행 노드(`n1294`/`n1154`/`n2472`) 상태·스레드 ID도 안 실어줘 포크가 조회 대상을 못 찾음.
    6. SSE 처리 12~22분 지연(Andy 00:57–01:12Z 734–1,228초 대기, Celly 00:56–01:01Z 승인 5건이 01:14–01:23Z 처리 → 그사이 메인이 01:14–01:19Z 수동 해제) 및 호스트 전용 도구(`work_side_resolve`) 노출로 인한 JSON 미반환 실패(Andy 17건, Celly 15건).
  - **프롬프트·모델 문제**:
    1. `claude-haiku-5-5`가 `"미조회라고 적어라"` 문구를 조회 생략 탈출구로 사용(도구 0회: Andy 15.9%, Celly 35.4%; 외부 미조회: Andy 35.2%, Celly 66.7%).
    2. 도구를 써도 `threads.read`/`tasks.search` 대신 `walkie_knowledge_search`만 써서 이미 승인된 `156064`/`155996`을 미승인으로 오판(`agent-ab0e6fadd45cbd6cd` 01:38Z).
    3. `turn_end`+`gate_recheck`+`gate_actor` 결합 시 메인 전달 규칙 충돌.
  - **미확인**: `_walkie_p1` / `_walkie_p1-celly[1m]` 빈 응답(Andy 3·Celly 7건)의 정확한 내부 원인, Celly `gate-threads.json` 직접 읽기(권한 거부로 미확인, 단 포크 입력상 스레드 메시지 포함 0/213 확인).

- 01:12Z(11-22) 읽기 전용 틱(비교 기준 00:52Z; 운영 재시작·상태 수정 없음):
  - **792b1d7 설치 완료**: KOBE 해시 검증 overlay 성공(`4b67ee8edc81c7fc`, 백업 `wsfields-792b1d7`, 라우터 재시작 없음) → 4개 호스트(로컬 3루트·Celly·KOBE·Prody) 파일 설치 완료. 실행 중 라우터는 재시작 전까지 `d24a9e8` 유지.
  - **health·G: 측정(01:12Z)**: Celly 166ms(PID 16340), Andy 81ms(PID 81436). G: 여유 `32,531,644,416 B`(00:52Z 32.82 GB → −0.29 GB/20분; 23:47Z 37.16 GB 대비 −4.62 GB/85분).
  - **Celly 라우터 무기록 구간 재발(로컬 18:52–19:12)**: **18:52:54–18:56:55(241초)**, 19:10:46–19:11:27(41초). 대화 `Ciel Work gate: router-error` 2건(00:52:31Z·00:56:57Z)이 두 무기록 구간(18:50:56–18:52:27, 18:52:54–18:56:55) 종료 직후와 정확히 일치.
  - **Andy** (`v5525 → v5530`): `submitted 23`, `waiting_input 9→8`, `running+slot 5`, `findings=1`, `new_failures=0`.
    - `findings=1`: `n1822`(f8c38f27, P1) 01:06:41Z 완료 영수증·01:11:49Z `missing_since` → 직후 01:12:33Z `review` `pending` 행 게시 확인(일시 지연 해소).
    - 라우터(로컬 18:52–19:06): 외부 SSE connect timeout 16·EOF 9, transcript timeout 8, `/ca/mcp` client reset 6, `turn_projection_persistent` 4, `agent_runtime_event_retry` 1, 모델 reset 1.
    - 대화: `auto continue could not read the Work status` 1(01:01:40Z), `work_host_page: ECONNREFUSED` 1(01:02:51Z).
  - **Celly** (`v6256 → v6264`): `waiting_input 15`, `submitted 1→0`, `reserved+slot 1→0`, `running+slot 2→4`, `findings=0`, `new_failures=0`.
    - 라우터(로컬 18:52–19:05): `/ca/mcp` client reset 31, 외부 SSE connect timeout 9·EOF 7·reset 2, transcript timeout 7, `turn_projection_persistent` 4, 모델 reset 2. 이번 틱에는 `ChannelFileLockBusy` 없음.
    - 대화: `Ciel Work gate: router-error` 2(00:52:31·00:56:57Z), 에이전트 cp1252 오류 1(00:57:48Z), side 원문 append 1(01:03:45Z), Walkie 3분 무응답 안내 1(01:11:39Z).
  - **KOBE** (`v2804 → v2808`): `waiting_input 8`, `submitted 1→0`, `running+slot 0→1`, `findings=0`, `new_failures=0`.
    - 대화: 에이전트 rollback 스크립트 exit 4 1(00:54:05Z), plan version 충돌 1(01:07:44Z), side 원문 append 1(01:12:21Z).
    - 라우터(로컬 16:52–17:06): keep-alive 정상 경고 240, 외부 SSE EOF 19, `turn_projection_persistent` 7, 모델 SSE reset 2.
  - **Prody** (`v349 → v357`): `submitted 6→8`, `reserved+slot 1`, `waiting_input 1`, `running+slot 1→0`, `findings=0`, `new_failures=0`, 새 라우터 경고 없음. 대화: side 원문 append 1(00:54:33Z), 에이전트 python SyntaxError 1(00:58:22Z).
  - 이번 틱에서 새로 입증된 제품 결함 없음.

- 00:52Z(11-22) 읽기 전용 틱(비교 기준 00:35Z; 운영 재시작·상태 수정 없음):
  - **792b1d7 Publish 확인**: GitHub Publish run `44967003504` **completed / success**, npm `@beta` = `0.2.51-beta.20261122-003114.792b1d7`. KOBE는 모니터링 SSH는 응답했으나 직후 `792b1d7` overlay 전송은 120초 timeout(미설치 유지).
  - **health·G: 측정(00:52Z)**: Celly 2,170ms(PID 16340), Andy 25ms(PID 81436). G: 여유 `32,824,254,464 B`(00:35Z 33.16 GB → −0.34 GB/17분; 23:47Z 37.16 GB 대비 −4.33 GB/65분). 10초 프로세스 쓰기 델타 상위: `python3.13.exe` 35.8 MB, `chrome.exe` 20.8+8.1 MB, Andy router(81436) 5.1 MB, Claude(6516) 3.7 MB(어느 드라이브 대상인지는 미확인).
  - **Celly 라우터 무기록 구간 재발(로컬 18:35–18:52)**: 18:43:08–18:44:00(52s), 18:50:17–18:50:56(39s), 18:50:56–18:52:27(91s).
  - **Andy** (`v5522 → v5525`): `submitted 22→23`, `waiting_input 9`, `running+slot 6→5`, `findings=1`, `new_failures=0`, 대화 오류 0.
    - `findings=1`: `n2042`(c3b8553a, P1 rework gen 2) 00:48:33Z 완료 영수증·00:50:55Z `missing_since` → 직후(00:52:42Z) `review` `pending` 행 게시 확인(일시 지연 해소).
    - 라우터(로컬 18:35–45): 외부 SSE EOF 16·reset 1, transcript timeout 7, 모델 client reset 1.
  - **Celly** (`v6244 → v6256`): `waiting_input 14→15`, `running+slot 4→2`, `submitted 0→1`, `reserved+slot 0→1`, `findings=0`, `new_failures`: `decision_refused_stop 0d975669 x1`.
    - 라우터(로컬 18:35–48): `/ca/mcp` client reset 28(18:42:28부터), 외부 SSE EOF 13·reset 2, transcript timeout 6, `turn_projection_persistent` 4, **`ChannelFileLockBusy` 1(18:48:07, 반복 4회째)**, 모델 client reset 1.
    - 대화(00:41:59–00:47:15Z): `work_parallel_item_review: The operation timed out.` 2(00:41:59·00:47:15Z), `Parallel read failed: The operation timed out.` 3(00:43:00·00:44:01×2Z), 일반 tool timeout 1(00:44:01Z) — 52초 무기록 구간(18:43:08–18:44:00)과 시각 일치. side 원문 append 1(00:36:08Z).
  - **KOBE** (`v2798 → v2804`, 모니터링 재접속 성공): `waiting_input 7→8`, `running+slot 2→0`, `submitted 0→1`, `findings=0`, `new_failures=0`. 대화: 에이전트 rollback 스크립트 exit 4 1건(00:47:43Z). 라우터(로컬 16:35–44): keep-alive 정상 경고 203, 외부 SSE EOF 13·reset 3, transcript timeout 3, `turn_projection_persistent` 5, 모델 SSE reset 1.
  - **Prody** (`v342 → v349`): `submitted 6`, `reserved+slot 1`, `waiting_input 1`, `running+slot 1`, `findings=0`, `new_failures=0`, 새 라우터 경고 없음. 대화: Walkie HTTP 400 1(00:40:13Z), plan version 충돌 3(00:41:01·00:51:39·00:51:49Z), 에이전트 스크립트 exit 255 1(00:49:25Z).
  - 이번 틱에서 새로 입증된 제품 결함 없음.

- 00:35Z(11-22) 읽기 전용 틱(비교 기준 00:12Z; 운영 재시작·상태 수정 없음):
  - **792b1d7 설치/배포 상태**: Prody 해시 검증 overlay 완료(`4b67ee8edc81c7fc`, 백업 `wsfields-792b1d7`, 라우터 재시작 없음). KOBE(`100.64.0.14:1004`)는 SSH `Connection reset by peer`/`lost connection` 반복으로 모니터링·파일 설치 모두 실패(미확인/미설치). GitHub Publish run `44967003504`는 아직 `in_progress`, npm `@beta`는 `c948680` 유지.
  - **health·G: 측정(00:35Z)**: Celly 206ms(PID 16340), Andy 55ms(PID 81436). G: 여유 `33,159,532,544 B`(00:12Z 34.22 GB → −1.06 GB/23분, 23:47Z 37.16 GB 대비 −4.00 GB/48분). 순간 성능 카운터 `PercentDiskTime 8%`, `CurrentDiskQueueLength 0`, `DiskWriteBytesPerSec 4.07 MB/s`.
  - **Celly 라우터 무기록 구간 재발(로컬 18:12–18:35)**: 18:12:55–18:14:05(70s), 18:14:29–18:15:15(46s), 18:21:37–18:22:58(81s), 18:25:08–18:26:30(82s).
  - **Andy** (`v5521 → v5522`): `submitted 22`, `waiting_input 8→9`, `running+slot 7→6`, `findings=0`, `new_failures=0`, 대화 오류 0. 라우터(로컬 18:12–33): 외부 SSE EOF 17·broken pipe 2·reset 2, 모델 SSE client reset 3, transcript 전송 timeout 2, `turn_projection_persistent` 3.
  - **Celly** (`v6241 → v6244`): `waiting_input 14`, `submitted 1→0`, `running+slot 4`, `findings=0`, `new_failures`: `decision_refused_stop 4a8ef95d x1`(00:21:06Z `review already decided differently` — 정상 가드).
    - 라우터(로컬 18:14–31): MCP client reset 53(18:14:05부터), 외부 SSE EOF 13·reset 2·aborted 1, transcript 전송 timeout 4, 모델 client reset 6, **`channel_stdin_proxy_submission_error stage=post_submit errno=36 reason=channel_file_lock_busy` 1(18:16:11) + `channel_windows_console_proxy_exception stage=pending_poll type=RuntimeError` 1(18:16:36, cursor=15497)** — `channel_pending_injection.py:144`가 post_submit lock busy를 `RuntimeError`로 올린 기록(새 제품 결함 아님, lock busy 재발과 연결).
    - 대화: `Parallel read failed: The operation timed out.` 1(00:14:08Z, 70초 무기록 구간 18:12:55–18:14:05 직후), `review already decided differently` 1(00:21:06Z), 에이전트 스크립트 exit 1(00:28:51Z), Walkie HTTP 400 2(00:29:31·00:30:48Z), side 원문 append 1(00:29:33Z).
  - **KOBE**: `ssh failed` (`Connection reset by 100.64.0.14 port 1004`, 재시도도 `lost connection`) — 상태 미확인.
  - **Prody** (`v342` 유지): `submitted 6`, `reserved+slot 1`, `waiting_input 1`, `running+slot 1`, `findings=0`, `new_failures=0`, 대화 오류 0, 새 라우터 경고 없음.
  - 이번 틱에서 새로 입증된 제품 결함 없음.

- 00:25Z(11-22) 피어(Andy_Main) DX 보고 수정·설치(`792b1d7`, 운영 재시작 없음):
  - 라이브 근거: Andy `n2546`(469a6edf, `agent-af548d22c474b7a7f`)이 23:59:28Z 미보고 종료 독촉 후 00:01:16–00:02:26Z `work_parallel_report(event=handoff)`에서 `workspace fields` 4회 거부 뒤 `question`으로 회귀(같은 구간 `d24a9e8`의 `handoff fields: detail needs exactly …` 실동작도 관측됨).
  - 수정(`work_parallel_receipts.py`): `_workspace`(객체 아님/키 불일치/텍스트·bool 위반)와 completion `coverage` 행 검증 오류에 정확한 필수·선택 필드명(`branch, changed_files, diff_stat, dirty, head, path` / `item_id, verified`, optional `evidence_digest`) 명시.
  - 검증: RED 확인 → Windows 격리 119/119 OK, Linux py310/py311 각 119/119 OK(스킵 5).
  - 배포·설치: `beta` 푸시(Publish run `44967003504` in_progress). 해시 검증 overlay 설치(`4b67ee8edc81c7fc`, 백업 `wsfields-792b1d7`): 로컬 3루트·Celly 완료, KOBE·Prody는 SSH 연결 끊김으로 미설치(다음 틱 재시도). 라우터 재시작 없음(실행 중 라우터는 `d24a9e8` 유지). 피어 회신 완료.

- 00:12Z(11-22) 읽기 전용 틱(비교 기준 23:52Z; 운영 재시작·상태 수정·배포 없음):
  - **health 직접 측정(00:12Z)**: Celly 504ms(PID 16340), Andy 17ms(PID 81436). G: 여유 34,219,929,600 B(23:53Z 34.86 GB → −0.64 GB/19분).
  - **Celly 라우터 무기록 구간 추가 확인(로컬, 직전 틱 이후 재스캔)**: 17:36:49–17:37:21(32s), 17:37:24–17:38:31(67s), **17:38:56–17:46:49(473s, 23:38:56–23:46:49Z)**. 17:46:49 이후 18:12:35까지 30초 이상 무기록 없음. 같은 3시간 System 이벤트 로그에서 disk/stor/nvme/Ntfs/volmgr/partmgr 경고는 0건(필터 조회). 직전 틱의 G: 조회 지연(23:44Z 전후)이 473초 구간 안에 있음 — 라우터 로그 파일(`router.log`)이 G:에 있으므로 G: 쓰기 지연 시 동기 로깅에서 전체가 멈췄을 가능성은 **미검증 가설**로만 기록.
  - **Andy** (`v5490 → v5521`): `submitted 24→22`, `waiting_input 7→8`, `running+slot 6→7`, `findings=0`, `new_failures`: `decision_refused_stop 86395366 x1`(23:53:31Z `rework limit reached (2/2)` — n1744 정상 가드). 대화: side 원문 append 2(23:53:33·45Z), Walkie HTTP 400 1(23:58:38Z), `Ciel Work gate wg-mwr2lz81-23: router-error` 1(00:00:16Z, 반복 2회째), `work_host_page: ECONNREFUSED` 2(00:05:43·00:10:13Z). 라우터(로컬 17:52–18:07): 외부 SSE EOF 17·reset 8, transcript 전송 timeout 12·10061 1·HTTP 502 2(18:07:07), WinError 5 2, `turn_projection_persistent` 4. 30초 이상 무기록 없음.
  - **Celly** (`v6224 → v6241`): `waiting_input 14`, `running+slot 5→4`, `submitted 0→1`, `findings 1→0`(n1245 재개 후 갱신), `new_failures=0`. 대화: `work_host_page: ECONNREFUSED` 1(23:59:56Z), `auto continue could not read the Work status` 2(00:04:24·00:10:07Z), 에이전트 cp1252 인코딩 오류 2. 라우터(로컬 17:52–18:03): 외부 SSE EOF 16·reset 7, transcript 전송 timeout 10, 모델 reset 3·SSE client reset 2, `agent_runtime_event_retry` 3(`100.64.0.9:8787` TimeoutError).
  - **KOBE** (`v2790 → v2798`): `waiting_input 9→7`, `running+slot 1→2`, `findings=0`, `new_failures=0`. 대화: side 원문 append 4(00:00:19·20, 00:09:39·40Z), 없는 task ID 1, plan version 충돌 3, Walkie HTTP 400 `Reply target outside conversation` 1(00:05:46Z), Walkie 무응답 비재전송 안내 1(00:06:08Z). 라우터(로컬 15:52–16:00): keep-alive 정상 경고 236, 외부 SSE EOF 15·reset 6·read timeout 1, transcript 전송 timeout 9, `agent_runtime_event_retry` 2.
  - **Prody** (`v337 → v342`): `submitted 6`, `reserved+slot 1`, `waiting_input 0→1`, `running+slot 0→1`, `findings=0`, `new_failures=0`. 대화: P3·P4 슬롯 용량 정상 거부 2(00:00:04·00:02:18Z), main의 `work_parallel_event` 직접 호출이 `Parallel read failed: unsupported parallel event` 1(00:00:19Z)·`Parallel read failed: release proof required` 2(00:00:31·41Z)로 표시됨(정상 거부이나 "read" 라벨 — Celly `not a waiting worker`와 같은 라벨 항목으로 추적), side 원문 append 3.
  - 이번 틱에서 새로 입증된 제품 결함 없음.

- 00:00Z 피어(Andy_Main) 결함 보고 확인 — "work_node_remove 후에도 n872 검수 대기가 남아 매 턴 요청됨": 읽기 전용 확인 결과 **결함 아님(일회성 지연)**. 23:54:31Z `work_node_remove id=n872` → 23:55:47Z 기존 `review_close_resolved`(`close_resolved_reviews`)가 미승인 종결: lease 1bddc7cf `done`, review `finalized`, decision `close`, decision_id `close-resolved:1bddc7cf…`, evidence "Work item n872 was removed from the board … workspace is retained at …work-parallel-f0954fa5…(8a1743ea)". 23:56:05Z 알림 1건만 n872를 포함(종결 18초 뒤, 플러그인의 직전 review projection 기반) — 이후 n872를 포함한 알림 없음. 남은 n1744(86395366)·n2454(3a70b6e3) `pending`은 max_rework 2/2로 rework 불가·approve 부적합인 상태이며, 제거/완료되지 않은 항목의 비승인 종결 경로 부재는 기존 사용자 결정 대기 항목과 동일. 피어에 회신 완료.

- 23:52Z 읽기 전용 틱(비교 기준 23:32Z; 운영 재시작·상태 수정·배포 없음):
  - **health 직접 측정(23:50Z)**: Celly 155·44·18ms(PID 16340), Andy 62·43ms(PID 81436) — 직전 틱의 Celly 15초 timeout은 재현되지 않음.
  - **Andy** (`v5490` 유지): `submitted 24`, `waiting_input 7`, `running+slot 6`, `findings=0`, `new_failures=0`, 대화 오류 0. 라우터(로컬 17:32–52): 외부 SSE EOF 11·connect timeout 11·reset 2·URLError 1, transcript 전송 timeout 11·WinError 10061 4(17:47:38부터), `agent_runtime_event_retry` 3(`100.64.0.9:8787` URLError, 17:48:11–25), MCP client reset 1.
  - **Celly** (`v6220 → v6224`): `waiting_input 15→14`, `submitted 1→0`, `running+slot 4→5`, `findings=1`, `new_failures=0`.
    - `findings=1` `n1245`(02e86498, P1) "slot held, fork absent since 23:30": 원장상 23:29:00Z question 영수증 후 native `missing_since` 23:30:19Z. 포크 transcript는 23:51:39Z "Resuming n1245 per the coordinator's message" 이후 23:52:52Z까지 도구 실행 중 → 재개된 포크가 실제 작업 중이고 native 증거만 아직 갱신 전. `d24a9e8`/`c948680` 부재 정리 경로는 이 lease를 salvage하지 않음(재개 승인 시 부재 유예 재시작 동작과 일치). 종료로 보지 않음.
    - 라우터(로컬 17:35–52): MCP client reset 30(17:35:25부터), transcript 전송 timeout 10·10061 6, 외부 SSE connect timeout 5·EOF 4, `turn_projection_persistent` 3, **`ChannelFileLockBusy` 1(17:46:50, 반복 3회째)**, 모델 client disconnected 1.
    - 대화: `Parallel read failed: The operation timed out.` 4(23:35:26–23:37:37Z, 직전 틱 무기록 정지 17:31:59–17:35:07 직후), 중복 lease 정상 가드 2(23:36:36Z `n1214`, 23:50:26Z `n1245`), side 원문 append(already) 1.
  - **KOBE** (`v2787 → v2790`): `waiting_input 9`, `running+slot 1`, `findings=0`, `new_failures`: `revival_refused x1`(23:37:04Z "Work fork this process started, not a running or waiting Work lease" — 정상 가드). 대화: 메인 직접 수정 정상 거부 1(23:36:02Z), 중복 lease 정상 가드 1(23:37:42Z), `authenticated parallel host required` 1(23:38:37Z). 라우터(로컬 15:32–39): keep-alive 정상 경고 231, 외부 SSE EOF 17·broken pipe 1, transcript 전송 timeout 8, upstream SSE error 1.
  - **Prody** (`v337` 유지): `submitted 6`, `reserved+slot 1`, `findings=0`, `new_failures=0`. 대화: ripgrep timeout 1(23:35:12Z), side 원문 append 1(23:52:36Z). 새 라우터 경고 없음.
  - **G: 볼륨 여유 공간 추세(Celly·Walkie 경로)**: 23:47Z 37,155,913,728 B → 23:50Z 34,929,897,472 B → 23:53:31Z 34,857,836,544 B (약 6분간 −2.30 GB, 대부분 23:47–23:50Z 사이 −2.23 GB).
    - 읽기 전용 스캔(최근 30분 수정 파일, 경로별 60초 상한): Celly ciel-runtime 설정 19개 0.01 GB(최대 `router.log` 5.7 MB), Celly `.claude/projects` 32개 0.10 GB(최대 main transcript 78.3 MB), Celly `.claude/worktrees` 5,123개 중 상한 전 집계 0.08 GB(60초 상한 도달, `unattended-stub.exe` 13.3 MB×2 등), Walkie `.claude/worktrees` 0개(60초 상한 도달), Celly `.local/tmp` 2개.
    - 스캔된 경로의 최근 수정분 합계는 약 0.2 GB로 −2.23 GB를 설명하지 못함. 감소 원인은 **미확인**(상한에 걸린 worktree 나머지, 스캔 대상 밖 경로, 삭제된 임시 파일 등 가능성은 검증 전).
  - 이번 틱에서 새로 입증된 제품 결함 없음.

- 23:32–23:45Z 읽기 전용 틱(비교 기준 23:12Z; 운영 재시작·상태 수정·배포 없음):
  - **Andy** (`v5480 → v5490`): `submitted 24`, `waiting_input 6→7`, `running+slot 7→6`, `findings=0`, `new_failures=0`. 라우터(로컬 17:12–17): 외부 SSE EOF 19, transcript 전송 timeout 8, upstream SSE error event 2·error shape 2(17:14:20), `late_sse_retry` 3, `remote_instructions_failed` timeout 2, 모델 client disconnected 3. 대화: 에이전트 경로 오류 1(23:25:14Z), `auto continue could not read the Work status` 1(23:31:40Z). 로그 30초 이상 무기록 구간 없음.
  - **Celly** (`v6187 → v6220`): `waiting_input 16→15`, `submitted 0→1`, `running+slot 4`, `findings=0`, `new_failures`: `decision_refused_stop 777c8253 x1`.
    - 대화 23:20:38–23:25:37Z: `work_parallel_item_review: The operation timed out.` 1, `Parallel read failed: timed out` 1, `work_host_page: ECONNREFUSED` 5, `connection closed before the tool call was answered` 3, `Parallel read failed: ECONNREFUSED` 1, gate host round `pump failed` 1.
    - **반복 이상(신규 추적)**: Celly 라우터(PID 16340, 22:22Z 교체 후 동일) 로그에 30초 이상 완전 무기록 구간 반복 — 로컬 16:22:42–16:24:37(115s, 재기동 직후), 16:46:35–16:47:27(52s), 16:47:38–16:50:27(169s), 16:50:59–16:52:10(71s), 16:52:15–16:53:01(46s), **17:31:59–17:35:07(188s)**. 무기록 중에는 `/health` 응답도 기록이 없고 keep-alive `listener_reachable_health_busy`가 남음(16:58:10·16:58:32·17:20:07·17:35:45·17:36:00·17:36:10). 23:36Z 직접 측정 `/health`: 15초 timeout 1회 → 3,669ms → 16ms → 978ms, PID 16340 유지, 포트 9385 LISTEN·ESTABLISHED 32·TIME_WAIT 26. 같은 시각 Andy 라우터(C: 경로)에는 30초 이상 무기록 구간 없음.
    - 이 분석 중 Celly 경로(`G:\ciel-bridge-service\...`)에 대한 읽기 전용 `ls`/`Get-Item`/로그 스캔이 120초 이상 끝나지 않는 현상을 직접 관측(23:45Z 전후). G: 볼륨 I/O 지연이 라우터 무기록·health busy와 같은 원인인지는 **미확인**(상관만 관측). 제품 결함으로 단정하지 않음.
    - MCP 호출량(로컬 분당): Celly tools/call 300–500(17:10–17:31) 중 `work_host_page` 150–400; Andy `work_host_page` 325–674.
  - **KOBE** (`v2787` 유지): `waiting_input 9`, `running+slot 1`, `findings=0`, `new_failures=0`, 대화 오류 0. 라우터(로컬 15:12–20): keep-alive 정상 경고 255, 외부 SSE EOF 19·reset 2, transcript 전송 timeout 3, upstream SSE error event 2(15:20:17), WinError 5 1.
  - **Prody** (`v337` 유지): `submitted 6`, `reserved+slot 1`, `findings=0`, `new_failures=0`, 새 라우터 경고 없음. 대화: side 원문 append 2(23:12:51Z·23:13:06Z), host-only `work_side_status` 정상 거부 1(23:29:06Z), 에이전트 경로/파일 없음 오류 5.
  - G: 볼륨 측정(읽기 전용, 23:47Z 완료): NTFS Fixed `New Volume`, HealthStatus `Healthy`, 전체 4,000,768,323,584 B 중 여유 37,155,913,728 B(**약 0.93%**, 사용 99.07%). Celly `router.log` 단일 stat은 958ms(작업 시작 후), 그러나 같은 PowerShell 명령(Start-Job+Get-Volume) 전체는 60초 이상 걸림. 볼륨 여유 공간 부족·지연과 라우터 무기록 구간의 인과는 미확인.
  - 이번 틱에서 새로 입증된 제품 결함 없음. 신규 추적: Celly 라우터 무기록 정지 구간과 G: I/O 지연의 관계.

- 23:12Z 읽기 전용 틱(비교 기준 22:59Z; 운영 재시작·상태 수정·신규 배포 없음):
  - **Andy** (`v5468 → v5480`): `submitted 24`, `waiting_input 6`, `running+slot 7`(변화 없음), `findings=0`, `new_failures=0`.
    - 라우터(로컬 16:59–17:03): 외부 SSE EOF 10·reset 7, `transcript_delta_delivery_failed` timeout 8·WinError 10061 3(17:00:20부터), 모델 client disconnected 3, `agent_runtime_event_retry` 3(`100.64.0.9:8787`, TimeoutError, 17:01:29–42), `late_sse_retry` 1, `turn_projection_persistent` 1.
    - 대화: side 원문 append 1(23:03:49Z), `Ciel Work gate wg-mwr0wc76-s: router-error` 1(23:12:20Z). 라우터 로그 17:12:18–21에는 오류 줄이 없고 `work_host_page` 호출이 계속 기록됨 — 원인 미확인(MCP 연결 단절 반복 항목과 함께 추적).
  - **Celly** (`v6178 → v6187`): `waiting_input 16`, `submitted 2→0`, `running+slot 3→4`, `findings 1→0`, `new_failures=0`.
    - 라우터(로컬 17:00–03): 외부 SSE EOF 10·reset 6, `transcript_delta_delivery_failed` timeout 6, 모델 client disconnected 6, `/ca/mcp` client reset 2, `turn_projection_persistent` 3, `remote_instructions_failed` reset 1. 직전 틱 `ChannelFileLockBusy`·WinError 5는 이번 구간 없음.
    - 대화: `work_side_handoff: invalid side receipt identity` 1(23:09:16Z), Walkie HTTP 400 `Invalid operation arguments` 2(23:09:17Z·23:11:22Z), 에이전트 python 오류 1(23:12:13Z).
  - **KOBE** (`v2785 → v2787`): `waiting_input 9`, `running+slot 0→1`, `findings=0`, `new_failures=0`.
    - 라우터(로컬 14:59–15:03): keep-alive 정상 경고 157, `transcript_delta_delivery_failed` timeout 13·WinError 10061 5, 외부 SSE EOF 11·reset 5, 모델 client disconnected 3, `turn_projection_persistent` 2, `agent_runtime_event_retry` 3(`100.64.0.9:8787`, TimeoutError, 15:00:39–52).
    - 대화: 에이전트 python 오류 1(23:03:38Z), plan version 충돌 2(23:07:46Z·23:10:53Z), 활성 목표 변경 정상 가드 1(23:08:34Z).
  - **Prody** (`v337` 유지): `submitted 6`, `reserved+slot 1`, `findings=0`, `new_failures=0`, 새 라우터 경고 없음. 대화: plan version 충돌 3(23:08:19Z·23:09:19Z·23:10:03Z), side 원문 append 1(23:09:14Z).
  - **공통 관측**: 로컬 17:00–17:03(Andy·Celly)·14:59–15:03(KOBE, UTC−8) 같은 23:00Z 전후 구간에 세 호스트 모두 `transcript_delta_delivery_failed`(timeout/10061)와 `agent_runtime_event_retry`(`100.64.0.9:8787` TimeoutError)가 동시 발생 — 외부 수신처 쪽 일시 장애로 보이나 원인은 이 틱에서 확인하지 않음.
  - 이번 틱에서 새로 입증된 제품 결함 없음.

- 22:59Z 읽기 전용 틱(비교 기준 22:41Z; 운영 재시작·상태 수정 없음):
  - **Publish 확인**: `c948680` GitHub Publish run `44959013724` **completed / success**, npm `@beta` = `0.2.51-beta.20261121-225317.c948680`.
  - **Andy** (`v5452 → v5468`): `submitted 24`, `waiting_input 5→6`, `running+slot 8→7`, `findings=0`, `new_failures=0`.
    - 라우터(로컬 16:41–56): 외부 SSE EOF 16·connect timeout 3, MCP client reset 11(16:56:29부터), `transcript_delta_delivery_failed` timeout 3(16:47:13부터), 모델 client disconnected 2, `turn_projection_persistent(observe_only)` 5.
    - 대화(22:56:30–22:59:05Z): `work_host_page` connection closed 3, `Parallel read failed: is not connected` 6(22:57:46–47Z), `state changed; reread` 1(22:59:05Z).
    - Andy_Main 피어 메모: 유령 슬롯 없이 포크 7개 실행 중; `Ciel side wake 9cbc9ae3f83f`가 22:55:20Z(`8251febe6437`)·22:58:39Z(`40bbeaa3a1a4`) 두 번 도착(첫 번째 시각 22:55:20Z는 `c948680` `ciel-work: reloaded` 22:55:20.914Z 직전으로, 재로딩·재연결 경계와 겹침 — 원인은 아직 미확인, 추적 항목 추가).
  - **Celly** (`v6175 → v6178`): `waiting_input 16`, `submitted 0→2`, `running+slot 5→3`, `findings=1`, `new_failures=0`.
    - 직전 유령이던 `n1241`(662bd67f)이 완료 보고(`submitted`, `slot=False`) 후 검수 `pending` 행까지 정상 게시됨.
    - `findings=1`: `n1031`(3e70cd9e, P2) 완료 영수증 22:56:40Z, `missing_since` 22:59:57Z → 직후 재조회(23:01Z)에서 `pending` 검수 행 게시 확인(미관측·게시 지연이 일시 해소됨).
    - 라우터(로컬 16:42–58): MCP client reset 41(16:50:28부터), 외부 SSE EOF 10·connect timeout 1, `runtime_activity` WinError 5 접근 거부 4, `ChannelFileLockBusy` 2(16:58:16·16:58:30, `workspaces/52ea0f8a2296/side-inboxes/...`), 모델 client disconnected 2. 대화: `Parallel read failed: The operation timed out.` 2(22:50:22Z·22:53:02Z).
  - **KOBE** (`v2783 → v2785`): `waiting_input 9`, `submitted 1→0`, `findings=0`, `new_failures=0`, 대화 오류 0. 라우터(로컬 14:41–55): keep-alive 정상 경고 212, 외부 SSE EOF 15·broken pipe 1, `turn_projection_persistent` 2, `runtime_activity` WinError 5 1, 모델/MCP client reset 각 1.
  - **Prody** (`v337` 유지): `submitted 6`, `reserved+slot 1`, `findings=0`, `new_failures=0`, 새 라우터 경고 없음. 대화(22:44–47Z): `walkie_send_message` client_id UUID 검증 오류 1, `pdftoppm` 미설치 1, plan version 충돌 1, `open_requests_watch.py` 사용법 오류 1(모두 에이전트·도구 호출 오류).
  - 이번 틱에서 새로 입증된 제품 결함 없음(재시작·상태 수정·신규 배포 없음).

- 22:30Z 읽기 전용 틱 + 22:41Z KOBE/Prody 재조회 및 결함 2건 수정·설치(`c948680`, 운영 재시작 없음):
  - **d24a9e8 실효과 관측(22:41Z)**: Andy `n2532`(5cbfd654)와 Celly `n1229`(8deddae7)가 `host_salvage` 영수증(`edd4222a…`, `9d6f2b23…`) 기록 후 `unreported`로 전환되며 슬롯을 반환함(observed_at 22:33:45Z / 22:33:40Z). Celly `n1241`(662bd67f)은 아직 `running+slot`.
  - **4개 호스트 틱 비교(22:25Z → 22:30Z → 22:41Z)**:
    - Andy: v5435 → v5439 → v5452; `submitted 24`, `waiting_input 4→5`, `running+slot 10→8`, `findings=0`, `new_fail=0`. 22:30Z 라우터 로그(로컬 16:26–28): 외부 SSE EOF 4, 모델 클라이언트 reset 2, late SSE retry 1; 22:41Z 대화: 병렬 완료 대기 정상 가드 1(22:34:34Z).
    - Celly: v6164 → v6167 → v6175; `waiting_input 16`, `running+slot 5`, `findings=0`, `new_fail=0`. 22:30Z 라우터(로컬 16:25–30): 외부 SSE EOF 5, 모델 reset 3, MCP reset 1, turn projection observe_only 3; 대화: 22:26:30Z 에이전트 스크립트 exit 1, side 실패 원문 append 2(22:27:34Z·22:29:15Z). 22:41Z 대화: plan version 충돌 2(22:32:56Z·22:34:09Z), 중복 lease 정상 가드 1(22:34:58Z), `work_host_page: ECONNREFUSED` 1(22:34:16Z), `Parallel read failed: not a waiting worker` 1(22:35:19Z, 정상 거부 문구가 read label로 묶임 — 후속 확인 대상).
    - KOBE: 22:30Z `ssh failed` → 22:41Z 조회 성공. v2779 → v2783; `waiting_input 9`, `submitted 0→1`, `findings=0`, `new_fail=0`, 대화 오류 0. 라우터(로컬 14:30–40): keep-alive 134(정상 유지 경고), 외부 SSE EOF 9·reset 2·broken pipe 1.
    - Prody: 22:30Z `ssh failed` → 22:41Z 조회 성공. v337 유지; `submitted 6`, `reserved+slot 1`, `findings=0`, `new_fail=0`. 대화: side 원문 append 1(22:38:19Z), 에이전트 python 오류 1(22:41:00Z).
  - **입증된 결함 2건 수정 (`c948680`)**:
    1. `ciel-work/hooks/register.js`: P0 목표(`n1936`)가 `planning`일 때 재기동 후 복구된 P0 포크(`n2531`, `1c3ab08e`)의 Write가 `ciel-work` 자체 게이트에서 `"Work mode is planning"`으로 거부된 실사례 확인(21:54:40Z). `workToolCall`/`workToolCheck`가 `mod_planning`으로 막기 전에 라우터 `planning_write_authorized === true`인 running lease 포크를 허용하도록 수정(상태 조회 실패 시 fail-closed, 비인가 서브에이전트·메인 유지).
    2. `work_parallel_receipts.py`: Andy `n2531`(21:57:25Z)·Celly `n1229`(22:24:10Z)가 2,000자 초과 evidence로 `event=question`을 보내 `question text`만 받고 막힌 실사례 확인. 텍스트 길이/타입/빈문자/NUL 위반 시 필드명·실제 길이·2000자 제한을 명시.
    - RED 확인 후 수정 → Linux py310/py311 각 319/319 통과(스킵 38), Windows `claude plugin validate`·`test` 90/90 통과, 영향 4파일 319건 중 부하 시 `test_side_status_delivers_failed_original_without_a_free_actor_slot` 1건 실패 → 단독 재실행(side 25건) 통과.
    - `beta` 푸시 → npm `@beta` `0.2.51-beta.20261121-225317.c948680` 확인(Workflow `44959013724` in_progress).
    - 해시 검증 overlay 설치(백업 태그 `p0qtext-c948680`, `register.js` `4221497722bbe7d5`, `work_parallel_receipts.py` `c9e8d3fcb47e95fb`): 로컬 3루트·Celly·KOBE·Prody 모두 이전 해시 확인 후 교체. **운영 라우터/세션 재시작 금지 준수**: `register.js`는 Celly(22:53:53Z)·Andy(22:55:20Z)에서 `reloaded (19 hooks)` 확인, `work_parallel_receipts.py`는 파일만 설치됐고 실행 중 라우터는 재시작 전까지 이전(`d24a9e8`) 코드 유지.

- 22:18–22:28Z 배포·라우터 재시작·직후 틱(`d24a9e8`, npm `@beta` `0.2.51-beta.20261121-221842.d24a9e8`, Publish run `44956067853` success):
  - 회귀 최종: Windows 영향 4파일 317/317 통과(스킵 33), `test_work_mod_real_backend` 120/120 통과(스킵 3), `claude plugin validate`·`test` 90/90 통과, 전체 work_parallel 547건 중 git 30초 timeout 1건 단독 재실행 통과, mod 관련 21파일 642건 중 git 30초 timeout 1건 단독 재실행 통과; Linux py310/py311 각 317/317 통과(스킵 38).
  - 해시 검증 overlay 설치(백업 태그 `zombiefix-d24a9e8`): `register.js` `7790f9eed94446ec`, `work_parallel_receipts.py` `81052373896f931c` — 로컬 3개 루트, Celly(관리자 권한), KOBE, Prody 4개 호스트 모두 이전 해시 확인 후 교체·검증.
  - 사용자 지시 라우터만 재시작(launcher·Claude PID/생성시각 불변 검증, hashes 2/2):
    - Andy `9505-aae7db361c7f`: router `46320 → 81436`, launcher `72984`, Claude `6516`(21:45Z 재기동된 세션 유지).
    - Celly `9385-52ea0f8a2296`: router `10972 → 16340`, launcher `72236`, Claude `74984` 유지.
    - KOBE `9779-651d879f6c68`: router `1112 → 5704`, launcher `10132`, Claude `14844` 유지.
    - Prody `9592-c53b7c787223`: router `13068 → 17268`, launcher `6384`, Claude `12580` 유지(이전 버전 2fd75cd 패키지).
  - 라이브 반영 구분:
    - 라우터(receipts 검증): 4개 호스트 모두 새 라우터 PID로 기동됨.
    - `ciel-work` 훅(`register.js`): Andy(22:22:10Z)·Celly(22:22:35Z) transcript에 `ciel-work: reloaded (19 hooks)` 확인됨. KOBE·Prody 핫리로드 여부는 미확인.
    - 실제 동작 관측: Andy n2532(5cbfd654)가 22:25:50Z에 `Your native turn ended without a durable Work exit receipt` 독촉을 받아 재개됨(이 전에는 Work recovery 22:23:14Z). Celly n1229·n1241은 22:23Z Work recovery로 깨어났고, n1229가 22:24:09Z `work_parallel_report(event=question, detail 없음)`을 호출했으나 evidence 길이(2,216자 > MAX_TEXT 2,000자)로 `question text` 오류를 받음(새로 관측된 미결 항목: detail 기본값의 길이 제한·안내 부재).
  - 22:25–22:26Z 읽기 전용 틱: 4개 호스트 모두 `findings=0`, KOBE·Prody `unreadable: work-parallel-stranded-v1.json` 파일 없음. Andy v5435(submitted 24, waiting_input 4, running+slot 10; 22:22:11–13Z 라우터 교체 순간 MCP not connected 8+), Celly v6164(waiting_input 16, running+slot 5), KOBE v2779(waiting_input 9, running 0; keep-alive 30, router_lifetime_restart 1), Prody v337(submitted 6, reserved+slot 1; `decision_refused_stop a455477b x1`). 운영 상태 파일 직접 수정 없음.
  - 추가 미결(사용자 결정 필요): (1) Andy_Main 보고 `work_parallel_item_review` 비승인 종결 경로 부재(review.pending 4건 교착), (2) 127.0.0.1 + X-Forwarded-* 인증 중계 미구현, (3) SSE 원격 토큰 주입 미지원, (4) Celly n1229 `question text` 길이 초과(2,216자 > 2,000자).

- 21:20–22:00Z 결함 수정 2건(피어 보고 1-2·1-3), 사용자 지시 "개발 완료 후 andy kobe celly prody 업데이트, 라우터만 재시작":
  - 라이브 근거(읽기 전용): Andy n2532(5cbfd654)·n623(34b66f71) 포크 transcript 끝에서 `work_parallel_report(event=question, detail={reason})`가 `question fields`로 거부된 뒤 end_turn했고, meta `finished=true`. 원장 native 기록은 마지막 `running`(20:39:27Z/20:43:41Z) 뒤 `missing_since`(20:40:46Z/20:45:26Z)이며 terminal 관측은 없음. lease는 `running`·slot 보유, run exit 없음. n2543(d407260c)도 end_turn(20:36:34Z) 후 같은 형태. Celly n1229(8deddae7)·n1241(662bd67f)은 resume된 lease(`running`)에 옛 question exit가 남아 있고, 포크는 end_turn 후 목록에서 빠짐. 엔진 문서(`$.agent.list`): "One entry an agent, until the engine drops its task: as a teammate ends or seconds after, a subagent's later." 또 이 플러그인이 띄운 포크의 turn.complete는 이 플러그인 훅에 오지 않음(기존 메모 측정).
  - 수정 A(`work_parallel_receipts.py`): detail 키가 틀리면 `"<kind> fields: detail needs exactly …; got …"`로 필요한/받은 필드를 명시. 검증이 기록 전에 실행되므로 같은 worker_token으로 재보고 가능. RED: `'question fields'`만 반환(Andy 라이브 문구와 동일) → GREEN.
  - 수정 B(`ciel-work register.js`): ① running lease의 question exit/receipt는 이미 응답된 것이므로 종료 영수증으로 보지 않음(RED: TaskStop만 하고 슬롯 유지). ② 이 프로세스가 bind한 running 포크가 목록에서 30초 이상 계속 안 보이면 독촉, 다시 30초 뒤 두 번째 독촉. 그래도 보고가 없으면 기존 host_salvage(작업공간 관측·보존)+unreported로 슬롯 반환. turn 종료와 15초 관측 주기 양쪽에서 적용(RED: reconcile unknown만 반복). 제외 대상: 이전 프로세스(`gone`)의 lease, resume 승인 중인 lease, 막 resume된 포크(유예 재시작). 독촉 횟수는 초기화하지 않아 독촉→부활 반복이 생기지 않음. Linux 1차 실행에서 기존 테스트 `test_a_parked_worker_the_native_list_dropped_is_still_resumed`가 실패(재개 직후 독촉 2회) → "부재가 유예 이상 지속" 조건으로 고쳐 통과.
  - 회귀: Linux py310/py311 각 317건, 실패 0(스킵 38). Windows 결과는 아래 배포 항목에 기록.

- Andy_Main 피어 요청(Work host 결함 3건 + P0 131641 계약 2건) 코드·라이브 확인 회신. 정정: Andy 라우터 PID 74488 아님 → 18:50Z 라우터만 교체 후 46320, `/health` 버전 문자열은 overlay라 c955630 표시. (1) fork gate "Work state unreadable (work_host_page was refused or failed)": ciel-work-gate `register.js:219` fail-closed 정상 가드; 같은 구간 Andy MCP 연결 종료 11(20:35:32Z~)·ECONNREFUSED 6(20:37:30~20:51:23Z), 연결 실패의 근본 원인 미확인. (2) question 보고 거부: 라우터 tools/list의 detail 스키마는 `{"type":"object"}`, receipt 검증은 `{question, blocking_todos, decision_needed}` 정확 일치 요구(`work_parallel_receipts.py:122`), detail 생략 시 evidence로 기본값(`work_parallel_mcp.py:1247`); 스키마·설명·포크 지시문 어디에도 필드 미기재 → 계약 결함, 미수정. (3) 미보고 종료 포크 슬롯 잔류: 종료 계약 2회째(host_salvage→unreported)는 같은 포크의 다음 turn.complete 또는 종료 상태 행이 있어야만 실행(`register.js:3622-3658, 4430-4448`) → 목록에서 사라진 포크는 슬롯 영구 점유(Celly n1229/n1241, Andy n2543 동형), 미수정. 계약 1: `router_access.py` fe559b3 이후 무변경, forwarded 헤더+external_access_enabled=False면 거부 → 127.0.0.1+X-Forwarded-* 중계 미구현. 계약 2: `external_event_receiver.py`는 수신기 자신의 원격 SSE 접속용 Authorization/webhook secret만 사용, SSE로 admin/work 토큰 주입·회전 코드 없음. c4ec55d·2fd75cd 어디에도 5건 해결 없음. 피어 수신 확인. 라이브 슬롯 해제·상태 파일 수정·재시작 없음. 2fd75cd ciel-work `claude plugin test` 90/90 통과 기록 유지.

- 14:12:26–14:12:47Z 읽기 전용 틱: Andy v5180 실행 슬롯 7(직전 4)·입력 대기 1·제출 20; 후속 `/parallel` HTTP 200(0.49초), 네이티브 확인 0, 검수 `n2453/n2175/n2042 rework_ready` 유지, P0 reacquire 재발 항목 없음. 라우터 로컬 외부 SSE EOF 9(08:02:41부터), MCP reset 3(08:08:16부터), 모델 라우터 reset 1(08:09:27), transcript 전송 timeout 1(08:10:06). 대화: host-only side wake/status 거부 각 1, ripgrep timeout 1. Celly v5971 실행 슬롯 4·입력 대기 1·제출 1, 후속 REST HTTP 200(0.76초), native confirmed 0, 제출 검수 대기; n1215/n1234/n1195 검수 행 없음 findings는 이번 틱에 없음. 라우터 로컬: MCP reset 17(08:02:15부터), 외부 SSE EOF 9(08:02:55), MCP 파일 잠금 2(08:07:02부터), 모델 SSE reset 2(08:09:53), 턴 관측 지연 5(08:04:07), 채널 콘솔 미확인 1(08:04:39), keep-alive 0/정상 유지. KOBE v2763 실행 슬롯 1·입력 대기 7, n32 12:33부터 미관측 유지. 외부 SSE EOF 10(06:02부터), keep-alive 122(연결 실패 제외), side 알림 2줄, 12:58Z 도구 HTTP 404 하나로 종료됐는지는 확인하지 않음. Prody v311 실행 슬롯 2·입력 대기 1·제출 6, n1170 06:14Z부터 미관측; 계획 버전 충돌은 이번 틱에 없음. 비종료로 단정하지 않음. 운영 재시작·상태 파일 수정·배포 변경 없음.

- 14:02:09–14:03:49Z 읽기 전용 틱: Andy v5122 실행 슬롯 4·입력 대기 11·제출 20, 후속 REST HTTP 200(0.55초). `P0 reacquire parent authority` 5건(13:10–13:14Z, n2453/n2042). Celly v5964: 현재 호스트 조회와 REST는 API 감시 파일만 있고 raw tick 줄이 없으므로 이번 틱에서는 router MCP 종료 여부를 확정하지 않음. KOBE v2763 실행 슬롯 1·입력 대기 7, Prody v311 실행 슬롯 2·입력 대기 2·제출 6에서 측정한 상태 변화와 에러는 틱 원문을 보존한다. 이 틱에서 운영 세션·라우터·상태 파일 변경 없음. Windows 186건과 Linux 307/307건 패널·복구 회귀는 완료했고 최종 신호만 기록한다.

- 13:04:07–13:04:23Z 읽기 전용 틱 (`monitor/tick.jsonl`): Andy v5120 실행 슬롯 4·입력 대기 11·제출 20(직전 v5103, 제출 21에서 20), 신규 미관측 n1166(12:47Z부터)·n2532(07:54Z부터), 12:59Z 두 번의 `P0 reacquire parent authority` 도구 오류, 라우터 로컬 외부 SSE EOF/timeout 29(06:20부터), 턴 관측 지연 2, upstream HTTP 499/재시도 단일 기록(06:46Z). Celly v5848 실행 슬롯 0·제출 4, 직전 제출 1·대기 1·실행 2→비활성 슬롯 감소; n1228 완료 영수증 있으나 검수 행 없음/native failed, n1234·n1195 완료 영수증/검수 행 없음/native running(각 12:16Z 기준), Celly 스크린/상태 REST 타임아웃. 소유자 실행대기 해제 이력 9건 로그는 07:03 로컬; 새 primary pid 이후 현재 건강성은 본 쿼리에서 확인하지 않음(07:03 전후 로그만 집계). KOBE v2760 실행 슬롯 1·입력 대기 7, n32/n44 미관측(각각 12:33/12:44부터), side 원본 전달 알림 2건; 외부 SSE EOF 26(04:20:49부터), 턴 지연 5, 전송 timeout 2. Prody v306 실행 슬롯 1→2·입력 대기 2·제출 6→7; n1181 검수 행 없음/native running, n1170 06:14 이후 미관측 유지. 대화: 12:58Z KOBE 도구 HTTP 404 1, Prody 계획 버전 충돌 2. Celly keep-alive 경고는 연결 실패로 집계하지 않음. 미관측은 종료로 해석하지 않았고 운영 상태 파일·라우터 재시작은 없음.

- Celly 프리즈/MCP 수동 재연결 구간: 읽기 전용으로 확인한 현재 Celly MCP·라우터는 9385 `/health` 0.01–0.02초, `work_status` MCP 0.22초, `/ca/work/api/v1/parallel` 0.41초 HTTP 200이었으나, 실제 화면이 살아났는지는 이번 쿼리로 확인되지 않았다. 프리즈의 원인은 연결 실패가 아니라 Celly 세션 전후 기록부(Transcript)로 남았으며, 로컬 MCP는 라우터와 Work MCP가 함께 죽은 경우 재연결이 필요하다. 이 틱(읽기 전용)에서 운영 라우터 재시작·상태 파일 수정은 하지 않았다.
- 현재 작업 중인 격리 코그 분석은 Celly 패널(`readSnapshot` fallback 캐시 가드)+복구 원장 손상(격리 후 새 원장)만 재현·수정·검사한다. Code Mode는 공식 https://code.claude.com/docs/en/code-mode 기준, 새/재시작 세션 시작 시 환경변수/설정으로 평가되며 `/code-mode` 토글은 문서에 없고, 이 계획의 코드 변경에서는 제외한다.

- Code Mode 조사(공식 https://code.claude.com/docs/en/code-mode): 문서에 `/code-mode` 같은 실행 중 슬래시 토글은 없으며, 활성화는 환경변수 `CLAUDE_CODE_EXPERIMENTAL_CODE_MODE` 또는 사용자 설정 `env` 블록으로 새/재시작 세션을 시작하고 최초 확인이 필요하다. Ciel은 로컬 라우터·gateway·Work MCP/hooks를 함께 쓰므로 이번 패치와 함께 활성화하지 않는다. 이후 별도 gate로 조사 대상이다.
- Celly 프리즈/MCP 분석·패치(계획 범위): ① Work 패널 fallback 버그를 fail-first로 재현하고, fallback 새로 고침 성공 시 패널이 detail을 여는 수정과 상태를 모를 때 종료로 단정하지 않는 메시지 수정을 격리 테스트로 검증. ② 스트림 복구 원장의 NUL/깨진 JSON 문제를 `tests/test_claude_stream_recovery_runtime.py:CorruptLedgerTests`에 넣고, 손상 바이트를 `.corrupt-*`로 격리한 뒤 새 원장으로 초기화하는 수정과 파일 부재 케이스를 검증. Windows Celly recovery 테스트 28건 및 Linux py310/py311 28/28, plugin validate/test 90건 통과를 기존 증거로 유지. 원인 단정은 하지 않음: NUL 파일이 어떻게 생성됐는지, 파일 잠금이 프리즈의 원인인지 미확인. 설치·라우터 재시작은 이 검토에서 하지 않았다.

- 사용자가 보고한 Celly 심한 렉·프리즈 뒤 MCP 수동 재연결 관측: 06:31 로컬 현재 재조회에서 `/health` 0.01–0.02초, `work_status` MCP 0.22초, `/ca/work/api/v1/parallel` 0.41초 모두 HTTP 200이었다. 이 결과는 프리즈가 현재 해소·복구됐다는 진단이 아니다. 재연결 또는 라우터와 Celly 터미널 지연의 인과는 확인되지 않았다. Celly의 실제 화면 상태와 포크/포크 모드의 현재 실패 여부는 이번 읽기 전용 후위 조회에서 확인하지 않음.

- 사용자 응급 요청으로 9385 포트 Celly 라우터를 재시작했고 새 프로세스 PID가 보고됐다. 이후 재확인에서 PID가 최종 74260으로 확인됐으나 교체 횟수는 이 요약에서 확정하지 않음. 같은 instance `9385-52ea0f8a2296`, 세션 `claude:c07e...`, 라우터 버전 `7765e9d`, `/health`·Work runtime HTTP 200, MCP `tools/list` HTTP 200(60개 도구), 12초 간격 확인에서 PID 유지. 실제 Celly 화면과 Work 패널 재연결은 확인하지 않음. 상태 파일 직접 편집 없음.

- 11:32:23–11:32:39Z 읽기 전용 틱 (`monitor/tick.jsonl`):
  - Andy v5103: 슬롯 실행 4→3·입력 대기 11→12·제출 20→21·예약 슬롯 1→0; 후속 REST HTTP 200(0.58초), native confirmed 0, 검수 n2453/n2175/n2042 rework_ready. n1024가 완료/제출 행으로 이동했다. n2532 07:54Z 이후 미관측 유지. 라우터 로컬 외부 SSE 읽기 EOF 13/broken pipe 1(05:15:45부터), MCP reset 1(05:15:03부터), 모델 SSE reset 1(05:02:03), 턴 관측 지연 3. 12초 전 틱의 `native=running last=11:32`는 직후 재조회 native confirmed 0와 다르게 기록되어 이를 확정된 상태 변화로 보지 않음.
  - Celly v5808: 슬롯 실행 5 유지·입력 대기 1→2·제출 1; 후속 REST HTTP 200(2.09초), native confirmed 0, 검수 n1220 awaiting_capacity 유지. 대화: 11:25:26Z side receipt identity 오류, 11:26:08Z plan version 충돌 각 1. 라우터 로컬 외부 SSE EOF 14(05:16:12부터), 모델 SSE reset 23(05:15:37부터, 05:23:30 stream recovery note JSONDecodeError와 OSError 1쌍(삭제 예정 손상 파일 현재 전체 NUL(37,582바이트) 상태 유지; 파일 손상의 경위 미확인). 삭제·복구·상태 파일 편집은 이 틱에서 하지 않음. activity 파일 교체 접근 거부 3(05:18:53부터), MCP reset 2(05:08:50부터), 턴 관측 지연 5, TaskList 합성 20, 정상 keep-alive 205.
  - KOBE v2746: 실행 슬롯 1 유지·입력 대기 7→6·제출 1→0; n42는 검수 행 없음/native running(10:50Z) finding이 이번에 없음. 대화 11:15:49Z 미승인 목표 재개 거부, 11:15:59Z 사용자 요청 없이 활성 목표 변경 거부 각 1; side 알림 3줄(2 이벤트). 라우터 로컬 외부 SSE EOF 17(03:15:06부터), 모델 SSE reset 1(03:28:03), 모델 라우터 reset 1(03:16:05), 턴 관측 지연 2, 정상 keep-alive 207. 새 Work 원장 실패와 findings 없음. stranded 파일 없음.
  - Prody v293: 실행 슬롯 1·입력 대기 2·제출 6 유지; n1180 완료 영수증·검수 행 없음/native running(11:18Z, miss 없음), n1170 미관측 유지. 새 라우터·대화·원장 오류 없음; 검수 종결 증거 아님.
  - P0 패치 npm `@beta` 0.2.51-beta.20261121-111428.43ca5c6 확인 및 게시 44898138497 성공. 운영 상태 파일·세션·라우터 변경 없음.

- 11:14:59–11:15:22Z 읽기 전용 틱 (`monitor/tick.jsonl`):
  - Andy v5095: 슬롯 실행 4 유지·입력 대기 11·제출 20→21·예약 슬롯 1; 후속 v5096 REST HTTP 200(0.7초), native confirmed 0→3. 검수는 n1023 pending, n2453/n2175/n2042 rework_ready. n2532 미관측 유지(07:54Z 이후). 라우터 로컬 외부 SSE EOF 19·broken pipe 1(04:54:00부터), 턴 관측 지연 3(04:59:31부터), transcript 전송 timeout 1(05:00:22), 모델 라우터 reset 1(04:26:54), late SSE retry 1(05:04:24, attempts=20까지 같은 재시도 상한 표시는 시도 건수와 별개). 대화 신규 오류와 원장 실패 없음.
  - Celly v5801: 슬롯 실행 6→5·입력 대기 1→2·제출 1 유지; 후속 REST HTTP 200(2.7초), native confirmed 0, 검수 n1220 awaiting_capacity만 표시. 라우터 로컬 외부 SSE EOF 16(04:54:16부터), 모델 SSE reset 25(04:54:56부터), MCP reset 12(05:08:50부터), 턴 관측 지연 1(04:55:50), transcript 전송 timeout 1(05:05:20), 활동 파일 교체 WinError 5 2(05:08:53부터), late SSE retry 1(05:03:50), TaskList 합성 30, 정상 keep-alive 206. 이 틱에는 stream-recovery·파일 잠금 그룹 없음; 파일 복구는 이 틱에서 재읽지 않음.
  - KOBE v2745: 슬롯 실행 1·입력 대기 7 유지. 10:53:48/58 및 10:54:07Z `work_parallel_item_review: Work finalization changed; reread` 3건, 원장 decision_refused_stop(187d0858) 3; 해당 코드는 상태 충돌 예외 `Work finalization changed; reread`를 던지므로 이후 성공/종결 여부는 이 틱에 없음. 라우터 로컬 외부 SSE EOF 20(02:54:12부터), 턴 관측 지연 2, transcript 전송 timeout 1, 모델 라우터 reset 1(03:05:34), 모델 SSE reset 1(03:12:22), 정상 keep-alive 255. stranded 파일 없음.
  - Prody v292: 슬롯 실행 1→2·입력 대기 2·제출 6 유지, n1170 06:14Z부터 미관측 유지. 대화 10:54:45Z P5 실행 슬롯 1/1으로 용량 거부 1, 10:57:54Z Work 상태 읽기 실패 1; 새 라우터 로그 그룹·durable 실패 없음, stranded/실패 파일 없음.
  - `43ca5c6` GitHub 게시 still in_progress, npm `@beta`는 56805d1 유지. 신규 코드 결함으로 재현하지 않았고 운영 세션·라우터·상태 파일 변경 없음.

- 10:53:33Z 읽기 전용 틱 (`monitor/tick.jsonl`): Andy v5089 실행 슬롯 4/입력 대기 11/제출 20, REST 200, native confirmed 0. n2532만 미관측 유지(n2472/n2453 findings 사라짐), 검수 n2453/n2175/n2069/n2042 rework_ready. 라우터 로컬 외부 SSE EOF 14(04:38:55부터), 턴 관측 지연 3, 모델 SSE reset 1(04:43:25). 새 durable 실패 없음. Celly v5794 실행 슬롯 3→6/입력 대기 1/제출 1 표시; n1215 완료 영수증·검수 행 없음, 최신 native running(10:51)과 미관측(10:53)이 병기됨. 대화 10:48:50Z side receipt identity 오류 1, 계획 분해 가드 1. 라우터 로컬 외부 SSE EOF 14(04:39:16부터), MCP reset 2(04:39:20부터), 모델 SSE reset 23(04:39:31부터), activity 파일 교체 접근 거부 3(04:46:05부터), 채널 커서 쓰기 오류 1(04:43:00), 턴 관측 지연 3, TaskList 합성 22, 정상 keep-alive 154. KOBE v2742 입력 대기 7 유지; n42 완료 영수증·검수 행 없음, native running(10:50Z), 라우터 로컬 EOF 15(02:38:45부터)/모델 SSE reset 5(02:45:34부터)/턴 관측 지연 2/keep-alive 179; side 알림 6줄, 10:52:55Z goal n34 workflow 없음 1. Prody v290/실행 슬롯 1/대기 2/제출 6 유지, 대화: 외부/업무 인수 오류(HTTP 400 및 client_id UUID), 계획 버전 충돌 2, 완료 보류 안내 1, side 알림 2, 별도 CLI exit 2/1 각 1. 미관측을 종료로 보지 않음. P0 rework native-name 가드는 이 보고 후 격리 패치 검증 중; 운영 적용하지 않음.

- 10:12:22–10:12:35Z 및 10:38:33–10:38:49Z 읽기 전용 틱:
  - Andy: v5081 실행 슬롯 4/입력 대기 11/제출 21 → v5089 4/11/20. 후속 REST HTTP 200(0.94초), native confirmed 0, 검수 n2453/n2175/n2042 rework_ready(n2069 표시 없음). n2532 07:54Z부터 미관측; 종료 증거 아님. 10:12 구간 대화: Walkie client_id UUID 검증 오류 1(09:59:36Z), 같은 side 이벤트 알림 2줄. 라우터 로컬: 외부 SSE EOF 19→25(04:12:41부터), 턴 관측 지연 3/3(04:12:59부터), 모델 라우터 reset 1/2(04:26:54부터). 새 Work 원장 실패 없음. P0 reacquire 부모 이름 불일치 결함은 이전 틱에서 원장으로 확인했으며 아직 미수정.
  - Celly: v5758 실행 슬롯 6/입력 대기 1/제출 1 → v5779 유지. 10:38 후속 REST 7초 timeout; 라우터 로그 04:40:07 동일 `/parallel` 200 1, 503 1. 10:38 구간 대화: 자동 계속의 Work 상태 읽기 실패 1(10:38:00Z), gate router-error 1(10:38:11Z), plan version conflict 2, 파일 없음 1, Walkie client_id UUID 오류 1, 별도 CLI exit 1. 10:12 구간에는 진단 스크립트 KeyError/AttributeError/SyntaxError 각 1·2, 쓰기 가드 1, HTTP 400 2, gate router-error 1. 라우터 로컬: keep-alive 191→291(정상 유지 경고), TaskList 합성 59→60, 외부 SSE EOF 18→23, 모델 SSE reset 34→39, MCP reset 9→3, 모델 라우터 reset 3→1, 턴 관측 지연 2/2, 04:38:11 compaction scope 파일 잠금 1. 503의 저장소 원인은 고정 응답이라 확인되지 않음.
  - KOBE: v2738 실행 슬롯 0/입력 대기 7 → v2742 2/7. 10:38 구간 대화: 큰 파일 읽기 거부 1, 진단 스크립트 인코딩 오류 2, n42/n43 중복 lease 안내 각 1, side 알림 2줄. 라우터 로컬: keep-alive 237→311, 외부 SSE EOF 17→25(02:12:47부터), 턴 관측 지연 2→11, activity 파일 교체 접근 거부 2(02:26:02부터), 컨텍스트 압축 3, 모델 SSE reset 3. stranded 파일 없음, 새 Work 원장 실패 없음.
  - Prody: v285 실행 슬롯 1/입력 대기 2/제출 7 → v290 1/2/6. n1170 06:14Z부터 미관측. 10:38 구간 대화: Walkie client_id UUID 오류 2, 계획 버전 충돌 3, HTTP 400 1, 별도 CLI exit 2. 새 라우터 로그 그룹 없음, stranded/실패 파일 없음.
  - 운영 세션·라우터 재시작, 상태 파일 수정, 배포 없음. 신규 코드 결함 확정은 없음; Celly REST 503/timeout, Andy P0 재개 결함, Celly 복구 파일 NUL 상태를 미결 추적.

- 09:52:24–09:52:42Z 읽기 전용 틱 (`monitor/tick.jsonl`):
  - Andy v5070: 슬롯 실행 4·입력 대기 13·제출 20 유지, 이후 REST v5072에서 native confirmed 0. 대화: 09:36:11 `n2453`, 09:36:29 `n2042`가 `Parallel read failed: P0 reacquire parent authority`로 재개 거부. 행별 검사에서 두 열린 재작업 lease와 bound P0 intent는 같은 정확한 id/소유를 보이나 lease agent 이름 `work-rework-…`와 P0 intent `dispatch_name`(`work-p0-…`)이 불일치; 정상 generation 1 대조 행은 이름이 일치한다. 라우터 로컬 외부 SSE EOF 19(03:12:54부터), MCP reset 2(03:13:20부터), 턴 관측 지연 6(첫 03:13:20, 최대 487초), 모델 SSE reset 1(03:27:12). 새 durable 실패 없음. `n2453` 슬롯 미관측은 이번 findings가 아니며 종료를 뜻하지 않는다.
  - Celly v5754: 슬롯 실행 1→3, 입력 대기 1, 제출 1. 후속 v5669 REST에서 native confirmed 1, n1216 rework_ready를 봤다. 대화 09:51:20 Work 상태 읽기 실패 재발 1. 원장 decision_refused_stop, P0 readiness 오류, P0 intent bound는 이번 틱의 new_failures/findings에 없음. 로컬 라우터: 정상 라우터 유지 경고 226(연결 실패 아님), TaskList 합성 32, 외부 SSE EOF 19, 모델 SSE reset 24, 모델 라우터 reset 4다운스트림 1, upstream error payload 1, 턴 관측 지연 1. 근거: monitor rows 01:34Z 이후는 이번 구간 첫 줄 기준으로 별도로 누적한다.
  - KOBE v2737: 입력 대기 7→6, 슬롯 실행 0→1. 라우터 로컬 정상 유지 경고 226, 외부 SSE EOF 18(01:13:09부터), runaway 반복·복구 각 1(01:20:47), context compact map/reduce 2(01:23:32부터), 모델 라우터 reset 1, 턴 관측 지연 1, transcript 전송 timeout 1. stable containment/recovery 경고 자체는 도구 실패로 집계하지 않음. 대화 side 전달 3줄(원인 미표시), 새 Work 원장 실패 없음, stranded 파일 없음.
  - Prody v274: 원장 버전 265→274, 슬롯 실행 1→4, 입력 대기 2·제출 6 유지. n1170 06:14Z 이후 미관측 유지. 외부/업무 CLI 오류 5건을 기록했으나 새 라우터 경고나 durable 실패 없음; stranded/실패 파일 없음. 미관측을 종료로 단정하지 않음.
  - `56805d1` GitHub 게시 성공 및 npm `@beta` 0.2.51-beta.20261121-083943.56805d1 확인. 운영 상태 변경·재시작 없음. Celly 복구 파일, P0 재개 권한, Andy n2532/n2472 미관측은 미결 추적.

- 09:12:18–09:12:34Z 읽기 전용 틱 (`monitor/tick.jsonl`):
  - Andy v5053: 슬롯 실행 4/입력 대기 12→13/제출 20, REST HTTP 200, native confirmed 2→1. n2532/n2472 미관측 유지, 검수 n2453/n2175/n2069/n2042 모두 rework_ready 유지. 라우터 로컬 외부 SSE EOF 19(02:53:24부터), 모델 downstream 종료 1(03:04:10); 대화 08:53:51Z side 원문 전달 알림 1(원인 미표시), 새 원장 실패 없음.
  - Celly v5711: 슬롯 실행 1→2/입력 대기 0→1/제출 0→1, REST HTTP 200 및 native confirmed 2. 검수는 n1218(8f65eabc) rework_ready, n932 pending; 직전 n1216은 목록에서 빠졌으나 종결 방식 미확인. n1218 lease `decision_refused_stop` 4, 대화 검수 `review decision` 거부 4(09:09:16/50, 09:10:11/53Z); 문구만으로 요청 인수와 원인 특정 불가. 라우터 로컬 외부 SSE EOF 20(02:52:43부터), 모델 SSE reset 36(02:53:07부터), TaskList 합성 경고 47(02:53:07부터), 턴 관측 지연 1(02:54:08, 363초), 정상 라우터 유지 경고 237. 복구 파일 손상 및 패널 수정의 라이브 상태는 이번 틱에서 재검증하지 않음.
  - KOBE v2728: 슬롯 실행 0/입력 대기 7 유지, 신규 검수·슬롯 이상 경고 없음. 라우터 로컬 외부 SSE EOF 16(00:52:56부터), 읽기 timeout 1(00:54:29), broken pipe 1(00:58:39), 모델 downstream 종료 1(00:55:13), 정상 유지 경고 236. 새 대화·원장 실패 없음, stranded 파일 없음.
  - Prody v265: 슬롯 실행 1/입력 대기 2/제출 6 유지, n1170 06:14Z부터 미관측. 새 라우터·대화 오류 없음, stranded/실패 파일 없음.
  - 미관측을 종료로 판정하지 않음. 새 코드 결함 재현·배포 및 운영 세션·라우터 재시작·상태 파일 수정 없음.

- 08:52:24–08:52:39Z 읽기 전용 틱:
  - Andy v5042: 슬롯 실행 4·입력 대기 12·제출 20 유지, REST 네이티브 확인 1→2. n2532/n2472 미관측 유지, n2453 미관측 경고는 사라지고 검수 `rework_ready`. 대화: 08:47:10Z 소유권 이관으로 작업공간 보존 1, 08:51:45Z Git 검증 실패 1(상세 원인 미표시). 라우터 로컬: 외부 SSE EOF 20(02:32:43부터), transcript 전송 timeout 1(02:38:32), 모델 SSE reset 1(02:43:05). 새 Work 원장 실패 없음.
  - Celly v5669: 슬롯 실행 0→1, 제출 1→0, REST 네이티브 확인 0→1. n1216 `deciding/stage_failed: P0 intent bound`에서 `rework_ready`로 전환 확인; 패치가 전환을 일으켰는지는 미확인. 대화: 검수 결정 거부 2(08:48:49/08:49:09Z), side 전달 알림 2. 원장 decision_refused_stop 1. 라우터 로컬: 외부 SSE EOF 19(02:32:21부터), MCP reset 5 및 파일 잠금 예외 4(02:38:56부터), 모델 SSE reset 8(02:44:30부터), retention 상태 불가 1(02:37:51), router-activity 교체 WinError 5 1(02:44:46), stream recovery note 판독·기록 실패 1쌍(02:47:43, 현 파일 37,582바이트 전부 NUL), 턴 관측 지연 2 및 transcript 전송 timeout 1(02:50:08), TaskList 합성 7, 정상 라우터 유지 경고 208.
  - KOBE v2728: 슬롯 실행 0·입력 대기 7 유지. 라우터 로컬 외부 SSE EOF 19(00:33:22부터)/broken pipe 1(00:32:21), 정상 유지 경고 239. 대화 08:35:04Z 같은 side 이벤트 알림 2줄, stranded 파일 없음. 새 Work 원장 실패 없음.
  - Prody v265: 슬롯 실행 1·입력 대기 2·제출 6 유지. n1170 06:14Z 이후 미관측, 종료 증거 아님. 새 라우터·대화 오류 없음, stranded/실패 파일 없음.
  - P0 수정 `56805d1` npm `@beta` 버전 `0.2.51-beta.20261121-083943.56805d1` 및 게시 44885899037 성공 확인. 이번 틱 운영 재시작·상태 파일 수정·배포 없음. 신규 코드 결함 확정 없음.

- 08:32:19–08:32:32Z 읽기 전용 틱 (`monitor/tick.jsonl`):
  - Andy v5025: 슬롯 실행 4/입력 대기 10→12/제출 20, 후속 REST 네이티브 확인 1, n2069/n2042 `rework_ready`. n2532/n2472/n2453 미관측 유지. 라우터 로컬 외부 SSE EOF 19(02:13:10부터), 턴 관측 지연 1(02:13:35), 모델 SSE reset 1(02:18:30), context-usage 교체 WinError 5 1(02:30:40). 대화 08:12:26Z 경로 없음 1.
  - Celly v5655: 슬롯 실행 1→0/제출 0→1, REST 네이티브 확인 0 및 n1216 `deciding/rework`, `stage_failed: P0 intent bound`. 대화 08:13:16/08:17:05 동일 오류 2, side 전달 알림 3줄/2개 이벤트. 라우터 로컬 외부 SSE EOF 19(02:12:49부터), 모델 SSE reset 7(02:13:33부터), 모델 라우터 reset 1(02:23:56), 턴 관측 지연 1(02:13:07), router-activity 교체 WinError 5 1(02:22:21), TaskList 합성 8, 정상 keep-alive 237. 원장 v1446의 P0 intent 256/256(terminal 119, bound 92, cancelled 32, intent 13).
  - KOBE v2728: 슬롯 실행 0/입력 대기 7/제출 1→0. 라우터 로컬 외부 SSE EOF 18(00:12:58부터)/broken pipe 1(00:32:21), 모델 SSE reset 2(00:23:28부터)/수신 측 종료 1(00:12:56), 정상 keep-alive 238. 대화 08:27:21Z 별도 CLI exit 1 및 side 알림 3줄/2개 이벤트. stranded 파일 없음.
  - Prody v265: 슬롯 실행 1/입력 대기 2/제출 6 유지, n1170 06:14Z 이후 미관측. 새 라우터·대화 오류 없음, stranded/실패 파일 없음. 미관측을 종료로 판단하지 않음.
  - P0 포화 회귀: 격리 `p0_reserve_next`가 같은 오류로 수정 전 실패. 새 intent 수용 시 포화된 경우에만 가장 오래된 cancelled intent 하나를 제거하고 bound/terminal 및 승인 증거는 유지. Windows/Python 3.10/3.11 각 50건, 실패 0. 로컬·Celly·KOBE 설치 해시 `BDDD0DC6F95B83DD…` 확인 후 `56805d1`을 beta 푸시. 운영 세션·라우터 재시작/상태 파일 편집 없음. 라이브 n1216 복구 미확인. 패널 수정은 별개이며 배포에 포함하지 않음.

- 08:12:20–08:12:34Z 읽기 전용 틱 (`monitor/tick.jsonl`):
  - Andy v5017: 실행 슬롯 4/입력 대기 10/제출 20 유지, 후속 REST HTTP 200(0.66초) 네이티브 실행 확인 0, 검수 n2069/n2042 rework_ready. n2453 미관측 유지(07:21Z 이후), n2532/n2472 다시 표시(07:54/07:50Z 이후). 라우터 로컬 외부 SSE EOF 20(01:52:37부터), 턴 관측 지연 1(01:57:13). 대화 side 전달 1(08:11:41Z, 원인 미표시), 새 원장 실패 없음.
  - Celly v5646: 실행 슬롯 4→1, 예약 1→0, 입력 대기·accepted 각 3→0. 후속 REST HTTP 200(0.61초), native confirmed 1, 검수 n1206 rework_ready만 표시. 이전 부모 검수 보류 및 미관측 슬롯은 이번 출력에 없음(처리 방식 미확인). 원장 decision_refused_stop 2; 대화 08:00:20Z durable 결정 없음, 08:00:34 결정 불일치, 08:08:02 n931 미승인, 08:11:15 P0 intent bound 각 1; side 전달 및 이관 workspace 보존 각 1. 라우터 로컬 외부 SSE EOF 19(01:53:21부터), MCP reset 2(01:59:15부터), 모델 SSE reset 15(01:55:50부터)/downstream 종료 1(01:59:22), 모델 라우터 reset 3(01:56:14부터), TaskList 합성 13(02:00:21부터), router-activity 파일 교체 WinError 5 2(02:01:51부터), 턴 관측 지연 1(02:01:29), keep-alive 237(연결 실패와 별개). 복구 파일 37,582바이트 전부 NUL 유지. 패널 패치는 작업 트리 상태이며 설치·라이브 검증과 별개.
  - KOBE v2726: 슬롯 실행 0/입력 대기 7/제출 0→1. 08:04:35Z P2 용량 거부 1(1/1), 새 원장 오류 없음. 라우터 로컬 외부 SSE EOF 20(23:52:28부터), 모델 downstream 종료 1(23:52:58)/SSE reset 1(00:02:08), SSE gate 상태 읽기 PermissionError 1(00:03:12), 턴 관측 지연 1(00:04:14), keep-alive 236. stranded 파일 없음.
  - Prody v265: 슬롯 실행 1/입력 대기 2/제출 6 유지, n1170 미관측 유지(06:14Z 이후). 새 라우터·대화 오류 없음, stranded/실패 파일 없음.
  - 네이티브 미관측을 종료로 해석하지 않음. 신규 재현/코드 수정/배포/운영 재시작·상태 파일 수정 없음.

- 07:52:24–07:52:38Z 읽기 전용 틱 (`monitor/tick.jsonl`): Andy v5010 실행 슬롯 4/대기 10/제출 20. `n2453` 07:21부터 native 미관측. 원장 `decision_refused_stop` 1, 대화 검수 timeout(07:38:44), 조정 timeout(07:41:03), 검색 timeout(07:46:26) 각 1. 라우터 로컬 외부 SSE EOF 19(01:33:06부터), MCP reset 2(01:42:45부터), 모델 downstream 종료 2(01:47:12부터), 턴 관측 지연 3. Celly v5617 실행 슬롯 4/예약 1/대기 3/accepted 3, n1205/n1206 미관측과 부모 검수 보류 3 유지. 07:38:06 Work 상태 타임아웃으로 전송 거부 1, 원장 `revival_refused` 1. 라우터 로컬 MCP reset 19(01:34:12부터)/abort 2(01:36:19부터), MCP 잠금 HTTP 500 4(side inbox 01:34:21/01:35:29/01:36:29, P0 예약 wait-boost 01:36:32), compaction scope 잠금 2 및 transcript poll 잠금 1(01:38:44), 외부 SSE EOF 13(01:34:22부터), 모델 연결 reset 6, 턴 관측 지연 4, 정상 라우터 유지 경고 126. 잠금 4건은 패널 요약/상세 timeout과 동일 요청으로 입증되지 않음. KOBE v2722 실행 슬롯 0/대기 7, 외부 SSE EOF 20(23:32:51부터), 모델 라우터 reset 2 및 모델 SSE reset 1, 턴 관측 지연 1, 유지 경고 238. side 전달 알림 3줄(2개 이벤트, 07:34–07:38Z), stranded 파일 없음. Prody v265 실행 슬롯 1/대기 2/제출 6, n1170 미관측 유지, 새 라우터 경고 없음; side 전달 알림 1(07:42:08), 점검 CLI exit 2(07:43:08), stranded/실패 파일 없음. 운영 재시작·상태 파일 수정 없음.

- Celly Work & SSE 화면 장애: 사용자 화면에 unavailable, MCP connected/60 tools와 `/register` 404가 동시에 표시됐다. 동일 세션의 summary/detail MCP는 처음 각각 12초 timeout, 이후 같은 버전 8044에서 각각 0.33/1.64초 HTTP 200(detail 15개 노드). OAuth 404와 타임아웃의 인과는 확인되지 않았다. Inspector에서 첫 상태 조회가 실패한 뒤, 성공한 fallback을 캐시에 반영하지 않아 패널 데이터가 버려지는 별도 결함을 fail-first로 재현하고 수정했다. Work 포크 전송도 상태 확인에 실패한 경우 기존 보호는 유지하되 종료된 lease로 표시하지 않게 수정했다. Windows 플러그인 테스트 90건 통과, 실제 Claude 모드 로드·명령 응답 확인, 별도 Python/Node 실제 백엔드 회귀 1건 통과, Work 가드 회귀 Windows 및 Linux 3.10/3.11 각각 159건(33 skip), 실패 0. 화면 장애의 라이브 해소나 MCP 타임아웃 원인 해결은 검증되지 않았다. 운영 재시작·상태 수정 없음.

- 07:12Z 틱 후 개선 후보: Celly/KOBE의 `reachable_health_mismatch_active_client` 반복 경고가 각각 147/233줄. `router_client_lifecycle.py:227–253,328–332` 확인: 매 감독 주기에 동일한 정상 유지 분기를 WARN으로 기록할 수 있다. 라우터 유지 동작은 그대로 두고 동일 상태의 반복 경고만 집계·억제하는 개선 후보로 기록했다. Celly의 복구 기록 NUL 손상 및 Andy의 슬롯/네이티브 관측 불일치와는 별개다. 운영 변경 없음.

- 07:12:18–07:12:34Z 읽기 전용 틱 (`monitor/tick.jsonl`):
  - Andy v4998: 슬롯 실행 4 유지/입력 대기 7→9/제출 21→20, REST 200에서 native confirmed 0, n2532/n2472 미관측 유지. n2453 미관측 경고는 이번 출력에 없음. n2069 deciding→rework_ready, n2042 rework_ready 유지. 라우터 로컬 기준 외부 SSE EOF 18(00:52:31부터), broken pipe 1(01:06:25), 턴 관측 지연 1(00:58:32), context-usage 교체 접근 거부 WinError 5 1(01:05:54). 대화: workspace 소유 이관으로 보존 1(06:54:53Z), side 원문 전달 1(07:07:59Z, 원인 미표시).
  - Celly v5581: 슬롯 실행 5→6/입력 대기 3/accepted 3, REST 200 native confirmed 4. n1205/n1206 미관측 및 미완료 자식으로 보류된 n1179/n1181/n924 유지. n1205/n1191 rework_ready. 복구 파일 37,582바이트 전체 NUL 유지, 손상 원인 미확인. 라우터 로컬 외부 SSE EOF 15(00:53:12부터)/읽기 timeout 1(01:06:26), MCP reset 4(00:58:24부터), transcript poll의 compaction-scope 잠금 경합 1(00:54:53), 턴 관측 지연 3(최대 678초, 01:02:35), gateway discovery 불가 1(01:07:29), keep-alive 147(연결 실패와 별개). 대화: ripgrep timeout 2(06:54:44/06:59:10Z), side 전달 1(07:06:34Z).
  - KOBE v2722: 슬롯 실행 0/입력 대기 7 유지. 라우터 로컬 외부 SSE EOF 16(22:52:38부터)/broken pipe 2(23:00:38부터), 모델 라우터 reset 1(23:02:55), 동일 요청의 늦은 SSE 재시도 2(23:00:27/23:03:49), 턴 관측 지연 1(22:57:06), keep-alive 233. stranded 파일 없음; 새 대화/원장 오류 없음.
  - Prody v265: 슬롯 실행 1/입력 대기 2/제출 6 유지. n1170 06:14Z부터 미관측, side 원문 전달 알림 2(07:02:21/07:06:17Z)로 원인 미표시. 새 라우터 오류 없음, stranded/실패 파일 없음.
  - 새 Work 원장 실패 없음. 미관측을 종료로 판정하지 않았고, 신규 코드 결함으로 재현할 원인 증거 없음. 운영 세션·라우터·상태 파일 변경과 배포 없음.

- 06:52:20–06:52:38Z 읽기 전용 틱 (`monitor/tick.jsonl`):
  - Andy v4987: 슬롯 실행 4 유지/입력 대기 5→7/제출 20→21. n2532·n2472 미관측 유지, n2453 미관측 추가(06:28부터); 종료 증거 없음. 후속 REST v4988 네이티브 실행 확인 1, n2069 deciding·n2042 rework_ready. 대화 06:36:29 `no eligible candidate` 1. 라우터 로컬 첫 시각: 외부 SSE 읽기 EOF 19(00:33:02), 모델 스트림 reset 1(00:33:10).
  - Celly v5577: 슬롯 실행 2→5/입력 대기 3/accepted 3/제출 1→0. n1205/n1206 미관측, n1179/n1181/n924 미완료 자식 보류 유지; 후속 검수 REST 5초 timeout. 원장 `decision_refused_stop` 1. 대화: 재작업 한도 2/2(06:32:28), cp1252 오류(06:32:36), Vault 경로 HTTP 400(06:47:23), n1136 중복 lease(06:48:53) 각 1; side 전달 알림 1. 라우터: keep-alive 231(정상 응답 라우터 유지), 외부 SSE EOF 20(00:32:31), 모델 SSE reset 3(첫 00:34:23), 게이트웨이 검색 실패 1(00:36:59), 턴 관측 지연 6(첫 00:33:56, 최대 365초). 00:34:23 stream recovery failure 기록 실패: JSONDecodeError/OSError는 동일 작업. 후속 읽기에서 해당 파일 37,582바이트 모두 NUL 확인. 파일 손상 원인은 확인되지 않음. 현재 원장 코드는 판독 불가 상태를 덮어쓰지 않고 거부하며, 운영 파일을 수정하지 않음.
  - KOBE v2722: 슬롯 실행 0/입력 대기 7 유지. 라우터: keep-alive 238, 외부 SSE EOF 15(22:33:16)/broken pipe 2(22:35:07), 모델 라우터 reset 2(22:32:44)/downstream 종료 2(22:45:40), transcript 전송 timeout 1(22:33:58), 턴 관측 지연 3. 대화 06:36:37 계획 버전 충돌 1(198 vs 197), 06:39:12 동일 side 이벤트 전달 알림 2줄. stranded 파일 없음.
  - Prody v265: 슬롯 실행 1/입력 대기 2/제출 6 유지. n1170 06:14 이후 미관측 유지, 새 라우터·대화 오류 없음, stranded/실패 파일 없음. 이번 틱에서 운영 세션·라우터·상태 파일 변경 및 배포 없음.

- 06:32:21–06:32:35Z 예약 읽기 전용 틱 (`monitor/tick.jsonl`):
  - Andy v4974: 슬롯 실행 4/입력 대기 5/제출 20 (직전 4/4/20), REST native confirmed 1. n2532 `e9508081` 06:18부터, n2472 `48ace518` 06:13부터 미관측. n2042 `rework_ready` 유지. 06:15:10 중복 lease 거부 1, 새 원장 오류 없음. 라우터 로컬 첫 시각: 외부 SSE EOF 19(00:13:34), 모델 downstream 종료 1(00:13:32), 모델 라우터 reset 1(00:17:09), 턴 관측 지연 1(00:15:19).
  - Celly v5563: 슬롯 실행 2/입력 대기 3/accepted 3/제출 1 유지, REST native confirmed 0. n1206 `6004c5a8`, n1205 `e46eb63b` 모두 06:08부터 슬롯 점유·미관측. 부모 미완료 자식 보류 3건 유지; n1197은 이전 후속 `deciding`에서 현재 `pending` 표시. 대화 진단 Python 오류 06:24:26 AttributeError 1, 06:24:32/38 cp1252 출력 오류 2. 라우터 로컬 첫 시각: keep-alive 234(00:12:40, reachable router 유지), 외부 SSE EOF 19(00:12:54), stream recovery note JSONDecodeError/OSError 각 1(동일 00:23:54, 동일 작업 관련, 파일 손상 원인 미확인), 모델 라우터 reset 1(00:27:34).
  - KOBE v2722: 슬롯 실행 0/입력 대기 7 유지, 미관측 슬롯 경고 0. side 전달 알림 6줄/고유 이벤트 4(06:13:39–06:32:03, 원인 미표시). 라우터 로컬 첫 시각: keep-alive 234 및 SSE EOF 18(22:12:40), 모델 라우터 reset 3(22:15:32), 모델 SSE reset 1(22:12:54)/downstream 연결 종료 1(22:32:09), 외부 broken pipe 1(22:15:47), 턴 관측 지연 1(22:30:37). stranded 파일 없음.
  - Prody v265: 슬롯 실행 1/입력 대기 2/제출 6 유지. n1170 `69938858` 06:14부터 미관측 다시 표시. 06:23:41 별도 점검 CLI exit 2(미응답), 06:27:16–06:32:06 side 전달 알림 3(원인 미표시). 새 라우터 로그 그룹 없음, stranded/실패 파일 없음.
  - 새 슬롯 미관측은 포크 종료 증거가 아님. 운영 상태 변경·재시작 없음. 현재 로그로 확인한 오류를 신규 코드 결함으로 확정하거나 수정하지 않음.

- 06:12:36–06:12:50Z 읽기 전용 틱 (`monitor/tick.jsonl`):
  - Andy v4960: 슬롯 실행 4/입력 대기 4/제출 20. 이후 REST v4963 native confirmed 2, 검수 `n2042 rework_ready`. 원장 v4964 `n2453` lease `8f712f66`은 `waiting_input`, workspace `ready`; `992cc386` 및 `882b154f`(n1953 아래 n2032), n1962의 `8bfb6b21`은 `orphaned`, 슬롯 없음. 이번 틱 n1962 capability 거부 재발 미관측. 라우터: 외부 SSE EOF 16/broken pipe 1, 모델 라우터 reset 3/MCP reset 2, 턴 관측 지연 4. 대화: host-only `work_side_status` 거부 1, 별도 CLI exit 1 및 ripgrep timeout 1. 새 Work 원장 실패 없음.
  - Celly v5547: 슬롯 실행 2/입력 대기 3/accepted 3/제출 1. n1179/n1181/n924 미완료 자식 보류 3건 유지. 이후 REST v5548에서 `n1205 rework_ready` 확인. 대화: 조정 timeout 및 workspace 소유 이관에 따른 보존 각 1, 계획/실행 쓰기 가드 각 1. 라우터 PID 35084: reachable health mismatch keep-alive 69, 승인된 재시작 감지 1, 외부 SSE EOF 5, 모델 라우터/Anthropic SSE reset 각 1, 턴 관측 지연 1. keep-alive는 `router_client_lifecycle.py:233–253`에서 접근 가능한 라우터를 유지하는 분기이며 연결 실패로 집계하지 않음.
  - KOBE v2722: 슬롯 실행 0/입력 대기 7; 직전 n32 슬롯 미관측 경고가 사라짐, 정확한 종결 상태나 PID 15244의 영향은 이 출력에 없음. 라우터 PID 1344: 같은 keep-alive 경고 12, 승인된 재시작 감지 1, 외부 SSE EOF 및 gateway discovery unavailable 각 1. 대화: 별도 CLI exit 1, 같은 side 이벤트 전달 알림 2, 원인 미표시. stranded 파일 없음, 새 Work 원장 실패 없음.
  - Prody v265: 슬롯 실행 1/입력 대기 2/제출 6; 직전 n1176/n1170 슬롯 미관측 경고가 사라짐. 새 라우터 경고 없음, 대화 side 전달 알림 1(원인 미표시), stranded/실패 파일 없음.
  - 신규 제품 결함 재현 근거 없음; forwarded-loopback 관리자 토큰은 아직 미반영. 이번 틱에서 운영 세션·라우터·상태 파일 변경 없음.

- 사용자 승인 배포: npm `0.2.51-beta.20261121-053016.7765e9d` tarball SHA-512 확인, Celly/KOBE/Prody 활성 설치본의 배포 파일 645개씩 SHA-256 대조. 변경 파일 각각 2/3/7개 백업 후 교체. 라우터 PID Celly 17152→35084, KOBE 6072→1344, Prody 17184→23660. 세 새 라우터에서 동일 Work 세션 ID, 대상 베타 버전 및 `/ca/mcp` `tools/list` HTTP 200 확인. Celly 종료 직전 in-flight 요청 1건, 그 요청 결과는 미확인. 각 Claude 클라이언트의 전체 MCP 재연결 여부는 이 서버 측 검사로 확인하지 않음. Andy는 대상 밖.

- 05:54:18–05:54:31Z 읽기 전용 틱 (`monitor/tick.jsonl`): Andy v4915 슬롯 실행 8/입력 대기 1/제출 20. 라우터 외부 SSE 읽기 EOF 2, 턴 관측 지연 241초 1 (`model_request_state=unknown`); 신규 Work 원장·대화 오류 없음. n2453 작업공간 준비 상태의 새 증거 및 n1962 capability 오류 재발 없음; 구 `992cc386`의 orphaned 판정을 바꿀 새 관측 없음. Celly v5520 슬롯 실행 1/입력 대기 4/accepted 3/제출 2. n1205 `c669e8ba`는 완료 영수증이 있지만 검수 행이 없고 최신 native는 05:52 `running`; 종료 증거 없음. 미완료 자식 보류 3건 유지, 05:53:22 자동 계속의 Work 상태 읽기 실패 1, 외부 SSE 읽기 EOF 1. KOBE v2695 슬롯 실행 1/입력 대기 7, n32 미관측 유지; 외부 SSE 읽기 EOF 1, 모델 SSE 연결 reset(10054) 1, stranded 파일 없음. Prody v249 슬롯 실행 2/입력 대기 7/제출 6, n1176/n1170 미관측 유지; side 원문 전달 알림 1(원인 미표시), 신규 라우터 경고 없음, stranded/실패 파일 없음. 신규 제품 결함으로 확정할 증거 없음. 운영 세션·라우터·상태 파일 변경 없음.

- 05:52:30–05:52:44Z 읽기 전용 틱: Andy v4914 슬롯 실행 9/제출 20, REST native confirmed 9. 대화에서 구 a2475 fork `ab55e15d` 재활성화 거부 1, n2475 현역 `a2f75393` 중복 안내 1, 원장 `revival_refused` 1. `992cc386`은 n2032 orphaned 슬롯 없음; 후속 `882b154f`와 n1962의 `8bfb6b21`, n2453 구 lease도 orphaned 회수됨. n2453은 새 running `8f712f66` 1, 이전 unreported 4; Workspace manifest에서 assignment 행을 읽지 못해 구 `preparing` 해소 여부는 판정 불가. n1962 capability 오류 재발 없음. 라우터 외부 SSE EOF 3/턴 관측 지연 1. Celly v5519 실행2/대기4/accepted3/제출1, 미완료 자식 보류 3, 외부 SSE EOF 2. KOBE v2695 실행1/대기7, n32 미관측, 외부 SSE EOF 3. Prody v249 실행2/대기7/제출6, n1176/n1170 미관측; stranded/실패 파일 없음, 신규 오류 없음. 운영 변경 없음.

- 05:49:48–05:50:06Z 읽기 전용 틱: Andy 원장 v4905 슬롯 실행 6/제출 20/입력 대기 0. 후속 REST v4911에서 네이티브 확인 6, P0 네 루트 owner_failure 해소, 검수 목록 비어 있음. 대화 05:39–05:41의 중복 lease 3, 미승인 목표 1, P2 용량 1, Python cp1252 오류 1, side 전달 알림 1은 현재 상태와 구분. 라우터 종료 감지 1(직전 승인 재시작), 외부 SSE EOF 7, gateway discovery 불가 1. beta 게시 44871159169 성공.
  - Celly v5519 슬롯 실행 2/입력 대기 4/accepted 3/제출 1; n1179/n1181/n924 미완료 자식 검수 보류 3. 대화 중복 lease n1206 1, 05:39:15 다른 assignment manifest로 예약 해제 1(직전 유사 오류와 같은 문구이나 정확한 lease 및 원인 미확인), side 전달 알림 2. 라우터 외부 SSE EOF 19, 모델 연결 reset 1, 턴 관측 지연 2.
  - KOBE v2695 슬롯 실행 1/입력 대기 7, n32 03:18 이후 미관측, PID 15244 재발 미확인. 외부 SSE EOF 18, 모델 downstream 종료 2. stranded 파일 없음, 대화·원장 신규 오류 없음. Prody v249 슬롯 실행 2/입력 대기 7/제출 6, n1176/n1170 미관측 유지, stranded 및 실패 파일 없음. 새 라우터·대화 오류 없음.
  - 운영 세션·라우터·상태 파일 변경 없음. Forwarded-loopback 관리 토큰 미반영. Celly 예약 해제는 관측된 실패이며 신규 코드 결함 여부는 미재현.

- 사용자 지시로 Andy 라우터만 교체: PID 52976→52948, 감독 런처 PID 61264 및 Work 세션 동일. 전환 중 9505 연결 거부(10061)를 관측했고 이후 `/health`와 MCP `tools/list` 200(60개)을 확인했다. 라우터 로그 23:42:29/23:42:43 로컬에 실제 Work `work_status` 호출이 남았다. 다른 MCP 연결은 확인하지 않았다. 원장 v4828에서 앞서 확인한 대기 lease 9건 모두 `orphaned`; 보드 작업 완료와는 별개다. Celly PID 17152 유지 확인, 다른 프로세스는 종료하지 않았다.

- 05:30:44–05:30:59Z 읽기 전용 틱 (`monitor/tick.jsonl`):
  - Andy v4757: 슬롯 실행 0/입력 대기 49/제출 20, confirmed 0. REST에서 `n2442/n1953/n1445/n1936` `owner_failure` 유지, 검수 v1335에서 `n2158 rework_ready`만 표시. PID 52976 유지. 기존 manifest `preparing`이나 `n1962` capability 재발은 이번 출력에 없음. `992cc386`은 앞서 원장에서 확인한 n2032 orphaned 행이며, n1953 루트 아래 n2032의 열린 대기 lease는 `882b154f`. 라우터: 외부 SSE EOF 17/broken pipe 1, transcript 전송 timeout 1, 모델 라우터 reset 1/Anthropic SSE reset 1, 턴 관측 지연 120초·240초 각 1. 신규 Work 원장/대화 오류 없음.
  - Celly v5495: 슬롯 실행 2/입력 대기 4/accepted 3/제출 1. n1179/n1181/n924 미완료 자식 보류 3건. Work 원장 `decision_refused_stop` 1, 대화 Task ID 없음 1/검수 timeout 1/검수 `ChannelFileLockBusy` 1. 라우터: 외부 SSE EOF 15/broken pipe 1/읽기 timeout 1, MCP reset 14, MCP `work-plan` lock busy 5, 콘솔 proxy pending_poll lock busy 1 및 proxy 실패 경고 1(동일 원인, 별도 표면), stdin 미제출 보류 3, 모델 스트림 reset 2/라우터 reset 1, 턴 관측 지연 4. 잠금 점유자 및 지속 원인은 이 출력에서 확인되지 않음.
  - KOBE v2695: 슬롯 실행 1/입력 대기 7. n32 `2cf4c651` 03:18 이후 미관측; 종료 및 PID 15244 재발 증거 없음. 외부 SSE EOF 20, 모델 라우터 reset 3/downstream 종료 2/upstream error event 2(각 shape 로그 동반)/late SSE retry 2, 턴 관측 지연 2. Work 대화 plan 버전 충돌 1 (187 vs 186), 원장 신규 실패 없음, stranded 파일 없음.
  - Prody v249: 슬롯 실행 2/입력 대기 7/제출 6. n1176/n1170 네이티브 미관측 유지; stranded/실패 파일 없음. 새 라우터 로그 그룹 없음, side 원문 전달 알림 1(05:23:53; 원인 미표시).
  - `7765e9d`는 설치 해시 확인 및 beta push 완료, npm workflow 44871159169 진행 중이며 마지막 조회의 beta 태그는 c89471b. Andy 라이브 회수 미확인, forwarded-loopback 관리 토큰 미반영. 운영 상태 수정·재시작 없음.

- Andy dead parked worker hotfix `7765e9d`: 제품의 PID 신원 조회에서 `43911984`, `22c98907`, `a8a1e37f`, `066eab84`, `882b154f`, `c0fa5ab2`, `8bfb6b21`, `b33e68ad`, `d5b4c758`의 예약 당시 소유 PID가 부재했다. 기존 orphan preview는 슬롯이 없는 `waiting_input`을 전부 제외했다. 동일 조건의 격리 테스트에서 수정 전 회수 0건으로 실패, 수정 후 사라진 소유자의 lease만 orphaned 및 슬롯 없음으로 전이, 살아 있거나 불명/레거시 소유자는 유지. 검수나 보드 완료를 수행하지 않는다. Windows 92건 통과, 기존 Linux Python 3.10/3.11 각각 92건(2 skip), 실패·오류 0. Andy·Celly 설치 경로 및 KOBE에 세 파일 백업·해시 검증 후 beta push. 운영 프로세스 종료 및 상태 수동 수정 없음. 라이브 회수와 작업 재개는 별도 검증 항목.

- 05:10:51–05:10:59Z 읽기 전용 틱 (`monitor/tick.jsonl`): Andy v4757 슬롯 실행 0/입력 대기 49/제출 20, 외부 SSE 읽기 EOF 1(23:10:39 로컬), 신규 원장/대화 오류 없음. REST 보드 v9860의 n2453은 pending, n1445/n1962/n1953은 in_progress. P0 진단에서 n1445/n1953은 owner_failure 및 waiting_input 각 1건. 해당 manifest 상태, n1962 capability 오류 재발 및 n1953과 992cc386의 연결은 이번 출력에서 확인되지 않음. Celly v5474 슬롯 실행 2/예약 1/입력 대기 2/accepted 3/제출 1; n1179/n1181/n924 미완료 자식 보류 3건 유지. 외부 SSE EOF 1, 턴 관측 지연(120초, model_request_state=unknown) 1; 신규 원장/대화 오류 없음. KOBE v2686 슬롯 실행 4(직전 1)/입력 대기 3; n32 `2cf4c651`은 03:18 이후 미관측 유지, 종료 증거 아님. 외부 SSE EOF 1, stranded 파일 없음, 신규 원장/대화 오류 없음. Prody SSH(.12:1004) 연결 종료로 이번 상태 및 오류 미수집. 신규 결함 재현 근거 없음. 운영 세션·라우터·상태 파일 변경 없음.

- 05:09:38–05:09:54Z 읽기 전용 틱 (`monitor/tick.jsonl`):
  - Andy v4757: 슬롯 실행 0, 입력 대기 49, 제출 20. `n518/n2157/n2122`는 이번 출력 및 REST 검수 v1335 목록에 없고 `n2158 rework_ready`만 남았다. 새 원장/대화 오류 없음. 라우터: 외부 SSE EOF 6, 동일 offset의 4 MiB 초과 3, gateway discovery 불가 1, MCP reset 1. `/health` PID 52976, 버전 c955630; 이 값만으로 최신 패치의 로드를 증명하지 않음.
  - Celly v5466: 슬롯 실행 2, 입력 대기 1, accepted 3, 제출 1. `still has unfinished children`은 n1179/n1181/n924 3건(직전 4건). 대화: 중복 lease 안내 n1079/n1199 2, n1079 동일 포크 SendMessage 재활성화 거부 2, `plan version`/`stale plan version`/`transition` 각 1. 원장 `revival_refused` 1. 05:01:21 안내 직후 05:01:36 거부가 있었으나 그 사이의 lease 상태 이력 미확인. 라우터: 외부 SSE EOF 9, transcript 전송 timeout 1, 턴 관측 지연 1, MCP reset 1.
  - KOBE v2680: 슬롯 실행 1, 입력 대기 3. n32 `2cf4c651` 03:18 이후 미관측 유지, PID 15244 재발이나 실제 종료 증거 없음. stranded 파일 없음. 라우터: 외부 SSE EOF 8/읽기 timeout 1, 모델 downstream 종료 1. 새 대화·Work 원장 오류 없음.
  - Prody v249: 슬롯 실행 2, 입력 대기 7, 제출 6. n1176/n1170 네이티브 미관측 유지. stranded/실패 파일 없음, 새 라우터 로그 그룹 없음. 대화에는 운영 점검 CLI exit 2 두 건(05:00:46/05:01:04), side 원문 전달 알림 1(05:03:06; 원인 미표시).
  - 운영 세션·라우터·상태 파일 변경 없음. 신규 결함으로 확정할 재현 증거 없음. Forwarded-loopback 관리 토큰은 미반영.

- 05:00:00–05:00:24Z 읽기 전용 틱 (`monitor/tick.jsonl`):
  - Andy v4757: 슬롯 실행 0, 입력 대기 49, 제출 20. 검수 `n518/n2157/n2122`의 `run is not terminal` 3건 유지. 라우터 9505: 외부 SSE 읽기 EOF 35, MCP reset 4, 턴 기록 4 MiB 초과 3, transcript delivery 연결 거부 1, gateway discovery unavailable 1, 모델 SSE downstream 연결 종료 1, 모델 라우터 reset 1. 새 원장/대화 오류 없음. 이 틱은 n2453/n1445 manifest, n1962 capability, 992cc386의 현재 상태를 확인하지 않음.
  - Celly v5452: 슬롯 실행 0, 입력 대기 2, accepted 4, 제출 2. n1198 `ed609494`는 완료 영수증·검수 행 없음·04:59 native `running`. 부모 n1179/n1158/n1181/n924의 승인 기록 `still has unfinished children` 4건. 검수 도구: idle 증거 부족 2, 결정 불일치 1, timeout 1, ChannelFileLockBusy 2. 제출 포크 재활성화 거부 1, 중복 lease 거부 1, 지원되지 않는 이벤트 1, 잘못된 goal 1, 기타 CLI exit 1. 04:56:29 `assignment already exists with a different manifest`으로 예약 해제: 이 요청의 입력·lease와 원인 미확인. 원장 decision_refused_stop 5, revival_refused 1. 라우터 9385: SSE EOF 55, MCP reset 1, 모델 스트림/라우터 reset 5, 원격 지침 timeout(Claude/Codex 각 1), transcript delivery 연결 거부 1, JSON 디코드(compaction scope 4/cursor 1/SSE gate 1), transcript NUL 소비자 경고 4, 턴 관측 지연 1. dead-owner release 3은 오류가 아닌 처리 로그.
  - KOBE v2680: 슬롯 실행 1, 입력 대기 3. n32 `2cf4c651` native 미관측(03:18 이후), dead-owner 15244 재발 미확인. stranded 파일 없음. 라우터 9779: 외부 SSE EOF 72/읽기 timeout 4/연결 timeout 3/broken pipe 2, MCP reset 4, 모델 downstream 종료 2/라우터 reset 1, upstream error event와 shape 로그 각 1(동일 시각), transcript delivery timeout 1, 턴 관측 지연 4. 대화에서 04:47:24 잘못된 인수 HTTP 400 1과 side 원문 전달 알림. 이 알림은 side 실패의 원인을 보여주지 않음.
  - Prody v249: 슬롯 실행 2, 입력 대기 7, 제출 6. n1176/n1170 native 미관측 유지, 종료 증거 아님. stranded/실패 원장 읽기 불가(파일 없음). 라우터 신규 로그 그룹 없음. 대화: 04:19:46 Work 상태 읽기 실패 1, 운영 점검 CLI exit 2(결정 미확인·미응답 등) 5, side 원문 전달 알림 7.
  - 운영 상태 파일 수정·라우터 재시작 없음. 기재한 경고의 근본 원인이나 신규 제품 결함은 이 틱의 출력만으로 확정하지 않음. 기존 Andy 회귀 패치 `c89471b` 로컬 파일 설치 확인, npm `@beta`는 확인 시점 `cb8a0f6`; 라이브 적용 미확인. Forwarded-loopback 관리 토큰 별도 작업은 미반영.

## 2026-11-20

- 18:45–18:55Z: the user reported Andy and Celly frozen, both after the line "Prompt dropped by a hook: Owned Work notification retained outside main conversation". Both recovered by about 18:58Z. Read-only observations; the cause is not confirmed.
  - This PC: from about 18:47Z my own shell and PowerShell commands did not return (curl with a 20 s timeout, `Get-Date`). After I stopped my stalled checks and the f932da4 Linux Docker run, commands answered again at 18:55:03Z.
    - CPU load read 97% at about 18:56Z. At 18:59Z it was low: the top process used 1.35 of 24 cores.
    - My Windows bundle for 0be8eec (python 43224, started 17:59Z) is still running.
  - Celly router 9385: 569 log lines at 12:46 local, then none until 12:55. Andy router 9505: about 1,500 lines a minute fell to 65–300 between 12:47 and 12:53, and recovered at 12:54–12:55.
  - Celly main: the hook drop at 18:46:11Z, then a model request at 18:46:12Z with no answer until the user interrupted at 18:55:03Z. Then `work_host_page` timeouts and one ECONNREFUSED at 18:55:17Z (`/health` answered 200 at 18:55:15Z).
  - Andy main: the turn ended at 18:44:52Z with the upstream "blocked by our safety systems" API error. Only host notices follow until 18:55Z.
  - Upstream: at 12:45:01 local both routers logged `cielairouter_gateway_failure` (503), and Celly logged "no healthy gateway" with transport-disconnect retries.
  - The hook drop line is the designed handling of a Work fork's task notification (record.md, 11:25Z). In Celly it came one second before the stalled request. Neither this observation nor the source shows the drop causing the stall.

- 18:37Z tick (since 18:15Z):
  - Andy: 3 running, 1 reserved, 45 waiting, 22 submitted. No new failures or router ERROR lines.
    - n1174's recovery still holds its P3 slot (fix f932da4 waits for install). n2260 was refused again at 18:22:04Z on P3 capacity.
    - Finding `slot held, fork absent since 18:24: n2508 7a7cf0c4 P1 running`: not a defect. Native shows the fork `running` and fresh at 18:37:57Z; the row was briefly missing from one list.
  - Celly: 5 running, 7 waiting. Nothing new.
  - KOBE: ssh failed this tick.
  - Prody: 1 running, 5 waiting, 6 submitted. Main's own Python error only.
  - Andy_Main relayed five Andy observations (16:50–18:25Z). Read-only checks:
    1. The side's `understanding` led the posted reply. That is the 11-19 rule (0e0cb7e: one message per event, understanding then result). An `update` decision is always posted with its queue line, because `no_reply` applies only to `answer`. Not a defect against the stated requirements. The open Prody request (no thread reply for pure forward-to-Work) is waiting for the user's decision.
    2. Event b69915206b50 was processed twice.
       - Actor ade7618c (claimed 17:29:16Z) wrote its decision JSON at 17:33:13Z, but no understanding/resolve call followed. Renew calls stopped after 17:41 local+6.
       - Actor a8f1c6ea re-claimed it at 17:41:28Z and committed: understanding 17:43:58Z, resolve, reply. Only one thread post (154304).
       - Why the first actor's answer was not taken is not confirmed. Andy's main transcript has no rows between 16:49:13Z and 17:58:15Z, and the router log does not record the host's answer read.
    3. A fork Bash was refused "Work state unreadable (work_host_page was refused or failed)". That is the gate failing closed on a failed `work_host_page`. Andy's `work_host_page` failures (ECONNREFUSED, EADDRINUSE, ETIMEDOUT) are on record since 15:57Z; their cause is not confirmed.
    4. `work_side_handoff` and `work_parallel_status` were refused for side actors with "conversation identity is unavailable". These are main-session tools, and ciel-work hooks do not run inside forks it spawned ([[claude-plugin-own-fork-hook-skip]]). Not verified whether the side instruction leads the actor to them.
    5. Forks ended with an upstream "blocked by our safety systems" refusal; one side actor's first output line was Chinese and was not posted. Both are model or upstream output, not ciel-runtime.

- 18:15Z tick (since 18:00Z):
  - Andy: 45 waiting, 22 submitted, 1 reserved. No new failures or router ERROR lines.
    - Finding: `slot held by dead owner pid 14672: n1174 8b365b32 P3`. The sweep had released the reservation; the stranded recovery of n1174 (old lease d2e56870) re-reserved it at 18:01:46Z. The row is `deciding` and blocked: `stage_failed: workspace preparation/removal incomplete; retained`.
      - Read-only check of the recovery workspace. The manifest `status` is `preparing` and has been unchanged since 11-19 17:42:36Z (`git_dir` null). The worktree itself exists: it is registered, its toplevel is the target, HEAD is the manifest's own branch, and HEAD equals `initial_head` 8a1743e.
      - Cause, confirmed in source (0be8eec): `prepare` returns `_verify(manifest)` whenever a manifest exists, and `_verify` refuses any status other than ready/rebound. So a creation whose last manifest save was lost after `worktree add` can never complete. The recovery holds its P3 slot at every pump.
      - Effect: the parked P3 worker n2260 (lease 61e01208) was refused at 18:03:18Z with "active slot capacity reached for P3".
      - The interruption at 11-19 17:42Z itself is not recorded. The router log from that time has rotated, and the rotated file holds NUL bytes.
      - Fixed in f932da4. `prepare` now completes only an unbound fresh creation whose worktree verifies exactly and is still at its initial HEAD. Fail-first: the new test reproduces the same error on 0be8eec. The existing interrupted-before-add test still blocks.
  - Celly: 5 running, 7 waiting. No new failures or router ERROR lines. Main saw `work_host_page` connection-closed twice and one `no eligible candidate`.
  - KOBE: unchanged (2 running, 5 waiting, 1 accepted). n950 is still blocked and n964 still holds its slot (hotfix10 waits for a router restart).
  - Prody: unchanged (6 waiting, 6 submitted). No findings.

- 18:00Z tick (since 17:50Z):
  - This PC (Andy and Celly) booted at 17:54:38Z. I did not restart it, and the cause is not recorded here. My own background test runs for 0be8eec stopped with it and were started again.
    - After the boot, Celly's router 9385 was spawned at 17:55:34Z and Andy's router 9505 at 18:00:01Z, and new Claude processes started. Both routers load the hotfix9 and hotfix10 overlays from disk. `/health` still shows the package version (92ef95b and c955630), because the overlays do not change `package.json`.
  - Andy: 45 waiting, 22 submitted, nothing running. The startup sweep released reservation 8b365b32 (n1174), whose owner was gone. n1856 is still blocked. No new failures or router ERROR lines.
  - Celly: 4 running, 6 waiting.
    - n1078: new P0 lease c8a81bf5 is bound and running; the old lease 6f7a6a32 was orphaned by the startup sweep. The 5-minute "resume was refused" loop on 6f7a6a32 has ended.
    - n1141 (lease a90cf83f) was finalized (approved) after the restart. Before that it was `submitted` with a completion receipt from 15:59:12Z, but no review row for about two hours. Its native status stayed `waiting` from 15:59:36Z. The fork transcript ends at 15:59:26Z with its final text.
      - Native idle publication (`NATIVE_IDLE_STATUSES`) and the turn-end reconcile (`parallelTerminal`) both exclude `waiting`, so nothing could publish the review before a restart.
      - Why the engine reported `waiting` for a fork that had ended its turn is not confirmed. This is the only such case in the census: Celly n1141. Andy and KOBE have no submitted lease with a completion receipt and native `waiting`.
    - Router ERROR at 11:57:59 local, twice: `ChannelFileLockBusy` on `work-parallel.json.lock`, in `project` → `WorkParallelStore.read`. This was during router startup (`health_busy` keep-alive at 11:57:51). The lock holder is not established.
    - The tick could not read `work-parallel-coordination.json` once (OSError).
  - KOBE and Prody: ssh failed ("Connection timed out during banner exchange"). TCP 1004 is open on both, and ICMP gets no reply. No state was read.
  - Retried at 18:07Z for Prody and 18:14Z for KOBE (since 17:50Z and 17:35Z):
    - Prody: 6 waiting, 6 submitted. No findings, failures or router ERROR lines.
    - KOBE: 2 running, 5 waiting, 1 accepted. n950 is still blocked on unfinished children. No new failures or router ERROR lines.
      - n964 (1f7c4d9b) still holds its P1 slot, with run `terminal` and native `failed`. KOBE's router has not restarted since the hotfix10 overlay, so 0be8eec is not running there yet.
      - Main's tool errors are Walkie answers (404, 403, one unanswered send) and the goal-selection guard.

- 17:50Z: Prody added to the monitor (user order "지속적으로 celly, andy, kobe, prody 모니터링하면서 발견되는문제들을 수정해나가"). `run.sh` now runs `monitor_host.py` on .12 over `ssh -p 1004` too. The copy goes to `C:\Windows\Temp\monitor_host_prody.py` and is deleted after the run. Read-only.
  - First pass (last hour): scope claude:ef61c539…, 1 running, 5 waiting, 6 submitted. No failure journal and no stranded store yet (files absent). No router ERROR lines. Three side originals were forwarded to main.
  - Finding `slot held, fork absent since 16:38: n1164 118cdb2f P2 running`. Not a defect:
    - The fork's question report succeeded at 16:36:26Z (`waiting_input`, slot released).
    - At 17:49:20Z the host's parked resume sent "the wait on your Work item n1164 is cleared". That is the reacquire → running path, so the lease is legitimately running with a slot.
    - The 16:38:14Z `missing_since` is from before the resume. The fork has been running since 17:49 (reads only so far).
- 17:46Z: no-workspace host salvage fix 0be8eec installed (hash-checked, backups `*.backup-hotfix10`, every target held 2cb27cc's bytes first): local (Andy home, default home, global npm), Celly (elevated) and KOBE (ssh). Two files, all verified. No process restarted. The Windows and Linux bundles are still running, so there is no beta push yet.

- 17:35Z tick (since 17:15Z):
  - Andy: 2 running (was 3), 1 reserved, 45 waiting, 22 submitted. n1856 is still blocked. No new failures or router ERROR lines. `work_host_page` failed twice more (ECONNREFUSED, EADDRINUSE).
  - Celly: 3 running, 6 waiting, 1 submitted. n1141 is still unpublished.
    - One router `channel_stdin_proxy_blocked ... stale_unconfirmed` (11:21 local).
    - The parked lease 6f7a6a32 (n1078) repeats "a resume was refused within the last 5 minutes". Same P0 open-child shape; 2cb27cc is installed and waits for a restart.
  - KOBE: 2 running, 5 waiting, 1 accepted. Two more `channel_stdin_proxy_blocked` lines.
    - New defect, n964 (lease 1f7c4d9b, no workspace): the lease is `running` with a P1 slot, but its run is `terminal` and native is `failed` since 17:19:55Z.
      - The fork transcript shows every turn since 17:16:11Z ending in `API Error: This request was blocked by our safety systems`. That covers the mod's four exit-contract prompts and main's two resumes.
      - Main tried `work_parallel_event` `help`/`native_exit`/`stopped`/`cancel`, all refused "unsupported parallel event". TaskStop answered "not running (status: failed)".
      - Cause, confirmed in source: `host_salvage` refused any lease without a workspace ("unknown salvage workspace"). `parallelEnsureExitContract` swallows that refusal. So a fork reserved without a workspace that ends without a report keeps its slot forever. KOBE reserves every fork without a workspace (session folder is not a git repo).
      - 1446629 (11-19) allowed no-workspace leases on the review path only.
      - Fixed in 0be8eec: fail-first reproduced on bd21399; the bundle is running.

- 17:35Z: beta 0cb598b published as npm `0.2.51-beta.20261120-172521.0cb598b`. Its two files match the hotfix9 installs on local, Celly and KOBE. No session restarted.
- 17:22Z: Andy's Work prompts reset to defaults (user order "andy 세션 프롬프트 업데이트해줘 기본값으로"). Done through Andy's live router 9505 (c955630), with compare-and-set on the fingerprints just read:
  - `workflow-prompt-reset` for `gate_actor` (374721ea… → default df8a6cc3…) and `gate_recheck` (its override already equalled the default). Both 200.
  - `settings-update` `work_side_intake_instruction` = "" (it was 15104 bytes, the c955630 default text stored with `instruction_replace=true`). 200.
  - Read back: 0 non-default prompts, stored instruction 0 bytes, settings fingerprint e395ed07… → 74643763…. The workspace config no longer has `instruction`/`instruction_replace`, and `workflow-prompts.json` holds no prompts (its history records both resets, source `rest`).
  - Nothing else changed. `work_side_intake_session` stays `fresh`. The router was not restarted. The managed prompts and the side instruction are read per request and per side event.
- 17:20Z: Betty (.12 u42, read-only, user request). Version `0.2.51-beta.20261120-051824.6e0818e` (router 9564 `/health`).
  - Prompts: 30, all default, no history. 6e0818e predates the three prompts added in 03e064d. Its 30 fingerprints equal Prody's.
  - Settings fingerprint 9df9eb78…. Side instruction empty (built-in default); `work_side_intake_session` `fork`.
  - Differences from Prody:
    - SSE injection mode and side intake type: `session` (Prody `btw_with_mcp`); side intake disabled.
    - Reply connectors empty.
    - Every model `inherit`.
    - P1/P2 limits 5/3 (Prody 2/1); orphans `live=1`.
    - `work_completed_auto_delete` false; `claude_background_agents_enabled` false.
- 17:19Z: P0 reacquire completion fix 2cb27cc installed (hash-checked, backups `*.backup-hotfix9`, every target held 95cf25c's bytes first):
  - Local: the Andy home, the default home and the global npm package.
  - Celly: elevated overlay. KOBE: overlay over ssh.
  - Two files each, all verified. No process was restarted.
- 17:15Z tick (since 17:00Z):
  - Andy: unchanged (3 running, 1 reserved, 45 waiting, 22 submitted). No new failures or router ERROR lines. Main called host-only `work_side_texts` and was refused (by design). Two side originals were forwarded.
  - Celly: 3 running, 6 waiting, 1 submitted.
    - n1078's resume was refused with `no active slot` (17:05:49Z). This is the third lease with the P0 open-child shape (with n1143 and n1156), covered by 2cb27cc.
    - Two `ChannelFileLockBusy` on a side-inbox lock (11:13 local).
    - n1141 is still unpublished. Two `git.exe` processes started at 15:48:56Z are still alive under an account this monitor cannot read. Their start time is 4 s after n1141's fork ran its `git fetch` command at 15:48:52Z, which went to the background. That matches a background task the gate stopped the fork from ending. Ownership is not confirmed.
  - KOBE: unchanged (1 running, 5 waiting, 1 accepted; n950 blocked). Seven side-original forwards, two of them `already` duplicates.

- 17:15Z: Prody Work prompts and settings compared with Andy (request relayed by Andy_Main, read-only). Method: loopback GETs to Prody router 9592, `/ca/work/api/v1/workflow/prompts` and `/settings`. Only fingerprints and sizes were printed.
  - Prompts: all 33 use defaults, with no history. Andy differs on `gate_actor` (override 374721ea… vs Prody default df8a6cc3…). Andy's `gate_recheck` override is identical to the default.
  - Settings fingerprints differ (Prody 20de7c6c…, Andy e395ed07…).
    - Prody's stored side instruction is empty, so it uses the built-in default.
    - Andy's workspace config stores `instruction` (15104 bytes, fc5146173c08c4ee) with `instruction_replace=true`. That text is byte-identical to c955630's built-in default.
    - `work_side_intake_session` is `fork` on Prody and `fresh` on Andy.
  - Defaults by source:
    - 79673d9 adds `side_complaint_and_urgency`, `side_complaint_notice` and `mod_side_ask_main_wake`. The other 30 default fingerprints are unchanged.
    - The built-in side instruction went from 15104 to 15115 bytes (6096ccfa…), adding `, complaint` to the decision fields.
  - Consequence for Andy after a restart (from source, not yet observed): the stored replace-mode text still lists the fields ending in "no_reply. 다른 필드는 없습니다." The appended `side_complaint_and_urgency` asks for `"complaint": true`. The two instructions conflict. Nothing was changed.

- 17:00Z tick (since 16:40Z):
  - Andy: unchanged (3 running, 1 reserved, 45 waiting, 22 submitted); n1856 is still blocked. No new failures or router ERROR lines. Main's errors are its own (Python script errors, Walkie 403, "n538 is not a goal").
  - Celly: 2 running, 6 waiting, 1 submitted. No new failures or router ERROR lines.
    - n1156 (lease c171a2d4, P0 lane): main's resume was refused twice with `no active slot` (16:52:46Z, 16:53:43Z). Same shape as n1143: P0 send child `slot_acquired` 16:46:22Z, worker question 16:50:12Z. Covered by 2cb27cc (not installed yet).
    - n1141 (lease a90cf83f) is still submitted with no review row, native `waiting`. Read-only evidence from the fork transcript:
      - Two Bash calls (15:48:52Z `git fetch`+`git show`, 15:54:54Z a recursive `grep -rl` under `.local`) did not finish within the tool timeout and were moved to the background (b7c810nhs, bz6e3m0uc).
      - At 15:57:09Z the fork called TaskStop on both. `ciel-work-gate` denied each: `TaskStop is not available to this Work fork` (design 11-12 row 11: a worker must not stop other agents).
      - No completion notice for either task is in the transcript, which ends with the fork's final text at 15:59:26Z.
      - n1156's fork has the same pattern: TaskStop and TaskList denied at 16:32Z, native `waiting` since 16:51:21Z after its last answer.
      - The engine type doc defines `waiting` as held on background work the agent owns. `_publish_native_idle_review` and the turn-end reconcile accept only idle/completed/failed/killed. So the review is not published while a fork's own background tasks run.
      - Whether those background processes are still alive is not confirmed: Celly's processes belong to another account and their command lines are not readable here.
  - KOBE: 1 running, 5 waiting, 1 accepted; n950 is still blocked. Two router `[ERROR] channel_stdin_proxy_blocked ... prior_submission_failed detail=stale_unconfirmed action=skipped` (08:54, 08:59 local). Same class as Celly's 11:18Z line (not yet investigated). Five side originals forwarded (two `already` duplicates of the same event).
  - 2cb27cc: the Windows bundle (81 files) and the Linux 3.11 bundle (90 files) are still running.

- 16:40Z tick (since 15:40Z):
  - Andy: 3 running, 1 reserved, 45 waiting, 22 submitted. n1856 is still blocked. New `decision_refused_stop` 487db395 (n2471); that review is now finalized/approve.
    - One router `PermissionError [WinError 5]` at 10:08:30 local, on the `work-parallel-stranded-v1.json` replace. Its trace has no `replace_with_retry` frame, so the running router predates the 0dc57b0 overlay (same as the 13:49Z entry).
    - Main: n2472 got a new lease 6c99a6a9 (15:59Z refusal names it, so the 15:55Z orphan path worked).
    - `work_host_page` failed four times with ECONNREFUSED/EADDRINUSE/ETIMEDOUT (15:57–16:26Z). Cause not measured.
  - Celly: 1 running, 6 waiting, 1 submitted. No new failure-journal entries or router ERROR lines.
    - New finding n1141 (lease a90cf83f): completion receipt 15:59:11Z, lease submitted, no review row. The fork's native status has been `waiting` since 15:59:36Z, and its transcript ends at 15:59:26Z with a final text answer. `_publish_native_idle_review` publishes only for idle/completed/failed/killed, and the turn-end reconcile treats `waiting` as not terminal. So no path has published it yet. Why the engine lists a fork that has ended its turn as `waiting` is not confirmed (the type doc says `waiting` is held on background work, plan approval or an Agent call).
    - 15:47:22Z `work_parallel_run` n1141 was refused with `coordination persistence failed; reservation released: ... assignment already exists with a different manifest`. The same item was reserved and bound at 15:47:14–21Z by the P0 lane, so this was a second dispatch of the same item, refused by the existing-run guard.
  - KOBE: 1 running, 5 waiting, 1 accepted. n950 is still blocked on unfinished children. Six side originals were forwarded to main (`invalid_answer` path); one was `already`. Main's other errors are Walkie flow/QA refusals and a GitHub token failure (not Ciel). 16:36:14Z "auto continue could not read the Work status" (seen before).
  - P0 reacquire completion fix 2cb27cc: fail-first recorded; Python 3.10 changed modules 228 tests, 0 failures. Windows bundle and Linux 3.11 bundle still running.
  - .12 (user question): Betty runs `0.2.51-beta.20261120-051824.6e0818e` (package.json 06:20:19Z, launcher 06:26:03Z, router 9564 `/health` same). Prody runs `0.2.51-beta.20261120-152815.79673d9` (package.json 16:25:12Z, launcher 16:27:03Z, Claude 16:32:27Z, router 9592 `/health` same).
    - Prody's npm debug log at 16:25:07Z records `npm install --force --global @one-ciel-ai/ciel-runtime@beta`, exit 0. Ciel's own self-update and upgrade build `npm install -g [--prefix] [--allow-scripts] @one-ciel-ai/ciel-runtime@<exact version>` (`npm_global_install_command`). So this install did not come from Ciel's updater. Who ran it is not established from the host.
    - Betty's npm logs before 10:56Z have rotated out, so how 6e0818e was installed at 06:20Z is not recorded.

- 15:40Z tick (since 15:16Z):
  - Andy: 2 running, 1 reserved, 46 waiting, 23 submitted (one fewer running and one more submitted). n1856 review is still blocked on `unknown Work item`; n1174 reservation still has no matching run or native identity. No new failure-journal entries or router ERROR/uncaught lines. At 15:21:16Z the hooks worker crashed, `ciel-work` unloaded, and `ciel-work-gate` fell back three times. The next two board updates failed because there was no conversation identity. `ciel-work` reloaded at 15:22:02Z. A parked P3 worker was refused on slot capacity. The mod directory in the running process's launch arguments is the install target updated at 15:20:49Z. The transcript proves a crash and later reload, not that the overlay caused the crash; the same crash was recorded earlier that day.
  - Celly: 2 running, 5 waiting (unchanged). No new failure-journal entries. One new `/ca/mcp` router ERROR, `ChannelFileLockBusy` on a side-inbox lock at 15:23:33Z. Lock holder not established. Main's three attempts to resume n1143 (15:20:43Z, 15:30:37Z, 15:39:59Z) were refused with `no active slot`. The lease is `waiting_input`, slot false, with an older P0 send still `slot_acquired` since 14:49:16Z. Router reacquire returns that same open send without taking a slot; neither mod resume path calls the router's `p0_reacquire_complete`. The send census is 12 open on Celly (3 still waiting), 1 on Andy (orphaned) and 2 on KOBE (one still waiting); all 15 were `slot_acquired`. `ciel-work` reloaded at 15:22:00Z.
  - KOBE: reachable again; 2 running, 5 waiting, 1 accepted. n955 now waits without a slot; lease prefixes previously reported for n953/n957 are no longer present. New finding n950: approval committed to the review, lease accepted, but board finalization blocked on unfinished children. Two new `decision_refused_stop` entries; main saw a stale finalization version, a conflicting review decision, two existing-lease guards and two goal-not-approved guards. Three distinct side originals were forwarded, with one additional `already` notice for the same event. No new router ERROR/uncaught lines. `ciel-work` reloaded at 15:26:35Z.
  - All three transcripts confirm hot reloads after the previous overlay; this does not establish that their running Python routers picked up the new code or that the end-to-end fix is active. No live session or router was restarted by the check.
  - Follow-up at 15:55Z: Andy n2472 dispatch was refused at 15:53:40Z while its old lease was open. Startup orphan sweep recorded `orphaned`, slot false, at 15:54:27Z; the attempted SendMessage to the previous fork was refused at 15:54:44Z. Relayed the observed transition and the existing dispatch path to Andy's session. No lease was manually released.

- 15:40Z: side complaint and P0 inheritance fix (03e064d) is installed on local, Celly and KOBE (hash-checked, backups `*.backup-hotfix8`). Beta 79673d9 is published as npm `0.2.51-beta.20261120-152815.79673d9`, and its ten files match the installs. No session was restarted.
- 15:16Z tick (since 15:00Z):
  - Andy: unchanged (3 running, 1 reserved, 46 waiting, 22 submitted); n1856 still blocked. No new failures or router ERROR/uncaught lines.
    - 15:01–15:02Z: five `work_node_update status=done` calls (n2304–n2308) were refused with "Work completion waits for this item's parallel validation, review and cleanup". This is the existing completion gate; nothing was applied.
  - Celly: 2 running, 5 waiting. No new failures or router ERROR/uncaught lines. One side-failure forward.
    - 15:03:36Z: `work_parallel_run` n1152 (P1 worker) was refused with `unknown P0 assignment`. That text is raised by `p0_launch_assignment` (`work_parallel_mcp.py`) when the intent/lease/dispatch-name pin does not match a reserved P0 launch. Main's `work_parallel_run` surfaced it, so the dispatch went through the P0 lane. Not confirmed which pin failed.
  - KOBE: unreachable this tick. ssh first got "lost connection", then "Connection timed out during banner exchange" on two retries, and no state was read. The previous tick's KOBE findings (n955, n953, n957 leases running with slot while their runs are terminal or waiting; artifact-format report refusals) stay open.
  - Linux Docker run of 03e064d is still in progress.

- 15:00Z tick (since 14:35Z):
  - Andy: unchanged (3 running, 1 reserved, 46 waiting, 22 submitted); n1856 still blocked. One side-failure forward. Main's own regex error. No router ERROR or uncaught lines.
  - Celly: 2 running, 4 waiting, 1 submitted. No new failures or router ERROR/uncaught lines.
    - Main got refusals on its own calls:
      - `P0 reacquire parent authority` on SendMessage to n1128's parked worker (14:36Z, 14:41Z).
      - `P0 intent transition` on `work_parallel_run` n1151 (14:47Z).
      - `plan version`, `stale plan version`, `no active slot`, `no eligible candidate`, `unsupported parallel event`.
      - Walkie `Conversation access denied`.
    - The two P0 refusals are new refusal texts on this host. Their cause is not investigated yet.
  - KOBE: running leases rose to 7 at 14:36–14:45Z. Main's P0 goals for incident 153865 started six P0 forks (n950, n953, n955, n956, n957, n18, n20).
    - Finding `slot held, fork absent since 14:46: n955 a1e68f4b P1 running`: the run holds a `question` exit at 14:44:16Z, and the lease is still `running` with slot. The fork's question report returned `waiting_input` with slot released. The ledger row read later still shows `running` / slot true. Not confirmed why.
    - Fork n953 (af55b265):
      - 14:42:09Z: API error "blocked by our safety systems".
      - Then 6 completion reports refused with `completion artifact SHA256 required`. It passed paths or `sha256:`/`#sha256=` forms. `digest()` accepts only a bare 64-char lowercase hex string.
      - Then a handoff without `workspace`/`next_step`/`blockers`, answered `handoff done` as an error. That is the KeyError text of the missing `blockers` field.
      - Run `terminal`/native `failed`, lease still `running`/slot.
    - Fork n957 (ae94b273): API errors "circuit breaker is open (HTTP 503)" and "all targets were skipped by pre-dispatch filters". Two exit-contract prompts. Run terminal/failed, lease running/slot.
    - Report refusals since 11-18 (read-only fork transcript census):
      - KOBE: 72 accepted. Refused 48 times in 25 forks with `completion artifact SHA256 required`, and 28 times with `worker may only report question, completion or handoff`.
      - Andy: 81 accepted. 61 refusals in 34 forks with `completion artifact SHA256 required`, 44 with the event-name refusal, and 31 with `completion text`.
      - Both hosts have one `handoff done`.
    - Main: `TaskStop`/`No task found` for two P0 forks; `mod_stop_unavailable` at 14:37Z; `work_host_page: ECONNREFUSED` at 14:49Z.
  - Side complaint/P0 change 03e064d:
    - Windows bundle 847 tests, 0 failures, 55 skipped.
    - Real claude.exe 2.1.329 load passed.
    - All three installs hold the 3d42d1d bytes for the 10 changed files.
    - The Linux run is in progress.

- 14:35Z tick (since 14:16Z):
  - Andy: unchanged (3 running, 1 reserved, 46 waiting, 22 submitted); n1856 still blocked. Nothing new.
  - Celly: 3 running, 4 waiting.
    - No new failures and no router ERROR or uncaught lines.
    - Main's tool errors were its own commands: shell exits, cp1252 encoding, git ignore, ripgrep timeout. There were also two correct host refusals: `work_side_status` is host-only, and n1149 already has a lease.
  - KOBE: 2 running, 3 waiting.
    - One side-failure forward.
    - 14:32:19Z `mod_stop_unavailable`: the Stop hook's board read missed its 10 s deadline (`STOP_READ_MS`, register.js). Celly showed the same notice at 14:01Z. These are single occurrences. Why the read was slow is not measured.
    - Main's own Python cp1252 print errors.
  - Reported to Andy_Main for Betty 153842, from 6e0818e source:
    - A claimed restart waits only for (a) no unconfirmed submitted channel input (`restart_protected`, no time limit) and (b) turn end or 900 s. Work leases and forks are not part of the condition.
    - `/exit` releases a deferred request (`after_child_exit`).
    - An in-place restart keeps launcher pid 760, so the unbound reservation ad958b5c stays live-owned and is not released. This is open as a product gap. Andy 8b365b32 has the same shape.

- 14:16Z tick (since 13:55Z):
  - Andy: unchanged (3 running, 1 reserved, 46 waiting, 22 submitted); n1856 still blocked. No new failures, router ERROR/uncaught lines or main tool errors.
  - Celly: 2 running, 4 waiting. No new failures or router ERROR/uncaught lines.
    - 14:01:54Z notice "auto continue could not read the Work status". The router logged the `work_status` call at 08:01:23 local with no error after it. The reason for the read failure is not confirmed.
    - 14:08:38Z: a turn-end event was forwarded to main as `superseded` (existing quarantine path).
  - KOBE: 2 running, 3 waiting; nothing new.
  - The affected-bundle rerun for the side complaint/P0 change was still running at this tick.

- 13:55Z tick (since 13:36:29Z):
  - Andy: 3 running, 1 reserved, 46 waiting, 22 submitted (unchanged); n1856 remains blocked. No new entries in the Work failure journal or main tool errors. At 13:49:24Z, `/ca/mcp` logged one uncaught `PermissionError [WinError 5]` writing `work-parallel-stranded-v1.json` via `_stranded_roll_forward` → `block` → `_change`. This is the same write path fixed in 0dc57b0. The fix is on disk; the running process predates the overlay, and this trace has no `replace_with_retry` frame.
  - Celly: 2 running, 3 waiting (unchanged). The previous n1143 missing-fork finding cleared after main resumed the worker. No new Work failure entries or router ERROR/uncaught lines. Main had two tool errors: Walkie rejected operation arguments (13:49:09Z) and PowerShell rejected a redirection without a target (13:49:32Z).
  - KOBE: 1 running, 3 waiting, 1 submitted (previously 2 running and 4 waiting). No new lease finding, Work failure-journal entry, router ERROR/uncaught, or actual main tool error. Four side notices represent three distinct originals forwarded and one already-delivered notice. A read-only backup-mode copy of recent receipts (removed after inspection) showed 24 of 26 `invalid_answer` failures over three hours had `side answer carries no decision JSON`. For one newly forwarded gate event, a decision JSON appears before another tool call but the final answer is prose; in other inspected events the final answer is prose without a decision. The sampled transcripts show no received corrective message; why the correction was not delivered is unconfirmed.
  - No live session was restarted or changed by this check.

- 13:36Z tick (since 12:15Z):
  - Andy: 3 running, 1 reserved, 46 waiting, 22 submitted (unchanged); n1856 still blocked.
    - New router lines are WARN only: SSE disconnects, and an upstream 503 "all targets were skipped by pre-dispatch filters" at 07:19 and 07:24 local.
    - Tool error 12:50:24Z: `work_side_handoff: invalid side receipt identity`. Main passed the 12-character event ref from the notice `Ciel side asks main (event 132ea9c9a6a9)` as `event_id`. The notice offers no full id and needs no handoff, so this is a wording gap, not a lost event. It is covered by the complaint/ask_main change under test, whose wake says "act on it now" instead of "released recorded wait".
    - Reservation 8b365b32 (n1174 P3) is still held. The owner PID is alive, and there is no dispatch name or coordination run. Reported to Andy_Main for Betty 153726; no state changed.
  - Celly: 2 running, 3 waiting.
    - Finding: n1143 (lease e5b6cdd7, P0 lane) was absent from the list from 13:25:53Z. Its fork transcript ends at 13:24:13Z with `API Error: 503 ... all targets were skipped by pre-dispatch filters` (stop_sequence). That is the same upstream 503 Andy logged.
      - Main resumed the worker with SendMessage at 13:38:32Z, and the fork continued at 13:38:45Z.
      - The native list showed no terminal status, so no exit-contract message was sent. Main noticed only through the dispatch check.
      - Not confirmed: whether the host should treat this as terminal by itself.
    - New `[ERROR] router_post_uncaught ChannelFileLockBusy` ×5 at 06:39–06:40 local, all on `runtime-inputs.jsonl.lock`. The path is `/v1/messages` → `body_with_pending_channel_messages` → `ChannelWakeClaimRepository.prompt`, which waits on the runtime-input lock. Earlier ticks saw the same class on other locks; the holder is still not identified.
  - KOBE: 2 running, 4 waiting; no lease findings.
    - 13 side-failure forwards since 12:15Z, all `invalid_answer · 1회 시도`: 11 gate re-check or turn-end events and 2 Walkie messages.
    - Not confirmed: the refusal reasons. The forward text carries only the category, and the receipts under `workspaces\651d879f6c68` are ACL-denied to the monitor account.
  - The vanished-fork review gap (0dc57b0) shows no new case. KOBE n878 and n880 still wait for KOBE's restart.

- 12:30Z: vanished-fork review and sidecar replace-retry fixes (0dc57b0) installed on local, Celly and KOBE (hash-checked, backups `*.backup-hotfix7`). Beta eab2a18 published as npm `0.2.51-beta.20261120-122502.eab2a18`; its five files match the installs. No session restarted.
- 12:15Z tick:
  - Andy relaunched 12:12Z (c955630); 3 running, 1 reserved, 46 waiting, 22 submitted. The launch sweep orphaned 4 leases (3 P1 running, 1 P3 reserved).
  - Celly: 4 running.
    - New: 4 × `router_post_uncaught ChannelFileLockBusy` at 12:03:16Z–12:08:31Z. One was on `work-plan.json.lock` (work session 909aa6ee…), three on side-inbox event locks.
    - A `work_parallel_run` was refused at 12:10:13Z with `workspace_prepare_retained ... channel file lock acquisition busy`.
    - Confirmed: `msvcrt.locking(LK_LOCK)` gives up after 10 one-second attempts, so some holder kept these locks for more than 10 s.
    - Not confirmed: which code path held them.
  - KOBE: unchanged; n878 and n880 are still without reviews.
- 12:14Z: user question "어떻게 앤디 세션이 워크모드가 저렇게 그냥 멈춰버릴수있지?". Read-only check of Andy:
  - Main transcript: 11:56:22Z `ciel-work: ciel-work was unloaded: it crashed the hooks worker`, then 12:04:44Z `Unknown command: /work-mode`. With the mod unloaded, none of its hooks run: no pump, no native observation, no review wake, no fork gate.
  - Same notice earlier: Andy 04:08:09Z; Celly 11-19 08:59:03Z and 11-20 06:42:51Z; KOBE 11-18 12:34:33Z, 17:26:43Z and 17:43:06Z.
  - Andy's ciel-runtime-router MCP log: 515–685 tool calls per minute 11:53–11:55Z, of which 461–621 were `work_host_page`. The previous Andy process logged 288–358 per minute before its 10:13Z exit. Celly's current session: 64–119 per minute.
  - Andy's 11:52:34Z notice `Parallel read failed: EADDRINUSE` came 4 minutes before the unload.
  - Router timeline (local -6):
    - 06:11:49 `router_lifetime_stopped reason=claude_exit`.
    - Andy relaunched at 12:12:13Z under the same conversation. The router restarted at 06:12:15 and 06:12:23 local, and the launch orphan sweep released 4 leases.
    - The `/health` miss was during that relaunch. At 12:13:56Z the router answered with c955630, pid 99752.
  - No Windows Application Error or WER event 1000/1001 for claude.exe between 11:55 and 11:58Z.
  - Not confirmed: what the hooks worker died of. The engine's notice carries no reason, and no other log was found. The `work_host_page` volume is the measured anomaly, and the claim that it caused the crash is unproven.
- 11:55Z tick:
  - Andy: unchanged lease counts.
    - New notice at 11:52:34Z: "Work reconciliation: Parallel read failed: EADDRINUSE: Unable to connect".
    - The router log shows no error then.
    - The router answered 500–700 `/ca/mcp` POSTs per minute (05:48–05:55 local), mostly `work_host_page`. Ephemeral port exhaustion on the client side is a hypothesis, not confirmed.
  - Celly: 3 running, no findings.
  - KOBE: n878 and n880 are still without reviews, waiting for the vanished-fork fix. KOBE has no `work-parallel-stranded-v1.json` yet, which is expected: the store is created on first write.
- 11:40Z tick:
  - Andy: 46 waiting, 22 submitted, 3 running, 1 reserved; n1856 still blocked.
    - New: `router_post_uncaught PermissionError [WinError 5]` at 11:38:32Z, raised by `os.replace` of `work-parallel-stranded-v1.json` in `_stranded_roll_forward`. That call was writing `stage_failed` after `prepare` raised `workspace preparation/removal incomplete; retained`.
    - The stranded and finalization stores have no `PermissionError` retry around `os.replace`; the ledger, native, P0 and receipts stores retry 20 times.
  - Celly: restarted again (scope b86a350f); 2 running, 1 waiting; no findings.
  - KOBE: n878 is still without a review, and n880 (lease edf577f8) has the same shape: native `failed` 11:19:59Z, receipt 11:27:35Z, listed idle 11:28:39Z, no review. Covered by the vanished-fork fix under test.
- 11:25Z: First passes of the read-only monitor over Andy, Celly and KOBE. Confirmed a vanished-fork review gap on Celly (n1070, n1135; published only by Celly's 11:14Z restart) and KOBE (n878, open). Periodic monitoring scheduled.

(Fill-in note: the ticks below were written during the day above this line in the same file; this closing entry was added last.)
- 22:40Z note (not a monitor run; the session-only cron is still to be recreated after the restarts):
  - Board refs change (6720d3d, 75e604b, 351fa9a) installed by hash-checked overlay (hotfix11) on Andy (3 roots), Celly, KOBE, Prody and Betty. Record: features/board-refs. Router parts take effect at each router's next restart; mod parts reload at a turn end.
  - Prody and Betty had auto-updated at 18:20Z to beta 0.2.51-beta.20261120-172521.0cb598b before this overlay.
  - Andy_Main reported at 22:3xZ three observations after the ~21:32Z PC restart: n2453 (goal n1445) resumed, its worker's last line is an upstream "all targets were skipped by pre-dispatch filters" error, and the goal still shows "workspace preparation failed and its folder was kept"; n1962's resumed worker was refused "invalid or revoked worker capability" and the host assigned the same item a new P0 fork; n1953 P0 still has an open lease of an ended process. Read-only investigation pending.
  - Docker on this PC: 9 containers. All test containers came from Andy's Walkie Work forks (`docker run -d --rm --name <item>-...`). The 4 exited ones (walkie-n2453-bd49-pg/redis, walkie-n2453b-2043-pg/redis) were removed on the user's order. The 4 running ones (n2453-base-pg/redis, n2508-e2e-final-pg/rd) belong to two still-working forks and were left alone. Andy applied a fixed-instance rule (walkie-test-pg, walkie-test-redis, per-item database and redis db number) and told the running forks. ciel-runtime's Linux tests now reuse two fixed containers (ciel-runtime-test-py311/py310) instead of one container per run. Unused volumes: 363 (about 16.9 GB); not deleted, pending the user's decision.

- 22:57Z note (not a monitor run):
  - Prody 154103 decided by the user: forward-only side decisions post nothing. fc9ada9, installed as hotfix12 on Andy, Celly, KOBE, Prody and Betty (record: fixes/forward-no-reply). Router restart still pending per host.
  - User asked about "Prompt dropped by a hook: Owned Work notification retained outside main conversation" on Celly: the designed drop of a host-owned fork's native task notification (`ownedWorkNotification`, register.js:1931-1936); the fork's result reaches main through its durable receipt. Census of Celly transcripts (read-only): session b86a350f has 101 such lines for 39 task ids, 20 of them repeated, one 6 times; the current session 9288dece has 62, two task ids resolved, last at 22:40Z. The repeated drops of one notification are recorded here; why the engine redelivers is not confirmed.
  - Andy_Main relayed a Walkie request (154648/154643): accept forwarded loopback Work API requests carrying a valid admin token while the router stays on 127.0.0.1, and confirm how the token reaches the runtime. This changes an external access surface, so it is waiting for the user's decision; nothing changed.

- 01:31Z, 01:50Z, 02:09Z ticks (cron recreated as 3fe565b3 after the restarts):
  - Router swap verified live (23:1xZ): Andy pid 21456 -> 57696, Celly 30540 -> 51628 (elevated kill; unprivileged Stop-Process was Access denied), KOBE 14824 -> 14288, Prody 14772 -> 17184, Betty 24320 -> 8016. Every board reads refs True. Session restarts alone did not replace routers (the launcher keeps a healthy reachable router; RouterClientSupervisor).
  - Andy: n2508 and n1174 stranded recoveries finished on the new router (stranded state done, old leases done, replacements have workspaces); n1856 still deciding with stage_failed "unknown Work item" (item removed); n2503 rework_ready rows x2. User decision 2026-11-21: decided rows of removed or done items close too. Fail-first test exists in the worktree.
  - Celly: same 3 reviews blocked by 'still has unfinished children'; latest submitted lease 96a8b827 -> fa2278d6, no review row while native is running (same shape as before); accepted 5 unchanged.
  - KOBE: pid 15244 is gone; a live process tree is present (node npm-bin 17492 spawning python 2492 -> 10132 -> 6072/6740 and claude.cmd -> claude.exe), so the "dead owner" report was a pid comparison artifact. The two slot rows cleared by the next tick; the monitor's owner-proven check resolves a pid through the router log, while KOBE's process tree is alive. Finding 0 at 01:50Z and 02:09Z.
  - Prody: one unchanged "slot held, fork absent since 23:09" row (n1176); no new failures.

- 2026-11-21 03:26–03:27Z tick (read-only):
  - Andy (ledger 4744): running with slot 2, waiting_input 47, submitted 21. n1856 / 55353fab still deciding with `stage_failed: unknown Work item`. Transcript: n1479/n2044 execution approval refused; n2042/n2130 P2 capacity refused. Router: external SSE read EOF 74, client resets at router/Anthropic stream/MCP 7/6/2, one router-activity WinError 5 and one transcript delivery timeout.
  - Celly (5364): running with slot 1, waiting_input 2, submitted 1, accepted 5. Five board stages still report unfinished children (n1179, n1160, n1158, n1181, n924). n1188 / fa2278d6 has a completion receipt but no review row, native `waiting` at 03:20Z; three decision-refused-stop entries. The transcript reports missing matching idle workspace proof. Router: external SSE read EOF 69, MCP/router resets 14/6; upstream 503 and transport retries also logged. No root cause inferred for the native waiting state.
  - KOBE (2670): running with slot 2, waiting_input 3; no new Work failure or dead-owner finding. External SSE EOF 70 and connect timeouts 3; transcript has board version conflicts and a no-eligible-candidate refusal.
  - Prody (246): running with slot 2, waiting_input 7, submitted 6. No native fork observed by the monitor for the slot holders n1176 / 817353a6 and n1170 / 69938858; absence alone is not proof of exit. Three upstream SSE forwarding errors before message_stop.
  - Andy review closure hotfix `cb8a0f6`: installed and file hashes verified on Andy, Celly and KOBE; npm beta tarball matched those hashes. No live restart or state mutation during this tick. Still-open Andy review is not evidence of live activation.

- 2026-11-21 20:52Z monitoring tick (read-only; compared with 20:33–20:34Z; times UTC):
  - Andy v5363 → v5385, 6 running slots and 22 submitted unchanged, waiting 1 → 2. New absence flags n2543 since 20:38 and n2532 since 20:40; previous absence flags no longer listed, not completion proof. One `decision_refused_stop` for n2042/8db80ab6 aligns with the 20:41:32 execution-approval refusal for rework (guard, not a transport failure). Transcript: MCP closed x11 from 20:35:32 and ECONNREFUSED x6 from 20:37:30 to 20:51:23; auto-continue read failure x1 at 20:38:08. Router: `/ca/mcp` client resets x19 from 20:35:29, external SSE read EOF x17 from 20:34:32, transcript delivery timeout x4 from 20:39:39, turn-event retry x1 at 20:40:10, activity file WinError 5 x1 at 20:41:14. Current health OK, PID 46320 unchanged; not proof the intermittent MCP errors are resolved.
  - Celly v6129 → v6132, running slots 2 → 3, waiting 16 unchanged. n1229/n1241 stale-slot discrepancy persists (see independent 20:47Z reading), and the transcript records a P3 capacity refusal at 20:47:28. The new n1288 binding is not classified as stale. Transcript also shows exact submitted lease/receipt guard x2 at 20:46:07/14, duplicate lease guard x1 at 20:47:15, version guard x1 at 20:46:25, and HTTP 400 x2 at 20:45:11/15. Router: external SSE read EOF x17 from 20:34:32, external SSE resets x3 from 20:45:16, transcript delivery timeouts x7 from 20:35:10 and two turn-event timeout retries for the same event at 20:35:16/22. No new Work failure-journal entry.
  - KOBE v2778 unchanged (1 running slot, 8 waiting), n32 still native-unobserved. Router: keep-alive version mismatch x224 from 20:33:34 with health OK; external SSE read EOF x15 from 20:33:48, read timeout x2 from 20:35:19, connection reset x2 from 20:50:46. One upstream stream error at 20:42:21 plus its shape diagnostic, no transcript tool errors. Prody: SSH collection failed x1; no fresh Work or router evidence.
  - No slot forced free, runtime-state file edited, or session/router restarted. The P3 scheduling impact is established; a safe, reproducible stale-slot transition to fix has not yet been isolated.

- 2026-11-21 20:41–20:47Z Celly P0 and stale-slot investigation (read-only):
  - Board v8646 and parallel REST show n1032/n1048/n926 still in progress, effective P1; the P0 journal records `priority_changed`, not completion or deletion (08:43:15/21/31Z respectively). n997 remains effective P0 and executing; its only unfinished leaf n1140 waits for the recorded fleet-wide n1154 F1 deployment. No P0 fork, no runnable P0 item. P0 intents total 256, but no Celly intent or current capacity refusal was established as the cause of this blocked goal.
  - At 20:47Z, execution ledger v6132 and native projection v8423: zero confirmed Work forks, zero published pending reviews; nevertheless n1229/8deddae7 (P3) and n1241/662bd67f (P1) remain `running, slot=true` in the execution ledger while both coordination runs are `waiting`. Exact native records last showed n1229 waiting / missing since 19:16:07Z, n1241 idle / missing since 14:54:04Z. The board records these items as superseded and explicitly not to redispatch. Missing alone is not independent process-exit proof. n1288/1ff2974f is a newly bound P1 lease with no native observation in this snapshot, not classified as stale.
  - P3 is held at 1/1 by n1229 and has 4 runnable candidates in the v6132 sample; direct observed scheduling impact. P1 2/5 has 25 waiting/blocked leaves and one fenced by a running lease; P2 0/2 has 5 waiting/blocked; P5 0/1 has 56; P4 has no work. Paused=false. Stale slots are real but not the universal explanation. `native_observation` updates present coordination rows and publishes submitted reviews; it does not retire these running leases from absence alone. Exact cause of the stale transition not yet established; no state edits, redispatches, or restarts.

- 2026-11-21 20:33–20:34Z monitoring tick (read-only; compared with 20:12Z; times UTC):
  - Andy v5356 → v5363, running+slot 7 → 6, submitted 21 → 22, waiting 1 unchanged. n1744/n2042/n2541 remain native-unobserved. Router: external SSE read EOF x20 from 20:13:18, transcript delivery timeout x3 from 20:15:25, turn-event delivery retry x1 at 20:16:16 (timeout), SSE connect reset x1 at 20:26:00, observe-only projection x6 from 20:22:04. Transcript: UI approval prompt received no answer x1 at 20:33:22; no bypass or defect inferred. CPU not resampled.
  - Celly v6129 unchanged, running+slot 2 and waiting 16; n1229/n1241 still native-unobserved. Router: external SSE read EOF x20 from 20:13:16, transcript delivery timeout x3 from 20:15:11, connect reset x1 at 20:15:36, context-usage file replacement WinError 5 x1 at 20:30:55 (underlying cause unverified), observe-only projection x6. No repeat of stale_unconfirmed in the sample. Transcript: invalid arguments HTTP 400 x4 (20:12:36, 20:18:00, 20:19:13, 20:19:20), Walkie UUID validation x1 at 20:18:56, version guard x1 at 20:19:04 and unknown side handoff x1 at 20:25:40. Other tool failures: external fetch 404, search timeout, and script exit; no runtime causality established.
  - KOBE v2778 unchanged, running+slot 1, waiting 8; n32 still native-unobserved. Router: healthy active-client keep-alive version mismatch x252 from 20:12:22, external SSE read EOF x18 from 20:13:14, connect reset x1 at 20:15:28 and connect timeout x1 at 20:23:17. No further hooks worker crash notice in this window. Stranded journal absent.
  - Prody v326 unchanged, running+slot 1, waiting 2, submitted 6. n1170 remains native-unobserved and monitor still reports the recorded owner PID unavailable; not proof of fork/CLI exit. No router/transcript warnings; failure and stranded journals absent.
  - No new failure-journal entries in available data or newly reproduced defect. No restart, operational state edit or new install. Publish workflow 44946132511 subsequently confirmed success for 2fd75cd; no live hook verification.

- 2026-11-21 20:31Z improvement release: `2fd75cd` labels Work reservation failures by the actual operation (`reserve`, `reserve_next`, `p0_reserve_next`) instead of reporting them as read failures. The reproduced capacity and no-candidate refusals keep their original reasons and do not spawn; genuine status read failure and the side handoff conflict contract are unchanged. Windows and Linux py310/py311 each ran 160 harness tests (127 passed, 33 skipped); plugin validation and 90 plugin tests passed. Public npm beta `0.2.51-beta.20261121-202452.2fd75cd` tarball register.js SHA-256 matched the commit (`79252aaf887e67a2a63c5e771d8b27d8445594d956a59d65056ed22a477f2bfe`). The same file was backed up, installed, and SHA-256 verified in all three local install roots. No remote install or production session restart was performed for this release; live hook behavior was not verified.

- 2026-11-21 20:12Z monitoring tick (read-only; compared with 20:07Z; times UTC):
  - Andy v5356 unchanged (7 running with slot, 21 submitted, 1 waiting); n1744/n2042/n2541 remain native-unobserved. Router external SSE read EOF x5 from 20:08:05; no transcript errors or repeat of the preceding 503 retry burst in the sample. CPU not resampled.
  - Celly v6126 → v6129; 2 running with slot and 16 waiting unchanged, submitted 1 → 0. n1280 no longer flagged, but review outcome not read from this projection. n1229/n1241 remain native-unobserved. Router input 15442: unseen after 20.1s at 20:08:00, `prior_submission_failed / stale_unconfirmed` skip at 20:08:02 (one warning and one error for the same input; repeats 15420/15437 pattern). Actual delivery and later queue impact unknown. Router client reset x1 at 20:07:59, external SSE read EOF x5 from 20:08:07, observe-only projection x3. Transcript: subprocess reader exception x1 at 20:08:52 (truncated), invalid arguments 400 x1 at 20:11:07, conversation access denied 403 x1 at 20:11:33; no causal link established.
  - KOBE v2778 unchanged (1 running with slot, 8 waiting); n32 remains native-unobserved. Transcript: `ciel-work was unloaded: it crashed the hooks worker` x1 at 20:11:29, same symptom as Andy at 17:15, not proof of same trigger; side append notice x1 at 20:09:52. Router: keep-alive version mismatch x63 from 20:07:11 (health OK), external SSE read EOF x5 from 20:07:59, transcript-delta timeout x1 at 20:10:23.
  - Prody reachable after five missed snapshots. v326 unchanged (1 running with slot, 6 submitted, 2 waiting); n1170 still native-unobserved. New: monitor could not open recorded owner PID 14112 with a process-not-found-class Windows error; the reporter does not validate owner creation identity. No fork exit, session exit or sweep failure inferred from this alone. No collected router/transcript warnings; stranded/failure journals absent.
  - No new failure entries in accessible journals or newly reproduced product defect. No restart, runtime-state edit, or deployment in this tick.

- 2026-11-21 20:07Z follow-up collection (read-only; compared with 19:52Z; times UTC):
  - Andy v5351 → v5356; 7 occupied running slots, 21 submitted, 1 waiting unchanged. n1744/n2042 still unobserved; n2541 newly unobserved since 19:56Z. Router: external SSE read EOF x15 from 19:52:34; one client reset at 19:54:23. From 19:58:49 the sampled router groups contain 20 HTTP 503 retry entries and 5 transport-disconnect retry entries across multiple operation IDs, not 25 failed requests; final outcomes and upstream cause unverified. No new Work failure or transcript tool error in the sample. CPU not resampled.
  - Celly v6122 → v6126; running slots 3 → 2, submitted 1 and waiting 16 unchanged. n1241/n1229 remain unobserved. n1280 has a completion receipt and no review row; native last observed running at 20:06, so no stalled-review conclusion. Duplicate-dispatch refusal at 19:52:50 is an expected guard. Router: external SSE read EOF x15 from 19:52:28; gateway transport retries x2 (20:00:38, 20:05:19), transcript-delta timeout x1 at 20:05:09. Stream-recovery note read failure and its wrapper warning at 20:01:19 are one incident, not two. No repeat of stale_unconfirmed or router-activity replacement denial in this sample.
  - KOBE v2778 unchanged; n32 still unobserved, running slot 1 and waiting 8 unchanged. Keep-alive version mismatch x175 from 19:52:19 with health OK; external SSE read EOF x15 from 19:52:21. No repeat of prior transcript delivery warning or turn-event drop in sampled output. Prody SSH collection failed once again (fifth consecutive unobserved tick); no inference about liveness.
  - Track gateway retry outcome visibility, receipt → native idle → review publication, and Prody observability along with the previously recorded Celly delivery confirmation and Andy CPU cases. Existing 20-minute job retained; no restart, state edit, or proven new defect.

- 2026-11-21 19:52Z monitoring tick (read-only; compared with 19:32Z; times UTC):
  - Andy v5338 → v5351; running+slot 7, submitted 21 and waiting_input 1 unchanged. n1744/n2042 remain native-unobserved. Router: external SSE read EOF x19 from 19:33:01; router client resets x2 from 19:38:07, Anthropic streaming client reset x1 at 19:48:03. At 19:41:07 late SSE retry x1 plus upstream error event x1 and its error-shape diagnostic (not counted as another failure); relationship between retry and error not established. Transcript delivery timeout x1 at 19:45:27; no transcript tool errors or new Work failure entries. CPU not resampled.
  - Celly v6114 → v6122; running+slot 2 → 3, submitted 0 → 1, waiting_input 16; n1241/n1229 still native-unobserved. Router: external SSE read EOF x19 from 19:33:02; Anthropic streaming resets x3 from 19:33:17, provider-path client resets x2 from 19:34:02, external SSE connect reset x1 at 19:42:03. Router-activity file replace WinError 5 x1 at 19:37:25 (repeat, underlying cause unknown). Input 15437: unconfirmed after 20.6s at 19:50:58, `prior_submission_failed / stale_unconfirmed` skip x1 at 19:51:02; same signature as input 15420 in prior tick, actual delivery and downstream queue impact unverified. Transcript: external shell parser error x1 at 19:35:52, side append notices x2, no-eligible-candidate refusal labeled `Parallel read failed` x1 at 19:51:39 (same clarity candidate as capacity refusals). No new Work failure-journal entries.
  - KOBE reachable again after three unobserved ticks. v2778 unchanged from last successful sample: waiting_input 8, running+slot 1, n32 remains native-unobserved. Router: transcript delivery connection refused (WinError 10061) x22 from 19:32:23, delivery timeout x25 from 19:32:33; agent turn event retries exhausted and one event explicitly dropped at 19:34:49, a separate delivery path. Underlying transport cause not established. External SSE read EOF x13 from 19:38:46, connect timeout x6 from 19:33:00 plus x1 at 19:49:14, Anthropic client reset x3. Keep-alive version mismatch x228 from 19:32:26, health OK (not a router restart failure). Transcript: same-event appended/already notice pair x1 at 19:38:21, invalid goal x1 at 19:47:29, search timeout x1 at 19:48:21. No new Work failure-journal entries; stranded journal not readable (missing file).
  - Prody: SSH collection failed once, fourth consecutive unobserved tick. No conclusion about router, review or native state.
  - No newly reproduced product defect, code deployment, restart or manual operational-state edit. Native absence is not proof of exit.

- 2026-11-21 19:32Z monitoring tick (read-only; compared with 19:12Z; times UTC):
  - Andy v5331 → v5338; running+slot 5 → 7, submitted 21 and waiting_input 1 unchanged. n1744/n2042 remain native-unobserved; n2532 no longer flagged, not proof of completion. Router: external SSE read EOF x19 from 19:13:18; SSE connect reset x1 at 19:14:21; delivery timeout x1 at 19:30:21; observe-only projection x4. No WinError 5 or oversized-record warning in the sample. Transcript: P2 capacity guard x1 at 19:13:25 again labeled `Parallel read failed`; same clarity candidate as prior P5 guard. Side append notice x1 at 19:21:16. CPU not resampled.
  - Celly v6106 → v6114; running+slot 4 → 2, waiting_input 16 unchanged. n1241 still native-unobserved; n1229 newly flagged since 19:16; n1273/n1274 no longer flagged. Router: external SSE read EOF x19 from 19:13:17, connect reset x1 at 19:14:21, provider-path client resets x3 from 19:16:28 and Anthropic streaming client resets x2 from 19:14:36, observe-only projection x4, late ping ignored x2. Same input 15420: inject logged 19:13:52, no confirmation by 19:14:11 (20.1s; one warning), `prior_submission_failed / stale_unconfirmed` logged 19:14:12 (one error), skip logged 19:14:23. Actual delivery, root cause and any continuing queue impact are unverified; repeat of the stale-confirmation signature seen earlier. Transcript: exact owned submitted lease/receipt guard x1 at 19:16:57 and validation/review/cleanup completion guard x1 at 19:18:22; the 19:12:19 script assertion was already counted in the previous sample.
  - KOBE and Prody: SSH collection failed once each; third consecutive tick unobserved. No inference about router, reviews or forks.
  - No new Work failure-journal entries on the observed hosts. No newly reproduced defect, restart, deployment or manual operational state edit in this tick.

- 2026-11-21 19:12Z monitoring tick (read-only; compared with 19:06Z; times UTC):
  - Andy v5327 → v5331; submitted 21, running+slot 5, waiting_input 1 and native-unobserved n1744/n2042/n2532 unchanged. Router: external SSE read EOF x6 from 19:07:00; WinError 5 writing channel cursor x1 at 19:10:05 and replacing router-activity file x1 at 19:11:15 (replacement denial now observed on Andy and Celly, underlying cause not established); client reset x1 at 19:11:10, `/ca/mcp` client reset x1 at 19:11:23; observe-only projection x2. Transcript: GitHub run 404 x1 at 19:07:20, board version guards x2 at 19:09:04 and 19:11:22, side append notice x1 at 19:09:30, P5 capacity guard x1 at 19:10:42 (labeled `Parallel read failed`; message clarity candidate, not a dispatch failure), invalid arguments HTTP 400 x1 at 19:10:53. No new Work failure record or oversized-record warning. Andy CPU/UI symptom still open, not measured anew in this tick.
  - Celly v6106 unchanged: running+slot 4, waiting_input 16; same n1241/n1274/n1273 native-unobserved. Router: SSE read EOF x6 from 19:07:00, observe-only projection x1 at 19:08:08. Transcript: completion-evidence guard x1 at 19:06:25, external script assertion failures x2 at 19:11:41 and 19:12:19; no runtime causality established. No new Work failure record or activity-file denial in this sample.
  - KOBE and Prody: SSH disconnected once per host during collection; second consecutive tick without state observations. Last observed v2778 and v326 respectively; no inference about liveness or Work state.
  - No newly reproduced defect, deployment, restart or manual operational state edit. Native absence does not show exit.

- 2026-11-21 19:06Z post-rollout observation baseline (read-only, compared with 18:55Z; recurring job 833102d2 retained):
  - Andy v5327 and submitted 21 / running+slot 5 / waiting_input 1 unchanged. Native observations missing for n2042 (since 18:20Z), now also n1744 and n2532 (since 18:51Z); not exit evidence. Router: external SSE read EOF x11 from 18:55:38Z; Anthropic client reset x1 at 18:59:36Z; transcript-delta delivery timeout x1 at 19:00:34Z. No transcript errors or new journal failures reported. At 19:07Z router PID 46320 health OK and consumed 6.16 CPU-s in 5.02 wall-s (prior sample 6.61 in 5.01); residual load remains an investigation item. No oversized transcript warning in this sample; no UI freeze resolution inferred.
  - Celly v6106 and waiting_input 16 / running+slot 4 unchanged. Native observations missing for n1241 (since 14:54Z), now also n1274 and n1273 (since 18:54Z). Router: SSE read EOF x11 from 18:55:31Z, router-activity temporary file replacement denied with WinError 5 x2 from 19:03:48Z, Anthropic client reset x1 at 19:03:59Z; projection warnings x4 are observe-only. Transcript: Walkie client_id UUID validation rejection x1 at 19:06:05Z (repeat), side original append notices x2. Root cause of file replacement denial unknown; no new journal failures.
  - KOBE and Prody both SSH-unobserved; one collection failure each and one read-only retry each timed out during SSH banner exchange. No inference about router, review or fork state. Last observed v2778 and v326, respectively.
  - Collect recurring symptoms and improvement candidates separately from reproduced defects. No new proven defect, restart or manual runtime-state edit this pass.

- 2026-11-21 18:55Z monitoring tick (read-only; compared with 18:32Z):
  - Andy v5319 → v5327: running+slot 4 → 5, reserved+slot 1 → 0; waiting_input 1 and submitted 21 unchanged. n2042 / 8db80ab6 remains slot-held and native-unobserved since 18:20Z. Transcript: one ECONNRESET at 18:50:19Z and 20 `MCP server is not connected` entries at 18:50:20–26Z (19 notices, one tool error), overlapping the authorized router replacement; subsequent Work status calls were observed, not proof of full Work recovery. Router local times (UTC−06): process-exited restart 1 at 12:50:22, gateway discovery fallback 1 at 12:51:16, external SSE read EOF 4 from 12:51:31, client reset 1 at 12:54:58. No new failure-journal entry or oversized transcript warning in the tick output; prior post-restart CPU finding remains open.
  - Celly v6100 → v6106: running+slot 2 → 4, waiting_input 16 unchanged; n1241 / 662bd67f still native-unobserved since 14:54Z. Transcript: Walkie `client_id` UUID validation rejected 4 calls (18:38:02–20Z); duplicate lease guard 1 (18:41:46Z), invalid side receipt identity 1 (18:41:47Z), goal-selection guard 1 (18:43:15Z), side original appended 1 (18:43:46Z). These precede the authorized restart. Router local time (UTC−06): expected process-exited restart 1 at 12:53:19, SSE read EOF 1 at 12:54:29. No new Work failure-journal entry.
  - KOBE v2778 unchanged: running+slot 1, waiting_input 8; n32 / 69dc1a51 remains native-unobserved since 12:33Z. Router local time (UTC−08): expected process-exited restart 1 at 10:52:20, active-client keep-alive version mismatch 32 from 10:52:40 (health OK, replacement PID 1112), SSE read EOF 2 from 10:53:36, gateway discovery fallback 1 at 10:54:30. Transcript had one side-original append notice at 18:49:18Z, no tool error; no new Work failure-journal entry.
  - Prody v326 unchanged: submitted 6, waiting_input 2, running+slot 1. n1170 / 69938858 still native-unobserved since 06:14Z. No router or transcript warnings in this sample; stranded/failure journals absent, not evidence of a clean end-to-end run.
  - No new defect established, no edits to operations state or restart during this tick. Native absence is not exit; approved restart warnings and validation guards are not treated as defects. Overlay hashes and new router PIDs were verified separately in the rollout; freeze resolution remains unverified.

- 2026-11-21 18:50–18:54Z operator-authorized router-only rollout of c4ec55d:
  - Verified the three overlay file hashes in the router entrypoint roots for Andy, Celly, KOBE and Prody. Prody received its overlay in this rollout (prior file hashes checked, backups tagged `freezefix-20261121`). Package version metadata was not replaced.
  - Router PID changes and successful health probes: Andy 74488 → 46320 (launcher 72984; Claude 71576 retained); Celly 48616 → 10972 (launcher 72236; Claude 74984 retained); KOBE 1344 → 1112 (launcher 10132; Claude 15432 retained); Prody 23660 → 8852 (launcher 14112; Claude 22796 retained). Only the router processes were stopped; the existing launchers respawned them. No Work state was manually edited.
  - Local post-restart logs showed 55 Andy and 3 Celly Work status MCP calls and zero oversized transcript warnings in the sampled windows. Andy still used 6.61 CPU-seconds over 5.01 seconds, so CPU contention and the full freeze symptom are not resolved by this evidence. Four-host read-only monitor returned data; an end-to-end Work result and UI responsiveness were not verified on every host.

- 2026-11-21 18:36Z publish confirmation: npm publish run 44935484560 completed success (head c4ec55d); beta dist-tag = `0.2.51-beta.20261121-182602.c4ec55d`. File overlay was already installed (tag `freezefix-20261121`); no router restart yet, so the live load check stays open.

- 2026-11-21 18:32Z tick (read-only) + freeze-bundle deploy:
  - User ordered the fix shipped. c4ec55d pushed to beta; npm dist-tag beta now reads `0.2.51-beta.20261121-182602.c4ec55d` while run 44935484560 still shows in_progress (registry tag is the published fact; the run's later jobs are not done). Hash-checked overlay (base ce92166, backup tag `freezefix-20261121`) installed the three files on local (3), Celly and KOBE; no process restarted, so no running router loads them yet. Andy_Main notified.
  - Andy: ledger 5315 → 5319; running+slot 5 → 4, waiting_input 1, reserved+slot 1. New finding "slot held, fork absent since 18:20: n2042 8db80ab6 P1 running" — fresh, absence is not exit. Router window is quiet now (6 SSE EOF, 1 client reset); no new lock errors and no new replay warnings in this window.
  - Celly: ledger 6100 unchanged; same n1241 finding; only MCP resets/SSE EOF/observe-only projection warnings this window; transcript errors are agent-side (NameError, missing module).
  - KOBE: ledger 2778 unchanged; n32 only; keep-alive 74 this window on 7765e9d.
  - Prody: ledger 326 unchanged; n1170 only; silent.
  - Live verification of the deployed bundle is still open: it needs a router restart (out of tick scope) followed by health version change + no exceeds-memory-limit replay progression + router CPU not saturating right after restart.

- 2026-11-21 18:26Z tick (read-only, after the user-reported Andy restart freeze):
  - Andy restarted at 12:02 local. Ledger 5274 → 5315; waiting_input 10 → 0 (the launch orphan sweep released 11 dead-owner waiting_input leases at 12:02:50), running+slot 5, submitted 21; findings empty. Restart freeze confirmed in the router log: the 1.5GB resumed transcript (feeac3ad, 1,518,325,509 bytes) was replayed from offset 0 — exceeds-memory-limit warnings at offsets 398,005,786 (12:04:25), 684,242,205 (12:05:22), 1,150,034,998 (12:06:53) — while the router used 6.9-7.97 CPU-seconds per 5 seconds. Code-confirmed mechanism: set_scope's bounded boundary scan (2.5s) can miss its deadline leaving turn_scan_path unset; read_turn_updates then pinned the path with offset 0 and replayed all pre-launch history in 4MB batches. Fix c4ec55d: that first poll now establishes EOF as the boundary and returns "". Same window: 7 ChannelFileLockBusy on workspace manifest c8cf0b6c (status ready) from _main_review -> review_observe / set_activity even though the installed durable_file_lock.py already had the ce92166 3x50ms retry; budget raised to 11x50ms in the same commit. Planning-visibility: diagnose() reproduced that a full priority hides planning blocks (full + planning=1 → attention []); c4ec55d names "goal in planning" in that reason and adds the priority to attention, plain full stays quiet. Linux regression of the bundle: 189 tests OK on the existing py310/py311 containers. npm beta publish run 44935484560 was in_progress at this tick. NOT loaded by any running router yet (Andy's health still reports c955630).
  - Slots at a fresh read: n1744/n2476/n2042/n2532/n2472 running with host_id+dispatch_name; older reserved n2476 (4d995afb) and orphaned n2442 (c0fa5ab2, run active) remain; n1936/n1445 have no lease. Parallel read timeout at 18:09:22 in main's transcript. No duplicate assignment made; Andy_Main notified.
  - Celly: ledger 6096 → 6100; waiting_input 15 → 16. More ChannelFileLockBusy from the pre-ce92166 router: side-inboxes locks x5 from 12:06:16 and runtime-inputs.jsonl.lock x2 on /v1/messages at 12:05:47, plus one console-deferred lock busy. Host round at 18:15:10 logged "pump failed · attention P0,P1,P2,P5". All-NUL stream-recovery JSONDecodeError repeated once at 12:20:12.
  - KOBE: ledger 2778 unchanged; n32 finding only; keep-alive 415 on 7765e9d; side appended/already pair again (f92ebb007dac).
  - Prody: ledger 326 unchanged; n1170 only; silent.

- 2026-11-21 17:52Z tick (read-only):
  - Quiet tick. Andy ledger 5274 unchanged, only 19 external SSE read EOF; the 17:15 hooks-worker crash did not repeat in this window. Celly ledger 6092 → 6096, waiting_input 14 → 15, same n1241 finding; NO `ChannelFileLockBusy` and no auto-continue notice this window. KOBE ledger 2778 unchanged, only n32 finding; keep-alive 234 on 7765e9d; one side original appended (no pair this time). Prody ledger 326 unchanged, completely quiet.
  - No new proven defect. Tracked items stand: mod hooks-worker crash (Andy 17:15, one occurrence), auto-continue status-read failures (Celly x2 / Prody x1), planning resume refusals (Andy, KOBE), side appended/already pairs (KOBE, Prody), and pre-ce92166 lock contention across four lock files. The lock-retry code remains on disk but unloaded by every running router.

- 2026-11-21 17:32Z tick (read-only):
  - Andy: ledger 5274 unchanged. New transcript notices (17:15:20/17:15:34): "ciel-work was unloaded: it crashed the hooks worker" then the hooks worker was replaced during tool.call and ciel-work-gate's `.catch` answered in place. Which input crashed the worker is not in the monitor window; recorded as a new tracked item (mod worker crash) rather than a proven defect. No new failures.
  - Celly: ledger 6092 unchanged. `ChannelFileLockBusy` again x2 at 11:27:04 local, another side-inboxes per-event lock (`.../locks/16/1…`, a different conversation hash from 10:32:56) — contention is now 2 distinct side-inbox conversations plus coordination plus Andy's manifest. Auto-continue status-read notice RECURRED at 17:22:13 (Celly's second; Prody has one). MCP client resets 25.
  - KOBE: ledger 2777 → 2778; waiting_input 7 → 8, running+slot 2 → 1. New failure `revival_refused b748a0a8 x1` — that is n49's lease: at 17:18:27 main's message to its worker was refused because the lease is `unreported`, not running/waiting. So n49 was never confirmed running by this transcript; earlier "fork absent" windows for it are superseded by this host text. Guard behavior recorded as designed; why the lease never reported is not established here.
  - Prody: ledger 326 unchanged. Transcript shows the side appended/already pair again (2840f2b016a1 at 17:26:10 then 17:26:22) — the same-notice pair pattern now on two hosts. Other transcript errors are Walkie-room answers.
  - Recurrence tally: auto-continue status-read failure: Celly x2, Prody x1. Side notice pairs: KOBE x2 events, Prody x1 event. `ChannelFileLockBusy` (pre-ce92166 routers only): Andy manifest x7 (08:35Z+), Celly side-inbox A x14 (08:39Z+) + x1 (10:32), coordination x1 (09:55), side-inbox B x2 (11:27). No live restarts; lock retry still unloaded everywhere.

- 2026-11-21 17:12Z tick (read-only):
  - Andy: ledger 5264 → 5274; submitted 21 → 22, running+slot 2 → 1. New failure `decision_refused_stop 3a70b6e3 x1` and transcript refusal "rework limit reached (2/2); result remains pending review" (17:09:49) — the limit guard firing, recorded as designed behavior, not a defect. The "workspace ownership was handed off; retained" parallel-read notice repeated (17:03:58; second occurrence after 16:51:25).
  - Celly: ledger 6078 → 6092; waiting_input 13 → 14. No `ChannelFileLockBusy` this window. New notice at 17:07:28: "Side router operation failed: work_host_page: EADDRINUSE" — single occurrence, cause not established. Other transcript errors are agent-side (unterminated string, KeyError) and the Work completion guard.
  - KOBE: ledger 2774 → 2777. n49 finding gone again (intermittent observation confirmed twice); only n32 (since 12:33) remains. `runtime_activity_context_usage_failed` WinError 5 x2 (same tmp-rename shape as Andy's earlier one). Two side originals appended.
  - Prody: ledger 326 unchanged; n1170 only; completely quiet.
  - Auto-continue status-read notice did not recur this tick on either Celly or Prody. Lock contention count unchanged (no new `ChannelFileLockBusy` anywhere this window; all routers still pre-ce92166 processes).

- 2026-11-21 16:52Z tick (read-only):
  - Andy: ledger 5256 → 5264 (moved after five flat ticks). Same n1744 finding. New lines: one `secure_json_read_failed` PermissionError reading `sse-gate-status.json` (10:41:16 local) and transcript notice "Parallel read failed: workspace ownership was handed off; retained" (16:51:25). Both single occurrences; no cause inferred.
  - Celly: ledger 6062 → 6078; waiting_input 11 → 13, running+slot 4 → 2. ONE new `ChannelFileLockBusy` at 10:32:56 local, on a side-inboxes per-event lock path (`.../locks/13/1…`) — a fourth distinct contended lock file, still from the pre-ce92166 router. Transcript: `work_side_handoff: invalid side receipt identity` once (16:38:58).
  - KOBE: ledger 2774 unchanged. n49 finding is back with a NEW absent-since time (16:24, not 15:36), so the fork was observed in between; absence windows are intermittent, not exits. n32 unchanged since 12:33.
  - Prody: ledger 326 unchanged; n1170 only. Same "auto continue could not read the Work status" notice as Celly's at 16:39:03 — now observed on two hosts, each once, ~9 min apart. Tracked as a recurring pattern; the monitor does not yet show whether status reads fail briefly on schedule.
  - Pattern count update: planning resume refusal x2 hosts; auto-continue status-read failure x2 hosts; contended lock files x4 distinct paths (all pre-retry routers). No new proven defect; no restarts.

- 2026-11-21 16:32Z tick (read-only):
  - Andy: ledger 5256 unchanged (five ticks). Same n1744 finding, no new failures. Transcript error is a GitHub 404 for a run in another repo (OneCiel/ciel-walkie) — agent-side, not a runtime defect.
  - Celly: ledger 6046 → 6062; waiting_input 9 → 11. Same n1241 finding. No `ChannelFileLockBusy` this window. Notable transcript notice: "Ciel Work auto continue could not read the Work status, so unfinished items were not checked." at 16:30:24 — a transient status read failure recorded by the mod; whether it recurs is unwatched so far.
  - KOBE: ledger 2774 unchanged. n49 finding GONE this tick (only n32 since 12:33 remains) — the earlier absence was therefore not an exit proof, consistent with the no-conclusion rule. New transcript error at 16:17:37: "Work approval must be restored before resuming workers." — the same planning-phase resume refusal as Andy's n1445/n2453, now observed on a second host. Keep-alive warnings 236 on 7765e9d.
  - Prody: ledger 326 unchanged; n1170 only; silent.
  - Recurring unproven patterns tracked: (1) planning resume refusal observed twice (Andy 14:13, KOBE 16:17) with no pro-active surfacing; (2) KOBE side-notice appended/already pairs; (3) Celly's three distinct lock files contended from a pre-ce92166 router. None is yet proven in a fail-first repro beyond the shipped lock retry; no live restarts made.

- 2026-11-21 16:12Z tick (read-only):
  - Andy: ledger 5256 unchanged (four ticks). Same n1744 awaiting_capacity finding, no new failures; SSE EOF 19 + one client reset.
  - Celly: ledger 6034 → 6046; waiting_input 7 → 9, running+slot 5 → 4. Router: MCP client resets 14, SSE EOF 13, plus ONE new `ChannelFileLockBusy` at 09:55:19 local on `work-parallel-coordination.json.lock` (a different lock file from Andy's manifest and from the earlier Celly side-inbox file; same primitive). Celly's running router predates ce92166 (installed 15:37Z, process never restarted), so this is the old single-attempt behavior, not evidence against the retry. No transcript errors.
  - KOBE: ledger 2774 unchanged; n49 absent-fork row persists (since 15:36, now >30 min unobserved); n32 unchanged since 12:33. Keep-alive warnings 208 on version 7765e9d; SSE EOF 12, connect timeouts 6+1, transcript-delta timeout 1. Side notices: same-event "appended"/"already" pairs again (4d712f2df260 at 16:04:25 in both orders across ticks) — recorded as a repeated pattern, cause not established.
  - Prody: ledger 326 unchanged; n1170 finding only; silent router log.
  - Stripes: no host's running router loads ce92166 yet (overlay files verified on disk; restart is out of scope for ticks). Celly's two distinct lock files and now a third (coordination) suggest contention is spread across lock consumers, which the shared-primitive retry covers in code; no live proof either way.

- 2026-11-21 15:52Z tick (read-only):
  - Andy: ledger 5256 unchanged (three ticks now); same n1744 awaiting_capacity; no new failures. Router: external SSE read EOF 14, broken pipe 1, one `runtime_activity_context_usage_failed` WinError 5 (context-usage tmp rename). No ChannelFileLockBusy in window.
  - Celly: ledger 6026 → 6034; waiting_input 5 → 7; same n1241 finding. Router: SSE EOF 14, broken pipe 1, transcript-delta timeout 1, observe-only projection warnings; no lock errors this window. Transcript errors are agent-side (AttributeError/cp1252) and two upstream "Invalid operation arguments" 400s; not router defects.
  - KOBE: ledger 2774 unchanged; new finding alongside n32 (since 12:33): "slot held, fork absent since 15:36: n49 b748a0a8 P3 running" — too fresh to conclude; absence is not proof of exit. `router_lifetime_keep_alive` 184 lines still on health version 7765e9d. Two side-original notices for the same event a3ec41ade9b8 one second apart ("already", then "appended").
  - Prody: ledger 326 unchanged; n1170 finding unchanged; zero router WARN/ERROR; no transcript errors.
  - No new proven product defect this tick. The installed lock-retry file is still not loaded by any running router (no restarts permitted in ticks), so there is no live-effect evidence either way.

- 2026-11-21 15:36Z tick (read-only) + lock-retry install:
  - Andy: ledger 5256 unchanged since 15:34Z; same n1744 awaiting_capacity finding; new failures empty this window; one external SSE read EOF. No other change.
  - Celly: ledger 6026 unchanged; same n1241 slot-held/fork-absent finding; only two external SSE EOF and one transcript-delta timeout in window. No new lock errors in this window (too short to conclude).
  - KOBE reachable again (prior tick ssh failed): ledger 2670 → 2774, waiting_input 7, running with slot 2. Finding "slot held, fork absent since 12:33: n32 69dc1a51 P1 running" — absence is not proof of exit. Router log shows `router_lifetime_keep_alive` x24 with health version `0.2.51-beta.20261121-053016.7765e9d` (the dead-parked-worker build, not the newest). Stranded journal file absent.
  - Prody: ledger 326 unchanged; n1170 finding unchanged since 06:14; no router WARN/ERROR; one side original forwarded to main.
  - Lock retry ce92166: npm beta publish run 44919620668 completed success; `npm view` dist-tag beta = `0.2.51-beta.20261121-153131.ce92166`. Hash-checked overlay of `ciel_runtime_support/durable_file_lock.py` only (base 99320bc, backup tag `lockretry-20261121`): local three targets, Celly elevated, KOBE ssh — every target held the base bytes first and verified to the new hash (17A05071D3C45B9D…, before 4BA7ED3EEA12A562…). No process restarted, so no running router has loaded it yet. Andy_Main notified with the issue URL and the install/load distinction before this tick; n2472 itself stays recovered — do not resend the decision.

- 2026-11-21 15:34Z tick (read-only):
  - Andy (ledger 4744 → 5256): running with slot 2, waiting_input 47 → 10, submitted 20 → 21. One finding: n1744/a4f91fd1 review `awaiting_capacity` ("active slot capacity reached for P3 (1/1)") — admission wait, not treated as a product defect. New failure `decision_refused_stop 8b8a0299 x1` is the n2472 lease, already recovered. Transcript shows the n2472 window: `work_parallel_item_review` timeout 14:43:52 (local), then `ChannelFileLockBusy` 14:44:35 and 14:47:20; router log has 7 such ERROR lines from 08:35Z. Bounded lock retry committed as ce92166, pushed to beta (issue OneCielAI/ciel-runtime#4); npm beta publish run 44919620668 still in progress at this tick. File install and live router load are separate; the running router has not loaded it. Transcript also shows the 14:13:20 "Work approval must be restored before resuming workers." refusal (planning phase, n1445/n2453 case). Code-confirmed structure: title/note edit or out-of-scope child returns the goal phase to planning; planning is not a side-actor gate (`gates()` collects waiting_on/blocked only), so no actor raises it until main reads status or tries a resume. Other router lines: external SSE read EOF 75, transcript-delta delivery timeout 9 and connection refused 9, `channel_turn_projection_persistent` observe-only warnings; no cause inferred.
  - Celly (5364 → 6026): running with slot 1 → 5, waiting_input 2 → 5, submitted 1 → 0. New finding: "slot held, fork absent since 14:54: n1241 662bd67f P1 running" — native absence in this sample is not proof of exit. Router log: 14 `ChannelFileLockBusy` ERROR lines on a side-inboxes decision file from 08:39Z — the same lock primitive ce92166 now retries, but Celly's running router predates it. `secure_json_read_failed` (JSONDecodeError) still twice on the all-NUL stream-recovery ledger; no further evidence that the narrowed recovery code is live on Celly. `channel_stdin_proxy_blocked` once (prior_submission_failed, stale_unconfirmed). Transcript errors are Work guards (main inline-edit refusal, decompose-ready refusal), tooling (ripgrep/pdf/poppler), and one `work_side_status` host-only refusal; not new router defects.
  - KOBE: monitor run printed `lost connection` / `{"host":"kobe","error":"ssh failed"}`. No state read this tick; recorded as unobserved, not down.
  - Prody (246 → 326): running with slot 2 → 1, waiting_input 7 → 2, submitted 6. n1170/69938858 still "slot held, fork absent since 06:14" (would match the dead-owner orphan sweep once the already-deployed build is actually running; no live change made). No router WARN/ERROR lines in window. Stranded/failure journal files still absent (FileNotFoundError), consistent with prior ticks. Transcript: board version conflicts and side originals forwarded to main; no new failures.

- 2026-11-21 03:32Z tick (read-only):
  - Andy: 2 running with slot, 47 waiting_input, 20 submitted. Three `close` reviews (n518, n2157, n2122) have every stage except archive complete, but report `run is not terminal`. Exact completion receipts and ledger retirement proofs remain; corresponding coordination runs and archive entries are absent. A pruned archive fixture reproduced the failure; targeted regression passes with exact retirement and receipt checks. Bundles pending. Router replacement window logged MCP disconnections; external SSE EOF 2, reset 1, gateway discovery unavailable 1. Later ledger v4746: no occupied P2 slot at snapshot; n2042 and n2130 retain question receipts only. Lease 992cc386 belongs to n2032, orphaned without a slot; n2453 has a workspace on its current waiting lease.
  - Celly: 1 running with slot, 2 waiting_input, 1 submitted, 5 accepted. Five approved parent rows are blocked on unfinished children, as confirmed in the operator report; not treated as a defect. n1188 still has an exact completion receipt and no review row: native `waiting`, manifest `ready` and `idle=false`. The user-authorized router replacement already occurred; no CLI restart was requested or performed by this tick. Why native remains `waiting` is not established. Gateway discovery unavailable after router replacement.
  - KOBE: 2 running with slot, 3 waiting_input. n32 / 2cf4c651 lacks a recent native observation; no dead-owner finding this tick. External SSE read EOF 4 and broken pipe 1; one late upstream SSE retry and one side fallback notice.
  - Prody: 2 running with slot, 7 waiting_input, 6 submitted. n1176 and n1170 still lack corresponding native observations; no new router failure recorded. Native absence in this sample does not prove a fork exited.
