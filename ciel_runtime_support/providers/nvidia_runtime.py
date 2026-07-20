"""NVIDIA hosted provider runtime and nvd-claude-proxy lifecycle."""

from __future__ import annotations

from dataclasses import dataclass
import importlib
import importlib.util
import os
from pathlib import Path
import subprocess
import sys
import time
from typing import Any, Callable


@dataclass(frozen=True, slots=True)
class NvidiaProxyRuntimeConfig:
    env_path: Path
    log_path: Path
    package_name: str
    upstream_base_url: str = "https://integrate.api.nvidia.com/v1"


@dataclass(frozen=True, slots=True)
class NvidiaProxyRuntimePorts:
    load_config: Callable[..., dict[str, Any]]
    read_env_file: Callable[[Path], dict[str, str]]
    is_url_up: Callable[[str], bool]
    find_executable: Callable[[str], str | None]
    positive_int: Callable[[Any], int | None]
    http_json: Callable[..., Any]
    join_url: Callable[[str, str], str]


@dataclass(frozen=True, slots=True)
class NvidiaProxyRuntime:
    config: NvidiaProxyRuntimeConfig
    ports: NvidiaProxyRuntimePorts

    def upstream_base_url(self) -> str:
        return self.config.upstream_base_url

    def proxy_base_url(self) -> str:
        environment = self.ports.read_env_file(self.config.env_path)
        host = environment.get("PROXY_HOST") or "127.0.0.1"
        port = environment.get("PROXY_PORT") or "8788"
        return f"http://{host}:{port}"

    def api_key(self) -> str:
        return (
            self.ports.read_env_file(self.config.env_path).get("NVIDIA_API_KEY")
            or os.environ.get("NVIDIA_API_KEY")
            or os.environ.get("NV_API_KEY")
            or ""
        ).strip()

    def install_proxy(self) -> str | None:
        if os.environ.get("CIEL_RUNTIME_AUTO_INSTALL_NCP", "1").lower() in ("0", "false", "no"):
            return None
        self.config.log_path.parent.mkdir(parents=True, exist_ok=True)
        with self.config.log_path.open("ab", buffering=0) as log:
            log.write(b"\n[ciel-runtime] installing nvd-claude-proxy with pip\n")
            process = subprocess.run(
                [sys.executable, "-m", "pip", "install", "--user", "--upgrade", self.config.package_name],
                stdout=log,
                stderr=log,
                timeout=240,
            )
        if process.returncode != 0:
            return None
        importlib.invalidate_caches()
        return self.ports.find_executable("ncp")

    @staticmethod
    def module_available() -> bool:
        return importlib.util.find_spec("nvd_claude_proxy") is not None

    def proxy_executable(self) -> str | None:
        return self.ports.find_executable("nvd-claude-proxy") or self.ports.find_executable("ncp")

    def ensure(self) -> None:
        provider = self.ports.load_config()["providers"]["nvidia-hosted"]
        upstream = provider.get("base_url") or self.upstream_base_url()
        environment = os.environ.copy()
        environment.update(self.ports.read_env_file(self.config.env_path))
        environment["NVIDIA_BASE_URL"] = upstream.rstrip("/")
        environment.setdefault("PROXY_HOST", "127.0.0.1")
        environment.setdefault("PROXY_PORT", "8788")
        environment.setdefault("STORAGE_ENGINE", "sqlite")
        timeout_ms = self.ports.positive_int(provider.get("request_timeout_ms"))
        if timeout_ms:
            environment["REQUEST_TIMEOUT_SECONDS"] = str(max(1, timeout_ms / 1000))
        base_url = f"http://{environment['PROXY_HOST']}:{environment['PROXY_PORT']}"
        if self.ports.is_url_up(f"{base_url}/v1/models"):
            return
        self.config.log_path.parent.mkdir(parents=True, exist_ok=True)
        executable = self.proxy_executable()
        if not (executable or self.module_available()):
            self.install_proxy()
            executable = self.proxy_executable()
        if not (executable or self.module_available()):
            raise RuntimeError(
                "nvd-claude-proxy was not found. Install it with: "
                "python -m pip install --user nvd-claude-proxy"
            )
        creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
        with self.config.log_path.open("ab", buffering=0) as log:
            if executable:
                command = [executable]
                log.write(f"\n[ciel-runtime] starting nvd-claude-proxy executable: {executable}\n".encode())
            else:
                command = [sys.executable, "-m", "nvd_claude_proxy.main"]
                log.write(b"\n[ciel-runtime] starting nvd-claude-proxy module\n")
            subprocess.Popen(
                command,
                stdout=log,
                stderr=log,
                env=environment,
                cwd=str(self.config.env_path.parent),
                creationflags=creation_flags,
            )
        deadline = time.time() + 45
        while time.time() < deadline:
            if self.ports.is_url_up(f"{base_url}/v1/models"):
                return
            time.sleep(0.5)
        raise RuntimeError("nvd-claude-proxy did not become ready")

    def model_id(self, model_id: str) -> str:
        if model_id.startswith("claude-") and not model_id.startswith("ciel-runtime-"):
            return model_id
        try:
            data = self.ports.http_json(
                self.ports.join_url(self.proxy_base_url(), "/v1/models"),
                timeout=3.0,
            )
        except Exception:
            return model_id
        items = data.get("data") if isinstance(data, dict) else None
        if not isinstance(items, list):
            return model_id
        for item in items:
            if not isinstance(item, dict):
                continue
            proxy_id = str(item.get("id") or "").strip()
            upstream_id = str(item.get("nvidia_id") or "").strip()
            if proxy_id == model_id:
                return proxy_id
            if upstream_id and upstream_id == model_id and proxy_id:
                return proxy_id
        return model_id


__all__ = ["NvidiaProxyRuntime", "NvidiaProxyRuntimeConfig", "NvidiaProxyRuntimePorts"]
