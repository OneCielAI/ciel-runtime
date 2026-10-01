# Codex `--remote` TUI 런타임 (`codex-remote`)

Ciel Runtime이 라우팅된 `codex app-server`를 띄우고, 그 위에 Codex TUI를
`codex --remote ws://...`로 붙여 실행한다.

```text
ciel-runtime codex-remote
ciel-runtime --ca-runtime codex-remote
```

## 일반 `codex` 런타임과 다른 점

| 항목 | `codex` (일반 TUI) | `codex-remote` |
|---|---|---|
| 채널 메시지 전달 | 콘솔 입력(ConPTY/PTY)에 타이핑 | app-server JSON-RPC (`turn/start`, 실행 중이면 `turn/steer`) |
| 전달 확인 | 트랜스크립트(rollout)에서 프롬프트 레코드 확인 | `turn/completed` 알림 |
| `compact_session` | `/compact` 타이핑 | `thread/compact/start` |
| `new_session` | `/new` 타이핑 | `/new` 타이핑 (TUI는 자기 스레드를 바꾸는 외부 API가 없음) |
| `restart_session` | TUI 재실행 (`resume --last`) | TUI만 재실행, app-server는 유지 (`resume <thread id>`) |

- 채널 클라이언트는 TUI가 만든 스레드를 `thread/started` 알림으로 찾아
  따라간다. TUI에서 `/new`를 하면 새 스레드로 대상이 옮겨진다.
- app-server 로그: `<config dir>/codex-remote/<workspace id>/app-server.log`.
- listen 주소는 `codex-app-server`/`codex-desktop`과 같다
  (`CIEL_RUNTIME_CODEX_APP_SERVER_LISTEN`, 기본 `ws://127.0.0.1:<router port + 20>`).

## 세션 제어 MCP 도구 (모든 런타임 공통)

라우터 MCP 서버(`ciel-runtime-router`)의 `compact_session`, `new_session`은
요청을 한 칸짜리 큐에 넣고, 실행 중인 세션이 현재 턴이 끝난 뒤 처리한다.

| 런타임 | `compact_session` | `new_session` |
|---|---|---|
| Claude Code | `/compact` | `/clear` (새 세션 id) |
| Codex TUI | `/compact` | `/new` |
| `codex-remote` | `thread/compact/start` | `/new` |
| `codex-app-server`, `codex-desktop` | `thread/compact/start` | `thread/start` (채널 대상이 새 스레드로 이동) |

`codex-app-server`(bare)에서도 ws:// 리슨이면 채널 클라이언트가 붙어, 다른
클라이언트가 만든 스레드를 따라가며 메시지/compact/new를 JSON-RPC로 처리한다.
