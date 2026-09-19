"""Native Muse Code CLI launch with subscription-safe authentication."""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping


from .muse_cli import muse_passthrough_mapping
from .muse_mcp import (
    router_mcp_decision,
    router_mcp_entry,
    settings_store_for,
)

MUSE_INSTALL_URL = "https://dev.meta.ai/install.sh"
MUSE_SUBSCRIPTION_ENV_KEYS = ("META_API_KEY", "MODEL_API_KEY")
# Launcher-side WSL probes must answer within this deadline. A wedged drvfs
# mount hangs `wsl.exe` while it translates an inherited workspace cwd on that
# drive (live 2026-09-19: `wsl -e sh -lc "command -v muse"` from
# F:\aap.ezonebot never returned while the F: 9p mount was wedged, and the
# launch sat there for 46 minutes with no log output). Probes therefore run
# from the user's home directory - which needs no translation - and under this
# deadline, so a broken WSL surfaces as an error instead of a silent freeze.
WSL_PROBE_TIMEOUT_SECONDS = 20.0
# Routed mode points Muse Code at the Ciel Router instead of Meta's Model API.
# `muse --base-url <URL>` overrides the Meta provider base and Muse then calls
# `<URL>/responses`, so the URL carries the router's `/v1` prefix (captured from
# a real `muse exec` run on 2026-09-18: GET /muse-code/models and
# POST /v1/responses with `Authorization: Bearer <META_API_KEY>`).
MUSE_ROUTER_BASE_PATH = "/v1"
MUSE_ROUTER_FLAG = "--ca-router"
# Local placeholder the router accepts from its own clients (the same value a
# routed Codex launch uses); the router holds the real Model API key.
MUSE_ROUTER_AUTH_TOKEN = "ciel-runtime-router-local-key"
MUSE_ROUTER_AUTH_ENV_KEYS = MUSE_SUBSCRIPTION_ENV_KEYS


def has_option(argv: list[str], *names: str) -> bool:
    return any(
        value in names or any(value.startswith(f"{name}=") for name in names)
        for value in argv
    )


def without_option(argv: list[str], *names: str) -> list[str]:
    """Drop a Ciel-namespaced flag so the runtime CLI never sees it."""

    return [
        value
        for value in argv
        if value not in names
        and not any(value.startswith(f"{name}=") for name in names)
    ]


def router_host_is_loopback(base_url: str) -> bool:
    """Whether a router base URL points at a loopback address."""

    from urllib.parse import urlsplit

    try:
        host = str(urlsplit(str(base_url)).hostname or "").strip().lower()
    except ValueError:
        return False
    return host in {"127.0.0.1", "localhost", "::1", "[::1]", ""} or host.startswith("127.")


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
    transcript_root: Path | None = None


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
    record_launch: Callable[..., None]
    set_transcript_scope: Callable[..., None]
    # (base URL, bearer token Muse must send); the token is the local
    # placeholder for a loopback router and the router's external-access token
    # once the router is bound to an address outside loopback (Windows + WSL).
    router_endpoint: Callable[[], tuple[str, str]] = lambda: ("", MUSE_ROUTER_AUTH_TOKEN)
    log: Callable[[str, str], Any] | None = None


