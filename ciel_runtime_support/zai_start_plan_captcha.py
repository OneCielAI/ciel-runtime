"""Interactive Aliyun CAPTCHA headers for the Z.AI Start Plan gateway.

ZCode's Start Plan gateway consumes a fresh Aliyun CAPTCHA verification value
for each model request.  This module hosts the official browser SDK on a
loopback-only page, receives one state-bound result, and returns only the two
request-scoped headers used by the gateway.
"""

from __future__ import annotations

import hmac
import html
import json
import os
import platform
import secrets
import threading
import urllib.parse
import urllib.request
import uuid
import webbrowser
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable, Mapping

from .runtime_interaction import RuntimeInteractionEvent, RuntimeInteractionRepository


ZCODE_CLIENT_CONFIG_URL = "https://zcode.z.ai/api/v1/client/configs"
ALIYUN_CAPTCHA_SDK_URL = (
    "https://o.alicdn.com/captcha-frontend/aliyunCaptcha/AliyunCaptcha.js"
)
CAPTCHA_PARAM_HEADER = "X-Aliyun-Captcha-Verify-Param"
CAPTCHA_REGION_HEADER = "X-Aliyun-Captcha-Verify-Region"
_CAPTCHA_PATH = "/zai-start-plan-captcha"
_CAPTCHA_RESULT_PATH = f"{_CAPTCHA_PATH}/result"
_MAX_RESULT_BYTES = 64 * 1024


@dataclass(frozen=True, slots=True)
class ZaiStartPlanCaptchaConfig:
    enabled: bool
    region: str
    prefix: str
    scene_id: str

    @classmethod
    def from_envelope(cls, envelope: Any) -> "ZaiStartPlanCaptchaConfig":
        if not isinstance(envelope, Mapping):
            raise RuntimeError("ZCode client config returned an invalid envelope.")
        if envelope.get("code") not in {None, 0, "0"}:
            raise RuntimeError(
                f"ZCode client config request failed (code {envelope.get('code')})."
            )
        data = envelope.get("data")
        configs = data.get("configs") if isinstance(data, Mapping) else None
        captcha = configs.get("captcha") if isinstance(configs, Mapping) else None
        if not isinstance(captcha, Mapping):
            raise RuntimeError("ZCode client config did not include CAPTCHA settings.")
        config = cls(
            enabled=captcha.get("enabled") is not False,
            region=str(captcha.get("region") or "").strip(),
            prefix=str(captcha.get("prefix") or "").strip(),
            scene_id=str(captcha.get("sceneId") or "").strip(),
        )
        if not config.enabled:
            raise RuntimeError("ZCode Start Plan CAPTCHA is disabled by server config.")
        if not config.region or not config.prefix or not config.scene_id:
            raise RuntimeError("ZCode client config returned incomplete CAPTCHA settings.")
        return config


class _LoopbackCaptchaServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self._accepted_result_requests: set[int] = set()
        self._accepted_result_lock = threading.Lock()
        self.result_response_finished: Callable[[], None] | None = None
        super().__init__(*args, **kwargs)

    def mark_result_request_accepted(self, request: Any) -> None:
        with self._accepted_result_lock:
            self._accepted_result_requests.add(id(request))

    def shutdown_request(self, request: Any) -> None:
        with self._accepted_result_lock:
            accepted = id(request) in self._accepted_result_requests
            self._accepted_result_requests.discard(id(request))
        try:
            super().shutdown_request(request)
        finally:
            if accepted and self.result_response_finished is not None:
                self.result_response_finished()


