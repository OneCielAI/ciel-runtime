"""Speech configuration and OpenAI-compatible ASR/TTS proxy endpoints."""

from __future__ import annotations

import base64
from contextlib import contextmanager
import json
import os
import re
import secrets
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler
from collections.abc import Iterator
from typing import Any, Callable

from ciel_runtime_support.speech_models import (
    is_cosyvoice3_model,
    normalize_cosyvoice_reference,
    reference_audio_source,
)
from ciel_runtime_support.request_limits_config import (
    MIB,
    WorkspaceRequestLimits,
    format_mib,
    resolve_workspace_request_limits,
)
from ciel_runtime_support.request_body_policy import (
    RequestBodyCapacityExceeded,
    RequestBodyTooLarge,
)
from ciel_runtime_support.tts_reference_audio_repository import (
    TtsReferenceAudioRepository,
)


SpeechConfig = dict[str, Any]
MAX_SPEECH_AUDIO_BYTES = 500 * MIB
MAX_TTS_REFERENCE_AUDIO_BYTES = 500 * MIB


def _maximum_base64_characters(decoded_bytes: int) -> int:
    return 4 * ((decoded_bytes + 2) // 3)


def _decode_bounded_base64(value: Any, maximum: int, label: str) -> bytes:
    text = str(value or "")
    if len(text) > _maximum_base64_characters(maximum):
        raise OverflowError(f"{label} exceeds {maximum} decoded bytes")
    try:
        decoded = base64.b64decode(text, validate=True)
    except (ValueError, TypeError, UnicodeEncodeError) as exc:
        raise ValueError(f"invalid base64 {label}") from exc
    if len(decoded) > maximum:
        raise OverflowError(f"{label} exceeds {maximum} decoded bytes")
    return decoded


@dataclass(frozen=True, slots=True)
class SpeechHttpPorts:
    load_config: Callable[[], dict[str, Any]]
    save_config: Callable[[dict[str, Any]], None]
    write_json: Callable[..., None]
    log: Callable[[str, str], None]
    urlopen: Callable[..., Any] = urllib.request.urlopen
    colab_action: Callable[[str, dict[str, Any], dict[str, str]], dict[str, Any]] | None = None
    colab_status: Callable[[str], dict[str, Any]] | None = None
    colab_credentials: Callable[[str], dict[str, bool]] | None = None
    request_limits: Callable[[], WorkspaceRequestLimits] | None = None
    reference_audio_repository: TtsReferenceAudioRepository | None = None


@dataclass(frozen=True, slots=True)
class SpeechHttpController:
    ports: SpeechHttpPorts

    def get(self, handler: BaseHTTPRequestHandler, path: str) -> bool:
        if path == "/ca/speech/config":
            self.ports.write_json(handler, self.public_config())
            return True
        if path == "/ca/speech/health":
            self.ports.write_json(handler, self.health_payload())
            return True
        if path == "/ca/speech/colab/job":
            payload = self.ports.colab_status("") if self.ports.colab_status else {"ok": True, "job": None}
            self.ports.write_json(handler, payload)
            return True
        if path == "/ca/web/chat/api":
            self.ports.write_json(handler, self.discovery_payload())
            return True
        if path == "/v1/audio/voices":
            return self._proxy_get(handler, "tts", "voices_endpoint")
        return False

    def post(
        self,
        handler: BaseHTTPRequestHandler,
        path: str,
        raw: bytes,
        content_type: str,
    ) -> bool:
        if path == "/ca/speech/config":
            return self._save_public_config(handler, raw)
        if path == "/ca/speech/colab/action":
            return self._start_colab_action(handler, raw)
        if path in {"/v1/audio/transcriptions", "/v1/audio/translations"}:
            return self._proxy_asr(handler, raw, content_type)
        if path in {"/v1/audio/speech", "/v1/audio/speech/batch"}:
            return self._proxy_tts(handler, raw, content_type, batch=path.endswith("/batch"))
        if path == "/v1/audio/voices":
            return self._proxy_raw(handler, "tts", "voices_endpoint", raw, content_type)
        return False

    def public_config(self) -> dict[str, Any]:
        speech = self._speech_config()
        limits = self._request_limits()
        public: dict[str, Any] = {
            "ok": True,
            "limits": {
                "speech_audio_max_bytes": limits.speech_audio_max_bytes,
                "tts_reference_audio_max_bytes": limits.tts_reference_audio_max_bytes,
            },
        }
        for name in ("asr", "tts"):
            source = speech.get(name) if isinstance(speech.get(name), dict) else {}
            item = {key: value for key, value in source.items() if key not in {"api_key", "ref_audio"}}
            item["api_key_set"] = bool(str(source.get("api_key") or "").strip())
            if name == "tts":
                item["ref_audio_set"] = bool(str(source.get("ref_audio") or "").strip())
                item["ref_audio_source"] = reference_audio_source(source)
            public[name] = item
        tailscale = speech.get("tailscale")
        public["tailscale"] = dict(tailscale) if isinstance(tailscale, dict) else {}
        colab = speech.get("colab")
        public["colab"] = dict(colab) if isinstance(colab, dict) else {}
        if self.ports.colab_credentials:
            profile = str(public["colab"].get("profile") or "default")
            public["colab"].update(self.ports.colab_credentials(profile))
        public["endpoints"] = self.discovery_payload()["endpoints"]
        return public

    def discovery_payload(self) -> dict[str, Any]:
        return {
            "ok": True,
            "web_chat": "/ca/web/chat",
            "endpoints": {
                "chat_health": "GET /ca/channel/health",
                "chat_messages": "GET|POST /ca/channel/messages",
                "chat_message_injection_modes": {
                    "parameter": "injection_mode",
                    "default": "web_chat",
                    "allowed": ["web_chat", "tty"],
                    "tty_note": "Private plain TTY injection; not published to the Web Chat transcript.",
                },
                "chat_wait": "GET /ca/channel/wait",
                "chat_stream": "GET /ca/channel/stream",
                "chat_files": "POST /ca/channel/files",
                "speech_config": "GET|POST /ca/speech/config",
                "speech_health": "GET /ca/speech/health",
                "colab_action": "POST /ca/speech/colab/action",
                "colab_job": "GET /ca/speech/colab/job",
                "asr": "POST /v1/audio/transcriptions",
                "asr_translate": "POST /v1/audio/translations",
                "tts": "POST /v1/audio/speech",
                "tts_batch": "POST /v1/audio/speech/batch",
                "tts_voices": "GET|POST /v1/audio/voices",
                "models": "GET /v1/models",
                "responses": "POST /v1/responses",
                "messages": "POST /v1/messages",
                "tui_status": "GET /ca/tui/status",
                "tui_recent": "GET /ca/tui/recent",
                "tui_stream": "GET /ca/tui/stream",
                "tui_monitor": "GET /ca/tui",
            },
        }

    def health_payload(self) -> dict[str, Any]:
        services = {name: self._probe(name) for name in ("asr", "tts")}
        return {"ok": all(not item["enabled"] or item["reachable"] for item in services.values()), "services": services}

    def _speech_config(self) -> SpeechConfig:
        speech = self.ports.load_config().get("speech")
        return speech if isinstance(speech, dict) else {}

    def _request_limits(self) -> WorkspaceRequestLimits:
        if self.ports.request_limits is not None:
            return self.ports.request_limits()
        return resolve_workspace_request_limits(
            self.ports.load_config(),
            "",
            os.environ,
        )

    def _service_config(self, name: str) -> SpeechConfig:
        service = self._speech_config().get(name)
        return service if isinstance(service, dict) else {}

    def _save_public_config(self, handler: BaseHTTPRequestHandler, raw: bytes) -> bool:
        pending_reference_markers: list[str] = []
        config_saved = False
        try:
            update = json.loads(raw.decode("utf-8") if raw else "{}")
            if not isinstance(update, dict):
                raise ValueError("configuration body must be a JSON object")
            config = self.ports.load_config()
            speech = config.setdefault("speech", {})
            if not isinstance(speech, dict):
                speech = {}
                config["speech"] = speech
            previous_tts = speech.get("tts")
            previous_reference = (
                str(previous_tts.get("ref_audio") or "").strip()
                if isinstance(previous_tts, dict)
                else ""
            )
            for name in ("asr", "tts"):
                incoming = update.get(name)
                if not isinstance(incoming, dict):
                    continue
                current = speech.setdefault(name, {})
                if not isinstance(current, dict):
                    current = {}
                    speech[name] = current
                for key, value in incoming.items():
                    if key in {"api_key_set", "clear_api_key", "ref_audio_set", "clear_ref_audio"}:
                        continue
                    if key in {"api_key", "ref_audio"} and not str(value or "").strip():
                        continue
                    current[key] = self._validated_value(name, key, value)
                if incoming.get("clear_api_key") is True:
                    current["api_key"] = ""
                if name == "tts" and incoming.get("clear_ref_audio") is True:
                    current["ref_audio"] = ""
                    current["ref_text"] = ""
                if name == "tts":
                    normalize_cosyvoice_reference(current)
            current_tts = speech.get("tts")
            if isinstance(current_tts, dict):
                reference = str(current_tts.get("ref_audio") or "").strip()
                repository = self.ports.reference_audio_repository
                if (
                    repository is not None
                    and reference.startswith("data:audio/")
                    and ";base64," in reference
                ):
                    marker = repository.store_data_url(
                        reference,
                        self._request_limits().tts_reference_audio_max_bytes,
                    )
                    current_tts["ref_audio"] = marker
                    pending_reference_markers.append(marker)
            tailscale = update.get("tailscale")
            if isinstance(tailscale, dict):
                current_tailscale = speech.setdefault("tailscale", {})
                if not isinstance(current_tailscale, dict):
                    current_tailscale = {}
                    speech["tailscale"] = current_tailscale
                for key in ("enabled", "asr_hostname", "tts_hostname"):
                    if key in tailscale:
                        current_tailscale[key] = tailscale[key]
            colab = update.get("colab")
            if isinstance(colab, dict):
                current_colab = speech.setdefault("colab", {})
                if not isinstance(current_colab, dict):
                    current_colab = {}
                    speech["colab"] = current_colab
                for key, value in colab.items():
                    current_colab[key] = self._validated_colab_value(key, value)
            self.ports.save_config(config)
            config_saved = True
            repository = self.ports.reference_audio_repository
            current_reference = (
                str(current_tts.get("ref_audio") or "").strip()
                if isinstance(current_tts, dict)
                else ""
            )
            if repository is not None and previous_reference != current_reference:
                self._discard_reference_best_effort(previous_reference)
            self.ports.write_json(handler, self.public_config())
        except OverflowError as exc:
            self.ports.write_json(
                handler,
                {"ok": False, "type": "request_too_large", "error": str(exc)},
                413,
            )
        except (UnicodeError, ValueError, TypeError) as exc:
            self.ports.write_json(handler, {"ok": False, "error": str(exc)}, 400)
        except (OSError, RuntimeError) as exc:
            self.ports.log(
                "ERROR",
                f"tts_reference_audio_persistence_failed error={type(exc).__name__}: {exc}",
            )
            self.ports.write_json(
                handler,
                {"ok": False, "error": "TTS reference audio could not be stored securely"},
                500,
            )
        finally:
            if not config_saved and self.ports.reference_audio_repository is not None:
                for marker in pending_reference_markers:
                    self._discard_reference_best_effort(marker)
        return True

    def _discard_reference_best_effort(self, reference: str) -> None:
        repository = self.ports.reference_audio_repository
        if repository is None:
            return
        try:
            repository.discard(reference)
        except OSError as exc:
            # A concurrent Windows forward may still have the immutable file
            # open.  The saved config is authoritative; leaving an orphan is
            # safer than turning a successful save into a false failure.
            self.ports.log(
                "WARN",
                "tts_reference_audio_cleanup_deferred "
                f"error={type(exc).__name__}: {exc}",
            )

    def _start_colab_action(self, handler: BaseHTTPRequestHandler, raw: bytes) -> bool:
        try:
            if self.ports.colab_action is None:
                raise RuntimeError("Colab deployment jobs are unavailable")
            body = json.loads(raw.decode("utf-8") if raw else "{}")
            if not isinstance(body, dict):
                raise ValueError("request must be a JSON object")
            action = str(body.get("action") or "").strip().lower()
            config = self._speech_config()
            colab = config.get("colab") if isinstance(config.get("colab"), dict) else {}
            if colab.get("enabled") is False:
                raise ValueError("Colab worker management is disabled")
            secrets_payload = body.get("secrets") if isinstance(body.get("secrets"), dict) else {}
            secrets = {
                "tailscale_auth_key": str(secrets_payload.get("tailscale_auth_key") or ""),
                "speech_api_key": str(secrets_payload.get("speech_api_key") or ""),
                "reset_authentication": "1" if body.get("reset_authentication") is True else "",
                "forget_saved_credentials": "1" if body.get("forget_saved_credentials") is True else "",
            }
            result = self.ports.colab_action(action, dict(colab), secrets)
            self.ports.write_json(handler, result)
        except (UnicodeError, json.JSONDecodeError, ValueError, TypeError) as exc:
            self.ports.write_json(handler, {"ok": False, "error": str(exc)}, 400)
        except RuntimeError as exc:
            self.ports.write_json(handler, {"ok": False, "error": str(exc)}, 409)
        return True

    def _validated_value(self, service: str, key: str, value: Any) -> Any:
        allowed = {
            "asr": {"enabled", "base_url", "endpoint", "model", "language", "silence_ms", "min_speech_ms", "vad_threshold", "api_key", "timeout_seconds"},
            "tts": {"enabled", "base_url", "endpoint", "voices_endpoint", "model", "voice", "language", "ref_audio", "ref_text", "response_format", "speed", "auto_speak", "streaming", "sample_rate", "api_key", "timeout_seconds"},
        }
        if key not in allowed[service]:
            raise ValueError(f"unsupported {service} setting: {key}")
        if key in {"enabled", "auto_speak", "streaming"}:
            return bool(value)
        if key == "sample_rate":
            return max(8000, min(192000, int(value)))
        if key == "timeout_seconds":
            return max(1, min(3600, int(value)))
        if key == "speed":
            return max(0.25, min(4.0, float(value)))
        if key == "silence_ms":
            return max(250, min(3000, int(value)))
        if key == "min_speech_ms":
            return max(100, min(2000, int(value)))
        if key == "vad_threshold":
            return max(0.005, min(0.2, float(value)))
        text = str(value or "").strip()
        if key == "ref_audio":
            maximum = self._request_limits().tts_reference_audio_max_bytes
            if len(text) > _maximum_base64_characters(maximum) + 256:
                raise ValueError(
                    f"TTS reference audio must be {format_mib(maximum)} or smaller"
                )
            if text.startswith("data:audio/") and ";base64," in text:
                if self.ports.reference_audio_repository is not None:
                    # The repository validates and decodes exactly once while
                    # atomically writing the binary sidecar.
                    return text
                try:
                    audio = _decode_bounded_base64(
                        text.split(",", 1)[1],
                        maximum,
                        "TTS reference audio",
                    )
                except (ValueError, OverflowError) as exc:
                    raise ValueError(
                        f"invalid base64 TTS reference audio; maximum is "
                        f"{format_mib(maximum)}"
                    ) from exc
                if not audio:
                    raise ValueError(
                        "TTS reference audio must be between 1 byte and "
                        f"{format_mib(maximum)}"
                    )
                return text
            parsed_ref = urllib.parse.urlparse(text)
            if parsed_ref.scheme not in {"http", "https"} or not parsed_ref.netloc:
                raise ValueError("TTS ref_audio must be an audio data URL or HTTP(S) URL")
            return text
        if key == "base_url":
            parsed = urllib.parse.urlparse(text)
            if parsed.scheme not in {"http", "https"} or not parsed.netloc:
                raise ValueError(f"invalid {service} base_url")
            return text.rstrip("/")
        if key in {"endpoint", "voices_endpoint"} and not text.startswith("/"):
            raise ValueError(f"{service} {key} must begin with /")
        return text

    @staticmethod
    def _validated_colab_value(key: str, value: Any) -> Any:
        allowed = {
            "enabled",
            "distribution",
            "auth",
            "profile",
            "asr_session",
            "tts_session",
            "asr_model",
            "asr_accelerator",
            "tts_accelerator",
            "tts_backend",
        }
        if key not in allowed:
            raise ValueError(f"unsupported colab setting: {key}")
        if key == "enabled":
            return bool(value)
        text = str(value or "").strip()
        if key == "auth":
            auth = text.lower()
            if auth not in {"adc", "oauth2"}:
                raise ValueError("Colab auth must be adc or oauth2")
            return auth
        if key.endswith("_accelerator"):
            accelerator = text.upper()
            if accelerator not in {"T4", "L4", "G4", "A100", "H100"}:
                raise ValueError("unsupported Colab accelerator")
            return accelerator
        if key == "tts_backend":
            backend = text.lower()
            if backend not in {"moss", "cosyvoice3"}:
                raise ValueError("unsupported Colab TTS backend")
            return backend
        if key == "asr_model":
            if text not in {"Qwen/Qwen3-ASR-0.6B", "Qwen/Qwen3-ASR-1.7B"}:
                raise ValueError("unsupported Colab ASR model")
            return text
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", text):
            raise ValueError(f"invalid Colab {key}")
        return text

    def _probe(self, name: str) -> dict[str, Any]:
        config = self._service_config(name)
        enabled = bool(config.get("enabled"))
        result: dict[str, Any] = {
            "enabled": enabled,
            "configured": bool(str(config.get("base_url") or "").strip()),
            "reachable": False,
        }
        if not enabled:
            return result
        try:
            request = urllib.request.Request(self._url(config, "/health"), headers=self._headers(config, "application/json"))
            with self.ports.urlopen(request, timeout=min(10.0, self._timeout(config))) as response:
                result["status"] = int(getattr(response, "status", 200))
                result["reachable"] = result["status"] < 500
        except Exception as exc:
            result["error"] = f"{type(exc).__name__}: {exc}"
        return result

    def _proxy_get(self, handler: BaseHTTPRequestHandler, service: str, endpoint_key: str) -> bool:
        config = self._service_config(service)
        if not self._require_enabled(handler, service, config):
            return True
        request = urllib.request.Request(self._url(config, str(config.get(endpoint_key) or "")), headers=self._headers(config, "application/json"))
        return self._open_and_write(handler, service, config, request)

    def _proxy_asr(self, handler: BaseHTTPRequestHandler, raw: bytes, content_type: str) -> bool:
        config = self._service_config("asr")
        if not self._require_enabled(handler, "asr", config):
            return True
        maximum = self._request_limits().speech_audio_max_bytes
        if "application/json" in content_type.lower():
            try:
                body = json.loads(raw.decode("utf-8"))
                if not isinstance(body, dict):
                    raise ValueError("request must be a JSON object")
                audio = _decode_bounded_base64(
                    body.get("audio_base64"),
                    maximum,
                    "audio_base64",
                )
                if not audio:
                    raise ValueError("audio_base64 is required")
                fields = {
                    "model": str(body.get("model") or config.get("model") or ""),
                    "language": str(body.get("language") or config.get("language") or ""),
                    "response_format": str(body.get("response_format") or "json"),
                }
                if fields["language"].lower() == "auto":
                    fields.pop("language")
                raw, content_type = self._multipart(fields, "file", str(body.get("filename") or "recording.wav"), str(body.get("content_type") or "audio/wav"), audio)
            except OverflowError as exc:
                self.ports.write_json(handler, {"error": {"type": "request_too_large", "message": str(exc)}}, 413)
                return True
            except (ValueError, TypeError, UnicodeError) as exc:
                self.ports.write_json(handler, {"error": {"type": "invalid_request_error", "message": str(exc)}}, 400)
                return True
        elif len(raw) > maximum:
            self.ports.write_json(
                handler,
                {
                    "error": {
                        "type": "request_too_large",
                        "message": (
                            f"speech input exceeds {format_mib(maximum)} decoded bytes"
                        ),
                    }
                },
                413,
            )
            return True
        return self._proxy_raw(handler, "asr", "endpoint", raw, content_type)

    def _proxy_tts(self, handler: BaseHTTPRequestHandler, raw: bytes, content_type: str, *, batch: bool) -> bool:
        config = self._service_config("tts")
        streaming = False
        body: dict[str, Any] | None = None
        configured_reference_injected = False
        if not self._require_enabled(handler, "tts", config):
            return True
        if "application/json" in content_type.lower():
            try:
                reference_maximum = self._request_limits().tts_reference_audio_max_bytes
                body = json.loads(raw.decode("utf-8") if raw else "{}")
                if not isinstance(body, dict):
                    raise ValueError("request must be a JSON object")
                if not batch:
                    body.setdefault("model", str(config.get("model") or ""))
                    body.setdefault("voice", str(config.get("voice") or "default"))
                    body.setdefault("language", str(config.get("language") or "Auto"))
                    request_has_ref_audio = bool(str(body.get("ref_audio") or "").strip())
                    request_has_ref_text = bool(str(body.get("ref_text") or "").strip())
                    if is_cosyvoice3_model(body.get("model")) and request_has_ref_audio != request_has_ref_text:
                        raise ValueError("CosyVoice 3 request must provide both ref_audio and its exact ref_text transcript")
                    if not request_has_ref_audio and not request_has_ref_text:
                        effective_config = dict(config)
                        normalize_cosyvoice_reference(effective_config)
                        if str(effective_config.get("ref_audio") or "").strip():
                            body["ref_audio"] = str(effective_config["ref_audio"])
                            configured_reference_injected = True
                        if str(effective_config.get("ref_text") or "").strip():
                            body["ref_text"] = str(effective_config["ref_text"])
                    if is_cosyvoice3_model(body.get("model")) and (
                        not str(body.get("ref_audio") or "").strip()
                        or not str(body.get("ref_text") or "").strip()
                    ):
                        raise ValueError("CosyVoice 3 reference is empty; start live voice once to enroll it automatically")
                    body.setdefault("response_format", str(config.get("response_format") or "wav"))
                    body.setdefault("speed", float(config.get("speed") or 1.0))
                    streaming = bool(body.get("stream") and body.get("stream_format") == "audio")
                self._validate_tts_reference_audio(
                    body,
                    reference_maximum,
                    batch=batch,
                    skip_top_reference=(
                        configured_reference_injected
                        and self.ports.reference_audio_repository is not None
                    ),
                )
            except OverflowError as exc:
                self.ports.write_json(handler, {"error": {"type": "request_too_large", "message": str(exc)}}, 413)
                return True
            except (ValueError, TypeError, UnicodeError) as exc:
                self.ports.write_json(handler, {"error": {"type": "invalid_request_error", "message": str(exc)}}, 400)
                return True
        endpoint = str(config.get("endpoint") or "/v1/audio/speech") + ("/batch" if batch else "")
        try:
            with self._materialized_tts_body(
                body,
                raw,
                reference_maximum=self._request_limits().tts_reference_audio_max_bytes,
                batch=batch,
                configured_reference_injected=configured_reference_injected,
            ) as forwarding_raw:
                return self._proxy_bytes(
                    handler,
                    "tts",
                    config,
                    endpoint,
                    forwarding_raw,
                    content_type,
                    streaming=streaming,
                )
        except (RequestBodyTooLarge, RequestBodyCapacityExceeded):
            raise
        except OverflowError as exc:
            self.ports.write_json(handler, {"error": {"type": "request_too_large", "message": str(exc)}}, 413)
            return True
        except (ValueError, TypeError, UnicodeError) as exc:
            self.ports.write_json(handler, {"error": {"type": "invalid_request_error", "message": str(exc)}}, 400)
            return True

    @contextmanager
    def _materialized_tts_body(
        self,
        body: dict[str, Any] | None,
        raw: bytes,
        *,
        reference_maximum: int,
        batch: bool,
        configured_reference_injected: bool,
    ) -> Iterator[bytes]:
        if body is None:
            yield raw
            return
        repository = self.ports.reference_audio_repository
        containers: list[tuple[dict[str, Any], bool]] = [
            (body, configured_reference_injected)
        ]
        if batch and isinstance(body.get("items"), list):
            containers.extend(
                (item, False)
                for item in body["items"]
                if isinstance(item, dict)
            )
        marker_container: dict[str, Any] | None = None
        marker = ""
        for container, marker_allowed in containers:
            reference = str(container.get("ref_audio") or "").strip()
            if repository is None or not repository.is_marker(reference):
                continue
            if not marker_allowed:
                raise ValueError(
                    "opaque TTS reference markers are not accepted from API clients"
                )
            if marker_container is not None:
                raise ValueError("only one configured TTS reference marker is supported")
            marker_container = container
            marker = reference

        path = "/v1/audio/speech/batch" if batch else "/v1/audio/speech"
        if repository is None:
            yield json.dumps(body, ensure_ascii=False).encode("utf-8")
            return
        configured_reference = str(body.get("ref_audio") or "").strip()
        if (
            marker_container is None
            and configured_reference_injected
            and configured_reference.startswith("data:audio/")
            and ";base64," in configured_reference
        ):
            data_url_length = repository.data_url_wire_length(
                configured_reference,
                reference_maximum,
            )
            body["ref_audio"] = ""
            try:
                skeleton_length = len(
                    json.dumps(body, ensure_ascii=False).encode("utf-8")
                )
            finally:
                body["ref_audio"] = configured_reference
            transformed_length = skeleton_length + data_url_length
            with repository.admit_transformed(
                path,
                len(raw),
                transformed_length,
                "application/json",
            ):
                self._validate_tts_reference_audio(
                    body,
                    reference_maximum,
                    batch=batch,
                )
                forwarding_raw = json.dumps(body, ensure_ascii=False).encode("utf-8")
                if len(forwarding_raw) != transformed_length:
                    raise RuntimeError("TTS reference transformed length mismatch")
                yield forwarding_raw
            return
        if marker_container is None:
            forwarding_raw = json.dumps(body, ensure_ascii=False).encode("utf-8")
            with repository.admit_transformed(
                path,
                len(raw),
                len(forwarding_raw),
                "application/json",
            ):
                yield forwarding_raw
            return

        marker_container["ref_audio"] = ""
        try:
            skeleton_length = len(
                json.dumps(body, ensure_ascii=False).encode("utf-8")
            )
        finally:
            marker_container["ref_audio"] = marker

        def transformed_length(data_url_length: int) -> int:
            return skeleton_length + data_url_length

        with repository.materialize_data_url(
            marker,
            reference_maximum,
            path=path,
            original_length=len(raw),
            transformed_length=transformed_length,
            content_type="application/json",
        ) as data_url:
            marker_container["ref_audio"] = data_url
            try:
                forwarding_raw = json.dumps(body, ensure_ascii=False).encode("utf-8")
                if len(forwarding_raw) != transformed_length(len(data_url)):
                    raise RuntimeError("TTS reference transformed length mismatch")
                yield forwarding_raw
            finally:
                marker_container["ref_audio"] = marker

    @staticmethod
    def _validate_tts_reference_audio(
        body: dict[str, Any],
        maximum: int,
        *,
        batch: bool,
        skip_top_reference: bool = False,
    ) -> None:
        references: list[Any] = [] if skip_top_reference else [body.get("ref_audio")]
        if batch and isinstance(body.get("items"), list):
            references.extend(
                item.get("ref_audio")
                for item in body["items"]
                if isinstance(item, dict)
            )
        for reference in references:
            value = str(reference or "").strip()
            if value.startswith("data:audio/") and ";base64," in value:
                _decode_bounded_base64(
                    value.split(",", 1)[1],
                    maximum,
                    "TTS reference audio",
                )

    def _proxy_raw(self, handler: BaseHTTPRequestHandler, service: str, endpoint_key: str, raw: bytes, content_type: str) -> bool:
        config = self._service_config(service)
        if not self._require_enabled(handler, service, config):
            return True
        return self._proxy_bytes(handler, service, config, str(config.get(endpoint_key) or ""), raw, content_type)

    def _proxy_bytes(self, handler: BaseHTTPRequestHandler, service: str, config: SpeechConfig, endpoint: str, raw: bytes, content_type: str, *, streaming: bool = False) -> bool:
        request = urllib.request.Request(
            self._url(config, endpoint),
            data=raw,
            headers=self._headers(config, content_type or "application/octet-stream"),
            method="POST",
        )
        return self._open_and_stream(handler, service, config, request) if streaming else self._open_and_write(handler, service, config, request)

    def _open_and_stream(self, handler: BaseHTTPRequestHandler, service: str, config: SpeechConfig, request: urllib.request.Request) -> bool:
        started = False
        try:
            with self.ports.urlopen(request, timeout=self._request_timeout(service, config)) as response:
                handler.send_response(int(getattr(response, "status", 200)))
                handler.send_header("content-type", str(response.headers.get("content-type") or "audio/pcm"))
                handler.send_header("cache-control", "no-store")
                handler.send_header("connection", "close")
                handler.end_headers()
                started = True
                handler.close_connection = True
                read_chunk = getattr(response, "read1", response.read)
                while chunk := read_chunk(16 * 1024):
                    handler.wfile.write(chunk)
                    handler.wfile.flush()
        except urllib.error.HTTPError as exc:
            if not started:
                self._write_bytes(handler, exc.read(), int(exc.code), str(exc.headers.get("content-type") or "application/json"))
        except Exception as exc:
            self.ports.log("ERROR", f"speech_stream_failed service={service} error={type(exc).__name__}: {exc}")
            if not started:
                self.ports.write_json(handler, {"error": {"type": "upstream_error", "message": f"{service.upper()} upstream unavailable: {exc}"}}, 502)
        return True

    def _open_and_write(self, handler: BaseHTTPRequestHandler, service: str, config: SpeechConfig, request: urllib.request.Request) -> bool:
        try:
            with self.ports.urlopen(request, timeout=self._request_timeout(service, config)) as response:
                self._write_bytes(handler, response.read(), int(getattr(response, "status", 200)), str(response.headers.get("content-type") or "application/octet-stream"))
        except urllib.error.HTTPError as exc:
            self._write_bytes(handler, exc.read(), int(exc.code), str(exc.headers.get("content-type") or "application/json"))
        except Exception as exc:
            self.ports.log("ERROR", f"speech_proxy_failed service={service} error={type(exc).__name__}: {exc}")
            self.ports.write_json(handler, {"error": {"type": "upstream_error", "message": f"{service.upper()} upstream unavailable: {exc}"}}, 502)
        return True

    @staticmethod
    def _require_enabled(handler: BaseHTTPRequestHandler, service: str, config: SpeechConfig) -> bool:
        if bool(config.get("enabled")) and str(config.get("base_url") or "").strip():
            return True
        body = json.dumps({"error": {"type": "service_disabled", "message": f"{service.upper()} is not configured or enabled"}}).encode()
        handler.send_response(503)
        handler.send_header("content-type", "application/json")
        handler.send_header("content-length", str(len(body)))
        handler.end_headers()
        handler.wfile.write(body)
        return False

    @staticmethod
    def _write_bytes(handler: BaseHTTPRequestHandler, data: bytes, status: int, content_type: str) -> None:
        handler.send_response(status)
        handler.send_header("content-type", content_type)
        handler.send_header("content-length", str(len(data)))
        handler.send_header("cache-control", "no-store")
        handler.end_headers()
        handler.wfile.write(data)

    @staticmethod
    def _headers(config: SpeechConfig, content_type: str) -> dict[str, str]:
        headers = {"accept": "*/*", "content-type": content_type}
        api_key = str(config.get("api_key") or "").strip()
        if api_key:
            headers["authorization"] = f"Bearer {api_key}"
        return headers

    @staticmethod
    def _url(config: SpeechConfig, endpoint: str) -> str:
        return str(config.get("base_url") or "").rstrip("/") + "/" + endpoint.lstrip("/")

    @staticmethod
    def _timeout(config: SpeechConfig) -> float:
        try:
            return max(1.0, min(3600.0, float(config.get("timeout_seconds") or 300)))
        except (TypeError, ValueError):
            return 300.0

    @classmethod
    def _request_timeout(cls, service: str, config: SpeechConfig) -> float:
        timeout = cls._timeout(config)
        return min(30.0, timeout) if service == "asr" else timeout

    @staticmethod
    def _multipart(fields: dict[str, str], file_field: str, filename: str, mime: str, data: bytes) -> tuple[bytes, str]:
        boundary = "ciel-" + secrets.token_hex(16)
        chunks: list[bytes] = []
        for name, value in fields.items():
            chunks.extend([
                f"--{boundary}\r\n".encode(),
                f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode(),
                str(value).encode("utf-8"), b"\r\n",
            ])
        safe_filename = filename.replace('"', "_").replace("\r", "_").replace("\n", "_")
        chunks.extend([
            f"--{boundary}\r\n".encode(),
            f'Content-Disposition: form-data; name="{file_field}"; filename="{safe_filename}"\r\n'.encode(),
            f"Content-Type: {mime}\r\n\r\n".encode(), data, b"\r\n",
            f"--{boundary}--\r\n".encode(),
        ])
        return b"".join(chunks), f"multipart/form-data; boundary={boundary}"


__all__ = [
    "MAX_SPEECH_AUDIO_BYTES",
    "MAX_TTS_REFERENCE_AUDIO_BYTES",
    "SpeechHttpController",
    "SpeechHttpPorts",
]
