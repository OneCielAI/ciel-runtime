# Architecture — 시스템 아키텍처

> 소스: `ciel_runtime_support/architecture.py`
> 라우터 소스: `ciel_runtime_support/agent_router.py`, `claude_router.py`, `codex_router.py`

---

## 소유권 경계 (Ownership Boundaries)

| 계층 | 역할 | 금지 사항 |
|------|------|----------|
| **Runtime Adapter** | Claude Code 등 CLI 실행 관리 | 제공자 레이트 리밋 로직 금지 |
| **Provider Adapter** | 업스트림 LLM API, 키, 모델, 헤더, 제한 관리 | 런타임 실행 플래그 금지 |
| **Protocol Adapter** | 요청/응답 Wire 형식 변환 | — |
| **Tool Dialect** | 런타임별 툴 이름 및 수정 | — |
| **Runtime Router** | 런타임별 HTTP 경로와 라우터 기능 소유 | 다른 런타임의 경로 직접 처리 금지 |
| **Channel Injection** | 런타임 입력 정책과 터미널 전송 조정 | SSE/MCP 수집, cursor 저장, subprocess 생명주기 금지 |
| **Credential Source** | API key, inbound OAuth, 향후 계정 저장소의 인증 해석 | provider wire 변환 및 retry 금지 |
| **Prompt Strategy** | protocol별 system context 배치와 원본 불변성 보장 | provider 이름 분기 금지 |
| **Fallback Policy** | 실패 원인별 provider/model 후보 계획 | HTTP 실행 및 credential 접근 금지 |
| **Usage Sink** | provider 중립 token usage event 저장·집계 | prompt/credential 저장 금지 |

---

## 핵심 데이터클래스

### `ProviderConfig`
업스트림 LLM 서비스 설정.

```python
@dataclass(frozen=True)
class ProviderConfig:
    name: str
    base_url: str
    model: str
    api_keys: tuple[str, ...]
    options: Mapping[str, Any]
```

### `RuntimeConfig`
실행할 클라이언트(Claude Code 등) 설정.

```python
@dataclass(frozen=True)
class RuntimeConfig:
    name: str
    executable: str | None
    mcp_config_paths: tuple[Path, ...]
    enable_channels: bool
    options: Mapping[str, Any]
```

### `LaunchSpec`
런타임/제공자 경계를 넘는 정규화된 실행 요청.

```python
@dataclass(frozen=True)
class LaunchSpec:
    runtime: RuntimeConfig
    provider: ProviderConfig
    mode: LaunchMode          # "native" | "routed" | "router"
    protocol: MessageProtocol # "anthropic_messages" | "openai_chat" | "openai_responses"
    passthrough: tuple[str, ...]
    cwd: Path | None
```

### `RuntimeCommand`
최종 실행 커맨드.

```python
@dataclass(frozen=True)
class RuntimeCommand:
    argv: tuple[str, ...]
    env: Mapping[str, str]
    cwd: Path | None
```

### `ModelInfo`
제공자 중립 모델 메타데이터.

```python
@dataclass(frozen=True)
class ModelInfo:
    id: str
    display_name: str | None
    context_window: int | None
    max_output_tokens: int | None
    supports_tools: bool | None
    supports_vision: bool | None
    raw: Mapping[str, Any]
```

### `RateLimitState`
제공자 중립 레이트 리밋 상태.

```python
@dataclass(frozen=True)
class RateLimitState:
    limited: bool
    retry_after_seconds: float | None
    scope: str | None
    detail: str | None
```

---

## 추상 베이스 클래스

### `RuntimeAdapter` (ABC)
- `find_executable()` → `Path`
- `build_command(spec: LaunchSpec)` → `RuntimeCommand`
- `mcp_config_paths(spec)` → `list[Path]`
- `supports_channel_injection(spec)` → `bool`

