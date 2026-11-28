# CLI Reference — CLI 커맨드 전체 참조

> 소스: `ciel_runtime.py` — `main()`, `cmd_*` 함수들  
> 메인 진입점: `ciel-runtime`, `ciel-runtimectl`

---

## ciel-runtime

기본값은 Claude Code를 Router를 통해 실행한다. 다른 런타임은 명시적으로 선택한다.

```bash
ciel-runtime [OPTIONS] [-- RUNTIME_ARGS...]
ciel-runtime codex [CODEX_ARGS...]
ciel-runtime zcode [ZCODE_ARGS...]
ciel-runtime --ca-runtime codex -- exec "hello"
```

### 주요 옵션

| 옵션 | 설명 |
|------|------|
| `--ca-provider <name>` | 사용할 제공자 설정 후 실행 |
| `--ca-model <id>` | 현재 제공자 모델 설정 후 실행 |
| `--ca-base-url <url>` | 현재 제공자 기본 URL 설정 후 실행 |
| `--ca-api-key <key>` | 현재 제공자 API 키 설정 후 실행 |
| `--ca-runtime claude\|codex\|codex-app-server\|codex-desktop\|codex-remote\|agy\|grok\|zcode\|muse` | 실행 런타임 선택 (`codex-desktop`: [Codex-Desktop.md](Codex-Desktop.md), `codex-remote`: [Codex-Remote.md](Codex-Remote.md)) |
| `--ca-no-launch` | 설정만 적용하고 런타임 실행 생략 |
| `--` | 이후 인자를 선택된 런타임에 그대로 전달 |

### Codex 실행

```bash
ciel-runtime codex
ciel-runtime codex --no-alt-screen
ciel-runtime --ca-provider ollama --ca-runtime codex -- exec "작업 내용"
```

Codex 런처는 `~/.codex/config.toml`을 수정하지 않는다. 실행 시점에 `-c model_providers.ciel-runtime...` 오버라이드를 붙여 로컬 Router를 OpenAI Responses provider로 등록한다.

### ZCode 실행

```bash
ciel-runtime zcode
ciel-runtime --ca-runtime zcode -- --continue
ciel-runtimectl launch-zcode --prompt "작업 내용"
ciel-runtime zcode login --oauth --profile coding-plan
ciel-runtime zcode login --oauth --profile start-plan
```

ZCode는 워크스페이스별 Ciel 관리 홈과 `ZCODE_STORAGE_DIR`을 사용한다. 선택된
provider/model은 로컬 Anthropic Router 설정으로 투영되며 사용자의 일반
`%USERPROFILE%\.zcode` 또는 `~/.zcode` 설정은 덮어쓰지 않는다. `ciel-runtime
zcode login --oauth`와 API-key 패널의 Z.AI OAuth는 별도 `zai-coding-plan`
profile에 Coding Plan 키를 저장하므로 다른 routed runtime도 같은 키를 사용한다.
기존 `zai` 수동 키는 변경하지 않는다.

---

## ciel-runtimectl

설정 및 Router 제어 CLI.

```bash
ciel-runtimectl <subcommand> [args]
```

---

### 샌드박스 라우터 복구

```bash
ciel-runtimectl restart-router --reason "Work MCP not responding"
```

대상 워크스페이스의 샌드박스 실행 계정에서 호출합니다. 등록된 런처가 소유한 라우터만 교체하며 Claude 세션은 재시작하지 않습니다. 호출 계정에 라우터 확인·제어 권한이 없으면 실패합니다. 다른 계정의 ACL을 변경하거나 강제 종료하는 명령이 아닙니다. 교체 성공은 새 PID와 health 응답으로 확인하고, Work MCP 및 포크 재개 여부는 별도로 확인합니다.

### Work · SSE 설정

```bash
ciel-runtimectl work list --json
ciel-runtimectl work status [--session claude:<uuid>] [--json]
ciel-runtimectl work get work_mode
ciel-runtimectl work set work_mode on
ciel-runtimectl work set sse_injection_mode btw_with_mcp
ciel-runtimectl work set work_side_intake_max_active 2
ciel-runtimectl work set work_side_intake_priority_policy adaptive
ciel-runtimectl work set work_side_intake_session fresh
```

초기 메뉴 `Work & SSE settings`, 실행 중 `/work-mode settings`, 웹 콘솔 Settings 탭
(REST `settings-update`), ctl은 같은 검증기(`work_setting_values`)와 설정 저장소를
사용한다. 키는 저장 전에 검증한다. 스위치는 어디서나
`on/off/true/false/yes/no/1/0/enable(d)/disable(d)`를 받고 `default`/`unset`은 저장값을
지워 기본값으로 되돌린다(`work_plan_approval_ask`는 `ask/auto`도 받는다). 숫자는 검증기
범위의 정수 전부를 받는다(메뉴는 기타/Edit로 입력). `list`는 승인·자동 계속·보존·병렬
실행·탐색·SSE·사이드 설정 전체를 열거한다. 병렬 원장을 읽을 수 없어도 모든 키가 남고
원장 값은 `status: unreadable`로 표시된다.

`--json` 값은 타입을 유지한다(boolean·정수·문자열, reply connector는 배열).
`status`에는 `operational.device`(원장 version, 기기 용량 = P1~P5 한도 합, 사용 중 슬롯,
우선순위별 사용량)와, `--session`을 주면 `operational.session`(대기 리뷰·stranded·
superseded·legacy 리뷰·멈춘 rework 시작·불확실한 wake/재추가/알림·응답 outbox 상태별
개수·review delivery 상태·수신 건수)이 붙는다. 개수와 상태만 내보내며 본문은 내보내지 않는다.

