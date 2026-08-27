# CLI Reference — CLI 커맨드 전체 참조

> 소스: `ciel_runtime.py` — `main()`, `cmd_*` 함수들  
> 메인 진입점: `ciel-runtime`, `ciel-runtimectl`

---

## ciel-runtime

기본값은 Claude Code를 Router를 통해 실행한다. 다른 런타임은 명시적으로 선택한다.

```bash
ciel-runtime [OPTIONS] [-- RUNTIME_ARGS...]
ciel-runtime codex [CODEX_ARGS...]
ciel-runtime zcode [ZCODE_ARGS...]
ciel-runtime --ca-runtime codex -- exec "hello"
```

### 주요 옵션

| 옵션 | 설명 |
|------|------|
| `--ca-provider <name>` | 사용할 제공자 설정 후 실행 |
| `--ca-model <id>` | 현재 제공자 모델 설정 후 실행 |
| `--ca-base-url <url>` | 현재 제공자 기본 URL 설정 후 실행 |
| `--ca-api-key <key>` | 현재 제공자 API 키 설정 후 실행 |
| `--ca-runtime claude\|codex\|codex-app-server\|agy\|grok\|zcode` | 실행 런타임 선택 |
| `--ca-no-launch` | 설정만 적용하고 런타임 실행 생략 |
| `--` | 이후 인자를 선택된 런타임에 그대로 전달 |

### Codex 실행

```bash
ciel-runtime codex
ciel-runtime codex --no-alt-screen
ciel-runtime --ca-provider ollama --ca-runtime codex -- exec "작업 내용"
```

Codex 런처는 `~/.codex/config.toml`을 수정하지 않는다. 실행 시점에 `-c model_providers.ciel-runtime...` 오버라이드를 붙여 로컬 Router를 OpenAI Responses provider로 등록한다.

### ZCode 실행

```bash
ciel-runtime zcode
ciel-runtime --ca-runtime zcode -- --continue
ciel-runtimectl launch-zcode --prompt "작업 내용"
ciel-runtime zcode login --oauth --profile coding-plan
ciel-runtime zcode login --oauth --profile start-plan
```

ZCode는 워크스페이스별 Ciel 관리 홈과 `ZCODE_STORAGE_DIR`을 사용한다. 선택된
provider/model은 로컬 Anthropic Router 설정으로 투영되며 사용자의 일반
`%USERPROFILE%\.zcode` 또는 `~/.zcode` 설정은 덮어쓰지 않는다. `ciel-runtime
zcode login --oauth`와 API-key 패널의 Z.AI OAuth는 별도 `zai-coding-plan`
profile에 Coding Plan 키를 저장하므로 다른 routed runtime도 같은 키를 사용한다.
기존 `zai` 수동 키는 변경하지 않는다.

---

## ciel-runtimectl

설정 및 Router 제어 CLI.

```bash
ciel-runtimectl <subcommand> [args]
```

---

### 제공자/모델 설정

#### `provider`
```bash
ciel-runtimectl provider [NAME]
```
현재 활성 제공자 조회 또는 변경.

```bash
ciel-runtimectl provider ollama
ciel-runtimectl provider anthropic
ciel-runtimectl provider deepseek
```

#### `model`
```bash
ciel-runtimectl model [MODEL_ID]
```
현재 모델 조회 또는 변경.

```bash
ciel-runtimectl model qwen3-coder:30b
ciel-runtimectl model claude-sonnet-4-6
```

#### `models`
```bash
ciel-runtimectl models [PROVIDER]
```
사용 가능한 모델 목록 출력.

#### `base-url`
```bash
ciel-runtimectl base-url [PROVIDER] [URL]
```
제공자 기본 URL 설정.

```bash
ciel-runtimectl base-url ollama http://remote-server:11434
```

#### `provider-options` — Z.AI Start Plan remote CAPTCHA

원격 Linux 호스트에서 Z.AI Start Plan의 사람 검증 페이지를 운영자 브라우저로
열어야 할 때는 접근 가능한 인터페이스만 명시적으로 바인딩한다. 기본값은 계속
`127.0.0.1`이며 외부에 공개되지 않는다.

```bash
ciel-runtimectl provider-options zai-start-plan \
  captcha_bind_host=100.95.132.58 \
  captcha_port=42119 \
  'captcha_public_base_url=http://100.95.132.58:{port}'
```

