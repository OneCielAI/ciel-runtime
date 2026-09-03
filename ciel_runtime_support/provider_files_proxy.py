"""Streaming passthrough for provider-native OpenAI Files endpoints."""

from __future__ import annotations

import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any, Callable


PROVIDER_FILES_PATH = "/v1/files"
META_FILE_UPLOAD_MAX_BYTES = 1_073_741_824
# Multipart boundaries and form fields are transport overhead rather than file
# bytes.  Leave one MiB for that envelope while Meta enforces the exact 1 GiB
# file limit upstream.
META_FILE_UPLOAD_WIRE_MAX_BYTES = META_FILE_UPLOAD_MAX_BYTES + 1_048_576
FILES_API_PROVIDERS = frozenset({"meta"})


def is_provider_files_path(path: str) -> bool:
    normalized = urllib.parse.urlparse(str(path or "")).path
    return normalized == PROVIDER_FILES_PATH or normalized.startswith(
        PROVIDER_FILES_PATH + "/"
    )


class _LengthLimitedReader:
    """Expose exactly one HTTP request body without waiting for socket EOF."""

    def __init__(self, stream: Any, length: int) -> None:
        self._stream = stream
        self._remaining = length

    def read(self, size: int = -1) -> bytes:
        if self._remaining <= 0:
            return b""
        requested = self._remaining if size is None or size < 0 else min(size, self._remaining)
        data = self._stream.read(requested)
        if not data and self._remaining:
            raise EOFError(
                f"multipart request ended with {self._remaining} bytes unread"
            )
        self._remaining -= len(data)
        return data


@dataclass(frozen=True, slots=True)
class ProviderFilesProxyPorts:
    current_provider: Callable[[dict[str, Any]], tuple[str, dict[str, Any]]]
    bridge_enabled: Callable[[dict[str, Any]], bool]
    bridge_is_request: Callable[[Any, dict[str, Any]], bool]
    bridge_resolve: Callable[..., Any]
    upstream_base: Callable[[str, dict[str, Any]], str]
    join_url: Callable[[str, str], str]
    headers: Callable[..., dict[str, str]]
    urlopen: Callable[..., Any]
    timeout_seconds: Callable[[dict[str, Any]], float]
    copy_response_headers: Callable[[Any, Any], None]
    write_json: Callable[..., Any]
    log: Callable[[str, str], Any] = lambda _level, _message: None


