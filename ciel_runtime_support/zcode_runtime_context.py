"""ZCode installation, shared Z.AI OAuth, and routed launch context."""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any, Callable


def zcode_settings(*, model: str, base_url: str, api_key: str) -> dict[str, Any]:
    """Build the documented ZCode custom Anthropic-provider configuration."""

    model_id = str(model or "model").strip() or "model"
    return {
        "provider": {
            "zai": {
                "kind": "anthropic",
                "name": "Ciel Runtime",
                "options": {
                    "apiKey": str(api_key or "not-used"),
                    "apiKeyRequired": True,
                    "baseURL": str(base_url).rstrip("/"),
                },
                "headers": {},
                "models": {model_id: {"name": model_id}},
            }
        },
        "model": {"main": f"zai/{model_id}", "lite": f"zai/{model_id}"},
    }


@dataclass(frozen=True, slots=True)
class ZcodeProcessPorts:
    find_executable: Callable[[str], str | None]
    run: Callable[..., Any]
    call: Callable[..., int]
    print_line: Callable[..., None]
    environment: dict[str, str]
    augment_path: Callable[[dict[str, str]], str]


@dataclass(frozen=True, slots=True)
class ZcodeConfigurationPorts:
    load: Callable[[], dict[str, Any]]
    current_provider: Callable[[dict[str, Any]], tuple[str, dict[str, Any]]]
    current_alias: Callable[[dict[str, Any]], str]
    router_auth_token: Callable[[str, dict[str, Any]], str]
    router_base: str
    zai_anthropic_base_url: str
    settings_path: Path
    save_json: Callable[[Path, dict[str, Any], str], None]
    import_oauth_api_key: Callable[[str], list[str]]


@dataclass(frozen=True, slots=True)
class ZcodeLifecyclePorts:
    oauth_action: Callable[..., list[str]]
    apply_endpoint_policy: Callable[[dict[str, Any], str], list[str]]
    start_router: Callable[[], Any]
    run_with_router: Callable[[Callable[[], int], bool], int]
    materialize_command: Callable[..., tuple[list[str], dict[str, str]]]
    record_launch: Callable[[str, str], None]


