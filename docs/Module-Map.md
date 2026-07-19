# Module Map — 파일별 역할 요약

---

## Python 소스

### `ciel_runtime.py`

메인 호환 엔트리포인트. 라우터 조정, 제공자 설정, CLI 및 레거시 변환 로직을 포함한다.
파일 크기와 줄 번호는 빠르게 변하므로 아래 표는 물리적 줄 범위가 아니라 논리적 영역을 나타낸다.

주요 구역:

| 영역 | 내용 |
|---------|------|
| 기반 | 임포트, 플랫폼 유틸리티, 경로, 포트 및 전역 설정 |
| 설정 | 설정 마이그레이션, 캐시, 제공자·모델·API 키 선택 |
| 프로토콜 | Anthropic, OpenAI, Ollama 요청·응답과 스트리밍 호환 계층 |
| 라우터 | `RouterHandler`, 런타임 라우터 위임, 업스트림 전달 |
| 채널 | MCP 탐색, cursor, delivery acknowledgement, 터미널 주입 |
| 런처 | Claude, Codex, Codex App Server, AGY 프로세스 실행 |
| CLI/UI | 메뉴, 상태, 설정 명령, 설치 및 업데이트 진단 |

---

### `ciel_runtime_support/__init__.py`

패키지 초기화. 빈 파일.

### `ciel_runtime_support/architecture.py`

아키텍처 계약 정의:
- `ProviderConfig`, `RuntimeConfig`, `LaunchSpec`
- `RuntimeCommand`, `ModelInfo`, `RateLimitState`
- 추상 클래스: `RuntimeAdapter`, `ProviderAdapter`, `ToolDialect`

→ [[Architecture]]

### `ciel_runtime_support/claude_router.py`

Claude Code의 Anthropic Messages HTTP routing Application Service. 문자열 키 기반의 거대 dependency dict 대신 Core, Pipeline, Shortcuts, Delivery, Routing, Normalization, Transport, Response 등 최대 10필드의 typed port 그룹을 사용한다.

### `ciel_runtime_support/openai_responses_router.py`

Codex/OpenAI Responses 요청의 protocol 변환, provider routing, channel delivery acknowledgement와 오류 mapping을 조정하는 Runtime Application Service. Core, Conversion, Routing, Delivery, Output 포트는 각각 최대 10필드로 제한한다.

### `ciel_runtime_support/openai_responses_stream.py`

OpenAI Responses 성공·오류 payload를 JSON 또는 Codex-compatible SSE lifecycle event sequence로 투영하는 Protocol Transport. response 변환과 JSON writer는 2필드 포트로 주입한다.

### `ciel_runtime_support/protocols/ollama_response.py`

Ollama `/api/chat` 응답을 Anthropic Messages content로 투영하는 Provider-specific Protocol Service. Text decoding, tool normalization, recovery policy, output encoding을 각각 10필드 이하 포트로 분리해 메인과 Provider-neutral codec에서 Ollama 실행 정책을 격리한다.

### `ciel_runtime_support/protocols/conversation_policy.py`

Attachment-only message, Plan Mode 상태, plan file write 이력, unchanged Read 결과와 tool-result history를 해석하는 순수 Protocol Policy. Transcript/guard 판정과 content codec은 6필드 Port로 주입받아 provider와 composition root에 의존하지 않는다.

### `ciel_runtime_support/router_http.py`

HTTP parsing과 GET/POST/HEAD/DELETE endpoint dispatch를 담당하는 Router Adapter. Core, GET endpoints, POST endpoints, Presentation, Errors 포트를 통해 Runtime/Channel/Protocol 서비스에 위임하며 composition root를 직접 import하지 않는다.

### `ciel_runtime_support/chat_http_controller.py`

Web Chat/Channel bridge의 health, message history·long-poll·SSE, file download/upload, notify와 transport connect/disconnect endpoint를 소유하는 HTTP Controller. read/write port를 분리해 HTTP 표현과 channel Repository·Lifecycle 구현을 격리한다.

### `ciel_runtime_support/channel_mcp_http_controller.py`

내장 Channel MCP의 session Repository, SSE read-loop와 JSON-RPC POST endpoint를 소유하는 HTTP Controller. session state, stream effects, RPC effects를 분리해 HTTP transport가 전역 dict/lock을 직접 조작하지 않게 한다.

### `ciel_runtime_support/plan_artifact_controller.py`

공유 plan artifact의 목록·조회·저장·latest projection과 channel announce를 소유하는 HTTP Controller/Repository Adapter. 경로 정규화와 HTTP 표현을 plan 생성 호출부에서 분리한다.

### `ciel_runtime_support/anthropic_tool_turns.py`

잘린 대화 기록의 짝 없는 Anthropic `tool_use`/`tool_result`를 안전한 text block으로 내리는 Protocol Normalization Service. 적용 여부는 provider 이름 비교가 아니라 adapter의 `ProviderRequestPolicy.normalize_historical_tool_turns`로 결정한다.

### `ciel_runtime_support/advisor_policy.py`

Advisor tool schema, message/system projection, review focus·trigger·gate와 server-side advisor tool 제거를 담당하는 Application Policy. 텍스트와 의사결정 포트를 각각 6필드 이하로 나누며, server tool 지원 여부는 `ProviderAdapter.supports_server_advisor_tool` capability로 받아 provider 이름을 비교하지 않는다.

### `ciel_runtime_support/channel_injection.py`

대화형 CLI 메시지 주입 계층:
- `InputTransport` 포트
- `CallableInputTransport` 레거시 입력 어댑터
- `RuntimeInjectionPolicy` 런타임별 제출 전략
- `PromptInjection` 명령 객체
- `ChannelPromptInjector` 주입 오케스트레이터

SSE/MCP 수집 및 cursor 상태와 분리되어 있으며 Windows Console과 PTY가 같은 제출 계약을 사용한다.

