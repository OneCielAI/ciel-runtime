---
type: fix-record
title: 재시작 후 Work 큐가 자동으로 포크를 띄우지 않던 결함
description: Andy·Celly·Prody에서 재시작 후 큐가 돌지 않고 메인이 수작업으로 처리하던 문제. 입증된 원인 두 가지(대기 중인 목표를 승인할 길 없음, 큐 역순 디스패치)의 증거, 실패 테스트, 수정, 검증. 나머지는 미결로 남긴다.
tags: [work-parallel, p0, queue, restart, andy]
timestamp: 2026-11-23T12:20:00Z
---

# 사용자 요구 (2026-11-23)

- 재시작되면 진행하던 Work 큐가 자동으로 다시 실행된다.
- P0는 전부 포크로 다시 실행된다. P1–P5는 설정된 병렬 개수만큼 시작된다.
- 우선순위마다 할당된 병렬 개수를 지키면서 큐 순서대로 포크가 동작한다.
- 오래된 대기와 승인 전 단계에 남은 목표 때문에 큐가 막히는 것은 상태 관리 버그다.

# 관측 (Andy, 읽기 전용)

- 11:3xZ 라우터 진단과 보드 재집계가 일치: P0 18/P1 78/P2 3/P3 65/P5 53 미완료, 후보 수 모두 0, 실행 확인 포크 0.
- 재시작 펌프(`sessionStartHost` → `parallelPump('session.start')`)는 실행됐다. 후보가 없어서 띄운 것이 없었다.
- 11:51Z 사용자가 "P0 모두 포크 재개"를 지시했다. Andy 메인은 P0 목표마다 `within_scope` 자식 노드를 새로 만들고 우선순위를 다시 적었다(11:52–11:59Z, 버전 충돌 2회). 12:03:57Z 실행 확인 포크 7개. 즉 기존 보드로는 포크를 띄울 수 없었고, 메인이 새 리프를 만들어야 했다.

# 원인 1 — 대기 중인 목표를 승인할 방법이 없음 (입증)

- 증거: Andy 대화의 `work_*` 호출 추적(`.local/tmp/monitor/phase_trace.py`).
  - n2584·n2578·n1936·n2442·n2624는 `work_plan_set`/`work_node_add`로 등록될 때 "queued behind the active goal"이었다.
  - 그 뒤 이 루트들에 대한 `work_execute` 성공은 한 번도 없다. coordination `root_generations`도 `approval_generation 0`, `phase planning`이다.
  - n1445·n1756만 `approval_generation` 21·4, `executing`이다.
- 코드:
  - `work_execute`는 활성 목표만 승인했다(`_execute`, `tree.set_phase(doc, EXECUTING)`의 기본 root가 `active_root`).
  - `work_select`는 활성 목표에 실행 가능한 단계가 남아 있으면 사용자 요청 없이는 전환을 거부한다(`select_root`, `_select_refusal`).
  - 따라서 활성 목표가 일하는 동안 대기 중인 목표는 승인될 수 없었다.
  - 일반 레인은 planning 루트를 후보에서 뺀다(`runnable_leaves`). 그래서 대기 목표는 영원히 실행되지 않았다.
- 수정:
  - `work_execute`에 `root_id`를 추가했다. 정확한 대기 목표 하나를 승인하고 활성 목표는 바꾸지 않는다. MCP, REST command `execute`, 모드의 ask 승인 대화상자에 같은 동작을 적용했다.
  - 승인 게이트(`set_phase`의 워크플로우 `gate`), 준비 판정(`readiness`, 이제 root 지정), ask 승인 대화상자는 그대로 거친다.
  - 메인 지침(관리 프롬프트 `work_texts.py`)에 root_id 사용을 한 문장 추가했다.
- 실패 테스트 먼저:
  - `tests/test_work_queue_dispatch_order.py`의 `QueuedGoalApprovalTests` 2건. 수정 전에는 대기 목표가 `planning`으로 남았고, 없는 root_id도 오류 없이 받아들였다.
  - `tests/test_work_mod_write_gate.py`의 `test_ask_approval_of_a_queued_goal_shows_and_records_that_goal`(하네스 `execute-approval-queued-root`). 수정 전 register.js로 실행하면 대화상자에 활성 목표(n1)의 계획이 떴다.

# 원인 2 — 같은 우선순위 안에서 큐 역순으로 디스패치 (입증)

- 코드: 일반 레인 `runnable_leaves`와 P0 레인 `_p0_runnable_leaves`가 후보를 `-created_at`(최신 먼저)로 다시 정렬했다. 그래서 큐에 먼저 들어간 목표보다 나중에 생긴 목표가 슬롯을 먼저 받았다.
  - P0 함수의 docstring은 "oldest first"라고 적고 있었는데 코드는 반대였다.
  - 정렬은 4eebfdc(2026-11-09)에서 들어왔다. 그 커밋 메시지에는 최신순의 근거가 없다.
- 수정:
  - 두 레인 모두 큐 순서를 따른다: 활성 목표 먼저, 그 뒤 `queued_roots` 순서, 각 목표 안에서는 트리 순서(진행 중 먼저).
  - 일반 레인은 focus와 우선순위 순위만 앞에 둔다(안정 정렬).
  - 우선순위별 병렬 한도는 기존 `reserve`의 `priority_limits`가 그대로 지킨다.
