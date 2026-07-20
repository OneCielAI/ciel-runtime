"""Terminal controller for persisted provider credentials."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable


@dataclass(frozen=True, slots=True)
class CredentialCliPolicy:
    required_providers: frozenset[str]


@dataclass(frozen=True, slots=True)
class CredentialCliPorts:
    normalize_provider: Callable[[str], str]
    load_config: Callable[[], dict[str, Any]]
    key_count: Callable[[str, dict[str, Any]], int]
    primary_key: Callable[[str, dict[str, Any]], str]
    mask: Callable[[str | None], str]
    fingerprint: Callable[[str | None], str]
    clear_requested: Callable[[Any], bool]
    clear: Callable[[str], list[str]]
    store_input: Callable[[str, str], list[str]]
    store_many: Callable[[str, list[str]], list[str]]


@dataclass(frozen=True, slots=True)
class CredentialCliIO:
    stdin_isatty: Callable[[], bool]
    prompt_secret: Callable[[str], str]
    write_line: Callable[[str], None]


@dataclass(frozen=True, slots=True)
class CredentialCliController:
    policy: CredentialCliPolicy
    ports: CredentialCliPorts
    io: CredentialCliIO

    def set_one(self, args: Any) -> None:
        provider = self.ports.normalize_provider(args.provider)
        key = args.key.strip()
        if not key:
            raise SystemExit("No key provided; unchanged.")
        self._write(self.ports.store_input(provider, key))

    def set_many(self, args: Any) -> None:
        provider = self.ports.normalize_provider(args.provider)
        raw = "\n".join(str(item) for item in getattr(args, "keys", []) if str(item).strip())
        keys = self._parse_cli_keys(raw)
        if not keys:
            raise SystemExit("No API keys provided; unchanged.")
        self._write(self.ports.store_many(provider, keys))

    def manage(self, args: Any) -> None:
        if not args.provider:
            self._write_status()
            return
        provider = self.ports.normalize_provider(args.provider)
        action = str(getattr(args, "action", "") or "").strip()
        if self.ports.clear_requested(action):
            self._write(self.ports.clear(provider))
            return
        if not self.io.stdin_isatty():
            self.io.write_line("For security, do not paste API keys into Claude Code chat.")
            self.io.write_line(f"Run this in the SSH terminal instead: ciel-runtimectl api-key {provider}")
            return
        key = self.io.prompt_secret(f"API key for {provider}: ").strip()
        if not key:
            raise SystemExit("No key entered; unchanged.")
        self._write(self.ports.store_input(provider, key))

    def _write_status(self) -> None:
        config = self.ports.load_config()
        self.io.write_line("API key status:")
        for provider, provider_config in config["providers"].items():
            count = self.ports.key_count(provider, provider_config)
            if count > 1:
                label = f"{count} keys (round-robin)"
            elif count == 1:
                label = "set"
            else:
                label = "missing" if provider in self.policy.required_providers else "not required"
            primary = self.ports.primary_key(provider, provider_config)
            suffix = (
                f" (primary {self.ports.mask(primary)}; fp {self.ports.fingerprint(primary)})"
                if count
                else ""
            )
            self.io.write_line(f" {provider:<15} {label}{suffix}")
        self.io.write_line("\nSet securely from terminal: ciel-runtimectl api-key anthropic")
        self.io.write_line("Set multiple keys: ciel-runtimectl set-api-keys deepseek KEY1,KEY2")
        self.io.write_line("For NVIDIA hosted, use: ciel-runtimectl api-key nvidia-hosted")

    def _parse_cli_keys(self, raw: str) -> list[str]:
        # store_many owns canonical parsing; preserve separate CLI empty-input validation.
        return [item.strip() for item in raw.splitlines() if item.strip()]

    def _write(self, lines: list[str]) -> None:
        for line in lines:
            self.io.write_line(line)


__all__ = ["CredentialCliController", "CredentialCliIO", "CredentialCliPolicy", "CredentialCliPorts"]
