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

### `ciel_runtime_support/config_repository.py`

원자적 JSON 설정 저장, mtime cache, migration 및 정규화 callback을 소유하는 Repository 구현.

### `ciel_runtime_support/llm_presets.py`

모델 용량과 공급자 특성을 반영해 LLM 프리셋을 적용하는 애플리케이션 서비스.

### `ciel_runtime_support/cli_dispatch.py`

명시적 `CliServices` dependency object를 사용하는 CLI Application Service와 command dispatcher.

### `ciel_runtime_support/agy_cli.py`

AGY passthrough 인수 분석과 Claude 호환 인수 매핑.

### `ciel_runtime_support/codex_cli.py`

Codex passthrough, resume 및 채널 관련 인수 정규화.

### `ciel_runtime_support/codex_app_server.py`

Codex App Server 프로세스와 JSON-RPC/WebSocket 상태 조정.

### `ciel_runtime_support/provider_adapters.py`

Anthropic, Ollama, OpenRouter, LM Studio, vLLM, NVIDIA NIM, DeepSeek, Kimi, Z.AI, Fireworks, OpenCode 등 Provider별 구체 Adapter와 Registry. 각 Adapter가 인증, protocol capability, endpoint 및 모델 discovery 경로를 소유한다.

### `ciel_runtime_support/ollama_catalog.py`

Ollama Library 모델 ID, context window, timeout 및 catalog 갱신을 부작용 없이 계산하는 Functional Core. 파일·네트워크 I/O는 composition root의 wrapper가 담당한다.

### `ciel_runtime_support/provider_limits.py`

API 키 순환과 학습형 rate-limit 상태·backoff·적용 정책을 캡슐화한 공급자 서비스.

### `ciel_runtime_support/provider_models.py`

공급자별 모델 카탈로그 조회, fallback, cache 및 registry 갱신을 조정하는 모델 서비스.

### `ciel_runtime_support/provider_policy.py`

공급자별 wire profile과 요청 메시지·thinking·tool-choice 정규화를 담당하는 순수 정책 서비스.

### `ciel_runtime_support/prelaunch.py`

공급자·모델·채널·컨텍스트 설정 패널을 조정하는 사전 실행 메뉴 애플리케이션 서비스.

### `ciel_runtime_support/runtime_adapters.py`

정규화된 `LaunchSpec`을 최종 `RuntimeCommand`로 변환하는 실제 CLI 런타임 어댑터.

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