`sse_injection_mode`는 하나의 실효값이다. 사이드 레인이 켜져 있고 기본 레인이
`btw_with_mcp`일 때만 `btw_with_mcp`, 그 밖에는 `session`이다. 설정하면
`btw_with_mcp`는 `{enabled: true, type: btw_with_mcp}`, `session`은
`{enabled: false, type: btw_with_mcp}`를 저장한다(콘솔 `/btw_with_mcp on|off`와 같은
바이트). 메뉴·ctl·REST(`settings-update`)·`/work-mode settings` 모두 이 키를 받는다.
`work_side_intake_type`은 호환용 원시 키다.

#### 운영자 조회와 조치

웹 콘솔의 운영자 조회·조치는 ctl에서도 같다. ctl은 라우터가 마운트하는 것과 같은
`WorkHttpController`를 이 프로세스에서 만들어 같은 REST 경로
(`/ca/work/api/v1/...`, `/ca/work/api/v1/commands/<명령>`)를 호출한다. 검증·버전 고정·
멱등성·감사는 한 구현에서 나온다. 라우터는 자기 `compaction-scope.json`의 대화만
제공하므로(다른 세션은 422) 닫힌 대화의 리뷰를 처리하려면 이 방식이어야 한다.
조치는 대상 대화를 `--session claude:<uuid>`(또는 `codex:<id>`)로 정확히 지정해야 한다.
작업공간은 ctl을 실행한 현재 디렉터리이며 다른 작업공간 옵션은 없다(그 작업공간 설정을 함께 읽으므로 대화는 자기 작업공간에서만 조작한다). 이 작업공간에 Work 보드나 사이드 접수함이 없는 `--session`은 어떤 쓰기보다 먼저 거부된다.

```bash
ciel-runtimectl work sessions [--json]                 # 신원·마지막 활동·개수만, 내용 없음
ciel-runtimectl work reviews --session S               # 대기/stranded/superseded/legacy 리뷰, rework 시작, 실패
ciel-runtimectl work launches --session S              # 리뷰별 rework/복구 시작 상태(launch_ref, 멈춤 여부)
ciel-runtimectl work capacity --session S              # 기기 용량·사용 슬롯·정책·포크 그룹 수·fork gate
ciel-runtimectl work receipts|handoffs|notices|review-delivery|audit --session S
ciel-runtimectl work receipt RECEIPT --session S
ciel-runtimectl work orphan-preview --session S
ciel-runtimectl work review-approve LEASE [EVIDENCE] --session S --confirm
ciel-runtimectl work review-rework LEASE "필요한 변경" --session S
ciel-runtimectl work launch-resolve LEASE --session S --confirm
ciel-runtimectl work legacy-accept LEASE --session S --confirm
ciel-runtimectl work orphan-release LEASE --session S --confirm [--preview-digest D]
ciel-runtimectl work orphan-reconcile --session S --confirm [--preview-digest D]  # 시작하지 못한 예약 기록 정리(백업 후)
ciel-runtimectl work wake-retry EVENT --session S --confirm
ciel-runtimectl work reappend-retry EVENT --session S --confirm
ciel-runtimectl work review-reappend-retry LEASE --session S --confirm
ciel-runtimectl work notice-retry RECEIPT --session S --confirm
ciel-runtimectl work reply-retry RECEIPT --session S [--confirm]
ciel-runtimectl work side-review-ok RECEIPT --session S
ciel-runtimectl work side-review-corrected RECEIPT "무엇이 틀렸고 어떤 정정을 보냈는지" --session S
ciel-runtimectl work handoff-ack EVENT ["source로 보낼 답"] --session S [--activate] [--clear-wait WAIT]
ciel-runtimectl work side-drain [LIMIT] --session S
ciel-runtimectl work side-discard-failed [CATEGORY] --session S --confirm
ciel-runtimectl side discard-failed [CATEGORY] --session S --confirm   # 위와 같은 조작
ciel-runtimectl work operation-reconcile OPERATION --session S --confirm
ciel-runtimectl work operation-release OPERATION --session S --confirm [--expected-version N]
ciel-runtimectl work execute [ROOT] --session S --confirm [--expected-version N]   # ROOT: 줄을 선 목표 하나만 승인
ciel-runtimectl work workflow [ROOT] --session S        # 승인 워크플로우: 단계·담당·차단 사유·제안서·승인·QA·재작업·메인 할 일
ciel-runtimectl work workflow-prompts|workflow-metrics --session S
ciel-runtimectl work workflow-resolve ROOT resend|cancel|escalate_main|escalate_model=MODEL --session S [--confirm]
ciel-runtimectl work workflow-delegate ROOT proposal|review --session S
ciel-runtimectl work prompt-set NAME "새 프롬프트" --session S
ciel-runtimectl work prompt-reset NAME --session S
```

| ctl 조치 | REST 명령 | 확인 |
|---|---|---|
| `review-approve` / `review-rework` | `parallel-item-review` | approve만 `--confirm` |
| `launch-resolve` | `parallel-launch-resolve` | `--confirm` |
| `legacy-accept` | `parallel-legacy-accept` | `--confirm` |
| `orphan-preview` / `orphan-release` | `parallel-orphans-preview` / `parallel-orphans-apply` | release는 `--confirm` |
| `orphan-reconcile` | `parallel-orphans-reconcile` | `--confirm`(preview의 `reconcile.digest`에 고정, 먼저 백업) |
| `wake-retry` / `reappend-retry` | `side-wake-retry` / `side-reappend-retry` | `--confirm`(중복 가능) |
| `review-reappend-retry` | `review-reappend-retry` | `--confirm`(중복 가능) |
| `notice-retry` | `side-notice-retry` | `--confirm`(중복 가능) |
| `reply-retry` | `side-reply-retry` | `uncertain`/`submitting`만 `--confirm`(중복 가능) |
| `side-review-ok` / `side-review-corrected` | `side-review` | 없음(corrected는 메모 필수) |
| `handoff-ack` (시간 제한 handoff 포함) | `side-handoff` | 없음 |
| `side-drain` | `side-drain` | 없음 |
| `side-discard-failed` | `side-discard-failed` (= `POST /ca/work/api/v1/side/discard-failed`) | `--confirm` |
| `execute [ROOT]` | `execute` (보드 명령, 웹 Execute와 같음. ROOT를 주면 `root_id`) | `--confirm`(확인 전에 대상 목표 root·제목·phase·approval 표시) |
| `operation-reconcile` | `operation-reconcile` | `--confirm` |
| `operation-release` | `operation-release` | `--confirm`(효과가 이미 일어났을 수 있음, 재시도는 중복 가능) |
| `workflow-resolve` | `workflow-resolve` | `cancel`·`escalate_*`만 `--confirm` |
| `workflow-delegate` | `workflow-delegate` | 없음 |
| `prompt-set` / `prompt-reset` | `workflow-prompt-set` / `workflow-prompt-reset` | 없음(방금 읽은 지문에 고정, 그 사이 바뀌면 409 `prompt_conflict`) |

