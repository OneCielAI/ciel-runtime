# MCP & Channels — MCP 서버 연동 및 채널 시스템

> 소스: `ciel_runtime.py` — `detect_channel_capable_mcp_servers()`, `channel_specs_for_launch()`, `probe_*` 함수군

---

## 개요

**채널(Channel)** 시스템은 MCP(Model Context Protocol) 서버를 통해  
외부 메시지(Telegram, Discord, iMessage 등)를 Claude Code 세션으로 전달하는 기능이다.

---

## 채널 스펙 형식

```
server:<mcp-server-name>         # MCP 서버 이름으로 지정
plugin:<name>@<registry>         # 플러그인 레지스트리에서 설치
```

### 빌트인 채널
```python
BUILTIN_CHANNEL_SPEC = "server:ciel-runtime-router"
```

### 공식 플러그인 채널
```python
OFFICIAL_CHANNEL_PLUGINS = {
    "telegram":  "plugin:telegram@claude-plugins-official",
    "discord":   "plugin:discord@claude-plugins-official",
    "imessage":  "plugin:imessage@claude-plugins-official",
    "fakechat":  "plugin:fakechat@claude-plugins-official",
}
```

---

## MCP 서버 탐색

### 설정 파일 탐색 경로

`claude_mcp_config_paths()` — 아래 위치에서 MCP 설정 JSON 탐색:
1. Claude Code 글로벌 설정 (`~/.claude/settings.json`)
2. 프로젝트별 설정 (`.mcp.json` 등)
3. passthrough에 포함된 경로

### 프로브 전략

각 MCP 서버의 채널 지원 여부를 프로브로 탐지한다.

| 전략 | 함수 |
|------|------|
| `stdio` | `probe_stdio_mcp_for_channel_capability_detailed()` |
| `sse` | `probe_sse_mcp_for_channel_capability_detailed()` |
| `streamable-http` | `probe_streamable_http_mcp_for_channel_capability_detailed()` |

탐지 조건: MCP initialize 응답에 채널 capability가 포함된 경우.

### 프로브 캐시

`read_channel_probe_cache()` / `_write_channel_probe_cache()`  
결과를 캐시하여 재실행 시 재탐지를 생략한다.

`refresh_channel_probe_cache()` — 강제 갱신.

---

## MCP 프록시

외부 MCP 서버를 Router가 프록시로 관리하는 경우:

- `MCP_PROXY_CONFIG` = `CONFIG_DIR / "mcp-proxy.json"`
- `proxy_owned_channel_server_names()` — Router가 소유한 채널 서버 이름 목록
- `start_router_managed_channel_sse()` — Router 관리 채널 SSE 시작

---

## 채널 메시지 흐름

```
외부 메시지 소스 (Telegram 등)
        │
MCP 채널 서버 (stdio/SSE)
        │  MCP notifications/claude/channel
        ▼
ciel-runtime Router
        │  stdin inject
        ▼
Claude Code 세션 (프롬프트로 전달)
```

채널 메시지는 `CHAT_MESSAGES_PATH`(`chat-messages.jsonl`)에 기록된다.

---

## 채널 배달 모드

`normalize_channel_delivery()` / `channel_delivery_mode()`:

| 모드 | 동작 |
|------|------|
| `auto` | 자동 선택 |
| `stdin` | stdin 주입 방식 |
| `native` | Native MCP 알림 방식 |

---

## Native MCP 설정

채널용 Native MCP 설정은 `NATIVE_MCP_CONFIG` = `CONFIG_DIR / "native-mcp.json"`에 자동 생성된다.

`write_native_mcp_config_from_discovery()` — 탐지된 채널 서버로 설정 파일 생성.

---

## 채널 LLM 연동

채널 메시지에 LLM 응답을 자동으로 생성하는 기능:

- `CHANNEL_LLM_CURSOR_PATH` — LLM 처리 커서
- `CHANNEL_LLM_SUMMARY_QUEUE_PATH` — 요약 큐
- `CHANNEL_LLM_LAUNCH_GUARD_PATH` — 중복 실행 방지
- `CHANNEL_LLM_LAUNCH_RECENT_SECONDS_DEFAULT` = 600초

---

## 중복 제거

MCP 알림 중복 방지:

- `_MCP_NOTIFICATION_DEDUP_TTL_SECONDS` = 3.0초
- `_MCP_NOTIFICATION_DEDUP_RECENT` — 최근 알림 해시 기록

---

## 채널 SSE 연결

Router가 관리하는 SSE 연결 상태:

- `_CHANNEL_SSE_CONNECTIONS` — 활성 SSE 연결 딕셔너리
- `_CHANNEL_SSE_RPC_CONDITION` — RPC 응답 대기 조건 변수
- `_CHANNEL_MCP_SESSIONS` — MCP 세션 상태

---

## 관련 설정 파일

| 파일 | 용도 |
|------|------|
| `channel-mcp.json` | 채널 MCP 설정 |
| `native-mcp.json` | Native MCP 자동 생성 설정 |
| `channel-probe-cache.json` | 채널 프로브 캐시 |
| `channel-mcp-cursor.json` | MCP 커서 상태 |
| `channel-llm-cursor.json` | LLM 커서 상태 |
| `channel-llm-clear-floor.json` | LLM 플로어 초기화 |
| `channel-llm-launch-guard.json` | LLM 실행 가드 |
| `channel-llm-summary-queue.jsonl` | LLM 요약 큐 |

---

## 관련 문서
- [[Configuration]] — 채널 설정 방법
- [[Router]] — Router 서버
- [[CLI-Reference]] — 채널 관련 CLI 커맨드
