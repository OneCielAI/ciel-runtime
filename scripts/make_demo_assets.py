#!/usr/bin/env python3
from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


ROOT = Path(__file__).resolve().parents[1]
ASSET_DIR = ROOT / "docs" / "assets"
WIDTH = 1500
HEIGHT = 920


COLORS = {
    "bg": (0, 0, 0),
    "fg": (220, 220, 220),
    "muted": (150, 150, 150),
    "red": (212, 76, 76),
    "green": (30, 220, 70),
    "yellow": (225, 220, 0),
    "orange": (255, 145, 40),
    "white": (245, 245, 245),
    "select_bg": (235, 235, 235),
    "select_fg": (20, 20, 20),
}


LANG = {
    "en": {
        "name": "English",
        "welcome": "Welcome back!",
        "tips_title": "Tips for getting started",
        "status": ["provider: vllm", "language: en", "mode: vllm-native", "base_url: http://127.0.0.1:8000", "model: qwen3-coder-30b"],
        "tips": [
            "Choose provider, model, base URL, and API key before launch.",
            "Routes Claude Code to Anthropic, Ollama, vLLM, Nvidia, or NIM.",
            "Checks text, tool_use, and tool_result compatibility before launch.",
        ],
        "menu": [
            "0. Language  [English]",
            "1. Provider  [vllm]",
            "2. API key  [optional]",
            "3. Base URL  [http://127.0.0.1:8000]",
            "4. Model  [qwen3-coder-30b]",
            "5. LLM options  [ctx 65K; out 4K]",
            "6. Compatibility test",
            "7. Launch Claude Code",
            "Quit",
        ],
        "provider": ["Provider menu", "Anthropic", "Ollama", "Ollama Cloud", "vLLM [selected]", "Nvidia Hosted", "Self Hosted NIM"],
        "base": ["Base URL", "http://127.0.0.1:8000", "Use the server root. Ciel Runtime calls /v1/messages."],
        "model": ["Model picker", "qwen3-coder-30b [selected]", "qwen3.6-27b-nvfp4", "+ Custom model id..."],
        "options": ["LLM options", "Apply preset  [Coding deterministic]", "Context window  [65536]", "Context reserve  [4096]", "Max output tokens  [4096]", "Native compatibility  [True]"],
        "test": ["Compatibility test", "Runtime max_model_len: 65536", "vLLM hint: Qwen3-Coder -> qwen3_xml", "Text response: OK", "Tool use: OK", "Tool result: OK"],
        "footers": [
            "vLLM must expose Anthropic-compatible /v1/messages and model-specific tool calling.",
            "Do not append /v1 here. Enter the vLLM server root only.",
            "Models are sorted alphabetically when the endpoint can list them.",
            "For Qwen3-Coder, serve vLLM with --enable-auto-tool-choice --tool-call-parser qwen3_xml.",
            "Successful tests advance focus to Launch Claude Code.",
        ],
    },
    "ko": {
        "name": "한국어",
        "welcome": "환영합니다!",
        "tips_title": "시작 도움말",
        "status": ["provider: vllm", "language: ko", "mode: vllm-native", "base_url: http://127.0.0.1:8000", "model: qwen3-coder-30b"],
        "tips": [
            "실행 전에 프로바이더, 모델, Base URL, API 키를 선택합니다.",
            "Claude Code를 Anthropic, Ollama, vLLM, Nvidia, NIM으로 라우팅합니다.",
            "실행 전 text, tool_use, tool_result 호환성을 확인합니다.",
        ],
        "menu": [
            "0. 언어  [한국어]",
            "1. 프로바이더  [vllm]",
            "2. API 키  [선택]",
            "3. Base URL  [http://127.0.0.1:8000]",
            "4. 모델  [qwen3-coder-30b]",
            "5. LLM 옵션  [ctx 65K; out 4K]",
            "6. 호환성 테스트",
            "7. Claude Code 실행",
            "종료",
        ],
        "provider": ["프로바이더 메뉴", "Anthropic", "Ollama", "Ollama Cloud", "vLLM [선택됨]", "Nvidia Hosted", "Self Hosted NIM"],
        "base": ["Base URL", "http://127.0.0.1:8000", "서버 root를 입력합니다. Ciel Runtime가 /v1/messages를 호출합니다."],
        "model": ["모델 선택", "qwen3-coder-30b [선택됨]", "qwen3.6-27b-nvfp4", "+ 사용자 모델 id..."],
        "options": ["LLM 옵션", "프리셋 적용  [코딩 결정론]", "Context window  [65536]", "Context reserve  [4096]", "Max output tokens  [4096]", "Native compatibility  [True]"],
        "test": ["호환성 테스트", "Runtime max_model_len: 65536", "vLLM hint: Qwen3-Coder -> qwen3_xml", "Text response: OK", "Tool use: OK", "Tool result: OK"],
        "footers": [
            "vLLM은 Anthropic 호환 /v1/messages와 모델별 tool calling 설정이 필요합니다.",
            "/v1을 붙이지 말고 vLLM 서버 root만 입력합니다.",
            "엔드포인트가 목록을 제공하면 모델은 알파벳순으로 정렬됩니다.",
            "Qwen3-Coder는 --enable-auto-tool-choice --tool-call-parser qwen3_xml로 실행하세요.",
            "테스트가 성공하면 포커스가 Claude Code 실행으로 이동합니다.",
        ],
    },
    "ja": {
        "name": "日本語",
        "welcome": "おかえり!",
        "tips_title": "はじめるためのヒント",
        "status": ["provider: vllm", "language: ja", "mode: vllm-native", "base_url: http://127.0.0.1:8000", "model: qwen3-coder-30b"],
        "tips": [
            "起動前にプロバイダー、モデル、Base URL、APIキーを選択します。",
            "Claude CodeをAnthropic、Ollama、vLLM、Nvidia、NIMへ接続します。",
            "起動前にtext、tool_use、tool_result互換性を確認します。",
        ],
        "menu": [
            "0. 言語  [日本語]",
            "1. プロバイダー  [vllm]",
            "2. APIキー  [任意]",
            "3. Base URL  [http://127.0.0.1:8000]",
            "4. モデル  [qwen3-coder-30b]",
            "5. LLMオプション  [ctx 65K; out 4K]",
            "6. 互換性テスト",
            "7. Claude Code 起動",
            "終了",
        ],
        "provider": ["プロバイダーメニュー", "Anthropic", "Ollama", "Ollama Cloud", "vLLM [選択中]", "Nvidia Hosted", "Self Hosted NIM"],
        "base": ["Base URL", "http://127.0.0.1:8000", "サーバーrootを入力します。Ciel Runtimeが/v1/messagesを呼びます。"],
        "model": ["モデル選択", "qwen3-coder-30b [選択中]", "qwen3.6-27b-nvfp4", "+ カスタムモデルid..."],
        "options": ["LLMオプション", "プリセット適用  [コーディング決定論]", "Context window  [65536]", "Context reserve  [4096]", "Max output tokens  [4096]", "Native compatibility  [True]"],
        "test": ["互換性テスト", "Runtime max_model_len: 65536", "vLLM hint: Qwen3-Coder -> qwen3_xml", "Text response: OK", "Tool use: OK", "Tool result: OK"],
        "footers": [
            "vLLMにはAnthropic互換/v1/messagesとモデル別tool calling設定が必要です。",
            "/v1を付けず、vLLMサーバーrootだけを入力します。",
            "エンドポイントが一覧を返す場合、モデルはアルファベット順に並びます。",
            "Qwen3-Coderは --enable-auto-tool-choice --tool-call-parser qwen3_xml で起動してください。",
            "テスト成功後、フォーカスはClaude Code起動へ移動します。",
        ],
    },
    "zh": {
        "name": "中文",
        "welcome": "欢迎回来!",
        "tips_title": "入门提示",
        "status": ["provider: vllm", "language: zh", "mode: vllm-native", "base_url: http://127.0.0.1:8000", "model: qwen3-coder-30b"],
        "tips": [
            "启动前选择供应商、模型、Base URL 和 API 密钥。",
            "将 Claude Code 路由到 Anthropic、Ollama、vLLM、Nvidia 或 NIM。",
            "启动前检查 text、tool_use 和 tool_result 兼容性。",
        ],
        "menu": [
            "0. 语言  [中文]",
            "1. 供应商  [vllm]",
            "2. API 密钥  [可选]",
            "3. Base URL  [http://127.0.0.1:8000]",
            "4. 模型  [qwen3-coder-30b]",
            "5. LLM 选项  [ctx 65K; out 4K]",
            "6. 兼容性测试",
            "7. 启动 Claude Code",
            "退出",
        ],
        "provider": ["供应商菜单", "Anthropic", "Ollama", "Ollama Cloud", "vLLM [已选择]", "Nvidia Hosted", "Self Hosted NIM"],
        "base": ["Base URL", "http://127.0.0.1:8000", "输入服务器 root。Ciel Runtime 会调用 /v1/messages。"],
        "model": ["模型选择", "qwen3-coder-30b [已选择]", "qwen3.6-27b-nvfp4", "+ 自定义模型 id..."],
        "options": ["LLM 选项", "应用预设  [确定性编码]", "Context window  [65536]", "Context reserve  [4096]", "Max output tokens  [4096]", "Native compatibility  [True]"],
        "test": ["兼容性测试", "Runtime max_model_len: 65536", "vLLM hint: Qwen3-Coder -> qwen3_xml", "Text response: OK", "Tool use: OK", "Tool result: OK"],
        "footers": [
            "vLLM 需要 Anthropic 兼容 /v1/messages 和匹配模型的 tool calling 设置。",
            "不要附加 /v1，只输入 vLLM 服务器 root。",
            "如果端点能列出模型，模型会按字母顺序排序。",
            "Qwen3-Coder 请使用 --enable-auto-tool-choice --tool-call-parser qwen3_xml 启动。",
            "测试成功后，焦点会移动到启动 Claude Code。",
        ],
    },
}