@dataclass(slots=True)
class _CaptchaResultReceiver:
    config: ZaiStartPlanCaptchaConfig
    state: str
    timeout_seconds: float
    host: str = "127.0.0.1"
    port: int = 0
    public_base_url: str = ""
    _server: _LoopbackCaptchaServer | None = field(default=None, init=False)
    _thread: threading.Thread | None = field(default=None, init=False)
    _ready: threading.Event = field(default_factory=threading.Event, init=False)
    _result: str = field(default="", init=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, init=False)

    @property
    def url(self) -> str:
        if self._server is None:
            raise RuntimeError("Z.AI CAPTCHA receiver was not started.")
        query = urllib.parse.urlencode({"state": self.state})
        base = self._resolved_public_base_url(self._server.server_port)
        return f"{base}{_CAPTCHA_PATH}?{query}"

    def _resolved_public_base_url(self, server_port: int) -> str:
        configured = str(self.public_base_url or "").strip().rstrip("/")
        if not configured:
            return f"http://localhost:{server_port}"
        candidate = configured.replace("{port}", str(server_port))
        parsed = urllib.parse.urlsplit(candidate)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
            or parsed.path not in {"", "/"}
        ):
            raise RuntimeError(
                "Z.AI CAPTCHA public base URL must be an HTTP(S) origin; "
                "use {port} for the receiver port."
            )
        if "{port}" not in configured and parsed.port is None:
            hostname = parsed.hostname
            if ":" in hostname and not hostname.startswith("["):
                hostname = f"[{hostname}]"
            candidate = urllib.parse.urlunsplit(
                (parsed.scheme, f"{hostname}:{server_port}", "", "", "")
            )
        return candidate.rstrip("/")

    def __enter__(self) -> "_CaptchaResultReceiver":
        receiver = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # noqa: N802
                receiver._handle_get(self)

            def do_POST(self) -> None:  # noqa: N802
                if receiver._handle_post(self) and receiver._server is not None:
                    receiver._server.mark_result_request_accepted(self.request)

            def log_message(self, _format: str, *_args: object) -> None:
                return

        self._server = _LoopbackCaptchaServer((self.host, self.port), Handler)
        self._server.result_response_finished = self._mark_result_response_finished
        self._thread = threading.Thread(
            target=self._server.serve_forever,
            kwargs={"poll_interval": 0.05},
            name="ciel-zai-start-plan-captcha",
            daemon=True,
        )
        self._thread.start()
        return self

    def __exit__(self, _exc_type: object, _exc: object, _traceback: object) -> None:
        server, thread = self._server, self._thread
        if server is not None:
            server.shutdown()
            server.server_close()
        if thread is not None:
            thread.join(timeout=2.0)
        self._server = None
        self._thread = None

    def wait(self) -> str:
        if not self._ready.wait(max(0.0, self.timeout_seconds)):
            raise RuntimeError(
                "Z.AI Start Plan CAPTCHA timed out after "
                f"{int(self.timeout_seconds)} seconds."
            )
        with self._lock:
            result = self._result
        if not result:
            raise RuntimeError("Z.AI Start Plan CAPTCHA returned an empty result.")
        return result

    def _valid_state(self, query: str) -> bool:
        values = urllib.parse.parse_qs(query, keep_blank_values=True)
        supplied = str((values.get("state") or [""])[0])
        return bool(supplied) and hmac.compare_digest(supplied, self.state)

    def _handle_get(self, handler: BaseHTTPRequestHandler) -> None:
        parsed = urllib.parse.urlsplit(handler.path)
        if parsed.path != _CAPTCHA_PATH:
            self._respond(handler, 404, b"Not found", "text/plain; charset=utf-8")
            return
        if not self._valid_state(parsed.query):
            self._respond(handler, 403, b"Invalid CAPTCHA state", "text/plain; charset=utf-8")
            return
        body = self._page().encode("utf-8")
        self._respond(handler, 200, body, "text/html; charset=utf-8")

    def _handle_post(self, handler: BaseHTTPRequestHandler) -> bool:
        parsed = urllib.parse.urlsplit(handler.path)
        if parsed.path != _CAPTCHA_RESULT_PATH:
            self._respond(handler, 404, b"Not found", "text/plain; charset=utf-8")
            return False
        if not self._valid_state(parsed.query):
            self._respond(handler, 403, b"Invalid CAPTCHA state", "text/plain; charset=utf-8")
            return False
        try:
            length = int(handler.headers.get("Content-Length") or "0")
        except ValueError:
            length = 0
        if length <= 0 or length > _MAX_RESULT_BYTES:
            self._respond(handler, 413, b"Invalid result size", "text/plain; charset=utf-8")
            return False
        value = handler.rfile.read(length).decode("utf-8", errors="strict").strip()
        if not value:
            self._respond(handler, 400, b"Empty result", "text/plain; charset=utf-8")
            return False
        with self._lock:
            if self._result:
                self._respond(handler, 409, b"Result already received", "text/plain; charset=utf-8")
                return False
            self._result = value
        self._respond(handler, 204, b"", "text/plain; charset=utf-8")
        return True

    def _mark_result_response_finished(self) -> None:
        """Release the model request only after the browser response is finalized."""
        self._ready.set()

    @staticmethod
    def _respond(
        handler: BaseHTTPRequestHandler,
        status: int,
        body: bytes,
        content_type: str,
    ) -> None:
        handler.send_response(status)
        handler.send_header("Content-Type", content_type)
        handler.send_header("Content-Length", str(len(body)))
        handler.send_header("Cache-Control", "no-store")
        handler.send_header("Connection", "close")
        handler.end_headers()
        handler.close_connection = True
        if body:
            try:
                handler.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                pass

    def _page(self) -> str:
        config_json = json.dumps(
            {
                "region": self.config.region,
                "prefix": self.config.prefix,
                "sceneId": self.config.scene_id,
                "state": self.state,
                "resultPath": _CAPTCHA_RESULT_PATH,
            },
            ensure_ascii=True,
        ).replace("<", "\\u003c")
        sdk_url = html.escape(ALIYUN_CAPTCHA_SDK_URL, quote=True)
        return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Ciel Runtime · Z.AI Start Plan verification</title>
