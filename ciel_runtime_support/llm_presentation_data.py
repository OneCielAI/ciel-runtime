"""Immutable LLM preset and option presentation catalog."""

from __future__ import annotations

from ciel_runtime_support.providers.constants import DEFAULT_REQUEST_TIMEOUT_MS

LLM_PRESETS: dict[str, tuple[str, str]] = {
    "balanced": ("Balanced Claude Code", "4K output, stable coding/chat defaults"),
    "coding": ("Coding deterministic", "lower randomness for edits, scripts, reviews"),
    "fast": ("Fast short tasks", "shorter output and timeout for quick jobs"),
    "long-context-65k": ("Long context 65K", "65K context target, 4K output reserve"),
    "long-context-128k": ("Long context 128K", "64K-128K context target, 4K-8K output reserve"),
    "long-context-256k": ("Long context 256K", "256K context target, 8K output reserve"),
    "long-context-300k": ("Long context 300K", "300K context target, 8K output reserve"),
    "long-context-512k": ("Long context 512K", "512K context target, 8K output reserve"),
    "million-context-1m": ("Ultra context 1M", "1M context target for high-capacity models"),
    "large-output": ("Large output/report", "larger 8K output for summaries/reports"),
    "reasoning": ("Reasoning model", "reasoning-friendly sampling"),
    "novelist": ("Novelist", "creative prose, voice, scenes, and narrative continuity"),
    "humanities-researcher": ("Humanities researcher", "interpretive research, close reading, and long evidence chains"),
    "mathematician": ("Mathematician", "careful derivations, proofs, and low-randomness reasoning"),
    "product-architect": ("Product architect", "requirements, system structure, tradeoffs, and implementation plans"),
    "teacher": ("Teacher / tutor", "clear explanations, examples, and step-by-step learning"),
}


LLM_SLIDER_LABELS: dict[str, str] = {
    "balanced": "balanced",
    "coding": "coding",
    "fast": "fast",
    "long-context-65k": "65K",
    "long-context-128k": "128K",
    "long-context-256k": "256K",
    "long-context-300k": "300K",
    "long-context-512k": "512K",
    "million-context-1m": "1M",
    "large-output": "output",
    "reasoning": "reasoning",
    "novelist": "novel",
    "humanities-researcher": "research",
    "mathematician": "math",
    "product-architect": "architect",
    "teacher": "teacher",
}


