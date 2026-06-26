#!/usr/bin/env python3
from __future__ import annotations

import json
import select
import shutil
import subprocess
import sys
import termios
import time
import textwrap
import tty
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

try:
    import msvcrt
    HAS_MSVCRT = True
except ImportError:
    HAS_MSVCRT = False

try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass


def _enable_windows_ansi() -> None:
    if sys.platform != "win32":
        return
    try:
        import ctypes
        kernel32 = ctypes.windll.kernel32
        hOut = kernel32.GetStdHandle(-11)
        mode = ctypes.c_ulong()
        kernel32.GetConsoleMode(hOut, ctypes.byref(mode))
        ENABLE_VIRTUAL_TERMINAL_PROCESSING = 0x0004
        kernel32.SetConsoleMode(hOut, mode.value | ENABLE_VIRTUAL_TERMINAL_PROCESSING)
    except Exception:
        pass


class _RawTerminal:
    def __enter__(self):
        _enable_windows_ansi()
        if sys.platform != "win32" and sys.stdin.isatty():
            self._fd = sys.stdin.fileno()
            self._old = termios.tcgetattr(self._fd)
            tty.setraw(self._fd)
        return self

    def __exit__(self, *a):
        if sys.platform != "win32" and hasattr(self, "_old"):
            termios.tcsetattr(self._fd, termios.TCSADRAIN, self._old)
        return False


def _getch(timeout: float = 60.0) -> bytes | None:
    if sys.platform == "win32" and HAS_MSVCRT:
        start = time.monotonic()
        while time.monotonic() - start < timeout:
            if msvcrt.kbhit():
                return msvcrt.getch()
            time.sleep(0.01)
        return None
    else:
        r, _, _ = select.select([sys.stdin.buffer], [], [], timeout)
        if r:
            return sys.stdin.buffer.read(1)
        return None


def _debug_log(msg: str) -> None:
    try:
        with open("/tmp/ca-menu-debug.log", "a", encoding="utf-8") as f:
            f.write(f"{time.monotonic():.3f} {msg}\n")
            f.flush()
    except Exception:
        pass


def read_menu_key() -> str:
    ch = _getch()
    _debug_log(f"_getch returned: {repr(ch)}")
    if ch is None:
        return ""
    if ch == b"\x1b":
        seq = b"\x1b"
        for _ in range(3):
            nxt = _getch(1.0)
            _debug_log(f"  seq byte: {repr(nxt)}")
            if nxt is None:
                break
            seq += nxt
        _debug_log(f"  full seq: {repr(seq)} hex: {seq.hex()}")
        if seq in (b"\x1b[A", b"\x1bOA"):
            return "KEY_UP"
        if seq in (b"\x1b[B", b"\x1bOB"):
            return "KEY_DOWN"
        if seq == b"\x1b[5~":
            return "KEY_PPAGE"
        if seq == b"\x1b[6~":
            return "KEY_NPAGE"
        return "KEY_ESC"
    if sys.platform == "win32" and HAS_MSVCRT:
        if ch in (b"\x00", b"\xe0"):
            ch2 = _getch(0.05)
            if ch2 == b"H":
                return "KEY_UP"
            if ch2 == b"P":
                return "KEY_DOWN"
            if ch2 == b"K":
                return "KEY_LEFT"
            if ch2 == b"M":
                return "KEY_RIGHT"
            if ch2 == b"I":
                return "KEY_PPAGE"
            if ch2 == b"Q":
                return "KEY_NPAGE"
            return ""
    if ch in (b"\r", b"\n"):
        return "KEY_ENTER"
    if ch in (b"\x7f", b"\x08"):
        return "KEY_BACKSPACE"
    if ch and 0 < ch[0] < 128 and chr(ch[0]).isprintable():
        return chr(ch[0])
    return ""


def _term_size() -> tuple[int, int]:
    try:
        return shutil.get_terminal_size(fallback=(80, 24))
    except Exception:
        return (80, 24)


def _clear() -> None:
    sys.stdout.write("\033[2J\033[H")
    sys.stdout.flush()


def _move(row: int, col: int) -> None:
    sys.stdout.write(f"\033[{row + 1};{col + 1}H")


def _style(fg: int | None = None, bg: int | None = None, bold: bool = False, dim: bool = False, reverse: bool = False) -> str:
    codes: list[str] = []
    if bold:
        codes.append("1")
    if dim:
        codes.append("2")
    if reverse:
        codes.append("7")
    if fg is not None:
        codes.append(f"38;5;{fg}")
    if bg is not None:
        codes.append(f"48;5;{bg}")
    return f"\033[{';&'.join(codes)}m" if codes else ""


def _reset() -> str:
    return "\033[0m"


ANIMATED_TEXT_PALETTE = (203, 209, 215, 221, 229, 187, 151, 116, 111, 147, 183, 219)


def animated_text(text: str, *, phase: int | None = None, bold: bool = True) -> str:
    if not sys.stdout.isatty():
        return text
    if phase is None:
        phase = int(time.monotonic() * 8)
    parts: list[str] = []
    for i, ch in enumerate(text):
        if ch.isspace():
            parts.append(ch)
            continue
        color = ANIMATED_TEXT_PALETTE[(phase + i) % len(ANIMATED_TEXT_PALETTE)]
        parts.append(_style(fg=color, bold=bold) + ch)
    parts.append(_reset())
    return "".join(parts)


def _write(row: int, col: int, text: str, style: str = "") -> None:
    if row < 0 or col < 0:
        return
    _move(row, col)
    if style:
        sys.stdout.write(style)
    sys.stdout.write(text)
    if style:
        sys.stdout.write(_reset())
    sys.stdout.flush()


def _write_safe(row: int, col: int, text: str, style: str = "") -> None:
    h, w = _term_size()
    if row < 0 or row >= h or col >= w:
        return
    _write(row, col, text[: max(0, w - max(0, col) - 1)], style)


CTL = str(Path.home() / ".local/bin/ciel-runtimectl")
CONFIG = Path.home() / ".config/ciel-runtime/config.json"
NCP_ENV = Path.home() / ".config/nvd-claude-proxy/.env"
PROVIDERS = [
    ("anthropic:routed", "Anthropic routed"),
    ("codex:native", "Codex Native"),
    ("codex:routed", "Codex routed"),
    ("anthropic:native", "Claude Native"),
    ("deepseek", "DeepSeek.com"),
    ("lm-studio", "LM Studio"),
    ("nvidia-hosted", "Nvidia Hosted"),
    ("ollama", "Ollama"),
    ("ollama-cloud", "Ollama Cloud"),
    ("self-hosted-nim", "Self Hosted NIM"),
    ("vllm", "vLLM"),
]
APP_NAME = "Ciel Runtime"


def app_version() -> str:
    try:
        package = json.loads(Path(__file__).with_name("package.json").read_text(encoding="utf-8"))
        return str(package.get("version") or "").strip()
    except Exception:
        return ""


def app_title() -> str:
    version = app_version()
    return f"{APP_NAME} v{version}" if version else APP_NAME


CREDITS = "Credits: One Ciel LLC"
PRELAUNCH_CANCEL = 10
PRELAUNCH_LAUNCH_CODEX = 11
PRELAUNCH_LAUNCH_CLAUDE = 12
LANGUAGES = {
    "en": "English",
    "ko": "한국어",
    "ja": "日本語",
    "zh": "中文",
}
UI_TEXT = {
    "en": {
        "language": "Language",
        "provider": "Provider",
        "api_key": "API key",
        "base_url": "Base URL",
        "model": "Model",
        "advisor_model": "Advisor Model",
        "ollama_options": "Ollama options",
        "provider_options": "Provider options",
        "test": "Test compatibility",
        "launch": "Launch Claude Code",
        "launch_codex": "Launch Codex",
        "quit": "Quit",
        "title": "ciel-runtime pre-launch",
        "select_language": "Enter selects language. Up/Down moves inside submenu. Esc closes submenu.",
        "select_provider": "Enter selects provider. Up/Down moves inside submenu. Esc closes submenu.",
        "select_model": "Enter selects model. Up/Down moves inside submenu. Esc closes submenu. Custom input is at the end.",
        "select_advisor_model": "Enter selects advisor model. Use a long-context model such as deepseek-v4-pro.",
        "select_ollama_options": "Enter applies this Ollama option. Custom input accepts KEY=VALUE or unset:KEY.",
        "select_provider_options": "Enter applies this provider option. Custom input accepts KEY=VALUE or unset:KEY.",
        "test_result": "Compatibility result is shown inline. Esc closes the result. Enter runs the test again.",
        "help_launch": "Enter launches Claude Code with the selected provider and model.",
        "help_launch_codex": "Enter launches Codex with the selected provider mode.",
        "help_test": "Enter tests current provider/model with a minimal Claude Code tool request.",
        "help_language": "Enter expands language submenu inline.",
        "help_provider": "Enter expands provider submenu inline.",
        "help_model": "Enter expands model submenu inline when the provider endpoint is reachable.",
        "help_advisor_model": "Enter selects the larger model used by ciel-runtime advisor routing.",
        "help_ollama_options": "Enter expands Ollama context and generation options.",
        "help_provider_options": "Enter expands provider output/context/timeout options.",
        "help_api_key": "Enter opens secure API key setup in the terminal. Keys are not pasted into Claude Code.",
        "help_base_url": "Enter edits the current provider base URL on this row.",
        "help_quit": "Enter exits without launching Claude Code.",
        "running_test": "Running compatibility test...",
        "test_passed": "Compatibility test passed.",
        "test_failed": "Compatibility test failed.",
        "loading_models": "Loading models from current provider...",
        "api_key_unchanged": "API key unchanged.",
    },
    "ko": {
        "language": "언어",
        "provider": "프로바이더",
        "api_key": "API 키",
        "base_url": "Base URL",
        "model": "모델",
        "advisor_model": "Advisor Model",
        "ollama_options": "Ollama 옵션",
        "provider_options": "프로바이더 옵션",
        "test": "호환성 테스트",
        "launch": "Claude Code 실행",
        "launch_codex": "Codex 실행",
        "quit": "종료",
        "title": "ciel-runtime 실행 전 설정",
        "select_language": "Enter로 언어를 선택합니다. 위/아래로 이동, Esc로 닫기.",
        "select_provider": "Enter로 프로바이더를 선택합니다. 위/아래로 이동, Esc로 닫기.",
        "select_model": "Enter로 모델을 선택합니다. 위/아래로 이동, Esc로 닫기. 마지막 항목은 직접 입력입니다.",
        "select_advisor_model": "Advisor Model을 선택합니다. deepseek-v4-pro 같은 긴 컨텍스트 모델을 권장합니다.",
        "select_ollama_options": "Enter로 Ollama 옵션을 적용합니다. 직접 입력은 KEY=VALUE 또는 unset:KEY를 받습니다.",
        "select_provider_options": "Enter로 프로바이더 옵션을 적용합니다. 직접 입력은 KEY=VALUE 또는 unset:KEY를 받습니다.",
        "test_result": "호환성 결과가 메뉴 안에 표시됩니다. Esc로 닫고 Enter로 다시 테스트합니다.",
        "help_launch": "선택한 프로바이더와 모델로 Claude Code를 실행합니다.",
        "help_launch_codex": "선택한 프로바이더 모드로 Codex를 실행합니다.",
        "help_test": "현재 프로바이더/모델에 최소 Claude Code 도구 요청을 보내 호환성을 확인합니다.",
        "help_language": "언어 선택 메뉴를 펼칩니다.",
        "help_provider": "프로바이더 선택 메뉴를 펼칩니다.",
        "help_model": "프로바이더 엔드포인트가 유효하면 모델 선택 메뉴를 펼칩니다.",
        "help_advisor_model": "ciel-runtime advisor 라우팅에 사용할 더 큰 모델을 선택합니다.",
        "help_ollama_options": "Ollama 컨텍스트 크기와 생성 파라미터 메뉴를 펼칩니다.",
        "help_provider_options": "프로바이더의 출력 토큰, 컨텍스트, 타임아웃 옵션 메뉴를 펼칩니다.",
        "help_api_key": "API 키 입력을 이 터미널에서 안전하게 엽니다. 키는 Claude Code 채팅에 붙여넣지 않습니다.",
        "help_base_url": "현재 프로바이더의 Base URL을 이 줄에서 수정합니다.",
        "help_quit": "Claude Code를 실행하지 않고 종료합니다.",
        "running_test": "호환성 테스트 실행 중...",
        "test_passed": "호환성 테스트 성공.",
        "test_failed": "호환성 테스트 실패.",
        "loading_models": "현재 프로바이더에서 모델을 불러오는 중...",
        "api_key_unchanged": "API 키는 변경되지 않았습니다.",
    },
    "ja": {
        "language": "言語",
        "provider": "プロバイダー",
        "api_key": "APIキー",
        "base_url": "Base URL",
        "model": "モデル",
        "advisor_model": "Advisor Model",
        "ollama_options": "Ollamaオプション",
        "provider_options": "プロバイダーオプション",
        "test": "互換性テスト",
        "launch": "Claude Codeを起動",
        "launch_codex": "Codexを起動",
        "quit": "終了",
        "title": "ciel-runtime 起動前設定",
        "select_language": "Enterで言語を選択します。上下で移動、Escで閉じます。",
        "select_provider": "Enterでプロバイダーを選択します。上下で移動、Escで閉じます。",
        "select_model": "Enterでモデルを選択します。上下で移動、Escで閉じます。最後は手入力です。",
        "select_advisor_model": "Advisor Modelを選択します。deepseek-v4-proのような長コンテキストモデルを推奨します。",
        "select_ollama_options": "EnterでOllamaオプションを適用します。手入力はKEY=VALUEまたはunset:KEYです。",
        "select_provider_options": "Enterでプロバイダーオプションを適用します。手入力はKEY=VALUEまたはunset:KEYです。",
        "test_result": "互換性結果はメニュー内に表示されます。Escで閉じ、Enterで再テストします。",
        "help_launch": "選択したプロバイダーとモデルでClaude Codeを起動します。",
        "help_launch_codex": "選択したプロバイダーモードでCodexを起動します。",
        "help_test": "現在のプロバイダー/モデルへ最小のClaude Codeツール要求を送り互換性を確認します。",
        "help_language": "言語選択メニューを展開します。",
        "help_provider": "プロバイダー選択メニューを展開します。",
        "help_model": "プロバイダーのエンドポイントが有効な場合、モデル選択メニューを展開します。",
        "help_advisor_model": "ciel-runtime advisorルーティングで使う大きなモデルを選択します。",
        "help_ollama_options": "Ollamaのコンテキストサイズと生成パラメータを開きます。",
        "help_provider_options": "プロバイダーの出力トークン、コンテキスト、タイムアウト設定を開きます。",
        "help_api_key": "APIキー入力をこの端末で安全に開きます。キーはClaude Codeチャットに貼り付けません。",
        "help_base_url": "現在のプロバイダーのBase URLをこの行で編集します。",
        "help_quit": "Claude Codeを起動せずに終了します。",
        "running_test": "互換性テストを実行中...",
        "test_passed": "互換性テスト成功。",
        "test_failed": "互換性テスト失敗。",
        "loading_models": "現在のプロバイダーからモデルを読み込み中...",
        "api_key_unchanged": "APIキーは変更されませんでした。",
    },
    "zh": {
        "language": "语言",
        "provider": "提供商",
        "api_key": "API 密钥",
        "base_url": "Base URL",
        "model": "模型",
        "advisor_model": "Advisor Model",
        "ollama_options": "Ollama 选项",
        "provider_options": "提供商选项",
        "test": "兼容性测试",
        "launch": "启动 Claude Code",
        "launch_codex": "启动 Codex",
        "quit": "退出",
        "title": "ciel-runtime 启动前设置",
        "select_language": "按 Enter 选择语言。上下移动，Esc 关闭。",
        "select_provider": "按 Enter 选择提供商。上下移动，Esc 关闭。",
        "select_model": "按 Enter 选择模型。上下移动，Esc 关闭。最后一项可手动输入。",
        "select_advisor_model": "选择 Advisor Model。建议使用 deepseek-v4-pro 等长上下文模型。",
        "select_ollama_options": "按 Enter 应用 Ollama 选项。手动输入支持 KEY=VALUE 或 unset:KEY。",
        "select_provider_options": "按 Enter 应用提供商选项。手动输入支持 KEY=VALUE 或 unset:KEY。",
        "test_result": "兼容性结果会在菜单内显示。Esc 关闭，Enter 重新测试。",
        "help_launch": "使用所选提供商和模型启动 Claude Code。",
        "help_launch_codex": "使用所选提供商模式启动 Codex。",
        "help_test": "向当前提供商/模型发送最小 Claude Code 工具请求以检查兼容性。",
        "help_language": "展开语言选择菜单。",
        "help_provider": "展开提供商选择菜单。",
        "help_model": "当提供商端点可用时展开模型选择菜单。",
        "help_advisor_model": "选择 ciel-runtime advisor 路由使用的更大模型。",
        "help_ollama_options": "展开 Ollama 上下文大小和生成参数。",
        "help_provider_options": "展开提供商输出 token、上下文和超时选项。",
        "help_api_key": "在此终端安全输入 API 密钥。不要把密钥粘贴到 Claude Code 聊天中。",
        "help_base_url": "在这一行编辑当前提供商的 Base URL。",
        "help_quit": "不启动 Claude Code 并退出。",
        "running_test": "正在运行兼容性测试...",
        "test_passed": "兼容性测试成功。",
        "test_failed": "兼容性测试失败。",
        "loading_models": "正在从当前提供商加载模型...",
        "api_key_unchanged": "API 密钥未更改。",
    },
}


