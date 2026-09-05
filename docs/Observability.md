# Observability — EventBus, 로그, SSE 이벤트

> 소스: `ciel_runtime_support/observability.py`

---

## 개요

Router의 내부 이벤트를 실시간으로 추적하는 시스템이다.  
`EventBus`를 통해 이벤트를 발행하고, SSE 스트림 또는 HTTP 엔드포인트로 노출한다.

---

## EventBus

```python
EVENT_BUS = EventBus()  # 전역 인스턴스 (ciel_runtime.py)
```

### 이벤트 레벨

| 레벨 | 값 |
|------|-----|
| trace | 10 |
| debug | 20 |
| info | 30 |
| warn | 40 |
| error | 50 |
| fatal | 60 |

기본 레벨: `info`

### EventBus 메서드

```python
# 이벤트 발행
bus.publish(
    level="info",
    category="router.request",
    message="Upstream request started",
    source="router",
    session_id="...",
    request_id="...",
    provider="ollama",
    model="qwen3-coder:30b",
    data={"tokens": 1234}
)

# 최근 이벤트 조회
events = bus.recent(limit=200, min_id=None, level="info", category="router")

# 새 이벤트 대기 (SSE용)
events = bus.wait_after(last_id=42, timeout=15.0)
```

---

## 이벤트 구조

```json
{
  "id": 123,
  "time": "2026-06-24T12:00:00+09:00",
  "ts": 1750000000.0,
  "level": "info",
  "source": "router",
  "category": "router.request",
  "session_id": "...",
  "request_id": "...",
  "provider": "ollama",
  "model": "qwen3-coder:30b",
  "message": "Request forwarded",
  "data": {}
}
```

---

## 민감 정보 자동 redact

`_redact_value()` — 이벤트 data에서 민감 키 자동 마스킹:

마스킹 대상 키:
- `authorization`, `password`, `secret`, `token`, `api_key`, `apikey`
- `access_token`, `refresh_token`
- `_key`, `_token`, `_secret`, `_password` 접미사

→ `"[redacted]"`로 대체.

---

## EventConfig 환경변수

| 환경변수 | 기본값 | 설명 |
|---------|--------|------|
| `CIEL_RUNTIME_EVENT_LOG` | `true` | 이벤트 로그 활성화 |
| `CIEL_RUNTIME_EVENT_LEVEL` | `info` | 최소 로그 레벨 |
| `CIEL_RUNTIME_EVENT_BUFFER` | `1000` | 최대 이벤트 버퍼 크기 |

---

## HTTP 이벤트 엔드포인트

### SSE 스트림
```
GET /ca/events/stream
```
Server-Sent Events로 실시간 이벤트 전송.  
쿼리 파라미터: `?level=debug&category=router`

### WebSocket 스트림
```
GET /ca/events/ws
```
RFC 6455 WebSocket text frame으로 같은 이벤트 JSON을 실시간 전송한다.
`after`, `level`, `category` 쿼리 파라미터를 SSE와 동일하게 지원하며,
원격 연결은 라우터의 기존 외부 접근 토큰 검사를 그대로 거친다.

CLI 툴콜만 구독:
```
ws://127.0.0.1:<port>/ca/events/ws?category=tool.call
```

`tool.call` 이벤트는 라우터 변환 스트림과 Claude/Codex/Muse Code 세션
transcript의 구조화된 툴 시작 레코드에서 생성된다. Windows에서 실행되는
Muse WSL 세션도 `wslpath`로 확인한 세션 저장소를 직접 추적한다. `data`에는 `call_id`, `name`,
`call_type`, `arguments`, `runtime`, `model`이 가능한 범위에서 포함된다.
동일한 `call_id`가 라우터와 transcript 양쪽에서 관측되면 한 번만 전달한다.
`tool_call_events.include_arguments=false`로 설정하면 모든 `tool.call` 이벤트의
인자 값을 제외할 수 있다.

### 최근 이벤트 JSON
```
GET /ca/events/recent
```

### Web UI
`render_events_html()` — 이벤트를 시각화하는 HTML 페이지.  
브라우저에서 `http://localhost:<port>/ca/events` 접속.

---