@dataclass(frozen=True, slots=True)
class MuseRuntimeContext:
    process: MuseProcessPorts
    config: MuseConfigurationPorts
    lifecycle: MuseLifecyclePorts

    def _sync_router_mcp(
        self,
        executable: MuseExecutable,
        config: dict[str, Any],
        *,
        manage_router: bool,
    ) -> None:
        """Attach - or clear - the router entry Muse reads from settings.json.

        This is what lets a Muse session restart itself: Muse has no
        ``--mcp-config`` flag, so the router server has to be merged into
        ``~/.config/muse/settings.json`` (inside WSL on Windows). A launch with
        no router behind it removes the entry again.
        """

        log = self.lifecycle.log or (lambda _level, _message: None)
        section = config.get("muse")
        enabled = True
        if isinstance(section, Mapping) and section.get("router_mcp") is not None:
            enabled = bool(section.get("router_mcp"))
        base_url, token = self.lifecycle.router_endpoint()
        wsl = str(executable.platform or "").lower() == "wsl"
        decision = router_mcp_decision(
            enabled=enabled,
            manage_router=manage_router,
            base_url=base_url,
            token=token,
            wsl=wsl,
            loopback=router_host_is_loopback(base_url),
        )
        if not decision.attach:
            log("INFO", f"muse_router_mcp_skipped reason={decision.reason}")
        entry = (
            router_mcp_entry(base_url, token) if decision.attach else None
        )
        store = settings_store_for(wsl=wsl, run=self.process.run, log=log)
        store.sync(entry)

    def _native_executable(self) -> MuseExecutable | None:
        executable = self.process.find_executable("muse")
        if executable:
            path = Path(str(executable)).expanduser()
            home = path.parents[2] if len(path.parents) >= 3 else Path.home()
            return MuseExecutable(
                str(executable),
                muse_path=str(executable),
                transcript_root=home / ".local" / "share" / "muse",
            )
        return None

    def _wsl_run(self, argv: list[str]) -> Any:
        """Run a launcher-side WSL probe under a deadline.

        The probe runs from the user's home directory so ``wsl.exe`` never has
        to translate a workspace cwd on a possibly wedged drive, and returns
        ``None`` when WSL does not answer in time (or cannot be started).
        """

        try:
            return self.process.run(
                argv,
                check=False,
                capture_output=True,
                text=True,
                cwd=str(Path.home()),
                timeout=WSL_PROBE_TIMEOUT_SECONDS,
            )
        except (subprocess.TimeoutExpired, OSError):
            return None

    def _wsl_executable(self) -> MuseExecutable | None:
        if self.process.platform_name != "nt":
            return None
        wsl = self.process.find_executable("wsl.exe") or self.process.find_executable("wsl")
        if not wsl:
            return None
        result = self._wsl_run([wsl, "-e", "sh", "-lc", "command -v muse"])
        if result is None or result.returncode:
            return None
        muse_path = str(result.stdout or "").strip().splitlines()
        if not muse_path:
            return None
        path = muse_path[-1].strip()
        if not path.startswith("/"):
            return None
        root_result = self._wsl_run(
            [wsl, "-e", "sh", "-lc", 'wslpath -w "$HOME/.local/share/muse"']
        )
        root_lines = (
            str(root_result.stdout or "").strip().splitlines()
            if root_result is not None
            else []
        )
        transcript_root = (
            Path(root_lines[-1].strip())
            if root_result is not None and root_result.returncode == 0 and root_lines
            else None
        )
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
            transcript_root,
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
            if self._wsl_run([wsl, "-e", "true"]) is None:
                self.process.print_line(
                    "WSL did not answer within "
                    f"{WSL_PROBE_TIMEOUT_SECONDS:.0f}s, so Muse Code cannot be "
                    "located or installed. Run `wsl --shutdown` in a terminal, "
                    "then launch again.",
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

    def _workspace_reachable(self, executable: MuseExecutable) -> bool:
        """Whether WSL can enter the workspace directory right now.

        Spawning the TUI with a cwd on a wedged drvfs mount hangs inside
        ``wsl.exe`` while it translates that cwd, and the user sees a frozen
        window with no output (live 2026-09-19: F:\\aap.ezonebot while the F:
        9p mount was wedged). Probe first and fail with instructions instead.
        Only a probe timeout blocks the launch - a "path not found" answer is
        left to Muse, which may still handle its own cwd.
        """

        cwd = Path.cwd()
        drive = str(getattr(cwd, "drive", "") or "")
        if len(drive) != 2 or drive[1] != ":":
            return True
        rest = str(cwd)[len(drive) :].replace("\\", "/").lstrip("/")
        linux = f"/mnt/{drive[0].lower()}" + (f"/{rest}" if rest else "")
        wsl = str(executable.command or "")
        if not wsl:
            return True
        if self._wsl_run([wsl, "--cd", linux, "-e", "true"]) is not None:
            return True
        self.process.print_line(
            f"WSL did not answer within {WSL_PROBE_TIMEOUT_SECONDS:.0f}s while "
            f"checking {cwd}. The drive mount may be wedged; run "
            "`wsl --shutdown` in a terminal, then launch again.",
            flush=True,
        )
        return False

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
        routed = has_option(argv, MUSE_ROUTER_FLAG)
        router_token = MUSE_ROUTER_AUTH_TOKEN
        argv = without_option(argv, MUSE_ROUTER_FLAG)
        # Claude-style session flags mean nothing to Muse: translate them into
        # its own `resume` subcommand before anything else reads the argv.
        argv, mapping_notes = muse_passthrough_mapping(argv)
        if mapping_notes:
            log = self.lifecycle.log or (lambda _level, _message: None)
            log("INFO", "muse_passthrough_mapping " + "; ".join(mapping_notes))
        executable = self.install_if_missing()
        if executable is None:
            return 127
        if executable.platform == "wsl" and not self._workspace_reachable(executable):
            return 2
        config = self.config.load()
        provider, provider_config = self.config.current_provider(config)
        model = self._model(provider, provider_config)
        options: dict[str, Any] = {"prefix_args": executable.prefix_args}
        if not has_option(argv, "--yolo"):
            options["yolo_args"] = ("--yolo",)
        muse_provider = option_value(argv, "--provider").lower()
        meta_launch = not muse_provider or muse_provider == "meta"
        if routed:
            # Routed mode reaches Meta through the router, so it must stay on
            # the Meta provider; echo would bypass the router entirely.
            if muse_provider and muse_provider != "meta":
                self.process.print_line(
                    f"Muse Code routed mode requires the meta provider "
                    f"(got --provider {muse_provider}).",
                    flush=True,
                )
                return 2
            meta_launch = True
            if not muse_provider:
                options["provider"] = "meta"
            router_base, router_token = self.lifecycle.router_endpoint()
            router_base = str(router_base or "").rstrip("/")
            if executable.platform == "wsl" and router_host_is_loopback(router_base):
                # The WSL distro cannot reach the Windows loopback, so a router
                # bound to 127.0.0.1 is invisible to Muse Code. Refuse instead
                # of letting every model call fail with a connection error.
                self.process.print_line(
                    "Muse Code runs inside WSL and cannot reach the Ciel Router at "
                    f"{router_base}. Start the router on an address WSL can reach:\n"
                    "  ciel-runtime muse --ca-router --ca-web-address <windows-wsl-ip>\n"
                    "Find the address with: wsl -e sh -lc \"ip route show default | "
                    "awk '{print $3}'\"",
                    flush=True,
                )
                return 2
            if not str(router_token or "").strip():
                # A router outside loopback only accepts external clients when
                # debug external access is enabled; refuse rather than let
                # every model call fail with 401.
                self.process.print_line(
                    "Muse Code routed mode needs the Ciel Router to accept clients "
                    f"from {router_base}, which is outside its loopback. Enable "
                    "router debug external access (ciel-runtime menu -> router debug "
                    "external access) and retry.",
                    flush=True,
                )
                return 2
            options["base_url"] = f"{router_base}{MUSE_ROUTER_BASE_PATH}"
        if meta_launch and model and not has_option(argv, "-m", "--model"):
            options["model"] = model
        effort = self._effort(provider, provider_config)
        if meta_launch and effort and not has_option(argv, "--reasoning-effort"):
            options["reasoning_effort"] = effort

        env = self.process.environment.copy()
        env["PATH"] = self.process.augment_path(env)
        for name in MUSE_SUBSCRIPTION_ENV_KEYS:
            env.pop(name, None)
        if routed:
            # The router authenticates its own clients with the resolved token
            # and uses the configured Model API key upstream.
            for name in MUSE_ROUTER_AUTH_ENV_KEYS:
                env[name] = router_token
        command, child_env = self.lifecycle.materialize_command(
            "muse",
            executable.command,
            env,
            provider,
            provider_config,
            mode="routed" if routed else "native",
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
            self.lifecycle.record_launch(
                provider,
                model,
                "muse-router" if routed else "",
            )
        if interactive_session:
            self.lifecycle.set_transcript_scope(
                "muse",
                cwd=Path.cwd(),
                muse_home=executable.transcript_root,
            )
        # A routed session must keep its router alive for the whole session,
        # including headless `muse exec` runs, because every model call goes
        # through it.
        manage_router = bool(
            (interactive_session or routed)
            and (
                routed
                or self.lifecycle.channel_delivery_mode(config) == "llm"
                or self.lifecycle.web_backend_requested(config)
            )
            and self.lifecycle.start_router()
        )
        if interactive_session:
            self._sync_router_mcp(executable, config, manage_router=manage_router)

        def run() -> int:
            if not interactive_session:
                return self.process.call(command, env=child_env)
            return self.lifecycle.call_with_channel_proxy(
                command,
                child_env,
                wake_for_llm_delivery=False,
                synthetic_enter_bytes=None,
                normalize_bare_cr_for_synthetic_enter=False,
                # Muse's session JSONL uses a different schema from the
                # Claude/Codex turn-confirmation reader. Keep delivery
                # fire-and-forget while the tool-event observer tails it.
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