→ [[Architecture]]

### `ciel_runtime_support/channel_event_projection.py`

SSE/MCP envelope에서 사용자 메시지와 metadata를 추출하고, 제어 이벤트를 제외하며, 민감 키를 redaction한 chat payload로 투영하는 순수 Channel Presentation Service. Transport, 저장소, 전역 상태에 의존하지 않아 SSE와 MCP notification 경로가 같은 해석 규칙을 공유한다.

### `ciel_runtime_support/channel_session_repository.py`

Streamable HTTP session record의 조회, URL/session 중복 교체, 최대 보관 수 제한과 삭제를 소유하는 파일 기반 Repository. 경로·프로토콜 기본값·시간·프로세스·권한 효과를 주입하며 손상된 JSON과 chmod 실패를 경고로 노출한다.

### `ciel_runtime_support/channel_session_lifecycle.py`

MCP Streamable HTTP session의 DELETE 요청, 이미 사라진 session의 멱등 처리와 stale session 정리를 담당하는 Channel Lifecycle Service. HTTP codec, Repository, transport, 로그 효과를 typed port로 받아 session 기록과 네트워크 정책을 분리한다.

### `ciel_runtime_support/channel_compact_poll.py`

대기 중인 `/compact` 요청의 polling cadence, in-flight 차단, defer 로그 간격과 injection 옵션을 관리하는 Channel Application Service. Windows Console과 POSIX PTY 루프가 동일 상태 전이를 사용한다.

### `ciel_runtime_support/channel_panel.py`

채널 probe cache와 현재 설정을 사전 실행 메뉴의 행/값 projection으로 변환하는 Channel UI policy. 공식 플러그인, probe 분류, delivery mode는 6필드 `ChannelPanelPolicy`로 주입한다.

### `ciel_runtime_support/channel_pending_injection.py`

대기 중인 channel 메시지의 필터링, 중복 제거, wake claim, prompt 생성 및 터미널 주입을 조정하는 Channel Application Service. 상태 판정, prompt 전략, wake 저장소, I/O를 각각 10필드 이하 포트로 분리한다.

### `ciel_runtime_support/channel_pending_poll.py`

채널 메시지 파일 marker, active-turn defer, delivery cursor와 in-flight 시작을 조정하는 polling 상태 머신. Windows Console과 POSIX PTY는 플랫폼별 active predicate와 로그 policy만 제공하고 동일 injection workflow를 사용한다.

### `ciel_runtime_support/channel_terminal_proxy.py`

POSIX PTY의 생성, 터미널 크기 동기화, 표준 입출력 전달, channel/compact polling과 자식 프로세스 정리를 조정하는 Channel Infrastructure Adapter. Process, Terminal I/O, Policy, Polling 포트를 각각 10필드 이하로 주입하며 메인 composition root는 플랫폼 선택과 실제 콜백 조립만 담당한다.

### `ciel_runtime_support/channel_transcript.py`

Claude/Codex JSONL transcript의 사용자 텍스트, assistant turn, tool call/result 및 활성 turn 상태를 해석하는 순수 Channel 도메인 서비스. 파일·프로세스·composition root 전역에 의존하지 않는다.

### `ciel_runtime_support/channel_message_policy.py`

MCP/SSE/Web Chat 메시지의 출처와 고유 참조, 이벤트 순서, coalescing identity를 해석하고 같은 스트림에서 superseded된 알림을 판정하는 순수 Channel 정책. 전송·저장 효과와 독립적이며 Provider나 composition root를 참조하지 않는다.

### `ciel_runtime_support/channel_message_prompt.py`

Channel 메시지의 노이즈·scope 필터링, 안전한 metadata 축약, 일반/Web Chat/LLM prompt 표현을 소유하는 순수 projection 정책. MCP/SSE envelope 표시는 원문 JSON을 보존하면서 민감 metadata를 prompt header에서 제외한다.

### `ciel_runtime_support/channel_event_identity.py`

MCP/SSE notification envelope에서 transport와 무관한 event identity를 만들고 stable/fallback chat dedupe key를 계산하는 순수 정책. 저장소의 최근 행 검색과 TTL 판단은 composition 계층에 남기고 identity 의미만 캡슐화한다.

### `ciel_runtime_support/channel_message_repository.py`

Channel JSONL 메시지의 ID/cutoff 스캔, channel·recipient 가시성 필터, 앞뒤 방향 조회와 dedupe용 최근 행 조회를 소유하는 Repository. 경로와 진단 logger만 주입받으며 손상된 개별 행은 격리하고 파일 I/O 실패는 경고로 노출한다.

### `ciel_runtime_support/channel_launch_guard_repository.py`

Channel 시작 직후 replay dedupe guard의 만료 판정과 원자 저장을 소유하는 Repository. 파일 부재·만료는 정상적인 empty 상태로 처리하고, 손상·권한·I/O 실패는 명시적인 경고로 관측 가능하게 남긴다.

### `ciel_runtime_support/channel_cursor_repository.py`

MCP delivery, LLM delivery, backlog clear-floor cursor의 공통 JSON 읽기와 원자 교체를 소유하는 Repository. 부재는 초기화 신호로 반환하고 손상·권한·write 실패는 경로가 포함된 경고로 노출한다.

### `ciel_runtime_support/channel_wake_claim_repository.py`

터미널 wake prompt의 메시지 ID/문자열 참조 판정과 교차 프로세스 claim의 TTL·원자 저장을 소유하는 Repository. 파일 lock, clock, TTL policy, logger를 주입받고 메인의 in-memory fast path와 독립적으로 동작한다.

### `ciel_runtime_support/channel_terminal_input.py`

플랫폼·환경별 Enter sequence 선택, 실제 사용자 입력에서 submit key 관찰, synthetic CR 정규화, wake byte 조립과 bounded delay를 소유하는 Terminal Input 정책. PTY/Console write 효과와 분리되어 플랫폼 조합을 독립적으로 검증할 수 있다.

