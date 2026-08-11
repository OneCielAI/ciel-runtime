"""Shared speech model defaults and reference-pair validation."""

from __future__ import annotations

from typing import Any


MOSS_TTS_MODEL = "OpenMOSS-Team/MOSS-TTS-Nano"
COSYVOICE3_MODEL = "FunAudioLLM/Fun-CosyVoice3-0.5B-2512"
DEFAULT_TTS_REFERENCE_AUDIO = "https://raw.githubusercontent.com/OpenMOSS/MOSS-TTS-Nano/main/assets/audio/zh_1.wav"
DEFAULT_COSYVOICE_REFERENCE_AUDIO = "https://raw.githubusercontent.com/QwenAudio/CosyVoice/main/asset/zero_shot_prompt.wav"
DEFAULT_COSYVOICE_REFERENCE_TEXT = "希望你以后能够做的比我还好呦。"


def is_cosyvoice3_model(model: Any) -> bool:
    return "cosyvoice3" in str(model or "").lower()


def reference_audio_source(config: dict[str, Any]) -> str:
    audio = str(config.get("ref_audio") or "").strip()
    text = str(config.get("ref_text") or "").strip()
    if not audio:
        return "none"
    if (
        audio == DEFAULT_COSYVOICE_REFERENCE_AUDIO
        and text == DEFAULT_COSYVOICE_REFERENCE_TEXT
    ) or audio == DEFAULT_TTS_REFERENCE_AUDIO:
        return "default"
    return "custom"


def normalize_cosyvoice_reference(config: dict[str, Any]) -> bool:
    """Repair legacy/default reference state and reject incomplete custom pairs."""
    if not is_cosyvoice3_model(config.get("model")):
        return False
    audio = str(config.get("ref_audio") or "").strip()
    text = str(config.get("ref_text") or "").strip()
    known_default_audio = {"", DEFAULT_TTS_REFERENCE_AUDIO, DEFAULT_COSYVOICE_REFERENCE_AUDIO}
    if audio in known_default_audio:
        changed = audio != DEFAULT_COSYVOICE_REFERENCE_AUDIO or text != DEFAULT_COSYVOICE_REFERENCE_TEXT
        config["ref_audio"] = DEFAULT_COSYVOICE_REFERENCE_AUDIO
        config["ref_text"] = DEFAULT_COSYVOICE_REFERENCE_TEXT
        return changed
    if not text:
        raise ValueError("CosyVoice 3 custom ref_audio requires its exact ref_text transcript")
    return False
