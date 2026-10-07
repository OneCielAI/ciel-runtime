# Codex `--remote` TUI 런타임 (`codex-remote`)

Ciel Runtime이 라우팅된 `codex app-server`를 띄우고, 그 위에 Codex TUI를
`codex --remote ws://...`로 붙여 실행한다.

```text
ciel-runtime codex-remote
ciel-runtime --ca-runtime codex-remote
ciel-runtime --ca-runtime codex-remote --continue      # 현재 폴더의 마지막 Codex 대화
ciel-runtime --ca-runtime codex-remote resume <id>     # 특정 대화 (resume 만 쓰면 목록에서 선택)
```

기본 Codex 런타임은 일반 TUI(`codex`)다. `codex-remote`는 선택해서 쓰는 실행 방식이다.

## 기존 대화 이어받기 (트랜스크립트 호환)

일반 `codex` TUI와 `codex-remote`는 같은 대화 기록(`CODEX_HOME/sessions/.../rollout-*.jsonl`,
`state_5.sqlite`)을 공유한다. 한쪽에서 이어서 쓴 대화를 다른 쪽에서 `--continue`/`resume <id>`로
그대로 열 수 있다. 기록 파일은 하나로 이어 붙고, 대화 id와 작업 폴더도 바뀌지 않는다.

- `--continue`, `--resume`, `resume [--last|<id>|--all]`은 app-server가 아니라 TUI 쪽 요청으로
  처리한다. 대화 선택은 일반 `codex`와 같이 현재 폴더 기준이다.
- 채널 클라이언트가 먼저 그 대화를 `thread/resume`으로 연다. 이때 일반 `codex` 실행과 같은
  권한(`--yolo`: 승인 없음, `danger-full-access`)을 넣는다. 그다음 TUI가
  `codex --remote URL resume <id>`로 붙는다.
  - `--remote` TUI에 `--yolo`를 붙이면 Codex(0.160.0)가 거부한다:
    "Permission overrides are not supported when resuming a remote task."
  - 권한을 넣지 않고 이어받으면, `--yolo`로 만든 대화의 명령이 "blocked by policy"로 거부된다.
- 채널로 넣는 턴(`turn/start`)에도 같은 권한을 넣는다. 새 대화(이어받기 없음)도 마찬가지다.

## 접속 인증

app-server는 실행마다 새로 만든 토큰으로만 접속을 받는다(`--ws-auth capability-token`).
같은 호스트의 다른 계정(샌드박스)이 이 세션에 접속해 턴을 넣을 수 없게 하기 위해서다.

- 토큰 파일: `<config dir>/codex-remote/<workspace id>/ws-token`. 세션이 끝나면 지운다.
- TUI는 `--remote-auth-token-env CIEL_RUNTIME_CODEX_REMOTE_TOKEN`으로, 채널 클라이언트는
  `Authorization: Bearer` 헤더로 접속한다.
- `/readyz`는 토큰 없이 응답한다(Codex 동작).

## 일반 `codex` 런타임과 다른 점

| 항목 | `codex` (일반 TUI) | `codex-remote` |
|---|---|---|
| 채널 메시지 전달 | 콘솔 입력(ConPTY/PTY)에 타이핑 | app-server JSON-RPC (`turn/start`, 실행 중이면 `turn/steer`) |
| 전달 확인 | 트랜스크립트(rollout)에서 프롬프트 레코드 확인 | `turn/completed` 알림 |
| `compact_session` | `/compact` 타이핑 | `thread/compact/start` |
| `new_session` | `/new` 타이핑 | `/new` 타이핑 (TUI는 자기 스레드를 바꾸는 외부 API가 없음) |
| `goal_clear` | `/goal clear` 타이핑 (턴 진행 중에도) | `thread/goal/clear` |
| `restart_session` | TUI 재실행 (`resume --last`) | TUI만 재실행, app-server는 유지 (`resume <thread id>`) |

- 새 대화이면 채널 클라이언트가 TUI가 만든 스레드를 `thread/started` 알림으로 찾아
  따라간다. TUI에서 `/new`를 하면 새 스레드로 대상이 옮겨진다.
- 입력 경로가 `router`로 지정된 메시지는 턴이 진행 중이면 라우터가 다음 모델 요청에 붙이고,
  대화가 쉬고 있으면 `turn/start`로 들어간다(쉬는 대화는 모델 요청을 만들지 않기 때문).
- app-server 로그: `<config dir>/codex-remote/<workspace id>/app-server.log`.
- listen 주소는 `codex-app-server`/`codex-desktop`과 같다
  (`CIEL_RUNTIME_CODEX_APP_SERVER_LISTEN`, 기본 `ws://127.0.0.1:<router port + 20>`).

## 세션 제어 MCP 도구 (모든 런타임 공통)

라우터 MCP 서버(`ciel-runtime-router`)의 `compact_session`, `new_session`, `goal_clear`는
요청을 한 칸짜리 큐에 넣고, 실행 중인 세션이 처리한다. `compact_session`과 `new_session`은
현재 턴이 끝난 뒤 처리하고, `goal_clear`는 기다리지 않는다(활성 목표는 쉬지 않고 턴을 이어가므로).
목표 설정은 각 런타임의 `/goal <조건>`을 그대로 쓴다.

| 런타임 | `compact_session` | `new_session` | `goal_clear` |
|---|---|---|---|
| Claude Code | `/compact` | `/clear` (새 세션 id) | `/goal clear` |
| Codex TUI | `/compact` | `/new` | `/goal clear` |
| `codex-remote` | `thread/compact/start` | `/new` | `thread/goal/clear` |
| `codex-app-server`, `codex-desktop` | `thread/compact/start` | `thread/start` (채널 대상이 새 스레드로 이동) | `thread/goal/clear` |

`codex-app-server`(bare)에서도 ws:// 리슨이면 채널 클라이언트가 붙어, 다른
클라이언트가 만든 스레드를 따라가며 메시지/compact/new를 JSON-RPC로 처리한다.
