"""Compose the prelaunch application service from bounded UI port groups."""

from __future__ import annotations

from dataclasses import dataclass

from . import prelaunch


@dataclass(frozen=True, slots=True)
class PrelaunchAssembly:
    terminal: prelaunch.PrelaunchTerminal
    config: prelaunch.PrelaunchConfig
    launch_policy: prelaunch.PrelaunchLaunchPolicy
    panel_rows: prelaunch.PrelaunchPanelRows
    mutations: prelaunch.PrelaunchMutations
    secrets: prelaunch.PrelaunchSecrets
    options: prelaunch.PrelaunchOptions

    def services(self) -> prelaunch.PrelaunchServices:
        return prelaunch.PrelaunchServices(
            constants=prelaunch.build_default_prelaunch_constants(),
            terminal=self.terminal,
            config=self.config,
            launch_policy=self.launch_policy,
            panel_rows=self.panel_rows,
            mutations=self.mutations,
            secrets=self.secrets,
            options=self.options,
        )


__all__ = ["PrelaunchAssembly"]
