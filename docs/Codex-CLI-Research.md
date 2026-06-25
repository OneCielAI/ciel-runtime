# Codex CLI 지원 분석

> **상태**: Phase 1 구현 시작 (2026-06-24)  
> **대상**: OpenAI Codex CLI `codex-cli 0.142.0`  
> **핵심 결론**: Codex 기본 라우팅은 가능하다. 외부 SSE 이벤트로 "잠든" Codex를 깨우는 기능은 TUI stdin이 아니라 **Codex App Server**를 주 경로로 설계해야 한다.

---

## 1. Claude Code vs Codex CLI

| 항목 | Claude Code | Codex CLI |
|------|-------------|-----------|
| 런타임 | Node.js / TypeScript | Rust |
| 기본 API | Anthropic Messages API | OpenAI Responses API |
| 라우터 엔드포인트 | `POST /v1/messages` | `POST /v1/responses` |
| 스트리밍 | Anthropic SSE | Responses API SSE |
| 라우터 주입 | `ANTHROPIC_BASE_URL` 환경변수 | `-c model_providers...` CLI override |
| 설정 파일 수정 필요 | 없음 | 없음 |
| 툴 형태 | Claude Code tool schema | Responses `function` tools |
| idle wake | stdin/PTY 프록시가 작동 | App Server 또는 새 turn 필요 |

기존 문서의 `~/.codex/config.toml` 직접 수정 방식은 더 이상 권장하지 않는다. Codex는 `-c key=value` 오버라이드로 `model_provider`와 `model_providers.<id>`를 실행 시점에 주입할 수 있으므로 사용자 홈 설정을 건드릴 필요가 없다.

---

## 2. 구현된 런처 방식

```bash
ciel-runtime codex
ciel-runtime --ca-runtime codex -- exec "hello"
```

런처는 Codex에 다음 설정을 명령줄로 전달한다.

```toml
model_provider = "ciel-runtime"

[model_providers.ciel-runtime]
name = "Ciel Runtime"
base_url = "http://127.0.0.1:PORT/v1"
wire_api = "responses"
env_key = "CIEL_RUNTIME_CODEX_API_KEY"
request_max_retries = 0
stream_max_retries = 0
```

중요한 점:

- `~/.codex/config.toml`을 수정하지 않는다.
- `base_url`은 `/v1`까지 포함한다. Codex가 여기에 `/responses`를 붙여 호출한다.
- 로컬 라우터 인증용으로 `CIEL_RUNTIME_CODEX_API_KEY`를 세팅한다. 실제 upstream API 키는 기존 ciel-runtime provider 설정이 관리한다.
- 사용자가 `-m` 또는 `--model`을 직접 넘기면 그대로 존중한다. 없으면 현재 ciel-runtime 모델 alias를 `-m`으로 넘긴다.

---

## 3. Responses API 라우팅

Codex는 `POST /v1/responses`에 Responses 형식 요청을 보낸다. 라우터는 이를 내부 Anthropic Messages 형식으로 낮춘 뒤 기존 provider 라우팅을 재사용한다.

### 수신 변환

| Responses 입력 | 내부 Anthropic 변환 |
|----------------|---------------------|
| `instructions` | top-level `system` |
| `input[].role=user` | `messages[].role=user` |
| `input[].role=assistant` | `messages[].role=assistant` |
| `function_call` | assistant `tool_use` |
| `function_call_output` | user `tool_result` |
| `tools[].type=function` | Anthropic `tools[].input_schema` |
| `tool_choice=required` | `{"type": "any"}` |

### 송신 변환

현재 구현은 upstream 응답을 non-stream으로 수집한 뒤 Codex가 기대하는 Responses SSE로 다시 내보낸다.

텍스트 응답은 다음 이벤트 생명주기를 사용한다.

```text
response.created
response.output_item.added
response.content_part.added
response.output_text.delta
response.output_text.done
response.content_part.done
response.output_item.done
response.completed
```

검증 결과, 단순히 `response.output_text.delta`와 `response.completed`만 보내면 Codex가 스트림 완료로 인정하지 않을 수 있다. `response.content_part.added/done`까지 포함하는 생명주기가 안전하다.

도구 호출은 `function_call` output item으로 보낸다.

```json
{
  "type": "function_call",
  "call_id": "call_1",
  "name": "shell_command",
  "arguments": "{\"command\":\"pwd\"}"
}
```

Codex는 이 item을 받으면 로컬 tool을 실행하고, 다음 `/v1/responses` 요청에 `function_call_output.call_id`를 포함해 보낸다. 라우터는 이 `call_id`를 Anthropic `tool_result.tool_use_id`로 보존한다.

---

## 4. 모델 목록 엔드포인트

Codex는 시작 시 `GET /v1/models?client_version=...`를 호출한다.

현재 라우터의 OpenAI-style 모델 목록은 Codex 전용 catalog schema와 완전히 일치하지 않을 수 있다. 다만 실험상 모델 목록 호출 실패는 치명적이지 않았고, `/v1/responses` 스트림이 올바르면 세션은 진행된다.

추후 개선:

- 기존 OpenAI `data` 배열은 유지한다.
- Codex가 기대하는 `models` catalog 배열을 병행 제공한다.
- `slug`, `display_name`, context/output token, reasoning preset schema를 실제 Codex 요구사항에 맞춰 검증한다.

---

## 5. 외부 SSE 이벤트 주입: "잠든 상태" 연구

여기서 "잠든 상태"는 Codex TUI 또는 세션이 대기 중이고, 모델 turn이 실행 중이 아니며, MCP tool 호출도 진행 중이 아닌 상태를 의미한다. 이 상태에서는 외부 SSE를 단순히 Responses SSE로 밀어 넣을 수 없다. `/v1/responses` SSE는 **모델 응답 스트림**이지, idle client를 깨우는 inbound event bus가 아니다.