### `ProviderAdapter` (ABC)
- `default_base_url()` → `str`
- `list_models(config: ProviderConfig)` → `list[ModelInfo]`
- `build_headers(config, api_key)` → `dict[str, str]`
- `capabilities(config)` → upstream protocol 및 tools/thinking/local 특성
- `supported_protocols(config, model)` → 모델별 지원 protocol 집합
- `select_protocol(operation, config, model)` → 요청 목적별 upstream protocol 선택
- `request_policy(config)` → chat/models/model-info endpoint와 timeout 기본값
- `resolve_endpoint(operation, config)` → provider-owned endpoint 선택
- `model_paths(config)` → provider별 모델 discovery fallback 순서
- `model_catalog_policy(config)` → configured/Anthropic/OpenAI/Ollama/LM Studio/NVIDIA/Fireworks catalog 전략 선택

### `ToolDialect` (ABC)
런타임별 툴 이름 정규화 인터페이스.

---

## Runtime Router 분리

`RouterHandler`는 공통 HTTP 진입점만 담당하고, 런타임별 경로 소유권은 별도 라우터가 가진다.

| 라우터 | 파일 | 경로 | 프로토콜 |
|--------|------|------|----------|
| Claude Router | `ciel_runtime_support/claude_router.py` | `/v1/messages`, `/v1/messages/count_tokens` | Anthropic Messages |
| Codex Router | `ciel_runtime_support/codex_router.py` | `/backend-api/codex/*`, `/backend-api/codex/responses`, `/v1/responses` | OpenAI Responses / Codex backend |

공통 기능은 `COMMON_RUNTIME_ROUTER_CAPABILITIES`로 검증한다:
- auth forwarding
- SSE stream proxy
- channel context injection
- pending delivery ack
- request observability
- upstream error mapping

`tests/test_runtime_routers.py`가 Claude/Codex 라우터가 공통 기능을 모두 제공하는지와 경로 소유권이 섞이지 않는지 확인한다.

## Channel Injection 구조

`ciel_runtime_support/channel_injection.py`는 외부 메시지를 대화 입력으로 제출하는 마지막 단계만 담당한다.

| 구성요소 | 패턴 | 책임 |
|----------|------|------|
| `InputTransport` | Port / Adapter | PTY 또는 Windows Console 입력 전송 계약 |
| `CallableInputTransport` | Adapter | 기존 descriptor/writer를 전송 계약에 연결 |
| `RuntimeInjectionPolicy` | Strategy value | clear, paste, Enter, 지연, 재시도 등 런타임 의미 |
| `PromptInjection` | Command | 변경 불가능한 한 번의 주입 요청 |
| `ChannelPromptInjector` | Application Service | 정책과 transport를 조정하며 제출 순서를 보장 |

의존성 방향은 `channel source -> delivery coordinator -> prompt injector -> input transport`이다.
`ChannelPromptInjector`는 SSE, MCP, cursor, transcript 또는 subprocess를 참조하지 않는다. 플랫폼별 transport도 메시지 의미를 해석하지 않는다. 이 경계를 통해 Claude, Codex, AGY 정책과 Windows/PTY 구현을 독립적으로 테스트하고 교체한다.

---

## LaunchMode 흐름

```
"native"  → Anthropic API 직접 사용 (Claude Code 기본 모드)
"routed"  → Router를 통해 비-Anthropic 제공자에 중계
"router"  → Router 프로세스 자체 실행
```

---

## 관련 문서
- [[Providers]] — 제공자 목록
- [[Router]] — HTTP 라우터
- [[Configuration]] — 설정 시스템

## 운영 경로 적용

아키텍처 계약은 테스트용 모델에 머무르지 않고 운영 경로의 composition root에서 실제로 적용된다.

