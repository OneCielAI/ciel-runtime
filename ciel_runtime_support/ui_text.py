"""Localized prelaunch labels and provider guidance data."""

from __future__ import annotations

UI_TEXT = {
    "en": {
        "language": "Language",
        "provider": "Provider",
        "api_key": "API key",
        "base_url": "Base URL",
        "model": "Model",
        "advisor_model": "Advisor Model",
        "test": "Test compatibility",
        "options": "LLM options",
        "channel_delivery": "Channel delivery",
        "channels": "Channels",
        "log_level": "Log level",
        "presets": "LLM presets",
        "context_setup": "Context setup",
        "timeout_preset": "Timeout preset",
        "apply_preset": "Apply preset",
        "model_family": "Model family",
        "recommended_preset_is": "recommended preset is",
        "back": "Back",
        "launch": "Launch Claude Code",
        "launch_agy": "Launch AGY",
        "launch_codex_app_server": "Launch Codex App Server",
        "launch_codex": "Launch Codex",
        "quit": "Quit",
        "title": "ciel-runtime pre-launch",
    },
    "ko": {
        "language": "언어",
        "provider": "프로바이더",
        "api_key": "API 키",
        "base_url": "Base URL",
        "model": "모델",
        "advisor_model": "Advisor Model",
        "test": "호환성 테스트",
        "options": "LLM 옵션",
        "channel_delivery": "채널 전달 방식",
        "channels": "채널",
        "log_level": "로그 레벨",
        "presets": "LLM 프리셋",
        "context_setup": "컨텍스트 설정",
        "timeout_preset": "타임아웃 프리셋",
        "apply_preset": "프리셋 적용",
        "model_family": "모델 계열",
        "recommended_preset_is": "추천 프리셋",
        "back": "뒤로",
        "launch": "Claude Code 실행",
        "launch_agy": "AGY 실행",
        "launch_codex_app_server": "Codex App Server 실행",
        "launch_codex": "Codex 실행",
        "quit": "종료",
        "title": "ciel-runtime 실행 전 설정",
    },
    "ja": {
        "language": "言語",
        "provider": "プロバイダー",
        "api_key": "APIキー",
        "base_url": "Base URL",
        "model": "モデル",
        "advisor_model": "Advisor Model",
        "test": "互換性テスト",
        "options": "LLMオプション",
        "channel_delivery": "チャンネル配信方式",
        "channels": "チャンネル",
        "log_level": "ログレベル",
        "presets": "LLMプリセット",
        "context_setup": "コンテキスト設定",
        "timeout_preset": "timeout プリセット",
        "apply_preset": "プリセットを適用",
        "model_family": "モデル系統",
        "recommended_preset_is": "推奨プリセット",
        "back": "戻る",
        "launch": "Claude Codeを起動",
        "launch_agy": "AGYを起動",
        "launch_codex_app_server": "Codex App Serverを起動",
        "launch_codex": "Codexを起動",
        "quit": "終了",
        "title": "ciel-runtime 起動前設定",
    },
    "zh": {
        "language": "语言",
        "provider": "提供商",
        "api_key": "API 密钥",
        "base_url": "Base URL",
        "model": "模型",
        "advisor_model": "Advisor Model",
        "test": "兼容性测试",
        "options": "LLM 选项",
        "channel_delivery": "频道投递方式",
        "channels": "频道",
        "log_level": "日志级别",
        "presets": "LLM 预设",
        "context_setup": "上下文设置",
        "timeout_preset": "Timeout 预设",
        "apply_preset": "应用预设",
        "model_family": "模型类型",
        "recommended_preset_is": "推荐预设",
        "back": "返回",
        "launch": "启动 Claude Code",
        "launch_agy": "启动 AGY",
        "launch_codex_app_server": "启动 Codex App Server",
        "launch_codex": "启动 Codex",
        "quit": "退出",
        "title": "ciel-runtime 启动前设置",
    },
}



