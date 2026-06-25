# install.md — setup modes

이 문서는 Ciel Runtime을 설치한 뒤 설정을 적용하는 방법을 목적별로 정리한다.
설치 위치와 파일 구성은 `docs/Installation.md`, 전체 명령 목록은 `docs/CLI-Reference.md`를 함께 참고한다.

## 1. 기본 설치

npm 설치가 권장 경로다.

```bash
npm install -g @oneciel-ai/ciel-runtime
```

설치 후 사용할 명령:

```bash
ciel-runtime --version
ciel-runtime
ciel-runtimectl status
```

수동 설치가 필요하면 repository 루트에서 다음을 실행한다.

```bash
# Linux/macOS
PREFIX=$HOME/.local ./install.sh

# Windows PowerShell
.\install.ps1
```

Python 실행 파일을 직접 지정해야 하는 환경에서는 `CIEL_RUNTIME_PYTHON`을 사용한다.

```bash
export CIEL_RUNTIME_PYTHON=/usr/local/bin/python3.12
```

## 2. 프롬프트/메뉴 기반 설정

아무 옵션 없이 실행하면 저장된 설정을 기준으로 Claude Code를 실행한다. 설정이 비어 있거나 메뉴가 필요한 경우에는 대화형 메뉴를 사용할 수 있다.

```bash
ciel-runtime --ca-menu
```

메뉴 언어는 실행 시점에 지정할 수 있다.

```bash
ciel-runtime --ca-language ko --ca-menu
ciel-runtime --ca-language en --ca-menu
```

제공자 선택만 메뉴로 열고 싶으면 다음 명령을 사용한다.

```bash
ciel-runtime provider
ciel-runtimectl provider
```

설정만 적용하고 런타임을 바로 띄우지 않으려면 `--ca-no-launch`를 붙인다.

```bash
ciel-runtime --ca-provider deepseek --ca-model deepseek-coder --ca-no-launch
```

## 3. CLI 플래그 기반 설정

`--ca-*` 옵션은 Claude Code 또는 Codex 자체 옵션과 충돌하지 않도록 Ciel Runtime 전용 namespace로 분리되어 있다.

```bash
ciel-runtime \
  --ca-provider deepseek \
  --ca-api-key "$DEEPSEEK_API_KEY" \
  --ca-model deepseek-coder \
  --ca-auto-llm-options
```

자주 쓰는 옵션:

| 옵션 | 용도 |
|------|------|
| `--ca-provider PROVIDER` | 현재 provider 설정 |
| `--ca-model MODEL_ID` | 현재 provider 모델 설정 |
| `--ca-base-url URL` | 현재 provider base URL 설정 |
| `--ca-api-key KEY` | 현재 provider 단일 API key 저장 |
| `--ca-api-key-env ENVVAR` | ENVVAR 값을 읽어 현재 provider API key로 저장 |
| `--ca-api-keys KEY1,KEY2` | 다중 API key 저장 |
| `--ca-api-keys-env ENVVAR` | ENVVAR 값을 읽어 다중 API key로 저장 |
| `--ca-advisor-model MODEL_ID` | Advisor model 설정 |
| `--ca-auto-llm-options [MODEL_ID]` | 추천 LLM 옵션 적용 |
| `--ca-no-launch` | 설정만 적용하고 종료 |
| `--ca-menu` | 설정 적용 후 메뉴 열기 |
| `--ca-runtime claude|codex` | 실행 런타임 선택 |
| `--ca-no-update-check` | Claude Code/Codex 업데이트 체크 생략 |
| `--ca-no-self-update-check` | Ciel Runtime self-update 체크 생략 |

runtime 옵션을 그대로 넘기려면 `--` 뒤에 둔다.

```bash
ciel-runtime --ca-provider ollama -- --permission-mode bypassPermissions
ciel-runtime --ca-runtime codex -- exec "write a short status report"
```

## 4. 환경변수 기반 무인 설정

CI, 서버, 원격 세션에서는 `CIEL_RUNTIME_*` 환경변수로 설정을 주입할 수 있다. 환경변수에서 설정값이 발견되면 메뉴 없이 headless 설정으로 처리된다.

```bash
export CIEL_RUNTIME_PROVIDER=deepseek
export CIEL_RUNTIME_MODEL=deepseek-coder
export CIEL_RUNTIME_API_KEY_ENV=DEEPSEEK_API_KEY
export CIEL_RUNTIME_SKIP_MENU=1

ciel-runtime --ca-no-launch
ciel-runtime
```

주요 환경변수:

| 변수 | 용도 |
|------|------|
| `CIEL_RUNTIME_PROVIDER` | provider 선택 |
| `CIEL_RUNTIME_MODEL` | 모델 선택 |
| `CIEL_RUNTIME_BASE_URL` | base URL 설정 |
| `CIEL_RUNTIME_API_KEY` | 직접 API key 값 지정 |
| `CIEL_RUNTIME_API_KEY_ENV` | API key를 읽을 환경변수 이름 지정 |
| `CIEL_RUNTIME_API_KEYS` | comma/semicolon/newline 구분 다중 key |
| `CIEL_RUNTIME_API_KEYS_ENV` | 다중 key를 읽을 환경변수 이름 지정 |
| `CIEL_RUNTIME_ADVISOR_MODEL` | Advisor model 설정 |
| `CIEL_RUNTIME_LANGUAGE` | 메뉴/출력 언어 설정 (`en`, `ko`, `ja`, `zh`) |
| `CIEL_RUNTIME_SKIP_MENU` | `1`이면 메뉴 생략 |
| `CIEL_RUNTIME_FORCE_MENU` | true면 메뉴 강제 표시 |
| `CIEL_RUNTIME_UPDATE_CHECK` | Claude Code/Codex 업데이트 체크 on/off |
| `CIEL_RUNTIME_SELF_UPDATE_CHECK` | Ciel Runtime self-update 체크 on/off |
| `CIEL_RUNTIME_CONFIG_DIR` | 설정 디렉터리 재정의 |
| `CIEL_RUNTIME_PYTHON` | 사용할 Python 실행 파일 |