`captcha_public_base_url`은 `http://` 또는 `https://` origin만 허용하며,
`{port}`는 실제 CAPTCHA 수신 포트로 치환된다. URL에는 요청마다 새로 생성되는
state 값이 추가된다.

---

### API 키 관리

#### `zai-oauth`

```bash
ciel-runtimectl zai-oauth login --profile coding-plan
ciel-runtimectl zai-oauth login --profile coding-plan --no-browser
ciel-runtimectl zai-oauth status --profile coding-plan
ciel-runtimectl zai-oauth logout --profile coding-plan
ciel-runtimectl zai-oauth login --profile start-plan
ciel-runtimectl zai-oauth import --profile start-plan
ciel-runtimectl zai-oauth status --profile start-plan
ciel-runtimectl zai-oauth logout --profile start-plan
```

기본 profile은 `coding-plan`이다. 두 profile의 `login`은 설치된 공식 ZCode CLI의
OAuth init/poll 흐름에 위임한다. Coding Plan은 CLI가 생성한 provider API key를,
Start Plan은 공식 공유 credential store의 `zcodejwttoken`을 각각 가져온다. 이미
ZCode 로그인이 완료된 경우 `import --profile start-plan`은 같은 공유 JWT를
`zai-start-plan` profile에 저장한다. Desktop의 선택된 Start Plan 설정은 이전
ZCode 버전을 위한 import fallback으로 유지된다. Start Plan 모델 요청은 요청별 Aliyun CAPTCHA
결과를 받은 뒤 Start Plan 전용 Anthropic endpoint로 계속된다. `logout`은 선택한
profile의 Ciel credential만 지우며 ZCode의 로그인이나 원격 승인을 철회하지 않는다.

#### `api-key`
```bash
ciel-runtimectl api-key [PROVIDER] [KEY]
```
API 키 설정 (단일 키).

```bash
ciel-runtimectl api-key anthropic sk-ant-...
ciel-runtimectl api-key deepseek sk-...
```

#### `api-keys`
```bash
ciel-runtimectl api-keys [PROVIDER] [KEY1] [KEY2] ...
```
다중 API 키 설정 (로테이션용).

---

### Router 관리

#### `bridge`

Router가 실행되는 호스트와 실제 Claude Code/Codex CLI가 실행되는 호스트를
네트워크로 분리한다. 원격 클라이언트는 하나의 인증된 OpenAI/Anthropic 호환
endpoint를 사용한다. 요청마다 구성된 bridge-compatible provider와 upstream model
ID를 선택하며, 허용하는 provider에 한해 request-scoped API key를 지정할 수 있다.

```bash
ciel-runtimectl bridge status
ciel-runtimectl bridge enable --host 0.0.0.0
ciel-runtimectl bridge token
ciel-runtimectl bridge serve --host 0.0.0.0
ciel-runtimectl bridge disable
```

`enable`과 `serve`는 클라이언트 설정을 위해 token 값을 출력한다. `status`는
값을 출력하지 않고 존재 여부만 표시하며, `token`은 값을 명시적으로 출력한다.
Bridge는 별도 고정 포트를 사용하지 않으므로 `status`의 `Listen:`에 표시되는
현재 `ROUTER_PORT`를 클라이언트 base URL에 사용한다.

Bridge token은 Router debug/Web 관리자 token과 분리되어 있고 관리자 API를
승인하지 않으며 `CIEL_RUNTIME_REMOTE_BRIDGE_TOKEN`으로 재정의할 수 있다. 관리자
외부 접근을 별도로 활성화한 경우 관리자 token은 bridge endpoint에도 접근할 수
있는 더 넓은 권한이므로 원격 LLM 클라이언트에 배포하지 않는다.

OpenAI Chat 요청은 선택 모델의 실제 protocol이 Responses, Anthropic Messages,
Ollama Chat이어도 호환 변환할 수 있다. 변환된 `stream: true` Chat 응답은 upstream
완료 결과를 수집한 뒤 합성하는 SSE다. Effort는 Chat의 `reasoning_effort`,
Responses의 `reasoning.effort`, Anthropic Messages의 `output_config.effort`로
지정한다.

