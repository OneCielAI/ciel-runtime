"""Upstream retry, credential rotation, and rate-limit bounded context."""

from __future__ import annotations

import urllib.error
from dataclasses import dataclass
from typing import Any, Callable

from .upstream_retry import (
    UpstreamRetryHttp,
    UpstreamRetryKeys,
    UpstreamRetryPolicy,
    UpstreamRetryRateLimit,
    UpstreamRetryServices,
    open_openai_stream_with_rate_retry,
    open_provider_request_with_key_retry,
    post_json_with_rate_retry,
)


@dataclass(frozen=True, slots=True)
class UpstreamRetryErrorPorts:
    project_http_error: Callable[..., str]
    project_retry_message: Callable[..., str]
    first_header: Callable[..., Any]
    parse_retry_after: Callable[..., Any]
    format_duration: Callable[..., str]


@dataclass(frozen=True, slots=True)
class UpstreamRetryPolicyPorts:
    configured_retries: Callable[[dict[str, Any]], int]
    retry_after_exceeds_timeout: Callable[..., bool]
    retryable_exception: Callable[[BaseException], bool]
    retry_wait_seconds: Callable[..., float]
    retry_http_codes: frozenset[int]
    language: Callable[[], str]


@dataclass(frozen=True, slots=True)
class UpstreamRetryCredentialPorts:
    key_from_headers: Callable[..., Any]
    api_key_count: Callable[..., int]
    has_live_api_key: Callable[..., bool]
    headers: Callable[..., dict[str, str]]
    prepare_runtime_headers: Callable[..., dict[str, str]]
    register_cooldown: Callable[..., Any]


@dataclass(frozen=True, slots=True)
class UpstreamRetryRateLimitPorts:
    learn_headers: Callable[..., Any]
    log: Callable[..., Any]
    register_backoff: Callable[..., Any]
    write_activity: Callable[..., Any]


@dataclass(frozen=True, slots=True)
class UpstreamRetryTransportPorts:
    estimate_tokens: Callable[..., int]
    urlopen: Callable[..., Any]
    set_stream_read_timeout: Callable[..., Any]
    stream_idle_timeout_seconds: Callable[..., float]


@dataclass(frozen=True, slots=True)
class UpstreamRetryContext:
    errors: UpstreamRetryErrorPorts
    policy: UpstreamRetryPolicyPorts
    credentials: UpstreamRetryCredentialPorts
    rate_limit: UpstreamRetryRateLimitPorts
    transport: UpstreamRetryTransportPorts

    def http_error_message(
        self,
        exc: urllib.error.HTTPError,
        raw: str | None = None,
    ) -> str:
        return self.errors.project_http_error(
            exc,
            raw,
            first_header=self.errors.first_header,
            parse_retry_after=self.errors.parse_retry_after,
            format_duration=self.errors.format_duration,
        )

    def retry_message(self, attempt: int, total: int) -> str:
        return self.errors.project_retry_message(
            self.policy.language(), attempt, total
        )

    def rate_limit_retry_message(self, attempt: int, total: int) -> str:
        return self.errors.project_retry_message(
            self.policy.language(), attempt, total, rate_limit=True
        )

    def services(self) -> UpstreamRetryServices:
        return UpstreamRetryServices(
            policy=UpstreamRetryPolicy(
                configured_gateway_retries=self.policy.configured_retries,
                retry_after_exceeds_request_timeout=(
                    self.policy.retry_after_exceeds_timeout
                ),
                retryable_upstream_exception=self.policy.retryable_exception,
                upstream_rate_limit_retry_message=self.rate_limit_retry_message,
                upstream_retry_http_codes=self.policy.retry_http_codes,
                upstream_retry_message=self.retry_message,
                upstream_retry_wait_seconds=self.policy.retry_wait_seconds,
            ),
            keys=UpstreamRetryKeys(
                key_from_request_headers=self.credentials.key_from_headers,
                provider_api_key_count=self.credentials.api_key_count,
                provider_has_live_api_key=self.credentials.has_live_api_key,
                provider_headers=self.credentials.headers,
                prepare_runtime_headers=self.credentials.prepare_runtime_headers,
                register_api_key_cooldown=self.credentials.register_cooldown,
            ),
            rate_limit=UpstreamRetryRateLimit(
                learn_headers=self.rate_limit.learn_headers,
                log=self.rate_limit.log,
                register_backoff=self.rate_limit.register_backoff,
                write_activity=self.rate_limit.write_activity,
            ),
            http=UpstreamRetryHttp(
                estimate_tokens=self.transport.estimate_tokens,
                provider_urlopen=self.transport.urlopen,
                set_stream_read_timeout=self.transport.set_stream_read_timeout,
                stream_idle_timeout_seconds=(
                    self.transport.stream_idle_timeout_seconds
                ),
                upstream_http_error_message=self.http_error_message,
            ),
        )

    def post_json(
        self,
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
    ) -> Any:
        return post_json_with_rate_retry(
            url,
            req_body,
            headers,
            timeout,
            provider,
            pcfg,
            model,
            retry_notice,
            retry_rate_limits=retry_rate_limits,
            services=self.services(),
        )

    def open_provider_request(
        self,
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
    ) -> Any:
        return open_provider_request_with_key_retry(
            url,
            req_body,
            headers,
            timeout,
            provider,
            pcfg,
            model,
            stream=stream,
            retry_rate_limits=retry_rate_limits,
            services=self.services(),
        )

    def open_openai_stream(
        self,
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
    ) -> Any:
        return open_openai_stream_with_rate_retry(
            url,
            req_body,
            headers,
            timeout,
            provider,
            pcfg,
            model,
            retry_notice,
            retry_rate_limits=retry_rate_limits,
            services=self.services(),
        )


@dataclass(frozen=True, slots=True)
class UpstreamRetryCompatibilityApi:
    context: Callable[[], UpstreamRetryContext]

    def http_error_message(
        self,
        exc: urllib.error.HTTPError,
        raw: str | None = None,
    ) -> str:
        return self.context().http_error_message(exc, raw)

    def retry_message(self, attempt: int, total: int) -> str:
        return self.context().retry_message(attempt, total)

    def rate_limit_retry_message(self, attempt: int, total: int) -> str:
        return self.context().rate_limit_retry_message(attempt, total)

    def retry_wait_seconds(self, *args: Any, **kwargs: Any) -> float:
        return self.context().policy.retry_wait_seconds(*args, **kwargs)

    def retryable_exception(self, exc: BaseException) -> bool:
        return self.context().policy.retryable_exception(exc)

    def configured_retries(self, pcfg: dict[str, Any]) -> int:
        return self.context().policy.configured_retries(pcfg)

    def services(self) -> UpstreamRetryServices:
        return self.context().services()

    def post_json(self, *args: Any, **kwargs: Any) -> Any:
        return self.context().post_json(*args, **kwargs)

    def open_provider_request(self, *args: Any, **kwargs: Any) -> Any:
        return self.context().open_provider_request(*args, **kwargs)

    def open_openai_stream(self, *args: Any, **kwargs: Any) -> Any:
        return self.context().open_openai_stream(*args, **kwargs)


__all__ = [
    "UpstreamRetryCompatibilityApi",
    "UpstreamRetryContext",
    "UpstreamRetryCredentialPorts",
    "UpstreamRetryErrorPorts",
    "UpstreamRetryPolicyPorts",
    "UpstreamRetryRateLimitPorts",
    "UpstreamRetryTransportPorts",
]