- 실패 테스트 먼저: `QueueDispatchOrderTests` 2건(일반·P0). 수정 전에는 두 레인 모두 `['n4','n2']`(나중 목표 먼저)였다.

# 원인 3 — 포크 API 오류 재시도가 매번 1/20에서 다시 시작 (KOBE, 입증)

- 증거(읽기 전용, KOBE):
  - n107 포크 `agent-a944a21f7982dce9a.jsonl` 끝부분: 11:31:20Z부터 12:40:39Z까지 `Prompt is too long · automatic compaction failed: API Error: TimeoutError: timed out`가 20회 반복된다. 각 오류 뒤에 호스트의 `[ciel-runtime 포크 재시도 1/20]` 또는 exit 안내 user 기록이 있다. 진행 기록(정상 assistant)은 11:29:07Z 이후 없다.
  - 메인 시스템 줄: `retry 1/20` → `recovered after 1 attempt(s)` → `scheduling up to 20 retries`가 11:32Z부터 12:36Z까지 14회 반복된다.
- 원인:
  - `classify_fork_transcript`는 꼬리의 마지막 assistant/user 기록만 분류했다. 재시도 메시지가 붙으면 마지막 기록이 user라서 `None`을 반환했다.
  - 모드의 재시도 타이머(`armForkApiRetryTimer`)와 `parallelProcessForkApiRetries`는 오류가 없으면 회복으로 보고 체인을 지웠다.
  - 다음 오류에서 새 체인이 `attempts:0`으로 시작했다. 그래서 20회 상한과 exit contract(host_salvage)에 끝내 도달하지 못했다.
- 수정:
  - 분류기: 마지막 재시도 가능 오류 뒤에 user 기록만 있으면 `retryable:false, awaiting:true`로 같은 오류와 진행 uuid를 돌려준다. 새 assistant 진행 기록이 있으면 지금처럼 결과가 없다(회복).
  - 모드: `awaiting`이고 진행 uuid가 같으면 체인과 시도 횟수를 유지한다. 한 창(1분 또는 10분)은 답을 기다리고, 두 번째 무응답 창은 다음 시도로 센다. 그래서 응답이 없어도 20회 상한이 지켜진다.
- 실패 테스트 먼저:
  - `test_a_retry_message_after_the_error_is_awaiting_not_recovered`(Python). 수정 전에는 `None`이었다.
  - `test_fork_api_retry_counts_attempts_while_the_fork_takes_each_retry`(하네스 `fork-api-retry-counts-through-awaiting`). 수정 전에는 `scheduling` 3회와 `recovered` 3회가 KOBE 패턴 그대로 나왔다.
  - `test_a_fork_that_never_answers_its_retry_still_reaches_the_bound`(무응답 두 번째 창 = 시도 2/20).

# 검증

- 단위: `test_work_queue_dispatch_order.py` 4건 통과. `test_work_mod_write_gate.py` 173 실행/0 실패/33 skip(Windows).
- `claude plugin validate` 통과, `claude plugin test ciel-work` 94 통과.
- 영향 회귀(Windows 격리·py310·py311, `combo.125354`): 세 환경 모두 968 실행/0 실패(`test_work_mod_real_backend` 120건 포함).
- 최종 트리(`final.135143`, 아래 기존 결함 수정 포함):
  - CI `python-minimum` 상당(py310 unit shard 1–3, `--exclude test_work_mod_real_backend.py`): shard 1 1777 실행 EXIT 0, shard 3 1833 실행 EXIT 0.
  - shard 2는 첫 실행에서 `test_recovery_503_retries_within_the_requests_remaining_allowance` 1건이 실패했다(`5 != 2`, 복구 재시도가 한 번도 일어나지 않음). 같은 컨테이너에서 실행 5개가 동시에 돌던 중이었다.
    - 이 테스트와 관련 소스(`transient_retry.py`, `claude_router.py`, `upstream_retry.py`)는 이번 변경에 없다(`git diff HEAD` 비어 있음).
    - 단독 재현 시도: 최종 트리와 HEAD 각 10회 모두 OK. 최종 트리 shard 2 단독 재실행 1628 실행 EXIT 0.
    - 원인은 확인하지 못했다. 고정 횟수 예산은 첫 오픈부터 벽시계 60초 기한을 쓴다(`DEFAULT_SECONDS`, `_schedule_wait`). 동시 부하가 이 기한에 닿았을 가능성이 있지만 측정하지 않았다(미확인).
  - CI `test` 그룹 상당(py311): router 1686, channel 628, runtime 460 실행, 모두 EXIT 0.
  - 영향 파일 41개, Windows 격리·py310·py311 각각: 40개 파일 1303 실행/0 실패. `test_work_mod_real_backend` 결과는 아래에 적는다.

# 배포 전 검사에서 함께 고친 기존 결함 (HEAD 2570cd2에서도 재현)