승인 워크플로우(`work_workflow_enabled`, 기본 꺼짐)의 조회와 조치는 웹 콘솔 Workflow 탭,
REST(`GET /ca/work/api/v1/workflow`, `/workflow/prompts`, `/workflow/metrics`), 메뉴의
`Approval workflow status and actions`·`Approval workflow stage prompts` 행, 세션 안의
`/work-mode workflow`와 같은 코드다.

`operation-reconcile`·`operation-release`는 `work audit`에서 결과가 `unknown`인 운영자
영수증 하나를 정리한다. 대상은 그 영수증의 `operation_id`다(전체 또는 8자 이상의 고유한 앞부분). reconcile은
그 명령이 쓰는 저장소를 읽기만 해서 효과가 적용됐는지 확인하며 아무것도 다시 실행하지 않는다
(`applied` → 적용됨, `not_applied` → 새 키로 재시도, `not_observable` → `unknown` 유지). release는
reconcile이 `not_observable`을 기록한 뒤에만 되며, 방금 읽은 `receipt_version`에 고정된다(목록 밖
영수증은 `--expected-version`으로 준다). 결과는 `released_unverified`이고 효과는 다시 실행되지 않는다.
이 두 명령 자체는 영수증이 아니라서 `Idempotency-Key`와 `--operation-id`는 쓰이지 않고, 128개 영수증이 모두
`prepared`여서 새 명령이 `operator_receipts_full`로 거부될 때도 실행된다. 거부 코드:
`unknown_operation`, `operation_not_unknown`, `reconcile_required`, `receipt_changed`.

`side-discard-failed`는 실패한 사이드 영수증을 모두 폐기한다(카테고리를 주면 그 카테고리만, 예:
`source_context`). 대상은 아직 처리 중이 아닌(lease 없음) 실패 영수증이며, 각각 `quarantined`·
`operator_discarded`가 되고 재시도되지 않으며 원문은 메인으로 전달되지 않는다. 영수증 파일은 감사용으로
남는다. 결과는 `discarded`, `skipped_leased`(사이드 액터가 지금 잡고 있는 것), `already_terminal`
개수다. 같은 조작: Claude 세션의 `/work-mode discard-failed [category]`(모델 턴 없음), 런처 메뉴의
사이드 설정 → *실패한 사이드 영수증 폐기*(이 워크스페이스의 모든 세션), 웹 콘솔 Side 화면의
*Discard failed receipts* 버튼, `POST /ca/work/api/v1/side/discard-failed`.

`execute`는 웹 콘솔의 Execute 버튼과 같은 보드 명령(`execute`, `user_confirmed: true`)이다.
`approval=ask`에서 Work 대화상자가 없는 대화(Codex 등)의 계획 실행을 사람이 승인하는 경로다.
`--confirm` 없이 실행하면 활성 계획의 root id·제목·phase·approval과 보낼 요청을 보여 주고 종료
코드 3으로 끝난다. 요청은 방금 읽은 보드 버전(`GET /state`의 `version`)에 고정되며 그사이 계획이
바뀌면 `version_conflict`(종료 코드 1)이다. 준비되지 않은 계획은 REST 검증기의 이유와 함께 거부된다.
ROOT를 주면 활성 목표는 그대로 두고 줄을 선 그 목표 하나만 승인한다(보드 명령 `execute`의 `root_id`, MCP `work_execute`의 `root_id`와 같음). 승인된 목표의 단계는 우선순위별 병렬 한도 안에서 큐 순서대로 포크에 배정된다.

`work status --session S`와 `work capacity --session S`는 `GET /parallel`의 `fork_gate`
(`state`: `loaded`·`not_loaded`·`unknown`, `reason`, `gate_version`, `observed_at`, `reported_at`, `message`)와
`gate_presence_version`을 그대로 보여 준다. `message`가 null이 아니면 그 문장을 그대로 `warning:` 한 줄로
먼저 출력한다(`--json`이면 `warnings` 배열). 예: `Work fork gate not loaded: parallel execution disabled`,
보고가 아직 없으면 `Work fork gate presence not reported by this conversation's Work host`. 표시만 하며
ctl은 이 상태로 어떤 조치도 거부하지 않는다.

`work capacity --session S`와 parallel 리뷰 조회는 `GET /parallel`의 `wait_boost` 블록도 보여 준다.
SSE 사이드가 기록된 대기를 해제한 항목이 다음 슬롯을 기다리는 상태이며, 필드는 `eligible`,
`behavior`(`launch_first_eligible`·`parked_first_eligible`·`consuming`), `reason`(`none`·`unavailable`),
`ordinary_allowed`, `item_id`, `root_id`, `priority`뿐이다. lease·host·영수증·대기 문구는 포함하지 않는다.
해제된 대기는 P0 다음, 일반 P1–P5보다 먼저 그 우선순위의 다음 빈 슬롯에서 실행된다. 실행 중인 작업을
선점하지 않고, 저장된 우선순위를 바꾸지 않으며, 실행 승인도 아니다. `parked_first_eligible`이면 그
우선순위의 슬롯 하나가 비워진 채로 유지되고, 메인이 SendMessage로 그 워커를 재개할 때 소비된다.

