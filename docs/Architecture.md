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