- `ciel_runtime_support/provider_adapters.py`는 전용 Adapter와 선언형 hosted-provider factory를 하나의 Descriptor Registry에 등록한다. 표준 OpenAI Chat 제공자는 `providers/catalog.py`, Anthropic Messages 호환 제공자는 `providers/anthropic_catalog.py`, 동적 cloud endpoint는 `providers/cloud.py`가 소유한다. `HttpBearerProviderAdapter`는 공통 인증 기반일 뿐 Provider 선택 단위가 아니다.
- `provider_descriptor.py`는 provider 이름·표시명·별칭·구체 Adapter 타입의 단일 등록 원천이다. 단순 provider는 descriptor 데이터만 추가하고, 복잡한 protocol/OAuth 동작은 계속 전용 Adapter에 둔다.
- `prompt_injection.py`는 Anthropic Messages, OpenAI Chat/Responses, Ollama Chat, Google Generative의 system prompt 배치를 Strategy로 분리한다. Anthropic의 첫 identity/cache block 순서는 변경하지 않는다.
- `credentials.py`는 API key와 inbound OAuth header를 Chain of Responsibility로 해석한다. allowlist 밖의 cookie/secret header는 전달하지 않으며 OAuth token 발급·갱신은 향후 별도 infrastructure adapter가 구현한다.
- `routing_fallback.py`는 rate limit, unavailable, timeout, model error별 immutable 후보 계획만 담당한다. 기존 `upstream_retry.py`와 API-key cooldown이 transport 실행을 계속 소유하므로 정책과 재시도 효과가 섞이지 않는다.
- `usage_events.py`는 token usage를 prompt나 key 없이 JSONL로 기록하는 Port/Adapter다. UI 또는 특정 database에 결합하지 않아 analytics backend를 교체할 수 있다.
- wire profile, thinking passback 및 tool-choice 정책은 Provider 이름 조건문 대신 구체 Adapter의 protocol/capability 메서드를 사용한다. Kimi·Fireworks의 dual protocol과 OpenCode의 모델별 endpoint도 Adapter가 선택한다.
- 모델 조회 Application Service는 Provider 이름을 분기하지 않고 Adapter의 `ProviderModelCatalogPolicy`와 `model_paths()`를 사용한다. 새 Provider는 기존 catalog 전략을 조합하면 모델 서비스 수정 없이 등록할 수 있다.
- `provider_policy.py`도 Provider 이름을 분기하지 않는다. 모델별 protocol, request option, tool-choice 및 thinking 보존은 구체 Adapter 메서드로 선택한다.
- `ciel_runtime_support/runtime_adapters.py`의 `CliRuntimeAdapter`가 Claude, Codex, AGY의 정규화된 `LaunchSpec`을 `RuntimeCommand`로 변환한다.
- `ciel_runtime_support/protocols/openai_responses.py`는 설정이나 네트워크에 의존하지 않고 OpenAI Responses와 Anthropic Messages 사이를 변환한다.
- `ciel_runtime_support/registry.py`는 provider, runtime, protocol, tool dialect 구현을 조건문 대신 이름 기반 factory로 선택한다.
- `ciel_runtime_support/tool_dialects.py`는 Claude 도구 이름과 MCP 서버 이름 변형을 `ToolDialect` 구현으로 정규화한다.
- `ciel_runtime_support/provider_limits.py`, `provider_models.py`, `provider_policy.py`는 키 순환·rate limit·모델 카탈로그·wire 정규화를 Provider 계층에 둔다.
- `ciel_runtime_support/runtime_launch.py`는 네 런타임의 프로세스 및 라우터·채널 수명주기를 조정하고, `runtime_adapters.py`가 최종 CLI 문법을 소유한다.
- `ciel_runtime_support/streaming_anthropic.py`는 Anthropic/Ollama/OpenAI 스트림 변환을 명시적 dependency object로 실행한다.
- `ciel_runtime_support/config_repository.py`, `cli_dispatch.py`, `prelaunch.py`, `web_ui.py`는 각각 Repository, Application Service, UI Controller, Presentation 경계를 형성한다.
- `ciel_runtime_support/tool_schema.py`는 도구 schema registry와 입력 보정을, `tool_dialects.py`는 런타임별 이름 dialect를 분리해 담당한다.

`ciel_runtime.py`는 기존 공개 함수 호환성을 유지하는 composition root다. 각 래퍼는 호출 시점의 의존성을 typed service object로 조립해 하위 모듈에 위임하므로 테스트 patch 호환성을 유지하면서 숨은 전역 service locator를 만들지 않는다. 지원 모듈은 composition root를 역참조하지 않으며, 이 의존성 방향은 아키텍처 계약 테스트가 검증한다.

외부 gateway 프로젝트에서 검증된 data-driven provider registry, multi-account fallback 개념, protocol별 prompt injection, usage analytics는 위 경계로 흡수한다. 반면 client fingerprint 위장, subscription token 추출, 기본 활성 lossy prompt compression처럼 보안·호환성 위험이 큰 기법은 architecture capability로 취급하지 않는다.
