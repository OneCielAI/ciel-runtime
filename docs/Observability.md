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

### 최근 이벤트 JSON
```
GET /ca/events/recent
```

### Web UI
`render_events_html()` — 이벤트를 시각화하는 HTML 페이지.  
브라우저에서 `http://localhost:<port>/ca/events` 접속.

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
