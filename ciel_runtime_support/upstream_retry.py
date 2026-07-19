"""Shared upstream request retry transport."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import time
from typing import Any, Callable
import urllib.error
import urllib.request


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
    register_api_key_cooldown = keys.register_api_key_cooldown
    learn_router_rate_limit_headers = rate_limit.learn_headers
    register_router_rate_limit_backoff = rate_limit.register_backoff
    router_log = rate_limit.log
    write_router_activity = rate_limit.write_activity
    estimate_tokens = http.estimate_tokens
    provider_urlopen = http.provider_urlopen
    upstream_http_error_message = http.upstream_http_error_message
    gateway_retries = configured_gateway_retries(pcfg)
    max_attempts = max(1, gateway_retries + 1)
    rate_limit_max_attempts = max(max_attempts, provider_api_key_count(provider, pcfg))
    token_estimate = estimate_tokens(req_body)
    byte_estimate = len(json.dumps(req_body, ensure_ascii=False).encode("utf-8"))
    for attempt in range(rate_limit_max_attempts):
        try:
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
            raw = exc.read().decode("utf-8", errors="ignore")
            learn_router_rate_limit_headers(provider, pcfg, model, exc.headers)
            if exc.code == 429:
                register_api_key_cooldown(provider, pcfg, key_from_request_headers(headers), exc.headers)
            if (
                exc.code == 429
                and retry_rate_limits
                and provider_api_key_count(provider, pcfg) > 1
                and provider_has_live_api_key(provider, pcfg)
                and attempt + 1 < rate_limit_max_attempts
            ):
                retry_no = attempt + 1
                headers = provider_headers(provider, pcfg)
                next_hash = hashlib.sha256(key_from_request_headers(headers).encode("utf-8")).hexdigest()[:12]
                write_router_activity("retry", provider, model, attempt=retry_no, total=rate_limit_max_attempts - 1, code=exc.code, wait=0, tokens=token_estimate, bytes=byte_estimate)
                router_log("WARN", f"upstream_rate_limit_key_retry provider={provider} model={model} attempt={retry_no}/{rate_limit_max_attempts - 1} next_key_hash={next_hash} tokens={token_estimate} bytes={byte_estimate}")
                continue
            if exc.code == 429 and retry_rate_limits and attempt + 1 < max_attempts:
                skip_retry, retry_after_seconds = retry_after_exceeds_request_timeout(exc.headers, timeout)
                if skip_retry:
                    write_router_activity("error", provider, model, code=exc.code, retry_after=retry_after_seconds, tokens=token_estimate, bytes=byte_estimate)
                    router_log(
                        "WARN",
                        f"upstream_rate_limit_no_retry provider={provider} model={model} retry_after={retry_after_seconds:.2f}s timeout={timeout:.2f}s tokens={token_estimate} bytes={byte_estimate}",
                    )
                    raise RuntimeError(upstream_http_error_message(exc, raw)) from exc
                retry_no = attempt + 1
                wait = register_router_rate_limit_backoff(provider, pcfg, model, exc.headers.get("Retry-After"))
                write_router_activity("retry", provider, model, attempt=retry_no, total=gateway_retries, code=exc.code, wait=wait, tokens=token_estimate, bytes=byte_estimate)
                router_log("WARN", f"upstream_rate_limit_retry provider={provider} model={model} attempt={retry_no}/{gateway_retries} wait={wait:.2f}s tokens={token_estimate} bytes={byte_estimate}")
                if retry_notice:
                    retry_notice(upstream_rate_limit_retry_message(retry_no, gateway_retries))
                time.sleep(wait)
                # The just-failed key is now resting; re-pick so the retry uses a live key.
                headers = provider_headers(provider, pcfg)
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
            raise RuntimeError(upstream_http_error_message(exc, raw)) from exc
        except (TimeoutError, urllib.error.URLError) as exc:
            if retryable_upstream_exception(exc) and attempt + 1 < max_attempts:
                retry_no = attempt + 1
                write_router_activity("retry", provider, model, attempt=retry_no, total=gateway_retries, error=type(exc).__name__, tokens=token_estimate, bytes=byte_estimate)
                router_log("WARN", f"upstream_retry provider={provider} model={model} attempt={retry_no}/{gateway_retries} error={type(exc).__name__} tokens={token_estimate} bytes={byte_estimate}")
                if retry_notice:
                    retry_notice(upstream_retry_message(retry_no, gateway_retries))
                time.sleep(upstream_retry_wait_seconds(retry_no))
                continue
            write_router_activity("error", provider, model, error=type(exc).__name__, tokens=token_estimate, bytes=byte_estimate)
            raise RuntimeError(f"{type(exc).__name__}: {exc}") from exc
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
    register_api_key_cooldown = keys.register_api_key_cooldown
    learn_router_rate_limit_headers = rate_limit.learn_headers
    register_router_rate_limit_backoff = rate_limit.register_backoff
    router_log = rate_limit.log
    write_router_activity = rate_limit.write_activity
    estimate_tokens = http.estimate_tokens
    provider_urlopen = http.provider_urlopen
    gateway_retries = configured_gateway_retries(pcfg)
    max_attempts = max(1, gateway_retries + 1)
    rate_limit_max_attempts = max(max_attempts, provider_api_key_count(provider, pcfg))
    token_estimate = estimate_tokens(req_body)
    byte_estimate = len(json.dumps(req_body, ensure_ascii=False).encode("utf-8"))
    data_bytes = json.dumps(req_body).encode("utf-8")
    for attempt in range(rate_limit_max_attempts):
        try:
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
            )
            router_log("INFO", f"upstream_direct_request provider={provider} model={model} attempt={attempt + 1}/{max_attempts} tokens={token_estimate} bytes={byte_estimate} timeout={timeout}")
            req = urllib.request.Request(url, data=data_bytes, headers=headers, method="POST")
            resp = provider_urlopen(req, timeout=timeout, provider=provider, pcfg=pcfg)
            learn_router_rate_limit_headers(provider, pcfg, model, resp.headers)
            return resp
        except urllib.error.HTTPError as exc:
            learn_router_rate_limit_headers(provider, pcfg, model, exc.headers)
            if exc.code == 429:
                register_api_key_cooldown(provider, pcfg, key_from_request_headers(headers), exc.headers)
            if (
                exc.code == 429
                and retry_rate_limits
                and provider_api_key_count(provider, pcfg) > 1
                and provider_has_live_api_key(provider, pcfg)
                and attempt + 1 < rate_limit_max_attempts
            ):
                retry_no = attempt + 1
                headers = provider_headers(provider, pcfg)
                next_hash = hashlib.sha256(key_from_request_headers(headers).encode("utf-8")).hexdigest()[:12]
                write_router_activity("retry", provider, model, attempt=retry_no, total=rate_limit_max_attempts - 1, code=exc.code, wait=0, tokens=token_estimate, bytes=byte_estimate, stream=stream)
                router_log("WARN", f"upstream_direct_rate_limit_key_retry provider={provider} model={model} attempt={retry_no}/{rate_limit_max_attempts - 1} next_key_hash={next_hash} tokens={token_estimate} bytes={byte_estimate}")
                continue
            if exc.code == 429 and retry_rate_limits and attempt + 1 < max_attempts:
                skip_retry, retry_after_seconds = retry_after_exceeds_request_timeout(exc.headers, timeout)
                if skip_retry:
                    write_router_activity("error", provider, model, code=exc.code, retry_after=retry_after_seconds, tokens=token_estimate, bytes=byte_estimate, stream=stream)
                    router_log(
                        "WARN",
                        f"upstream_direct_rate_limit_no_retry provider={provider} model={model} retry_after={retry_after_seconds:.2f}s timeout={timeout:.2f}s tokens={token_estimate} bytes={byte_estimate}",
                    )
                    raise
                retry_no = attempt + 1
                wait = register_router_rate_limit_backoff(provider, pcfg, model, exc.headers.get("Retry-After"))
                write_router_activity("retry", provider, model, attempt=retry_no, total=gateway_retries, code=exc.code, wait=wait, tokens=token_estimate, bytes=byte_estimate, stream=stream)
                router_log("WARN", f"upstream_direct_rate_limit_retry provider={provider} model={model} attempt={retry_no}/{gateway_retries} wait={wait:.2f}s tokens={token_estimate} bytes={byte_estimate}")
                time.sleep(wait)
                continue
            if exc.code in UPSTREAM_RETRY_HTTP_CODES and attempt + 1 < max_attempts:
                retry_no = attempt + 1
                write_router_activity("retry", provider, model, attempt=retry_no, total=gateway_retries, code=exc.code, tokens=token_estimate, bytes=byte_estimate, stream=stream)
                router_log("WARN", f"upstream_direct_retry provider={provider} model={model} attempt={retry_no}/{gateway_retries} code={exc.code} tokens={token_estimate} bytes={byte_estimate}")
                time.sleep(upstream_retry_wait_seconds(retry_no))
                continue
            raise
        except (TimeoutError, urllib.error.URLError) as exc:
            if retryable_upstream_exception(exc) and attempt + 1 < max_attempts:
                retry_no = attempt + 1
                write_router_activity("retry", provider, model, attempt=retry_no, total=gateway_retries, error=type(exc).__name__, tokens=token_estimate, bytes=byte_estimate, stream=stream)
                router_log("WARN", f"upstream_direct_retry provider={provider} model={model} attempt={retry_no}/{gateway_retries} error={type(exc).__name__} tokens={token_estimate} bytes={byte_estimate}")
                time.sleep(upstream_retry_wait_seconds(retry_no))
                continue
            raise RuntimeError(f"{type(exc).__name__}: {exc}") from exc
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
    register_api_key_cooldown = keys.register_api_key_cooldown
    learn_router_rate_limit_headers = rate_limit.learn_headers
    register_router_rate_limit_backoff = rate_limit.register_backoff
    router_log = rate_limit.log
    write_router_activity = rate_limit.write_activity
    estimate_tokens = http.estimate_tokens
    provider_stream_idle_timeout_seconds = http.stream_idle_timeout_seconds
    provider_urlopen = http.provider_urlopen
    set_upstream_stream_read_timeout = http.set_stream_read_timeout
    upstream_http_error_message = http.upstream_http_error_message
    gateway_retries = configured_gateway_retries(pcfg)
    max_attempts = max(1, gateway_retries + 1)
    rate_limit_max_attempts = max(max_attempts, provider_api_key_count(provider, pcfg))
    token_estimate = estimate_tokens(req_body)
    byte_estimate = len(json.dumps(req_body, ensure_ascii=False).encode("utf-8"))
    data_bytes = json.dumps(req_body).encode("utf-8")
    for attempt in range(rate_limit_max_attempts):
        try:
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
            )
            router_log("INFO", f"upstream_stream_request provider={provider} model={model} attempt={attempt + 1}/{max_attempts} tokens={token_estimate} bytes={byte_estimate} timeout={timeout}")
            req = urllib.request.Request(url, data=data_bytes, headers=headers, method="POST")
            resp = provider_urlopen(req, timeout=timeout, provider=provider, pcfg=pcfg)
            set_upstream_stream_read_timeout(resp, provider_stream_idle_timeout_seconds(pcfg))
            learn_router_rate_limit_headers(provider, pcfg, model, resp.headers)
            return resp
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode("utf-8", errors="ignore")
            learn_router_rate_limit_headers(provider, pcfg, model, exc.headers)
            if exc.code == 429:
                register_api_key_cooldown(provider, pcfg, key_from_request_headers(headers), exc.headers)
            if (
                exc.code == 429
                and retry_rate_limits
                and provider_api_key_count(provider, pcfg) > 1
                and provider_has_live_api_key(provider, pcfg)
                and attempt + 1 < rate_limit_max_attempts
            ):
                retry_no = attempt + 1
                headers = provider_headers(provider, pcfg)
                next_hash = hashlib.sha256(key_from_request_headers(headers).encode("utf-8")).hexdigest()[:12]
                write_router_activity("retry", provider, model, attempt=retry_no, total=rate_limit_max_attempts - 1, code=exc.code, wait=0, tokens=token_estimate, bytes=byte_estimate, stream=True)
                router_log("WARN", f"upstream_stream_rate_limit_key_retry provider={provider} model={model} attempt={retry_no}/{rate_limit_max_attempts - 1} next_key_hash={next_hash} tokens={token_estimate} bytes={byte_estimate}")
                continue
            if exc.code == 429 and retry_rate_limits and attempt + 1 < max_attempts:
                skip_retry, retry_after_seconds = retry_after_exceeds_request_timeout(exc.headers, timeout)
                if skip_retry:
                    write_router_activity("error", provider, model, code=exc.code, retry_after=retry_after_seconds, tokens=token_estimate, bytes=byte_estimate, stream=True)
                    router_log(
                        "WARN",
                        f"upstream_stream_rate_limit_no_retry provider={provider} model={model} retry_after={retry_after_seconds:.2f}s timeout={timeout:.2f}s tokens={token_estimate} bytes={byte_estimate}",
                    )
                    raise RuntimeError(upstream_http_error_message(exc, raw)) from exc
                retry_no = attempt + 1
                wait = register_router_rate_limit_backoff(provider, pcfg, model, exc.headers.get("Retry-After"))
                write_router_activity("retry", provider, model, attempt=retry_no, total=gateway_retries, code=exc.code, wait=wait, tokens=token_estimate, bytes=byte_estimate, stream=True)
                router_log("WARN", f"upstream_stream_rate_limit_retry provider={provider} model={model} attempt={retry_no}/{gateway_retries} wait={wait:.2f}s tokens={token_estimate} bytes={byte_estimate}")
                if retry_notice:
                    retry_notice(upstream_rate_limit_retry_message(retry_no, gateway_retries))
                time.sleep(wait)
                # The just-failed key is now resting; re-pick so the retry uses a live key.
                headers = provider_headers(provider, pcfg)
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
            raise RuntimeError(upstream_http_error_message(exc, raw)) from exc
        except (TimeoutError, urllib.error.URLError) as exc:
            if retryable_upstream_exception(exc) and attempt + 1 < max_attempts:
                retry_no = attempt + 1
                write_router_activity("retry", provider, model, attempt=retry_no, total=gateway_retries, error=type(exc).__name__, tokens=token_estimate, bytes=byte_estimate, stream=True)
                router_log("WARN", f"upstream_stream_retry provider={provider} model={model} attempt={retry_no}/{gateway_retries} error={type(exc).__name__} tokens={token_estimate} bytes={byte_estimate}")
                if retry_notice:
                    retry_notice(upstream_retry_message(retry_no, gateway_retries))
                time.sleep(upstream_retry_wait_seconds(retry_no))
                continue
            write_router_activity("error", provider, model, error=type(exc).__name__, tokens=token_estimate, bytes=byte_estimate, stream=True)
            raise RuntimeError(f"{type(exc).__name__}: {exc}") from exc
    raise RuntimeError("upstream stream request failed")