### `ciel_runtime_support/tool_guard_hooks.py`

Claude 설정의 tool guard hook을 읽고, 레거시 hook 제거와 중복 정규화 후 원자적으로 저장하는 Security/Configuration Service. 이벤트 정책과 파일 시스템 효과를 분리하며 권한 제한 실패도 경고로 관측 가능하게 남긴다.

### `ciel_runtime_support/settings_repository.py`

Claude settings 호환 이름을 `SecureJsonRepository`에 연결하는 얇은 Configuration compatibility module. 기존 settings 호출자의 타입 이름을 보존하면서 저장 효과 구현은 일반 secure JSON 경계에 위임한다.

### `ciel_runtime_support/secure_json_repository.py`

Settings, MCP config, probe cache, compact request 같은 JSON artifact를 동일 디렉터리 임시 파일로 원자 저장하는 Configuration Repository. chmod, process id, clock, logger를 주입하며 읽기 손상과 private-permission 실패를 일관된 진단 이벤트로 만든다.

### `ciel_runtime_support/statusline_settings.py`

Statusline script 설치와 Claude `statusLine` 설정 mutation을 담당하는 Configuration Service. JSON persistence는 `JsonSettingsRepository`에 위임하고 script 실행 권한과 사용자 경고만 별도 효과로 관리한다.

### `ciel_runtime_support/request_trace.py`

Anthropic message/tool block 요약과 request/response JSONL trace 저장·회전을 담당하는 Observability Adapter. trace 활성화·경로·용량 정책과 content projection을 작은 포트로 분리하고, 저장 실패를 더 이상 숨기지 않고 진단 로그로 노출한다.

### `ciel_runtime_support/request_shortcuts.py`

대화 메시지에 포함된 local router marker, live option/API-key 값, channel bridge 명령과 ImportSession 인자를 해석하는 Application Policy. 런타임별 message decoding은 `ShortcutTextServices` Port로 주입받는다.

### `ciel_runtime_support/prompt_injection.py`

Protocol Strategy Registry. Anthropic/OpenAI Chat/OpenAI Responses/Ollama/Google 요청의 system context 배치 차이를 캡슐화하고 입력 객체와 Anthropic cache-control identity block을 보존한다.

### `ciel_runtime_support/provider_descriptor.py`

Provider 이름, 표시명, alias와 concrete Adapter factory를 정의하는 data-driven discovery Registry. 동작 정책은 descriptor가 아니라 concrete Provider Adapter가 소유한다.

### `ciel_runtime_support/credentials.py`

API-key 파싱·중복 제거·ProviderConfig 투영과 inbound OAuth header pass-through를 담당하는 Credential Application Service. `CredentialChain`은 인증 소스 우선순위를, source 구현은 allowlist와 header 투영을 소유한다.

### `ciel_runtime_support/routing_fallback.py`

실패 원인별 provider/model 후보를 중복 없이 계산하는 순수 Fallback Policy. 네트워크 호출과 retry state를 포함하지 않아 기존 upstream retry 및 key cooldown 계층과 독립적이다.

### `ciel_runtime_support/usage_events.py`

Provider 중립 `UsageEvent`, sink Port, bounded JSONL Adapter와 집계 함수를 제공한다. token 수 외의 prompt·credential 내용은 저장하지 않는다.

### `ciel_runtime_support/ui_text.py`

다국어 prelaunch label과 provider guidance 데이터. composition root와 UI controller에서 정적 번역 데이터를 분리한다.

### `ciel_runtime_support/architecture_budget.py`

모든 프로덕션 Python 파일의 최종 4,999줄 상한과 `ciel_runtime.py` 마이그레이션 ratchet을 검사한다. ratchet은 감소만 허용하며 최종적으로 두 예산을 일치시킨다.

### `ciel_runtime_support/launch_state.py`

작업 디렉터리별 최근 runtime/provider/model을 저장하는 Repository와 native/routed 전환 시 session fork 여부를 계산하는 순수 Policy. 파일 저장 효과와 전환 판단을 분리한다.

### `ciel_runtime_support/launch_diagnostics.py`

Claude·Codex·AGY 실행 명령과 환경을 비밀값 마스킹 후 기록하는 Observability Adapter. Claude stderr의 크기 제한 회전, console/file tee, subprocess fallback을 캡슐화하여 launch application service에서 파일·파이프·스레드 세부 구현을 제거한다.

### `ciel_runtime_support/llm_presentation_data.py`

LLM preset, timeout profile, model family, live option 설명을 보관하는 불변 Presentation Catalog. 설정 mutation과 UI controller에서 대규모 정적 데이터를 분리한다.

### `ciel_runtime_support/statusline_script.py`

설치되는 독립 statusline 프로그램의 배포 자산. 설치·설정 mutation은 `statusline_settings.py`가 계속 담당한다.

### `ciel_runtime_support/slash_command_assets.py`

설치되는 Claude slash-command 문서의 정적 배포 자산. command 설치 정책과 파일 효과는 composition 및 configuration service에 남긴다.

### `ciel_runtime_support/command_asset_installer.py`

Claude command와 Codex prompt의 소유권 표식, 사용자 파일 보존, stale asset 정리, 권한 설정을 소유하는 Infrastructure Adapter. `CommandAsset` 값 객체가 내용과 ownership marker를 함께 전달해 filename 조건 분기를 제거한다.

### `ciel_runtime_support/process_control.py`

Windows CIM/taskkill과 POSIX ps/signal 기반 프로세스 검색·종료를 캡슐화하는 Runtime Infrastructure Adapter. Query, Inspection, Signal 포트를 분리하며 명령행·환경·cwd 조회, Windows netstat와 Linux procfs/lsof/ss 기반 port listener 탐색, TERM/KILL 및 taskkill 실패를 구조화된 경고로 노출한다.