Codex CLI 0.150.1에서 `vllm`의 Chat wire처럼 Responses가 아닌 upstream으로
변환하는 route를 사용할 때는 hosted `web_search`를 해당 실행에서 꺼야 한다.
이 도구는 대상 wire에 손실 없이 투영할 수 없으므로 Bridge가 조용히 제거하지 않고
요청을 거부한다.

```bash
codex -c 'web_search="disabled"' --model vllm/my-model
```

이는 non-native Responses 변환 route에만 해당한다. Native Responses upstream을
선택한 route는 hosted `web_search`를 유지할 수 있다. Codex `namespace` tool은
비-Responses wire로 보낼 때 namespace/member 기반의 충돌 방지 이름으로 flatten되며,
동일한 투영 이름이 생기면 요청을 거부한다. 반환된 tool call은 원래 Responses의
`namespace`와 member `name`으로 복원한다. 반환값에 `toolset_name`이 있으면 원래
요청의 namespace와 정확히 일치해야 하며, 일반 tool에 임의 namespace를 만들 수 없다.
Strict bridge route는 tool type을 exact lowercase discriminator로 요구하고 top-level,
namespace, member 이름의 leading/trailing whitespace를 정규화하지 않고 거부한다.
Adapted Anthropic 요청도 client tool·tool choice·tool-use/result identity를 trim/case
정규화하지 않고 strict 경계에서 거부한다.

Codex 0.150.1의 bundled `gpt-5.5` metadata는 native search tool을 지원한다.
기본 multi-agent 도구가 deferred 상태이면 `web_search`와 별개인 hosted
`tool_search`가 추가되며, non-native route는 이 도구도 fail-closed로 거부한다.
다른 deferred MCP/plugin/app/dynamic tool이 없는 실행의 검증된 최소 설정은 다음과
같다. `features.tool_search=false`는 0.150.1에서 제거되어 무시되는 설정이다.

```bash
codex -c 'web_search="disabled"' \
  -c 'features.multi_agent=false' \
  -c 'model_reasoning_effort="low"' \
  --model vllm/gpt-5.5
```

Responses Lite의 선행 `additional_tools`, `reasoning.context=all_turns`, streaming
summary 순서 옵션과 Codex 내부 turn metadata는 검증 후 변환한다. Non-native
Anthropic route의 freeform custom tool은 Codex 0.150.1 공식 `apply_patch.lark`
(기본/optional Environment-ID 변형) 또는 code-mode `exec` grammar와 정확히
일치할 때만(LF/CRLF source line ending은 정규화)
Anthropic 표준 tool로 투영한다. 반환된 raw input도 해당 문법으로 검증한 뒤
`custom_tool_call`로 복원하며 다른 Lark definition과 잘못된·빈 payload는 거부한다.
Adapted Chat route는 object가 아닌 `function.parameters`와 non-null `message.name`을
손실 변환하지 않고 거부한다.

Request-scoped provider key는 저장하지 않으며 Router-host key의 rate-limit 사용량,
학습된 header, backoff penalty, per-key cooldown 상태와 분리한다. GitHub Copilot
OAuth는 Router 호스트에서만 로그인·갱신하고 원격 key override를 거부한다. Copilot
모델 목록은 `model_picker_enabled: true`만 공개하며 `mai-code-1-flash`를 공개 ID로
사용한다. `codex`, `agy`, `zai-start-plan`은 client-local 인증 또는 host-side
브라우저/CAPTCHA 상태에 의존하므로 Remote Bridge에서 제외한다. 전체
endpoint, route header, Codex 및 Claude Code 설정은
[Remote Runtime Bridge](Remote-Bridge.md)를 참고한다.

#### `transcript-events`

```bash
ciel-runtimectl transcript-events enabled=true url=https://memory.example/v1/transcripts authorization='Bearer {MEMORY_TOKEN}'
```

현재 런타임의 Claude/Codex JSONL 트랜스크립트를 등록한 HTTP(S) 주소로 증분
전송한다. URL과 트랜스크립트별 바이트 커서는 워크스페이스 상태에 저장되며 HTTP
2xx 이후에만 전진한다. `start_mode=tail`은 현재 실행 경계 이후만,
`start_mode=beginning`은 파일 처음부터 보낸다. 설정 확인은 인자 없이 실행한다.

#### `remote-memory`

```bash
ciel-runtimectl remote-memory
ciel-runtimectl remote-memory enabled=true manifest_url=https://memory.example/manifest.json sync
```

