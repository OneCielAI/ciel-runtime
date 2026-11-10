"""Compose validated direct TLS settings with the router HTTP server."""
from __future__ import annotations

from dataclasses import dataclass
import socket
import ssl
from pathlib import Path
from typing import Any

from .tls_runtime import TLSConfig, TLSMode, TLSRuntimeError, load_server_ssl_context
from .web_endpoints import WebBackendSettings


@dataclass(frozen=True, slots=True)
class WebTLSBinding:
    mode: str = "http"
    server_names: tuple[str, ...] = ()
    cert_file: Path | None = None
    key_file: Path | None = None
    status: dict[str, Any] | None = None

    @property
    def enabled(self) -> bool:
        return self.mode in ("certificate", "acme-ip")


def binding_from_settings(settings: WebBackendSettings) -> WebTLSBinding:
    mode = settings.tls_mode
    if mode in ("http", "tailscale"):
        return WebTLSBinding(mode=mode, server_names=settings.tls_server_names,
                             status={"mode": mode, "active": mode == "tailscale"})
    config = TLSConfig(
        mode=TLSMode.MANUAL,
        cert_file=settings.tls_cert_file,
        key_file=settings.tls_key_file,
        server_names=settings.tls_server_names,
    ).validated()
    try:
        _, metadata = load_server_ssl_context(
            config.cert_file, config.key_file, config.server_names)
    except TLSRuntimeError:
        raise
    return WebTLSBinding(
        mode=mode,
        server_names=config.server_names,
        cert_file=config.cert_file,
        key_file=config.key_file,
        status={"mode": mode, "active": True, "certificate": metadata.redacted_status()},
    )


TLS_HANDSHAKE_RECORD = 0x16
HANDSHAKE_TIMEOUT_SECONDS = 15.0
PLAINTEXT_REFUSAL = (b"HTTP/1.1 400 Bad Request\r\nConnection: close\r\nContent-Type: text/plain; charset=utf-8\r\n"
                     b"Content-Length: 30\r\n\r\nHTTPS is required on this port")


def admit_connection(request: Any, client_address: Any, context: ssl.SSLContext) -> Any | None:
    """Return the transport for one accepted connection, or None to drop it.

    The listener stays one port. A TLS ClientHello is wrapped with the
    validated certificate; plain HTTP is accepted only from this host (a
    loopback peer or the bound address itself), so local CLI clients keep
    their unchanged ``http://127.0.0.1`` base while every external peer must
    use HTTPS.
    """
    from .router_access import is_loopback_address

    previous = request.gettimeout()
    request.settimeout(HANDSHAKE_TIMEOUT_SECONDS)
    try:
        first = request.recv(1, socket.MSG_PEEK)
        if not first:
            return None
        if first[0] == TLS_HANDSHAKE_RECORD:
            connection = context.wrap_socket(request, server_side=True)
            connection.settimeout(previous)
            return connection
        peer = str(client_address[0])
        # A client on this host that dials the bound non-loopback address
        # arrives from that same address; no remote peer can complete TCP so.
        if is_loopback_address(peer) or peer == str(request.getsockname()[0]):
            request.settimeout(previous)
            return request
        request.sendall(PLAINTEXT_REFUSAL)
        return None
    except (OSError, ssl.SSLError):
        return None


def wrap_server(server: Any, binding: WebTLSBinding) -> Any:
    """Serve HTTPS to external peers after the certificate bundle was fully validated."""
    if not binding.enabled:
        return server
    context, _ = load_server_ssl_context(
        binding.cert_file, binding.key_file, binding.server_names)
    plain_finish = server.finish_request

    def finish_request(request: Any, client_address: Any) -> None:
        connection = admit_connection(request, client_address, context)
        if connection is None:
            return
        try:
            plain_finish(connection, client_address)
        finally:
            if connection is not request:
                # wrap_socket detached the accepted socket; close the TLS one here.
                try:
                    connection.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
                connection.close()

    server.finish_request = finish_request
    server.ciel_tls = binding.status
    return server


__all__ = ["WebTLSBinding", "admit_connection", "binding_from_settings", "wrap_server"]
