"""Official manifest-backed AGY installation and update lifecycle."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path
from typing import Any, Callable
import urllib.request


@dataclass(frozen=True, slots=True)
class AgyInstallerPorts:
    user_bin_dir: Callable[[], Path]
    forced_environment: Callable[[], dict[str, str]]
    find_executable: Callable[[str], str | None]
    version_newer: Callable[[str, str], bool]
    run_upgrade: Callable[..., tuple[int, str]]
    output: Callable[..., None] = print


@dataclass(frozen=True, slots=True)
class AgyInstaller:
    manifest_base_url: str
    ports: AgyInstallerPorts

    def manifest_name(self) -> str:
        arch = "arm64" if platform.machine().lower() in ("arm64", "aarch64") else "amd64"
        platform_name = "windows" if os.name == "nt" else ("darwin" if sys.platform == "darwin" else "linux")
        return f"{platform_name}_{arch}.json"

    def manifest_url(self) -> str:
        override = str(os.environ.get("CIEL_RUNTIME_AGY_MANIFEST_URL") or "").strip()
        return override or f"{self.manifest_base_url}/manifests/{self.manifest_name()}"

    @staticmethod
    def download_file(url: str, target: Path, timeout: float = 120.0) -> None:
        with urllib.request.urlopen(url, timeout=timeout) as response, target.open("wb") as output:
            shutil.copyfileobj(response, output)

    def latest_manifest(self, timeout: float = 15.0) -> dict[str, Any] | None:
        try:
            with urllib.request.urlopen(self.manifest_url(), timeout=timeout) as response:
                data = json.loads(response.read(65536).decode("utf-8", errors="replace"))
            if isinstance(data, dict) and data.get("url") and data.get("version"):
                return data
        except Exception as exc:
            self._print(f"AGY manifest check failed ({type(exc).__name__}); continuing.")
        return None

    @staticmethod
    def current_version(executable: str) -> str:
        try:
            process = subprocess.run([executable, "--version"], text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=8)
        except Exception:
            return ""
        if process.returncode != 0:
            return ""
        match = re.search(r"\d+(?:\.\d+)+(?:[-+][A-Za-z0-9_.-]+)?", process.stdout or "")
        return match.group(0) if match else (process.stdout or "").strip()

    @staticmethod
    def verify_sha512(path: Path, expected: str) -> bool:
        expected = str(expected or "").strip().lower()
        if not expected:
            return True
        digest = hashlib.sha512()
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest().lower() == expected

    def install_from_manifest(self, manifest: dict[str, Any]) -> str | None:
        url = str(manifest.get("url") or "").strip()
        version = str(manifest.get("version") or "").strip()
        checksum = str(manifest.get("sha512") or "").strip()
        if not url:
            self._print("AGY install failed: manifest did not include a download URL.")
            return None
        install_dir = self.ports.user_bin_dir()
        install_dir.mkdir(parents=True, exist_ok=True)
        target = install_dir / ("agy.exe" if os.name == "nt" else "agy")
        suffix = ".exe" if os.name == "nt" else ".tar.gz"
        with tempfile.TemporaryDirectory(prefix="ciel-runtime-agy-") as temporary_dir:
            download = Path(temporary_dir) / f"agy{suffix}"
            self._print(f"Downloading AGY {version or 'latest'} from Google official manifest...")
            self.download_file(url, download)
            if not self.verify_sha512(download, checksum):
                self._print("AGY install failed: sha512 verification did not match manifest.")
                return None
            if os.name == "nt":
                shutil.copy2(download, target)
            elif not self._extract_executable(download, target):
                return None
        try:
            subprocess.run([str(target), "install"], input="y\n", text=True, env=self.ports.forced_environment(), timeout=120, check=False)
        except Exception as exc:
            self._print(f"AGY post-install setup skipped ({type(exc).__name__}); continuing.")
        self._print(f"AGY installed: {target}")
        return str(target)

    def install_if_missing(self) -> str | None:
        executable = self.ports.find_executable("agy")
        if executable:
            return executable
        if os.environ.get("CIEL_RUNTIME_SKIP_AGY_INSTALL") == "1":
            return None
        manifest = self.latest_manifest()
        if not manifest:
            self._print("AGY executable was not found, and the official AGY manifest could not be read.")
            return None
        return self.install_from_manifest(manifest)

    def update_check(self, executable: str, enabled: bool = True) -> str:
        if not enabled or os.environ.get("CIEL_RUNTIME_SKIP_AGY_UPDATE") == "1":
            return executable
        self._print("Checking AGY update before launch...")
        current = self.current_version(executable)
        if current:
            self._print(f"Current AGY version: {current}")
        manifest = self.latest_manifest()
        latest = str((manifest or {}).get("version") or "").strip()
        if current and latest and not self.ports.version_newer(latest, current):
            self._print(f"AGY is up to date ({current}).")
            return executable
        self._print(f"AGY update available: {current or 'unknown'} -> {latest}; upgrading automatically." if latest else "AGY update version could not be confirmed; running native updater.")
        return_code, output = self.ports.run_upgrade([executable, "update"], timeout=300)
        if output:
            self._print(output)
        if return_code != 0:
            if manifest and latest and (not current or self.ports.version_newer(latest, current)):
                return self.install_from_manifest(manifest) or executable
            self._print(f"AGY update failed ({return_code}); continuing with current version.")
            return executable
        updated = self.ports.find_executable("agy") or executable
        new_version = self.current_version(updated)
        if new_version:
            self._print(f"AGY version after update: {new_version}")
        return updated

    def _extract_executable(self, archive_path: Path, target: Path) -> bool:
        with tarfile.open(archive_path, "r:gz") as archive:
            member = next((item for item in archive.getmembers() if Path(item.name).name == "agy" and item.isfile()), None)
            member = member or next((item for item in archive.getmembers() if item.isfile()), None)
            if member is None:
                self._print("AGY install failed: archive did not contain an executable file.")
                return False
            extracted = archive.extractfile(member)
            if extracted is None:
                self._print("AGY install failed: could not extract executable from archive.")
                return False
            with target.open("wb") as output:
                shutil.copyfileobj(extracted, output)
        target.chmod(target.stat().st_mode | 0o755)
        return True

    def _print(self, message: str) -> None:
        self.ports.output(message, flush=True)


__all__ = ["AgyInstaller", "AgyInstallerPorts"]