### `ciel_runtime_support/executable_discovery.py`

플랫폼별 실행 파일 확장자·추가 설치 경로, PATH 탐색, subprocess command 해석, uvx fallback과 tool-guard 위치 탐색을 소유하는 Runtime Infrastructure Resolver. launch와 MCP 설정 코드에서 Windows/POSIX 경로 분기를 제거한다.

### `ciel_runtime_support/cli_parser.py`

`ciel-runtimectl`의 argparse 문법과 명령-handler 연결을 소유하는 CLI Adapter. Launch, Runtime, Settings, Provider, Models 명령군을 각각 10필드 이하 typed port로 나누고 메인 composition root는 실제 handler만 조립한다.

### `ciel_runtime_support/channel_probe_report.py`

channel capability probe 결과를 capable/non-capable/inconclusive/skipped 행과 진단 hint로 투영하는 Presentation Service. probe 실행과 CLI 명령 해석에서 결과 formatting을 분리한다.

### `ciel_runtime_support/channel_connection_registry.py`

SSE/Streamable HTTP channel connection 상태, 공개 status projection, MCP session-loss 전이와 JSON-RPC 응답 대기를 소유하는 thread-safe Repository. 공유 dict/lock/condition 접근을 transport worker에서 분리한다.

### `ciel_runtime_support/channel_connection_lifecycle.py`

SSE/Streamable HTTP 연결 설정의 정규화, connection state 생성·교체, stale session 정리, worker 선택·기동과 stop 전이를 소유하는 Application Lifecycle Service. Factory와 thread-safe Store를 분리하고 메인에는 composition wrapper만 둔다.

### `ciel_runtime_support/channel_connection_worker.py`

일반 SSE와 MCP Streamable HTTP의 연결·재연결·session-loss·legacy fallback 상태 머신을 소유하는 Infrastructure Adapter. thread-safe state store와 typed effect/policy 포트로 protocol orchestration을 분리하며, 메인 모듈은 런타임 의존성을 조립하는 호환 wrapper만 유지한다.

### `ciel_runtime_support/channel_inflight.py`

터미널에 주입된 channel 메시지의 확인, 재조회, stale 복구 및 대기 전이를 담당하는 상태 머신. Windows Console과 POSIX PTY 프록시는 동일한 순수 전이 정책을 사용하고 cursor·wake 저장소·로그는 effect 포트로 주입한다.

### `ciel_runtime_support/channel_llm_context.py`

대기 channel 메시지를 LLM request context에 결합하는 Application Service. delivery/skip 정책, cursor·message Repository, wake/prompt Projection을 분리하며 메인 composition root는 global cursor와 파일 저장 adapter만 조립한다.

### `ciel_runtime_support/channel_mcp_tools.py`

내장 Channel MCP 도구의 schema catalog와 호출 dispatcher를 소유하는 Application Service. compact 요청, 채팅·파일 저장, LLM 옵션 변경은 6필드 `ChannelMcpToolServices` 포트로 주입되어 MCP protocol projection과 인프라 구현을 분리한다.

### `ciel_runtime_support/sse_stream.py`

SSE byte stream을 event frame으로 변환하는 Transport Codec. 일반 SSE와 MCP Streamable HTTP worker가 동일 parser를 사용하며, 종료 판정·event dispatch·잘못된 retry 관측을 `SseStreamServices`로 주입한다. `SseRetryState`는 예외 종료 후에도 서버의 retry 지시가 reconnect 정책에 전달되도록 보존한다.

### `ciel_runtime_support/windows_console_input.py`

Windows Console `INPUT_RECORD` 생성, UTF-16 surrogate 변환, 입력 queue 소비 확인을 담당하는 플랫폼 Adapter. Channel wake 오케스트레이션과 Win32 입력 세부 구현을 분리한다.

### `ciel_runtime_support/config_repository.py`

원자적 JSON 설정 저장, mtime cache, migration 및 정규화 callback을 소유하는 Repository 구현.

### `ciel_runtime_support/llm_presets.py`

모델 용량과 공급자 특성을 반영해 LLM 프리셋을 적용하는 애플리케이션 서비스.

### `ciel_runtime_support/llm_option_config.py`

LLM 옵션의 입력 검증, Provider 설정 변경, 컨텍스트·출력 보정 및 설정 저장을 조정하는 Configuration Application Service. Repository, Mutation, Policy 포트를 분리해 각 포트를 6필드 이하로 유지한다.

### `ciel_runtime_support/cli_dispatch.py`

명시적 `CliServices` dependency object를 사용하는 CLI Application Service와 command dispatcher.

### `ciel_runtime_support/cli_usage.py`

제어 명령, headless 설정 flag, 지원 provider를 설명하는 CLI usage presentation. entrypoint와 dispatcher에서 정적 사용자 문구를 분리한다.

### `ciel_runtime_support/headless_config.py`

`CIEL_RUNTIME_*` 환경 설정을 command 호출 계획으로 변환·적용하는 Configuration Application Service. 일반 설정 명령, channel 명령, 환경 조회를 10필드 이하 포트로 분리하며 API key 우선순위와 provider option mapping을 한곳에서 관리한다.

### `ciel_runtime_support/compatibility_test.py`

Provider text/tool_use/tool_result 호환성 진단을 실행하는 CLI Application Service. Configuration, mode, request, protocol, output 포트를 분리하며 각 포트는 최대 10개 의존성만 갖는다.

### `ciel_runtime_support/agy_cli.py`

AGY passthrough 인수 분석과 Claude 호환 인수 매핑.

### `ciel_runtime_support/codex_cli.py`

Codex passthrough, resume 및 채널 관련 인수 정규화.

### `ciel_runtime_support/codex_app_server.py`

