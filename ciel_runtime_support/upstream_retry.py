"""Shared upstream request retry transport."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import io
import json
import time
from typing import Any, Callable
import urllib.error
import urllib.parse
import urllib.request

from .remote_bridge import REMOTE_BRIDGE_CONFIG_MARKER
from .upstream_error_policy import (
    UpstreamFailure,
    ollama_request_validation_error,
    terminal_usage_limit_error,
)
from .upstream_dump import dump_upstream_request


def _preserves_upstream_status(error: urllib.error.HTTPError) -> bool:
    """Whether the protocol layer must see this status instead of a local 500.

    Every 4xx describes the request, not provider capacity.  Turning a 400,
    404, 409 or 422 into a bare ``RuntimeError`` dropped the status entirely
    and Ciel answered 500, so Codex showed provider overload while the real
    fault was a tool schema, a model name, or a conversation state conflict.
    """

    code = getattr(error, "code", None)
    return (
        isinstance(code, int) and 400 <= code < 500
    ) or hasattr(error, "ciel_runtime_upstream_status")


def _preserved_http_error(
    error: urllib.error.HTTPError, raw_bytes: bytes
) -> urllib.error.HTTPError:
    """Rebuild a consumed terminal error for the protocol-facing caller."""

    preserved = urllib.error.HTTPError(
        error.url,
        error.code,
        error.reason,
        error.headers,
        io.BytesIO(raw_bytes),
    )
    # Protocol-facing routers may need the body after another boundary has
    # inspected the error.  Keep an immutable copy instead of relying on the
    # one-shot HTTPError.fp stream.
    preserved.ciel_runtime_body = raw_bytes
    upstream_status = getattr(error, "ciel_runtime_upstream_status", None)
    if upstream_status is not None:
        preserved.ciel_runtime_upstream_status = upstream_status
    return preserved


def _normalized_provider_http_error(
    provider: str,
    error: urllib.error.HTTPError,
    raw_bytes: bytes,
) -> tuple[urllib.error.HTTPError, bytes]:
    """Normalize provider bugs that encode request validation as server load."""

    if (
        str(provider or "").casefold() == "ollama"
        and error.code == 500
        and ollama_request_validation_error(raw_bytes)
    ):
        detail = raw_bytes.decode("utf-8", errors="replace")
        try:
            payload = json.loads(detail)
            if isinstance(payload, dict) and payload.get("error"):
                detail = str(payload["error"])
        except (TypeError, ValueError):
            pass
        normalized_bytes = json.dumps(
            {
                "error": {
                    "type": "invalid_request_error",
                    "message": (
                        "Upstream Ollama rejected the request (HTTP 500): "
                        f"{detail.strip() or 'request validation failed'}"
                    ),
                }
            }
        ).encode("utf-8")
        normalized = urllib.error.HTTPError(
            error.url,
            400,
            "Ollama request validation failed",
            error.headers,
            io.BytesIO(normalized_bytes),
        )
        normalized.ciel_runtime_upstream_status = error.code
        normalized.ciel_runtime_body = normalized_bytes
        return normalized, normalized_bytes
    return error, raw_bytes


def _http_error_log_message(message: object, *, limit: int = 512) -> str:
    """Return a bounded single-line provider error for local diagnostics."""

    compact = " ".join(str(message or "HTTP error").split())
    if len(compact) <= limit:
        return compact
    return compact[: max(0, limit - 1)] + "…"


def _upstream_endpoint_identity(url: str) -> str:
    """Return the request origin and path without query credentials or fragments."""

    parsed = urllib.parse.urlsplit(str(url or ""))
    hostname = str(parsed.hostname or "")
    if ":" in hostname and not hostname.startswith("["):
        hostname = f"[{hostname}]"
    try:
        port = parsed.port
    except ValueError:
        port = None
    authority = f"{hostname}:{port}" if port is not None else hostname
    return urllib.parse.urlunsplit(
        (parsed.scheme, authority, parsed.path, "", "")
    )


@dataclass(frozen=True, slots=True)
class UpstreamRetryPolicy:
    configured_gateway_retries: Callable[..., Any]
    retry_after_exceeds_request_timeout: Callable[..., Any]
    retryable_upstream_exception: Callable[..., Any]
    upstream_rate_limit_retry_message: Callable[..., Any]
    upstream_retry_http_codes: frozenset[int] | set[int] | tuple[int, ...]
    upstream_retry_message: Callable[..., Any]
    upstream_retry_wait_seconds: Callable[..., Any]


@dataclass(frozen=True, slots=True)
class UpstreamRetryKeys:
    key_from_request_headers: Callable[..., Any]
    provider_api_key_count: Callable[..., Any]
    provider_has_live_api_key: Callable[..., Any]
    provider_headers: Callable[..., Any]
    prepare_runtime_headers: Callable[..., dict[str, str]]
    register_api_key_cooldown: Callable[..., Any]


@dataclass(frozen=True, slots=True)
class UpstreamRetryRateLimit:
    learn_headers: Callable[..., Any]
    log: Callable[..., Any]
    register_backoff: Callable[..., Any]
    write_activity: Callable[..., Any]


@dataclass(frozen=True, slots=True)
class UpstreamRetryHttp:
    estimate_tokens: Callable[..., Any]
    provider_urlopen: Callable[..., Any]
    set_stream_read_timeout: Callable[..., Any]
    stream_idle_timeout_seconds: Callable[..., Any]
    upstream_http_error_message: Callable[..., Any]


@dataclass(frozen=True, slots=True)
class UpstreamRetryServices:
    policy: UpstreamRetryPolicy
    keys: UpstreamRetryKeys
    rate_limit: UpstreamRetryRateLimit
    http: UpstreamRetryHttp


def post_json_with_rate_retry(
    url: str,
    req_body: Any,
    headers: dict[str, str],
    timeout: float,
    provider: str,
    pcfg: dict[str, Any],
    model: str,
    retry_notice: Callable[[str], None] | None = None,
    *,
    retry_rate_limits: bool = True,
    services: UpstreamRetryServices,
) -> Any:
    policy = services.policy
    keys = services.keys
    rate_limit = services.rate_limit
    http = services.http
    UPSTREAM_RETRY_HTTP_CODES = policy.upstream_retry_http_codes
    configured_gateway_retries = policy.configured_gateway_retries
    retry_after_exceeds_request_timeout = policy.retry_after_exceeds_request_timeout
    retryable_upstream_exception = policy.retryable_upstream_exception
    upstream_rate_limit_retry_message = policy.upstream_rate_limit_retry_message
    upstream_retry_message = policy.upstream_retry_message
    upstream_retry_wait_seconds = policy.upstream_retry_wait_seconds
    key_from_request_headers = keys.key_from_request_headers
    provider_api_key_count = keys.provider_api_key_count
    provider_has_live_api_key = keys.provider_has_live_api_key
    provider_headers = keys.provider_headers
    prepare_runtime_headers = keys.prepare_runtime_headers
    register_api_key_cooldown = keys.register_api_key_cooldown
    learn_router_rate_limit_headers = rate_limit.learn_headers
    register_router_rate_limit_backoff = rate_limit.register_backoff
    router_log = rate_limit.log
    write_router_activity = rate_limit.write_activity
    estimate_tokens = http.estimate_tokens
    provider_urlopen = http.provider_urlopen
    remote_bridge = pcfg.get(REMOTE_BRIDGE_CONFIG_MARKER) is True
    gateway_retries = 0 if remote_bridge else configured_gateway_retries(pcfg)
    max_attempts = max(1, gateway_retries + 1)
    rate_limit_max_attempts = (
        max_attempts
        if remote_bridge
        else max(max_attempts, provider_api_key_count(provider, pcfg))
    )
    token_estimate = estimate_tokens(req_body)
    byte_estimate = len(json.dumps(req_body, ensure_ascii=False).encode("utf-8"))
    for attempt in range(rate_limit_max_attempts):
        try:
            headers = prepare_runtime_headers(provider, pcfg, headers)
            write_router_activity(
                "request",
                provider,
                model,
                attempt=attempt + 1,
                total=max_attempts,
                tokens=token_estimate,
                bytes=byte_estimate,
                timeout=timeout,
            )
            router_log("INFO", f"upstream_request provider={provider} model={model} attempt={attempt + 1}/{max_attempts} tokens={token_estimate} bytes={byte_estimate} timeout={timeout}")
            data_bytes = json.dumps(req_body).encode("utf-8")
            req = urllib.request.Request(url, data=data_bytes, headers=headers, method="POST")
            with provider_urlopen(req, timeout=timeout, provider=provider, pcfg=pcfg) as resp:
                learn_router_rate_limit_headers(provider, pcfg, model, resp.headers)
                data = json.loads(resp.read().decode("utf-8"))
                write_router_activity("success", provider, model, attempt=attempt + 1, tokens=token_estimate, bytes=byte_estimate)
                return data
        except urllib.error.HTTPError as exc:
            raw_bytes = exc.read()
            exc, raw_bytes = _normalized_provider_http_error(provider, exc, raw_bytes)
            raw = raw_bytes.decode("utf-8", errors="ignore")
            learn_router_rate_limit_headers(provider, pcfg, model, exc.headers)
            terminal_usage_limit = exc.code == 429 and terminal_usage_limit_error(raw)
            if exc.code == 429:
                register_api_key_cooldown(provider, pcfg, key_from_request_headers(headers), exc.headers)
            if (
                exc.code == 429
                and not terminal_usage_limit
                and retry_rate_limits
                and provider_api_key_count(provider, pcfg) > 1
                and provider_has_live_api_key(provider, pcfg)
                and attempt + 1 < rate_limit_max_attempts
            ):
                retry_no = attempt + 1
                headers = provider_headers(
                    provider, pcfg, headers, None, True
                )
                next_hash = hashlib.sha256(key_from_request_headers(headers).encode("utf-8")).hexdigest()[:12]
                write_router_activity("retry", provider, model, attempt=retry_no, total=rate_limit_max_attempts - 1, code=exc.code, wait=0, tokens=token_estimate, bytes=byte_estimate)
                router_log("WARN", f"upstream_rate_limit_key_retry provider={provider} model={model} attempt={retry_no}/{rate_limit_max_attempts - 1} next_key_hash={next_hash} tokens={token_estimate} bytes={byte_estimate}")
                continue
            if exc.code == 429 and not terminal_usage_limit and retry_rate_limits and attempt + 1 < max_attempts:
                skip_retry, retry_after_seconds = retry_after_exceeds_request_timeout(exc.headers, timeout)
                if skip_retry:
                    write_router_activity("error", provider, model, code=exc.code, retry_after=retry_after_seconds, tokens=token_estimate, bytes=byte_estimate)
                    router_log(
                        "WARN",
                        f"upstream_rate_limit_no_retry provider={provider} model={model} retry_after={retry_after_seconds:.2f}s timeout={timeout:.2f}s tokens={token_estimate} bytes={byte_estimate}",
                    )
                    raise _preserved_http_error(exc, raw_bytes) from exc
                retry_no = attempt + 1
                wait = register_router_rate_limit_backoff(provider, pcfg, model, exc.headers.get("Retry-After"))
                write_router_activity("retry", provider, model, attempt=retry_no, total=gateway_retries, code=exc.code, wait=wait, tokens=token_estimate, bytes=byte_estimate)
                router_log("WARN", f"upstream_rate_limit_retry provider={provider} model={model} attempt={retry_no}/{gateway_retries} wait={wait:.2f}s tokens={token_estimate} bytes={byte_estimate}")
                if retry_notice:
                    retry_notice(upstream_rate_limit_retry_message(retry_no, gateway_retries))
                time.sleep(wait)
                # The just-failed key is now resting; re-pick so the retry uses a live key.
                headers = provider_headers(
                    provider, pcfg, headers, None, True
                )
                continue
            if exc.code in UPSTREAM_RETRY_HTTP_CODES and attempt + 1 < max_attempts:
                retry_no = attempt + 1
                write_router_activity("retry", provider, model, attempt=retry_no, total=gateway_retries, code=exc.code, tokens=token_estimate, bytes=byte_estimate)
                router_log("WARN", f"upstream_retry provider={provider} model={model} attempt={retry_no}/{gateway_retries} code={exc.code} tokens={token_estimate} bytes={byte_estimate}")
                if retry_notice:
                    retry_notice(upstream_retry_message(retry_no, gateway_retries))
                time.sleep(upstream_retry_wait_seconds(retry_no))
                continue
            write_router_activity("error", provider, model, code=exc.code, tokens=token_estimate, bytes=byte_estimate)
            if _preserves_upstream_status(exc):
                # Request-level failures are terminal, not capacity errors.
                # Preserve their status and body so callers never turn them
                # into a retryable generic 500. The original stream was
                # consumed above, so recreate it faithfully.
                raise _preserved_http_error(exc, raw_bytes) from exc
            raise UpstreamFailure.from_http_error(
                provider, model, exc, raw_bytes
            ) from exc
        except (urllib.error.URLError, OSError) as exc:
            if retryable_upstream_exception(exc) and attempt + 1 < max_attempts:
                retry_no = attempt + 1
                write_router_activity("retry", provider, model, attempt=retry_no, total=gateway_retries, error=type(exc).__name__, tokens=token_estimate, bytes=byte_estimate)
                router_log("WARN", f"upstream_retry provider={provider} model={model} attempt={retry_no}/{gateway_retries} error={type(exc).__name__} tokens={token_estimate} bytes={byte_estimate}")
                if retry_notice:
                    retry_notice(upstream_retry_message(retry_no, gateway_retries))
                time.sleep(upstream_retry_wait_seconds(retry_no))
                continue
            write_router_activity("error", provider, model, error=type(exc).__name__, tokens=token_estimate, bytes=byte_estimate)
            raise RuntimeError(
                f"upstream request failed provider={provider} model={model}: "
                f"{type(exc).__name__}: {exc}"
            ) from exc
    raise RuntimeError("upstream request failed")


def open_provider_request_with_key_retry(
    url: str,
    req_body: Any,
    headers: dict[str, str],
    timeout: float,
    provider: str,
    pcfg: dict[str, Any],
    model: str,
    *,
    stream: bool = False,
    retry_rate_limits: bool = True,
    services: UpstreamRetryServices,
) -> Any:
    policy = services.policy
    keys = services.keys
    rate_limit = services.rate_limit
    http = services.http
    UPSTREAM_RETRY_HTTP_CODES = policy.upstream_retry_http_codes
    configured_gateway_retries = policy.configured_gateway_retries
    retry_after_exceeds_request_timeout = policy.retry_after_exceeds_request_timeout
    retryable_upstream_exception = policy.retryable_upstream_exception
    upstream_retry_wait_seconds = policy.upstream_retry_wait_seconds
    key_from_request_headers = keys.key_from_request_headers
    provider_api_key_count = keys.provider_api_key_count
    provider_has_live_api_key = keys.provider_has_live_api_key
    provider_headers = keys.provider_headers
    prepare_runtime_headers = keys.prepare_runtime_headers
    register_api_key_cooldown = keys.register_api_key_cooldown
    learn_router_rate_limit_headers = rate_limit.learn_headers
    register_router_rate_limit_backoff = rate_limit.register_backoff
    router_log = rate_limit.log
    write_router_activity = rate_limit.write_activity
    estimate_tokens = http.estimate_tokens
    provider_urlopen = http.provider_urlopen
    upstream_http_error_message = http.upstream_http_error_message
    remote_bridge = pcfg.get(REMOTE_BRIDGE_CONFIG_MARKER) is True
    gateway_retries = 0 if remote_bridge else configured_gateway_retries(pcfg)
    max_attempts = max(1, gateway_retries + 1)
    rate_limit_max_attempts = (
        max_attempts
        if remote_bridge
        else max(max_attempts, provider_api_key_count(provider, pcfg))
    )
    token_estimate = estimate_tokens(req_body)
    byte_estimate = len(json.dumps(req_body, ensure_ascii=False).encode("utf-8"))
    data_bytes = json.dumps(req_body).encode("utf-8")
    endpoint = _upstream_endpoint_identity(url)
    for attempt in range(rate_limit_max_attempts):
        # Runtime-scoped headers may require an interactive step (for example,
        # a one-time CAPTCHA).  Record the already-resolved request target
        # before that step so a timeout or abandoned interaction still leaves
        # a query-free endpoint identity for diagnosis.  ``request`` remains
        # reserved for attempts that actually proceed past header preparation.
        write_router_activity(
            "prepare",
            provider,
            model,
            attempt=attempt + 1,
            total=max_attempts,
            tokens=token_estimate,
            bytes=byte_estimate,
            timeout=timeout,
            stream=stream,
            endpoint=endpoint,
        )
        try:
            headers = prepare_runtime_headers(provider, pcfg, headers)
            write_router_activity(
                "request",
                provider,
                model,
                attempt=attempt + 1,
                total=max_attempts,
                tokens=token_estimate,
                bytes=byte_estimate,
                timeout=timeout,
                stream=stream,
                endpoint=endpoint,
            )
            router_log("INFO", f"upstream_direct_request provider={provider} model={model} endpoint={endpoint} attempt={attempt + 1}/{max_attempts} tokens={token_estimate} bytes={byte_estimate} timeout={timeout}")
            dump_upstream_request(
                url,
                data_bytes,
                router_log,
                headers=headers,
            )
            req = urllib.request.Request(url, data=data_bytes, headers=headers, method="POST")
            resp = provider_urlopen(req, timeout=timeout, provider=provider, pcfg=pcfg)
            learn_router_rate_limit_headers(provider, pcfg, model, resp.headers)
            return resp
        except urllib.error.HTTPError as original_exc:
            raw_bytes = original_exc.read()
            original_exc, raw_bytes = _normalized_provider_http_error(
                provider, original_exc, raw_bytes
            )
            raw = raw_bytes.decode("utf-8", errors="replace")
            error_message = _http_error_log_message(
                upstream_http_error_message(original_exc, raw)
            )
            exc = _preserved_http_error(original_exc, raw_bytes)
            terminal_usage_limit = exc.code == 429 and terminal_usage_limit_error(raw_bytes)
            learn_router_rate_limit_headers(provider, pcfg, model, exc.headers)
            if exc.code == 429:
                register_api_key_cooldown(provider, pcfg, key_from_request_headers(headers), exc.headers)
            if (
                exc.code == 429
                and not terminal_usage_limit
                and retry_rate_limits
                and provider_api_key_count(provider, pcfg) > 1
                and provider_has_live_api_key(provider, pcfg)
                and attempt + 1 < rate_limit_max_attempts
            ):
                retry_no = attempt + 1
                headers = provider_headers(
                    provider, pcfg, headers, None, True
                )
                next_hash = hashlib.sha256(key_from_request_headers(headers).encode("utf-8")).hexdigest()[:12]
                write_router_activity("retry", provider, model, attempt=retry_no, total=rate_limit_max_attempts - 1, code=exc.code, wait=0, tokens=token_estimate, bytes=byte_estimate, stream=stream)
                router_log("WARN", f"upstream_direct_rate_limit_key_retry provider={provider} model={model} attempt={retry_no}/{rate_limit_max_attempts - 1} next_key_hash={next_hash} tokens={token_estimate} bytes={byte_estimate}")
                continue
            if exc.code == 429 and not terminal_usage_limit and retry_rate_limits and attempt + 1 < max_attempts:
                skip_retry, retry_after_seconds = retry_after_exceeds_request_timeout(exc.headers, timeout)
                if skip_retry:
                    write_router_activity("error", provider, model, code=exc.code, retry_after=retry_after_seconds, tokens=token_estimate, bytes=byte_estimate, stream=stream)
                    router_log(
                        "WARN",
                        f"upstream_direct_rate_limit_no_retry provider={provider} model={model} retry_after={retry_after_seconds:.2f}s timeout={timeout:.2f}s tokens={token_estimate} bytes={byte_estimate}",
                    )
                    raise exc from original_exc
                retry_no = attempt + 1
                wait = register_router_rate_limit_backoff(provider, pcfg, model, exc.headers.get("Retry-After"))
                write_router_activity("retry", provider, model, attempt=retry_no, total=gateway_retries, code=exc.code, wait=wait, tokens=token_estimate, bytes=byte_estimate, stream=stream)
                router_log("WARN", f"upstream_direct_rate_limit_retry provider={provider} model={model} attempt={retry_no}/{gateway_retries} wait={wait:.2f}s tokens={token_estimate} bytes={byte_estimate}")
                time.sleep(wait)
                continue
            if exc.code in UPSTREAM_RETRY_HTTP_CODES and attempt + 1 < max_attempts:
                retry_no = attempt + 1
                write_router_activity("retry", provider, model, attempt=retry_no, total=gateway_retries, code=exc.code, tokens=token_estimate, bytes=byte_estimate, stream=stream)
                router_log("WARN", f"upstream_direct_retry provider={provider} model={model} attempt={retry_no}/{gateway_retries} code={exc.code} message={error_message!r} tokens={token_estimate} bytes={byte_estimate}")
                time.sleep(upstream_retry_wait_seconds(retry_no))
                continue
            write_router_activity(
                "error",
                provider,
                model,
                code=exc.code,
                message=error_message,
                tokens=token_estimate,
                bytes=byte_estimate,
                stream=stream,
                endpoint=endpoint,
            )
            router_log(
                "WARN",
                f"upstream_direct_http_error provider={provider} model={model} "
                f"endpoint={endpoint} code={exc.code} message={error_message!r} "
                f"tokens={token_estimate} bytes={byte_estimate}",
            )
            raise exc from original_exc
        except (urllib.error.URLError, OSError) as exc:
            if retryable_upstream_exception(exc) and attempt + 1 < max_attempts:
                retry_no = attempt + 1
                write_router_activity("retry", provider, model, attempt=retry_no, total=gateway_retries, error=type(exc).__name__, tokens=token_estimate, bytes=byte_estimate, stream=stream)
                router_log("WARN", f"upstream_direct_retry provider={provider} model={model} attempt={retry_no}/{gateway_retries} error={type(exc).__name__} tokens={token_estimate} bytes={byte_estimate}")
                time.sleep(upstream_retry_wait_seconds(retry_no))
                continue
            raise RuntimeError(
                f"upstream direct request failed provider={provider} model={model}: "
                f"{type(exc).__name__}: {exc}"
            ) from exc
    raise RuntimeError("upstream direct request failed")


def open_openai_stream_with_rate_retry(
    url: str,
    req_body: Any,
    headers: dict[str, str],
    timeout: float,
    provider: str,
    pcfg: dict[str, Any],
    model: str,
    retry_notice: Callable[[str], None] | None = None,
    *,
    retry_rate_limits: bool = True,
    services: UpstreamRetryServices,
) -> Any:
    policy = services.policy
    keys = services.keys
    rate_limit = services.rate_limit
    http = services.http
    UPSTREAM_RETRY_HTTP_CODES = policy.upstream_retry_http_codes
    configured_gateway_retries = policy.configured_gateway_retries
    retry_after_exceeds_request_timeout = policy.retry_after_exceeds_request_timeout
    retryable_upstream_exception = policy.retryable_upstream_exception
    upstream_rate_limit_retry_message = policy.upstream_rate_limit_retry_message
    upstream_retry_message = policy.upstream_retry_message
    upstream_retry_wait_seconds = policy.upstream_retry_wait_seconds
    key_from_request_headers = keys.key_from_request_headers
    provider_api_key_count = keys.provider_api_key_count
    provider_has_live_api_key = keys.provider_has_live_api_key
    provider_headers = keys.provider_headers
    prepare_runtime_headers = keys.prepare_runtime_headers
    register_api_key_cooldown = keys.register_api_key_cooldown
    learn_router_rate_limit_headers = rate_limit.learn_headers
    register_router_rate_limit_backoff = rate_limit.register_backoff
    router_log = rate_limit.log
    write_router_activity = rate_limit.write_activity
    estimate_tokens = http.estimate_tokens
    provider_stream_idle_timeout_seconds = http.stream_idle_timeout_seconds
    provider_urlopen = http.provider_urlopen
    set_upstream_stream_read_timeout = http.set_stream_read_timeout
    remote_bridge = pcfg.get(REMOTE_BRIDGE_CONFIG_MARKER) is True
    gateway_retries = 0 if remote_bridge else configured_gateway_retries(pcfg)
    max_attempts = max(1, gateway_retries + 1)
    rate_limit_max_attempts = (
        max_attempts
        if remote_bridge
        else max(max_attempts, provider_api_key_count(provider, pcfg))
    )
    token_estimate = estimate_tokens(req_body)
    byte_estimate = len(json.dumps(req_body, ensure_ascii=False).encode("utf-8"))
    data_bytes = json.dumps(req_body).encode("utf-8")
    endpoint = _upstream_endpoint_identity(url)
    for attempt in range(rate_limit_max_attempts):
        write_router_activity(
            "prepare",
            provider,
            model,
            attempt=attempt + 1,
            total=max_attempts,
            tokens=token_estimate,
            bytes=byte_estimate,
            timeout=timeout,
            stream=True,
            endpoint=endpoint,
        )
        try:
            headers = prepare_runtime_headers(provider, pcfg, headers)
            write_router_activity(
                "request",
                provider,
                model,
                attempt=attempt + 1,
                total=max_attempts,
                tokens=token_estimate,
                bytes=byte_estimate,
                timeout=timeout,
                stream=True,
                endpoint=endpoint,
            )
            router_log("INFO", f"upstream_stream_request provider={provider} model={model} endpoint={endpoint} attempt={attempt + 1}/{max_attempts} tokens={token_estimate} bytes={byte_estimate} timeout={timeout}")
            dump_upstream_request(
                url,
                data_bytes,
                router_log,
                headers=headers,
            )
            req = urllib.request.Request(url, data=data_bytes, headers=headers, method="POST")
            resp = provider_urlopen(req, timeout=timeout, provider=provider, pcfg=pcfg)
            set_upstream_stream_read_timeout(resp, provider_stream_idle_timeout_seconds(pcfg))
            learn_router_rate_limit_headers(provider, pcfg, model, resp.headers)
            return resp
        except urllib.error.HTTPError as exc:
            raw_bytes = exc.read()
            exc, raw_bytes = _normalized_provider_http_error(provider, exc, raw_bytes)
            raw = raw_bytes.decode("utf-8", errors="ignore")
            learn_router_rate_limit_headers(provider, pcfg, model, exc.headers)
            terminal_usage_limit = exc.code == 429 and terminal_usage_limit_error(raw)
            if exc.code == 429:
                register_api_key_cooldown(provider, pcfg, key_from_request_headers(headers), exc.headers)
            if (
                exc.code == 429
                and not terminal_usage_limit
                and retry_rate_limits
                and provider_api_key_count(provider, pcfg) > 1
                and provider_has_live_api_key(provider, pcfg)
                and attempt + 1 < rate_limit_max_attempts
            ):
                retry_no = attempt + 1
                headers = provider_headers(
                    provider, pcfg, headers, None, True
                )
                next_hash = hashlib.sha256(key_from_request_headers(headers).encode("utf-8")).hexdigest()[:12]
                write_router_activity("retry", provider, model, attempt=retry_no, total=rate_limit_max_attempts - 1, code=exc.code, wait=0, tokens=token_estimate, bytes=byte_estimate, stream=True)
                router_log("WARN", f"upstream_stream_rate_limit_key_retry provider={provider} model={model} attempt={retry_no}/{rate_limit_max_attempts - 1} next_key_hash={next_hash} tokens={token_estimate} bytes={byte_estimate}")
                continue
            if exc.code == 429 and not terminal_usage_limit and retry_rate_limits and attempt + 1 < max_attempts:
                skip_retry, retry_after_seconds = retry_after_exceeds_request_timeout(exc.headers, timeout)
                if skip_retry:
                    write_router_activity("error", provider, model, code=exc.code, retry_after=retry_after_seconds, tokens=token_estimate, bytes=byte_estimate, stream=True)
                    router_log(
                        "WARN",
                        f"upstream_stream_rate_limit_no_retry provider={provider} model={model} retry_after={retry_after_seconds:.2f}s timeout={timeout:.2f}s tokens={token_estimate} bytes={byte_estimate}",
                    )
                    raise _preserved_http_error(exc, raw_bytes) from exc
                retry_no = attempt + 1
                wait = register_router_rate_limit_backoff(provider, pcfg, model, exc.headers.get("Retry-After"))
                write_router_activity("retry", provider, model, attempt=retry_no, total=gateway_retries, code=exc.code, wait=wait, tokens=token_estimate, bytes=byte_estimate, stream=True)
                router_log("WARN", f"upstream_stream_rate_limit_retry provider={provider} model={model} attempt={retry_no}/{gateway_retries} wait={wait:.2f}s tokens={token_estimate} bytes={byte_estimate}")
                if retry_notice:
                    retry_notice(upstream_rate_limit_retry_message(retry_no, gateway_retries))
                time.sleep(wait)
                # The just-failed key is now resting; re-pick so the retry uses a live key.
                headers = provider_headers(
                    provider, pcfg, headers, None, True
                )
                continue
            if exc.code in UPSTREAM_RETRY_HTTP_CODES and attempt + 1 < max_attempts:
                retry_no = attempt + 1
                write_router_activity("retry", provider, model, attempt=retry_no, total=gateway_retries, code=exc.code, tokens=token_estimate, bytes=byte_estimate, stream=True)
                router_log("WARN", f"upstream_stream_retry provider={provider} model={model} attempt={retry_no}/{gateway_retries} code={exc.code} tokens={token_estimate} bytes={byte_estimate}")
                if retry_notice:
                    retry_notice(upstream_retry_message(retry_no, gateway_retries))
                time.sleep(upstream_retry_wait_seconds(retry_no))
                continue
            write_router_activity("error", provider, model, code=exc.code, tokens=token_estimate, bytes=byte_estimate, stream=True)
            if _preserves_upstream_status(exc):
                raise _preserved_http_error(exc, raw_bytes) from exc
            raise UpstreamFailure.from_http_error(
                provider, model, exc, raw_bytes
            ) from exc
        except (urllib.error.URLError, OSError) as exc:
            error_message = _http_error_log_message(
                f"{type(exc).__name__}: {exc}"
            )
            if retryable_upstream_exception(exc) and attempt + 1 < max_attempts:
                retry_no = attempt + 1
                write_router_activity("retry", provider, model, attempt=retry_no, total=gateway_retries, error=type(exc).__name__, tokens=token_estimate, bytes=byte_estimate, stream=True)
                router_log("WARN", f"upstream_stream_retry provider={provider} model={model} attempt={retry_no}/{gateway_retries} error={type(exc).__name__} tokens={token_estimate} bytes={byte_estimate}")
                if retry_notice:
                    retry_notice(upstream_retry_message(retry_no, gateway_retries))
                time.sleep(upstream_retry_wait_seconds(retry_no))
                continue
            write_router_activity(
                "error",
                provider,
                model,
                error=type(exc).__name__,
                message=error_message,
                tokens=token_estimate,
                bytes=byte_estimate,
                stream=True,
                endpoint=endpoint,
            )
            router_log(
                "WARN",
                f"upstream_stream_transport_error provider={provider} model={model} "
                f"endpoint={endpoint} error={error_message!r} "
                f"tokens={token_estimate} bytes={byte_estimate}",
            )
            raise RuntimeError(
                f"upstream stream request failed provider={provider} model={model}: "
                f"{type(exc).__name__}: {exc}"
            ) from exc
    raise RuntimeError("upstream stream request failed")
