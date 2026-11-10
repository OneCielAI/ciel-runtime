"""Runtime composition for manual and ACME-managed router TLS."""
from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Any

from .acme_runtime import ACMEConfig, ACMECertificateManager, StdlibACMEAdapter
from .tls_runtime import TLSBundleStore
from .web_endpoints import web_backend_settings
from .web_tls_runtime import binding_from_settings, wrap_server


def configure_server_transport(server: Any, config: dict[str, Any], *, workspace: str,
                               router_port: int, config_dir: Path,
                               workspace_id: str) -> Any:
    settings = web_backend_settings(config, workspace, router_port)
    if settings.tls_mode == "acme-ip":
        if not settings.tls_server_names:
            raise RuntimeError("ACME IP TLS requires an explicit public IP identifier")
        manager = ACMECertificateManager(
            StdlibACMEAdapter(),
            ACMEConfig(settings.acme_directory_url, settings.tls_server_names,
                       settings.acme_profile, settings.acme_contacts),
            Path(config_dir) / "acme" / workspace_id,
            TLSBundleStore(Path(config_dir) / "tls" / workspace_id),
        )
        active = manager.ensure_certificate()
        settings = replace(settings, tls_cert_file=str(active.cert_file),
                           tls_key_file=str(active.key_file))
    if settings.tls_mode not in ("certificate", "acme-ip"):
        return server
    return wrap_server(server, binding_from_settings(settings))


__all__ = ["configure_server_transport"]