Codex App Server 프로세스와 JSON-RPC/WebSocket 상태 조정.

### `ciel_runtime_support/provider_adapters.py`

Provider Adapter Registry와 기존 import 경로를 위한 호환 re-export 진입점. 구체 Provider 구현은 `providers/` 하위 모듈이 소유하며, 이 모듈은 이름·표시 label·factory 및 전체 Provider 기본 설정 조립만 담당한다.

### `ciel_runtime_support/providers/__init__.py`

Provider 구현 패키지의 안정적인 공개 진입점. 분리된 공통 기반 클래스를 기존 import 사용자에게 re-export한다.

### `ciel_runtime_support/providers/base.py`

Bearer/API-key 인증, 무인증 및 OpenAI-compatible protocol 선택을 제공하는 공통 Provider transport 기반 클래스. 공통 persisted configuration shape와 안전한 중첩 기본값 복제를 제공하지만 구체 Provider 이름이나 Registry는 소유하지 않는다.

### `ciel_runtime_support/providers/anthropic.py`

Anthropic native/routed Adapter. Anthropic protocol, context hint, API-key 상태, built-in Advisor 안내와 native/routed UI 정책을 소유한다.

### `ciel_runtime_support/anthropic_model_policy.py`

Anthropic model family, context/output limit metadata, adaptive-thinking runtime hint, Claude Code capability inference와 registry 추천 preset을 소유하는 Provider Domain Policy. model registry와 launch 환경이 동일한 metadata 규칙을 공유한다.

### `ciel_runtime_support/providers/constants.py`

Provider 모듈들이 공유하는 기본 endpoint와 Z.AI fallback catalog 상수. 메인 composition root나 Registry 구현에 의존하지 않는다.

### `ciel_runtime_support/providers/native.py`

Codex 및 AGY native provider-selection Adapter. native/routed 표시, API-key 상태와 Runtime 소유 모델 설정 정책을 중앙 Registry 구현에서 분리한다.

### `ciel_runtime_support/providers/ollama.py`

로컬 Ollama와 Ollama Cloud Adapter. Ollama protocol endpoint, context·option 정책, catalog fallback 및 launch model alias 전략을 소유한다.

### `ciel_runtime_support/providers/openrouter.py`

OpenRouter 인증, OpenAI protocol capability와 hosted context 정책을 소유하는 독립 Adapter.

### `ciel_runtime_support/providers/lm_studio.py`

LM Studio catalog 경로, local capability, loaded-model readiness와 context/status 정책을 소유하는 독립 Adapter.

### `ciel_runtime_support/providers/vllm.py`

vLLM model-selection requirement, local capability, remote context discovery와 native readiness 정책을 소유하는 독립 Adapter.

### `ciel_runtime_support/providers/nvidia.py`

NVIDIA hosted 인증, NCP model alias, streaming requirement, context 및 router-only 호환성 정책을 소유하는 독립 Adapter.

### `ciel_runtime_support/providers/nim.py`

Self-hosted NVIDIA NIM의 local capability, catalog selection, context discovery와 native readiness 정책을 소유하는 독립 Adapter.

### `ciel_runtime_support/providers/deepseek.py`

DeepSeek Anthropic-compatible endpoint, fallback catalog, tool-choice 제약과 context/status 정책을 소유하는 독립 Adapter.

### `ciel_runtime_support/providers/zai.py`

Z.AI Anthropic-compatible endpoint, GLM fallback catalog, model-ID 정규화와 context/status 정책을 소유하는 독립 Adapter.

### `ciel_runtime_support/providers/kimi.py`

Kimi Anthropic/OpenAI protocol 선택, K3 1M context profile, tool-choice·thinking 정규화와 catalog/status 정책을 소유하는 독립 Adapter.

### `ciel_runtime_support/providers/fireworks.py`

Fireworks.ai 인증, management catalog scope, account 추론, protocol 선택과 모델 metadata projection 정책을 소유하는 독립 Adapter.

### `ciel_runtime_support/providers/opencode.py`

OpenCode Zen의 모델별 protocol routing, endpoint override, catalog/status와 UI 표시 정책을 소유하는 독립 Adapter.

### `ciel_runtime_support/providers/opencode_go.py`

OpenCode 공통 routing 정책을 확장하면서 OpenCode Go 전용 endpoint·label·인증 오류를 소유하는 독립 Adapter.

### `ciel_runtime_support/provider_compatibility.py`

Provider transport Adapter와 분리된 Advisor 전송, 호환성 실패 진단, runtime metadata projection, 자동 웹 검색 및 Claude compatibility prompt 정책 Registry. 기본 정책과 Provider별 override를 7필드 불변 정책으로 조합한다.

### `ciel_runtime_support/ollama_catalog.py`

Ollama Library 모델 ID, context window와 timeout을 계산하는 Functional Core이며, catalog tag 그룹화·기존 context 보존·context map 갱신 workflow도 소유한다. 파일·네트워크 효과는 6필드 `OllamaCatalogRefreshServices`로 주입되어 메인 composition root에는 실제 adapter 조립만 남는다.

### `ciel_runtime_support/ollama_catalog_repository.py`

Ollama catalog의 원자적 JSON 저장과 Ollama API/Library HTTP 조회를 소유하는 Infrastructure Repository. catalog Functional Core에서 파일 권한·임시 파일 교체·응답 크기 제한·tag page fallback 세부를 분리한다.

### `ciel_runtime_support/ollama_context_sync.py`

`/api/show` → cached catalog → Ollama Library → model-name hint 순서로 context capacity를 탐색하고 provider 설정 상한을 전이하는 Application Service. source port와 policy port를 분리해 중앙 facade의 Ollama 조건 분기를 제거한다.

### `ciel_runtime_support/ollama_forwarding.py`