PROVIDER_NOTES = {
    "en": {
        "anthropic": [
            "Anthropic: uses Claude Code's native Anthropic connection.",
            "Set an Anthropic API key here, or run `claude /login` separately to use your Claude account login.",
        ],
        "ollama": [
            "Ollama: uses your local Ollama daemon; API key is normally not required.",
            "To use :cloud models through local Ollama, sign in on the Ollama host with `ollama signin`.",
        ],
        "ollama-cloud": [
            "Ollama Cloud: calls https://ollama.com/api directly; an Ollama API key is required.",
            "Use this when you want cloud models without relying on the local Ollama daemon's sign-in state.",
        ],
        "deepseek": [
            "DeepSeek.com: uses DeepSeek's Anthropic API endpoint for Claude Code.",
            "Set a DeepSeek API key; ciel-runtime maps pro[1m] as the main model and flash for Haiku/subagents.",
        ],
        "vllm": [
            "vLLM: enter the vLLM server root that implements the Anthropic Messages API.",
            "Do not enter an OpenAI-only chat completions endpoint; use a compatibility proxy for those servers.",
        ],
        "lm-studio": [
            "LM Studio: uses the local Anthropic-compatible /v1/messages server by default.",
            "Start LM Studio's Local Server and use http://127.0.0.1:1234/v1 as the base URL; set native=false only for router fallback.",
        ],
        "self-hosted-nim": [
            "Self-hosted NIM: enter the NIM server root that exposes Anthropic-compatible /v1/messages.",
            "This native path does not use the NVIDIA hosted API Catalog proxy.",
        ],
        "nvidia-hosted": [
            "NVIDIA hosted: uses NVIDIA API Catalog at https://integrate.api.nvidia.com/v1.",
            "Hosted catalog models are OpenAI-style, so ciel-runtime keeps a compatibility route for Claude Code.",
        ],
    },
    "ko": {
        "anthropic": [
            "Anthropic: Claude Code의 기본 Anthropic 연결을 사용합니다.",
            "여기에 Anthropic API key를 넣거나, 별도로 `claude /login`을 실행해 Claude 계정 로그인을 사용하세요.",
        ],
        "ollama": [
            "Ollama: 로컬 Ollama 데몬을 사용합니다. 일반 로컬 모델은 API key가 필요 없습니다.",
            "로컬 Ollama로 :cloud 모델을 쓰려면 Ollama가 실행되는 호스트에서 `ollama signin`이 필요합니다.",
        ],
        "ollama-cloud": [
            "Ollama Cloud: https://ollama.com/api를 직접 호출합니다. Ollama API key가 필요합니다.",
            "로컬 Ollama 데몬의 로그인 상태와 무관하게 클라우드 모델을 쓰고 싶을 때 사용합니다.",
        ],
        "deepseek": [
            "DeepSeek.com: Claude Code용 DeepSeek Anthropic API endpoint를 직접 사용합니다.",
            "DeepSeek API key가 필요하며, pro[1m]은 메인 모델로, flash는 Haiku/subagent 모델로 설정됩니다.",
        ],
        "vllm": [
            "vLLM: Anthropic Messages API를 구현한 vLLM 서버 root를 넣으세요.",
            "OpenAI 전용 chat completions endpoint를 넣지 마세요. 그런 서버는 호환 프록시가 필요합니다.",
        ],
        "lm-studio": [
            "LM Studio: 기본적으로 로컬 Anthropic 호환 /v1/messages 서버를 직접 사용합니다.",
            "LM Studio의 Local Server를 켜고 base URL은 http://127.0.0.1:1234/v1 을 사용하세요. 라우터 fallback이 필요할 때만 native=false를 쓰세요.",
        ],
        "self-hosted-nim": [
            "Self-hosted NIM: Anthropic 호환 /v1/messages를 노출하는 NIM 서버 root를 넣으세요.",
            "이 native 경로는 NVIDIA hosted API Catalog 프록시를 사용하지 않습니다.",
        ],
        "nvidia-hosted": [
            "NVIDIA hosted: https://integrate.api.nvidia.com/v1 의 NVIDIA API Catalog를 사용합니다.",
            "Hosted catalog 모델은 OpenAI 방식이므로 Claude Code에는 ciel-runtime 호환 라우트를 유지합니다.",
        ],
    },
    "ja": {
        "anthropic": [
            "Anthropic: Claude CodeのネイティブAnthropic接続を使います。",
            "ここでAnthropic API keyを設定するか、別途`claude /login`を実行してClaudeアカウントログインを使ってください。",
        ],
        "ollama": [
            "Ollama: ローカルのOllama daemonを使います。通常のローカルモデルではAPI keyは不要です。",
            "ローカルOllama経由で:cloudモデルを使うには、Ollamaホストで`ollama signin`が必要です。",
        ],
        "ollama-cloud": [
            "Ollama Cloud: https://ollama.com/api を直接呼び出します。Ollama API keyが必要です。",
            "ローカルOllama daemonのサインイン状態に依存せずクラウドモデルを使う場合に選びます。",
        ],
        "deepseek": [
            "DeepSeek.com: Claude Code向けDeepSeek Anthropic API endpointを直接使います。",
            "DeepSeek API keyが必要です。pro[1m]をメイン、flashをHaiku/subagentに設定します。",
        ],
        "vllm": [
            "vLLM: Anthropic Messages APIを実装したvLLMサーバーrootを入力してください。",
            "OpenAI専用chat completions endpointは入力しないでください。その場合は互換プロキシが必要です。",
        ],
        "lm-studio": [
            "LM Studio: 既定ではローカルのAnthropic互換 /v1/messages サーバーを直接使います。",
            "LM StudioのLocal Serverを起動し、base URLは http://127.0.0.1:1234/v1 を使ってください。router fallbackが必要な時だけnative=falseにします。",
        ],
        "self-hosted-nim": [
            "Self-hosted NIM: Anthropic互換/v1/messagesを公開するNIMサーバーrootを入力してください。",
            "このnative経路はNVIDIA hosted API Catalog proxyを使いません。",
        ],
        "nvidia-hosted": [
            "NVIDIA hosted: https://integrate.api.nvidia.com/v1 のNVIDIA API Catalogを使います。",
            "Hosted catalogモデルはOpenAI形式のため、Claude Codeにはciel-runtime互換ルートを維持します。",
        ],
    },
    "zh": {
        "anthropic": [
            "Anthropic: 使用Claude Code原生Anthropic连接。",
            "可在此设置Anthropic API key，或另行运行`claude /login`使用Claude账号登录。",
        ],
        "ollama": [
            "Ollama: 使用本地Ollama daemon；普通本地模型通常不需要API key。",
            "若通过本地Ollama使用:cloud模型，需要在运行Ollama的主机上执行`ollama signin`。",
        ],
        "ollama-cloud": [
            "Ollama Cloud: 直接调用 https://ollama.com/api；需要Ollama API key。",
            "当你想不依赖本地Ollama daemon登录状态使用云端模型时选择它。",
        ],
        "deepseek": [
            "DeepSeek.com: 直接使用面向 Claude Code 的 DeepSeek Anthropic API endpoint。",
            "需要 DeepSeek API key；pro[1m] 作为主模型，flash 用于 Haiku/subagent。",
        ],
        "vllm": [
            "vLLM: 请输入实现Anthropic Messages API的vLLM服务器root。",
            "不要输入仅OpenAI chat completions的端点；这类服务器需要兼容代理。",
        ],
        "lm-studio": [
            "LM Studio: 默认直接使用本地 Anthropic-compatible /v1/messages 服务器。",
            "启动 LM Studio 的 Local Server，并使用 http://127.0.0.1:1234/v1 作为 base URL；仅在需要路由 fallback 时设置 native=false。",
        ],
        "self-hosted-nim": [
            "Self-hosted NIM: 请输入暴露 Anthropic-compatible /v1/messages 的 NIM 服务器 root。",
            "此 native 路径不使用 NVIDIA hosted API Catalog 代理。",
        ],
        "nvidia-hosted": [
            "NVIDIA hosted: 使用 https://integrate.api.nvidia.com/v1 的 NVIDIA API Catalog。",
            "Hosted catalog 模型是 OpenAI 风格，因此 Claude Code 仍使用 ciel-runtime 兼容路由。",
        ],
    },
}