def font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    candidates = [
        "C:/Windows/Fonts/malgunbd.ttf" if bold else "C:/Windows/Fonts/malgun.ttf",
        "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ]
    for candidate in candidates:
        path = Path(candidate)
        if path.exists():
            return ImageFont.truetype(str(path), size=size)
    return ImageFont.load_default()


FONT = font(28)
FONT_BOLD = font(28, bold=True)
FONT_BIG = font(34, bold=True)


def draw_text(draw: ImageDraw.ImageDraw, xy: tuple[int, int], text: str, color: str = "fg", big: bool = False) -> None:
    draw.text(xy, text, font=FONT_BIG if big else FONT, fill=COLORS[color])


def draw_header(draw: ImageDraw.ImageDraw, data: dict) -> None:
    draw.rectangle((10, 10, WIDTH - 10, 235), outline=COLORS["red"], width=2)
    draw_text(draw, (110, 38), "--- Ciel Runtime ---", "red", big=True)
    draw_text(draw, (135, 92), data["welcome"], "white", big=True)
    draw_text(draw, (165, 145), "CLAUDE", "green", big=True)
    draw_text(draw, (185, 185), "ANY", "yellow", big=True)
    draw.line((520, 40, 520, 205), fill=COLORS["red"], width=2)
    draw_text(draw, (560, 48), data["tips_title"], "red", big=True)
    for i, line in enumerate(data["tips"]):
        draw_text(draw, (560, 92 + i * 40), line, "fg")


def draw_status(draw: ImageDraw.ImageDraw, data: dict) -> None:
    y = 270
    for i, line in enumerate(data["status"]):
        color = "green" if i in (0, 4) else "fg"
        draw_text(draw, (40, y + i * 42), line, color)


def draw_menu(draw: ImageDraw.ImageDraw, data: dict, selected: int, extra: list[tuple[str, str]]) -> None:
    y = 495
    for i, item in enumerate(data["menu"]):
        row_y = y + i * 34
        if i == selected:
            draw.rectangle((35, row_y - 2, 760, row_y + 34), fill=COLORS["select_bg"])
            draw.text((48, row_y), item, font=FONT_BOLD, fill=COLORS["select_fg"])
        else:
            color = "green" if i == 7 else "yellow"
            draw_text(draw, (48, row_y), item, color)
    x = 815
    y2 = 492
    for label, color in extra:
        draw_text(draw, (x, y2), label, color)
        y2 += 42


def draw_footer(draw: ImageDraw.ImageDraw, line: str) -> None:
    draw.line((30, HEIGHT - 105, WIDTH - 30, HEIGHT - 105), fill=COLORS["orange"], width=2)
    draw_text(draw, (40, HEIGHT - 92), line, "orange")


def frame(data: dict, selected: int, extra_key: str, footer_idx: int) -> Image.Image:
    img = Image.new("RGB", (WIDTH, HEIGHT), COLORS["bg"])
    draw = ImageDraw.Draw(img)
    draw_header(draw, data)
    draw_status(draw, data)
    items = data[extra_key]
    extra = [(items[0], "red"), *[(item, "green" if "[" in item else "fg") for item in items[1:]]]
    draw_menu(draw, data, selected, extra)
    draw_footer(draw, data["footers"][footer_idx])
    return img


def save_mp4(frames: list[Image.Image], path: Path) -> None:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        return
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        for i, img in enumerate(frames):
            img.save(tmp / f"frame-{i:02d}.png")
        subprocess.run(
            [
                ffmpeg,
                "-y",
                "-framerate",
                "0.8",
                "-i",
                str(tmp / "frame-%02d.png"),
                "-vf",
                "scale=trunc(iw/2)*2:trunc(ih/2)*2,format=yuv420p",
                "-movflags",
                "+faststart",
                str(path),
            ],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )


def save_language_assets(code: str, data: dict) -> None:
    frames = [
        frame(data, 1, "provider", 0),
        frame(data, 3, "base", 1),
        frame(data, 4, "model", 2),
        frame(data, 5, "options", 3),
        frame(data, 6, "test", 4),
    ]
    suffix = f".{code}"
    names = [
        f"ciel-runtime-provider{suffix}.png",
        f"ciel-runtime-base-url{suffix}.png",
        f"ciel-runtime-model{suffix}.png",
        f"ciel-runtime-options{suffix}.png",
        f"ciel-runtime-test{suffix}.png",
    ]
    for img, name in zip(frames, names, strict=True):
        img.save(ASSET_DIR / name)
    frames[0].save(ASSET_DIR / f"ciel-runtime-main{suffix}.png")
    frames[0].save(
        ASSET_DIR / f"ciel-runtime-demo{suffix}.gif",
        save_all=True,
        append_images=frames[1:],
        duration=1250,
        loop=0,
        optimize=True,
    )
    save_mp4(frames, ASSET_DIR / f"ciel-runtime-demo{suffix}.mp4")


def main() -> None:
    ASSET_DIR.mkdir(parents=True, exist_ok=True)
    for code, data in LANG.items():
        save_language_assets(code, data)
    # Backward-compatible English defaults.
    for stem in ("main", "provider", "base-url", "model", "options", "test"):
        shutil.copyfile(ASSET_DIR / f"ciel-runtime-{stem}.en.png", ASSET_DIR / f"ciel-runtime-{stem}.png")
    shutil.copyfile(ASSET_DIR / "ciel-runtime-demo.en.gif", ASSET_DIR / "ciel-runtime-demo.gif")
    if (ASSET_DIR / "ciel-runtime-demo.en.mp4").exists():
        shutil.copyfile(ASSET_DIR / "ciel-runtime-demo.en.mp4", ASSET_DIR / "ciel-runtime-demo.mp4")
    print(f"Wrote localized demo assets to {ASSET_DIR}")


if __name__ == "__main__":
    main()