<style>
body{{font:16px system-ui,sans-serif;max-width:680px;margin:48px auto;padding:0 24px;color:#17202a}}
#captcha-element{{min-height:1px}} button{{font:inherit;padding:10px 16px}}
#status{{white-space:pre-wrap}} .muted{{color:#5f6b76}}
</style></head><body>
<h1>Z.AI Start Plan verification</h1>
<p id="status">Preparing the official Aliyun CAPTCHA…</p>
<p class="muted">This page sends the one-time verification result only to the local Ciel Runtime process.</p>
<div id="captcha-element"></div><button id="captcha-button" type="button">Verify</button>
<script>const CIEL_CAPTCHA={config_json};</script>
<script src="{sdk_url}"></script>
<script>
(() => {{
  const status = document.getElementById('status');
  const button = document.getElementById('captcha-button');
  let instance = null;
  let interactiveShown = false;
  let submission = null;
  const setStatus = value => {{ status.textContent = value; }};
  const submit = value => {{
    const param = String(value || '').trim();
    if (!param) throw new Error('CAPTCHA returned an empty verification result.');
    if (submission) return submission;
    submission = (async () => {{
      const query = new URLSearchParams({{state: CIEL_CAPTCHA.state}});
      const response = await fetch(`${{CIEL_CAPTCHA.resultPath}}?${{query}}`, {{
        method: 'POST', headers: {{'Content-Type': 'text/plain;charset=UTF-8'}}, body: param
      }});
      if (!response.ok) throw new Error(`Ciel Runtime rejected the result (${{response.status}}).`);
      setStatus('Verification complete. Returning to Ciel Runtime…');
      button.hidden = true;
      window.setTimeout(() => window.close(), 700);
    }})();
    return submission;
  }};
  const showInteractive = () => {{
    interactiveShown = true;
    setStatus('Complete the verification challenge to continue the model request.');
    if (instance && typeof instance.show === 'function') instance.show(); else button.click();
  }};
  button.addEventListener('click', () => {{
    if (instance && typeof instance.show === 'function') instance.show();
  }});
  window.AliyunCaptchaConfig = {{region: CIEL_CAPTCHA.region, prefix: CIEL_CAPTCHA.prefix}};
  if (typeof window.initAliyunCaptcha !== 'function') {{
    setStatus('The official Aliyun CAPTCHA SDK could not be loaded.');
    return;
  }}
  window.initAliyunCaptcha({{
    SceneId: CIEL_CAPTCHA.sceneId, mode: 'popup', language: 'en', showErrorTip: false,
    element: '#captcha-element', button: '#captcha-button',
    getInstance: value => {{
      instance = value;
      window.setTimeout(() => {{
        setStatus('Running security verification…');
        if (typeof value.startTracelessVerification === 'function') value.startTracelessVerification();
        else showInteractive();
      }}, 2000);
    }},
    success: value => submit(value).catch(error => setStatus(error.message)),
    fail: () => {{
      if (submission) return;
      if (!interactiveShown) showInteractive();
      else setStatus('Verification was not accepted. Select Verify to try again.');
    }},
    onError: error => setStatus(`Verification error: ${{error && error.message ? error.message : String(error)}}`)
  }});
}})();
</script></body></html>"""


@dataclass(slots=True)
class ZaiStartPlanCaptchaBroker:
    """Acquire one official Aliyun CAPTCHA result for each upstream attempt."""

    open_url: Callable[[str], bool] = webbrowser.open
    urlopen: Callable[..., Any] = urllib.request.urlopen
    random_state: Callable[[], str] = lambda: secrets.token_urlsafe(32)
    receiver_factory: Callable[..., Any] = _CaptchaResultReceiver
    log: Callable[[str, str], None] = lambda _level, _message: None
    interactions: RuntimeInteractionRepository | None = None
    _request_lock: threading.Lock = field(default_factory=threading.Lock, init=False)

    def fetch_config(self, app_version: str) -> ZaiStartPlanCaptchaConfig:
        query = urllib.parse.urlencode(
            {
                "app_version": app_version,
                "platform": self._platform_key(),
            }
        )
        request = urllib.request.Request(
            f"{ZCODE_CLIENT_CONFIG_URL}?{query}",
            headers={"Accept": "application/json", "User-Agent": f"ZCode/{app_version}"},
        )
        with self.urlopen(request, timeout=15.0) as response:
            envelope = json.loads(response.read().decode("utf-8"))
        return ZaiStartPlanCaptchaConfig.from_envelope(envelope)

    def headers(self, options: Mapping[str, Any]) -> dict[str, str]:
        app_version = str(options.get("zcode_app_version") or "0.16.3").strip()
        timeout = self._timeout(options)
        bind_host = self._bind_host(options)
        port = self._port(options)
        public_base_url = self._public_base_url(options)
        with self._request_lock:
            config = self.fetch_config(app_version)
            state = self.random_state()
            with self.receiver_factory(
                config,
                state,
                timeout,
                host=bind_host,
                port=port,
                public_base_url=public_base_url,
            ) as receiver:
                url = receiver.url
                self.log("INFO", f"zai_start_plan_captcha_waiting url={url}")
                interaction = self._publish_pending_interaction(
                    state=state,
                    url=url,
                    timeout=timeout,
                )
                try:
                    try:
                        opened = self.open_url(url)
                    except Exception as exc:
                        if not public_base_url:
                            raise RuntimeError(
                                "Could not open the Z.AI Start Plan verification page: "
                                + url
                            ) from exc
                        opened = False
                        self.log(
                            "WARN",
                            "zai_start_plan_captcha_browser_open_failed "
                            f"url={url} error={type(exc).__name__}",
                        )
                    if not opened and not public_base_url:
                        raise RuntimeError(
                            "Could not open the Z.AI Start Plan verification page: " + url
                        )
                    result = receiver.wait()
                except Exception as exc:
                    self._publish_interaction_status(interaction, "failed", str(exc))
                    raise
                self._publish_interaction_status(interaction, "completed")
        self.log(
            "INFO",
            "zai_start_plan_captcha_completed "
            f"region={config.region} result_length={len(result)}",
        )
        return {
            CAPTCHA_PARAM_HEADER: result,
            CAPTCHA_REGION_HEADER: config.region,
        }

    def _publish_pending_interaction(
        self,
        *,
        state: str,
        url: str,
        timeout: float,
    ) -> RuntimeInteractionEvent | None:
        if self.interactions is None:
            return None
        return self.interactions.publish_pending(
            request_id=state,
            kind="zai-start-plan-captcha",
            url=url,
            timeout_seconds=timeout,
            message="Complete the Aliyun CAPTCHA to continue the active model request.",
        )

    def _publish_interaction_status(
        self,
        event: RuntimeInteractionEvent | None,
        status: str,
        message: str = "",
    ) -> None:
        if self.interactions is not None and event is not None:
            self.interactions.publish_status(event, status, message=message)

    @staticmethod
    def _timeout(options: Mapping[str, Any]) -> float:
        raw = options.get("zai_captcha_timeout_seconds") or os.environ.get(
            "CIEL_RUNTIME_ZAI_CAPTCHA_TIMEOUT_SECONDS", "120"
        )
        try:
            return max(15.0, min(600.0, float(raw)))
        except (TypeError, ValueError):
            return 120.0

    @staticmethod
    def _bind_host(options: Mapping[str, Any]) -> str:
        return str(
            options.get("zai_captcha_bind_host")
            or os.environ.get("CIEL_RUNTIME_ZAI_CAPTCHA_BIND_HOST")
            or "127.0.0.1"
        ).strip()

    @staticmethod
    def _port(options: Mapping[str, Any]) -> int:
        raw = options.get("zai_captcha_port") or os.environ.get(
            "CIEL_RUNTIME_ZAI_CAPTCHA_PORT", "0"
        )
        try:
            port = int(raw)
        except (TypeError, ValueError) as exc:
            raise RuntimeError("Z.AI CAPTCHA port must be an integer.") from exc
        if port < 0 or port > 65535:
            raise RuntimeError("Z.AI CAPTCHA port must be between 0 and 65535.")
        return port

    @staticmethod
    def _public_base_url(options: Mapping[str, Any]) -> str:
        return str(
            options.get("zai_captcha_public_base_url")
            or os.environ.get("CIEL_RUNTIME_ZAI_CAPTCHA_PUBLIC_BASE_URL")
            or ""
        ).strip()

    @staticmethod
    def _platform_key() -> str:
        system = {"Windows": "win32", "Darwin": "darwin", "Linux": "linux"}.get(
            platform.system(), platform.system().lower()
        )
        machine = platform.machine().lower()
        arch = "arm64" if machine in {"arm64", "aarch64"} else "x64"
        return f"{system}-{arch}"


@dataclass(slots=True)
class ZaiStartPlanRuntimeHeaderPreparer:
    """Reusable upstream callback backed by one serialized CAPTCHA broker."""

    log: Callable[[str, str], None] = lambda _level, _message: None
    interactions: RuntimeInteractionRepository | None = None
    broker: ZaiStartPlanCaptchaBroker = field(init=False)

    def __post_init__(self) -> None:
        self.broker = ZaiStartPlanCaptchaBroker(
            log=self.log,
            interactions=self.interactions,
        )

    def __call__(
        self,
        provider: str,
        config: Mapping[str, Any],
        headers: Mapping[str, str],
    ) -> dict[str, str]:
        return apply_zai_start_plan_runtime_headers(
            provider, config, headers, broker=self.broker
        )


def apply_zai_start_plan_runtime_headers(
    provider: str,
    config: Mapping[str, Any],
    headers: Mapping[str, str],
    *,
    broker: ZaiStartPlanCaptchaBroker,
) -> dict[str, str]:
    """Refresh request-scoped CAPTCHA headers without altering other providers."""

    projected = {
        name: value
        for name, value in headers.items()
        if name.casefold()
        not in {CAPTCHA_PARAM_HEADER.casefold(), CAPTCHA_REGION_HEADER.casefold()}
    }
    if str(provider or "").casefold() != "zai-start-plan":
        return projected
    projected.update(broker.headers(config))
    projected.update(
        {
            "X-Request-Id": str(uuid.uuid4()),
            "X-ZCode-Trace-Id": str(uuid.uuid4()),
            "X-Query-Id": str(uuid.uuid4()),
            "X-Session-Id": str(uuid.uuid4()),
        }
    )
    return projected


__all__ = [
    "ALIYUN_CAPTCHA_SDK_URL",
    "CAPTCHA_PARAM_HEADER",
    "CAPTCHA_REGION_HEADER",
    "ZCODE_CLIENT_CONFIG_URL",
    "ZaiStartPlanCaptchaBroker",
    "ZaiStartPlanCaptchaConfig",
    "ZaiStartPlanRuntimeHeaderPreparer",
    "apply_zai_start_plan_runtime_headers",
]
