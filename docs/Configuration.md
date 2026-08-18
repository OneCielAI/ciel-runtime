# Configuration — 설정 시스템

> 소스: `ciel_runtime.py` — `load_config()`, `save_config()`, `apply_config_migrations()`, `CONFIG_DIR`

---

## 설정 디렉터리

| OS | 기본 경로 |
|----|----------|
| Linux/macOS | `~/.config/ciel-runtime/` |
| Windows | `%APPDATA%\ciel-runtime\` |

환경변수 `CIEL_RUNTIME_CONFIG_DIR`로 재정의 가능.

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

Codex가 local checkpoint compact를 일반 `/v1/responses` 요청으로 보내고, 그 요청이 선택한
provider/model의 입력 예산을 초과하면 translated Chat/Anthropic 경로에서만 기존 segmented
compact를 자동 사용한다. 보조 map 호출은 최대 8개이며, 더 많은 chunk가 필요하면 추가 모델
호출 없이 deterministic soft guard로 전환한다. 정상 turn, 예산 안의 checkpoint, native
Responses의 opaque compaction item에는 이 경로를 적용하지 않는다. provider 설정에
`context_compact_llm=false`를 명시하면 자동 segmented compact도 끌 수 있고, `true`는 기존의
명시적 opt-in 동작을 유지한다.

Alibaba Model Studio Token Plan(`alitoken`)의 native Responses endpoint는 실제 서비스가
10 MiB wire body를 거절하므로, Ciel은 이 provider에만 9 MiB 선제 경계를 적용한다. 본문이
경계를 넘으면 기존 Responses soft guard로 오래된 history를 deterministic chunk summary로
바꾸되 최신 safe tail과 opaque Responses 필드는 보존한다. 최대 4회의 bounded compaction 뒤에도
10 MiB를 넘는 단일 비축약 항목은 upstream으로 보내거나 재시도하지 않고 로컬 413으로 종료한다.
`alitoken-individual`과 다른 provider에는 이 wire 정책을 적용하지 않는다.

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

---

## 관련 문서
- [[CLI-Reference]] — 설정 변경 CLI 커맨드
- [[Providers]] — 제공자별 설정 옵션
- [[Observability]] — 이벤트 로그 설정