LLM_PRESET_I18N: dict[str, dict[str, tuple[str, str]]] = {
    "ko": {
        "balanced": ("균형형 Claude Code", "4K 출력, 안정적인 코딩/채팅 기본값"),
        "coding": ("코딩 결정형", "편집, 스크립트, 코드 리뷰용 낮은 무작위성"),
        "fast": ("빠른 짧은 작업", "짧은 출력과 짧은 타임아웃"),
        "long-context-65k": ("긴 컨텍스트 65K", "65K 컨텍스트 목표, 4K 출력 여유"),
        "long-context-128k": ("긴 컨텍스트 128K", "64K-128K 컨텍스트 목표, 4K-8K 출력 여유"),
        "long-context-256k": ("긴 컨텍스트 256K", "256K 컨텍스트 목표, 8K 출력 여유"),
        "long-context-300k": ("긴 컨텍스트 300K", "300K 컨텍스트 목표, 8K 출력 여유"),
        "long-context-512k": ("긴 컨텍스트 512K", "512K 컨텍스트 목표, 8K 출력 여유"),
        "million-context-1m": ("초장문 컨텍스트 1M", "고용량 모델용 1M 컨텍스트 목표"),
        "large-output": ("긴 출력/리포트", "요약과 리포트용 8K 출력"),
        "reasoning": ("추론 모델", "추론 친화 샘플링"),
        "novelist": ("소설가", "문체, 서사, 장면 전개를 위한 창작 설정"),
        "humanities-researcher": ("인문연구자", "해석, 근거 정리, 긴 문헌 맥락"),
        "mathematician": ("수학자", "낮은 무작위성과 단계적 증명/유도"),
        "product-architect": ("제품 설계자", "요구사항, 구조, 트레이드오프, 구현 계획"),
        "teacher": ("교사/튜터", "쉬운 설명, 예제, 단계별 학습"),
    },
    "ja": {
        "balanced": ("バランス型 Claude Code", "4K 出力、安定したコーディング/チャット既定値"),
        "coding": ("コーディング決定型", "編集、スクリプト、コードレビュー向けの低いランダム性"),
        "fast": ("高速な短い作業", "短い出力と短いタイムアウト"),
        "long-context-65k": ("長いコンテキスト 65K", "65K コンテキスト目標、4K 出力予約"),
        "long-context-128k": ("長いコンテキスト 128K", "64K-128K コンテキスト目標、4K-8K 出力予約"),
        "long-context-256k": ("長いコンテキスト 256K", "256K コンテキスト目標、8K 出力予約"),
        "long-context-300k": ("長いコンテキスト 300K", "300K コンテキスト目標、8K 出力予約"),
        "long-context-512k": ("長いコンテキスト 512K", "512K コンテキスト目標、8K 出力予約"),
        "million-context-1m": ("超長文コンテキスト 1M", "大容量モデル向けの 1M コンテキスト目標"),
        "large-output": ("長い出力/レポート", "要約とレポート向けの 8K 出力"),
        "reasoning": ("推論モデル", "推論向けサンプリング"),
        "novelist": ("小説家", "文体、物語、場面展開向けの創作設定"),
        "humanities-researcher": ("人文学研究者", "解釈、根拠整理、長い文献文脈"),
        "mathematician": ("数学者", "低いランダム性と段階的な証明/導出"),
        "product-architect": ("プロダクト設計者", "要件、構造、トレードオフ、実装計画"),
        "teacher": ("教師/チューター", "分かりやすい説明、例、段階的学習"),
    },
    "zh": {
        "balanced": ("均衡型 Claude Code", "4K 输出，稳定的编码/聊天默认值"),
        "coding": ("编码确定型", "用于编辑、脚本和代码审查的低随机性"),
        "fast": ("快速短任务", "较短输出和较短超时"),
        "long-context-65k": ("长上下文 65K", "65K 上下文目标，4K 输出预留"),
        "long-context-128k": ("长上下文 128K", "64K-128K 上下文目标，4K-8K 输出预留"),
        "long-context-256k": ("长上下文 256K", "256K 上下文目标，8K 输出预留"),
        "long-context-300k": ("长上下文 300K", "300K 上下文目标，8K 输出预留"),
        "long-context-512k": ("长上下文 512K", "512K 上下文目标，8K 输出预留"),
        "million-context-1m": ("超长上下文 1M", "面向高容量模型的 1M 上下文目标"),
        "large-output": ("长输出/报告", "用于摘要和报告的 8K 输出"),
        "reasoning": ("推理模型", "适合推理的采样"),
        "novelist": ("小说家", "面向文风、叙事、场景推进的创作设置"),
        "humanities-researcher": ("人文学研究者", "解释性研究、证据整理和长文献上下文"),
        "mathematician": ("数学家", "低随机性、逐步证明和推导"),
        "product-architect": ("产品架构师", "需求、系统结构、取舍和实现计划"),
        "teacher": ("教师/导师", "清晰解释、示例和分步学习"),
    },
}


TIMEOUT_PRESETS: dict[str, tuple[int, str, str]] = {
    "timeout-fast": (120000, "Fast retry 2m", "short wait, quick retry on stalled providers"),
    "timeout-standard": (300000, "Standard 5m", "balanced wait for normal cloud coding"),
    "timeout-long": (600000, "Long stream 10m", "large edits or slow streamed responses"),
    "timeout-deep": (1200000, "Deep work 20m", "long reasoning, reports, and big context"),
    "timeout-marathon": (3600000, "Marathon 60m", "very long hosted/model-server jobs"),
}


TIMEOUT_PRESET_I18N: dict[str, dict[str, tuple[str, str]]] = {
    "ko": {
        "timeout-fast": ("빠른 재시도 2분", "멈춘 provider를 빨리 감지"),
        "timeout-standard": ("표준 5분", "일반 클라우드 코딩용 균형값"),
        "timeout-long": ("긴 스트림 10분", "큰 편집 또는 느린 스트리밍 응답"),
        "timeout-deep": ("깊은 작업 20분", "긴 추론, 리포트, 큰 컨텍스트"),
        "timeout-marathon": ("장시간 60분", "매우 긴 hosted/model-server 작업"),
    },
    "ja": {
        "timeout-fast": ("高速 retry 2分", "停止した provider を早く検出"),
        "timeout-standard": ("標準 5分", "通常のクラウド coding 向け"),
        "timeout-long": ("長い stream 10分", "大きな編集や遅い streamed 応答"),
        "timeout-deep": ("深い作業 20分", "長い推論、report、大きな context"),
        "timeout-marathon": ("長時間 60分", "非常に長い hosted/model-server 作業"),
    },
    "zh": {
        "timeout-fast": ("快速重试 2分钟", "快速发现卡住的 provider"),
        "timeout-standard": ("标准 5分钟", "普通云端编码的均衡值"),
        "timeout-long": ("长流式 10分钟", "大型编辑或较慢流式响应"),
        "timeout-deep": ("深度工作 20分钟", "长推理、报告和大上下文"),
        "timeout-marathon": ("超长 60分钟", "很长的 hosted/model-server 任务"),
    },
}


