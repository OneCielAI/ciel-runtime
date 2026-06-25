# CLI Reference — CLI 커맨드 전체 참조

> 소스: `ciel_runtime.py` — `main()`, `cmd_*` 함수들  
> 메인 진입점: `ciel-runtime`, `ciel-runtimectl`

---

## ciel-runtime

기본값은 Claude Code를 Router를 통해 실행한다. Codex 런타임은 명시적으로 선택한다.

```bash
ciel-runtime [OPTIONS] [-- RUNTIME_ARGS...]
ciel-runtime codex [CODEX_ARGS...]
ciel-runtime --ca-runtime codex -- exec "hello"
```

### 주요 옵션

| 옵션 | 설명 |
|------|------|
| `--ca-provider <name>` | 사용할 제공자 설정 후 실행 |
| `--ca-model <id>` | 현재 제공자 모델 설정 후 실행 |
| `--ca-base-url <url>` | 현재 제공자 기본 URL 설정 후 실행 |
| `--ca-api-key <key>` | 현재 제공자 API 키 설정 후 실행 |
| `--ca-runtime claude\|codex` | 실행 런타임 선택 |
| `--ca-no-launch` | 설정만 적용하고 런타임 실행 생략 |
| `--` | 이후 인자를 선택된 런타임에 그대로 전달 |

### Codex 실행

```bash
ciel-runtime codex
ciel-runtime codex --no-alt-screen
ciel-runtime --ca-provider ollama --ca-runtime codex -- exec "작업 내용"
```

Codex 런처는 `~/.codex/config.toml`을 수정하지 않는다. 실행 시점에 `-c model_providers.ciel-runtime...` 오버라이드를 붙여 로컬 Router를 OpenAI Responses provider로 등록한다.

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

---

### API 키 관리

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
# → 0.1.0
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