`--confirm`은 웹 콘솔이 위험 확인 대화상자를 띄우는 조치에만 필요하다. 없으면 콘솔과 같은
경고와 보낼 요청을 출력하고 아무것도 바꾸지 않은 채 종료 코드 3으로 끝난다. 거부·버전
충돌·검증 오류는 Python traceback 없이 한 줄(`--json`이면 JSON 객체 하나)과 종료 코드 1이다.
대상 id는 조회 화면의 전체 id 또는 8자 이상의 고유한 앞부분이다. 지금 목록에 없는 전체 id는
ctl이 거르지 않고 그대로 보내 REST 검증기가 판단한다(이미 결정된 리뷰의 재시도 등). 버전·참조
(`expected_version`, `expected_finalization_version`, `launch_ref`, `attempt_ref`,
`revision`, `preview_digest`)는 웹 콘솔처럼 방금 읽은 조회에서 채우며, 이전 조회에 고정하려면
`--expected-version`, `--expected-finalization-version`, `--attempt-ref`, `--revision`,
`--preview-digest`로 지정한다. 바뀌었으면 거부된다(다시 조회 후 재실행).
`review-approve`·`review-rework`는 조회한 행에 `review_pin`(`rp1:` + 64 hex)이 있으면 그것을
`expected_review_pin`으로 함께 보낸다(`--expected-review-pin`으로 이전 조회의 값을 줄 수 있다).
pin이 행과 같으면 다른 리뷰나 wake 기록 때문에 버전이 바뀌어도 결정이 거부되지 않고, pin이
없으면 `expected_finalization_version` 규칙이 그대로 적용된다. 같은 조치를
멱등하게 재시도하려면 `--operation-id`를 같은 값으로 주고, 응답 `request`에 기록된 고정값
(예: `expected_finalization_version`)도 같은 값으로 준다. 서버는 요청 전체의 digest로 재실행을
판정하므로 같은 키로 다른 요청을 보내면 `idempotency_conflict`(종료 코드 1)이다. 키를 주지 않으면
`ctl-<uuid>` 키가 붙는다. 모든 성공한 조치는 세션 감사 기록(`work audit`)에 남는다.

고아 lease 해제는 메뉴·ctl·웹 모두 고정된 미리보기 하나에 대해 lease 하나씩만 해제한다.
미리보기 digest가 바뀌었으면(다른 lease·소유자 관측·원장 version 변경) 아무것도
해제하지 않는다. 메뉴의 선택지는 각각 그 미리보기의 digest를 담고 있으며 일괄 `apply`는
없다.

SSE 이벤트 하나는 **네이티브 `fork` 하나**로 처리한다. 포크는 메인 세션의 모델·
대화 문맥·도구·권한 모드를 그대로 상속한다. 별도 사이드 모델, 등록된 사이드 agent
타입, 라우터 부모 문맥 투영(`isolated`/`full`/`recent`, 문맥 비율)은 2026-11-11에
제거했다. 이전 빌드가 저장한 `model`·`context_mode`·`context_percent` 값은 디스크에
남아도 무시되며 `work set`으로 다시 설정할 수 없다. 이벤트 프롬프트에는 현재 Work 보드
전체(활성 작업·큐 순서·계층·상태·우선순위·대기·메모·근거·버전)를 전달한다.

사이드 포크의 제한은 **프롬프트 정책만**이다(운영자 결정, 기술적 도구 차단 없음).
이벤트 프롬프트는 파일·코드·설정 쓰기, Bash/PowerShell, 빌드·테스트·배포, 런타임·
설정 제어, Work 변경, Agent 호출, source로의 직접 ACK·응답·전송을 금지하고 결정 JSON
하나만 반환하도록 요구한다. 도구 호출은 메인의 호출과 같은 경로(Work 게이트·네이티브
권한)를 탄다. 결정은 host가 원자적으로 커밋하고 host outbox가 정확히 한 번 source
reply를 보낸다. 중복 제거, `work_side_intake_max_active`, SSE 사이드 포크의 P1 예약
슬롯(동시 사이드 포크가 `max_active`에 도달해도 P1 이벤트용으로 1개를 더 허용)은 그대로다.
Work 병렬 실행 원장에는 P1 예약 슬롯이 없고 P1~P5별 동시 한도만 있다.

Walkie 이벤트는 포크를 띄우기 전에 host가 정규 thread root를 검증해 읽는다.
root는 `data.thread_root_id`, `message_context.thread_task.root_id`, 대상 메시지 id 순으로
결정한다. root는 `walkie_read_messages(after=root-1)`로 읽고, reply는
`walkie_call_tool(operation_id:"threads.read")`로 200개씩 끝까지 페이지한다. 대상이
root 또는 검증된 reply에 있어야 하며, reply id는 오름차순이고 `thread_root_id`가 root와
같아야 한다. 명시적 null 계보는 root sentinel로 보존한다. 본문은 자르지 않고 인증정보
형태의 키만 가린다. 로컬 531개 thread 실측 최대(113행·341,233바이트)를 근거로 최대
8페이지·1,601행·384KiB이며 읽기 실패·대상 없음·형식/계보 오류·
초과는 `source_context` 실패로 원문을 보존하고 포크/응답을 만들지 않는다. 메시지나
thread 참조가 전혀 없는 Walkie 이벤트만 `source_context:none_referenced`로 이벤트 본문과
메인 문맥을 사용해 포크한다.

