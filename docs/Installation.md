# Installation — 설치 방법

> 소스: `install.sh`, `install.ps1`, `npm-bin/postinstall.js`, `package.json`

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

### 포스트인스톨 동작

`npm-bin/postinstall.js` — 설치 시 기존 Router 프로세스 자동 중지:
```
python3 ciel_runtime.py cli stop
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
