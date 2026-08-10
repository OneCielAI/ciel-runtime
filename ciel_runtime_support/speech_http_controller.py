"""Speech configuration and OpenAI-compatible ASR/TTS proxy endpoints."""

from __future__ import annotations

import base64
import json
import re
import secrets
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler
from typing import Any, Callable


SpeechConfig = dict[str, Any]


@dataclass(frozen=True, slots=True)
class SpeechHttpPorts:
    load_config: Callable[[], dict[str, Any]]
    save_config: Callable[[dict[str, Any]], None]
    write_json: Callable[..., None]
    log: Callable[[str, str], None]
    urlopen: Callable[..., Any] = urllib.request.urlopen
    colab_action: Callable[[str, dict[str, Any], dict[str, str]], dict[str, Any]] | None = None
    colab_status: Callable[[str], dict[str, Any]] | None = None


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
        public: dict[str, Any] = {"ok": True}
        for name in ("asr", "tts"):
            source = speech.get(name) if isinstance(speech.get(name), dict) else {}
            item = {key: value for key, value in source.items() if key not in {"api_key", "ref_audio"}}
            item["api_key_set"] = bool(str(source.get("api_key") or "").strip())
            if name == "tts":
                item["ref_audio_set"] = bool(str(source.get("ref_audio") or "").strip())
            public[name] = item
        tailscale = speech.get("tailscale")
        public["tailscale"] = dict(tailscale) if isinstance(tailscale, dict) else {}
        colab = speech.get("colab")
        public["colab"] = dict(colab) if isinstance(colab, dict) else {}
        public["endpoints"] = self.discovery_payload()["endpoints"]
        return public

    def discovery_payload(self) -> dict[str, Any]:
        return {
            "ok": True,
            "web_chat": "/ca/web/chat",
            "endpoints": {
                "chat_health": "GET /ca/channel/health",
                "chat_messages": "GET|POST /ca/channel/messages",
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
            },
        }

    def health_payload(self) -> dict[str, Any]:
        services = {name: self._probe(name) for name in ("asr", "tts")}
        return {"ok": all(not item["enabled"] or item["reachable"] for item in services.values()), "services": services}

    def _speech_config(self) -> SpeechConfig:
        speech = self.ports.load_config().get("speech")
        return speech if isinstance(speech, dict) else {}

    def _service_config(self, name: str) -> SpeechConfig:
        service = self._speech_config().get(name)
        return service if isinstance(service, dict) else {}

    def _save_public_config(self, handler: BaseHTTPRequestHandler, raw: bytes) -> bool:
        try:
            update = json.loads(raw.decode("utf-8") if raw else "{}")
            if not isinstance(update, dict):
                raise ValueError("configuration body must be a JSON object")
            config = self.ports.load_config()
            speech = config.setdefault("speech", {})
            if not isinstance(speech, dict):
                speech = {}
                config["speech"] = speech
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
            self.ports.write_json(handler, self.public_config())
        except (UnicodeError, ValueError, TypeError) as exc:
            self.ports.write_json(handler, {"ok": False, "error": str(exc)}, 400)
        return True

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
            }
            result = self.ports.colab_action(action, dict(colab), secrets)
            self.ports.write_json(handler, result)
        except (UnicodeError, json.JSONDecodeError, ValueError, TypeError) as exc:
            self.ports.write_json(handler, {"ok": False, "error": str(exc)}, 400)
        except RuntimeError as exc:
            self.ports.write_json(handler, {"ok": False, "error": str(exc)}, 409)
        return True

    @staticmethod
    def _validated_value(service: str, key: str, value: Any) -> Any:
        allowed = {
            "asr": {"enabled", "base_url", "endpoint", "model", "language", "silence_ms", "min_speech_ms", "vad_threshold", "api_key", "timeout_seconds"},
            "tts": {"enabled", "base_url", "endpoint", "voices_endpoint", "model", "voice", "language", "ref_audio", "ref_text", "response_format", "speed", "auto_speak", "api_key", "timeout_seconds"},
        }
        if key not in allowed[service]:
            raise ValueError(f"unsupported {service} setting: {key}")
        if key in {"enabled", "auto_speak"}:
            return bool(value)
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
            if len(text) > 14_000_000:
                raise ValueError("TTS reference audio must be 10 MB or smaller")
            if text.startswith("data:audio/") and ";base64," in text:
                try:
                    audio = base64.b64decode(text.split(",", 1)[1], validate=True)
                except (ValueError, TypeError) as exc:
                    raise ValueError("invalid base64 TTS reference audio") from exc
                if not audio or len(audio) > 10 * 1024 * 1024:
                    raise ValueError("TTS reference audio must be between 1 byte and 10 MB")
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
            "asr_accelerator",
            "tts_accelerator",
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
        if "application/json" in content_type.lower():
            try:
                body = json.loads(raw.decode("utf-8"))
                if not isinstance(body, dict):
                    raise ValueError("request must be a JSON object")
                audio = base64.b64decode(str(body.get("audio_base64") or ""), validate=True)
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
            except (ValueError, TypeError, UnicodeError) as exc:
                self.ports.write_json(handler, {"error": {"type": "invalid_request_error", "message": str(exc)}}, 400)
                return True
        return self._proxy_raw(handler, "asr", "endpoint", raw, content_type)

    def _proxy_tts(self, handler: BaseHTTPRequestHandler, raw: bytes, content_type: str, *, batch: bool) -> bool:
        config = self._service_config("tts")
        if not self._require_enabled(handler, "tts", config):
            return True
        if "application/json" in content_type.lower():
            try:
                body = json.loads(raw.decode("utf-8") if raw else "{}")
                if not isinstance(body, dict):
                    raise ValueError("request must be a JSON object")
                if not batch:
                    body.setdefault("model", str(config.get("model") or ""))
                    body.setdefault("voice", str(config.get("voice") or "default"))
                    body.setdefault("language", str(config.get("language") or "Auto"))
                    if str(config.get("ref_audio") or "").strip():
                        body.setdefault("ref_audio", str(config["ref_audio"]))
                    if str(config.get("ref_text") or "").strip():
                        body.setdefault("ref_text", str(config["ref_text"]))
                    body.setdefault("response_format", str(config.get("response_format") or "wav"))
                    body.setdefault("speed", float(config.get("speed") or 1.0))
                raw = json.dumps(body, ensure_ascii=False).encode("utf-8")
            except (ValueError, TypeError, UnicodeError) as exc:
                self.ports.write_json(handler, {"error": {"type": "invalid_request_error", "message": str(exc)}}, 400)
                return True
        endpoint = str(config.get("endpoint") or "/v1/audio/speech") + ("/batch" if batch else "")
        return self._proxy_bytes(handler, "tts", config, endpoint, raw, content_type)

    def _proxy_raw(self, handler: BaseHTTPRequestHandler, service: str, endpoint_key: str, raw: bytes, content_type: str) -> bool:
        config = self._service_config(service)
        if not self._require_enabled(handler, service, config):
            return True
        return self._proxy_bytes(handler, service, config, str(config.get(endpoint_key) or ""), raw, content_type)

    def _proxy_bytes(self, handler: BaseHTTPRequestHandler, service: str, config: SpeechConfig, endpoint: str, raw: bytes, content_type: str) -> bool:
        request = urllib.request.Request(
            self._url(config, endpoint),
            data=raw,
            headers=self._headers(config, content_type or "application/octet-stream"),
            method="POST",
        )
        return self._open_and_write(handler, service, config, request)

    def _open_and_write(self, handler: BaseHTTPRequestHandler, service: str, config: SpeechConfig, request: urllib.request.Request) -> bool:
        try:
            with self.ports.urlopen(request, timeout=self._timeout(config)) as response:
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


__all__ = ["SpeechHttpController", "SpeechHttpPorts"]
