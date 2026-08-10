"""Persist deployed speech worker URLs in the active Ciel Runtime config."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


DEFAULT_TTS_REFERENCE_AUDIO = "https://raw.githubusercontent.com/OpenMOSS/MOSS-TTS-Nano/main/assets/audio/zh_1.wav"
DEFAULT_COSYVOICE_REFERENCE_AUDIO = "https://raw.githubusercontent.com/QwenAudio/CosyVoice/main/asset/zero_shot_prompt.wav"
DEFAULT_COSYVOICE_REFERENCE_TEXT = "希望你以后能够做的比我还好呦。"
TTS_BACKENDS = {
    "moss": {"model": "OpenMOSS-Team/MOSS-TTS-Nano", "sample_rate": 48000, "streaming": False},
    "cosyvoice3": {"model": "FunAudioLLM/Fun-CosyVoice3-0.5B-2512", "sample_rate": 24000, "streaming": True},
}
ASR_MODELS = {"Qwen/Qwen3-ASR-0.6B", "Qwen/Qwen3-ASR-1.7B"}
DEFAULT_COLAB_SETTINGS: dict[str, Any] = {
    "enabled": True,
    "distribution": "Ubuntu-26.04",
    "auth": "adc",
    "profile": "default",
    "asr_session": "ciel-asr",
    "tts_session": "ciel-tts",
    "asr_model": "Qwen/Qwen3-ASR-0.6B",
    "asr_accelerator": "T4",
    "tts_accelerator": "T4",
    "tts_backend": "moss",
}


def colab_settings(config: dict[str, Any] | None = None) -> dict[str, Any]:
    import ciel_runtime

    active = config if config is not None else ciel_runtime.load_config()
    speech = active.get("speech") if isinstance(active.get("speech"), dict) else {}
    saved = speech.get("colab") if isinstance(speech.get("colab"), dict) else {}
    return {**DEFAULT_COLAB_SETTINGS, **saved}


def configure(
    asr_base_url: str,
    tts_base_url: str,
    tts_reference_audio: str = DEFAULT_TTS_REFERENCE_AUDIO,
    *,
    distribution: str | None = None,
    auth: str | None = None,
    profile: str | None = None,
    asr_session: str | None = None,
    tts_session: str | None = None,
    asr_model: str | None = None,
    asr_accelerator: str | None = None,
    tts_accelerator: str | None = None,
    tts_backend: str | None = None,
) -> dict[str, Any]:
    import ciel_runtime

    config = ciel_runtime.load_config()
    speech = config.setdefault("speech", {})
    asr = speech.setdefault("asr", {})
    tts = speech.setdefault("tts", {})
    colab = colab_settings(config)
    overrides = {
        "distribution": distribution,
        "auth": auth,
        "profile": profile,
        "asr_session": asr_session,
        "tts_session": tts_session,
        "asr_model": asr_model,
        "asr_accelerator": asr_accelerator,
        "tts_accelerator": tts_accelerator,
        "tts_backend": tts_backend,
    }
    colab.update({key: value for key, value in overrides.items() if value is not None})
    colab["enabled"] = True
    backend = str(colab.get("tts_backend") or "moss").strip().lower()
    if backend not in TTS_BACKENDS:
        raise ValueError(f"unsupported TTS backend: {backend}")
    backend_settings = TTS_BACKENDS[backend]
    selected_asr_model = str(colab.get("asr_model") or "Qwen/Qwen3-ASR-0.6B").strip()
    if selected_asr_model not in ASR_MODELS:
        raise ValueError(f"unsupported ASR model: {selected_asr_model}")
    speech["colab"] = colab
    asr.update({"enabled": True, "base_url": asr_base_url.rstrip("/"), "model": selected_asr_model})
    tts.update({"enabled": True, "base_url": tts_base_url.rstrip("/"), **backend_settings})
    known_defaults = {DEFAULT_TTS_REFERENCE_AUDIO, DEFAULT_COSYVOICE_REFERENCE_AUDIO, ""}
    current_reference = str(tts.get("ref_audio") or "").strip()
    desired_reference = DEFAULT_COSYVOICE_REFERENCE_AUDIO if backend == "cosyvoice3" else tts_reference_audio
    if desired_reference and current_reference in known_defaults:
        tts["ref_audio"] = desired_reference
    if backend == "cosyvoice3" and current_reference in known_defaults and not str(tts.get("ref_text") or "").strip():
        tts["ref_text"] = DEFAULT_COSYVOICE_REFERENCE_TEXT
    ciel_runtime.save_config(config)
    return {"asr": asr["base_url"], "tts": tts["base_url"], "colab": colab}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--asr-base-url")
    parser.add_argument("--tts-base-url")
    parser.add_argument("--tts-reference-audio", default=DEFAULT_TTS_REFERENCE_AUDIO)
    parser.add_argument("--distribution")
    parser.add_argument("--auth", choices=("adc", "oauth2"))
    parser.add_argument("--profile")
    parser.add_argument("--asr-session")
    parser.add_argument("--tts-session")
    parser.add_argument("--asr-model", choices=tuple(sorted(ASR_MODELS)))
    parser.add_argument("--asr-accelerator")
    parser.add_argument("--tts-accelerator")
    parser.add_argument("--tts-backend", choices=tuple(TTS_BACKENDS))
    parser.add_argument("--print-colab-settings", action="store_true")
    args = parser.parse_args()
    if args.print_colab_settings:
        print(json.dumps(colab_settings(), separators=(",", ":")))
        return 0
    if not args.asr_base_url or not args.tts_base_url:
        parser.error("--asr-base-url and --tts-base-url are required unless --print-colab-settings is used")
    result = configure(
        args.asr_base_url,
        args.tts_base_url,
        args.tts_reference_audio,
        distribution=args.distribution,
        auth=args.auth,
        profile=args.profile,
        asr_session=args.asr_session,
        tts_session=args.tts_session,
        asr_model=args.asr_model,
        asr_accelerator=args.asr_accelerator,
        tts_accelerator=args.tts_accelerator,
        tts_backend=args.tts_backend,
    )
    print(f"Configured Ciel speech workers: ASR={result['asr']} TTS={result['tts']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