별도 HTTP manifest에서 워크스페이스 메모리 트리를 동기화한다. `sync`는 설정을
저장한 뒤 즉시 한 번 내려받는다. 시작 시에는 선택된 런타임을 실행하기 전에 자동으로
다시 내려받으며, 시스템 지침 파일 하단에는 로컬 인덱스 주소만 추가한다.

주요 키: `enabled`, `manifest_url`, `authorization`, `directory`,
`timeout_seconds`, `max_manifest_bytes`, `max_file_bytes`, `max_total_bytes`,
`max_files`.

전체 manifest 계약은 [Remote Memory](Remote-Memory.md)를 참고한다.

#### `serve`
```bash
ciel-runtimectl serve [--port PORT]
```
Router를 백그라운드로 시작.

#### `stop`
```bash
ciel-runtimectl stop
# 또는
ciel-runtime-stop
```
실행 중인 Router 중지.

#### `restart`
```bash
ciel-runtimectl restart
```
Router 재시작.

#### `status`
```bash
ciel-runtimectl status
```
현재 설정 및 Router 상태 출력:
- 활성 제공자/모델
- API 키 상태 (마스킹)
- 키 쿨다운 상태
- 컨텍스트 한도
- 채널 상태

---

### 로그 및 진단

#### `log-level`
```bash
ciel-runtimectl log-level [LEVEL]
```
로그 레벨 조회 또는 변경.

```bash
ciel-runtimectl log-level DEBUG
ciel-runtimectl log-level SILENT
```

레벨: `SILENT`, `ERROR`, `WARN`, `INFO`, `DEBUG`, `TRACE`

#### `log`
```bash
ciel-runtimectl log [--tail N] [--follow]
```
Router 로그 출력.

#### `events`
```bash
ciel-runtimectl events [--level LEVEL]
```
실시간 이벤트 스트림 출력.

---

### 기능 설정

#### `language`
```bash
ciel-runtimectl language [CODE]
```
UI 언어 설정.

```bash
ciel-runtimectl language ko   # 한국어
ciel-runtimectl language en   # English
ciel-runtimectl language ja   # 日本語
ciel-runtimectl language zh   # 中文
```

#### `web-search`
```bash
ciel-runtimectl web-search [on|off]
```
웹 검색 MCP 서버 활성화/비활성화.

#### `web-fetch`
```bash
ciel-runtimectl web-fetch [on|off]
```
웹 페치 MCP 서버 활성화/비활성화.

---

### 채널

#### `channel add`
```bash
ciel-runtimectl channel add <SPEC>
```
채널 스펙 추가.

```bash
ciel-runtimectl channel add server:my-mcp-server
ciel-runtimectl channel add plugin:telegram@claude-plugins-official
```

#### `channel remove`
```bash
ciel-runtimectl channel remove <SPEC>
```

#### `channel list`
```bash
ciel-runtimectl channel list
```
현재 설정된 채널 목록.

#### `channel probe`
```bash
ciel-runtimectl channel probe [--refresh]
```
채널 지원 MCP 서버 탐지.

---

### Ollama 전용

#### `ollama-catalog`
```bash
ciel-runtimectl ollama-catalog [--refresh]
```
Ollama 모델 카탈로그 조회 및 갱신.

---

### 기타

#### `advisor-model`
```bash
ciel-runtimectl advisor-model [MODEL_ID]
```
Advisor LLM 모델 설정.

#### `install-statusline`
```bash
ciel-runtimectl install-statusline
```
셸 프롬프트 statusline 설치.

#### `install-diag`
```bash
ciel-runtimectl install-diag
```
설치 진단 실행.

#### `version`
```bash
ciel-runtimectl version
# → 0.1.1
```

---

## ciel-runtime-stop

```bash
ciel-runtime-stop
```

실행 중인 Router 프로세스를 종료한다.  
Windows: `ciel-runtime-stop.cmd` / `ciel-runtime-stop.ps1`  
Linux/macOS: `ciel-runtime-stop`

---

## 환경변수를 통한 일회성 실행

```bash
CIEL_RUNTIME_PROVIDER=deepseek CIEL_RUNTIME_MODEL=deepseek-coder-v2 ciel-runtime
```

---

## 관련 문서
- [[Configuration]] — 설정 파일 구조
- [[Providers]] — 제공자 이름 목록
- [[Installation]] — 설치 방법