LLM_PRESET_TIMEOUT_MS: dict[str, int] = {
    "balanced": DEFAULT_REQUEST_TIMEOUT_MS,
    "coding": DEFAULT_REQUEST_TIMEOUT_MS,
    "fast": 120000,
    "long-context-65k": DEFAULT_REQUEST_TIMEOUT_MS,
    "long-context-128k": 600000,
    "long-context-256k": 600000,
    "long-context-300k": 600000,
    "long-context-512k": 600000,
    "million-context-1m": 600000,
    "large-output": 600000,
    "reasoning": 600000,
    "novelist": 600000,
    "humanities-researcher": 600000,
    "mathematician": 600000,
    "product-architect": 600000,
    "teacher": 300000,
}


CONTEXT_HEAVY_PRESETS = {
    "long-context-65k",
    "long-context-128k",
    "long-context-256k",
    "long-context-300k",
    "long-context-512k",
    "million-context-1m",
    "large-output",
    "reasoning",
    "novelist",
    "humanities-researcher",
    "mathematician",
    "product-architect",
    "teacher",
}


AUTO_TIMEOUT_MIN_MS = 120000


AUTO_TIMEOUT_MAX_MS = 600000


AUTO_TIMEOUT_ROUND_MS = 30000


MODEL_FAMILY_I18N: dict[str, dict[str, str]] = {
    "ko": {
        "coding": "코딩",
        "reasoning": "추론",
        "million-context": "1M 컨텍스트",
        "large": "대형 모델",
        "long-context": "긴 컨텍스트",
        "general": "일반",
    },
    "ja": {
        "coding": "コーディング",
        "reasoning": "推論",
        "million-context": "1M コンテキスト",
        "large": "大型モデル",
        "long-context": "長いコンテキスト",
        "general": "汎用",
    },
    "zh": {
        "coding": "编码",
        "reasoning": "推理",
        "million-context": "1M 上下文",
        "large": "大型模型",
        "long-context": "长上下文",
        "general": "通用",
    },
}


RUNTIME_LLM_ORIGINAL_KEY = "runtime_llm_original_options"


RUNTIME_LLM_OPTION_KEYS = {
    "llm_preset",
    "context_window",
    "context_reserve_tokens",
    "max_output_tokens",
    "request_timeout_ms",
    "stream_idle_timeout_ms",
    "temperature",
    "top_p",
    "top_k",
    "native_compat",
    "stream_enabled",
    "stream_word_chunking",
    "num_ctx",
    "num_ctx_min",
    "num_ctx_max",
    "keep_alive",
    "think",
    "ollama_options",
    "rate_limit_rpm",
    "rate_limit_status",
    "force_query_string",
    "supports_tool_choice",
    "ip_family",
    "model_context_max",
    "model_context_model",
    "max_model_len",
}