`work_side_intake_reply_connectors`(메뉴: External reply route / Walkie preset)는
신뢰된 상관관계가 있는 웹 채팅을 원래 채널로 자동 전달하고, 일반 외부
SSE/CloudEvent는 운영자가 exact source/receiver_id에 대한 outbound route를 등록한다.
Walkie는 `{source, receiver_id, preset:"walkie", server}`를 쓴다. Walkie는 대화마다
CloudEvent source가 다르므로(`.../conversations/<uuid>`) 프리셋에 한해 `source:"*"`로
그 수신기의 모든 대화를 덮을 수 있다. 보낼 대화와 메시지는 이벤트의 형식 검사된
`data.conversation`/`data.message_id`에서만 정한다. 그 외 source는 기존
server/tool/arguments 템플릿을 쓰며 `"*"`를 쓸 수 없다.
템플릿 route의 `$reply`, `$event_id`, `$receiver_id`, `$source`, `$kind`,
`$receipt_id`는 전체 문자열 값일 때 치환된다. 고정 목적지는 운영자가 arguments에
설정하고 인증정보는 MCP 연결에만 둔다. 이벤트 본문은 목적지나 도구를 정하지 않는다.
외부 이벤트에 일치하는 재정의가 없으면 `route_unavailable`이며 로컬 로그 저장을
외부 전송으로 표시하지 않는다.

응답 패널의 `Retry reply…`는 실패한 응답을 명시적으로 재시도한다.
`uncertain`/`submitting`은 이미 도착했을 가능성이 있으므로 중복 전송 경고에
동의해야 한다. 자동 재전송하거나 성공으로 처리하지 않는다.

### 제공자/모델 설정

#### `provider`
```bash
ciel-runtimectl provider [NAME]
```
현재 활성 제공자 조회 또는 변경.

```bash
ciel-runtimectl provider ollama
ciel-runtimectl provider anthropic
ciel-runtimectl provider deepseek
```

#### `model`
```bash
ciel-runtimectl model [MODEL_ID]
```
현재 모델 조회 또는 변경.

```bash
ciel-runtimectl model qwen3-coder:30b
ciel-runtimectl model claude-sonnet-4-6
```

#### `models`
```bash
ciel-runtimectl models [PROVIDER]
```
사용 가능한 모델 목록 출력.

#### `base-url`
```bash
ciel-runtimectl base-url [PROVIDER] [URL]
```
제공자 기본 URL 설정.

```bash
ciel-runtimectl base-url ollama http://remote-server:11434
```

#### `provider-options` — Z.AI Start Plan remote CAPTCHA

원격 Linux 호스트에서 Z.AI Start Plan의 사람 검증 페이지를 운영자 브라우저로
열어야 할 때는 접근 가능한 인터페이스만 명시적으로 바인딩한다. 기본값은 계속
`127.0.0.1`이며 외부에 공개되지 않는다.

```bash
ciel-runtimectl provider-options zai-start-plan \
  captcha_bind_host=100.95.132.58 \
  captcha_port=42119 \
  'captcha_public_base_url=http://100.95.132.58:{port}'
```

`captcha_public_base_url`은 `http://` 또는 `https://` origin만 허용하며,
`{port}`는 실제 CAPTCHA 수신 포트로 치환된다. URL에는 요청마다 새로 생성되는
state 값이 추가된다.

---

### API 키 관리

#### `zai-oauth`

```bash
ciel-runtimectl zai-oauth login --profile coding-plan
ciel-runtimectl zai-oauth login --profile coding-plan --no-browser
ciel-runtimectl zai-oauth status --profile coding-plan
ciel-runtimectl zai-oauth logout --profile coding-plan
ciel-runtimectl zai-oauth login --profile start-plan
ciel-runtimectl zai-oauth import --profile start-plan
ciel-runtimectl zai-oauth status --profile start-plan
ciel-runtimectl zai-oauth logout --profile start-plan
```

기본 profile은 `coding-plan`이다. 두 profile의 `login`은 설치된 공식 ZCode CLI의
OAuth init/poll 흐름에 위임한다. Coding Plan은 CLI가 생성한 provider API key를,
Start Plan은 공식 공유 credential store의 `zcodejwttoken`을 각각 가져온다. 이미
ZCode 로그인이 완료된 경우 `import --profile start-plan`은 같은 공유 JWT를
`zai-start-plan` profile에 저장한다. Desktop의 선택된 Start Plan 설정은 이전
ZCode 버전을 위한 import fallback으로 유지된다. Start Plan 모델 요청은 요청별 Aliyun CAPTCHA
결과를 받은 뒤 Start Plan 전용 Anthropic endpoint로 계속된다. `logout`은 선택한
profile의 Ciel credential만 지우며 ZCode의 로그인이나 원격 승인을 철회하지 않는다.

#### `api-key`
```bash
ciel-runtimectl api-key [PROVIDER] [KEY]
```
API 키 설정 (단일 키).

```bash
ciel-runtimectl api-key anthropic sk-ant-...
ciel-runtimectl api-key deepseek sk-...
```

#### `api-keys`
```bash
ciel-runtimectl api-keys [PROVIDER] [KEY1] [KEY2] ...
```
다중 API 키 설정 (로테이션용).

`api-key`·`set-api-key`·`set-api-keys`는 **영구 저장** 키다. 실행 중인 한 세션에만
메모리로 쓰는 키는 아래 `session-api-key`를 쓴다.

