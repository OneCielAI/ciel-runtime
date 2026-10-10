"""Prelaunch menu panel for session backups (run now, schedule, triggers)."""

from __future__ import annotations

import time
from pathlib import Path

from ciel_runtime_support.session_backup_service import load_settings, run_backup, save_settings, workspace_state

INTERVALS = (15, 30, 60, 120, 240, 720, 1440)
_TOGGLES = {
    "toggle-enabled": "enabled",
    "toggle-turn": "on_turn_end",
    "toggle-restart": "before_restart",
    "toggle-end": "on_session_end",
    "toggle-files": "include_files",
}


def _onoff(value: object) -> str:
    return "on" if value else "off"


def _last(config_dir: Path, cwd: Path) -> str:
    state = workspace_state(config_dir, cwd)
    if not state.get("last_started"):
        return "never"
    when = time.strftime("%m-%d %H:%M", time.localtime(float(state.get("last_finished") or state["last_started"])))
    if state.get("running"):
        return f"running since {when}"
    return f"{when} {'ok' if state.get('last_ok') else 'failed'}"


def summary(config_dir: Path, cwd: Path) -> str:
    schedule = load_settings(config_dir)["schedule"]
    plan = f"every {schedule['interval_minutes']} min" if schedule["enabled"] else "schedule off"
    return f"{plan} · last {_last(config_dir, cwd)}"


def panel_rows(config_dir: Path, cwd: Path) -> tuple[list[str], list[str]]:
    settings = load_settings(config_dir)
    schedule = settings["schedule"]
    targets = ", ".join(settings["default_targets"] or ["local"])
    return (
        [
            f"Back up this session now  [last {_last(config_dir, cwd)}]",
            f"Scheduled backups  [{_onoff(schedule['enabled'])}]",
            f"Interval  [{schedule['interval_minutes']} min]",
            f"After each agent turn  [{_onoff(schedule['on_turn_end'])} · at most every {schedule['min_interval_minutes']} min]",
            f"Before a restart  [{_onoff(schedule['before_restart'])}]",
            f"When the session ends  [{_onoff(schedule['on_session_end'])}]",
            f"Include work folder files  [{_onoff(schedule['include_files'])}]",
            f"Targets  [{targets}]",
            "Back",
        ],
        ["now", "toggle-enabled", "interval", "toggle-turn", "toggle-restart", "toggle-end", "toggle-files", "targets", "back"],
    )


def apply(config_dir: Path, cwd: Path, value: str) -> list[str]:
    if value == "now":
        result = run_backup(config_dir, cwd, "menu")
        if result.get("ok"):
            return [f"Saved snapshot {result.get('id')} to {', '.join(result.get('targets') or [])}.",
                    f"Secrets: {result.get('secrets')}"]
        return [f"Backup not saved: {result.get('skipped') or str(result.get('output') or '')[:300]}"]
    if value == "targets":
        return [
            "Targets are managed with `ciel-runtime backup target add NAME TYPE key=value ...`",
            "(local path=DIR · ssh host=USER@HOST path=DIR · s3 endpoint=... bucket=... access_key=${ENV} secret_key=${ENV}",
            " · rclone remote=NAME:PATH) and `ciel-runtime backup target default NAME ...`.",
        ]
    settings = load_settings(config_dir)
    schedule = settings["schedule"]
    if value == "interval":
        current = int(schedule["interval_minutes"])
        schedule["interval_minutes"] = next((step for step in INTERVALS if step > current), INTERVALS[0])
        message = f"Scheduled backups run every {schedule['interval_minutes']} minutes when something changed."
    elif value in _TOGGLES:
        key = _TOGGLES[value]
        schedule[key] = not schedule[key]
        message = f"{key.replace('_', ' ')}: {_onoff(schedule[key])}"
    else:
        return []
    save_settings(config_dir, settings)
    # The router's scheduler and the launcher read these settings each time they decide, so this applies now.
    return [message]


__all__ = ["apply", "panel_rows", "summary"]