LLM_OPTION_DESCRIPTIONS: dict[str, dict[str, str]] = {
    "preset": {
        "en": "Apply a bundled LLM preset (output tokens, sampling, timeout) tuned for this provider/model family.",
        "ko": "현재 provider/모델 계열에 맞춘 LLM 프리셋(출력 토큰, 샘플링, 타임아웃)을 한 번에 적용합니다.",
        "ja": "現在のprovider/モデル系列向けに調整されたLLMプリセット(出力トークン、サンプリング、タイムアウト)を一括適用します。",
        "zh": "应用为当前 provider/模型系列调优的 LLM 预设（输出 token、采样、超时）。",
    },
    "context_setup": {
        "en": "Begin here: choose how much of the selected model's context window ciel-runtime may use. Larger windows read more files/history but cost more time and provider quota.",
        "ko": "처음엔 여기부터 설정하세요. 선택한 모델의 컨텍스트 창을 ciel-runtime가 얼마나 사용할지 고릅니다. 클수록 파일/히스토리를 더 많이 읽지만 느리고 provider 한도를 더 씁니다.",
        "ja": "まずここから設定します。選択モデルのコンテキスト窓をciel-runtimeがどれだけ使うかを選びます。大きいほど多くのファイル/履歴を読めますが、遅くなりprovider枠を使います。",
        "zh": "先从这里设置：选择 ciel-runtime 可使用所选模型多少上下文窗口。越大可读更多文件/历史，但更慢并消耗 provider 配额。",
    },
    "num_ctx": {
        "en": "Ollama context window (num_ctx). Use 'auto' to size per-request between min/max, or a fixed integer like 65536.",
        "ko": "Ollama 컨텍스트 창(num_ctx). auto 면 요청 크기에 따라 min/max 사이에서 자동 선택, 또는 65536 같은 고정 정수.",
        "ja": "Ollamaのコンテキスト窓(num_ctx)。autoでmin/max間を要求毎に自動選択、または65536のような固定整数を指定。",
        "zh": "Ollama 上下文窗口（num_ctx）。auto 在 min/max 间按请求自动选择，或填固定整数如 65536。",
    },
    "num_ctx_min": {
        "en": "Lower bound when num_ctx is auto. Small requests will not go below this value.",
        "ko": "num_ctx=auto일 때 사용할 최소 컨텍스트. 작은 요청도 이 값보다 작게 내려가지 않습니다.",
        "ja": "num_ctx=auto時の最小コンテキスト。小さな要求でもこの値未満にはなりません。",
        "zh": "num_ctx=auto 时的下限。小请求也不会低于此值。",
    },
    "num_ctx_max": {
        "en": "Upper bound when num_ctx is auto. Keep at or below the real server context limit.",
        "ko": "num_ctx=auto일 때 사용할 최대 컨텍스트. 실제 서버 한계 이하로 두세요.",
        "ja": "num_ctx=auto時の最大コンテキスト。実サーバー上限以下にしてください。",
        "zh": "num_ctx=auto 时的上限。应不高于真实服务器上下文上限。",
    },
    "num_predict": {
        "en": "Ollama max output tokens (num_predict). Input + reserved output must fit inside num_ctx.",
        "ko": "Ollama 최대 출력 토큰(num_predict). 입력 + 예약 출력이 num_ctx 안에 들어가야 합니다.",
        "ja": "Ollamaの最大出力トークン(num_predict)。入力と予約出力はnum_ctxの中に収まる必要があります。",
        "zh": "Ollama 最大输出 token（num_predict）。输入加预留输出必须放进 num_ctx。",
    },
    "max_output_tokens": {
        "en": "Max output tokens passed to Claude Code (CLAUDE_CODE_MAX_OUTPUT_TOKENS) and used as the router cap.",
        "ko": "Claude Code에 전달되는 최대 출력 토큰(CLAUDE_CODE_MAX_OUTPUT_TOKENS)이자 라우터 출력 상한.",
        "ja": "Claude Codeへ渡す最大出力トークン(CLAUDE_CODE_MAX_OUTPUT_TOKENS)であり、ルーター出力上限としても使われます。",
        "zh": "传给 Claude Code 的最大输出 token（CLAUDE_CODE_MAX_OUTPUT_TOKENS），同时作为路由器输出上限。",
    },
    "context_window": {
        "en": "vLLM/NIM context window used by ciel-runtime caps. Native mode cannot raise the real server limit.",
        "ko": "ciel-runtime 라우터가 사용하는 vLLM/NIM 컨텍스트 값. native 모드에서는 실제 서버 한계를 늘릴 수 없습니다.",
        "ja": "ciel-runtimeルーターが使うvLLM/NIMコンテキスト値。nativeモードでは実サーバー上限は超えられません。",
        "zh": "ciel-runtime 路由器使用的 vLLM/NIM 上下文值。native 模式无法提高真实服务器上限。",
    },
    "context_reserve_tokens": {
        "en": "Tokens reserved for the input side when ciel-runtime caps max_tokens. Ignored by direct native requests.",
        "ko": "ciel-runtime가 max_tokens를 줄일 때 입력 쪽 여유로 남기는 토큰. direct native 요청에는 적용되지 않습니다.",
        "ja": "ciel-runtimeがmax_tokensを制限する時に入力側へ残す余裕。direct native要求では無視されます。",
        "zh": "ciel-runtime 限制 max_tokens 时为输入侧预留的 token。direct native 请求会忽略。",
    },
    "request_timeout_ms": {
        "en": "Upstream wait timeout in milliseconds.",
        "ko": "업스트림 응답 대기 시간(ms).",
        "ja": "上流応答待ちタイムアウト(ms)。",
        "zh": "上游响应等待超时（毫秒）。",
    },
    "timeout_profile": {
        "en": "Choose a timeout preset. It sets both upstream wait timeout and stream idle timeout.",
        "ko": "타임아웃 프리셋을 선택합니다. 업스트림 대기 시간과 스트림 idle timeout을 함께 설정합니다.",
        "ja": "timeout preset を選びます。上流待機 timeout と stream idle timeout を同時に設定します。",
        "zh": "选择 timeout 预设，同时设置上游等待超时和 stream idle timeout。",
    },
    "stream_idle_timeout_ms": {
        "en": "Maximum silence allowed while a stream is open. If no bytes arrive for this long, ciel-runtime retries or reports a timeout.",
        "ko": "스트림 연결이 열린 뒤 아무 byte도 오지 않아도 기다리는 최대 시간입니다. 이 시간을 넘으면 재시도하거나 timeout으로 처리합니다.",
        "ja": "stream 接続中に byte が来ないまま待つ最大時間です。超えると retry または timeout として扱います。",
        "zh": "流式连接打开后允许无字节到达的最长时间；超过后重试或作为 timeout 处理。",
    },
    "rate_limit_rpm": {
        "en": "Router-side upstream request limit per minute. Default is off; set a positive RPM to enable waiting.",
        "ko": "라우터가 업스트림 요청 수를 분당 제한합니다. 기본값은 off입니다. 양수 RPM을 설정하면 대기 제한을 켭니다.",
        "ja": "ルーター側の上流リクエスト数/分の制限。既定は off。正の RPM を設定すると待機制限を有効化します。",
        "zh": "路由器侧上游每分钟请求限制。默认关闭；设置正数 RPM 后启用等待限制。",
    },
    "rate_limit_enabled": {
        "en": "Toggle ciel-runtime's router-side RPM limiter. Off means rate_limit_rpm=0 and no ciel-runtime wait is inserted.",
        "ko": "ciel-runtime 라우터 내부 RPM 제한을 켜거나 끕니다. off면 rate_limit_rpm=0이며 ciel-runtime가 대기 시간을 넣지 않습니다.",
        "ja": "ciel-runtime ルーター側 RPM 制限を切り替えます。off は rate_limit_rpm=0 で、ciel-runtime は待機を挿入しません。",
        "zh": "切换 ciel-runtime 路由器侧 RPM 限制。off 表示 rate_limit_rpm=0，ciel-runtime 不插入等待。",
    },
    "rate_limit_status": {
        "en": "Show optional colored RPM usage status in Claude responses. Default is off.",
        "ko": "Claude 응답에 RPM 사용량 상태를 색상 텍스트로 표시합니다. 기본값은 off입니다.",
        "ja": "Claude応答にRPM使用量状態を色付きテキストで表示します。既定は off。",
        "zh": "在 Claude 响应中显示彩色 RPM 使用量状态。默认关闭。",
    },
    "temperature": {
        "en": "Sampling temperature (0..2). Higher is more varied; lower is more deterministic.",
        "ko": "샘플링 temperature (0~2). 높을수록 다양, 낮을수록 결정적.",
        "ja": "サンプリングtemperature (0〜2)。高いほど多様、低いほど決定的。",
        "zh": "采样 temperature（0..2）。越高越多样，越低越确定。",
    },
    "top_p": {
        "en": "Nucleus sampling top_p (0..1). Lower restricts token choices; 0.8 is a moderate default.",
        "ko": "누적 확률 top_p (0~1). 낮을수록 후보 토큰을 좁힘. 0.8 정도가 적당한 기본값.",
        "ja": "nucleus samplingのtop_p (0〜1)。低いほど候補を絞ります。0.8は中程度の既定値。",
        "zh": "nucleus 采样 top_p（0..1）。越低候选越窄；0.8 是中等默认值。",
    },
    "top_k": {
        "en": "Top-K sampling cutoff. Smaller values pick from a tighter token shortlist.",
        "ko": "Top-K 샘플링. 값이 작을수록 후보 토큰 집합이 좁아집니다.",
        "ja": "Top-Kサンプリング。値が小さいほど候補集合は狭くなります。",
        "zh": "Top-K 采样。值越小候选集合越窄。",
    },
    "think": {
        "en": "Toggle Ollama 'think' output. Claude Code may not display provider-specific thinking cleanly.",
        "ko": "Ollama thinking 출력 여부. Claude Code가 provider별 thinking을 항상 깔끔히 표시하지는 않습니다.",
        "ja": "Ollama thinking出力を切り替えます。Claude Code側で常に綺麗に表示されるとは限りません。",
        "zh": "切换 Ollama thinking 输出。Claude Code 不一定能完整显示。",
    },
    "keep_alive": {
        "en": "How long Ollama keeps the model loaded after a request. Longer reduces reloads but holds memory.",
        "ko": "요청 후 Ollama가 모델을 메모리에 유지하는 시간. 길수록 재로딩은 줄지만 메모리를 더 잡습니다.",
        "ja": "要求後にOllamaがモデルを保持する時間。長いほど再読み込みは減りますがメモリを保持します。",
        "zh": "请求后 Ollama 保持模型加载的时间。越长减少重载，但占用内存更久。",
    },
    "native_compat": {
        "en": "Use direct Anthropic-compatible /v1/messages on this provider. Off routes through ciel-runtime's translator.",
        "ko": "이 provider의 Anthropic-호환 /v1/messages에 직접 연결합니다. off 면 ciel-runtime 라우터를 거칩니다.",
        "ja": "このproviderのAnthropic互換/v1/messagesに直接接続します。offだとciel-runtimeルーターを経由します。",
        "zh": "对该 provider 直接走 Anthropic 兼容 /v1/messages；关闭则经由 ciel-runtime 路由器转换。",
    },
    "supports_tool_choice": {
        "en": "Forward Claude Code tool_choice upstream. For vLLM, enable only when the server was launched with --enable-auto-tool-choice and the matching --tool-call-parser.",
        "ko": "Claude Code의 tool_choice를 upstream에 전달합니다. vLLM은 서버를 --enable-auto-tool-choice 및 모델에 맞는 --tool-call-parser로 실행한 경우에만 켜세요.",
        "ja": "Claude Code の tool_choice を上流へ転送します。vLLM では --enable-auto-tool-choice と対応する --tool-call-parser で起動した場合のみ有効にしてください。",
        "zh": "将 Claude Code 的 tool_choice 转发到上游。vLLM 仅在服务器使用 --enable-auto-tool-choice 和匹配的 --tool-call-parser 启动时启用。",
    },
    "stream_enabled": {
        "en": "Toggle streaming. Off forces stream:false upstream and returns the full response, useful when SSE fragmentation causes tool-call/JSON parse errors.",
        "ko": "스트리밍 on/off. off면 업스트림에 stream:false를 강제하고 응답 전체를 받습니다. SSE 단편화로 tool-call/JSON 파싱이 실패할 때 유용합니다.",
        "ja": "ストリーミングを切り替えます。offにすると上流にstream:falseを強制し、応答全体を返します。SSE断片化でtool-call/JSONが失敗する時に有効です。",
        "zh": "切换流式输出。off 时强制对上游 stream:false 并返回完整响应；用于 SSE 分片导致的 tool-call/JSON 解析失败。",
    },
    "stream_word_chunking": {
        "en": "Buffer text deltas at whitespace/word boundaries before flushing the SSE event. Tool deltas pass through unchanged.",
        "ko": "텍스트 delta를 공백/단어 경계까지 모아서 SSE 이벤트로 전송. tool delta는 그대로 통과합니다.",
        "ja": "テキストdeltaを空白/単語境界までバッファしてSSEイベントを送信します。tool deltaはそのまま透過します。",
        "zh": "在空白/单词边界处合并文本 delta 后发送 SSE 事件。工具 delta 原样透传。",
    },
    "workflows_enabled": {
        "en": "Allow Claude Code dynamic workflow features through the ciel-runtime gateway. This removes the experimental-beta disable env for this launch.",
        "ko": "ciel-runtime 게이트웨이 경유 상태에서 Claude Code dynamic workflow 기능을 허용합니다. 이 실행에서는 experimental beta 차단 env를 제거합니다.",
        "ja": "ciel-runtime gateway 経由で Claude Code dynamic workflow 機能を許可します。この起動では experimental beta 無効化 env を外します。",
        "zh": "允许 Claude Code dynamic workflow 通过 ciel-runtime gateway 工作。本次启动会移除 experimental beta 禁用环境变量。",
    },
    "ultracode_enabled": {
        "en": "Start Claude Code with the ultracode session setting. Requires verified xhigh_effort model capability.",
        "ko": "Claude Code를 ultracode 세션 설정으로 시작합니다. 검증된 xhigh_effort 모델 capability가 필요합니다.",
        "ja": "Claude Code を ultracode セッション設定で起動します。検証済みの xhigh_effort model capability が必要です。",
        "zh": "用 ultracode 会话设置启动 Claude Code。需要已验证的 xhigh_effort 模型 capability。",
    },
    "claude_code_supported_capabilities": {
        "en": "Comma-separated Claude Code capability override for the selected model: effort,xhigh_effort,max_effort,thinking,adaptive_thinking,interleaved_thinking.",
        "ko": "선택 모델의 Claude Code capability override입니다. 쉼표로 effort,xhigh_effort,max_effort,thinking,adaptive_thinking,interleaved_thinking 를 지정합니다.",
        "ja": "選択モデルの Claude Code capability override。カンマ区切りで effort,xhigh_effort,max_effort,thinking,adaptive_thinking,interleaved_thinking を指定します。",
        "zh": "所选模型的 Claude Code capability override，逗号分隔：effort,xhigh_effort,max_effort,thinking,adaptive_thinking,interleaved_thinking。",
    },
    "router_debug_external_access": {
        "en": "Expose the router UI/API to non-local clients for debugging. Off denies external clients and next launch binds locally unless environment overrides it.",
        "ko": "디버깅을 위해 라우터 UI/API를 외부 클라이언트에 노출합니다. off면 외부 요청을 차단하고 다음 실행부터 환경값이 없으면 로컬에만 바인딩합니다.",
        "ja": "デバッグ用にルーター UI/API を外部クライアントへ公開します。off では外部リクエストを拒否し、次回起動時は環境変数がなければローカルのみで bind します。",
        "zh": "为了调试向外部客户端暴露路由器 UI/API。关闭时会拒绝外部请求；下次启动在没有环境变量覆盖时仅绑定本地。",
    },
    "route_through_router": {
        "en": "Route Anthropic through the local ciel-runtime router. This enables router features such as LLM channel injection. If no Anthropic API key is configured, the router forwards Claude Code OAuth/API auth headers.",
        "ko": "Anthropic을 로컬 ciel-runtime 라우터로 경유합니다. LLM 채널 주입 같은 라우터 기능을 쓸 수 있습니다. Anthropic API 키가 없으면 Claude Code의 OAuth/API 인증 헤더를 라우터가 전달합니다.",
        "ja": "Anthropic をローカル ciel-runtime ルーター経由にします。LLM channel injection などのルーター機能を使えます。Anthropic API key が未設定の場合は Claude Code の OAuth/API 認証ヘッダーをルーターが転送します。",
        "zh": "通过本地 ciel-runtime 路由器转发 Anthropic。可使用 LLM channel injection 等路由器功能。未配置 Anthropic API key 时，路由器会转发 Claude Code 的 OAuth/API 认证头。",
    },
    "router_debug_message_preview_chars": {
        "en": "When greater than 0, include the first N characters of the latest user message in router event data for debugging. Keep 0 for privacy.",
        "ko": "0보다 크면 디버깅용 이벤트 data에 최근 사용자 메시지 앞 N자를 포함합니다. 개인정보 보호를 위해 기본값은 0입니다.",
        "ja": "0より大きい場合、デバッグ用イベントdataに直近ユーザーメッセージの先頭N文字を含めます。プライバシー保護のため既定値は0です。",
        "zh": "大于0时，在调试事件data中包含最近用户消息的前N个字符。为保护隐私默认值为0。",
    },
    "back": {
        "en": "Return to the main menu.",
        "ko": "메인 메뉴로 돌아갑니다.",
        "ja": "メインメニューに戻ります。",
        "zh": "返回主菜单。",
    },
}


LLM_OPTION_TOGGLE_KEYS = {
    "stream_enabled",
    "stream_word_chunking",
    "native_compat",
    "think",
    "rate_limit_enabled",
    "rate_limit_status",
    "router_debug_external_access",
    "route_through_router",
    "workflows_enabled",
    "ultracode_enabled",
}