provider 옵션도 환경변수로 줄 수 있다.

```bash
export CIEL_RUNTIME_MAX_OUTPUT_TOKENS=8192
export CIEL_RUNTIME_CONTEXT_WINDOW=131072
export CIEL_RUNTIME_REQUEST_TIMEOUT_MS=600000
export CIEL_RUNTIME_STREAM_IDLE_TIMEOUT_MS=300000
export CIEL_RUNTIME_RATE_LIMIT_RPM=60
export CIEL_RUNTIME_RATE_LIMIT_STATUS=on
export CIEL_RUNTIME_STREAM=on
export CIEL_RUNTIME_STREAM_WORD_CHUNKING=on
```

Ollama 전용 옵션:

```bash
export CIEL_RUNTIME_OLLAMA_NUM_CTX=65536
export CIEL_RUNTIME_OLLAMA_OPTIONS="temperature=0.2 top_p=0.9"
```

## 5. .env 파일 기반 설정

`--ca-env-file`은 지정한 파일의 `CIEL_RUNTIME_*` 값을 실행 전에 로드한다. secrets를 repository에 커밋하지 말고 로컬 또는 CI secret에서 생성한 파일을 사용한다.

```bash
# .ciel-runtime.env
CIEL_RUNTIME_PROVIDER=deepseek
CIEL_RUNTIME_MODEL=deepseek-coder
CIEL_RUNTIME_API_KEY_ENV=DEEPSEEK_API_KEY
CIEL_RUNTIME_SKIP_MENU=1
CIEL_RUNTIME_UPDATE_CHECK=on
CIEL_RUNTIME_SELF_UPDATE_CHECK=on
```

```bash
ciel-runtime --ca-env-file .ciel-runtime.env --ca-no-launch
ciel-runtime --ca-env-file .ciel-runtime.env
```

## 6. 완전 무인 설치/업그레이드

런타임 실행 직전에 Ciel Runtime은 필요한 경우 Claude Code와 Codex 설치/업데이트를 자동으로 처리한다. 수동으로 한 번에 업데이트하고 종료하려면 다음 명령을 사용한다.

```bash
ciel-runtime --ca-upgrade-and-exit
```

이 경로는 Ciel Runtime, Claude Code, Codex를 순서대로 업데이트하고 사용자에게 `y/N`을 묻지 않는다.

업데이트 체크를 끄는 방법:

```bash
ciel-runtime --ca-no-update-check --ca-no-self-update-check
```

환경변수로 끄는 방법:

```bash
export CIEL_RUNTIME_UPDATE_CHECK=off
export CIEL_RUNTIME_SELF_UPDATE_CHECK=off
```

## 7. 무인 설정 예시

### DeepSeek를 Claude Code 런타임으로 사용

```bash
export DEEPSEEK_API_KEY="sk-..."

ciel-runtime \
  --ca-provider deepseek \
  --ca-api-key-env DEEPSEEK_API_KEY \
  --ca-model deepseek-coder \
  --ca-auto-llm-options \
  --ca-no-launch

ciel-runtime
```

### Ollama 원격 서버 사용

```bash
ciel-runtime \
  --ca-provider ollama \
  --ca-base-url http://ollama.example.com:11434 \
  --ca-model qwen3-coder:30b \
  --ca-ollama-num-ctx 65536 \
  --ca-no-launch
```

### Codex 런타임으로 실행

```bash
ciel-runtime \
  --ca-provider ollama \
  --ca-model qwen3-coder:30b \
  --ca-runtime codex \
  -- exec "summarize the repository"
```

Codex 실행 시 Ciel Runtime은 `~/.codex/config.toml`을 수정하지 않고 실행 인자 `-c model_providers.ciel-runtime...`로 로컬 router를 OpenAI Responses provider로 주입한다.

### CI에서 설정만 검증

```bash
ciel-runtime \
  --ca-env-file .ciel-runtime.env \
  --ca-no-launch \
  --ca-no-update-check \
  --ca-no-self-update-check
```

## 8. 채널/SSE 관련 설정

외부 채널을 켤 때는 channel spec을 설정에 저장한다.

```bash
ciel-runtime --ca-channel server:ciel-runtime-router --ca-channel-delivery native --ca-no-launch
```

환경변수 방식:

```bash
export CIEL_RUNTIME_CHANNELS=server:ciel-runtime-router
export CIEL_RUNTIME_CHANNEL_DELIVERY=native
```

Codex idle wake-up은 Responses SSE에 직접 외부 이벤트를 밀어 넣는 방식이 아니라 Codex App Server 또는 `codex exec resume` 계열 설계로 처리해야 한다. 자세한 내용은 `docs/Codex-CLI-Research.md`를 참고한다.

## 9. 진단

설치 상태:

```bash
ciel-runtimectl install-diag
```

라우터 상태:

```bash
ciel-runtimectl status
```

패키지 구성 확인:

```bash
npm pack --dry-run
```
