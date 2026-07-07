# Router — HTTP 라우터

> 소스: `ciel_runtime.py` — `RouterHandler`, `serve()`, `post_json_with_rate_retry()`, `forward_*` 함수군

---

## 개요

`RouterHandler`는 `ThreadingHTTPServer` 위에서 동작하는 HTTP 핸들러다.  
Claude Code의 Anthropic Messages 요청과 Codex의 OpenAI Responses 요청을 받아 설정된 제공자로 중계한다.

```
POST /v1/messages          → 모델 라우팅 (핵심 엔드포인트)
POST /v1/responses         → Codex / OpenAI Responses 라우팅
GET  /ca/events/stream     → SSE 이벤트 스트림
GET  /ca/events/recent     → 최근 이벤트 JSON
GET  /ca/health            → 라우터 상태 확인
GET  /v1/models            → 사용 가능한 모델 목록
```

---

## 시작 포트 계산

포트는 사용자별로 고정되어 충돌을 방지한다.

```python
# 기본 포트 계산 로직
base = 8799
port = base + (uid % 1000)         # Linux/macOS: uid 사용
port = base + (sha256(user|home) % 1000)  # Windows: 해시 사용
```

- 환경변수 `CIEL_RUNTIME_ROUTER_PORT`로 직접 지정 가능.
- 기본 바인드 호스트: `127.0.0.1` (루프백 전용)

---

## 요청 흐름

```
1. RouterHandler.do_POST() 수신
2. 요청 본문 JSON 파싱
3. 설정에서 활성 제공자/모델 조회 (load_config())
4. `/v1/responses`는 먼저 Responses 입력을 내부 Anthropic Messages 형식으로 변환:
   - `openai_responses_to_anthropic_messages()`
   - `responses_tools_to_anthropic()`
   - `responses_tool_choice_to_anthropic()`
5. body 정규화:
   - normalize_thinking_for_non_anthropic_provider()
   - normalize_tool_choice_for_provider()
   - strip_anthropic_thinking_blocks_from_messages()
   - normalize_anthropic_tool_turns_for_provider()
6. 제공자별 forward/collect 함수 호출:
   - forward_ollama_api_chat()         (Ollama)
   - forward_openai_compatible_chat()  (OpenAI 호환 제공자들)
   - collect_provider_message_for_responses() (`/v1/responses`)
7. 응답 변환:
   - ollama_chat_to_anthropic()
   - openai_chat_to_anthropic()
   - anthropic_message_to_openai_response()
8. Claude Code에는 Anthropic SSE, Codex에는 Responses SSE 형식으로 응답
```

---

## 제공자별 Forward 함수

| 함수 | 대상 제공자 |
|------|-----------|
| `forward_ollama_api_chat()` | `ollama`, `ollama-cloud` |
| `forward_openai_compatible_chat()` | `deepseek`, `vllm`, `lm-studio`, `nvidia-hosted`, `self-hosted-nim`, `openrouter`, `fireworks`, `kimi` |
| `collect_provider_message_for_responses()` | Codex `/v1/responses` 요청의 provider별 non-stream 수집 |
| OpenCode: Anthropic / OpenAI / Responses 분기 | `opencode`, `opencode-go` |
| ZAI: Anthropic Messages 형식 | `zai` |

---

## 재시도 로직

`post_json_with_rate_retry()` / `open_provider_request_with_key_retry()`:

- 레이트 리밋(429) 감지 → `Retry-After` 헤더 파싱 후 대기
- 업스트림 오류 재시도: `configured_gateway_retries(pcfg)` 횟수만큼
- 재시도 대기: 지수 백오프 (`upstream_retry_wait_seconds(attempt)`)

---

## SSE 스트림 변환

Claude Code는 Anthropic SSE 형식을 기대한다. Codex는 Responses SSE 형식을 기대한다.

Claude Code 경로는 각 제공자 응답을 Anthropic SSE로 변환한다:

- `_ollama_stream_to_anthropic_sse()` — Ollama → Anthropic SSE
- `stream_openai_chat_to_anthropic_sse()` — OpenAI Chat → Anthropic SSE
- `_rebatch_anthropic_sse_text()` — Anthropic SSE 재배치 (단어 단위 버퍼링)

스트리밍 이벤트 예:
```
event: message_start
data: {"type": "message_start", "message": {...}}

event: content_block_start
data: {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}}

event: content_block_delta
data: {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "..."}}

event: message_stop
data: {"type": "message_stop"}
```

Codex 경로는 `write_openai_responses_response()`가 Responses SSE를 생성한다. 텍스트 응답은 `response.created`, `response.output_item.added`, `response.content_part.added`, `response.output_text.delta`, `response.output_text.done`, `response.content_part.done`, `response.output_item.done`, `response.completed` 순서를 사용한다. 도구 호출은 `function_call` output item으로 보낸다.

---

## 외부 접근 제어

기본적으로 루프백(127.0.0.1)만 허용.

- `router_request_allowed()` — 요청 허용 여부 검사
- `router_debug_external_access_enabled()` — 외부 접근 허용 여부
- `router_bind_host()` — 실제 바인드 호스트
- `set_router_debug_external_access_config()` — 외부 접근 설정 변경

---

## 건강 확인 (Health Check)

```
GET /ca/health
→ {"status": "ok", "version": "0.1.1", "provider": "...", "model": "...", ...}
```

- `router_health()` — 라우터 상태 JSON 반환
- `router_up()` — 라우터 실행 중 여부 boolean
- `router_health_config_matches_current()` — 현재 설정과 라우터 일치 여부

---

## Advisor 기능

고급 기능: 비-Anthropic 제공자의 응답 품질 향상을 위해 보조 LLM 호출.

- `maybe_handle_advisor_request()` — Advisor 요청 처리 여부 판단
- `refine_message_with_advisor()` — Advisor로 응답 개선
- `call_advisor_text()` — Advisor 텍스트 호출

---

## 관련 문서
- [[Providers]] — 제공자 목록
- [[Configuration]] — 라우터 설정
- [[Observability]] — 이벤트 스트림
- [[Rate-Limiting]] — 재시도 및 레이트 리밋