Ollama upstream 요청, context retry, rate-limit, streaming, Advisor 및 channel delivery를 조정하는 Provider Application Service. Request, RateLimit, Streaming, Advisor, Response 포트를 분리하고 각 포트를 9필드 이하로 제한한다.

### `ciel_runtime_support/lm_studio_runtime.py`

LM Studio v0/v1 model API에서 현재 runtime model, 최대·실제 loaded context와 instance 상태를 탐색하는 Provider Application Service. API URL, HTTP, model ID·context 정책을 9필드 포트로 주입하고 각 API 실패를 provider 진단 로그로 남긴다.

### `ciel_runtime_support/openai_forwarding.py`

OpenAI-compatible chat 전달, streaming, retry, Advisor gate 및 Anthropic 응답 projection을 조정하는 Provider Application Service. NVIDIA 같은 특수 동작은 Provider 이름 비교 대신 Adapter의 request policy로 입력받는다.

### `ciel_runtime_support/response_collection.py`

Responses API projection을 위한 비스트리밍 chat 수집 Template Method. Ollama/OpenAI 차이는 `ChatCollectionStrategy`로 주입하고, 요청·rate-limit·응답 projection 포트를 분리한다.

### `ciel_runtime_support/protocols/chat_projection.py`

Anthropic Messages 이력을 Ollama/OpenAI chat wire message로 변환하는 순수 Protocol projection. 텍스트 필터와 tool-result 문맥 정책을 포트로 분리하고, OpenAI의 assistant tool-call/tool-result 인접성 불변조건과 orphan history 복구도 이 계층에서 보장해 전송·Provider 선택 로직과 격리한다.

### `ciel_runtime_support/protocols/tool_result_projection.py`

성공·실패·unchanged Read 결과를 upstream chat 문맥과 다음 단계 지침으로 투영하는 Protocol Service. unchanged 판정, 길이 제한, truncation을 3필드 포트로 받아 provider wire projection이 메인 전역 정책에 직접 결합되지 않게 한다.

### `ciel_runtime_support/protocols/pseudo_tool_history.py`

모델이 text로 출력한 `<invoke>` 및 tool-name XML을 실제 structured tool call로 해석하고, 과거 assistant history의 가짜 호출 text를 제거하는 Protocol Normalization Service. 사용 가능한 tool 이름·alias·argument repair·로그를 5필드 포트로 주입한다.

### `ciel_runtime_support/upstream_retry.py`

Provider 공통 JSON 요청, 직접 요청 및 OpenAI stream 요청의 retry transport를 소유한다. 재시도 정책, API-key rotation, rate-limit 관측, HTTP I/O를 별도 포트로 분리하며 각 포트는 7필드 이하로 제한한다.

### `ciel_runtime_support/provider_limits.py`

API 키 순환과 학습형 rate-limit 상태·backoff·적용 정책을 캡슐화한 공급자 서비스.

### `ciel_runtime_support/rate_limit_policy.py`

RPM 설정 해석, 예약 capacity, sliding-window timestamp 정리, Retry-After/date와 reset epoch 해석, duration/header projection을 소유하는 순수 Domain Policy. rate-limit 상태 저장과 upstream retry 효과에서 HTTP 숫자 해석을 분리한다.

### `ciel_runtime_support/provider_models.py`

공급자별 모델 카탈로그 조회, fallback, cache 및 registry 갱신을 조정하는 모델 서비스.

### `ciel_runtime_support/model_panel.py`

모델·Advisor 모델 선택 행을 구성하는 UI projection. catalog와 presentation 포트를 분리하며 endpoint badge, Advisor 전용 안내 및 모델 주석은 Provider Adapter registry가 소유한다.

### `ciel_runtime_support/model_catalog_projection.py`

여러 model API 응답 shape를 공통 ID·context metadata map으로 투영하는 Provider Application Service. Provider 고유 metadata는 `ProviderAdapter.project_model_metadata` 전략에 위임하므로 Fireworks management 필드 같은 구체 구현이 중앙 parser에 섞이지 않는다.

### `ciel_runtime_support/model_registry_repository.py`

provider model registry와 단기 model-list cache의 key 호환, TTL, metadata 정규화, 권한 설정과 JSON persistence를 소유하는 Repository. 경로와 model 정책 포트를 분리해 provider catalog service가 파일 형식을 직접 다루지 않게 한다.

### `ciel_runtime_support/provider_policy.py`

공급자별 wire profile과 요청 메시지·thinking·tool-choice 정규화를 담당하는 순수 정책 서비스.

### `ciel_runtime_support/provider_context.py`

Provider Adapter의 `ProviderContextPolicy`에 따라 모델 컨텍스트 용량 탐색 순서, 설정 상한 및 LLM preset 추론을 수행하는 Provider-neutral 정책 서비스. 중앙 Provider 이름 분기 없이 managed/Ollama/standard 설정 전략을 실행한다.

### `ciel_runtime_support/provider_network.py`

upstream User-Agent, provider별 IP-family 기본값·별칭, strict/preferred DNS 정렬, HTTP 연결과 IPv4/IPv6 probe를 소유하는 Infrastructure Policy. provider application logic에서 process-wide socket override와 연결 진단 세부를 격리한다.

### `ciel_runtime_support/provider_option_panel.py`

Provider option 화면의 행 구성과 runtime/context/stream control projection을 담당하는 UI Application Service. 텍스트·runtime·Provider projection을 각각 10필드 이하 포트로 분리하고 Adapter의 presentation/context 정책만 소비한다.

### `ciel_runtime_support/llm_option_config.py`

LLM 옵션의 검증·저장·후속 context/timeout 상한 적용을 조정하는 Configuration Application Service. Provider별 mutation 방식과 라우팅 모드 projection은 Adapter 정책을 통해 주입되며 서비스 자체는 Provider 이름을 비교하지 않는다.

### `ciel_runtime_support/provider_status.py`

