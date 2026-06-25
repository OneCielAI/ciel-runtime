# Thinking Passthrough — Extended Thinking 지원

> 소스: `ciel_runtime.py` — `normalize_thinking_for_non_anthropic_provider()`, `rehydrate_suppressed_thinking_passback()`

---

## 개요

Claude Code는 Extended Thinking(장기 추론) 기능을 사용한다.  
비-Anthropic 제공자는 대부분 thinking 블록을 지원하지 않으므로,  
Router가 thinking 블록을 적절히 변환하거나 제거한다.

---

## Anthropic Thinking 블록 타입

```python
ANTHROPIC_THINKING_BLOCK_TYPES = ("thinking", "redacted_thinking")
```

---

## 요청 시 처리

### `normalize_thinking_for_non_anthropic_provider()`
비-Anthropic 제공자에 요청 전송 전:
1. `thinking` 파라미터 제거
2. 메시지 내 thinking 블록 제거 (`strip_anthropic_thinking_blocks_from_messages()`)
3. 억제된 thinking 블록을 passback 캐시에 저장

### `anthropic_thinking_requested(body)` 
요청 본문에 thinking이 요청되었는지 확인.

### `anthropic_thinking_block_count(body)`
현재 메시지 히스토리의 thinking 블록 수.

---

## 응답 시 처리

### `normalize_response_thinking_for_non_anthropic_provider()`
비-Anthropic 응답 수신 후:
1. 응답에서 reasoning/thinking 추출 (OpenAI의 `reasoning_content` 등)
2. Anthropic thinking 블록 형식으로 변환

### `openai_reasoning_to_anthropic_thinking_block(reasoning_content)`
OpenAI reasoning → Anthropic thinking 블록 변환.

---

## Thinking Passback 캐시

억제된 thinking 블록을 임시 저장해 다음 요청에 재주입한다.

```python
SUPPRESSED_THINKING_PASSBACK_MAX = 4096  # 최대 토큰 수
SUPPRESSED_THINKING_PASSBACK_CACHE: list[dict]
```

- `remember_suppressed_thinking_passback(provider, model, blocks)` — 저장
- `rehydrate_suppressed_thinking_passback(provider, pcfg, body)` — 재주입
- `clear_suppressed_thinking_passback_cache()` — 초기화

### 환경변수
- `CIEL_RUNTIME_THINKING_PASSBACK_MAX` — 최대 passback 토큰 수 (기본: 4096)

---

## 제공자별 Thinking 지원

`preserves_anthropic_thinking_contract(provider, pcfg)`:
- Anthropic native: ✅ 완전 지원
- 비-Anthropic: thinking 파라미터 제거 후 passback 방식 사용

`openai_chat_reasoning_passback_enabled(provider, model, pcfg)`:
- OpenAI compatible 제공자의 reasoning passback 활성화 여부

---

## Forced Tool Choice와 Thinking

thinking 요청 중 forced tool choice 처리:

`should_defer_forced_tool_choice_for_thinking()`:
- thinking 활성화 상태에서 forced tool choice를 지연시켜 충돌 방지

---

## 관련 문서
- [[Providers]] — 제공자별 thinking 지원
- [[Router]] — 요청 정규화 흐름
- [[Plan-Mode]] — Plan Mode와 thinking 상호작용