#### `session-api-key`
```bash
ciel-runtimectl session-api-key list [--json]
ciel-runtimectl session-api-key inspect --session claude:<uuid> [--provider PROVIDER] [--json]
ciel-runtimectl session-api-key inject  --session claude:<uuid> --provider PROVIDER [--ttl-seconds N]
                                        [--operation-id ID] [--stdin | --key-file - | --fd N] [--json]
ciel-runtimectl session-api-key replace --session claude:<uuid> --provider PROVIDER --expected-version N
                                        [--operation-id ID] [--stdin | --key-file - | --fd N] [--json]
ciel-runtimectl session-api-key clear   --session claude:<uuid> --provider PROVIDER --expected-version N
                                        [--operation-id ID] [--json]
ciel-runtimectl session-api-key reset-state [--stdin] [--operation-id ID]
```
`reset-state`: router-wide; after reset, sessions previously blocked use their stored key.
경고를 출력한 뒤 확인 문구를 받는다. 터미널이면 숨김 프롬프트에 정확히 입력하고, 아니면
`--stdin`으로 표준 입력에 준다. 문구는 명령줄로 받지 않는다(명령줄에 적으면 되풀이하지 않는
고정 문구로 거부하고 종료 코드 2). 문구가 다르면 "Not confirmed; nothing changed."와 종료 코드 1이다.
한 세션과 한 provider에만 쓰는 **메모리 전용** 키다. 디스크에 저장하지 않으며 영구 키
(`api-key`/`set-api-key`/`set-api-keys`)는 바뀌지 않는다. ctl은 이 작업공간의 loopback
라우터에 관리자 토큰으로 요청하며, 출력은 메타데이터(session·provider·source·version·
injected_at·expires_at·fingerprint)뿐이고 키 값은 출력하지 않는다.

키는 **명령줄로 받지 않는다**(프로세스 목록에 남지 않도록). 키를 받는 위치 인자나 옵션은
없고, 다음 중 하나로만 읽는다:
- 터미널이면 숨김 입력 프롬프트(기본)
- `--stdin` 또는 `--key-file -`: 표준 입력 (`--key-file`은 `-`만 받는다)
- `--fd N`: 상속받은 파일 디스크립터

키 출처는 하나만 지정할 수 있다. 이 명령의 인자 오류(알 수 없는 인자, 잘못된 값)는
입력한 값을 되풀이하지 않는 고정 문구로 거부하고 종료 코드 2로 끝난다. 실수로 명령줄에
적은 키가 터미널이나 로그에 다시 찍히지 않게 하기 위해서다. 옵션 축약(`--sess` 등)은
받지 않는다. `replace`와 `clear`는 `inspect`가 보여 준 `--expected-version`이 필요하다.
`--operation-id`(별칭 `--idempotency-key`)는 REST의 `Idempotency-Key`로 보내며, 같은
id로 재시도하면 한 번만 적용된다. 라우터나 vault가 거부하면 `code: message`를 출력하고
종료 코드 1로 끝난다.

```bash
# 숨김 프롬프트
ciel-runtimectl session-api-key inject --session claude:<uuid> --provider anthropic
# 파일에서 표준 입력으로 (bash / PowerShell)
ciel-runtimectl session-api-key inject --session claude:<uuid> --provider anthropic --key-file - < key.txt
Get-Content -Raw key.txt | ciel-runtimectl session-api-key inject --session claude:<uuid> --provider anthropic --key-file -
```

#### `tokens`
```bash
ciel-runtime tokens list [--json]
ciel-runtime tokens login codex|claude [--label L]
ciel-runtime tokens import codex|claude [--from PATH] [--label L]
ciel-runtime tokens refresh ID
ciel-runtime tokens enable ID | disable ID | remove ID
```
워크스페이스별 OAuth 토큰 관리소. Codex routed 모드와 Anthropic routed 모드에서만 쓰인다.
저장소는 `workspaces/<id>/oauth-tokens.vault.json`(암호화, 키 `oauth-tokens.vault.key` 또는
`CIEL_RUNTIME_SECRET_MASTER_KEY`)과 `oauth-tokens.state.json`(사용량·상태·대화 고정)이다.

- `login`: 브라우저 PKCE 로그인으로 계정마다 새 세션을 받는다(`prompt=login`). CLI 자신의 로그인과 갱신 계보가
  분리된다. Codex는 `localhost:1455`, Claude는 빈 포트의 `localhost` 콜백을 쓴다.
- `import`: `~/.codex/auth.json`(`CODEX_HOME`) 또는 `~/.claude/.credentials.json`(`CLAUDE_CONFIG_DIR`)을 가져온다.
  CLI가 같은 토큰을 계속 갱신하므로 한쪽이 무효화될 수 있다(Codex refresh 토큰은 1회용).
- 선택 규칙: 대화(Codex `prompt_cache_key`, Claude `x-claude-code-session-id`)는 사용량이 남아 있는 동안 같은 토큰을
  유지한다. 응답 헤더(`x-codex-*-used-percent`, `anthropic-ratelimit-unified-*-utilization`)의 사용률이
  `CIEL_RUNTIME_OAUTH_ROTATE_PERCENT`(기본 95)에 닿으면 다음 사용자 턴에서 다른 토큰으로 넘어가고, 도구 루프 안에서는
  바꾸지 않는다. 사용 한도 429는 출력 전에 다음 토큰으로 재시도하고, 그 토큰은 응답의 리셋 시각까지 빠졌다가 복구된다.
  새 대화는 저장 순서대로 한도에 닿지 않은 첫 토큰을 쓴다.
- 갱신: 라우터 감시 스레드(60초)가 만료 10분 전에 갱신하고, 401을 받으면 한 번 갱신 후 재시도한다. 끝내 거부되면
  CLI에는 401 대신 424를 돌려준다. refresh 토큰이 폐기되면 `refresh_failed`가 되어 다시 로그인해야 한다.
- Anthropic에 API 키가 설정돼 있으면(`x-api-key`) 그 키를 그대로 쓰고 관리소 토큰은 쓰지 않는다.

---

### Router 관리

#### `bridge`

Router가 실행되는 호스트와 실제 Claude Code/Codex CLI가 실행되는 호스트를
네트워크로 분리한다. 원격 클라이언트는 하나의 인증된 OpenAI/Anthropic 호환
endpoint를 사용한다. 요청마다 구성된 bridge-compatible provider와 upstream model
ID를 선택하며, 허용하는 provider에 한해 request-scoped API key를 지정할 수 있다.

```bash
ciel-runtimectl bridge status
ciel-runtimectl bridge enable --host 0.0.0.0
ciel-runtimectl bridge token
ciel-runtimectl bridge serve --host 0.0.0.0
ciel-runtimectl bridge disable
```