### 권장 경로: Codex App Server

가장 가능성이 높은 방식은 `codex app-server`를 장기 실행하고 외부 채널 브리지가 JSON-RPC로 새 turn을 시작하는 것이다.

예상 흐름:

```text
외부 SSE 채널
  -> ciel-runtime channel bridge
  -> Codex App Server JSON-RPC
  -> thread/resume 또는 thread/start
  -> turn/start 로 wake prompt 주입
  -> Codex가 /v1/responses 호출
  -> ciel-runtime router가 기존 provider로 라우팅
```

역할 분리:

- `turn/start`: idle thread에 새 사용자 입력을 넣는 wake 경로.
- `thread/resume`: 기존 Codex thread를 다시 열 때 사용.
- `turn/steer`: 이미 실행 중인 turn에만 적합하다. idle wake 용도로 보면 안 된다.

구현 시 필요한 상태:

- 외부 채널별 target Codex thread id 매핑.
- 이벤트 idempotency key. 같은 SSE 이벤트를 재연결 후 중복 주입하지 않기 위함.
- wake prompt 포맷. 기존 `format_channel_wake_prompt()`와 유사하되 Codex tool 이름과 정책에 맞춰 조정.
- App Server 연결 끊김 시 재시작/재연결 정책.

### 보조 경로: MCP

Codex는 stdio 및 Streamable HTTP MCP 서버를 지원한다. 라우터의 channel backlog를 MCP tool/resource로 노출하면 모델이 다음 turn에서 외부 메시지를 조회할 수 있다.

제약:

- MCP notification만으로 idle Codex TUI가 자동으로 새 turn을 시작한다고 보면 안 된다.
- MCP는 "모델이 깨어난 뒤 읽을 수 있는 inbox"로 보는 것이 정확하다.
- App Server `turn/start`와 결합하면, wake prompt가 MCP inbox를 읽도록 유도할 수 있다.

### 자동화 경로: `codex exec resume`

`codex exec`는 CI/자동화에는 적합하다. 외부 SSE 이벤트마다 `codex exec resume --last "<wake prompt>"`류로 처리하는 설계가 가능하다.

제약:

- TUI 세션 자체를 깨우는 방식은 아니다.
- 장기 interactive session의 화면/상태와 분리될 수 있다.
- 실제 thread 재사용 semantics는 별도 검증이 필요하다.

### 비권장: Rust TUI stdin 주입

Claude Code의 stdin wake proxy는 Node readline/TUI 동작에 기대고 있다. Codex는 Rust TUI와 PTY 이벤트 루프를 사용하므로 단순 stdin 텍스트 주입이 같은 의미로 동작한다고 가정하면 위험하다.

비권장 이유:

- raw mode/alternate screen/키 이벤트 상태에 따라 텍스트가 composer에 들어가지 않을 수 있다.
- Enter, paste bracket, escape sequence 처리가 플랫폼별로 다를 수 있다.
- 내부 composer 상태를 모른 채 입력을 밀어 넣으면 사용자의 현재 입력을 훼손할 수 있다.

### 비권장: hooks/notify

Codex hooks와 notify는 lifecycle/outbound 성격이다. 외부 SSE를 idle Codex에 inbound로 주입하는 wake 채널로는 적합하지 않다.

---

## 6. 구현 상태

### 완료

- `ciel-runtime codex [args...]`
- `ciel-runtime --ca-runtime codex`
- Codex provider 설정을 `-c model_providers...`로 주입
- `POST /v1/responses` 수신
- Responses input/tools/tool_choice -> 내부 Anthropic Messages 변환
- Anthropic message -> Responses JSON/SSE 변환
- 텍스트 SSE 생명주기와 `function_call` item 출력
- 단위 테스트: 변환, SSE 이벤트, 런처 명령 조립

### 남음

- Codex 전용 `/v1/models?client_version=...` catalog schema 보강
- streaming upstream을 Responses SSE로 실시간 변환
- App Server 기반 외부 SSE wake bridge 구현
- Codex MCP inbox/tool config 자동 주입
- Codex tool dialect 세부 보정. 현재는 function 이름을 보존하는 방식으로 시작한다.

---

## 7. 권장 로드맵

1. **Phase 1: 기본 라우팅**
   - Codex가 현재 ciel-runtime provider를 통해 응답하고 tool call roundtrip을 수행하는지 검증.
   - 최소 smoke: text answer, `shell_command`, `apply_patch`.

2. **Phase 2: App Server wake**
   - `codex app-server` 연결 관리자 추가.
   - 외부 SSE event -> `thread/resume` + `turn/start`로 wake prompt 주입.
   - 중복 이벤트 방지와 thread 매핑 저장.

3. **Phase 3: MCP inbox**
   - channel backlog를 Codex MCP server로 노출.
   - App Server wake prompt가 MCP inbox를 읽도록 연결.

4. **Phase 4: 실시간 스트리밍**
   - Anthropic/OpenAI/Ollama upstream streaming을 Responses SSE로 직접 변환.
   - 현재 non-stream collect 방식보다 latency를 낮춘다.

---

## 관련 공식 문서

- OpenAI Codex advanced config: https://developers.openai.com/codex/config-advanced
- OpenAI Codex config reference: https://developers.openai.com/codex/config-reference
- OpenAI Codex MCP: https://developers.openai.com/codex/mcp
- OpenAI Codex App Server: https://developers.openai.com/codex/app-server
- OpenAI Responses streaming: https://developers.openai.com/api/docs/guides/streaming-responses