class ProviderFilesProxy:
    """Proxy Meta's Files API without buffering uploads in router memory."""

    def __init__(self, ports: ProviderFilesProxyPorts) -> None:
        self._ports = ports

    def _route(
        self, handler: Any, config: dict[str, Any], path: str
    ) -> tuple[str, dict[str, Any]]:
        if self._ports.bridge_enabled(config) and self._ports.bridge_is_request(
            handler, config
        ):
            route = self._ports.bridge_resolve(config, handler.headers, {}, path)
            return route.provider, route.provider_config
        return self._ports.current_provider(config)

    def _reject(
        self, handler: Any, status: int, error_type: str, message: str
    ) -> bool:
        handler.close_connection = True
        self._ports.write_json(
            handler,
            {
                "error": {
                    "message": message,
                    "type": error_type,
                    "param": None,
                    "code": error_type,
                }
            },
            status,
        )
        return True

    def _target(
        self, handler: Any, config: dict[str, Any], path: str
    ) -> tuple[str, dict[str, Any], str] | None:
        if not is_provider_files_path(path):
            return None
        try:
            provider, provider_config = self._route(handler, config, path)
        except ValueError as exc:
            self._reject(handler, 400, "invalid_request_error", str(exc))
            return None
        if provider not in FILES_API_PROVIDERS:
            self._reject(
                handler,
                404,
                "not_found_error",
                f"Provider does not expose a native Files API through Ciel Runtime: {provider}",
            )
            return None
        parsed = urllib.parse.urlparse(handler.path)
        url = self._ports.join_url(
            self._ports.upstream_base(provider, provider_config), parsed.path
        )
        if parsed.query:
            url += "?" + parsed.query
        return provider, provider_config, url

    def _relay(
        self,
        handler: Any,
        provider: str,
        provider_config: dict[str, Any],
        request: urllib.request.Request,
    ) -> bool:
        try:
            response = self._ports.urlopen(
                request,
                timeout=self._ports.timeout_seconds(provider_config),
                provider=provider,
                pcfg=provider_config,
            )
        except urllib.error.HTTPError as exc:
            response = exc
        with response:
            status = getattr(response, "status", None) or getattr(
                response, "code", 200
            )
            handler.send_response(status)
            self._ports.copy_response_headers(handler, response.headers)
            handler.end_headers()
            while chunk := response.read(65_536):
                handler.wfile.write(chunk)
                handler.wfile.flush()
        return True

    def get(self, handler: Any, path: str, config: dict[str, Any]) -> bool:
        target = self._target(handler, config, path)
        if target is None:
            return is_provider_files_path(path)
        provider, provider_config, url = target
        request = urllib.request.Request(
            url,
            headers=self._ports.headers(
                provider, provider_config, handler.headers, "openai_responses"
            ),
            method="GET",
        )
        return self._relay(handler, provider, provider_config, request)

    def post(
        self,
        handler: Any,
        path: str,
        content_length: int,
        content_type: str,
        config: dict[str, Any],
    ) -> bool:
        if not is_provider_files_path(path):
            return False
        if path != PROVIDER_FILES_PATH:
            return self._reject(
                handler, 404, "not_found_error", f"Unsupported Files API path: {path}"
            )
        if "multipart/form-data" not in content_type.casefold():
            return self._reject(
                handler,
                415,
                "invalid_request_error",
                "POST /v1/files requires multipart/form-data",
            )
        if content_length <= 0 or content_length > META_FILE_UPLOAD_WIRE_MAX_BYTES:
            return self._reject(
                handler,
                413,
                "request_too_large",
                "Meta Files API multipart upload exceeds the 1 GiB file limit",
            )
        target = self._target(handler, config, path)
        if target is None:
            return True
        provider, provider_config, url = target
        headers = self._ports.headers(
            provider, provider_config, handler.headers, "openai_responses"
        )
        headers["content-length"] = str(content_length)
        request = urllib.request.Request(
            url,
            data=_LengthLimitedReader(handler.rfile, content_length),
            headers=headers,
            method="POST",
        )
        # An upstream can reject before consuming the whole body.  Closing the
        # client connection prevents unread multipart bytes from becoming a
        # second HTTP request on this socket.
        handler.close_connection = True
        self._ports.log(
            "INFO",
            f"provider_files_upload provider={provider} bytes={content_length}",
        )
        return self._relay(handler, provider, provider_config, request)

    def delete(self, handler: Any, path: str, config: dict[str, Any]) -> bool:
        target = self._target(handler, config, path)
        if target is None:
            return is_provider_files_path(path)
        if path == PROVIDER_FILES_PATH:
            return self._reject(
                handler,
                405,
                "invalid_request_error",
                "DELETE /v1/files requires a file id",
            )
        provider, provider_config, url = target
        request = urllib.request.Request(
            url,
            headers=self._ports.headers(
                provider, provider_config, handler.headers, "openai_responses"
            ),
            method="DELETE",
        )
        return self._relay(handler, provider, provider_config, request)


__all__ = [
    "FILES_API_PROVIDERS",
    "META_FILE_UPLOAD_MAX_BYTES",
    "META_FILE_UPLOAD_WIRE_MAX_BYTES",
    "PROVIDER_FILES_PATH",
    "ProviderFilesProxy",
    "ProviderFilesProxyPorts",
    "is_provider_files_path",
]
