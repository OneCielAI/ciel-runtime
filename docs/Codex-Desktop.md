# Codex 데스크톱 앱 런타임 (`codex-desktop`)

Windows용 Codex 데스크톱 앱(Microsoft Store 패키지 `OpenAI.Codex`, 실행 파일
`app\ChatGPT.exe`)을 Ciel Runtime 라우터 뒤에서 실행한다.

```powershell
ciel-runtime codex-desktop
ciel-runtime --ca-runtime codex-desktop
```

런처 메뉴의 `Launch` 패널에는 `Codex desktop app` 항목으로 나온다. Codex/Codex app-server와
같은 provider 조건을 쓴다(Codex 계열 런처가 허용하는 provider에서만 켜진다).

## 동작 방식

데스크톱 앱은 `-c` 설정 인자를 받지 않지만 실행 환경변수는 따른다. Ciel은 이를 이용해
앱을 **Ciel이 띄운 app-server**에 붙인다.

1. 라우터를 시작하고, Codex App Server 런처와 똑같은 라우팅 `-c` 인자로
   `codex app-server --listen ws://127.0.0.1:<라우터 포트+20>`을 실행한다.
2. app-server의 `/readyz`가 200을 줄 때까지 기다린다. 앱은 시작 시점에 app-server가
   없으면 "failed to start"를 띄우고 메인 창을 숨긴 채 남기 때문이다.
3. 앱을 다음 환경으로 실행한다.
   - `CODEX_APP_SERVER_WS_URL` — 자체 app-server를 띄우지 않고 1번 서버에 접속
   - `CODEX_HOME` — 격리된 Codex 홈
   - `CODEX_ELECTRON_USER_DATA_PATH` + `--user-data-dir` — 격리된 앱 프로필.
     스위치가 없으면 이미 열려 있는 사용자 앱 인스턴스로 실행이 넘어가 버린다.
4. 같은 app-server에 두 번째 클라이언트로 접속해 채널 메시지를 턴으로 넣는다(아래).
5. 앱 창을 닫으면 채널 클라이언트, app-server, 라우터를 정리한다.

사용자가 평소 쓰는 데스크톱 앱과 `~/.codex`는 건드리지 않으며, 두 앱을 동시에 띄울 수 있다.

## 격리 홈

`<config dir>/codex-desktop/<workspace id>/` 아래에 워크스페이스별로 만든다.

| 경로 | 내용 |
|---|---|
| `codex-home/` | 앱과 app-server의 `CODEX_HOME`. 대화 기록(`sessions/`)도 여기에 쌓인다 |
| `electron-user-data/` | 앱 프로필 |
| `logs/app-server.log`, `logs/desktop-app.log` | 두 프로세스의 출력 |

- `config.toml`은 홈이 처음 만들어질 때 `~/.codex/config.toml`에서 한 번만 복사한다.
  이후 앱에서 바꾼 설정은 유지된다. 라우팅 설정은 이 파일이 아니라 app-server 명령줄의
  `-c` 인자로 들어가므로 복사본이 라우팅을 덮어쓰지 않는다.
- `auth.json`은 원본이 더 새로우면 매번 다시 복사한다(ChatGPT 로그인 갱신 반영).
- 새 프로필로 처음 실행하면 앱 자체의 첫 실행 안내 화면이 나온다. 워크스페이스마다 한 번이다.

## 채널 메시지 주입

Web Chat, 외부 이벤트, `/ca/chat/notify` 등으로 들어온 채널 메시지는 app-server
JSON-RPC로 넣는다.

- 스레드가 유휴 상태면 `turn/start`, 턴이 진행 중이면 `turn/steer`로 그 턴에 합친다.
- 대상 스레드는 시작 시 워크스페이스 경로로 만든 스레드다. 앱에서 다른 대화의 턴이 시작되면
  그 대화로 옮긴다.
- 전달 확인은 transcript를 해석하지 않고 app-server의 `turn/completed` 알림으로 한다.
  `runtime-input-status.jsonl`에 `queued → submitted → replied`(실패 시 `failed`)가 남는다.
- `input_transport=router`로 들어온 메시지는 라우터가 다음 모델 요청에 붙이므로 이 경로는
  건너뛰지 않고 그 자리에서 기다린다.
- 제출이 3번 연속 실패한 메시지는 `failed`로 기록하고 다음 메시지로 넘어간다.

app-server는 `Origin` 헤더가 있는 WebSocket 핸드셰이크를 403으로 거부하므로, 채널 클라이언트
(`codex_app_server_websocket.py`)는 Origin을 보내지 않는다.

## 환경변수

| 변수 | 의미 |
|---|---|
| `CIEL_RUNTIME_CODEX_DESKTOP_EXE` | `ChatGPT.exe` 경로를 직접 지정. 없으면 `Get-AppxPackage OpenAI.Codex`로 찾는다 |
| `CIEL_RUNTIME_CODEX_APP_SERVER_LISTEN` | app-server 주소(기본 `ws://127.0.0.1:<라우터 포트+20>`). 반드시 `ws://`여야 한다 |

## 제약

- Windows 전용이다. 앱을 찾지 못하면 설치 안내를 출력하고 종료 코드 2로 끝난다.
- 앱에서 승인이 필요한 도구 호출(예: 권한 상승 명령)은 앱 화면의 승인 창을 거친다.
  채널로 들어온 턴도 마찬가지다.
