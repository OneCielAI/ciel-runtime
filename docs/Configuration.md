# Configuration — 설정 시스템

> 소스: `ciel_runtime.py` — `load_config()`, `save_config()`, `apply_config_migrations()`, `CONFIG_DIR`

---

## 설정 디렉터리

| OS | 기본 경로 |
|----|----------|
| Linux/macOS | `~/.config/ciel-runtime/` |
| Windows | `%APPDATA%\ciel-runtime\` |

환경변수 `CIEL_RUNTIME_CONFIG_DIR`로 재정의 가능.

---

## 주요 설정 파일

| 파일 | 용도 |
|------|------|
| `config.json` | 메인 설정 (제공자, 모델, API 키 등) |
| `router.log` | 라우터 로그 |
| `log-level` | 로그 레벨 파일 |
| `router.pid` | 라우터 프로세스 PID |
| `router-activity.json` | 라우터 활동 상태 |
| `rate-limit-state.json` | 레이트 리밋 상태 |
| `requests.jsonl` | 요청 덤프 (디버그용, 최대 5MB) |
| `responses.jsonl` | 응답 덤프 (디버그용, 최대 5MB) |
| `router-sse-trace.jsonl` | SSE 트레이스 (최대 2MB) |
| `model-list-cache.json` | 모델 목록 캐시 (TTL: 300초) |
| `model-registry.json` | 모델 레지스트리 |
| `ollama-model-catalog.json` | Ollama 모델 카탈로그 캐시 (TTL: 24시간) |
| `chat-messages.jsonl` | 채팅 메시지 (최대 20MB) |
| `channel-probe-cache.json` | 채널 프로브 캐시 |
| `launch-state.json` | 실행 상태 |

---

## config.json 구조

```json
{
  "provider": "ollama",
  "model": "qwen3-coder:30b",
  "language": "ko",
  "providers": {
    "anthropic": {
      "api_key": "sk-ant-...",
      "model": "claude-sonnet-4-6"
    },
    "ollama": {
      "base_url": "http://localhost:11434",
      "model": "qwen3-coder:30b",
      "context_limit": 65536,
      "num_ctx_min": 32768,
      "num_ctx_max": 131072,
      "timeout_ms": 300000
    },
    "deepseek": {
      "api_key": "...",
      "model": "deepseek-coder-v2"
    }
  },
  "blocked_tools": ["WebSearch", "WebFetch"],
  "advisor_model": "...",
  "channels": ["server:my-mcp-server"],
  "log_level": "ERROR"
}
```

---

## 환경 변수

| 변수명 | 용도 |
|--------|------|
| `CIEL_RUNTIME_CONFIG_DIR` | 설정 디렉터리 재정의 |
| `CIEL_RUNTIME_ROUTER_PORT` | 라우터 포트 재정의 |
| `CIEL_RUNTIME_ROUTER_CLIENT_HOST` | 라우터 클라이언트 바인드 호스트 |
| `CIEL_RUNTIME_UPSTREAM_USER_AGENT` | 업스트림 User-Agent (기본: `claude-cli`) |
| `CIEL_RUNTIME_EVENT_LOG` | 이벤트 로그 활성화 (기본: `true`) |
| `CIEL_RUNTIME_EVENT_LEVEL` | 이벤트 로그 레벨 (기본: `info`) |
| `CIEL_RUNTIME_EVENT_BUFFER` | 이벤트 버퍼 크기 (기본: `1000`) |
| `CIEL_RUNTIME_THINKING_PASSBACK_MAX` | Thinking 패스백 최대 토큰 (기본: `4096`) |
| `CIEL_RUNTIME_PYTHON` | 사용할 Python 실행 파일 경로 |
| `CIEL_RUNTIME_SKIP_POSTINSTALL_STOP` | npm 설치 후 stop 건너뜀 |

---

## 설정 마이그레이션

`apply_config_migrations(cfg)` — 구버전 설정을 현재 형식으로 자동 변환.

---

## 설정 캐시

`load_config()`는 내부 캐시를 사용한다.  
`invalidate_config_cache()` — 캐시 무효화.  
`clear_model_cache()` — 모델 캐시 초기화.

---

## 로그 레벨

| 레벨 | 값 |
|------|-----|
| SILENT | 0 |
| ERROR | 1 (기본) |
| WARN | 2 |
| INFO | 3 |
| DEBUG | 4 |
| TRACE | 5 |

설정 방법:
```bash
ciel-runtimectl log-level DEBUG
```

파일 `log-level`에 레벨 문자열을 저장하거나,  
`config.json`의 `"log_level"` 키로도 설정 가능.

---

## 언어 설정

지원 언어:

| 코드 | 언어 |
|------|------|
| `en` | English |
| `ko` | 한국어 |
| `ja` | 日本語 |
| `zh` | 中文 |

```bash
ciel-runtimectl language ko
```

---

## 관련 문서
- [[CLI-Reference]] — 설정 변경 CLI 커맨드
- [[Providers]] — 제공자별 설정 옵션
- [[Observability]] — 이벤트 로그 설정