Base URL 및 model catalog reachability 상태를 투영하는 Provider Application Service. Provider별 native/configured/catalog 전략은 Adapter의 `ProviderStatusPolicy`가 소유하며 중앙 서비스는 Provider 이름을 비교하지 않는다.

### `ciel_runtime_support/provider_readiness.py`

native launch 우회, Base URL·API-key 차단, ultracode capability 및 Provider runtime 검증을 조정하는 Readiness Application Service. LM Studio context 검증 같은 특수 동작은 `ProviderStatusPolicy.readiness_validation` 전략으로 선택한다.

### `ciel_runtime_support/prelaunch.py`

공급자·모델·채널·컨텍스트 설정 패널을 조정하는 사전 실행 메뉴 애플리케이션 서비스.

### `ciel_runtime_support/prelaunch_terminal.py`

사전 실행 메뉴의 화면 렌더링과 단일·다중행 TTY 입력을 담당하는 UI 어댑터. 렌더링 데이터, 텍스트 폭/색상, 브랜드 정보를 각각 10필드 이하 포트로 분리하며 터미널 polling·복구 실패는 로그로 관측된다.

### `ciel_runtime_support/prompt_compaction.py`

Provider context budget을 넘는 Anthropic 및 Ollama/OpenAI chat history를 tool-result 경계가 안전한 tail, hard-cap, 분산 요약으로 축약하는 Prompt Compaction Service. 텍스트 projection과 token/LLM/runtime 관측 포트를 분리하고 wire 형식은 호출 Strategy가 명시한다.

### `ciel_runtime_support/context_compaction.py`

보조 요약 요청과 map/reduce 컨텍스트 축약을 조정하는 Context Compaction Application Service. 요약 가능 여부는 Provider Adapter capability로 판단하고, 전송·워크플로·projection을 각각 10필드 이하 포트로 분리하며 Provider 이름에는 의존하지 않는다.

### `ciel_runtime_support/context_summary_policy.py`

compact 요청 판정·text-only 변환, tool 입력 축약, persisted-output 판정, deterministic chunk 범위·summary, compact instruction 탐색, map/reduce prompt와 response codec을 소유하는 순수 Protocol/Domain Policy. token·content·JSON projection만 명시적 포트로 받는다.

### `ciel_runtime_support/runtime_adapters.py`

정규화된 `LaunchSpec`을 최종 `RuntimeCommand`로 변환하는 실제 CLI 런타임 어댑터.

### `ciel_runtime_support/runtime_compatibility.py`

Provider UI와 분리된 Runtime×Provider 호환성 정책. native Provider의 단일 Runtime affinity와 일반 upstream Provider가 사용할 수 있는 routed Runtime 집합을 선언하고, 실행 메뉴와 기본 launch 선택이 이 정책을 공유한다.

### `ciel_runtime_support/runtime_launch.py`

Claude, Codex, Codex App Server, AGY 프로세스 실행과 라우터·채널 수명주기를 조정하는 런타임 애플리케이션 서비스.

### `ciel_runtime_support/streaming_anthropic.py`

Anthropic SSE 재배치와 thinking/tool-use 보존 정책을 실행하는 명시적 의존성 기반 스트리밍 서비스.

### `ciel_runtime_support/registry.py`

Provider, Runtime, Protocol, Tool 확장 지점에서 사용하는 이름·별칭 기반 typed factory registry.

### `ciel_runtime_support/tool_dialects.py`

Claude 도구 이름 dialect, MCP 서버 이름 정규화 및 Tool Dialect registry.

### `ciel_runtime_support/tool_schema.py`

도구 schema registry, JSON Schema 기반 입력 보정, Cron/Task 별칭 변환 및 필수 필드 검증.

### `ciel_runtime_support/protocols/openai_responses.py`

전역 설정과 네트워크 의존성이 없는 OpenAI Responses ↔ Anthropic Messages 순수 변환.

### `ciel_runtime_support/protocols/ollama_chat.py`

Anthropic system/tool schema를 Ollama `/api/chat` wire 구조로 투영하는 순수 Protocol codec.

### `ciel_runtime_support/mcp_config_reader.py`

Claude/Codex MCP JSON 설정의 root·project scope를 읽고 projector별 identity로 중복 제거하는 Configuration Reader. 파일 손상·권한 실패를 빈 설정과 구분해 경로가 포함된 경고로 남기며 transport별 server projection은 호출자에 위임한다.

### `ciel_runtime_support/mcp_transport.py`

채널 상태나 라우터 설정을 소유하지 않는 MCP SSE/Streamable HTTP 전송 codec과 split-proxy URL 규칙.

### `ciel_runtime_support/mcp_probe_codec.py`

MCP 채널 capability probe의 JSONL/LSP 프레임, SSE 이벤트 및 initialize 응답 판정을 담당하는 순수 codec.

### `ciel_runtime_support/mcp_probe_transport.py`

SSE와 Streamable HTTP MCP capability probe의 수명주기를 조정하는 Transport Application Service. codec, HTTP I/O, timeout 정책을 각각 6필드 이하 포트로 분리하고 reader·cleanup 실패를 구조화 로그로 노출한다.

### `ciel_runtime_support/mcp_stdio_probe.py`

stdio MCP server의 spawn, initialize framing, stdout/stderr 수집 및 종료 수명주기를 담당하는 Process Adapter. codec, process I/O, timeout·preview 정책 포트를 분리하며 모든 reader·write·cleanup 실패를 관측 가능하게 유지한다.

### `ciel_runtime_support/mcp_proxy_codec.py`

MCP 프록시의 JSON-RPC 오류·notification wait 응답과 대형 메시지 조회 결과 압축을 담당하는 codec. 전송 및 프로세스 수명주기와 분리되며 환경·로그·문자열 제한 규칙은 6필드 `McpProxyCodecPolicy` 포트로 주입된다.

