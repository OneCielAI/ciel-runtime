"""Ownership-aware installer for Claude commands and Codex prompts."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Callable


@dataclass(frozen=True, slots=True)
class CommandAsset:
    content: str
    ownership_markers: tuple[str, ...]


def is_owned_command_file(path: Path, markers: tuple[str, ...]) -> bool:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return False
    return any(marker in text for marker in markers)


@dataclass(frozen=True, slots=True)
class CommandAssetInstaller:
    directory: Path
    warn: Callable[[str], None]

    def install_one(self, name: str, asset: CommandAsset) -> None:
        try:
            self.directory.mkdir(parents=True, exist_ok=True)
            path = self.directory / name
            if path.exists():
                existing = path.read_text(encoding="utf-8", errors="replace")
                if existing == asset.content:
                    return
                if not is_owned_command_file(path, asset.ownership_markers):
                    return
            path.write_text(asset.content, encoding="utf-8")
            try:
                os.chmod(path, 0o600)
            except Exception:
                pass
        except Exception as error:
            self.warn(f"could not install {name} ({type(error).__name__}: {error}).")

    def remove_one(self, name: str, markers: tuple[str, ...]) -> None:
        try:
            path = self.directory / name
            if path.exists() and is_owned_command_file(path, markers):
                path.unlink()
        except Exception as error:
            self.warn(f"could not remove {name} ({type(error).__name__}: {error}).")

    def install_all(
        self,
        assets: dict[str, CommandAsset],
        stale_glob: str | None = None,
        stale_markers: tuple[str, ...] = (),
    ) -> None:
        try:
            self.directory.mkdir(parents=True, exist_ok=True)
            if stale_glob:
                for path in self.directory.glob(stale_glob):
                    if path.name not in assets and is_owned_command_file(path, stale_markers):
                        try:
                            path.unlink()
                        except Exception:
                            pass
            stale_channel = self.directory / "channel.md"
            if stale_channel.exists() and is_owned_command_file(
                stale_channel,
                ("CIEL_RUNTIME_CHANNEL_BRIDGE", "ciel-runtime channel bridge"),
            ):
                try:
                    stale_channel.unlink()
                except Exception:
                    pass
            for name, asset in assets.items():
                self.install_one(name, asset)
        except Exception as error:
            self.warn(f"could not install command assets ({type(error).__name__}: {error}).")

    def remove_all(
        self,
        assets: dict[str, CommandAsset],
        stale_glob: str | None = None,
        stale_markers: tuple[str, ...] = (),
    ) -> None:
        try:
            if not self.directory.exists():
                return
            for name, asset in assets.items():
                path = self.directory / name
                if path.exists() and is_owned_command_file(path, asset.ownership_markers):
                    path.unlink()
            if stale_glob:
                for path in self.directory.glob(stale_glob):
                    if path.name not in assets and is_owned_command_file(path, stale_markers):
                        path.unlink()
        except Exception as error:
            self.warn(f"could not remove command assets ({type(error).__name__}: {error}).")
