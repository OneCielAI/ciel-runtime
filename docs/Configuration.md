# Configuration — 설정 시스템

> 소스: `ciel_runtime.py` — `load_config()`, `save_config()`, `apply_config_migrations()`, `CONFIG_DIR`

---

## 설정 디렉터리

| OS | 기본 경로 |
|----|----------|
| Linux/macOS | `~/.config/ciel-runtime/` |
| Windows | `%APPDATA%\ciel-runtime\` |

환경변수 `CIEL_RUNTIME_CONFIG_DIR`로 재정의 가능.

### 샌드박스 프로필 우선

실행 디렉터리(또는 `CIEL_RUNTIME_LAUNCH_CWD`)부터 사용자 프로필 바로 아래까지
올라가며, 자체 프로필을 가진 첫 디렉터리를 샌드박스 홈으로 쓴다.

- `<홈>\.codex\state_*.sqlite`가 있으면 `CODEX_HOME=<홈>\.codex`
- `<홈>\.config\ciel-runtime\config.json`이 있으면 `CIEL_RUNTIME_CONFIG_DIR=<홈>\.config\ciel-runtime`

이미 설정된 환경변수가 항상 우선하고, 결정된 값은 환경변수로 기록돼 라우터와
Codex 자식 프로세스가 같은 위치를 쓴다. 저장소 안의 프로젝트용 `.codex`(상태 DB 없음)는
프로필로 보지 않는다. `CIEL_RUNTIME_SANDBOX_PROFILE=0`이면 끈다.
예: AAP Windows 샌드박스 `C:\ProgramData\aap-win\sandboxes\u19\home\robert-ai`에서
실행하면 계정 프로필(`C:\Users\<계정>\.codex`) 대신 샌드박스의 `.codex`와 설정을 쓴다.

---

## 주요 설정 파일

| 파일 | 용도 |
|------|------|
| `config.json` | 모든 workspace가 공유하는 API key, OAuth token, authorization 등 인증 정보의 원본. 기존 설치에서는 새 workspace 설정의 최초 migration seed로도 사용 |
| `workspaces/<workspace-id>/config.json` | workspace별 provider/model 선택, 모델 옵션, Web/Event/Speech 설정 등 비인증 설정. 인증 값은 기록하지 않음 |
| `router.log` | 라우터 로그 |
| `log-level` | 로그 레벨 파일 |
| `router.pid` | 라우터 프로세스 PID |
| `router-external-token` | 외부 디버그 접근용 Bearer 토큰 (`0600`) |
| `router-activity.json` | 라우터 활동 상태 |
| `rate-limit-state.json` | 레이트 리밋 상태 |
| `requests.jsonl` | 요청 덤프 (디버그용, 최대 5MB) |
| `responses.jsonl` | 응답 덤프 (디버그용, 최대 5MB) |
| `usage-events.jsonl` | provider/model별 token usage event (10MB에서 1세대 회전) |
| `router-sse-trace.jsonl` | SSE 트레이스 (최대 2MB) |
| `model-list-cache.json` | 모델 목록 캐시 (TTL: 300초) |
| `model-registry.json` | 모델 레지스트리 |
| `ollama-model-catalog.json` | Ollama 모델 카탈로그 캐시 (TTL: 24시간) |
| `chat-messages.jsonl` | 채팅 메시지 (최대 20MB) |
| `workspaces/<workspace-id>/runtime-inputs.jsonl` | TUI/세션 소켓으로 전달할 private 입력 큐 |
| `workspaces/<workspace-id>/runtime-input-status.jsonl` | private 입력의 queued/submitted/replied/failed 상태 이력 |
| `channel-probe-cache.json` | 채널 프로브 캐시 |
| `launch-state.json` | 실행 상태 |
| `tts-reference-audio/*.bin` | 업로드한 TTS reference의 private binary sidecar (`0600` 시도) |

---

각 workspace 설정은 최초 접근 시 기존 전역 `config.json`을 seed로 생성된다. 이때
`launch-state.json`에 해당 폴더의 마지막 provider/model 기록이 있으면 그 선택을 우선
복원한다. 이후 provider/model 변경은 해당 workspace 파일에만 저장되며 다른 폴더에서
실행 중인 라우터에 영향을 주지 않는다. 반대로 API key, token, authorization, secret은
전역 `config.json`에만 저장되고 모든 workspace가 최신 값을 함께 사용한다.

---

## config.json 구조

```json
{
  "provider": "ollama",
  "model": "qwen3-coder:30b",
  "language": "ko",
  "providers": {
    "anthropic": {
      "api_key": "sk-ant-...",
      "model": "claude-sonnet-4-6"
    },
    "ollama": {
      "base_url": "http://localhost:11434",
      "model": "qwen3-coder:30b",
      "context_limit": 65536,
      "num_ctx_min": 32768,
      "num_ctx_max": 131072,
      "timeout_ms": 300000
    },
    "deepseek": {
      "api_key": "...",
      "model": "deepseek-coder-v2"
    }
  },
  "blocked_tools": ["WebSearch", "WebFetch"],
  "advisor_model": "...",
  "channels": ["server:my-mcp-server"],
  "log_level": "ERROR"
}
```

---

## 환경 변수

| 변수명 | 용도 |
|--------|------|
| `CIEL_RUNTIME_CONFIG_DIR` | 설정 디렉터리 재정의 |
| `CIEL_RUNTIME_ROUTER_PORT` | 라우터 포트 재정의 |
| `CIEL_RUNTIME_ROUTER_CLIENT_HOST` | 라우터 클라이언트 바인드 호스트 |
| `CIEL_RUNTIME_ROUTER_EXTERNAL_TOKEN` | 외부 디버그 접근용 Bearer 토큰 재정의 |
| `CIEL_RUNTIME_ROUTER_MODEL_REQUEST_MAX_BYTES` | 모델 요청의 wire 안전 상한 (기본·최대 512 MiB) |
| `CIEL_RUNTIME_CHAT_FILE_MAX_BYTES` | 채팅 첨부파일의 디코딩 후 상한 (기본·최대 500 MiB) |
| `CIEL_RUNTIME_SPEECH_AUDIO_MAX_BYTES` | ASR/음성 입력의 디코딩 후 상한 (기본·최대 500 MiB) |
| `CIEL_RUNTIME_TTS_REFERENCE_AUDIO_MAX_BYTES` | TTS 레퍼런스 음성의 디코딩 후 상한 (기본·최대 500 MiB) |
| `CIEL_RUNTIME_ROUTER_INFLIGHT_REQUEST_BYTES` | 동시에 처리 중인 요청 본문의 총 byte 예산 (기본·최대 4 GiB) |
| `CIEL_RUNTIME_UPSTREAM_USER_AGENT` | 업스트림 User-Agent (기본: `claude-cli`) |
| `CIEL_RUNTIME_EVENT_LOG` | 이벤트 로그 활성화 (기본: `true`) |
| `CIEL_RUNTIME_EVENT_LEVEL` | 이벤트 로그 레벨 (기본: `info`) |
| `CIEL_RUNTIME_EVENT_BUFFER` | 이벤트 버퍼 크기 (기본: `1000`) |
| `CIEL_RUNTIME_USAGE_LOG` | token usage JSONL 기록 활성화 (기본: `true`) |
| `CIEL_RUNTIME_THINKING_PASSBACK_MAX` | Thinking 패스백 최대 토큰 (기본: `4096`) |
| `CIEL_RUNTIME_PYTHON` | 사용할 Python 실행 파일 경로 |
| `CIEL_RUNTIME_SKIP_POSTINSTALL_STOP` | npm 설치 후 stop 건너뜀 |
| `CIEL_RUNTIME_RUNAWAY_GUARD` | 반복 폭주 가드 (기본: 켜짐, `off`로 비활성화) |
| `CIEL_RUNTIME_RUNAWAY_CONTINUE` | 루프 감지 후 턴 이어가기 (기본: 켜짐, `off`면 감지만 하고 종료) |
| `CIEL_RUNTIME_RUNAWAY_RETRIES` | 수집 경로 재시도 횟수 (기본: `2`, 최대 `4`) |
| `CIEL_RUNTIME_COLLECT_STREAM` | 수집 경로 스트림 읽기 (기본: 켜짐, `off`면 단일 POST로 회귀) |
| `CIEL_RUNTIME_RUNAWAY_MIN_REPEATS` | 연속 반복 최소 횟수 (기본: `10`) |
| `CIEL_RUNTIME_RUNAWAY_MIN_CHARS` | 반복 구간 최소 길이 (기본: `2000`) |
| `CIEL_RUNTIME_RUNAWAY_MAX_PERIOD` | 반복 블록 최대 길이 (기본: `4096`) |
| `CIEL_RUNTIME_RUNAWAY_MIN_DENSITY` | 비연속 반복 최소 밀도 % (기본: `70`) |

### 요청/파일 제한

시작 환경설정 메뉴의 **Request/file limits**에서 작업 폴더별로 다음 값을 설정한다.

- 모델 요청 wire 크기
- 채팅 첨부파일의 디코딩 후 크기
- ASR/음성 입력의 디코딩 후 크기
- TTS 레퍼런스 음성의 디코딩 후 크기
- 동시에 처리할 요청 본문의 총 메모리 예산

값은 `config.json`의 `request_limits` 아래에 안정적인 workspace ID와 정규화된
작업 폴더 경로를 함께 저장한다. 따라서 한 컴퓨터에서 여러 Ciel Runtime을 실행해도
폴더 간 설정이 섞이지 않는다. 우선순위는 **환경변수 > workspace 설정 > 기본값**이다.

### 워크스페이스 MCP

시작 환경설정 메뉴의 **Workspace MCP modules**에서 등록한 stdio 및
Streamable HTTP MCP는 현재 작업 폴더의 `config.json`에만 저장된다. Claude
실행 시에는 launch 전용 JSON으로, Codex와 Codex App Server 실행 시에는
동등한 `mcp_servers.*` 시작 설정으로 투영된다. 사용자 전역 설정과 저장소의
`.mcp.json` 또는 `.codex/config.toml`은 수정하지 않는다.

각 실행은 workspace state의 `mcp-launches/<launch-id>`에 원자적인 lease를
남긴다. 정상 종료 시 즉시 삭제하며, 강제 종료로 남은 lease와 생성 파일은
다음 시작에서 owner 및 기록된 child 명령의 신원을 검증한 뒤 회수한다.
인증값은 workspace에 직접 저장하지 않고 `env_vars`, `env_http_headers`,
`bearer_token_env_var`로 전역 OS 환경변수 이름만 기록한다.

모든 설정 가능 항목의 기본값은 각각의 설정 가능한 최대값과 같고, 메뉴에서 필요한 경우
1 byte까지 명시적으로 낮출 수 있다. Ciel이 provider별
의미적 한도나 권장 파일 크기를 추정해 더 낮은 제한을 선택하지 않도록 하기 위한 것이다.
기존 workspace에 명시적으로 저장된 더 작은 값은 그대로 유지된다. 이 값들은 오직
전송·저장·메모리 안전 차단선이며 provider의 context, 파일 또는 음성 의미 제한이 아니다.

모델 요청 상한은 모델 context 제한이 아니라 라우터의 메모리 안전 차단선이다.
`/v1/responses`, `/v1/messages`, `/v1/chat/completions`, 모든
`/backend-api/codex/*` POST 및 `/v1/audio/voices` POST는
512 MiB까지 원문을 보존해 업스트림이 실제 context 오류를 결정한다. Base64를
사용하는 첨부파일과 음성은 디코딩 후 제한으로 관리하며, wire 제한은 Base64 팽창량과
1 MiB의 제한된 JSON envelope 여유를 더해 계산한다. 각 요청은 수신 원문과 JSON/Base64
파싱·디코딩 중 생길 수 있는 복사본을 고려해 `Content-Length`의 5배를 in-flight 예산에서
예약한다. 설정한 baseline 예산이 가장 큰 단일 wire 요청의 5배보다 작으면 그 요청 하나를
받을 수 있을 만큼 effective 예산을 자동으로 올린다. 시작 메뉴에는 configured 값과 자동
계산된 effective 값이 함께 표시된다. 최대 요청 하나는 허용하지만, 같은 크기의 두 번째
동시 요청은 남은 예산이 부족하면 503으로 거절하며 첫 요청 종료 후 예약을 반환한다.

TTS batch 요청은 item 수나 provider 의미를 가정하지 않고 `4 GiB / 5`인 약 819.2 MiB를
aggregate wire 기술 상한으로 쓴다. 500 MiB reference 하나의 Base64 JSON wire 상한보다
크며, 동시에 5배 예약이 4 GiB 기술 예산 안에 들어온다. 이 경로도 in-flight 하한 계산에
포함된다. `/ca/mcp`는 `send_file`의 실제 inline `content` 호출만 채팅 파일 wire 상한을
사용하며, `path` 기반 전송 및 다른 MCP 제어 호출은 4 MiB 제어 상한을 유지한다.

웹훅 제한은 인증 전 입력을 제한하는
보안 경계로서 workspace 설정과 무관하게 항상 1 MiB이고, 일반 제어 API는 기술적
제어 namespace 경계로 4 MiB다. 이 둘은 provider/file 설정값이 아니다.
어느 경우에도 이 전송 제한 때문에 라우터가 대화 기록을 임의로 자르거나 요약하지 않는다.

업로드한 TTS reference data URL은 `config.json`에 Base64로 보관하지 않는다. Ciel은
디코딩한 원본을 `CONFIG_DIR/tts-reference-audio/` 아래에 원자적으로 쓰고, 가능한
OS에서는 디렉터리 `0700`·파일 `0600` 권한을 적용한다. `config.json`에는 경로가 아닌
추측 불가능한 opaque marker와 정확한 `ref_text`만 남는다. marker는 public speech 설정
API에 노출되지 않으며, TTS 업스트림으로 전달하는 순간에만 다시 data URL로 확장된다.
확장된 최종 JSON 크기는 endpoint wire 상한으로 다시 검증되고, 최초 요청 예약과 겹치지
않는 증가분만 공용 in-flight 예산에 추가 예약한다. 기존 embedded data URL은 계속
동작하고 다음 speech 설정 저장 때 sidecar로 전환되며, HTTP(S) reference URL은 그대로
유지된다. reference를 지우거나 교체하면 이전 sidecar도 제거한다. Windows에서 동시에
전송 중인 파일을 즉시 지울 수 없는 경우 저장 성공을 되돌리지 않고 정리 경고를 남긴다.

---

## Runtime compact 설정

Claude Code와 Codex의 native auto-compaction 설정은 런타임을 시작할 때 현재
provider와 현재 model을 기준으로 **한 번만** 계산한다. 계산 결과는 해당 child process의
환경변수 또는 시작 인자에만 적용하며 `config.json`의 다른 model 설정으로 복사하지 않는다.
따라서 Kimi K3를 시작한 뒤 K2.7이나 다른 provider/model을 시작하면 K3 값이 남지 않고,
새 launch가 그 model의 명시적 설정 또는 기본값을 다시 사용한다. 우선순위는 다음과 같다.

1. provider 설정의 명시적 `auto_compact_window` 또는 `codex_auto_compact_window`
2. 현재 provider adapter가 현재 model에 선언한 launch-only 정책
3. 해당 CLI/runtime의 model 기본값

Kimi K3의 launch-only 정책은 Kimi CLI의 85% trigger 기준을 사용한다. Codex에는
`model_auto_compact_token_limit=891289`를 정확히 전달한다. Claude Code는 실제 context를
`CLAUDE_CODE_MAX_CONTEXT_TOKENS=1048576`으로 유지하고, 공식 auto-compact window 최대값
1,000,000과 `CLAUDE_AUTOCOMPACT_PCT_OVERRIDE=89`를 사용해 약 890,000 tokens에서
compact하도록 설정한다. 실제 model context와 compact trigger를 같은 값으로 취급하지 않는다.

Claude child process를 만들기 전에는 Ciel이 관리하는 Claude 환경변수를 복사된 child
환경에서 먼저 제거한 뒤 이번 launch의 snapshot만 넣는다. 부모 shell 환경은 변경하지 않는다.
Codex model catalog도 공용 mutable 파일을 덮어쓰지 않고 내용 해시별 immutable 파일을
생성한다. 같은 컴퓨터에서 서로 다른 provider/model의 Codex를 동시에 시작해도 이미 실행 중인
process가 참조하는 catalog가 다른 launch에 의해 바뀌지 않는다.
Codex App Server도 Codex TUI와 동일한 launch snapshot/catalog를 받는다.

Claude 또는 Codex 시작 명령에 `--model`/`-m`(또는 Codex `-c model=...`)을
명시하면 그 값이 저장된 메뉴 model보다 우선한다. Ciel이 해당 임시 model의 정확한 profile을
해석할 수 없는 경우에는 저장된 model의 context/compact 값을 재사용하지 않고 관련 override를
생략해 CLI 자체 catalog와 기본값에 맡긴다. 이는 K3용 임계값이 명시적으로 선택한 다른 model에
누출되는 것을 막기 위한 보수적 동작이다.

Ciel의 `prompt_compaction` soft guard는 이 native auto-compaction과 별개다. provider에 보낼
단일 요청 복사본이 실제 입력 예산을 넘을 때만 임시로 축약하며 Claude/Codex transcript를
영구 변경하지 않는다. 정상적인 지속 compact는 CLI 자체가 담당하고, soft guard는 native
compact가 먼저 일어나지 못한 예외 상황의 마지막 방어선으로만 남긴다. 일반 `413`, `429`,
high-demand 또는 network 오류를 context overflow로 재분류하지 않는다.

Codex가 local checkpoint compact를 일반 `/v1/responses` 요청으로 보내면 먼저 원본
checkpoint를 선택한 upstream provider/model에 그대로 전달한다. Ciel은 JSON/4 추정치나
UTF-8 byte 수를 provider token 수로 취급하지 않으며, native 요청이 예산 안이라고 관측되면
보조 summary를 만들지 않는다. provider가 출력 없이 명시적인 context-length 거절을 반환한
경우에만 기존 bounded map/reduce recovery를 한 번 시작할 수 있고, 일반 `400`, `413`,
network 오류, partial output은 재요약 근거가 아니다. 요약 요청은 내부 Ciel alias가 아닌
해석된 upstream model ID를 사용하며, client disconnect/turn cancellation 때 중단한다.
정상 turn과 native Responses의 opaque compaction item에는 이 경로를 적용하지 않는다.
provider 설정에 `context_compact_llm=false`를 명시하면 segmented compact를 끌 수 있다.

라우팅 provider가 공급자의 실제 context-length 거절을 그대로 받을 수 있으면
`preflight_prompt_compaction=false`를 명시한다. 그러면 JSON 크기 추정치만으로 일반
턴을 미리 압축하지 않는다. 실제 backlog가 남아 있는 요청은 공급자 거절을 받은 뒤
기존 reactive context recovery가 한 번 압축·재시도한다. 이 값은 옵션 기본값이며
네이티브 compact 요청과 번역된 Codex checkpoint에는 적용하지 않는다.

Alibaba Model Studio Token Plan(`alitoken`)의 native Responses endpoint는 실제 서비스가
10 MiB wire body를 거절하므로, Ciel은 이 provider에만 9 MiB 선제 경계를 적용한다. 본문이
경계를 넘으면 기존 Responses soft guard로 오래된 history를 deterministic chunk summary로
바꾸되 최신 safe tail과 opaque Responses 필드는 보존한다. 최대 4회의 bounded compaction 뒤에도
10 MiB를 넘는 단일 비축약 항목은 upstream으로 보내거나 재시도하지 않고 로컬 413으로 종료한다.
`alitoken-individual`과 다른 provider에는 이 wire 정책을 적용하지 않는다.

Alibaba의 Responses session cache는 요청 header가 없으면 기본적으로 비활성화된다.
`alims-intl`과 `alitoken`의 `openai_responses` 요청에는
`x-dashscope-session-cache: enable`을 기본 전송한다. 이 값은 provider의
`protocol_headers.openai_responses` 설정으로 관리되며 Chat/Anthropic wire에는 복사하지
않는다. 이 범용 protocol header 설정은 `authorization`, API key, cookie, host,
content-length 및 hop-by-hop header를 덮어쓸 수 없다.

---

## 원격 시스템 지침

시작 메뉴의 **Remote instructions**에서 런타임별 HTTP(S) GET URL을 설정할 수 있다.
기능을 켜면 런타임 시작 직전과 context compact 직전에 조건부 GET을 실행하며,
응답을 현재 작업 폴더의 표준 지침 파일에 원자적으로 반영한다.

| 런타임 | 작업 폴더 파일 |
|--------|----------------|
| Claude | `CLAUDE.md` |
| Codex / Codex App Server | `AGENTS.md` |
| AGY | `GEMINI.md` |
| Kimi | `AGENTS.md` |

인증이 필요한 엔드포인트는 같은 메뉴의 **Authorization header**에 전체 헤더 값을
설정한다. 키를 `config.json`에 직접 저장하지 않으려면
`Bearer %SYSTEM_PROMPT_AUTH%`, `Bearer ${SYSTEM_PROMPT_AUTH}` 또는
`Bearer {SYSTEM_PROMPT_AUTH}`처럼 환경변수를 참조한다. 세 표기법은 Windows,
macOS, Linux에서 동일하게 해석된다. 환경변수가 없으면 다운로드는 실패하며 기존
작업 폴더 파일은 그대로 유지된다. URL, ETag, Last-Modified와 해시만 상태 파일에
기록하고 해석된 Authorization 값은 로그나 상태 파일에 기록하지 않는다.

---

## 원격 메모리 트리

Remote Memory는 Remote Instructions와 분리된 manifest endpoint를 사용한다.
다음 명령은 시작할 때마다 `.ciel/memory`를 새 manifest 내용으로 원자적으로 교체한다.

```bash
ciel-runtimectl remote-memory enabled=true \
  manifest_url=https://memory.example/v1/manifest.json \
  authorization='Bearer {CIEL_MEMORY_TOKEN}' sync
```

manifest는 인덱스 상대경로, 폴더 구조를 나타내는 파일 상대경로, 각 파일의 다운로드
URL과 포맷을 제공한다. 지원 포맷은 OKF, Markdown, JSON, YAML, TOML, 일반 텍스트다.
동기화 후 실제 메모리 내용 전체를 프롬프트에 넣지 않는다. 대신 내부적으로 검증된 메모리
루트와 인덱스의 워크스페이스 기준 상대경로, 그리고 인덱스부터 읽고 관련 메모리 파일을 참조하라는 지시를 최종
wire 시스템/개발자 문맥 하단의 관리 블록에 추가한다. 같은 블록을 원격으로 내려받은
`CLAUDE.md`, `AGENTS.md`, `GEMINI.md`의 마지막에도 기록하여 라우터를 우회하는
네이티브 CLI 실행에서도 동일하게 참조할 수 있게 한다. Remote Instructions의 성공
상태와 해당 다운로드 파일이 확인된 경우에만 네이티브 파일을 갱신하며, Remote Memory
단독 설정으로 지시 파일을 새로 만들거나 확인되지 않은 사용자 파일을 변경하지 않는다.

계약, 제한, 예시는 [Remote Memory](Remote-Memory.md)를 참고한다.

## 세션 전 프로비저닝

Remote Provision은 런타임을 실행하기 전에 manifest의 파일을 워크스페이스에 두고
설치 스크립트를 실행한다. sha256이 맞지 않거나 스크립트가 실패하면 실행이 막힌다.

```bash
ciel-runtimectl remote-provision enabled=true   manifest_url=https://provision.example/v1/manifest.json   authorization='Bearer {CIEL_PROVISION_TOKEN}'
ciel-runtimectl remote-provision run
```

계약, 제한, 예시는 [Remote Provision](Remote-Provision.md)를 참고한다.

---

## Work 모드 (실험적)

> 소스: `ciel_runtime_support/work_mode_launch.py`, `work_mcp_tools.py`, `work_plan_store.py`,
> `claude_mods/ciel-work/` — 실험 기능이며 동작·키는 바뀔 수 있다.

Work 모드는 Plan 모드처럼 먼저 계획을 세우되, 목표 → 단계 → 작업 → 검증 가능한 스텝의
계층 트리로 계획을 유지하며 실행하게 한다. 계획은 라우터 MCP 서버(`ciel-runtime-router`)의
`work_*` 도구(`work_status`, `work_plan_set`, `work_node_add`, `work_node_update`,
`work_node_remove`, `work_select`, `work_execute`, `work_replan`, `work_reorder`, `work_urgent`) 뒤에 저장된다.

| config.json 키 | 기본값 | 메뉴 (LLM options) | 의미 |
|---|---|---|---|
| `work_mode` | `true` (켜짐) | `Work mode [on (default)]` | `false`로 명시해야만 꺼진다 |
| `work_plan_approval_ask` | `false` (자동 승인) | `Work plan approval [auto (default)]` | `true`(`ask user`)면 실행 전 사용자 승인을 요구 |
| `work_auto_continue` | `true` (켜짐) | `Work auto continue [on (default)]` | Claude 정상 턴 종료 시 실행 가능한 미결 Work를 자동 계속. Codex는 지침만 전달 |

- 메뉴 입력값: `Work mode`는 `on`/`off`, `Work plan approval`은 `ask`/`auto`.
  `default`/`unset`을 입력하면 키를 지워 기본값으로 돌아간다.
- 꺼져 있으면 `work_status`(`format: json`)는 `enabled: false` 스냅샷을, 그 외 `work_*` 호출은
  `Ciel Work mode is off` 오류를 돌려준다.

### 적용 범위

- 적용 조건: Work 모드가 켜져 있고, **네이티브 실행이 아니며**, 해당 실행에 라우터 MCP 서버가
  붙을 때만 적용된다 (`work_mode_applies`).
- **네이티브 실행(Claude Native, 네이티브 Codex)은 제외**된다. 아무 인자도 주입하지 않으므로
  설정과 무관하게 Work 모드가 적용되지 않는다.
- Claude: 번들 모드(mod) `ciel-work`를 `--plugin-dir <패키지>/ciel_runtime_support/claude_mods/ciel-work`
  로 붙인다. `--plugin-dir`는 반복 가능한 플래그라 사용자 플래그는 유지된다.
  `.claude-plugin/plugin.json`이 없으면 인자를 넣지 않는다. 세션 재시작 시 현재 설정으로
  인자를 다시 계산한다.
- Codex (TUI·app-server): `-c developer_instructions=...`로 Work 계약 지침을 넣고, Work 도구에만
  `mcp_servers.ciel-runtime-router.tools.<도구>.approval_mode="approve"`를 넣는다
  (`approval_policy=never`에서 MCP 호출이 막히지 않도록). 사용자가 같은 `-c` 키를 직접 지정하면
  사용자 값이 우선한다. 다른 라우터 도구의 승인 기본값은 바뀌지 않는다.

### Claude `/work-mode` 와 기존 외부 `/work` 는 별개

| | `/work-mode` (이 기능) | `/work` (기존 외부 명령) |
|---|---|---|
| 제공 위치 | 런타임 번들 모드 `ciel-work`가 세션 시작 시 등록 | 사용자 전역 명령 `~/.claude/commands/work.md` → ciel-code `work` CLI |
| 인자 | 없음(요약) \| `help` \| `view` \| `tree` \| `status`(JSON) | ciel-code 작업 보드 명령 |
| 저장소 | 라우터의 대화별 Work 계획 | ciel-code `CONFIG_DIR()/boards/sessions/<session-id>/active.json` (별도 CLI 보드) |

두 명령은 데이터를 공유하지 않는다. 외부 `/work`의 세션 공유 버그는 별도 수정으로 로컬 배포했다.
이 기능의 계획과 외부 CLI 보드는 서로 이관하거나 합치지 않는다.

### 사용 힌트

사용자에게 보이는 안내(모델에는 보내지 않는 것)와 모델 지침을 구분한다.

| 위치 | 언제 | 내용 |
|---|---|---|
| 대화 기록 알림 (`$.ui.log`, 모델 미전달) | Claude 대화형 세션 시작, Work 켜짐 | 계획 먼저(`work_plan_set`) → `work_execute` 후 편집, `/work-mode help`, 끄는 위치 |
| 프롬프트 위 줄 | 계획 없음 | `Work mode: no plan yet … /work-mode help` |
| 프롬프트 위 목록 머리 | 계획 있음 | `Work planning` 또는 `Work running` + 진행률, 큐 목표(`Queued: …`), 키 안내 끝에 `/work-mode help` |
| 토스트 (`$.ui.toast`) | 계획 단계에서 쓰기·셸이 거부될 때 | 대화·계획 버전마다 한 번 |
| 거부 사유 (모델과 사용자 모두 봄) | 계획 단계 쓰기·셸 거부 | `work_plan_set` 후 `work_execute`, `/work-mode help` |
| 콘솔 한 줄 | Codex TUI·app-server 실행 전, Work 적용 시 | Work 켜짐, 끄는 위치. Codex는 모드 UI가 없어 실행 전 콘솔에만 찍는다 |

`/work-mode` 명령(Claude):

- 인자 없음: 단계·진행률·승인 방식, 활성 목표의 항목 목록, 다음 스텝, 큐를 읽기 쉬운 요약으로 보여 준다.
- `help`: 계획/실행 흐름, 새 요청 처리(독립 요청은 번호 붙은 새 아이템으로 큐, 변경 요청은 해당 아이템 아래),
  프롬프트 위 목록 키(Ctrl+X Tab 포커스, Tab/↑↓ 선택, Enter 상세, Esc 복귀), 하위 명령, 설정 위치.
- `view`: 키보드 상세 보기 창, `tree`: 계획 트리, `status`: 원본 스냅샷(JSON).

Codex 지침은 사용자가 Work 모드 사용법을 물으면 간단히 설명하고 끄는 위치를 알려 주라고 덧붙인다.

### 새 요청 분류와 작업 큐

모델 지침(Claude 모드 프롬프트 문맥, Codex `developer_instructions`, `work_plan_set`·`work_node_add`
도구 설명)은 새 사용자 요청마다 먼저 분류하라고 안내한다.

- **독립 요청** → 별도 목표(루트 레벨 아이템). 가능하면 제목을 아이템 번호로 시작(`"1. ..."`, `"2. ..."`).
- **기존 아이템을 바꾸거나 늘리는 요청** → 그 아이템 아래 자식으로 추가(`work_node_add`의 `parent_id`).

번호는 안내일 뿐 라우터가 붙이거나 강제하지 않는다. 큐 동작은 라우터가 보장한다
(`work_plan_tree.py`):

| 상황 | 동작 |
|---|---|
| 끝나지 않은 활성 목표가 있을 때 새 목표 추가 (`work_plan_set` 또는 `parent_id` 없는 `work_node_add`) | 새 목표는 큐에 들어가고, 활성 목표와 단계(`executing` 등)는 그대로 |
| 활성 목표가 없거나 이미 끝났을 때 새 목표 추가 | 새 목표가 바로 활성, `planning` |
| 큐에 있는 목표나 끝난 목표를 `root_id`로 다시 계획 | 활성 목표는 그대로, 그 목표는 큐에 남음 |
| 큐에 있는 목표 아래 자식 추가 | 활성 목표·단계 변화 없음 |
| 끝난 목표 아래 자식 추가 | 그 목표가 다시 열려 생성 순서 위치로 큐에 들어감 |
| 활성 목표의 마지막 스텝 완료(또는 남은 스텝 제거) | 가장 오래된 큐 목표가 활성이 되고 `planning`. 실행하려면 `work_execute` |
| `work_select` | 사용자가 고른 목표를 지금 활성으로 바꾸고 `planning` |

- 스냅샷(`work_status` JSON)에 `queue`(활성 목표 다음 순서, 최대 20개, 진행률 포함)가 추가됐다.
  텍스트 트리는 `Queued goals (next first)`와 `Finished goals` 줄로 나눠 보여 준다.
- Claude 프롬프트 위 목록은 활성 목표의 항목 아래에 `Queued: …` 줄을 최대 3개(나머지는 `+N more queued`)
  보여 준다. 이 줄은 포커스 대상이 아니다. `/work-mode` 요약에는 `Queue (next first):` 목록이 붙는다.
- 이 변경 전에는 새 목표를 추가하면 실행 중인 목표가 활성 자리를 잃고 `planning`으로 돌아가
  Claude 편집이 막혔다(2026-10-26 실측 재현, 작업 저널 참고).

끄기·승인 방식: `ciel-runtime` 메뉴 → `LLM options` → `Work mode`(on/off), `Work plan approval`(auto/ask).
라우터는 Work 도구 호출마다 설정을 다시 읽는다(`work_mcp_tools.dispatch_work_tool`). Work 모드가 꺼진
상태로 시작한 Claude 세션에는 모드가 붙지 않는다.

### 자동 계속·우선순위·긴급 요청

- Claude의 `classic.Stop` 모드 훅은 정확한 대화의 Work 스냅샷을 읽고 실행 가능한 미결 항목이
  있으면 다음 모델 턴을 요청한다. Work/auto continue 꺼짐, 서브에이전트, 진행 중 백그라운드 작업,
  연결 실패, 실행 승인 대기는 제외한다. 사용자 중단·API 오류는 이 정상 Stop 경로에 포함하지 않는다.
- 같은 계획 버전에서는 두 번까지 계속하고 그 뒤에는 진행 없음 토스트와 함께 멈춘다.
  계획 버전 변경 또는 새 사용자 입력으로 다시 허용한다. Claude Code 자체의 연속 훅 제한도 적용된다.
- `waiting_on`(최대 120자)은 외부 의존 대상이며 `◷ 대상`으로 끝에 표시한다. 조상 항목의
  대기·blocked 상태도 실행 가능 판정에서 제외한다. 활성 목표에 실행 가능한 것이 없으면 큐를 찾는다.
- `ref`(최대 64자)는 연결 ID이며 `[ id ] 설명`으로 표시한다. 두 필드는 add/plan/update로
  설정하며 update의 빈 문자열로 해제한다. 프롬프트 위에는 목표 → 가지 → 하위 항목을 표시하고,
  가지별 하위 8개 초과분은 `+N more`로 알린다. 완료 표시는 녹색 `✓`다.
- `work_reorder(root_id, position)`은 미완료 루트 순서(1부터, 1은 활성 자리)를 바꾼다.
  기본은 큐 내 이동만 허용하며 활성 자리가 바뀌면 명시적인 사용자 요청과 `user_requested=true`가 필요하다.
  `/work-mode view`의 루트 상세에 있는 Move up/down 버튼도 같은 도구를 호출하며 버튼 입력은 사용자 요청이다.
- 명시적 긴급 요청은 `work_urgent(goal, children, user_requested=true)`로 분해하여 최상위에 놓는다.
  다른 모든 목표는 `Held`로 표시하고 자동 계속 대상에서 제외한다. 긴급 목표가 끝나면 직전 활성 목표부터 복귀한다.
- Codex에는 같은 규칙을 지침으로 전달할 뿐 Ciel이 종료된 Codex 턴을 직접 재개하지 않는다.

### 계획 단계와 승인

- **Claude**: 모드가 강제한다. 계획(`planning`) 단계에서는 `Edit`/`Write`/`NotebookEdit`/`apply_patch`
  를 거부하고, `Bash`/`PowerShell`은 엔진이 읽기 전용으로 분류한 호출만 허용한다. Work 상태를
  확인할 수 없으면 쓰기·셸 호출을 거부한다. `work_execute` 호출 시 모델이 넣은 `user_confirmed`는
  제거되며, `ask` 승인이면 모드가 `$.ui.ask` 대화상자(`Approve` / `Keep planning`)로 사용자에게
  묻고 승인 후 대화·계획 버전이 그대로일 때만 `user_confirmed=true`로 실행한다.
  Claude Code 2.1.310 실제 CLI에서 `bypassPermissions` 상태에서도 계획 중 Edit 및
  파일을 쓰는 Bash 호출이 차단되는 것을 검증했다.
- **Codex**: **모델 매개(model-mediated)** 방식이다. 계획 단계와 승인 규칙은 지침으로만 전달되며,
  이 버전은 Codex 편집에 대해 계획 게이트나 승인 게이트를 강제하지 않는다. `ask` 승인이면
  라우터의 `work_execute`가 "User confirmation is required" 응답을 돌려주고, 모델이 사용자에게
  계획을 보여 주고 동의를 받은 뒤 `user_confirmed=true`로 다시 호출해야 한다.

### 세션(대화) 분리

계획은 대화마다 따로 저장된다:
`WORKSPACE_STATE_DIR/work-sessions/<sha256(런타임:대화 ID)>/work-plan.json`.
식별자는 `claude:<id>` 또는 `codex:<id>` 형식(`[A-Za-z0-9_-]{1,200}`)이어야 하며, 없거나 형식이
틀리면 공유 기본값으로 대체하지 않고 거부한다(fail closed).

- **Claude**: 모드가 매 호출마다 엔진의 `$.session.id()`를 읽어 `claude:<id>`로 넣고, 모델이 넣은
  `session_id`는 덮어쓴다. `/clear`·재개 후에도 다시 읽으며, `session.end`에서 표시 캐시를 비운다.
  같은 대화의 서브에이전트는 부모 대화의 계획을 쓴다.
- **Codex**: MCP 호출의 네이티브 메타데이터 `_meta.sessionId`를 `codex:<id>`로 사용하며 모델 인자보다
  우선한다. `_meta.threadId`는 쓰지 않으므로 자식 스레드는 루트 대화의 계획을 공유한다.
  네이티브 메타데이터 없이 `codex:` 접두사만 붙인 인자는 거부된다.
- 새 대화는 별도의 빈 계획을 갖고, 같은 대화를 재개하면 계획이 유지된다.
- 이전의 워크스페이스 공유 파일 `WORKSPACE_STATE_DIR/work-plan.json`은 읽거나 옮기거나 지우지 않는다.
- 이것은 네임스페이스 분리이며 인증이 아니다. HTTP 호출자는 메타데이터를 위조할 수 있다.

---

## 반복 폭주 가드

모델이 같은 문장을 끝없이 되풀이하는 생성 루프에 빠지면 라우터가 이를 끊는다.
업스트림 연결을 즉시 닫으므로 남은 루프는 생성되지도, 과금되지도 않는다.

### 판정

두 가지 정확 규칙으로만 판단한다. 의미 판단이나 유사도 점수는 쓰지 않는다.

1. **연속 반복** — 같은 블록이 바로 뒤에 붙어 `MIN_REPEATS`회 이상,
   `MIN_CHARS`자 이상 반복될 때.
2. **비연속 반복** — 같은 블록이 사이사이 다른 문구를 끼고 되풀이될 때.
   횟수는 2배, 그리고 해당 구간의 `MIN_DENSITY`% 이상이 그 블록 자체여야 한다.

2번은 임계값이지 증명이 아니다. 실제로 대부분이 한 블록의 반복인 정상 출력
(데이터가 거의 없는 표, 거의 동일한 로그 줄 묶음)은 같은 밀도에 근접할 수 있다.
그런 워크로드에서는 `CIEL_RUNTIME_RUNAWAY_MIN_DENSITY`를 올리거나
`CIEL_RUNTIME_RUNAWAY_GUARD=off`로 끄면 된다. 1번 규칙은 그런 판단이 필요 없다.

### 감지 후 동작

턴을 죽이지 않고 이어가는 것이 기본이다. 무엇을 할 수 있는지는 경로마다 다르다.

- **수집 경로** (Codex): 클라이언트로 나간 바이트도, 실행된 툴도 없으므로 응답을
  버리고 **다시 요청한다.** 사용자에게는 아무 메시지도 보이지 않는다. 재시도는
  같은 조건 → `effort=high` → `effort=low`(사고 끄기) 순으로 올라간다.
  DeepSeek이 문서에서 권하는 대응(*"Retry or lower reasoning effort"*)과 같고,
  샘플링이 확률적(`do_sample: true, temperature: 1.0`)이라 재시도는 실제로 다른 결과다.
  세 프로토콜(Ollama NDJSON, OpenAI chat SSE, Anthropic Messages SSE) 모두
  스트림으로 읽어 조립하므로 루프가 다 만들어지기 전에 끊긴다. 전송 방식만
  되돌리려면 `CIEL_RUNTIME_COLLECT_STREAM=off`.
- **스트리밍 경로** (Claude Code): 이미 나간 바이트는 되돌릴 수 없으므로 재시도가
  불가능하다. 대신 짧은 알림과 함께 `TaskList` 툴 호출을 합성해 CLI가 다음 턴을
  가져가게 한다. 직전 어시스턴트 턴에 같은 알림이 이미 있으면 합성하지 않고
  종료한다 — 복구 자체가 루프가 되는 것을 막는다.

알림 문구에는 측정값을 넣지 않는다. 그 텍스트는 어시스턴트 메시지에 남아 다음 턴에
모델이 다시 읽기 때문이다. 반복 블록 길이·횟수·원문은 라우터 로그
(`collect_runaway_repetition`, `ollama_stream_runaway_repetition` 등)에만 기록된다.

---

## 설정 마이그레이션

`apply_config_migrations(cfg)` — 구버전 설정을 현재 형식으로 자동 변환.

---

## 설정 캐시

`load_config()`는 내부 캐시를 사용한다.  
`invalidate_config_cache()` — 캐시 무효화.  
`clear_model_cache()` — 모델 캐시 초기화.

---

## 로그 레벨

| 레벨 | 값 |
|------|-----|
| SILENT | 0 |
| ERROR | 1 (기본) |
| WARN | 2 |
| INFO | 3 |
| DEBUG | 4 |
| TRACE | 5 |

설정 방법:
```bash
ciel-runtimectl log-level DEBUG
```

파일 `log-level`에 레벨 문자열을 저장하거나,  
`config.json`의 `"log_level"` 키로도 설정 가능.

## 외부 라우터 접근

라우터는 기본적으로 `127.0.0.1`에만 바인딩된다. 외부 디버그 접근을 활성화하면
`router-external-token` 파일에 제한된 권한으로 토큰을 생성한다. 외부 클라이언트는
모든 요청에 다음 헤더를 보내야 한다.

```http
Authorization: Bearer <token>
```

`CIEL_RUNTIME_ROUTER_EXTERNAL_TOKEN` 환경 변수로 파일 토큰을 재정의할 수 있다.
루프백 요청은 기존 로컬 CLI 호환성을 위해 토큰 없이 허용된다.

## CielAiRouter 게이트웨이 선택

CielAiRouter 공급자의 `base_url`은 중앙 라우터 주소다. 라우터는 요청마다 이 주소의
`GET /api/gateway/discovery`(라우터 키, Bearer, 최대 5초)로 사용 가능한 게이트웨이를
확인하고 다음 순서로 고른다.

1. `status=online`, `health=healthy`, `availableUntil` 이전인 노드만 후보
2. 각 엔드포인트의 `/healthz`를 인증 없이 2초 안에 직접 측정(성공한 것만). 응답에
   `nodeId`가 있으면 그 노드의 `id`와 같아야 하며, 다르면 실패로 본다.
3. 루프백(`127.0.0.0/8`, `::1`, `localhost`) 게이트웨이 → 낮은 `congestion.utilization` →
   낮은 `backlog` → 짧은 측정 RTT 순서
   (`observedAt`이 90초보다 오래되면 혼잡도 값은 쓰지 않는다)

여러 노드가 같은 루프백 주소(예: `http://127.0.0.1:20128`)를 광고해도 후보는 노드 `id`와
주소의 조합으로 구분한다. 그 주소의 `/healthz`가 돌려준 `nodeId`와 일치하는 노드, 즉 이
컴퓨터에서 실제로 실행 중인 게이트웨이만 선택된다.

- Claude Code·Codex 세션은 정상인 동안 선택한 게이트웨이를 유지한다. 새 세션·후보 만료·연결 실패 때는 다시 탐색한다. 루프백도 반드시 이 컴퓨터에서 probe에 성공해야 하며, 존재하지 않는 `127.0.0.1`은 제외한다.
- 연결 실패, 시간 초과, HTTP 5xx, 완료 이벤트(`message_stop`, `response.completed`) 없이
  끝난 스트림은 그 게이트웨이의 실패로 기록되고 60초 동안 후보에서 빠진다. 그 요청은
  다시 보내지 않는다(이미 모델이 처리했을 수 있다). 다음 요청부터 다른 게이트웨이를 쓴다.
- 401·403·429 같은 키·한도 응답은 게이트웨이 실패로 보지 않는다.
- 쓸 수 있는 게이트웨이가 없거나 discovery가 실패하면 설정된 `base_url`을 그대로 쓴다.
- 모델 목록(`/v1/models`)은 항상 설정된 `base_url`에서 읽는다.
- 평문 `http` 게이트웨이는 루프백 주소만 허용한다. 한 노드의 대표 주소가 거부되어도
  다른 주소는 각각 검증한다. 생성 요청도 301·302·303·307·308 리다이렉트를 따르지 않으므로
  다른 origin으로 인증값을 넘기지 않는다.
- 선택 결과는 설정 파일에 저장하지 않는다. 라우터 `/health`의 `cielairouter_gateway`와
  로그(`cielairouter_gateway_selected`, `cielairouter_gateway_failure`)에서 확인한다.

끄려면 공급자 설정에 다음을 넣는다.

```json
{ "providers": { "cielairouter": { "gateway_selection": "off" } } }
```

### 직접 게이트웨이 데이터 경로 (`gateway_data_path`, opt-in)

기본값 `router`에서는 Claude Code·Codex의 모델 요청이 로컬 Ciel Runtime 라우터를 거쳐
게이트웨이로 간다(위 선택 규칙이 요청마다 적용). `direct`로 바꾸면 CLI를 실행하기 직전에
같은 선택기(`CielAiRouterGatewayService`, 새 세션과 같은 fresh 순위)로 게이트웨이를 한 번
고르고, CLI가 그 게이트웨이와 모델 HTTP를 직접 주고받는다. CielAiRouter 공급자에서만
설정할 수 있으며 다른 공급자는 이 옵션을 거부한다.

```json
{ "providers": { "cielairouter": { "gateway_data_path": "direct" } } }
```

```text
ciel-runtimectl provider-options cielairouter gateway_data_path=direct   # 또는 router
ciel-runtimectl provider-options cielairouter gateway-status              # 설정·마지막 실행 경로 확인
```

메뉴: 프리런치 LLM 옵션 패널의 `Gateway data path` 행(Enter로 router/direct 전환),
외부 메뉴 Provider options의 `Gateway data path: router|direct` 항목.

`direct`가 실제로 적용되면:

- Claude Code: `ANTHROPIC_BASE_URL`=게이트웨이 루트, `ANTHROPIC_AUTH_TOKEN`=CielAiRouter
  라우터 키, `ANTHROPIC_MODEL`·기본 haiku/opus/sonnet·`--model`은 라우터가 보냈을
  upstream 모델 id(`ciel-runtime-cielairouter-*` 별칭은 보내지 않는다).
  `CLAUDE_CODE_SUBAGENT_MODEL=inherit`는 모델 id가 아닌 제어 값으로 유지한다.

- Codex(app-server, codex-remote): `model_providers.ciel-runtime-gateway.base_url`=게이트웨이
  `/v1`, `env_key`=`CIEL_RUNTIME_GATEWAY_API_KEY`(라우터 키는 이 환경 변수에만 들어간다),
  모델과 model catalog slug는 upstream 모델 id. direct는 별도 provider id를 사용한다.
- 로컬 라우터는 그대로 시작되고 router MCP(`/ca/mcp`), workspace MCP, 채널 프록시·세션 소켓,
  외부 이벤트(SSE) 수신, 세션 재시작은 router 모드와 같다. 모델 요청만 라우터를 거치지 않는다.

Claude Code에서 Anthropic 이외 공급자를 선택하면 subagent와 teammate는 부모의 **현재 모델을 강제 상속**한다
(`CLAUDE_CODE_SUBAGENT_MODEL=inherit`, `CLAUDE_CODE_SUBAGENT_MODEL_FORCE=1`, Claude Code 2.1.257 이상).
Agent 호출의 `model=haiku/sonnet`, 에이전트 정의의 model, 기존 provider `subagent_model` 값은
이 경로에서 무시한다. 설정 파일의 해당 값은 삭제하지 않는다. 부모 `/model` 변경 뒤 새로 만든
자식은 변경 모델을 따르며, 이미 실행 중인 자식은 바뀌지 않는다. 공급자·모델 메뉴 선택으로 자동 적용되고,
실행 중 세션에는 소급 적용되지 않는다. Anthropic native/routed의 기존 정책과 보조 분류기 가족 별칭은
변경하지 않는다.

[Claude Code 공식 모델 우선순위](https://code.claude.com/docs/en/agent-teams#specify-teammates-and-models).

다음 경우에는 그 실행을 router 경로로 유지하고 키 없는 로그 한 줄
(`launch_data_path ... active=router reason=...`)과 안내 한 줄을 남긴다: `gateway_selection`이
`off`, 키 없음, 모델 없음, discovery·probe 실패, 중앙 base 결과, degraded 결과, 루프백이 아닌
평문 `http` 게이트웨이.

채널 전달 방식이 먼저 판단된다(게이트웨이 discovery·probe 전, 공급자와 무관). 채널 메시지가
CLI에 `[ciel-wake] pending_ids=...` 표식으로만 타이핑되고 전체 메시지 전송 수단(Claude Code
messaging socket, app-server RPC)이 없는 실행은 router에 남는다. 그 본문은 router가 모델 HTTP
요청에 붙이므로 직접 요청에는 표식만 실린다. 해당 실행과 기록 사유:

- 일반 Codex TUI(LLM 채널 전달): 소켓이 없고, Windows에서는 입력 본문 문자 누락 때문에 표식만
  입력한다(2026-10-03 진단). 다른 플랫폼에서는 표식 뒤에 본문도 입력하지만 그 turn은 router가
  교체하는 방식이고 router 없이 동작하는지는 검증되지 않았으므로, 모든 플랫폼에서 router로
  폴백한다. 세션 재시작 후에도 router다.
- Claude Code에서 session-socket 입력이 꺼진 경우(`claude_code.session_socket_input: false`,
  채널 wake 프록시 사용 중).
- 기록 사유: `channel wakes are typed [ciel-wake] markers whose bodies only router model HTTP
  injects (no whole-message transport)`.

Claude Code(messaging socket 사용)와 Codex app-server·codex-remote(RPC 주입, TUI는 채널 입력을
타이핑하지 않음)는 direct를 쓸 수 있다.

제한(직접 경로에서는 라우터가 요청을 보지 못한다):

- 실행 중인 CLI는 시작할 때 고른 게이트웨이를 계속 쓴다. 실행 중 재선택(live re-route),
  요청 단위 실패 반영·cooldown, 키 로테이션, provider 폴백, 카탈로그 출력 상한·effort 하향
  보정은 적용되지 않는다. 게이트웨이가 죽으면 그 CLI의 요청은 실패한다.
- 세션 재시작(`restart_session`)은 게이트웨이를 다시 고른다. 정상 게이트웨이가 없으면 그
  재시작부터 라우터로 돌아간다(upstream 모델 id는 라우터도 받는다). 실행 시점에 router로
  폴백한 세션은 다음 실행 전까지 router에 머문다.
- 일반 Codex TUI는 direct 설정이어도 router로 폴백한다(위 채널 전달 판단).
- Claude의 router 전송 지정은 direct에서 전체 메시지 소켓으로, 소켓을 쓸 수 없는 동안에는
  본문 전체 TTY 입력으로, app-server는 RPC로 전달된다. Windows Claude Code에 긴 본문을 TTY로
  입력할 때 문자 누락이 있는지는 확인되지 않았다(Codex에서는 2026-10-03에 확인됨).
- 명시적으로 넘긴 `--model`은 그대로 게이트웨이로 간다.

`gateway-status`는 설정값과 이 워크스페이스에서 CLI별로 마지막 실행 때 기록된 경로
(`<workspace state>/launch-data-path.json`: direct/router, 게이트웨이 주소, 노드, 사유, 시각;
키 없음)를 보여 준다.

---

## Windows 콘솔 채널 wake

Windows 10 이상에서는 기본적으로 네이티브 ConPTY를 생성하고 Claude/Codex TUI와
UTF-8 바이트 스트림으로 통신한다. 긴 프롬프트를 문자별 `KEY_EVENT`로 합성하지
않으며, 사용자 키보드와 TUI 출력도 같은 pseudo-console 파이프를 통과한다. Ciel은
이 입력 스트림에 Windows 전용 마우스 해석기를 적용하지 않으므로 F9의
`ESC[20~`와 bracketed-paste의 `ESC[200~`/`ESC[201~`를 그대로 전달한다.

ConPTY를 시작하기 전과 닫을 때는 부모 console host에 남은 DEC mouse, focus,
bracketed-paste mode를 reset한다. reset은 부모 출력 handle에서
`ENABLE_PROCESSED_OUTPUT`과 `ENABLE_VIRTUAL_TERMINAL_PROCESSING`을 활성화한 것이
확인된 경우에만 `WriteConsoleW`로 기록하며, 같은 handle의 원래 mode를 복원한다.
VT 활성화가 실패한 classic/legacy console에는 escape 문자열을 raw text로 쓰지 않는다.

부모 콘솔에서 사용자가 입력한 미제출 draft가 있으면 외부 channel wake 주입을
연기한다. 따라서 wake 주입 앞의 `Ctrl+U`가 사용자가 먼저 입력한 글자를 지우지
않으며, 사용자가 Enter로 draft를 제출한 뒤 기존 polling 절차가 다시 진행된다.

ConPTY prompt-render detector는 캡처된 UTF-8 출력을 기준으로 ANSI CSI/OSC/SGR을
가시 텍스트로 투영한 뒤 원문 prefix를 비교한다. 따라서 한글 바이트와 ANSI 색상·
cursor 이동 시퀀스가 섞여도 화면에 표시된 원문을 감지한다. 이 투영은 감지에만
사용하며 실제 입력 원문에는 표식이나 다른 문자를 추가하지 않는다.

ConPTY 생성이 불가능하거나 `CIEL_RUNTIME_WINDOWS_CONPTY=0`으로 명시적으로 끈
경우에만 기존 Windows Console 입력 큐 호환 경로를 사용한다. 이 호환 경로에서는
`clear → body → submit`을 각각 큐가 소비한 뒤 진행하고, 여러 줄의 외부 메시지는
줄바꿈이 Enter 키로 해석되지 않도록 한 줄로 정규화한다. 완전한 wake prompt 전달
확인에 실패하면 `Ctrl+U`로 해당 draft 제거를 한 번 요청하고 ConPTY redraw로 제거를
확인한다. 제거 여부와 관계없이 같은 원문을 자동 재입력하지 않으며 요청 상태를
`failed`로 유지한다. 앞선 실패 요청은 다음 입력을 차단한다. ASCII sentinel을
추가하거나 router request-body 전송으로 자동 변경하지 않는다.

호환 경로는 실행 중 Win32 `ENABLE_MOUSE_INPUT` bit를 끄고 종료할 때 원래 입력
mode를 복원한다. `CIEL_RUNTIME_WINDOWS_CONSOLE_MOUSE_FILTER=0`은 이 Win32 guard를,
`CIEL_RUNTIME_TERMINAL_INPUT_MODE_RESET=0`은 안전한 VT mode reset을 각각 끈다.
정확한 `ESC[20~`는 Microsoft VT 입력 표의 F9이므로 mouse report로 제거하지 않는다.

ConPTY resize 실패는 요청 크기와 HRESULT/예외를 router 로그에 기록한다. 성공한
resize 뒤에는 child output 발생 여부를 확인해 `redraw=observed|timeout`을 남긴다.

---

## 언어 설정

지원 언어:

| 코드 | 언어 |
|------|------|
| `en` | English |
| `ko` | 한국어 |
| `ja` | 日本語 |
| `zh` | 中文 |

```bash
ciel-runtimectl language ko
```

### Z.AI Start Plan remote CAPTCHA callback

Z.AI Start Plan은 모델 요청 전에 공식 Aliyun CAPTCHA의 일회성 결과가 필요할 수
있습니다. 브라우저가 없는 원격 호스트에서는 `provider-options`로 운영자
브라우저가 접근할 callback origin을 설정할 수 있습니다.

모든 Z.AI 프로필은 `zcode_app_version`에 맞춘
`User-Agent: ZCode/<version>`을 전송합니다. Start Plan의 Anthropic 요청은 ZCode와
동일하게 같은 OAuth JWT를 `Authorization: Bearer`와 `x-api-key`에 함께 넣습니다.

```bash
ciel-runtimectl provider-options zai-start-plan \
  captcha_bind_host=100.95.132.58 \
  captcha_port=42119 \
  'captcha_public_base_url=http://100.95.132.58:{port}' \
  captcha_timeout_seconds=120
```

- `captcha_bind_host`: 수신 서버가 바인딩할 로컬 인터페이스입니다. 기본값은
  `127.0.0.1`입니다.
- `captcha_port`: 고정 포트이며 `0`이면 운영체제가 빈 포트를 선택합니다.
- `captcha_public_base_url`: 운영자 브라우저에 표시할 HTTP(S) origin입니다.
  `{port}` 자리표시자를 사용할 수 있습니다.
- `captcha_timeout_seconds`: 결과 대기 시간이며 15~600초로 제한됩니다.

환경 변수 `CIEL_RUNTIME_ZAI_CAPTCHA_BIND_HOST`,
`CIEL_RUNTIME_ZAI_CAPTCHA_PORT`,
`CIEL_RUNTIME_ZAI_CAPTCHA_PUBLIC_BASE_URL`,
`CIEL_RUNTIME_ZAI_CAPTCHA_TIMEOUT_SECONDS`도 같은 값을 제공합니다. 공개 callback은
state-bound 일회성 URL을 사용하지만, 신뢰하는 사설 인터페이스에만 바인딩해야
합니다.

---

## 관련 문서
- [[CLI-Reference]] — 설정 변경 CLI 커맨드
- [[Providers]] — 제공자별 설정 옵션
- [[Observability]] — 이벤트 로그 설정

### Direct gateway safety boundary (2026-10-16)

The launcher declares its channel delivery (`launch_data_path.ChannelDelivery`) and the resolver checks it before any gateway selection: a launch whose channel messages reach the CLI only as typed `[ciel-wake]` markers, with no whole-message transport (Claude Code messaging socket, app-server RPC), stays on the router because the router injects those bodies into model HTTP. Plain Codex TUI (no socket; on Windows only the marker is typed) and Claude Code with `claude_code.session_socket_input: false` fall back on every platform, at launch and after a session restart, with reason `channel wakes are typed [ciel-wake] markers whose bodies only router model HTTP injects (no whole-message transport)`. Claude Code with its messaging socket and Codex app-server/remote use direct model HTTP. The direct Codex provider id is `ciel-runtime-gateway`, distinct from the local-router provider; authentication uses `CIEL_RUNTIME_GATEWAY_API_KEY`. Launch model ids are resolved with the existing `resolve_requested_model` service.

In direct mode, Claude router-transport messages use a whole-message session socket (or full-body TTY when unavailable); app-server router-transport messages use RPC rather than waiting for model HTTP. Restart rebuilds Claude environment/model/capabilities and app-server provider/model/catalog snapshots. Request-level TUI response observation is unavailable in direct mode; transcript-based `agent.turn_ended` remains available. These restrictions supersede references to direct plain Codex TUI above.


## External Work HTTP API

The router exposes the authenticated Work API only when the existing router access policy permits the caller. It does not enable external access or add CORS. Use `GET /ca/work?session_id=claude:<id>` for the complete board and `GET /ca/work/runtime` for configured routing, exact-session runtime/build identity, observed model/error metadata and null for unknown observations.

Mutations use `POST /ca/work/nodes/add`, `update`, `remove` and `reorder`; every request requires the board's nonnegative integer `expected_version`. A stale version returns `409`; refetch before retrying. `GET /ca/work/events` provides coalesced version invalidations and runtime-error invalidations; clients must refetch after snapshot, change, reset or reconnect. Error rows are correlated and allowlisted; prompts, request bodies, credentials and raw credential-error text are not returned.