`enable`과 `serve`는 클라이언트 설정을 위해 token 값을 출력한다. `status`는
값을 출력하지 않고 존재 여부만 표시하며, `token`은 값을 명시적으로 출력한다.
Bridge는 별도 고정 포트를 사용하지 않으므로 `status`의 `Listen:`에 표시되는
현재 `ROUTER_PORT`를 클라이언트 base URL에 사용한다.

Bridge token은 Router debug/Web 관리자 token과 분리되어 있고 관리자 API를
승인하지 않으며 `CIEL_RUNTIME_REMOTE_BRIDGE_TOKEN`으로 재정의할 수 있다. 관리자
외부 접근을 별도로 활성화한 경우 관리자 token은 bridge endpoint에도 접근할 수
있는 더 넓은 권한이므로 원격 LLM 클라이언트에 배포하지 않는다.

OpenAI Chat 요청은 선택 모델의 실제 protocol이 Responses, Anthropic Messages,
Ollama Chat이어도 호환 변환할 수 있다. 변환된 `stream: true` Chat 응답은 upstream
완료 결과를 수집한 뒤 합성하는 SSE다. Effort는 Chat의 `reasoning_effort`,
Responses의 `reasoning.effort`, Anthropic Messages의 `output_config.effort`로
지정한다.

Codex CLI 0.150.1에서 `vllm`의 Chat wire처럼 Responses가 아닌 upstream으로
변환하는 route를 사용할 때는 hosted `web_search`를 해당 실행에서 꺼야 한다.
이 도구는 대상 wire에 손실 없이 투영할 수 없으므로 Bridge가 조용히 제거하지 않고
요청을 거부한다.

```bash
codex -c 'web_search="disabled"' --model vllm/my-model
```

이는 non-native Responses 변환 route에만 해당한다. Native Responses upstream을
선택한 route는 hosted `web_search`를 유지할 수 있다. Codex `namespace` tool은
비-Responses wire로 보낼 때 namespace/member 기반의 충돌 방지 이름으로 flatten되며,
동일한 투영 이름이 생기면 요청을 거부한다. 반환된 tool call은 원래 Responses의
`namespace`와 member `name`으로 복원한다. 반환값에 `toolset_name`이 있으면 원래
요청의 namespace와 정확히 일치해야 하며, 일반 tool에 임의 namespace를 만들 수 없다.
Strict bridge route는 tool type을 exact lowercase discriminator로 요구하고 top-level,
namespace, member 이름의 leading/trailing whitespace를 정규화하지 않고 거부한다.
Adapted Anthropic 요청도 client tool·tool choice·tool-use/result identity를 trim/case
정규화하지 않고 strict 경계에서 거부한다.

Codex 0.150.1의 bundled `gpt-5.5` metadata는 native search tool을 지원한다.
기본 multi-agent 도구가 deferred 상태이면 `web_search`와 별개인 hosted
`tool_search`가 추가되며, non-native route는 이 도구도 fail-closed로 거부한다.
다른 deferred MCP/plugin/app/dynamic tool이 없는 실행의 검증된 최소 설정은 다음과
같다. `features.tool_search=false`는 0.150.1에서 제거되어 무시되는 설정이다.

```bash
codex -c 'web_search="disabled"' \
  -c 'features.multi_agent=false' \
  -c 'model_reasoning_effort="low"' \
  --model vllm/gpt-5.5
```

Responses Lite의 선행 `additional_tools`, `reasoning.context=all_turns`, streaming
summary 순서 옵션과 Codex 내부 turn metadata는 검증 후 변환한다. Non-native
Anthropic route의 freeform custom tool은 Codex 0.150.1 공식 `apply_patch.lark`
(기본/optional Environment-ID 변형) 또는 code-mode `exec` grammar와 정확히
일치할 때만(LF/CRLF source line ending은 정규화)
Anthropic 표준 tool로 투영한다. 반환된 raw input도 해당 문법으로 검증한 뒤
`custom_tool_call`로 복원하며 다른 Lark definition과 잘못된·빈 payload는 거부한다.
Adapted Chat route는 object가 아닌 `function.parameters`와 non-null `message.name`을
손실 변환하지 않고 거부한다.

Request-scoped provider key는 저장하지 않으며 Router-host key의 rate-limit 사용량,
학습된 header, backoff penalty, per-key cooldown 상태와 분리한다. GitHub Copilot
OAuth는 Router 호스트에서만 로그인·갱신하고 원격 key override를 거부한다. Copilot
모델 목록은 `model_picker_enabled: true`만 공개하며 `mai-code-1-flash`를 공개 ID로
사용한다. `codex`, `agy`, `zai-start-plan`은 client-local 인증 또는 host-side
브라우저/CAPTCHA 상태에 의존하므로 Remote Bridge에서 제외한다. 전체
endpoint, route header, Codex 및 Claude Code 설정은
[Remote Runtime Bridge](Remote-Bridge.md)를 참고한다.

#### `transcript-events`

```bash
ciel-runtimectl transcript-events enabled=true url=https://memory.example/v1/transcripts authorization='Bearer {MEMORY_TOKEN}'
```

현재 런타임의 Claude/Codex JSONL 트랜스크립트를 등록한 HTTP(S) 주소로 증분
전송한다. URL과 트랜스크립트별 바이트 커서는 워크스페이스 상태에 저장되며 HTTP
2xx 이후에만 전진한다. `start_mode=tail`은 현재 실행 경계 이후만,
`start_mode=beginning`은 파일 처음부터 보낸다. 설정 확인은 인자 없이 실행한다.

