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
