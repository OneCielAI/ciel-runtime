# Installation — 설치 방법

> 소스: `install.sh`, `install.ps1`, `package.json`

---

## npm으로 설치 (권장)

```bash
npm install -g @oneciel-ai/ciel-runtime
```

설치 후 아래 명령이 PATH에 추가된다:

| 명령 | 별칭 | 용도 |
|------|------|------|
| `ciel-runtime` | `ciel-runtime`, `cielrt` | 메인 실행 |
| `ciel-runtimectl` | — | 설정 제어 CLI |
| `ciel-runtime-stop` | — | Router 중지 |

### 요구사항
- Node.js ≥ 18
- Python 3 (`python3` 또는 `python`)

### npm install script

npm 패키지는 `postinstall` 같은 install script를 사용하지 않는다. 따라서 `allow-scripts` 설정이 엄격한 환경에서도 추가 승인 없이 설치된다.

업그레이드 후 이미 실행 중인 세션이 새 코드를 쓰게 하려면 명시적으로 재시작한다:

```bash
ciel-runtime-stop
ciel-runtime --continue
```

---

## Shell Script로 설치 (Linux/macOS)

```bash
PREFIX=$HOME/.local ./install.sh
```

설치 위치:
- 실행 파일: `$PREFIX/bin/` (기본: `~/.local/bin/`)
- 소스: `$CIEL_RUNTIME_HOME` (기본: `$PREFIX/share/ciel-runtime/`)

설치 파일:
```
~/.local/share/ciel-runtime/ciel_runtime.py
~/.local/share/ciel-runtime/ciel_runtime_support/
~/.local/bin/ciel-runtime
~/.local/bin/ciel-runtimectl
~/.local/bin/ciel-runtime-stop
~/.local/bin/ciel-runtime-menu
~/.local/bin/ciel-runtime-tool-guard
```

---

## PowerShell로 설치 (Windows)

```powershell
.\install.ps1
```

설치 위치:
- 실행 파일: `%LOCALAPPDATA%\ciel-runtime\bin\`
- 소스: `%LOCALAPPDATA%\ciel-runtime\`

---

## 첫 실행

### 1. 제공자 설정
```bash
# Ollama 사용
ciel-runtimectl provider ollama
ciel-runtimectl model qwen3-coder:30b

# Anthropic 사용
ciel-runtimectl provider anthropic
ciel-runtimectl api-key sk-ant-...

# DeepSeek 사용
ciel-runtimectl provider deepseek
ciel-runtimectl api-key sk-...
```

### 2. Claude Code 실행
```bash
ciel-runtime
```

Router가 자동으로 시작되고 Claude Code가 Router를 통해 설정된 제공자에 연결된다.

### Grok Build 실행

공식 Grok Build CLI가 설치되어 있으면 독립 runtime으로 실행할 수 있다.

```bash
ciel-runtime grok
ciel-runtime grok --continue
ciel-runtime grok agent stdio
```

Windows 설치 명령은 `irm https://x.ai/cli/install.ps1 | iex`이고 Linux/macOS는
`curl -fsSL https://x.ai/cli/install.sh | bash`다. 현재 provider가 `xai`이면 저장된
xAI API key, 선택 모델, reasoning effort를 launch process에만 투영한다. Grok의
`~/.grok` 인증·세션·사용자 설정은 Ciel이 덮어쓰지 않는다.

---

## Router 시작/중지

```bash
# 백그라운드 Router 시작
ciel-runtimectl serve

# Router 상태 확인
ciel-runtimectl status

# Router 중지
ciel-runtime-stop
```

---

## Python 실행 파일 지정

자동 탐색 순서:
1. 환경변수 `CIEL_RUNTIME_PYTHON`
2. Windows: `py -3` → `python` → `python3`
3. 기타: `python3` → `python`

수동 지정:
```bash
export CIEL_RUNTIME_PYTHON=/usr/local/bin/python3.12
```

---

## 설치 진단

```bash
ciel-runtimectl install-diag
```

설치 상태, Python 버전, 의존성 등을 점검한다.

---

## Statusline 설치 (선택사항)

셸 프롬프트에 현재 제공자/모델 표시:

```bash
ciel-runtimectl install-statusline
```

설치 위치: `~/.local/bin/ciel-runtime-statusline.py` 또는 Windows 동등 경로.

---

## 관련 문서
- [[CLI-Reference]] — 전체 CLI 커맨드
- [[Configuration]] — 설정 파일 위치
- [[Providers]] — 제공자 설정
