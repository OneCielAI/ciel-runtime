"""Control-plane command for the persistent remote LLM bridge mode."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class RemoteBridgeCliPorts:
    load_config: Callable[[], dict[str, Any]]
    save_config: Callable[[dict[str, Any]], None]
    ensure_token: Callable[[], str]
    token: Callable[[], str]
    serve: Callable[[Any], None]
    output: Callable[[str], None]
    port: int
    effective_enabled: Callable[[dict[str, Any]], bool] = lambda config: bool(
        config.get("remote_bridge", {}).get("enabled", False)
    )


@dataclass(frozen=True, slots=True)
class RemoteBridgeCliController:
    ports: RemoteBridgeCliPorts

    def run(self, args: Any) -> int:
        action = str(getattr(args, "action", None) or "status").strip().lower()
        config = self.ports.load_config()
        settings = config.setdefault("remote_bridge", {})
        if not isinstance(settings, dict):
            settings = {}
            config["remote_bridge"] = settings

        if action in {"enable", "serve"}:
            host = str(getattr(args, "host", None) or settings.get("host") or "0.0.0.0").strip()
            if not host:
                raise SystemExit("Remote bridge host cannot be empty")
            settings.update({"enabled": True, "host": host})
            self.ports.save_config(config)
            token = self.ports.ensure_token()
            effective = self.ports.effective_enabled(config)
            self.ports.output(
                f"Ciel Runtime remote bridge: {'enabled' if effective else 'disabled'}"
            )
            if not effective:
                self.ports.output(
                    "Saved setting: enabled; an environment override keeps the bridge disabled"
                )
            self.ports.output(f"Listen: http://{host}:{self.ports.port}")
            self.ports.output(f"Bridge token: {token}")
            self.ports.output("OpenAI base URL: /v1")
            self.ports.output("Anthropic base URL: /")
            if action == "serve":
                self.ports.serve(args)
            return 0

        if action == "disable":
            settings["enabled"] = False
            self.ports.save_config(config)
            effective = self.ports.effective_enabled(config)
            self.ports.output(
                f"Ciel Runtime remote bridge: {'enabled' if effective else 'disabled'}"
            )
            if effective:
                self.ports.output(
                    "Saved setting: disabled; an environment override keeps the bridge enabled"
                )
            return 0

        if action == "token":
            self.ports.output(self.ports.ensure_token())
            return 0

        if action != "status":
            raise SystemExit(
                "Remote bridge action must be status, enable, disable, token, or serve"
            )
        enabled = self.ports.effective_enabled(config)
        host = str(settings.get("host") or "0.0.0.0")
        token = self.ports.token()
        self.ports.output(
            f"Ciel Runtime remote bridge: {'enabled' if enabled else 'disabled'}"
        )
        self.ports.output(f"Listen: http://{host}:{self.ports.port}")
        self.ports.output(f"Bridge token: {'configured' if token else 'not generated'}")
        return 0


__all__ = ["RemoteBridgeCliController", "RemoteBridgeCliPorts"]