def init_colors() -> None:
    pass


def cp(n: int) -> str:
    if n == 1:
        return _style(fg=255)
    if n == 2:
        return _style(fg=10)
    if n == 3:
        return _style(fg=11)
    if n == 4:
        return _style(fg=9)
    if n == 5:
        return _style(fg=255)
    if n == 6:
        return _style(fg=208)
    return ""


def load_cfg() -> dict:
    if CONFIG.exists():
        try:
            return json.loads(CONFIG.read_text())
        except Exception:
            pass
    return {"current_provider": "nvidia-hosted", "providers": {}}


KNOWN_NVIDIA_MODEL_STATUS = {
    "claude-nvidia-llama-3.1-nemotron-ultra-253b-v1": ("FAIL 404", "listed but not callable for this NVIDIA account"),
}
DEFAULT_ADVISOR_MODELS = ["deepseek-v4-pro", "claude-opus-4-6", "claude-sonnet-4-6", "glm-5.1"]
COMPAT_OK_TTL_SECONDS = 24 * 60 * 60
COMPAT_FAIL_TTL_SECONDS = 5 * 60


def cache_age_seconds(entry: dict) -> int | None:
    try:
        tested_at = int(entry.get("tested_at"))
    except Exception:
        return None
    return max(0, int(time.time()) - tested_at)


def cache_entry_fresh(entry: dict) -> bool:
    age = cache_age_seconds(entry)
    if age is None:
        return False
    ttl = COMPAT_OK_TTL_SECONDS if entry.get("ok") else COMPAT_FAIL_TTL_SECONDS
    return age <= ttl


def human_age(seconds: int | None) -> str:
    if seconds is None:
        return "unknown age"
    if seconds < 60:
        return f"{seconds}s ago"
    if seconds < 3600:
        return f"{seconds // 60}m ago"
    return f"{seconds // 3600}h ago"


def compatibility_entry(provider: str, upstream: str, alias: str | None = None) -> dict | None:
    cache = load_cfg().get("compatibility_cache", {})
    if not isinstance(cache, dict):
        return None
    provider_cache = cache.get(provider, {})
    if not isinstance(provider_cache, dict):
        return None
    for key in (alias, upstream):
        if key and isinstance(provider_cache.get(key), dict):
            entry = provider_cache[key]
            return entry if cache_entry_fresh(entry) else None
    return None


def compatibility_badge(provider: str, upstream: str, alias: str | None = None) -> str:
    if provider == "nvidia-hosted":
        known = KNOWN_NVIDIA_MODEL_STATUS.get(upstream) or (KNOWN_NVIDIA_MODEL_STATUS.get(alias or "") if alias else None)
        if known:
            return f"[{known[0]}]"
    entry = compatibility_entry(provider, upstream, alias)
    if not entry:
        return "[untested]" if provider == "nvidia-hosted" else ""
    if entry.get("ok"):
        return "[OK]"
    code = entry.get("code")
    if code:
        return f"[FAIL {code}]"
    msg = str(entry.get("message") or "").lower()
    if "timeout" in msg or "timed out" in msg:
        return "[TIMEOUT]"
    return "[FAIL]"


def current_compatibility_line(provider: str, pcfg: dict) -> str | None:
    model = str(pcfg.get("current_model") or "")
    if not model:
        return "Compatibility: no model selected"
    badge = compatibility_badge(provider, model, model)
    if badge:
        entry = compatibility_entry(provider, model, model)
        if entry and not entry.get("ok"):
            msg = str(entry.get("message") or entry.get("diagnosis") or "")[:90]
            return f"Compatibility: {badge} {model} {msg}".strip()
        known = KNOWN_NVIDIA_MODEL_STATUS.get(model)
        if known:
            return f"Compatibility: {badge} {model} - {known[1]}"
        return f"Compatibility: {badge} {model}"
    return None


def current_language() -> str:
    lang = load_cfg().get("language", "en")
    return lang if lang in LANGUAGES else "en"


def t(key: str) -> str:
    lang = current_language()
    return UI_TEXT.get(lang, UI_TEXT["en"]).get(key, UI_TEXT["en"].get(key, key))


