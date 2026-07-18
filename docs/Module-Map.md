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

### `ciel_runtime_support/channel_injection.py`

대화형 CLI 메시지 주입 계층:
- `InputTransport` 포트
- `CallableInputTransport` 레거시 입력 어댑터
- `RuntimeInjectionPolicy` 런타임별 제출 전략
- `PromptInjection` 명령 객체
- `ChannelPromptInjector` 주입 오케스트레이터

SSE/MCP 수집 및 cursor 상태와 분리되어 있으며 Windows Console과 PTY가 같은 제출 계약을 사용한다.

→ [[Architecture]]

### `ciel_runtime_support/agy_cli.py`

AGY passthrough 인수 분석과 Claude 호환 인수 매핑.

### `ciel_runtime_support/codex_cli.py`

Codex passthrough, resume 및 채널 관련 인수 정규화.

### `ciel_runtime_support/codex_app_server.py`

Codex App Server 프로세스와 JSON-RPC/WebSocket 상태 조정.

### `ciel_runtime_support/provider_adapters.py`

`ProviderAdapter` 계약을 사용하는 실제 HTTP 인증 어댑터.

### `ciel_runtime_support/runtime_adapters.py`

정규화된 `LaunchSpec`을 최종 `RuntimeCommand`로 변환하는 실제 CLI 런타임 어댑터.

### `ciel_runtime_support/registry.py`

Provider, Runtime, Protocol, Tool 확장 지점에서 사용하는 이름·별칭 기반 typed factory registry.

### `ciel_runtime_support/tool_dialects.py`

Claude 도구 이름 dialect, MCP 서버 이름 정규화 및 Tool Dialect registry.

### `ciel_runtime_support/protocols/openai_responses.py`

전역 설정과 네트워크 의존성이 없는 OpenAI Responses ↔ Anthropic Messages 순수 변환.

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

### `ciel_runtime_support/transcript_filter.py`

Claude Code 트랜스크립트 이벤트 필터:
- `is_claude_code_transcript_event()`
- `CLAUDE_CODE_TRANSCRIPT_EVENT_TYPES`

→ [[Observability]]

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