CI 단위 묶음 3개를 py310 컨테이너에서 HEAD와 현재 트리 양쪽으로 돌려 비교했다. 아래 실패는 HEAD에서도 똑같이 났다. 즉 fcce6d7부터 있던 결함이다. 직전 배포 Publish 45160770706은 beta 브랜치라 quality·test·python-minimum이 skip돼 통과로 보였다.

- `test_architecture_budget`, `test_runtime_compaction_binding`:
  - `ciel_runtime.py`가 4986줄로 예산 4980을 넘었다.
  - `RouterThreadingHTTPServer` 서브클래스(c35b735)는 `RouterServerRuntime._server_factory_with_backlog`와 같은 일을 중복으로 했다. 기본 `ThreadingHTTPServer`를 넘겨도 런타임이 listen backlog를 256으로 올린다(실측: 인스턴스 `request_queue_size` 256). 그래서 서브클래스를 지우고 fcce6d7의 한 줄 인수를 합쳤다. 현재 4980줄이다.
- `test_architecture_budget`:
  - `work_parallel_mcp.py`가 5179줄로 파일 예산 4999를 넘었다.
  - 동작 변경 없이 두 덩어리를 옮겼다. 질문 대기 블록(`_question_detail`…`_worker_question`)은 `work_parallel_question.py`로, 포크 식별 조회(`_fork_attribution`, `_fork_identity`, `_launch_rows`)는 `work_parallel_fork_lookup.py`로 갔다. 현재 4899줄이다.
- `test_architecture_contracts.test_support_module_import_graph_is_acyclic`:
  - fcce6d7의 `work_parallel_p0._ledger_lease_states`가 `work_parallel`을 import해서 순환이 생겼다.
  - 원장은 원자적 교체로 쓰이므로, 공용 `PARALLEL_JSON_CACHE`를 쓰거나 파일을 한 번 읽는 것으로 바꿨다.
- `test_work_settings_cli` 5건:
  - 픽스처 원장에 `leases`가 없어 설정 목록의 고아 리스 행이 `KeyError`를 냈다. 실제 원장에는 항상 `leases`가 있다.
  - 픽스처를 실제 원장 형태로 고쳤다.
- `test_wait_boost_sidecar` manifest, `test_work_parallel_superseded_visibility`: 처음 스냅샷에서 저널 파일을 뺀 탓이다. 저널을 포함한 스냅샷에서는 통과한다(제품 결함 아님).
- 실엔진 드라이버의 스크래치 정리가 읽기 전용 git 객체에서 실패했다. 쓰기 권한을 풀고 다시 지우도록 고쳤다.

# 실엔진 로드 (Claude Code 2.1.330, 격리 프로필, 실제 자격 증명 없음)

- [evidence/real-load-1/result.json](evidence/real-load-1/result.json): 15/15 검사를 통과했다.
  - `failed to load` 0건, 모드 로드와 재로드, 이벤트 2건 처리, 교정 첫 시도 전달을 확인했다.
  - 스냅샷 해시: `register.js` 9438c5be…, `side-intake.js` 2c4d1a73…, `texts.generated.js` 5da9874f….
  - 라이브 프로필은 바뀌지 않았다.

# 라우터 실기동 (`ciel_runtime.py` 서버 클래스 제거 확인)

실엔진 드라이버는 스텁 MCP/API 서버만 띄우므로 라우터 Python 변경을 거치지 않는다. 그래서 최종 트리의 실제 라우터를 따로 띄웠다(`.local/tmp/monitor/router_start_check.py`).
- 조건: `CIEL_RUNTIME_*` 제거, `CIEL_RUNTIME_TEST_ISOLATED=1`, temp 프로필·포트·HOME, 자격 증명 없음. 운영 라우터와 프로필은 건드리지 않았다.
- 절차: `python ciel_runtime.py serve`, `/health` 200 대기, 원시 연결 200건과 `/health` 64건을 동시에 보낸다.
- 결과(Windows py3.11.6, Linux py3.10.22, py3.11.18 모두 같음):
  - 시작 줄이 나왔고 `/health`는 200, pid가 일치했다.
  - 연결 200/200, `/health` 64/64 성공. 버스트 뒤에도 프로세스가 살아 있었다.
  - 트레이스백 0, temp 정리 완료.
- 증거: [evidence/router-start/](evidence/router-start/)

# 미결 (이번 수정 범위 밖, 증거 수집 중)

- 오래된 대기(`waiting_on`): 기록 시각·주체·해제 근거가 없다. 대상 노드가 사라진 대기 3건(n1021→n1020, n1290→n1248, n2400→n2389), done 노드에 남은 대기 1건(n2529), 호스트 리스 정리 대기(n2296, n1308–n1310, n1398–n1402)와 11-09~11-11부터 `submitted`로 남은 리스 20건(11건은 아이템 없음)이 있다. 호스트 기계 감사와 side 판정 강제는 계획 1·3단계로 남는다.
- side 게이트 재확인이 "미조회"로 답하고 호스트가 그대로 받아들인 문제(11:22:09Z): 계획 3단계.
- 재시작 직후 n2594 재실행이 모드 시작보다 약 60초 늦은 이유: 엔진 debug 로그가 없어 미확인.