## TUI 실시간 관찰 API

`/ca/events`는 라우터 운영 이벤트용이다. 실제 coding-agent TUI turn에서 보이는
사용자 입력과 assistant 출력은 별도의 정규화된 관찰 API로 구독한다.

| 엔드포인트 | 용도 |
|---|---|
| `GET /ca/tui/status` | 활성 turn, 최신 cursor, 캡처 범위 확인 |
| `GET /ca/tui/recent?after=0&limit=200` | 메모리 버퍼의 최근 이벤트 조회 |
| `GET /ca/tui/stream?after=0&timeout=300` | SSE 실시간 구독 |
| `GET /ca/tui` | 브라우저용 실시간 모니터 |

`recent`와 `stream`은 `kind=output.text` 또는 `request_id=<id>` 필터를 받을 수
있다. SSE의 각 메시지는 `id`, `event: tui`, JSON `data`를 가지므로 연결이
끊어지면 마지막 `id`를 `after`에 넣어 이어 받을 수 있다. 표준
`Last-Event-ID` 헤더도 지원하므로 `EventSource` 자동 재연결은 같은 cursor에서
계속된다.

```bash
curl -N \
  -H "Authorization: Bearer <router-external-token>" \
  "http://<router-host>:<workspace-port>/ca/tui/stream?after=0"
```

대표 이벤트는 `turn.started`, `input.text`, `output.text.delta`,
`tool.started`, `tool.result`, `output.error`, `turn.completed`, `turn.error`다.
도구 인자와 도구 결과 원문은 파일 내용이나 자격 증명이 포함될 수 있어 보내지
않고 이름·문자 수 같은 메타데이터만 제공한다. hidden thinking도 제공하지 않는다.

이 API는 해당 workspace router를 통과하는 `/v1/messages`, `/v1/responses`,
`/backend-api/codex/responses` traffic만 관찰한다. Router를 우회하는 native traffic,
터미널 화면 픽셀, 로컬 키 입력 자체는 캡처하지 않는다. 각 workspace는 고유 router
port를 사용하므로 원격 클라이언트도 확인하려는 인스턴스의 포트에 연결해야 한다.

환경 변수 `CIEL_RUNTIME_TUI_OBSERVATION=0`으로 끌 수 있으며,
`CIEL_RUNTIME_TUI_OBSERVATION_BUFFER`(기본 2000)로 메모리 이벤트 수를 조정한다.
외부 요청은 다른 Router API와 동일하게 외부 접근 활성화 및 Bearer token 인증이
필수다.

---

## 라우터 로그

`router_log(level, message)`:
- `router.log` 파일에 기록
- 최대 크기: `ROUTER_LOG_MAX_BYTES` = 1MB (순환)
- `current_log_level()` 이상 레벨만 기록

---

## 덤프 파일

| 파일 | 최대 크기 | 용도 |
|------|---------|------|
| `requests.jsonl` | 5MB | 요청 전문 덤프 |
| `responses.jsonl` | 5MB | 응답 전문 덤프 (텍스트 16KB 제한) |
| `router-sse-trace.jsonl` | 2MB | SSE 이벤트 트레이스 (240 이벤트, 4KB/이벤트) |
| `router-last-sse.json` | — | 마지막 SSE 이벤트 |
| `tool-calls.jsonl` | — | 툴 호출 로그 |

---

## Transcript Filter

`ciel_runtime_support/transcript_filter.py`

Claude Code 트랜스크립트 이벤트를 Anthropic 메시지와 구분한다.

차단 대상 transcript 이벤트 타입:
```python
CLAUDE_CODE_TRANSCRIPT_EVENT_TYPES = frozenset({
    "queue-operation",
    "ai-title",
    "agent-name",
    "last-prompt",
    "permission-mode",
    "file-history-snapshot",
})
```

`is_claude_code_transcript_event(message)`:
- `role` 키가 없으면 transcript 이벤트 → 모델에 노출 금지
- 위 타입에 해당하는 `type` 키가 있으면 transcript 이벤트

---

## 관련 문서
- [[Configuration]] — 로그 레벨 설정
- [[Router]] — 이벤트 엔드포인트