def run_cmd(args: list[str]) -> tuple[int, str]:
    p = subprocess.run(args, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    return p.returncode, p.stdout


def read_env_file(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    env: dict[str, str] = {}
    for line in path.read_text(errors="ignore").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        env[k.strip()] = v.strip().strip("'\"")
    return env


def meaningful_key(value: str | None) -> bool:
    return bool(value and value not in ("dummy", "not-used", "ollama"))


def api_key_status(provider: str, pcfg: dict) -> str:
    if provider == "nvidia-hosted":
        return "API key: set (NVIDIA)" if meaningful_key(read_env_file(NCP_ENV).get("NVIDIA_API_KEY")) else "API key: missing (NVIDIA required)"
    if provider == "anthropic":
        return "API key: set (Anthropic)" if meaningful_key(pcfg.get("api_key")) else "API key: not set (use API key or Claude login)"
    if provider == "codex":
        if pcfg.get("route_through_router"):
            return "API key: not set (uses native Codex login/auth headers)"
        return "API key: optional fallback (uses native Codex login/config)"
    if provider == "ollama-cloud":
        return "API key: set (Ollama Cloud)" if meaningful_key(pcfg.get("api_key")) else "API key: missing (Ollama Cloud required)"
    if provider == "deepseek":
        return "API key: set (DeepSeek)" if meaningful_key(pcfg.get("api_key")) else "API key: missing (DeepSeek required)"
    key = pcfg.get("api_key")
    if meaningful_key(key):
        return "API key: set"
    if provider == "ollama":
        return "API key: not required for Ollama"
    return "API key: optional or not configured"


def launch_requires_api_key(provider: str, pcfg: dict) -> bool:
    return provider in ("nvidia-hosted", "ollama-cloud", "deepseek") and "missing" in api_key_status(provider, pcfg).lower()


def join_url(base: str, path: str) -> str:
    base = base.rstrip("/")
    if base.endswith("/v1") and path.startswith("/v1/"):
        return base + path[3:]
    return base + path


def probe_base_url(provider: str, pcfg: dict) -> str:
    base = (pcfg.get("base_url") or "").rstrip("/")
    if not base:
        return "Base URL: missing"
    if "your-" in base:
        return f"Base URL: placeholder ({base})"
    if provider == "nvidia-hosted":
        return f"Base URL: NVIDIA hosted ({base}); local router http://127.0.0.1:8799 starts on launch"
    if provider == "codex":
        if pcfg.get("route_through_router"):
            return "Base URL: Codex routed through local router (/backend-api/codex)"
        return "Base URL: native Codex config (not overridden)"
    if provider == "deepseek":
        return f"Base URL: DeepSeek Anthropic API configured ({base})"
    path = "/api/tags" if provider in ("ollama", "ollama-cloud") else "/v1/models"
    url = join_url(base, path)
    headers = {}
    key = pcfg.get("api_key")
    if meaningful_key(key):
        headers = {"x-api-key": key, "authorization": f"Bearer {key}"}
    try:
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=2.5) as resp:
            body = resp.read(131072).decode("utf-8", errors="ignore")
        count = ""
        try:
            data = json.loads(body)
            if provider in ("ollama", "ollama-cloud"):
                count = f", {len(data.get('models', []))} models"
            elif isinstance(data.get("data"), list):
                count = f", {len(data['data'])} models"
        except Exception:
            pass
        return f"Base URL: model list reachable ({path}{count})"
    except urllib.error.HTTPError as exc:
        if exc.code in (401, 403):
            return f"Base URL: model list reachable, auth rejected ({exc.code})"
        return f"Base URL: HTTP {exc.code}"
    except Exception as exc:
        if provider == "nvidia-hosted" and "127.0.0.1" in base:
            return "Base URL: proxy down; starts on launch"
        return f"Base URL: unreachable ({type(exc).__name__})"


def preflight_checks() -> list[str]:
    provider, pcfg = current_provider_cfg()
    lang = current_language()
    notes = PROVIDER_NOTES.get(lang, PROVIDER_NOTES["en"]).get(provider, [])
    lines = [
        probe_base_url(provider, pcfg),
        api_key_status(provider, pcfg),
        *notes,
    ]
    compat = current_compatibility_line(provider, pcfg)
    if compat:
        lines.append(compat)
    return lines


def provider_from_choice(value: str) -> str:
    return str(value or "").split(":", 1)[0]


def provider_choice_for_cfg(cfg: dict) -> str:
    provider = cfg.get("current_provider", "nvidia-hosted")
    pcfg = cfg.get("providers", {}).get(provider, {})
    if provider == "anthropic":
        return "anthropic:routed" if bool(pcfg.get("route_through_router", False)) else "anthropic:native"
    if provider == "codex":
        return "codex:routed" if bool(pcfg.get("route_through_router", False)) else "codex:native"
    return provider


def claude_launch_enabled(provider: str) -> bool:
    return provider != "codex"


def codex_launch_enabled(provider: str) -> bool:
    return provider == "codex"


def provider_preview_checks(provider: str) -> list[str]:
    provider = provider_from_choice(provider)
    cfg = load_cfg()
    pcfg = cfg.get("providers", {}).get(provider, {})
    lang = current_language()
    notes = PROVIDER_NOTES.get(lang, PROVIDER_NOTES["en"]).get(provider, [])
    return [
        f"Base URL: {pcfg.get('base_url') or 'unset'}",
        api_key_status(provider, pcfg),
        *notes,
    ]


def selected_provider_value(sub: dict | None) -> str | None:
    if not sub or sub.get("kind") != "provider":
        return None
    try:
        return str(sub["items"][sub["idx"]]["value"])
    except Exception:
        return None


def status_text() -> list[str]:
    _, out = run_cmd([CTL, "status"])
    return out.strip().splitlines() if out else ["status unavailable"]


def current_provider() -> str:
    return load_cfg().get("current_provider", "nvidia-hosted")


def current_provider_cfg() -> tuple[str, dict]:
    cfg = load_cfg()
    provider = cfg.get("current_provider", "nvidia-hosted")
    return provider, cfg.get("providers", {}).get(provider, {})


def is_ollama_provider(provider: str) -> bool:
    return provider in ("ollama", "ollama-cloud")


def has_provider_options(provider: str) -> bool:
    return provider in ("anthropic", "codex", "vllm", "lm-studio", "nvidia-hosted", "self-hosted-nim", "ollama", "ollama-cloud", "deepseek")


def ollama_ctx_text(pcfg: dict) -> str:
    value = pcfg.get("num_ctx", "auto")
    if str(value).lower() == "auto":
        return f"auto {pcfg.get('num_ctx_min', 32768)}-{pcfg.get('num_ctx_max', 131072)}"
    return str(value)


def ollama_options_summary(pcfg: dict) -> str:
    parts = [
        f"ctx {ollama_ctx_text(pcfg)}",
        f"keep {pcfg.get('keep_alive', 'default')}",
        f"think {str(bool(pcfg.get('think', False))).lower()}",
        f"timeout {pcfg.get('request_timeout_ms', 'default')}ms",
        f"rpm {pcfg.get('rate_limit_rpm', 40)}",
        f"stream {'on' if bool(pcfg.get('stream_enabled', True)) else 'off'}",
    ]
    if bool(pcfg.get("rate_limit_status", True)):
        parts.append("rpm_status on")
    if bool(pcfg.get("stream_word_chunking", False)):
        parts.append("word_chunk on")
    opts = pcfg.get("ollama_options") or {}
    if isinstance(opts, dict) and opts:
        extra = ", ".join(f"{k}={v}" for k, v in sorted(opts.items())[:3])
        parts.append(extra)
    return "; ".join(parts)


def provider_options_summary(provider: str, pcfg: dict) -> str:
    timeout = pcfg.get("request_timeout_ms", "default")
    timeout_text = f"{timeout}ms" if timeout != "default" else "default"
    parts = [
        f"max {pcfg.get('max_output_tokens', 'default')}",
        f"timeout {timeout_text}",
    ]
    if provider in ("lm-studio", "nvidia-hosted", "self-hosted-nim", "ollama", "ollama-cloud"):
        parts.append(f"rpm {pcfg.get('rate_limit_rpm', 40)}")
        if bool(pcfg.get("rate_limit_status", True)):
            parts.append("rpm_status on")
    if provider in ("vllm", "lm-studio", "self-hosted-nim", "deepseek"):
        parts.insert(0, f"ctx {pcfg.get('context_window', 'default')}")
        parts.insert(1, f"reserve {pcfg.get('context_reserve_tokens', 'default')}")
        parts.append(f"native {str(bool(pcfg.get('native_compat', True))).lower()}")
    if provider in ("vllm", "lm-studio", "nvidia-hosted", "self-hosted-nim", "deepseek"):
        parts.append(f"stream {'on' if bool(pcfg.get('stream_enabled', True)) else 'off'}")
        if bool(pcfg.get("stream_word_chunking", False)):
            parts.append("word_chunk on")
    return "; ".join(parts)


def provider_menu_label(provider: str, pcfg: dict) -> str:
    if provider == "anthropic":
        return "Anthropic routed" if bool(pcfg.get("route_through_router", False)) else "Claude Native"
    if provider == "codex":
        return "Codex routed" if bool(pcfg.get("route_through_router", False)) else "Codex Native"
    return provider


def main_items() -> list[tuple[str, str]]:
    provider, pcfg = current_provider_cfg()
    lang = current_language()
    model = "Codex default" if provider == "codex" and not pcfg.get("current_model") else pcfg.get("current_model", "unset")
    advisor_model = "Claude Code native /advisor" if provider == "anthropic" else ("Codex native" if provider == "codex" else (pcfg.get("advisor_model") or "off"))
    base = pcfg.get("base_url", "unset")
    rows: list[tuple[str, str]] = []

    def add(key: str, label: str) -> None:
        rows.append((key, f"{len(rows)}. {label}"))

    add("language", f"{t('language')}  [{LANGUAGES.get(lang, lang)}]")
    add("provider", f"{t('provider')}  [{provider_menu_label(provider, pcfg)}]")
    add("api-key", t("api_key"))
    add("base-url", f"{t('base_url')}  [{base}]")
    add("model", f"{t('model')}  [{model}]")
    add("advisor-model", f"{t('advisor_model')}  [{advisor_model}]")
    if is_ollama_provider(provider):
        add("ollama-options", f"{t('ollama_options')}  [{ollama_options_summary(pcfg)}]")
    if has_provider_options(provider):
        add("provider-options", f"{t('provider_options')}  [{provider_options_summary(provider, pcfg)}]")
    add("test", t("test"))
    launch_label = t("launch")
    if not claude_launch_enabled(provider):
        launch_label += " [disabled: Codex provider selected]"
    add("launch", launch_label)
    launch_codex_label = t("launch_codex")
    if not codex_launch_enabled(provider):
        launch_codex_label += " [disabled: select Codex provider]"
    add("launch-codex", launch_codex_label)
    rows.append(("quit", t("quit")))
    return rows


def settings_ready_except_api_key() -> bool:
    provider, pcfg = current_provider_cfg()
    if provider == "codex":
        return True
    base = pcfg.get("base_url", "")
    model = pcfg.get("current_model", "")
    return bool(provider and base and model and "your-" not in base)

def default_base_url(provider: str) -> str:
    return {
        "anthropic": "https://api.anthropic.com",
        "codex": "https://api.openai.com",
        "ollama": "http://your-ollama:11434",
        "ollama-cloud": "https://ollama.com",
        "deepseek": "https://api.deepseek.com/anthropic",
        "vllm": "http://your-vllm:8000",
        "lm-studio": "http://127.0.0.1:1234/v1",
        "nvidia-hosted": "https://integrate.api.nvidia.com/v1",
        "self-hosted-nim": "http://your-nim:8000",
    }.get(provider, "http://localhost:8000")


def help_for_action(action: str, sub_kind: str | None = None) -> str:
    if sub_kind == "language":
        return t("select_language")
    if sub_kind == "provider":
        return t("select_provider")
    if sub_kind == "model":
        return t("select_model")
    if sub_kind == "advisor-model":
        return t("select_advisor_model")
    if sub_kind == "ollama-options":
        return t("select_ollama_options")
    if sub_kind == "provider-options":
        return t("select_provider_options")
    if sub_kind == "test-result":
        return t("test_result")
    return {
        "launch": t("help_launch"),
        "launch-codex": t("help_launch_codex"),
        "test": t("help_test"),
        "language": t("help_language"),
        "provider": t("help_provider"),
        "model": t("help_model"),
        "advisor-model": t("help_advisor_model"),
        "ollama-options": t("help_ollama_options"),
        "provider-options": t("help_provider_options"),
        "api-key": t("help_api_key"),
        "base-url": t("help_base_url"),
        "quit": t("help_quit"),
    }.get(action, "Enter selects this action.")


def get_models_for_current_provider() -> tuple[list[tuple[str, str]], str]:
    code, out = run_cmd([CTL, "models"])
    models: list[tuple[str, str]] = []
    for line in out.splitlines()[1:]:
        if "\t" not in line:
            continue
        alias, upstream = line.split("\t", 1)
        if alias.strip() and upstream.strip():
            models.append((upstream.strip(), alias.strip()))
    return models, out


def build_provider_submenu() -> dict:
    cfg = load_cfg()
    current = provider_choice_for_cfg(cfg)
    items = []
    idx = 0
    for i, (key, label) in enumerate(PROVIDERS):
        if key == current:
            idx = i
        provider = provider_from_choice(key)
        base = cfg.get("providers", {}).get(provider, {}).get("base_url", "")
        items.append({"value": key, "label": f"{label:<16} {key:<17} {base}", "current": key == current})
    return {"kind": "provider", "parent": "provider", "items": items, "idx": idx, "offset": 0}


def build_language_submenu() -> dict:
    current = current_language()
    items = []
    idx = 0
    for i, (code, label) in enumerate(LANGUAGES.items()):
        if code == current:
            idx = i
        items.append({"value": code, "label": f"{code:<2} {label}", "current": code == current})
    return {"kind": "language", "parent": "language", "items": items, "idx": idx, "offset": 0}


def build_api_key_submenu() -> dict:
    current = current_provider()
    items = []
    idx = 0
    for i, (key, label) in enumerate(PROVIDERS):
        provider = provider_from_choice(key)
        if provider == current:
            idx = i
        items.append({"value": provider, "label": f"{label:<16} {provider:<15}", "current": provider == current})
    return {"kind": "api-key", "parent": "api-key", "items": items, "idx": idx, "offset": 0}

def build_model_submenu() -> tuple[dict | None, list[str]]:
    models, raw = get_models_for_current_provider()
    if not models:
        lines = raw.strip().splitlines() or ["No models found. Use custom input."]
        return None, lines[:2]
    provider, pcfg = current_provider_cfg()
    current = pcfg.get("current_model", "")
    items = []
    idx = 0
    for i, (upstream, alias) in enumerate(models):
        is_current = upstream == current or alias == current
        if is_current:
            idx = i
        badge = compatibility_badge(provider, upstream, alias)
        description = ""
        known = KNOWN_NVIDIA_MODEL_STATUS.get(upstream) or KNOWN_NVIDIA_MODEL_STATUS.get(alias)
        entry = compatibility_entry(provider, upstream, alias)
        if known:
            description = known[1]
        elif entry:
            state = "OK" if entry.get("ok") else "failed"
            detail = entry.get("diagnosis") or entry.get("message") or ""
            description = f"Last compatibility test: {state} ({human_age(cache_age_seconds(entry))}). {detail}".strip()
        items.append({
            "value": upstream,
            "label": f"{badge:<11} {upstream:<58} {alias}",
            "current": is_current,
            "description": description,
        })
    items.append({"value": "__custom__", "label": "Custom model id...", "current": False})
    return {"kind": "model", "parent": "model", "items": items, "idx": idx, "offset": 0}, []


def build_advisor_model_submenu() -> dict:
    provider, pcfg = current_provider_cfg()
    if provider == "anthropic":
        items = [{
            "value": "",
            "label": "Claude Code native /advisor",
            "current": True,
            "description": "Anthropic modes use Claude Code's built-in /advisor; run /advisor in the session to pick its model.",
        }]
        return {"kind": "advisor-model", "parent": "advisor-model", "items": items, "idx": 0, "offset": 0}
    current = pcfg.get("advisor_model") or ""
    values: list[str] = []
    for mid in DEFAULT_ADVISOR_MODELS + [upstream for upstream, _ in get_models_for_current_provider()[0]]:
        if mid and mid not in values:
            values.append(mid)
    items = [{"value": "", "label": "Disable Advisor Model", "current": not current, "description": "Disable ciel-runtime advisor routing."}]
    idx = 0
    for i, mid in enumerate(values, 1):
        is_current = mid == current
        if is_current:
            idx = i
        desc = "Recommended long-context advisor model." if mid == "deepseek-v4-pro" else ""
        items.append({"value": mid, "label": mid, "current": is_current, "description": desc})
    items.append({"value": "__custom__", "label": "Custom advisor model id...", "current": False})
    return {"kind": "advisor-model", "parent": "advisor-model", "items": items, "idx": idx, "offset": 0}


OLLAMA_OPTION_DESCRIPTIONS = {
    "__edit_num_ctx__": {
        "en": "Edit Ollama num_ctx. This is the context window sent to Ollama; it cannot exceed the server/model limit.",
        "ko": "Ollama num_ctx를 수정합니다. 한 번에 볼 컨텍스트 창이며 서버/모델 한계를 넘게 설정해도 실제 한계는 늘지 않습니다.",
        "ja": "Ollamaのnum_ctxを編集します。Ollamaへ送るコンテキスト幅で、サーバー/モデル上限は超えられません。",
        "zh": "编辑 Ollama num_ctx。这是发送给 Ollama 的上下文窗口，不能超过服务器/模型上限。",
    },
    "__edit_min__": {
        "en": "Edit the minimum context used when num_ctx is auto. Small requests will not go below this value.",
        "ko": "num_ctx=auto일 때 사용할 최소 컨텍스트입니다. 작은 요청도 이 값보다 작게 내려가지 않습니다.",
        "ja": "num_ctx=auto時の最小コンテキストです。小さな要求でもこの値未満にはなりません。",
        "zh": "编辑 num_ctx=auto 时的最小上下文。小请求也不会低于此值。",
    },
    "__edit_max__": {
        "en": "Edit the maximum context used when num_ctx is auto. Keep it at or below the real server context limit.",
        "ko": "num_ctx=auto일 때 사용할 최대 컨텍스트입니다. 실제 서버 컨텍스트 한계 이하로 두는 것이 맞습니다.",
        "ja": "num_ctx=auto時の最大コンテキストです。実際のサーバー上限以下にしてください。",
        "zh": "编辑 num_ctx=auto 时的最大上下文。应不高于真实服务器上下文上限。",
    },
    "__edit_keep_alive__": {
        "en": "Edit how long Ollama keeps the model loaded after a request. Longer values reduce reloads but hold memory.",
        "ko": "요청 후 Ollama가 모델을 메모리에 유지하는 시간입니다. 길수록 재로딩은 줄지만 메모리를 더 오래 잡습니다.",
        "ja": "要求後にOllamaがモデルを保持する時間です。長いほど再読み込みは減りますがメモリを保持します。",
        "zh": "编辑请求后 Ollama 保持模型加载的时间。更长可减少重载，但会占用内存。",
    },
    "__edit_temperature__": {
        "en": "Edit sampling temperature. Higher is more varied; lower is more deterministic.",
        "ko": "샘플링 temperature입니다. 높을수록 답변이 다양해지고, 낮을수록 결정적으로 동작합니다.",
        "ja": "サンプリングtemperatureです。高いほど多様、低いほど決定的になります。",
        "zh": "编辑采样 temperature。越高越多样，越低越确定。",
    },
    "__edit_top_p__": {
        "en": "Edit nucleus sampling top_p. Lower values restrict token choices; 0.8 is a moderate default.",
        "ko": "누적 확률 top_p입니다. 낮을수록 후보 토큰을 좁히며, 0.8은 중간 정도의 기본값입니다.",
        "ja": "nucleus samplingのtop_pです。低いほど候補を絞り、0.8は中程度の既定値です。",
        "zh": "编辑 nucleus sampling top_p。越低候选越窄；0.8 是中等默认值。",
    },
    "__edit_max_tokens__": {
        "en": "Edit max output tokens (Ollama num_predict). Input plus reserved output must fit in the context window.",
        "ko": "최대 출력 토큰(Ollama num_predict)입니다. 입력과 예약 출력이 컨텍스트 창 안에 같이 들어가야 합니다.",
        "ja": "最大出力トークン(Ollama num_predict)です。入力と予約出力は同じコンテキスト内に収まる必要があります。",
        "zh": "编辑最大输出 token（Ollama num_predict）。输入加预留输出必须放进上下文窗口。",
    },
    "__edit_timeout__": {
        "en": "Edit upstream wait timeout in milliseconds. 300000 means 5 minutes.",
        "ko": "업스트림 응답 대기 시간(ms)입니다. 300000은 5분입니다.",
        "ja": "上流応答待ちタイムアウト(ms)です。300000は5分です。",
        "zh": "编辑上游响应等待超时（毫秒）。300000 表示 5 分钟。",
    },
    "__custom__": {
        "en": "Enter any Ollama option as KEY=VALUE, or unset:KEY to remove it.",
        "ko": "임의의 Ollama 옵션을 KEY=VALUE로 입력합니다. 삭제하려면 unset:KEY를 입력합니다.",
        "ja": "任意のOllamaオプションをKEY=VALUEで入力します。削除はunset:KEYです。",
        "zh": "用 KEY=VALUE 输入任意 Ollama 选项；用 unset:KEY 删除。",
    },
}


def ollama_option_description(value: str) -> str:
    lang = current_language()
    if value in OLLAMA_OPTION_DESCRIPTIONS:
        entry = OLLAMA_OPTION_DESCRIPTIONS[value]
        return entry.get(lang, entry["en"])
    if value.startswith("num_ctx=auto"):
        return {
            "en": "Use automatic context sizing based on request size, bounded by the configured min/max.",
            "ko": "요청 크기에 따라 컨텍스트를 자동 선택합니다. 설정된 최소/최대 범위 안에서만 움직입니다.",
            "ja": "要求サイズに応じてコンテキストを自動選択します。設定した最小/最大範囲内です。",
            "zh": "根据请求大小自动选择上下文，并限制在设置的最小/最大范围内。",
        }.get(lang, "Use automatic context sizing based on request size, bounded by the configured min/max.")
    if value.startswith("num_ctx="):
        return {
            "en": "Use a fixed context window for every Ollama request. Larger values use more memory and may be slower.",
            "ko": "모든 Ollama 요청에 고정 컨텍스트를 사용합니다. 값이 클수록 메모리를 더 쓰고 느려질 수 있습니다.",
            "ja": "全てのOllama要求で固定コンテキストを使います。大きいほどメモリ使用量と遅延が増えます。",
            "zh": "为每个 Ollama 请求使用固定上下文。值越大内存占用越高，也可能更慢。",
        }.get(lang, "Use a fixed context window for every Ollama request.")
    if value.startswith("min="):
        return {
            "en": "Set the lower bound for automatic num_ctx selection.",
            "ko": "자동 num_ctx 선택의 하한값을 설정합니다.",
            "ja": "自動num_ctx選択の下限を設定します。",
            "zh": "设置自动 num_ctx 选择的下限。",
        }.get(lang, "Set the lower bound for automatic num_ctx selection.")
    if value.startswith("max="):
        return {
            "en": "Set the upper bound for automatic num_ctx selection.",
            "ko": "자동 num_ctx 선택의 상한값을 설정합니다.",
            "ja": "自動num_ctx選択の上限を設定します。",
            "zh": "设置自动 num_ctx 选择的上限。",
        }.get(lang, "Set the upper bound for automatic num_ctx selection.")
    if value.startswith("keep_alive="):
        return OLLAMA_OPTION_DESCRIPTIONS["__edit_keep_alive__"].get(lang, OLLAMA_OPTION_DESCRIPTIONS["__edit_keep_alive__"]["en"])
    if value.startswith("think="):
        return {
            "en": "Toggle Ollama thinking output support. Claude Code may not display provider-specific thinking cleanly.",
            "ko": "Ollama thinking 출력 요청 여부입니다. Claude Code가 provider별 thinking을 항상 깔끔하게 표시하지는 않습니다.",
            "ja": "Ollama thinking出力の要求を切り替えます。Claude Code側で常に綺麗に表示されるとは限りません。",
            "zh": "切换 Ollama thinking 输出请求。Claude Code 不一定能完整显示各提供商的 thinking。",
        }.get(lang, "Toggle Ollama thinking output support.")
    if value.startswith("stream="):
        return {
            "en": "Toggle streaming. When off, the router waits for the full upstream response before sending it to Claude Code. Use this when streaming fragmentation causes tool-call or JSON parse errors.",
            "ko": "스트리밍을 켜고/끕니다. off 면 업스트림 응답이 전부 모일 때까지 기다렸다가 Claude Code에 한 번에 보냅니다. 스트리밍 단편화로 tool-call/JSON 파싱이 실패할 때 사용합니다.",
            "ja": "ストリーミングを切り替えます。offにすると、ルーターは上流応答が揃ってからClaude Codeへ一括送信します。ストリーミング断片化でtool-call/JSON解析が失敗する時に使用します。",
            "zh": "切换流式输出。off 时路由器会等待上游完整响应再发送给 Claude Code。流式分片导致 tool-call/JSON 解析失败时使用。",
        }.get(lang, "Toggle streaming. When off, the router waits for the full upstream response.")
    if value.startswith("stream_word_chunking="):
        return {
            "en": "Buffer text tokens until a whitespace/word boundary before sending the SSE delta. Reduces SSE event volume and can mitigate tool/JSON fragmentation issues. Tool call inputs are not affected.",
            "ko": "토큰을 공백 단위(단어 경계)까지 버퍼링해서 SSE delta로 전송합니다. SSE 이벤트 빈도를 줄이고 tool/JSON 단편화 문제를 완화합니다. tool call 입력은 영향을 받지 않습니다.",
            "ja": "テキストトークンを空白/単語境界までバッファしてSSE deltaを送信します。SSEイベント量を減らし、tool/JSON断片化を緩和できます。tool call入力には影響しません。",
            "zh": "在空白/单词边界处批量发送 SSE 文本 delta。降低 SSE 事件频率并缓解 tool/JSON 分片问题。工具调用输入不受影响。",
        }.get(lang, "Buffer text tokens until a word boundary before sending the SSE delta.")
    if value.startswith("temperature="):
        return OLLAMA_OPTION_DESCRIPTIONS["__edit_temperature__"].get(lang, OLLAMA_OPTION_DESCRIPTIONS["__edit_temperature__"]["en"])
    if value.startswith("top_p="):
        return OLLAMA_OPTION_DESCRIPTIONS["__edit_top_p__"].get(lang, OLLAMA_OPTION_DESCRIPTIONS["__edit_top_p__"]["en"])
    if value.startswith(("max_tokens=", "num_predict=")):
        return OLLAMA_OPTION_DESCRIPTIONS["__edit_max_tokens__"].get(lang, OLLAMA_OPTION_DESCRIPTIONS["__edit_max_tokens__"]["en"])
    if value.startswith("timeout="):
        return OLLAMA_OPTION_DESCRIPTIONS["__edit_timeout__"].get(lang, OLLAMA_OPTION_DESCRIPTIONS["__edit_timeout__"]["en"])
    return OLLAMA_OPTION_DESCRIPTIONS["__custom__"].get(lang, OLLAMA_OPTION_DESCRIPTIONS["__custom__"]["en"])


def build_ollama_options_submenu() -> dict:
    provider, pcfg = current_provider_cfg()
    ctx = pcfg.get("num_ctx", "auto")
    keep = str(pcfg.get("keep_alive", "5m"))
    think = bool(pcfg.get("think", False))
    stream_on = bool(pcfg.get("stream_enabled", True))
    word_chunk_on = bool(pcfg.get("stream_word_chunking", False))
    options = pcfg.get("ollama_options") or {}
    if not isinstance(options, dict):
        options = {}
    choices = [
        ("__edit_num_ctx__", f"Edit num_ctx [{ollama_ctx_text(pcfg)}]", False),
        ("__edit_min__", f"Edit auto minimum [{pcfg.get('num_ctx_min', 32768)}]", False),
        ("__edit_max__", f"Edit auto maximum [{pcfg.get('num_ctx_max', 131072)}]", False),
        ("__edit_keep_alive__", f"Edit keep_alive [{keep}]", False),
        ("__edit_temperature__", f"Edit temperature [{options.get('temperature', 'unset')}]", False),
        ("__edit_top_p__", f"Edit top_p [{options.get('top_p', 'unset')}]", False),
        ("__edit_max_tokens__", f"Edit max_tokens/num_predict [{options.get('num_predict', 'unset')}]", False),
        ("__edit_timeout__", f"Edit timeout ms [{pcfg.get('request_timeout_ms', 'default')}]", False),
        ("__custom__", "Custom KEY=VALUE or unset:KEY...", False),
        ("num_ctx=auto", f"num_ctx auto ({pcfg.get('num_ctx_min', 32768)}-{pcfg.get('num_ctx_max', 131072)})", str(ctx).lower() == "auto"),
        ("num_ctx=32768", "num_ctx 32768", ctx == 32768),
        ("num_ctx=65536", "num_ctx 65536", ctx == 65536),
        ("num_ctx=131072", "num_ctx 131072", ctx == 131072),
        ("min=32768", "auto minimum 32768", pcfg.get("num_ctx_min", 32768) == 32768),
        ("max=131072", "auto maximum 131072", pcfg.get("num_ctx_max", 131072) == 131072),
        ("keep_alive=5m", "keep_alive 5m", keep == "5m"),
        ("keep_alive=30m", "keep_alive 30m", keep == "30m"),
        ("think=false", "think false", not think),
        ("think=true", "think true", think),
        ("stream=true", "stream on", stream_on),
        ("stream=false", "stream off (buffer full response)", not stream_on),
        ("stream_word_chunking=true", "stream_word_chunking on (flush at word boundary)", word_chunk_on),
        ("stream_word_chunking=false", "stream_word_chunking off (token-by-token)", not word_chunk_on),
        ("temperature=0.7", f"temperature 0.7 (current {options.get('temperature', 'unset')})", options.get("temperature") == 0.7),
        ("top_p=0.8", f"top_p 0.8 (current {options.get('top_p', 'unset')})", options.get("top_p") == 0.8),
        ("max_tokens=4096", f"max_tokens 4096 (current {options.get('num_predict', 'unset')})", options.get("num_predict") == 4096),
        ("timeout=300000", f"timeout 300000ms (current {pcfg.get('request_timeout_ms', 'default')})", pcfg.get("request_timeout_ms") == 300000),
    ]
    items = [
        {"value": value, "label": label, "current": current, "description": ollama_option_description(value)}
        for value, label, current in choices
    ]
    return {"kind": "ollama-options", "parent": "ollama-options", "items": items, "idx": 0, "offset": 0}


PROVIDER_OPTION_DESCRIPTIONS = {
    "__edit_context_window__": {
        "en": "Edit the context window value used by ciel-runtime tests and router caps. Native mode cannot raise the real server limit.",
        "ko": "ciel-runtime 테스트와 라우터 제한 계산에 쓰는 컨텍스트 값입니다. native 모드에서는 실제 서버 한계를 늘리지 못합니다.",
        "ja": "ciel-runtimeのテストとルーター制限計算に使うコンテキスト値です。nativeモードでは実サーバー上限は増やせません。",
        "zh": "编辑 ciel-runtime 测试和路由器限制计算使用的上下文值。native 模式不能提高真实服务器上限。",
    },
    "__edit_reserve__": {
        "en": "Reserve input-side room when ciel-runtime router caps max_tokens. This is ignored by direct native Claude Code requests.",
        "ko": "ciel-runtime 라우터가 max_tokens를 줄일 때 입력 쪽 여유로 남기는 토큰입니다. direct native 요청에는 적용되지 않습니다.",
        "ja": "ciel-runtimeルーターがmax_tokensを制限する時に入力側へ残す余裕です。direct native要求では無視されます。",
        "zh": "ciel-runtime 路由器限制 max_tokens 时预留给输入侧的空间。direct native 请求会忽略它。",
    },
    "__edit_max_output__": {
        "en": "Set Claude Code's CLAUDE_CODE_MAX_OUTPUT_TOKENS and the ciel-runtime router cap. 4096 is the default.",
        "ko": "Claude Code의 CLAUDE_CODE_MAX_OUTPUT_TOKENS와 ciel-runtime 라우터 출력 제한입니다. 기본값은 4096입니다.",
        "ja": "Claude CodeのCLAUDE_CODE_MAX_OUTPUT_TOKENSとciel-runtimeルーターの出力制限です。既定値は4096です。",
        "zh": "设置 Claude Code 的 CLAUDE_CODE_MAX_OUTPUT_TOKENS 和 ciel-runtime 路由器输出上限。默认 4096。",
    },
    "__edit_timeout__": {
        "en": "Edit ciel-runtime compatibility-test/router upstream timeout in milliseconds. Claude Code native networking has its own timeout behavior.",
        "ko": "ciel-runtime 호환성 테스트/라우터의 업스트림 대기 시간(ms)입니다. Claude Code native 네트워크 대기는 자체 동작을 따릅니다.",
        "ja": "ciel-runtime互換性テスト/ルーターの上流タイムアウト(ms)です。Claude Code native通信は独自の挙動です。",
        "zh": "编辑 ciel-runtime 兼容性测试/路由器上游超时（毫秒）。Claude Code native 网络有自身超时行为。",
    },
    "__edit_native__": {
        "en": "Toggle direct Anthropic Messages compatibility. Use it for LM Studio, vLLM, or self-hosted NIM servers that implement /v1/messages.",
        "ko": "Anthropic Messages 호환 엔드포인트에 직접 연결할지 정합니다. /v1/messages를 구현한 LM Studio, vLLM, self-hosted NIM에서 사용합니다.",
        "ja": "Anthropic Messages互換エンドポイントへ直接接続するかを切り替えます。/v1/messages対応のLM Studio、vLLM、self-hosted NIMで使います。",
        "zh": "切换是否直接连接 Anthropic Messages 兼容端点。用于实现 /v1/messages 的 LM Studio、vLLM 或 self-hosted NIM。",
    },
    "__custom__": {
        "en": "Enter provider option as KEY=VALUE, or unset:KEY to remove it.",
        "ko": "프로바이더 옵션을 KEY=VALUE로 입력합니다. 삭제하려면 unset:KEY를 입력합니다.",
        "ja": "プロバイダーオプションをKEY=VALUEで入力します。削除はunset:KEYです。",
        "zh": "用 KEY=VALUE 输入提供商选项；用 unset:KEY 删除。",
    },
}


def provider_option_description(value: str) -> str:
    lang = current_language()
    if value in PROVIDER_OPTION_DESCRIPTIONS:
        entry = PROVIDER_OPTION_DESCRIPTIONS[value]
        return entry.get(lang, entry["en"])
    if value.startswith("context_window="):
        return PROVIDER_OPTION_DESCRIPTIONS["__edit_context_window__"].get(lang, PROVIDER_OPTION_DESCRIPTIONS["__edit_context_window__"]["en"])
    if value.startswith("context_reserve_tokens="):
        return PROVIDER_OPTION_DESCRIPTIONS["__edit_reserve__"].get(lang, PROVIDER_OPTION_DESCRIPTIONS["__edit_reserve__"]["en"])
    if value.startswith("max_output_tokens="):
        return PROVIDER_OPTION_DESCRIPTIONS["__edit_max_output__"].get(lang, PROVIDER_OPTION_DESCRIPTIONS["__edit_max_output__"]["en"])
    if value.startswith(("timeout=", "request_timeout_ms=")):
        return PROVIDER_OPTION_DESCRIPTIONS["__edit_timeout__"].get(lang, PROVIDER_OPTION_DESCRIPTIONS["__edit_timeout__"]["en"])
    if value.startswith(("rate_limit=", "rate_limit_rpm=", "rpm=")) or value == "__edit_rate_limit__":
        return {
            "en": "Router-side upstream requests per minute. NIM hosted defaults to 40 RPM; 0 disables waiting.",
            "ko": "라우터가 업스트림 요청 수를 분당 제한합니다. NIM hosted 기본값은 40 RPM이고, 0이면 대기하지 않습니다.",
            "ja": "ルーター側の上流リクエスト数/分。NIM hosted は既定 40 RPM、0 で待機なし。",
            "zh": "路由器侧上游每分钟请求限制。NIM hosted 默认 40 RPM；0 表示不等待。",
        }.get(lang, "Router-side upstream requests per minute.")
    if value.startswith(("rate_limit_status=", "rpm_status=")):
        return {
            "en": "Show optional colored RPM usage status in Claude responses.",
            "ko": "Claude 응답에 RPM 사용량 상태를 색상 텍스트로 표시합니다.",
            "ja": "Claude応答にRPM使用量状態を色付きテキストで表示します。",
            "zh": "在 Claude 响应中显示彩色 RPM 使用量状态。",
        }.get(lang, "Show optional colored RPM usage status.")
    if value.startswith(("native=", "native_compat=")):
        return PROVIDER_OPTION_DESCRIPTIONS["__edit_native__"].get(lang, PROVIDER_OPTION_DESCRIPTIONS["__edit_native__"]["en"])
    if value.startswith("stream="):
        return {
            "en": "Toggle streaming. When off, the router forces stream:false upstream and returns the full response to Claude Code. Use this if streaming fragmentation causes tool-call or JSON parse errors.",
            "ko": "스트리밍 on/off. off 면 업스트림에 stream:false 를 강제하고 응답 전체를 Claude Code에 보냅니다. 스트리밍 단편화로 tool-call/JSON 파싱이 실패할 때 사용합니다.",
            "ja": "ストリーミングを切り替えます。offにすると上流にstream:falseを強制し、応答全体をClaude Codeへ返します。ストリーミング断片化でtool-call/JSONが失敗する時に使います。",
            "zh": "切换流式输出。off 时强制对上游设置 stream:false 并返回完整响应给 Claude Code。流式分片导致 tool-call/JSON 解析失败时使用。",
        }.get(lang, "Toggle streaming. When off, the router forces stream:false upstream and returns the full response.")
    if value.startswith("stream_word_chunking="):
        return {
            "en": "Parse upstream Anthropic SSE and re-emit text_delta events buffered to word boundaries. Reduces SSE event volume; tool deltas and non-text events pass through unchanged.",
            "ko": "업스트림 Anthropic SSE를 파싱해서 text_delta 를 단어 경계 단위로 모아서 다시 전송합니다. SSE 이벤트 빈도를 낮춥니다. tool delta와 텍스트가 아닌 이벤트는 그대로 통과합니다.",
            "ja": "上流のAnthropic SSEを解析し、text_deltaを単語境界でまとめて再送します。SSEイベント量を削減します。tool deltaやテキスト以外のイベントはそのまま透過します。",
            "zh": "解析上游 Anthropic SSE 并将 text_delta 在单词边界处合并后重新发送。降低 SSE 事件频率。工具 delta 与非文本事件原样透传。",
        }.get(lang, "Buffer text_delta events at word boundaries; tool deltas pass through unchanged.")
    return PROVIDER_OPTION_DESCRIPTIONS["__custom__"].get(lang, PROVIDER_OPTION_DESCRIPTIONS["__custom__"]["en"])


def build_provider_options_submenu() -> dict:
    provider, pcfg = current_provider_cfg()
    max_output = pcfg.get("max_output_tokens", "4096")
    timeout = pcfg.get("request_timeout_ms", "300000")
    stream_on = bool(pcfg.get("stream_enabled", True))
    word_chunk_on = bool(pcfg.get("stream_word_chunking", False))
    choices = [
        ("__edit_max_output__", f"Edit max_output_tokens [{max_output}]", False),
        ("__edit_timeout__", f"Edit timeout ms [{timeout}]", False),
    ]
    if provider in ("anthropic", "codex"):
        routed = bool(pcfg.get("route_through_router", False))
        choices.insert(0, ("route_through_router=true", "routed through ciel-runtime router", routed))
        choices.insert(1, ("route_through_router=false", "native direct mode", not routed))
    if provider in ("lm-studio", "nvidia-hosted", "self-hosted-nim", "ollama", "ollama-cloud"):
        choices.append(("__edit_rate_limit__", f"Edit rate_limit_rpm [{pcfg.get('rate_limit_rpm', 40)}]", False))
        choices.append(("rate_limit_status=true", "rate_limit_status on", bool(pcfg.get("rate_limit_status", True))))
        choices.append(("rate_limit_status=false", "rate_limit_status off", not bool(pcfg.get("rate_limit_status", True))))
    if provider in ("vllm", "lm-studio", "self-hosted-nim", "deepseek"):
        native = bool(pcfg.get("native_compat", True))
        choices = [
            ("__edit_context_window__", f"Edit context_window [{pcfg.get('context_window', 'default')}]", False),
            ("__edit_reserve__", f"Edit context reserve [{pcfg.get('context_reserve_tokens', 'default')}]", False),
            *choices,
        ]
        choices.append(("__edit_native__", f"Edit native mode [{str(native).lower()}]", False))
    choices.extend([
        ("__custom__", "Custom KEY=VALUE or unset:KEY...", False),
        ("max_output_tokens=4096", f"max_output_tokens 4096 (current {max_output})", str(max_output) == "4096"),
        ("max_output_tokens=8192", f"max_output_tokens 8192 (current {max_output})", str(max_output) == "8192"),
        ("timeout=300000", f"timeout 300000ms (current {timeout})", str(timeout) == "300000"),
    ])
    if provider in ("vllm", "lm-studio", "nvidia-hosted", "self-hosted-nim", "deepseek"):
        choices.extend([
            ("stream=true", "stream on", stream_on),
            ("stream=false", "stream off (buffer full response)", not stream_on),
            ("stream_word_chunking=true", "stream_word_chunking on (flush at word boundary)", word_chunk_on),
            ("stream_word_chunking=false", "stream_word_chunking off (raw upstream SSE)", not word_chunk_on),
        ])
    if provider in ("vllm", "lm-studio", "self-hosted-nim", "deepseek"):
        choices.extend([
            ("context_window=32768", f"context_window 32768 (current {pcfg.get('context_window', 'default')})", pcfg.get("context_window") == 32768),
            ("context_window=65536", f"context_window 65536 (current {pcfg.get('context_window', 'default')})", pcfg.get("context_window") == 65536),
            ("context_window=1048576", f"context_window 1048576 (current {pcfg.get('context_window', 'default')})", pcfg.get("context_window") == 1048576),
        ])
        choices.extend([
            ("native=true", "native true", bool(pcfg.get("native_compat", True))),
            ("native=false", "native false", not bool(pcfg.get("native_compat", True))),
        ])
    items = [
        {"value": value, "label": label, "current": current, "description": provider_option_description(value)}
        for value, label, current in choices
    ]
    return {"kind": "provider-options", "parent": "provider-options", "items": items, "idx": 0, "offset": 0}


def after_model_action() -> str:
    provider = current_provider()
    if is_ollama_provider(provider):
        return "ollama-options"
    if has_provider_options(provider):
        return "provider-options"
    return "test"


def summarize_test_output(code: int, out: str) -> list[str]:
    raw = out.strip().splitlines()
    if not raw:
        return ["Compatibility: FAIL" if code else "Compatibility: OK", "No output from compatibility test."]
    if any(line.startswith("Traceback ") for line in raw):
        reason = next((line.strip() for line in reversed(raw) if line.strip() and not line.lstrip().startswith("~")), "Internal test error")
        return ["Compatibility: FAIL", "Reason: internal ciel-runtime test error", reason[:160]]
    keep_prefixes = (
        "Testing provider:",
        "Test mode:",
        "Mode:",
        "URL:",
        "Claude API URL:",
        "Upstream base URL:",
        "Model:",
        "Compatibility:",
        "HTTP:",
        "Reason:",
        "Diagnosis:",
        "Stop reason:",
        "Content blocks:",
        "Tokens:",
        "Tool result text:",
        "Note:",
    )
    lines = [line for line in raw if line.startswith(keep_prefixes)]
    if not lines:
        lines = raw[:8]
    if code != 0 and not any(line.startswith("Compatibility:") for line in lines):
        lines.insert(0, "Compatibility: FAIL")
    return lines[:12]


def test_submenu(lines: list[str]) -> dict:
    return {
        "kind": "test-result",
        "parent": "test",
        "items": [{"value": "", "label": line, "current": False} for line in lines],
        "idx": 0,
        "offset": 0,
        "readonly": True,
    }


def run_test_with_animation(idx: int, checks: list[str]) -> tuple[int, str]:
    frames = ["|", "/", "-", "\\"]
    started = time.monotonic()
    test_timeout = 60
    hard_timeout = test_timeout + 15
    proc = subprocess.Popen(
        [CTL, "test", str(test_timeout), "auto"],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    frame = 0
    while proc.poll() is None:
        elapsed = int(time.monotonic() - started)
        notice = [f"{frames[frame % len(frames)]} {t('running_test')} ({elapsed}s/{test_timeout}s)"]
        render(None, idx, None, notice, checks)
        if elapsed >= hard_timeout:
            proc.terminate()
            try:
                out, _ = proc.communicate(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                out, _ = proc.communicate()
            timeout_msg = (
                f"Compatibility: FAIL\n"
                f"Reason: compatibility test exceeded {test_timeout}s and was stopped by the menu.\n"
                "Diagnosis: retry the test or choose a faster/more reliable model."
            )
            return 124, ((out or "").rstrip() + "\n" + timeout_msg).strip()
        frame += 1
        time.sleep(0.2)
    out, _ = proc.communicate()
    return proc.returncode or 0, out or ""


def inline_prompt(stdscr, prompt_text: str, row: int, default: str = "") -> str:
    h, w = _term_size()
    y = max(1, min(row, h - 3))
    style = _style(reverse=True)
    style_bold = _style(reverse=True, bold=True)
    _write(y, 0, " " * max(0, w - 1), style)
    _write(y, 2, prompt_text[: max(0, w - 4)], style_bold)
    x = min(len(prompt_text) + 2, max(0, w - 2))
    if default:
        _write(y, x, default[: max(0, w - x - 1)], style)
        _move(y, min(x + len(default), max(0, w - 2)))
    else:
        _move(y, x)
    sys.stdout.flush()
    chars = []
    while True:
        ch = read_menu_key()
        if ch == "KEY_ENTER":
            break
        if ch == "KEY_ESC":
            return default
        if ch == "KEY_BACKSPACE":
            if chars:
                chars.pop()
        elif len(ch) == 1 and ch.isprintable():
            chars.append(ch)
        _write(y, 0, " " * max(0, w - 1), style)
        _write(y, 2, prompt_text[: max(0, w - 4)], style_bold)
        _write(y, x, "".join(chars)[: max(0, w - x - 1)], style)
        _move(y, min(x + len(chars), max(0, w - 2)))
        sys.stdout.flush()
    return "".join(chars).strip() or default


def inline_secret_prompt(stdscr, prompt_text: str, row: int) -> str:
    h, w = _term_size()
    y = max(1, min(row, h - 3))
    style = _style(reverse=True)
    style_bold = _style(reverse=True, bold=True)
    _write(y, 0, " " * max(0, w - 1), style)
    _write(y, 2, prompt_text[: max(0, w - 4)], style_bold)
    x = min(len(prompt_text) + 2, max(0, w - 2))
    _move(y, x)
    sys.stdout.flush()
    chars = []
    while True:
        ch = read_menu_key()
        if ch == "KEY_ENTER":
            break
        if ch == "KEY_ESC":
            return ""
        if ch == "KEY_BACKSPACE":
            if chars:
                chars.pop()
        elif len(ch) == 1 and ch.isprintable():
            chars.append(ch)
        _write(y, 0, " " * max(0, w - 1), style)
        _write(y, 2, prompt_text[: max(0, w - 4)], style_bold)
        masked = "*" * len(chars)
        _write(y, x, masked[: max(0, w - x - 1)], style)
        _move(y, min(x + len(masked), max(0, w - 2)))
        sys.stdout.flush()
    return "".join(chars).strip()


def message(stdscr, title: str, lines: list[str]) -> None:
    _clear()
    h, w = _term_size()
    _write_safe(0, 0, title[: w - 1], _style(bold=True))
    for i, line in enumerate(lines[: h - 4]):
        _write_safe(2 + i, 0, line[: w - 1])
    _write_safe(h - 2, 0, "Press any key to continue", _style(dim=True) + cp(5))
    sys.stdout.flush()
    read_menu_key()


def api_key_flow(stdscr) -> list[str]:
    provider = current_provider()
    subprocess.run([CTL, "api-key", provider], check=False)
    input("Press Enter to return to ciel-runtime menu...")
    return [f"API key flow completed for {provider}"]


def visible_sub_window(sub: dict, max_rows: int) -> tuple[int, int]:
    count = len(sub["items"])
    idx = sub["idx"]
    offset = sub.get("offset", 0)
    if idx < offset:
        offset = idx
    if idx >= offset + max_rows:
        offset = idx - max_rows + 1
    offset = max(0, min(offset, max(0, count - max_rows)))
    sub["offset"] = offset
    return offset, min(count, offset + max_rows)


def selected_sub_description(sub: dict | None) -> str:
    if not sub:
        return ""
    try:
        item = sub["items"][sub["idx"]]
    except Exception:
        return ""
    return str(item.get("description") or "")


def index_for_action(action: str) -> int:
    items = main_items()
    return next((i for i, (key, _) in enumerate(items) if key == action), 0)


def default_launch_action() -> str:
    return "launch-codex" if codex_launch_enabled(current_provider()) else "launch"


def add(stdscr, y: int, x: int, text: str, style: str = "") -> None:
    _write_safe(y, max(0, x), text, style)


def draw_intro_panel(stdscr) -> int:
    h, w = _term_size()
    title = app_title()
    if h < 20:
        _write(0, 0, animated_text(title) + f" - {CREDITS}")
        return 1

    panel_w = max(40, w - 2)
    panel_h = 8 if h >= 24 else 7
    border = cp(4)
    add(stdscr, 0, 0, "+" + "-" * (panel_w - 2) + "+", border)
    _write(0, 4, " " + animated_text(title) + " ", border)
    for y in range(1, panel_h - 1):
        add(stdscr, y, 0, "|", border)
        add(stdscr, y, panel_w - 1, "|", border)
    add(stdscr, panel_h - 1, 0, "+" + "-" * (panel_w - 2) + "+", border)

    if w >= 92:
        split = min(44, panel_w // 2)
        for y in range(1, panel_h - 1):
            add(stdscr, y, split, "|", border)
        add(stdscr, 1, 8, "Welcome back!", _style(bold=True) + cp(5))
        _write(3, 9, animated_text("CLAUDE"))
        _write(4, 12, animated_text("ANY", phase=int(time.monotonic() * 8) + 4))
        add(stdscr, 6, 6, CREDITS, _style(bold=True) + cp(5))

        right = split + 3
        add(stdscr, 1, right, "Tips for getting started", _style(bold=True) + cp(4))
        add(stdscr, 2, right, "Choose provider, model, base URL, and API key before launch.", cp(5))
        add(stdscr, 3, right, "Routes Claude Code to Anthropic, Ollama, LM Studio, vLLM, Nvidia, or NIM.", cp(5))
        add(stdscr, 4, right, "Adds DuckDuckGo web search tooling for non-native providers.", cp(5))
        add(stdscr, 5, right, "Use --ca-* flags for headless runs; Claude flags pass through.", cp(5))
    else:
        add(stdscr, 1, 3, f"{title} routes Claude Code through selectable providers.", _style(bold=True) + cp(5))
        add(stdscr, 2, 3, "Anthropic, Ollama, LM Studio, vLLM, Nvidia Hosted, and self-hosted NIM.", cp(5))
        add(stdscr, 3, 3, "DuckDuckGo web search is attached for non-native providers.", cp(5))
        add(stdscr, 4, 3, "Headless setup uses --ca-* flags; Claude flags pass through.", cp(5))
        if panel_h > 6:
            add(stdscr, 6, 3, CREDITS, _style(bold=True) + cp(3))
        else:
            add(stdscr, 5, 3, CREDITS, _style(bold=True) + cp(3))

    return panel_h + 1


def render(stdscr, idx: int, sub: dict | None, notice: list[str], checks: list[str]) -> dict[str, int]:
    lines = status_text()
    items = main_items()
    h, w = _term_size()
    _clear()
    top = draw_intro_panel(stdscr)
    status_count = 5 if h >= 28 else 4 if h >= 23 else 2
    for i, line in enumerate(lines[:status_count]):
        color = cp(2) if line.startswith("provider:") or line.startswith("model:") else cp(5)
        add(stdscr, top + i, 2, line, color)

    row = top + status_count + 1
    row_by_action: dict[str, int] = {}
    sub_selected_row = -1
    submenu_budget = max(3, min(10, h - row - len(items) - len(checks) - 4))
    if sub and sub.get("kind") == "test-result":
        submenu_budget = max(4, min(10, h - row - len(items) - len(checks) - 3))

    for i, (key, label) in enumerate(items):
        row_by_action[key] = row
        if row >= h - 3:
            break
        if i == idx and (sub is None or sub.get("readonly")):
            style = _style(reverse=True, bold=True)
        elif key in ("launch", "launch-codex") and "disabled:" in label:
            style = _style(dim=True)
        elif key in ("launch", "launch-codex"):
            style = cp(2) + _style(bold=True)
        elif key == "test":
            style = cp(3) + _style(bold=True)
        elif key == "quit":
            style = cp(4)
        elif key in ("language", "provider", "model", "advisor-model", "ollama-options", "provider-options", "api-key", "base-url"):
            style = cp(3)
        else:
            style = ""
        _write_safe(row, 2, label[: max(0, w - 4)], style)
        row += 1

        if sub and sub.get("parent") == key:
            start, end = visible_sub_window(sub, submenu_budget)
            if start > 0 and row < h - 3:
                _write_safe(row, 6, f"... {start} above", _style(dim=True) + cp(5))
                row += 1
            for si in range(start, end):
                if row >= h - 3:
                    break
                item = sub["items"][si]
                if sub.get("kind") == "test-result":
                    text = f"  {item['label']}"
                    if "FAIL" in item["label"] or "TIMEOUT" in item["label"] or item["label"].startswith(("HTTP:", "Reason:", "Diagnosis:")):
                        style = cp(4) + _style(bold=True)
                    elif "OK" in item["label"]:
                        style = cp(2) + _style(bold=True)
                    else:
                        style = _style(dim=True) + cp(5)
                else:
                    marker = "*" if item.get("current") else " "
                    prefix = ">" if si == sub["idx"] else " "
                    text = f"{prefix} {marker} {item['label']}"
                    if si == sub["idx"]:
                        style = _style(reverse=True, bold=True)
                        sub_selected_row = row
                    elif item.get("current"):
                        style = cp(2) + _style(bold=True)
                    elif "[OK]" in item["label"]:
                        style = cp(2)
                    elif "[FAIL" in item["label"] or "[TIMEOUT]" in item["label"]:
                        style = cp(4) + _style(bold=True)
                    else:
                        style = _style(dim=True)
                if si == sub["idx"] and not sub.get("readonly"):
                    style = _style(reverse=True, bold=True)
                    sub_selected_row = row
                _write_safe(row, 4, text[: max(0, w - 6)], style)
                row += 1
            remaining = len(sub["items"]) - end
            if remaining > 0 and row < h - 3:
                _write_safe(row, 6, f"... {remaining} more", _style(dim=True) + cp(5))
                row += 1

    desc = selected_sub_description(sub)
    if desc and row < h - 5:
        _write_safe(row, 2, ("-" * max(8, w - 4))[: max(0, w - 4)], _style(dim=True) + cp(6))
        row += 1
        for line in textwrap.wrap(desc, width=max(24, w - 6))[:2]:
            if row >= h - 4:
                break
            _write_safe(row, 2, line[: max(0, w - 4)], _style(bold=True) + cp(6))
            row += 1

    if row < h - 4:
        _write_safe(row, 2, ("-" * max(8, w - 4))[: max(0, w - 4)], _style(dim=True) + cp(6))
        row += 1
        for line in checks[: max(0, h - row - 3)]:
            _write_safe(row, 2, line[: max(0, w - 4)], _style(bold=True) + cp(6))
            row += 1

    if notice:
        y = max(0, h - 5 - min(len(notice), 2))
        for j, line in enumerate(notice[:2]):
            _write_safe(y + j, 0, line[: w - 1], cp(2) if j == 0 else _style(dim=True))

    current_action = items[idx][0]
    _write_safe(h - 2, 0, help_for_action(current_action, sub.get("kind") if sub else None)[: w - 1], _style(dim=True) + cp(5))
    sys.stdout.flush()
    row_by_action["__sub_selected__"] = sub_selected_row
    return row_by_action


def main() -> int:
    init_colors()
    idx = index_for_action(default_launch_action()) if settings_ready_except_api_key() else 0
    sub: dict | None = None
    notice: list[str] = []
    checks = preflight_checks()
    row_by_action: dict[str, int] = {}

    def apply_test_result(code: int, out: str) -> None:
        nonlocal sub, notice, checks, idx
        ok = code == 0
        sub = test_submenu(summarize_test_output(code, out))
        if ok:
            notice = [t("test_passed")]
        elif "TIMEOUT" in out.upper() or "timed out" in out.lower():
            notice = ["Compatibility test timed out. The provider or model took too long to respond."]
        else:
            notice = [t("test_failed")]
        checks = preflight_checks()
        idx = index_for_action(default_launch_action() if ok else "model")

    while True:
        items = main_items()
        idx = max(0, min(idx, len(items) - 1))
        row_by_action = render(None, idx, sub, notice, checks)
        ch = read_menu_key()

        if sub and sub.get("readonly"):
            if ch in ("KEY_ESC", "q"):
                sub = None
                notice = []
                continue
            if ch in ("KEY_UP", "k"):
                notice = []
                idx = (idx - 1) % len(items)
                continue
            if ch in ("KEY_DOWN", "j"):
                notice = []
                idx = (idx + 1) % len(items)
                continue
            if ch == "KEY_ENTER":
                action = items[idx][0]
                if action == "launch":
                    provider, pcfg = current_provider_cfg()
                    if not claude_launch_enabled(provider):
                        notice = ["Launch Claude Code is disabled while a Codex provider is selected."]
                        sub = None
                        continue
                    if launch_requires_api_key(provider, pcfg):
                        label = dict(PROVIDERS).get(provider, provider)
                        notice = [
                            f"Launch blocked: {label} requires an API key.",
                            "Opening API key setup.",
                        ]
                        idx = index_for_action("api-key")
                        sub = None
                        continue
                    return PRELAUNCH_LAUNCH_CLAUDE
                if action == "launch-codex":
                    provider, pcfg = current_provider_cfg()
                    if not codex_launch_enabled(provider):
                        notice = ["Launch Codex is disabled until you select Codex or Codex routed as the provider."]
                        sub = None
                        continue
                    if launch_requires_api_key(provider, pcfg):
                        label = dict(PROVIDERS).get(provider, provider)
                        notice = [
                            f"Launch blocked: {label} requires an API key.",
                            "Opening API key setup.",
                        ]
                        idx = index_for_action("api-key")
                        sub = None
                        continue
                    return PRELAUNCH_LAUNCH_CODEX
                if action == "test":
                    code, out = run_test_with_animation(idx, checks)
                    apply_test_result(code, out)
                    continue
                sub = None
            else:
                continue

        if sub:
            if ch in ("KEY_ESC", "q"):
                sub = None
                notice = []
                checks = preflight_checks()
                continue
            if ch in ("KEY_UP", "k"):
                notice = []
                sub["idx"] = (sub["idx"] - 1) % len(sub["items"])
                provider_preview = selected_provider_value(sub)
                if provider_preview:
                    checks = provider_preview_checks(provider_preview)
                continue
            if ch in ("KEY_DOWN", "j"):
                notice = []
                sub["idx"] = (sub["idx"] + 1) % len(sub["items"])
                provider_preview = selected_provider_value(sub)
                if provider_preview:
                    checks = provider_preview_checks(provider_preview)
                continue
            if ch == "KEY_NPAGE":
                sub["idx"] = min(len(sub["items"]) - 1, sub["idx"] + 10)
                provider_preview = selected_provider_value(sub)
                if provider_preview:
                    checks = provider_preview_checks(provider_preview)
                continue
            if ch == "KEY_PPAGE":
                sub["idx"] = max(0, sub["idx"] - 10)
                provider_preview = selected_provider_value(sub)
                if provider_preview:
                    checks = provider_preview_checks(provider_preview)
                continue
            if ch == "KEY_ENTER":
                item = sub["items"][sub["idx"]]
                if sub["kind"] == "language":
                    _, out = run_cmd([CTL, "language", item["value"]])
                    notice = (out.strip().splitlines() or [item["value"]])[:2]
                    checks = preflight_checks()
                    sub = None
                    idx = index_for_action("provider")
                elif sub["kind"] == "provider":
                    _, out = run_cmd([CTL, "provider", item["value"]])
                    notice = (out.strip().splitlines() or [item["value"]])[:2]
                    checks = preflight_checks()
                    sub = None
                    provider = current_provider()
                    idx = index_for_action(default_launch_action() if provider == "codex" else "model")
                elif sub["kind"] == "api-key":
                    row = row_by_action.get("__sub_selected__", row_by_action.get("api-key", 10))
                    key = inline_secret_prompt(None, f"API key for {item['value']}: ", row)
                    if key:
                        _, out = run_cmd([CTL, "set-api-key", item["value"], key])
                        notice = (out.strip().splitlines() or [item["value"]])[:2]
                        checks = preflight_checks()
                        idx = index_for_action("base-url")
                    else:
                        notice = [t("api_key_unchanged")]
                    sub = None
                elif sub["kind"] == "model":
                    if item["value"] == "__custom__":
                        row = row_by_action.get("__sub_selected__", row_by_action.get("model", 10))
                        value = inline_prompt(None, "Model id or alias: ", row)
                        if value:
                            _, out = run_cmd([CTL, "model", value])
                            notice = (out.strip().splitlines() or [value])[:2]
                            checks = preflight_checks()
                            idx = index_for_action(after_model_action())
                        sub = None
                    else:
                        _, out = run_cmd([CTL, "model", item["value"]])
                        notice = (out.strip().splitlines() or [item["value"]])[:2]
                        checks = preflight_checks()
                        sub = None
                        idx = index_for_action(after_model_action())
                elif sub["kind"] == "advisor-model":
                    row = row_by_action.get("__sub_selected__", row_by_action.get("advisor-model", 10))
                    if item["value"] == "__custom__":
                        value = inline_prompt(None, "Advisor model id: ", row, "deepseek-v4-pro")
                    else:
                        value = item["value"] or "off"
                    _, out = run_cmd([CTL, "advisor-model", value])
                    notice = (out.strip().splitlines() or [value])[:2]
                    checks = preflight_checks()
                    sub = None
                    idx = index_for_action("ollama-options" if is_ollama_provider(current_provider()) else ("provider-options" if has_provider_options(current_provider()) else "test"))
                elif sub["kind"] == "ollama-options":
                    provider = current_provider()
                    row = row_by_action.get("__sub_selected__", row_by_action.get("ollama-options", 10))
                    provider_now, pcfg_now = current_provider_cfg()
                    opts_now = pcfg_now.get("ollama_options") or {}
                    if not isinstance(opts_now, dict):
                        opts_now = {}
                    action_value = item["value"]
                    value = ""
                    if action_value == "__edit_num_ctx__":
                        default = str(pcfg_now.get("num_ctx", "auto"))
                        entered = inline_prompt(None, "num_ctx (auto or integer): ", row, default)
                        value = f"num_ctx={entered}" if entered else ""
                    elif action_value == "__edit_min__":
                        default = str(pcfg_now.get("num_ctx_min", 32768))
                        entered = inline_prompt(None, "num_ctx auto minimum: ", row, default)
                        value = f"min={entered}" if entered else ""
                    elif action_value == "__edit_max__":
                        default = str(pcfg_now.get("num_ctx_max", 131072))
                        entered = inline_prompt(None, "num_ctx auto maximum: ", row, default)
                        value = f"max={entered}" if entered else ""
                    elif action_value == "__edit_keep_alive__":
                        default = str(pcfg_now.get("keep_alive", "5m"))
                        entered = inline_prompt(None, "keep_alive: ", row, default)
                        value = f"keep_alive={entered}" if entered else ""
                    elif action_value == "__edit_temperature__":
                        default = str(opts_now.get("temperature", "0.7"))
                        entered = inline_prompt(None, "temperature (unset:temperature clears): ", row, default)
                        value = entered if entered.startswith("unset:") else (f"temperature={entered}" if entered else "")
                    elif action_value == "__edit_top_p__":
                        default = str(opts_now.get("top_p", "0.8"))
                        entered = inline_prompt(None, "top_p (unset:top_p clears): ", row, default)
                        value = entered if entered.startswith("unset:") else (f"top_p={entered}" if entered else "")
                    elif action_value == "__edit_max_tokens__":
                        default = str(opts_now.get("num_predict", "4096"))
                        entered = inline_prompt(None, "max_tokens / num_predict: ", row, default)
                        value = f"max_tokens={entered}" if entered else ""
                    elif action_value == "__edit_timeout__":
                        default = str(pcfg_now.get("request_timeout_ms", "300000"))
                        entered = inline_prompt(None, "timeout ms: ", row, default)
                        value = f"timeout={entered}" if entered else ""
                    elif action_value == "__edit_rate_limit__":
                        default = str(pcfg_now.get("rate_limit_rpm", "40"))
                        entered = inline_prompt(None, "rate_limit_rpm (0 disables): ", row, default)
                        value = f"rate_limit_rpm={entered}" if entered else ""
                    elif action_value == "__custom__":
                        value = inline_prompt(None, "Ollama option KEY=VALUE: ", row, "temperature=0.7")
                    else:
                        value = action_value
                    if value:
                        _, out = run_cmd([CTL, "ollama-options", provider, value])
                        notice = (out.strip().splitlines() or [value])[:2]
                        checks = preflight_checks()
                        idx = index_for_action("test")
                    sub = None
                elif sub["kind"] == "provider-options":
                    provider = current_provider()
                    row = row_by_action.get("__sub_selected__", row_by_action.get("provider-options", 10))
                    provider_now, pcfg_now = current_provider_cfg()
                    action_value = item["value"]
                    value = ""
                    if action_value == "__edit_context_window__":
                        default = str(pcfg_now.get("context_window", "32768"))
                        entered = inline_prompt(None, "context_window: ", row, default)
                        value = f"context_window={entered}" if entered else ""
                    elif action_value == "__edit_reserve__":
                        default = str(pcfg_now.get("context_reserve_tokens", "1024"))
                        entered = inline_prompt(None, "context_reserve_tokens: ", row, default)
                        value = f"context_reserve_tokens={entered}" if entered else ""
                    elif action_value == "__edit_max_output__":
                        default = str(pcfg_now.get("max_output_tokens", "4096"))
                        entered = inline_prompt(None, "max_output_tokens: ", row, default)
                        value = f"max_output_tokens={entered}" if entered else ""
                    elif action_value == "__edit_timeout__":
                        default = str(pcfg_now.get("request_timeout_ms", "300000"))
                        entered = inline_prompt(None, "timeout ms: ", row, default)
                        value = f"timeout={entered}" if entered else ""
                    elif action_value == "__edit_rate_limit__":
                        default = str(pcfg_now.get("rate_limit_rpm", "40"))
                        entered = inline_prompt(None, "rate_limit_rpm (0 disables): ", row, default)
                        value = f"rate_limit_rpm={entered}" if entered else ""
                    elif action_value == "__edit_native__":
                        default = "true" if pcfg_now.get("native_compat", True) else "false"
                        entered = inline_prompt(None, "native true/false: ", row, default)
                        value = f"native={entered}" if entered else ""
                    elif action_value == "__custom__":
                        value = inline_prompt(None, "Provider option KEY=VALUE: ", row, "max_output_tokens=4096")
                    else:
                        value = action_value
                    if value:
                        _, out = run_cmd([CTL, "provider-options", provider, value])
                        notice = (out.strip().splitlines() or [value])[:2]
                        checks = preflight_checks()
                        idx = index_for_action("test")
                    sub = None
                continue
            continue

        if ch in ("KEY_ESC", "q"):
            return PRELAUNCH_CANCEL
        if ch in ("KEY_UP", "k"):
            notice = []
            idx = (idx - 1) % len(items)
            continue
        if ch in ("KEY_DOWN", "j"):
            notice = []
            idx = (idx + 1) % len(items)
            continue
        if ch != "KEY_ENTER":
            continue

        action = items[idx][0]
        if action == "launch":
            provider, pcfg = current_provider_cfg()
            if not claude_launch_enabled(provider):
                notice = ["Launch Claude Code is disabled while a Codex provider is selected."]
                continue
            if launch_requires_api_key(provider, pcfg):
                label = dict(PROVIDERS).get(provider, provider)
                notice = [
                    f"Launch blocked: {label} requires an API key.",
                    "Opening API key setup.",
                ]
                idx = index_for_action("api-key")
                continue
            return PRELAUNCH_LAUNCH_CLAUDE
        if action == "launch-codex":
            provider, pcfg = current_provider_cfg()
            if not codex_launch_enabled(provider):
                notice = ["Launch Codex is disabled until you select Codex or Codex routed as the provider."]
                continue
            if launch_requires_api_key(provider, pcfg):
                label = dict(PROVIDERS).get(provider, provider)
                notice = [
                    f"Launch blocked: {label} requires an API key.",
                    "Opening API key setup.",
                ]
                idx = index_for_action("api-key")
                continue
            return PRELAUNCH_LAUNCH_CODEX
        if action == "test":
            code, out = run_test_with_animation(idx, checks)
            apply_test_result(code, out)
            continue
        if action == "quit":
            return PRELAUNCH_CANCEL
        if action == "language":
            sub = build_language_submenu()
            notice = []
        elif action == "provider":
            sub = build_provider_submenu()
            notice = []
            provider_preview = selected_provider_value(sub)
            if provider_preview:
                checks = provider_preview_checks(provider_preview)
        elif action == "model":
            notice = [t("loading_models")]
            render(None, idx, None, notice, checks)
            sub, fallback_notice = build_model_submenu()
            notice = fallback_notice
            if sub is None:
                row = row_by_action.get("model", 10)
                value = inline_prompt(None, "Model id or alias: ", row)
                if value:
                    _, out = run_cmd([CTL, "model", value])
                    notice = (out.strip().splitlines() or [value])[:2]
                    checks = preflight_checks()
                    idx = index_for_action(after_model_action())
        elif action == "advisor-model":
            notice = []
            sub = build_advisor_model_submenu()
        elif action == "ollama-options":
            provider = current_provider()
            if is_ollama_provider(provider):
                sub = build_ollama_options_submenu()
                notice = []
            else:
                notice = ["Ollama options are available only for ollama and ollama-cloud."]
        elif action == "provider-options":
            provider = current_provider()
            if has_provider_options(provider):
                sub = build_provider_options_submenu()
                notice = []
            else:
                notice = ["Provider options are available for vLLM, NVIDIA hosted, and self-hosted NIM."]
        elif action == "api-key":
            provider = current_provider()
            row = row_by_action.get("api-key", 10)
            key = inline_secret_prompt(None, f"API key for {provider}: ", row)
            if key:
                _, out = run_cmd([CTL, "set-api-key", provider, key])
                notice = (out.strip().splitlines() or [provider])[:2]
                checks = preflight_checks()
            else:
                notice = [t("api_key_unchanged")]
            idx = index_for_action("base-url")
        elif action == "base-url":
            provider = current_provider()
            row = row_by_action.get("base-url", 12)
            value = inline_prompt(None, f"Base URL for {provider}: ", row, default_base_url(provider))
            if value:
                _, out = run_cmd([CTL, "base-url", provider, value])
                notice = (out.strip().splitlines() or [value])[:2]
                checks = preflight_checks()
            idx = index_for_action("model")


if __name__ == "__main__":
    try:
        with _RawTerminal():
            raise SystemExit(main())
    except KeyboardInterrupt:
        raise SystemExit(10)