PROVIDER_NOTES = {
    "en": {
        "anthropic": [
            "Anthropic: uses Claude Code's native Anthropic connection.",
            "Set an Anthropic API key here, or run `claude /login` separately to use your Claude account login.",
        ],
        "agy": [
            "AGY: uses Google Antigravity CLI's native sign-in and settings.",
            "AGY routed currently adds Ciel Runtime channel/PTY wake support; it does not override AGY model upstream traffic.",
        ],
        "ollama": [
            "Ollama: uses your local Ollama daemon; API key is normally not required.",
            "To use :cloud models through local Ollama, sign in on the Ollama host with `ollama signin`.",
        ],
        "ollama-cloud": [
            "Ollama Cloud: calls https://ollama.com/api directly; an Ollama API key is required.",
            "Use this when you want cloud models without relying on the local Ollama daemon's sign-in state.",
        ],
        "vllm": [
            "vLLM: Anthropic Messages API is the default; OpenAI-only chat completions endpoints are auto-detected.",
            "If auto-detection finds only /v1/chat/completions, ciel-runtime disables Native compatibility and routes through the local converter.",
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
            "Hosted API Catalog currently uses the ciel-runtime router path; self-hosted NIM uses native Messages.",
        ],
    },
    "ko": {
        "anthropic": [
            "Anthropic: Claude Code의 기본 Anthropic 연결을 사용합니다.",
            "여기에 Anthropic API key를 넣거나, 별도로 `claude /login`을 실행해 Claude 계정 로그인을 사용하세요.",
        ],
        "agy": [
            "AGY: Google Antigravity CLI의 기본 로그인과 설정을 사용합니다.",
            "AGY routed는 현재 Ciel Runtime channel/PTY wake 보조 기능을 붙이는 모드이며 AGY 모델 upstream 트래픽을 바꾸지는 않습니다.",
        ],
        "ollama": [
            "Ollama: 로컬 Ollama 데몬을 사용합니다. 일반 로컬 모델은 API key가 필요 없습니다.",
            "로컬 Ollama로 :cloud 모델을 쓰려면 Ollama가 실행되는 호스트에서 `ollama signin`이 필요합니다.",
        ],
        "ollama-cloud": [
            "Ollama Cloud: https://ollama.com/api를 직접 호출합니다. Ollama API key가 필요합니다.",
            "로컬 Ollama 데몬의 로그인 상태와 무관하게 클라우드 모델을 쓰고 싶을 때 사용합니다.",
        ],
        "vllm": [
            "vLLM: Anthropic Messages API를 기본값으로 사용합니다. OpenAI 전용 chat completions endpoint는 자동 감지합니다.",
            "자동 감지가 /v1/chat/completions만 찾으면 Native compatibility를 끄고 로컬 변환 라우터를 사용합니다.",
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
            "Hosted API Catalog는 ciel-runtime router 경로를 기본 사용합니다. self-hosted NIM은 native Messages를 사용합니다.",
        ],
    },
    "ja": {
        "anthropic": [
            "Anthropic: Claude CodeのネイティブAnthropic接続を使います。",
            "ここでAnthropic API keyを設定するか、別途`claude /login`を実行してClaudeアカウントログインを使ってください。",
        ],
        "agy": [
            "AGY: Google Antigravity CLIのネイティブサインインと設定を使います。",
            "AGY routedは現時点でCiel Runtime channel/PTY wake補助を追加し、AGYのモデルupstream trafficは上書きしません。",
        ],
        "ollama": [
            "Ollama: ローカルのOllama daemonを使います。通常のローカルモデルではAPI keyは不要です。",
            "ローカルOllama経由で:cloudモデルを使うには、Ollamaホストで`ollama signin`が必要です。",
        ],
        "ollama-cloud": [
            "Ollama Cloud: https://ollama.com/api を直接呼び出します。Ollama API keyが必要です。",
            "ローカルOllama daemonのサインイン状態に依存せずクラウドモデルを使う場合に選びます。",
        ],
        "vllm": [
            "vLLM: Anthropic Messages APIを既定にします。OpenAI専用chat completions endpointは自動検出します。",
            "自動検出で/v1/chat/completionsのみ見つかった場合、Native compatibilityを無効にしてローカル変換routerを使います。",
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
            "Hosted API Catalogはciel-runtime router経路を既定で使います。self-hosted NIMはnative Messagesを使います。",
        ],
    },
    "zh": {
        "anthropic": [
            "Anthropic: 使用Claude Code原生Anthropic连接。",
            "可在此设置Anthropic API key，或另行运行`claude /login`使用Claude账号登录。",
        ],
        "agy": [
            "AGY: 使用 Google Antigravity CLI 原生登录和设置。",
            "AGY routed 当前添加 Ciel Runtime channel/PTY wake 辅助，不覆盖 AGY 模型 upstream 流量。",
        ],
        "ollama": [
            "Ollama: 使用本地Ollama daemon；普通本地模型通常不需要API key。",
            "若通过本地Ollama使用:cloud模型，需要在运行Ollama的主机上执行`ollama signin`。",
        ],
        "ollama-cloud": [
            "Ollama Cloud: 直接调用 https://ollama.com/api；需要Ollama API key。",
            "当你想不依赖本地Ollama daemon登录状态使用云端模型时选择它。",
        ],
        "vllm": [
            "vLLM: 默认使用 Anthropic Messages API。仅 OpenAI chat completions 的端点会自动检测。",
            "如果自动检测只发现 /v1/chat/completions，ciel-runtime 会关闭 Native compatibility 并使用本地转换路由。",
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
            "Hosted API Catalog 默认使用 ciel-runtime router 路径；self-hosted NIM 使用 native Messages。",
        ],
    },
}

DEFAULT_ADVISOR_MODELS: tuple[str, ...] = (
    "",
    "claude-fable-5",
    "claude-opus-4-8",
    "deepseek-v4-pro",
    "claude-opus-4-6",
    "claude-sonnet-4-6",
    "glm-5.1",
)
