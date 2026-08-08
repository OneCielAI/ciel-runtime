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
| `router-external-token` | 외부 디버그 접근용 Bearer 토큰 (`0600`) |
| `router-activity.json` | 라우터 활동 상태 |
| `rate-limit-state.json` | 레이트 리밋 상태 |
| `requests.jsonl` | 요청 덤프 (디버그용, 최대 5MB) |
| `responses.jsonl` | 응답 덤프 (디버그용, 최대 5MB) |
| `usage-events.jsonl` | provider/model별 token usage event (10MB에서 1세대 회전) |
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
| `CIEL_RUNTIME_ROUTER_EXTERNAL_TOKEN` | 외부 디버그 접근용 Bearer 토큰 재정의 |
| `CIEL_RUNTIME_UPSTREAM_USER_AGENT` | 업스트림 User-Agent (기본: `claude-cli`) |
| `CIEL_RUNTIME_EVENT_LOG` | 이벤트 로그 활성화 (기본: `true`) |
| `CIEL_RUNTIME_EVENT_LEVEL` | 이벤트 로그 레벨 (기본: `info`) |
| `CIEL_RUNTIME_EVENT_BUFFER` | 이벤트 버퍼 크기 (기본: `1000`) |
| `CIEL_RUNTIME_USAGE_LOG` | token usage JSONL 기록 활성화 (기본: `true`) |
| `CIEL_RUNTIME_THINKING_PASSBACK_MAX` | Thinking 패스백 최대 토큰 (기본: `4096`) |
| `CIEL_RUNTIME_PYTHON` | 사용할 Python 실행 파일 경로 |
| `CIEL_RUNTIME_SKIP_POSTINSTALL_STOP` | npm 설치 후 stop 건너뜀 |
| `CIEL_RUNTIME_RUNAWAY_GUARD` | 반복 폭주 가드 (기본: 켜짐, `off`로 비활성화) |
| `CIEL_RUNTIME_RUNAWAY_CONTINUE` | 루프 감지 후 턴 이어가기 (기본: 켜짐, `off`면 감지만 하고 종료) |
| `CIEL_RUNTIME_RUNAWAY_RETRIES` | 수집 경로 재시도 횟수 (기본: `2`, 최대 `4`) |
| `CIEL_RUNTIME_RUNAWAY_MIN_REPEATS` | 연속 반복 최소 횟수 (기본: `10`) |
| `CIEL_RUNTIME_RUNAWAY_MIN_CHARS` | 반복 구간 최소 길이 (기본: `2000`) |
| `CIEL_RUNTIME_RUNAWAY_MAX_PERIOD` | 반복 블록 최대 길이 (기본: `4096`) |
| `CIEL_RUNTIME_RUNAWAY_MIN_DENSITY` | 비연속 반복 최소 밀도 % (기본: `70`) |

---

## 반복 폭주 가드

모델이 같은 문장을 끝없이 되풀이하는 생성 루프에 빠지면 라우터가 이를 끊는다.
업스트림 연결을 즉시 닫으므로 남은 루프는 생성되지도, 과금되지도 않는다.

### 판정

두 가지 정확 규칙으로만 판단한다. 의미 판단이나 유사도 점수는 쓰지 않는다.

1. **연속 반복** — 같은 블록이 바로 뒤에 붙어 `MIN_REPEATS`회 이상,
   `MIN_CHARS`자 이상 반복될 때.
2. **비연속 반복** — 같은 블록이 사이사이 다른 문구를 끼고 되풀이될 때.
   횟수는 2배, 그리고 해당 구간의 `MIN_DENSITY`% 이상이 그 블록 자체여야 한다.

2번은 임계값이지 증명이 아니다. 실제로 대부분이 한 블록의 반복인 정상 출력
(데이터가 거의 없는 표, 거의 동일한 로그 줄 묶음)은 같은 밀도에 근접할 수 있다.
그런 워크로드에서는 `CIEL_RUNTIME_RUNAWAY_MIN_DENSITY`를 올리거나
`CIEL_RUNTIME_RUNAWAY_GUARD=off`로 끄면 된다. 1번 규칙은 그런 판단이 필요 없다.

### 감지 후 동작

턴을 죽이지 않고 이어가는 것이 기본이다. 무엇을 할 수 있는지는 경로마다 다르다.

- **수집 경로** (Codex): 클라이언트로 나간 바이트도, 실행된 툴도 없으므로 응답을
  버리고 **다시 요청한다.** 사용자에게는 아무 메시지도 보이지 않는다. 재시도는
  같은 조건 → `effort=high` → `effort=low`(사고 끄기) 순으로 올라간다.
  DeepSeek이 문서에서 권하는 대응(*"Retry or lower reasoning effort"*)과 같고,
  샘플링이 확률적(`do_sample: true, temperature: 1.0`)이라 재시도는 실제로 다른 결과다.
  Ollama 업스트림은 스트림으로 읽어 조립하므로 루프가 다 만들어지기 전에 끊긴다.
- **스트리밍 경로** (Claude Code): 이미 나간 바이트는 되돌릴 수 없으므로 재시도가
  불가능하다. 대신 짧은 알림과 함께 `TaskList` 툴 호출을 합성해 CLI가 다음 턴을
  가져가게 한다. 직전 어시스턴트 턴에 같은 알림이 이미 있으면 합성하지 않고
  종료한다 — 복구 자체가 루프가 되는 것을 막는다.

알림 문구에는 측정값을 넣지 않는다. 그 텍스트는 어시스턴트 메시지에 남아 다음 턴에
모델이 다시 읽기 때문이다. 반복 블록 길이·횟수·원문은 라우터 로그
(`collect_runaway_repetition`, `ollama_stream_runaway_repetition` 등)에만 기록된다.

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

## 외부 라우터 접근

라우터는 기본적으로 `127.0.0.1`에만 바인딩된다. 외부 디버그 접근을 활성화하면
`router-external-token` 파일에 제한된 권한으로 토큰을 생성한다. 외부 클라이언트는
모든 요청에 다음 헤더를 보내야 한다.

```http
Authorization: Bearer <token>
```

`CIEL_RUNTIME_ROUTER_EXTERNAL_TOKEN` 환경 변수로 파일 토큰을 재정의할 수 있다.
루프백 요청은 기존 로컬 CLI 호환성을 위해 토큰 없이 허용된다.

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
