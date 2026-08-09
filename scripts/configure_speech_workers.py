"""Persist deployed speech worker URLs in the active Ciel Runtime config."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


DEFAULT_TTS_REFERENCE_AUDIO = "https://raw.githubusercontent.com/OpenMOSS/MOSS-TTS-Nano/main/assets/audio/zh_1.wav"


def configure(asr_base_url: str, tts_base_url: str, tts_reference_audio: str = DEFAULT_TTS_REFERENCE_AUDIO) -> dict[str, Any]:
    import ciel_runtime

    config = ciel_runtime.load_config()
    speech = config.setdefault("speech", {})
    asr = speech.setdefault("asr", {})
    tts = speech.setdefault("tts", {})
    asr.update({"enabled": True, "base_url": asr_base_url.rstrip("/"), "model": "Qwen/Qwen3-ASR-0.6B"})
    tts.update({"enabled": True, "base_url": tts_base_url.rstrip("/"), "model": "OpenMOSS-Team/MOSS-TTS-Nano"})
    if tts_reference_audio and not str(tts.get("ref_audio") or "").strip():
        tts["ref_audio"] = tts_reference_audio
    ciel_runtime.save_config(config)
    return {"asr": asr["base_url"], "tts": tts["base_url"]}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--asr-base-url", required=True)
    parser.add_argument("--tts-base-url", required=True)
    parser.add_argument("--tts-reference-audio", default=DEFAULT_TTS_REFERENCE_AUDIO)
    args = parser.parse_args()
    result = configure(args.asr_base_url, args.tts_base_url, args.tts_reference_audio)
    print(f"Configured Ciel speech workers: ASR={result['asr']} TTS={result['tts']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