@dataclass(frozen=True, slots=True)
class ZcodeRuntimeContext:
    process: ZcodeProcessPorts
    config: ZcodeConfigurationPorts
    lifecycle: ZcodeLifecyclePorts

    def install_if_missing(self) -> str:
        executable = self.process.find_executable("zcode")
        if executable:
            return executable
        npm = self.process.find_executable("npm")
        if not npm:
            raise RuntimeError(
                "ZCode CLI is missing; install zcode-app-cli (Node.js 22.19+)."
            )
        self.process.print_line(
            "Installing ZCode CLI (zcode-app-cli)...", flush=True
        )
        result = self.process.run(
            [npm, "install", "-g", "zcode-app-cli"], check=False
        )
        if result.returncode:
            raise RuntimeError(
                f"ZCode CLI installation failed (exit {result.returncode})."
            )
        executable = self.process.find_executable("zcode")
        if not executable:
            raise RuntimeError(
                "ZCode CLI installed but 'zcode' is not available on PATH."
            )
        return executable

    def native_oauth_login(self, no_browser: bool = False) -> int:
        """Run the public ZCode OAuth launcher in the real user profile."""

        executable = self.install_if_missing()
        command = [executable, "login", "--oauth"]
        if no_browser:
            command.append("--no-browser")
        env = self.process.environment.copy()
        env["PATH"] = self.process.augment_path(env)
        return self.process.call(command, env=env)

    def shared_oauth_jwt(self) -> str:
        """Read the official ZCode shared JWT without logging its value."""

        node = self.process.find_executable("node")
        if not node:
            raise RuntimeError("Node.js is required to read ZCode OAuth credentials.")
        helper = Path(__file__).with_name("zcode_credential_reader.cjs")
        credentials = Path.home() / ".zcode" / "v2" / "credentials.json"
        env = self.process.environment.copy()
        env["PATH"] = self.process.augment_path(env)
        result = self.process.run(
            [node, str(helper), str(credentials), "zcodejwttoken"],
            check=False,
            capture_output=True,
            text=True,
            env=env,
        )
        if result.returncode:
            raise RuntimeError("Unable to read the official ZCode OAuth credential.")
        try:
            payload = json.loads(str(result.stdout or ""))
            jwt = str(payload.get("value") or "").strip()
        except (json.JSONDecodeError, AttributeError) as exc:
            raise RuntimeError(
                "The official ZCode OAuth credential response was invalid."
            ) from exc
        if not jwt:
            raise RuntimeError("The official ZCode OAuth credential is missing.")
        return jwt

    @staticmethod
    def _shared_oauth_action(argv: list[str]) -> str | None:
        if not argv:
            return None
        if argv[0] == "login" and (len(argv) == 1 or "--oauth" in argv):
            return "login"
        if argv[0] == "logout":
            return "logout"
        return None

    @staticmethod
    def _shared_oauth_profile(argv: list[str]) -> str:
        for index, value in enumerate(argv):
            if value.startswith("--profile="):
                return value.split("=", 1)[1].strip() or "coding-plan"
            if value == "--profile" and index + 1 < len(argv):
                return str(argv[index + 1]).strip() or "coding-plan"
        return "coding-plan"

    def _run_shared_oauth(self, action: str, argv: list[str]) -> int:
        try:
            lines = self.lifecycle.oauth_action(
                action,
                no_browser="--no-browser" in argv,
                profile=self._shared_oauth_profile(argv),
            )
        except RuntimeError as exc:
            self.process.print_line(f"Z.AI OAuth failed: {exc}", flush=True)
            return 1
        for line in lines:
            self.process.print_line(line, flush=True)
        return 0

    def write_settings(
        self, config: dict[str, Any], provider: str, provider_config: dict[str, Any]
    ) -> Path:
        payload = zcode_settings(
            model=self.config.current_alias(config),
            base_url=self.config.router_base,
            api_key=self.config.router_auth_token(provider, provider_config),
        )
        self.config.save_json(
            self.config.settings_path, payload, "zcode routed settings"
        )
        return self.config.settings_path

    def import_zcode_oauth_credential(self, settings_path: Path) -> list[str]:
        try:
            payload = json.loads(settings_path.read_text(encoding="utf-8"))
            provider = payload.get("provider", {}).get("zai", {})
            options = provider.get("options", {})
            kind = str(provider.get("kind") or "").strip().lower()
            base_url = str(options.get("baseURL") or "").rstrip("/")
            key = str(options.get("apiKey") or "").strip()
        except (OSError, UnicodeError, json.JSONDecodeError, AttributeError):
            return []
        if (
            kind != "anthropic"
            or base_url != self.config.zai_anthropic_base_url.rstrip("/")
            or not key
        ):
            return []
        return self.config.import_oauth_api_key(key)

    def launch(self, passthrough: list[str]) -> int:
        argv = list(passthrough)
        oauth_action = self._shared_oauth_action(argv)
        if oauth_action:
            return self._run_shared_oauth(oauth_action, argv)
        executable = self.install_if_missing()
        config = self.config.load()
        for line in self.lifecycle.apply_endpoint_policy(config, "zcode"):
            self.process.print_line(line, flush=True)
        provider, provider_config = self.config.current_provider(config)
        settings_path = self.write_settings(config, provider, provider_config)
        env = self.process.environment.copy()
        env["PATH"] = self.process.augment_path(env)
        launch_home = settings_path.parents[2]
        env["USERPROFILE"] = str(launch_home)
        env["HOME"] = str(launch_home)
        env["ZCODE_HOME"] = str(launch_home / ".zcode")
        env["ZCODE_STORAGE_DIR"] = str(launch_home / ".zcode")
        command, child_env = self.lifecycle.materialize_command(
            "zcode",
            executable,
            env,
            provider,
            provider_config,
            mode="routed",
            protocol="anthropic_messages",
            cwd=Path.cwd(),
            enable_channels=False,
            passthrough=argv,
            options={},
        )
        manage_router = bool(self.lifecycle.start_router())
        if not argv or argv[0] not in {"--help", "-h", "--version", "-v", "version"}:
            self.lifecycle.record_launch(provider, self.config.current_alias(config))
        result = self.lifecycle.run_with_router(
            lambda: self.process.call(command, env=child_env), manage_router
        )
        if result == 0:
            for line in self.import_zcode_oauth_credential(settings_path):
                self.process.print_line(line, flush=True)
        return result


@dataclass(frozen=True, slots=True)
class ZcodeRuntimeCompatibilityApi:
    context: Callable[[], ZcodeRuntimeContext]

    def install_if_missing(self) -> str:
        return self.context().install_if_missing()

    def launch(self, passthrough: list[str]) -> int:
        return self.context().launch(passthrough)


__all__ = [
    "ZcodeConfigurationPorts",
    "ZcodeLifecyclePorts",
    "ZcodeProcessPorts",
    "ZcodeRuntimeCompatibilityApi",
    "ZcodeRuntimeContext",
    "zcode_settings",
]
