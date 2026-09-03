"""Native Muse Code CLI launch with subscription-safe authentication."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable


MUSE_INSTALL_URL = "https://dev.meta.ai/install.sh"
MUSE_SUBSCRIPTION_ENV_KEYS = ("META_API_KEY", "MODEL_API_KEY")


def has_option(argv: list[str], *names: str) -> bool:
    return any(
        value in names or any(value.startswith(f"{name}=") for name in names)
        for value in argv
    )


def option_value(argv: list[str], name: str) -> str:
    for index, value in enumerate(argv):
        if value.startswith(f"{name}="):
            return value.split("=", 1)[1].strip()
        if value == name and index + 1 < len(argv):
            return str(argv[index + 1]).strip()
    return ""


@dataclass(frozen=True, slots=True)
class MuseExecutable:
    command: str
    prefix_args: tuple[str, ...] = ()
    platform: str = "native"
    muse_path: str = ""


@dataclass(frozen=True, slots=True)
class MuseProcessPorts:
    find_executable: Callable[[str], str | None]
    run: Callable[..., Any]
    call: Callable[..., int]
    print_line: Callable[..., None]
    environment: dict[str, str]
    augment_path: Callable[[dict[str, str]], str]
    platform_name: str


@dataclass(frozen=True, slots=True)
class MuseConfigurationPorts:
    load: Callable[[], dict[str, Any]]
    current_provider: Callable[[dict[str, Any]], tuple[str, dict[str, Any]]]


@dataclass(frozen=True, slots=True)
class MuseLifecyclePorts:
    materialize_command: Callable[..., tuple[list[str], dict[str, str]]]
    start_router: Callable[[], Any]
    run_with_router: Callable[[Callable[[], int], bool], int]
    call_with_channel_proxy: Callable[..., int]
    channel_delivery_mode: Callable[[dict[str, Any]], str]
    web_backend_requested: Callable[[dict[str, Any]], bool]
    record_launch: Callable[[str, str], None]
    set_transcript_scope: Callable[..., None]


@dataclass(frozen=True, slots=True)
class MuseRuntimeContext:
    process: MuseProcessPorts
    config: MuseConfigurationPorts
    lifecycle: MuseLifecyclePorts

    def _native_executable(self) -> MuseExecutable | None:
        executable = self.process.find_executable("muse")
        if executable:
            return MuseExecutable(str(executable), muse_path=str(executable))
        return None

    def _wsl_executable(self) -> MuseExecutable | None:
        if self.process.platform_name != "nt":
            return None
        wsl = self.process.find_executable("wsl.exe") or self.process.find_executable("wsl")
        if not wsl:
            return None
        result = self.process.run(
            [wsl, "-e", "sh", "-lc", "command -v muse"],
            check=False,
            capture_output=True,
            text=True,
        )
        if result.returncode:
            return None
        muse_path = str(result.stdout or "").strip().splitlines()
        if not muse_path:
            return None
        path = muse_path[-1].strip()
        if not path.startswith("/"):
            return None
        return MuseExecutable(
            str(wsl),
            (
                "-e",
                "env",
                "-u",
                "META_API_KEY",
                "-u",
                "MODEL_API_KEY",
                path,
            ),
            "wsl",
            path,
        )

    def discover(self) -> MuseExecutable | None:
        return self._native_executable() or self._wsl_executable()

    def install_if_missing(self) -> MuseExecutable | None:
        installed = self.discover()
        if installed:
            return installed
        shell = self.process.find_executable("bash")
        command = f"curl -fsSL {MUSE_INSTALL_URL} | bash"
        if self.process.platform_name == "nt":
            wsl = self.process.find_executable("wsl.exe") or self.process.find_executable("wsl")
            if not wsl:
                self.process.print_line(
                    "Muse Code supports macOS and Linux. Install WSL2, then run: "
                    f"wsl bash -lc '{command}'",
                    flush=True,
                )
                return None
            install_command = [wsl, "-e", "bash", "-lc", command]
        elif shell:
            install_command = [shell, "-lc", command]
        else:
            self.process.print_line(
                f"Muse Code is missing. Install it with: {command}", flush=True
            )
            return None
        self.process.print_line("Installing Muse Code from Meta's official installer...", flush=True)
        result = self.process.run(install_command, check=False)
        if result.returncode:
            self.process.print_line(
                f"Muse Code installation failed (exit {result.returncode}).", flush=True
            )
            return None
        return self.discover()

    @staticmethod
    def _model(provider: str, provider_config: dict[str, Any]) -> str:
        configured = str(provider_config.get("current_model") or "").strip()
        if provider == "meta" and configured.startswith("muse-"):
            return configured
        return "muse-spark-1.3"

    @staticmethod
    def _effort(provider: str, provider_config: dict[str, Any]) -> str:
        if provider != "meta":
            return ""
        configured = str(provider_config.get("effort_level") or "").strip().lower()
        configured = {"max": "ultra"}.get(configured, configured)
        if configured in {"none", "minimal", "low", "medium", "high", "xhigh", "ultra"}:
            return configured
        return ""

    def launch(self, passthrough: list[str]) -> int:
        argv = list(passthrough)
        executable = self.install_if_missing()
        if executable is None:
            return 127
        config = self.config.load()
        provider, provider_config = self.config.current_provider(config)
        model = self._model(provider, provider_config)
        options: dict[str, Any] = {"prefix_args": executable.prefix_args}
        if not has_option(argv, "--yolo"):
            options["yolo_args"] = ("--yolo",)
        muse_provider = option_value(argv, "--provider").lower()
        meta_launch = not muse_provider or muse_provider == "meta"
        if meta_launch and model and not has_option(argv, "-m", "--model"):
            options["model"] = model
        effort = self._effort(provider, provider_config)
        if meta_launch and effort and not has_option(argv, "--reasoning-effort"):
            options["reasoning_effort"] = effort

        env = self.process.environment.copy()
        env["PATH"] = self.process.augment_path(env)
        for name in MUSE_SUBSCRIPTION_ENV_KEYS:
            env.pop(name, None)
        command, child_env = self.lifecycle.materialize_command(
            "muse",
            executable.command,
            env,
            provider,
            provider_config,
            mode="native",
            protocol="native",
            cwd=Path.cwd(),
            enable_channels=True,
            passthrough=argv,
            options=options,
        )
        non_session_commands = {
            "--help", "-h", "--version", "-V", "-v", "version", "config", "export",
            "trace", "skills", "sandbox", "schema", "serve", "session-message",
            "auth", "login", "logout", "init",
        }
        is_session = not argv or argv[0] not in non_session_commands
        interactive_session = is_session and (not argv or argv[0] != "exec")
        if is_session:
            self.lifecycle.record_launch(provider, model)
        if interactive_session:
            # Channel turn state is runtime-scoped. Resetting it here prevents
            # a transcript from another CLI from classifying an idle Muse TUI
            # as permanently busy and starving external input delivery.
            self.lifecycle.set_transcript_scope("muse", cwd=Path.cwd())
        manage_router = bool(
            interactive_session
            and (
                self.lifecycle.channel_delivery_mode(config) == "llm"
                or self.lifecycle.web_backend_requested(config)
            )
            and self.lifecycle.start_router()
        )

        def run() -> int:
            if not interactive_session:
                return self.process.call(command, env=child_env)
            return self.lifecycle.call_with_channel_proxy(
                command,
                child_env,
                wake_for_llm_delivery=False,
                synthetic_enter_bytes=None,
                normalize_bare_cr_for_synthetic_enter=False,
                # Muse does not publish its TUI turns in Claude/Codex JSONL.
                # Transcript-based confirmation would therefore retry a
                # successfully typed prompt and duplicate the user message.
                channel_wake_submit_retries=1,
                channel_wake_confirm_submit=False,
                channel_wake_bracketed_paste=True,
            )

        return self.lifecycle.run_with_router(run, manage_router)


@dataclass(frozen=True, slots=True)
class MuseRuntimeCompatibilityApi:
    context: Callable[[], MuseRuntimeContext]

    def discover(self) -> MuseExecutable | None:
        return self.context().discover()

    def install_if_missing(self) -> MuseExecutable | None:
        return self.context().install_if_missing()

    def launch(self, passthrough: list[str]) -> int:
        return self.context().launch(passthrough)


__all__ = [
    "MUSE_INSTALL_URL",
    "MUSE_SUBSCRIPTION_ENV_KEYS",
    "MuseConfigurationPorts",
    "MuseExecutable",
    "MuseLifecyclePorts",
    "MuseProcessPorts",
    "MuseRuntimeCompatibilityApi",
    "MuseRuntimeContext",
    "has_option",
    "option_value",
]