`content_filter`는 보내는 범위를 정한다. 기본값 `public_only`는 PC 안에서 Walkie
relay와 같은 공개용 투영만 만들어 보낸다. 사용자 본문은 Walkie 라우팅 티켓(`wkp_`)만 남기고,
툴은 이름만, assistant는 Codex commentary와 Claude 텍스트만 남기며, 턴 끝마다
`task_complete`를 넣는다. reasoning과 툴 인자·결과는 보내지 않는다. 공개할 내용이 없는
묶음은 요청 없이 커서만 전진한다. `content_filter=raw`를 명시해야 원본 JSONL을 보낸다.
이벤트 `data.content_filter`에 적용된 값이 들어간다.

#### `remote-memory`

```bash
ciel-runtimectl remote-memory
ciel-runtimectl remote-memory enabled=true manifest_url=https://memory.example/manifest.json sync
```

별도 HTTP manifest에서 워크스페이스 메모리 트리를 동기화한다. `sync`는 설정을
저장한 뒤 즉시 한 번 내려받는다. 시작 시에는 선택된 런타임을 실행하기 전에 자동으로
다시 내려받으며, 시스템 지침 파일 하단에는 로컬 인덱스 주소만 추가한다.

주요 키: `enabled`, `manifest_url`, `authorization`, `directory`,
`timeout_seconds`, `max_manifest_bytes`, `max_file_bytes`, `max_total_bytes`,
`max_files`.

전체 manifest 계약은 [Remote Memory](Remote-Memory.md)를 참고한다.

#### `remote-provision`

```bash
ciel-runtimectl remote-provision
ciel-runtimectl remote-provision enabled=true manifest_url=https://provision.example/manifest.json
ciel-runtimectl remote-provision run
ciel-runtimectl remote-provision status
```

세션 시작 전에 HTTP manifest의 파일을 워크스페이스에 두고 스크립트(PowerShell, bash,
Python)를 실행한다. 모든 파일과 스크립트는 sha256이 맞아야 쓰이거나 실행되며, 실패하면
실행이 막힌다. `run`은 세션 없이 바로 한 번 수행하고 실패하면 0이 아닌 코드로 끝난다.
`status`는 마지막 결과와 단계별 종료 코드·로그 경로를 보여준다.

주요 키: `enabled`, `manifest_url`, `authorization`, `timeout_seconds`,
`max_manifest_bytes`, `max_file_bytes`, `max_total_bytes`.

전체 manifest 계약은 [Remote Provision](Remote-Provision.md)를 참고한다.

#### `serve`
```bash
ciel-runtimectl serve [--port PORT]
```
Router를 백그라운드로 시작.

#### `stop`
```bash
ciel-runtimectl stop
# 또는
ciel-runtime-stop
```
실행 중인 Router 중지.

#### `restart`
```bash
ciel-runtimectl restart
```
Router 재시작.

#### `status`
```bash
ciel-runtimectl status
```
현재 설정 및 Router 상태 출력:
- 활성 제공자/모델
- API 키 상태 (마스킹)
- 키 쿨다운 상태
- 컨텍스트 한도
- 채널 상태

---

### 로그 및 진단

#### `log-level`
```bash
ciel-runtimectl log-level [LEVEL]
```
로그 레벨 조회 또는 변경.

```bash
ciel-runtimectl log-level DEBUG
ciel-runtimectl log-level SILENT
```

레벨: `SILENT`, `ERROR`, `WARN`, `INFO`, `DEBUG`, `TRACE`

#### `log`
```bash
ciel-runtimectl log [--tail N] [--follow]
```
Router 로그 출력.

#### `events`
```bash
ciel-runtimectl events [--level LEVEL]
```
실시간 이벤트 스트림 출력.

---

### 기능 설정

#### `language`
```bash
ciel-runtimectl language [CODE]
```
UI 언어 설정.

```bash
ciel-runtimectl language ko   # 한국어
ciel-runtimectl language en   # English
ciel-runtimectl language ja   # 日本語
ciel-runtimectl language zh   # 中文
```

#### `web-search`
```bash
ciel-runtimectl web-search [on|off]
```
웹 검색 MCP 서버 활성화/비활성화.

#### `web-fetch`
```bash
ciel-runtimectl web-fetch [on|off]
```
웹 페치 MCP 서버 활성화/비활성화.

---

### 채널

#### `channel add`
```bash
ciel-runtimectl channel add <SPEC>
```
채널 스펙 추가.

```bash
ciel-runtimectl channel add server:my-mcp-server
ciel-runtimectl channel add plugin:telegram@claude-plugins-official
```

#### `channel remove`
```bash
ciel-runtimectl channel remove <SPEC>
```

#### `channel list`
```bash
ciel-runtimectl channel list
```
현재 설정된 채널 목록.

#### `channel probe`
```bash
ciel-runtimectl channel probe [--refresh]
```
채널 지원 MCP 서버 탐지.

---

### Ollama 전용

#### `ollama-catalog`
```bash
ciel-runtimectl ollama-catalog [--refresh]
```
Ollama 모델 카탈로그 조회 및 갱신.

---

### 기타

#### `advisor-model`
```bash
ciel-runtimectl advisor-model [MODEL_ID]
```
Advisor LLM 모델 설정.

#### `install-statusline`
```bash
ciel-runtimectl install-statusline
```
셸 프롬프트 statusline 설치.

#### `install-diag`
```bash
ciel-runtimectl install-diag
```
설치 진단 실행.

#### `version`
```bash
ciel-runtimectl version
# → 0.1.1
```

---

## ciel-runtime-stop

```bash
ciel-runtime-stop
```

실행 중인 Router 프로세스를 종료한다.  
Windows: `ciel-runtime-stop.cmd` / `ciel-runtime-stop.ps1`  
Linux/macOS: `ciel-runtime-stop`

---

## 환경변수를 통한 일회성 실행

```bash
CIEL_RUNTIME_PROVIDER=deepseek CIEL_RUNTIME_MODEL=deepseek-coder-v2 ciel-runtime
```

---

## 관련 문서
- [[Configuration]] — 설정 파일 구조
- [[Providers]] — 제공자 이름 목록
- [[Installation]] — 설치 방법
