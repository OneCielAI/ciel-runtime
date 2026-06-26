"""HTTP runtime router contracts for ciel-runtime.

Runtime routers own request paths and feature parity for a local coding
runtime.  They stay dependency-light and receive concrete transport callbacks
from the portable entrypoint.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol


@dataclass(frozen=True)
class RouterCapability:
    """One externally visible router behavior."""

    name: str
    description: str


COMMON_RUNTIME_ROUTER_CAPABILITIES: tuple[str, ...] = (
    "auth_forwarding",
    "sse_stream_proxy",
    "channel_context_injection",
    "pending_delivery_ack",
    "request_observability",
    "upstream_error_mapping",
)


class RuntimeRouter(Protocol):
    """Dispatch contract for one runtime's HTTP routes."""

    name: str
    runtime: str
    protocol: str
    request_paths: tuple[str, ...]
    capabilities: tuple[RouterCapability, ...]

    def can_handle_get(self, path: str, provider: str, pcfg: dict[str, Any]) -> bool:
        """Return whether this router owns a GET path."""

    def handle_get(self, handler: Any, path: str, provider: str, pcfg: dict[str, Any]) -> bool:
        """Handle a GET request.  Return True when consumed."""

    def can_handle_post(self, path: str, provider: str, pcfg: dict[str, Any]) -> bool:
        """Return whether this router owns a POST path."""

    def handle_post(
        self,
        handler: Any,
        cfg: dict[str, Any],
        provider: str,
        pcfg: dict[str, Any],
        path: str,
        body: dict[str, Any],
    ) -> bool:
        """Handle a POST request.  Return True when consumed."""


def capability_names(router: RuntimeRouter) -> set[str]:
    return {capability.name for capability in router.capabilities}


def router_capability_matrix(routers: tuple[RuntimeRouter, ...]) -> dict[str, dict[str, Any]]:
    return {
        router.name: {
            "runtime": router.runtime,
            "protocol": router.protocol,
            "request_paths": list(router.request_paths),
            "capabilities": sorted(capability_names(router)),
        }
        for router in routers
    }


def missing_common_capabilities(
    routers: tuple[RuntimeRouter, ...],
    required: tuple[str, ...] = COMMON_RUNTIME_ROUTER_CAPABILITIES,
) -> dict[str, list[str]]:
    required_set = set(required)
    return {
        router.name: sorted(required_set - capability_names(router))
        for router in routers
        if required_set - capability_names(router)
    }


__all__ = [
    "COMMON_RUNTIME_ROUTER_CAPABILITIES",
    "RouterCapability",
    "RuntimeRouter",
    "capability_names",
    "missing_common_capabilities",
    "router_capability_matrix",
]