### `ciel_runtime_support/mcp_proxy_process.py`

MCP stdio의 Content-Length/JSONL framing, stdin/stdout/stderr 전달과 Streamable HTTP 요청 어댑터. notification 관찰·로그·HTTP 전송은 호출 시 명시적으로 주입되어 채널 저장소 및 메인 composition root와의 숨은 결합을 만들지 않는다.

### `ciel_runtime_support/mcp_http_proxy.py`

Streamable HTTP MCP session의 initialize, notification GET stream 재연결, tool-call 전달을 조정하는 Application Service. 의존성을 최대 10필드의 `McpHttpProxyCodec`, `McpHttpProxyTransport`, `McpHttpProxyRuntime` 포트로 분리하고 최상위 서비스는 이 세 포트만 소유한다.

### `ciel_runtime_support/config_migrations.py`

파일 저장이나 런타임 I/O 없이 구성 schema migration을 순서대로 적용하는 Configuration policy.

### `ciel_runtime_support/provider_config_mutations.py`

CLI와 저장소에 의존하지 않고 Provider option의 검증·정규화·설정 변경을 수행하는 Configuration policy. 프로바이더 이름 분기 없이 각 Provider Adapter의 `ProviderConfigurationPolicy`가 Ollama mutation 전략, endpoint override, native 제한, route 지원 및 텍스트 option alias를 선언한다.

### `ciel_runtime_support/agent_router.py`

런타임 HTTP 라우터 공통 계약:
- `RuntimeRouter` 프로토콜
- `RouterCapability`
- `COMMON_RUNTIME_ROUTER_CAPABILITIES`
- 라우터 capability matrix / gap 검사

→ [[Architecture]]

### `ciel_runtime_support/claude_router.py`

Claude Code용 HTTP 라우터:
- `/v1/messages`
- `/v1/messages/count_tokens`
- Anthropic Messages 기반 SSE, 채널 주입, 토큰 카운트 경로 소유

→ [[Architecture]]

### `ciel_runtime_support/codex_router.py`

Codex용 HTTP 라우터:
- `/backend-api/codex/*`
- `/backend-api/codex/responses`
- `/v1/responses`
- native Codex auth passthrough, Responses SSE proxy, 채널 주입 경로 소유

→ [[Architecture]]

### `ciel_runtime_support/observability.py`

이벤트 버스 및 HTML 렌더러:
- `EventBus` 클래스
- `EventConfig` 클래스
- `render_events_html()` 함수

→ [[Observability]]

### `ciel_runtime_support/runtime_logging.py`

log-level 파일·환경 우선순위, mtime cache, 설정 저장·reset과 router.log 회전을 소유하는 Repository/Infrastructure Adapter. 메인 facade는 기존 logging 함수명을 유지하면서 저장소와 file logger만 조립한다.

### `ciel_runtime_support/transcript_filter.py`

Claude Code 트랜스크립트 이벤트 필터:
- `is_claude_code_transcript_event()`
- `CLAUDE_CODE_TRANSCRIPT_EVENT_TYPES`

→ [[Observability]]

### `ciel_runtime_support/web_ui.py`

설정·파일·네트워크에 의존하지 않는 Router Web Chat HTML renderer.

---

## 실행 스크립트

| 파일 | 용도 |
|------|------|
| `ciel-runtime` | Linux/macOS 메인 실행 셸 스크립트 |
| `ciel-runtime.cmd` | Windows CMD 래퍼 |
| `ciel-runtime.ps1` | Windows PowerShell 래퍼 |
| `ciel-runtimectl` | Linux/macOS 제어 CLI 셸 스크립트 |
| `ciel-runtimectl.cmd` | Windows CMD 래퍼 |
| `ciel-runtimectl.ps1` | Windows PowerShell 래퍼 |
| `ciel-runtime-stop` | Linux/macOS Router 종료 스크립트 |
| `ciel-runtime-stop.cmd` | Windows CMD 래퍼 |
| `ciel-runtime-stop.ps1` | Windows PowerShell 래퍼 |

---

## Python 보조 스크립트

| 파일 | 용도 |
|------|------|
| `ciel-runtime-menu.py` | 터미널 메뉴 UI (제공자/모델 선택) |
| `ciel-runtime-tool-guard.py` | Tool Guard 독립 스크립트 (Claude Code 훅) |

---

## npm 바이너리

| 파일 | 용도 |
|------|------|
| `npm-bin/ciel-runtime.js` | npm 글로벌 바이너리 (`ciel-runtime`) |
| `npm-bin/ciel-runtimectl.js` | npm 글로벌 바이너리 (`ciel-runtimectl`) |
| `npm-bin/ciel-runtime-stop.js` | npm 글로벌 바이너리 (`ciel-runtime-stop`) |
| `npm-bin/run-ciel-runtime.js` | Python 프로세스 실행 헬퍼 |

---

## 설치 스크립트

| 파일 | 용도 |
|------|------|
| `install.sh` | Linux/macOS 수동 설치 |
| `install.ps1` | Windows 수동 설치 |

---

## 설정 파일

| 파일 | 용도 |
|------|------|
| `package.json` | npm 패키지 메타데이터, 테스트/린트 스크립트 |
| `LICENSE` | MIT 라이선스 |
| `NOTICE` | 저작권 고지 |

---

## 스크립트 / 도구

| 파일 | 용도 |
|------|------|
| `scripts/diag_window_activity.py` | 윈도우 활동 진단 |
| `scripts/make_demo_assets.py` | 데모 에셋 생성 |
| `scripts/remote-cleanup.sh` | 원격 정리 스크립트 |
| `scripts/test-raw-term.py` | 터미널 원시 입력 테스트 |

---

## 관련 문서
- [[Architecture]] — 아키텍처 상세
- [[Router]] — RouterHandler 상세
- [[Observability]] — 관찰성 모듈
