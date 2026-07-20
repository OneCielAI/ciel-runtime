"""Launch command observability and subprocess stderr capture adapters."""

from __future__ import annotations

import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO, Callable


Log = Callable[[str, str], None]


@dataclass(frozen=True, slots=True)
class LaunchCommandDiagnostics:
    log: Log
    mask_secret: Callable[[str], str]
    codex_api_key_env: str

    def agy(self, command: list[str], environment: dict[str, str]) -> None:
        routed = "yes" if "--dangerously-skip-permissions" in command else "no"
        self.log("INFO", f"agy_launch_cmd argv_len={len(command)} routed_flags={routed}")
        summary = [
            f"{key}={environment[key]}"
            for key in ("CIEL_RUNTIME_PROVIDER", "CIEL_RUNTIME_MODEL_ALIAS")
            if key in environment
        ]
        if summary:
            self.log("INFO", "agy_launch_env " + " ".join(summary))

    def claude(self, command: list[str], environment: dict[str, str]) -> None:
        try:
            mcp_index = command.index("--mcp-config") if "--mcp-config" in command else -1
            mcp_value = command[mcp_index + 1] if 0 <= mcp_index < len(command) - 1 else "-"
        except Exception:
            mcp_value = "-"
        channel_specs: list[str] = []
        if "--dangerously-load-development-channels" in command:
            start = command.index("--dangerously-load-development-channels") + 1
            for argument in command[start:]:
                if argument.startswith("--"):
                    break
                channel_specs.append(argument)
        self.log(
            "INFO",
            "claude_launch_cmd mcp_config=%s channels=%s argv_len=%d"
            % (mcp_value, ",".join(channel_specs) or "-", len(command)),
        )
        keys = (
            "ANTHROPIC_BASE_URL",
            "ANTHROPIC_MODEL",
            "ANTHROPIC_API_KEY",
            "ANTHROPIC_AUTH_TOKEN",
            "CIEL_RUNTIME_PROVIDER",
            "CIEL_RUNTIME_MODEL_ALIAS",
            "CLAUDE_CODE_DISABLE_EXPERIMENTAL_BETAS",
            "CLAUDE_CODE_EFFORT_LEVEL",
            "ANTHROPIC_CUSTOM_MODEL_OPTION_SUPPORTED_CAPABILITIES",
            "ANTHROPIC_DEFAULT_HAIKU_MODEL_SUPPORTS",
            "ANTHROPIC_DEFAULT_OPUS_MODEL_SUPPORTS",
            "ANTHROPIC_DEFAULT_SONNET_MODEL_SUPPORTS",
        )
        summary = self._environment_summary(environment, keys)
        if summary:
            self.log("INFO", "claude_launch_env " + " ".join(summary))

    def codex(self, command: list[str], environment: dict[str, str]) -> None:
        provider_args = [
            argument
            for argument in command
            if argument.startswith("model_provider=") or argument.startswith("model_providers.")
        ]
        self.log(
            "INFO",
            f"codex_launch_cmd argv_len={len(command)} provider_overrides={len(provider_args)}",
        )
        summary = self._environment_summary(
            environment,
            ("CIEL_RUNTIME_PROVIDER", "CIEL_RUNTIME_MODEL_ALIAS", self.codex_api_key_env),
        )
        if summary:
            self.log("INFO", "codex_launch_env " + " ".join(summary))

    def codex_app_server(
        self, command: list[str], environment: dict[str, str]
    ) -> None:
        provider_args = [
            argument
            for argument in command
            if argument.startswith("model_provider=")
            or argument.startswith("model_providers.")
        ]
        listen = ""
        for index, argument in enumerate(command):
            if argument == "--listen" and index + 1 < len(command):
                listen = str(command[index + 1])
                break
            if argument.startswith("--listen="):
                listen = argument.split("=", 1)[1]
                break
        self.log(
            "INFO",
            "codex_app_server_launch_cmd argv_len=%d provider_overrides=%d listen=%s"
            % (len(command), len(provider_args), listen or "stdio/default"),
        )
        summary = self._environment_summary(
            environment,
            (
                "CIEL_RUNTIME_PROVIDER",
                "CIEL_RUNTIME_MODEL_ALIAS",
                self.codex_api_key_env,
            ),
        )
        if summary:
            self.log("INFO", "codex_app_server_launch_env " + " ".join(summary))

    def _environment_summary(
        self,
        environment: dict[str, str],
        keys: tuple[str, ...],
    ) -> list[str]:
        summary: list[str] = []
        for key in keys:
            if key not in environment:
                continue
            value = environment[key]
            if "KEY" in key or "TOKEN" in key:
                value = self.mask_secret(value)
            summary.append(f"{key}={value}")
        return summary


@dataclass(frozen=True, slots=True)
class StderrCaptureAdapter:
    config_dir: Path
    stderr_log: Path
    log: Log
    rotation_bytes: int = 2_000_000

    def call(self, command: list[str], environment: dict[str, str]) -> int:
        self.config_dir.mkdir(parents=True, exist_ok=True)
        self._rotate()
        try:
            log_handle = self.stderr_log.open("ab", buffering=0)
        except Exception as error:
            self.log("WARN", f"claude_stderr_capture_open_failed error={type(error).__name__}: {error}")
            return subprocess.call(command, env=environment)
        self._write_header(log_handle)
        try:
            process = subprocess.Popen(command, env=environment, stderr=subprocess.PIPE)
        except Exception as error:
            self.log("WARN", f"claude_stderr_capture_spawn_failed error={type(error).__name__}: {error}")
            self._close(log_handle)
            return subprocess.call(command, env=environment)

        def tee_stderr() -> None:
            try:
                if process.stderr is None:
                    return
                while chunk := process.stderr.read(4096):
                    self._write_console(chunk)
                    try:
                        log_handle.write(chunk)
                    except Exception as error:
                        self.log("WARN", f"claude_stderr_log_tee_failed error={type(error).__name__}: {error}")
            finally:
                self._close(log_handle)

        thread = threading.Thread(target=tee_stderr, daemon=True, name="claude-stderr-tee")
        thread.start()
        return_code = process.wait()
        thread.join(timeout=2.0)
        self.log("INFO", f"claude_exit code={return_code} stderr_log={self.stderr_log}")
        return return_code

    def _rotate(self) -> None:
        try:
            if self.stderr_log.exists() and self.stderr_log.stat().st_size > self.rotation_bytes:
                self.stderr_log.replace(self.stderr_log.with_suffix(".log.1"))
        except Exception as error:
            self.log("WARN", f"claude_stderr_capture_rotate_failed error={type(error).__name__}: {error}")

    def _write_header(self, log_handle: BinaryIO) -> None:
        header = f"\n===== claude launch at {time.strftime('%Y-%m-%dT%H:%M:%S')} =====\n".encode()
        try:
            log_handle.write(header)
        except Exception as error:
            self.log("WARN", f"claude_stderr_capture_header_failed error={type(error).__name__}: {error}")

    def _write_console(self, chunk: bytes) -> None:
        try:
            sys.stderr.buffer.write(chunk)
            sys.stderr.buffer.flush()
        except Exception as error:
            self.log("WARN", f"claude_stderr_console_tee_failed error={type(error).__name__}: {error}")

    def _close(self, log_handle: BinaryIO) -> None:
        try:
            log_handle.close()
        except Exception as error:
            self.log("WARN", f"claude_stderr_capture_close_failed error={type(error).__name__}: {error}")
