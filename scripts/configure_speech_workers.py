"""Persist deployed speech worker URLs in the active Ciel Runtime config."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def configure(asr_base_url: str, tts_base_url: str) -> dict[str, Any]:
    import ciel_runtime

    config = ciel_runtime.load_config()
    speech = config.setdefault("speech", {})
    asr = speech.setdefault("asr", {})
    tts = speech.setdefault("tts", {})
    asr.update({"enabled": True, "base_url": asr_base_url.rstrip("/"), "model": "Qwen/Qwen3-ASR-0.6B"})
    tts.update({"enabled": True, "base_url": tts_base_url.rstrip("/"), "model": "OpenMOSS-Team/MOSS-TTS-Nano"})
    ciel_runtime.save_config(config)
    return {"asr": asr["base_url"], "tts": tts["base_url"]}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--asr-base-url", required=True)
    parser.add_argument("--tts-base-url", required=True)
    args = parser.parse_args()
    result = configure(args.asr_base_url, args.tts_base_url)
    print(f"Configured Ciel speech workers: ASR={result['asr']} TTS={result['tts']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
