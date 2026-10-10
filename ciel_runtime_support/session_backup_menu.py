"""Prelaunch menu panel for session backups: every backup operation is reachable here.

Run a backup, list / verify / restore / prune snapshots, add, test, enable or remove
targets, set the backup key file and every schedule option.  Operations that touch
snapshots run ``ciel-runtime backup ...`` as a child process (the same code path as
the command line and the automatic triggers).
"""

from __future__ import annotations

import json
import os
import secrets
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Callable

from ciel_runtime_support.session_backup_service import (
    SCRIPT,
    load_settings,
    run_backup,
    save_settings,
    workspace_state,
)

Prompt = Callable[[str, str], str]
INTERVALS = (15, 30, 60, 120, 240, 720, 1440)
_TOGGLES = {
    "toggle-enabled": "enabled",
    "toggle-turn": "on_turn_end",
    "toggle-restart": "before_restart",
    "toggle-end": "on_session_end",
    "toggle-files": "include_files",
}
_TARGET_FIELDS = {
    "local": (("path", "Folder (local disk or \\\\server\\share)", ""),),
    "ssh": (("host", "SSH user@host", ""), ("path", "Remote folder", "ciel-backups"), ("port", "Port (blank = 22)", ""),
            ("identity", "Private key file (blank = ssh defaults/agent)", ""), ("known_hosts", "known_hosts file (blank = ssh default)", "")),
    "s3": (("endpoint", "Endpoint URL (e.g. https://s3.amazonaws.com)", ""), ("bucket", "Bucket", ""), ("prefix", "Prefix", "ciel"),
           ("region", "Region", "us-east-1"), ("access_key", "Access key as an environment reference", "${AWS_ACCESS_KEY_ID}"),
           ("secret_key", "Secret key as an environment reference", "${AWS_SECRET_ACCESS_KEY}")),
    "rclone": (("remote", "rclone remote and path (e.g. gdrive:ciel-backups)", ""), ("binary", "rclone executable", "rclone")),
}
_NO_WINDOW = 0x08000000 if os.name == "nt" else 0


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


def _key_status(settings: dict[str, Any]) -> str:
    if settings.get("key_file"):
        return f"file {Path(settings['key_file']).name}" + ("" if Path(settings["key_file"]).is_file() else " (missing)")
    if os.environ.get("CIEL_RUNTIME_BACKUP_KEY"):
        return "CIEL_RUNTIME_BACKUP_KEY"
    return "none - credentials are left out"


def _target_detail(spec: dict[str, Any]) -> str:
    kind = spec.get("type")
    if kind == "local":
        return f"local · {spec.get('path')}"
    if kind == "ssh":
        return f"ssh · {spec.get('host')}:{spec.get('path')}"
    if kind == "s3":
        return f"s3 · {spec.get('endpoint')}/{spec.get('bucket')}/{spec.get('prefix') or ''}"
    if kind == "rclone":
        return f"rclone · {spec.get('remote')}"
    return str(kind)


def summary(config_dir: Path, cwd: Path) -> str:
    settings = load_settings(config_dir)
    schedule = settings["schedule"]
    plan = f"every {schedule['interval_minutes']} min" if schedule["enabled"] else "schedule off"
    targets = ",".join(settings["default_targets"] or ["local"])
    return f"incremental · {plan} · to {targets} · last {_last(config_dir, cwd)}"


def panel_rows(config_dir: Path, cwd: Path) -> tuple[list[str], list[str]]:
    settings = load_settings(config_dir)
    schedule = settings["schedule"]
    selected = set(settings["default_targets"] or ["local"])
    rows = [
        f"Back up this session now  [incremental · last {_last(config_dir, cwd)}]",
        "Snapshots of this folder  [list]",
        "Restore a snapshot…",
        "Verify a snapshot…",
        f"Prune old snapshots now  [keep last {schedule['keep_last']} + {schedule['keep_daily']} daily]",
        "── Targets (Enter on a target: use on/off · test · remove) ──",
        f"  [{'x' if 'local' in selected else ' '}] local  [built-in · {Path(config_dir) / 'backups'}]",
    ]
    values = ["now", "list", "restore", "verify", "prune", "__info__", "target:local"]
    for name, spec in sorted(settings["targets"].items()):
        rows.append(f"  [{'x' if name in selected else ' '}] {name}  [{_target_detail(spec)}]")
        values.append(f"target:{name}")
    rows += [
        "  Add a target…  [local folder · SSH · S3 · rclone]",
        "── Schedule and triggers ──",
        f"Scheduled backups  [{_onoff(schedule['enabled'])}]",
        f"Interval  [{schedule['interval_minutes']} min]",
        f"After each agent turn  [{_onoff(schedule['on_turn_end'])}]",
        f"Minimum minutes between turn backups  [{schedule['min_interval_minutes']}]",
        f"Before a restart  [{_onoff(schedule['before_restart'])}]",
        f"When the session ends  [{_onoff(schedule['on_session_end'])}]",
        f"Include work folder files  [{_onoff(schedule['include_files'])}]",
        f"Keep snapshots  [last {schedule['keep_last']} + {schedule['keep_daily']} daily]",
        f"Backup key for credentials  [{_key_status(settings)}]",
        "Back",
    ]
    values += ["target-add", "__info__", "toggle-enabled", "interval", "toggle-turn", "min-interval", "toggle-restart",
               "toggle-end", "toggle-files", "keep", "key", "back"]
    return rows, values


def _cli(*args: str, timeout: float = 900) -> tuple[int, Any]:
    result = subprocess.run([sys.executable, str(SCRIPT), "cli", "backup", *args], capture_output=True, text=True,
                            timeout=timeout, creationflags=_NO_WINDOW)
    try:
        return result.returncode, json.loads(result.stdout)
    except ValueError:
        return result.returncode, (result.stdout + result.stderr).strip()


def _first_target(settings: dict[str, Any], prompt: Prompt) -> str:
    choices = settings["default_targets"] or ["local"]
    if len(choices) == 1:
        return choices[0]
    names = ["local", *sorted(settings["targets"])]
    answer = prompt(f"Target ({' / '.join(names)})", choices[0]).strip()
    return answer or choices[0]


def _mb(value: object) -> str:
    return f"{int(value or 0) / (1024 * 1024):.1f} MB"


def _incremental(stats: dict[str, Any] | None) -> str:
    stats = stats or {}
    return (f"incremental: {stats.get('uploaded_chunks', 0)} of {stats.get('chunks', 0)} chunks new, "
            f"{_mb(stats.get('bytes_uploaded'))} uploaded (snapshot {_mb(stats.get('bytes_total'))})")


def _snapshot_lines(rows: list[dict[str, Any]], limit: int = 12) -> list[str]:
    if not rows:
        return ["No snapshots of this folder on that target yet."]
    lines = []
    for row in rows[-limit:]:
        lines.append(f"{row['id']}  {row.get('created', '')}  {row.get('trigger') or '-':<11} {row.get('files')} files  "
                     f"{_mb(row.get('bytes'))}  +{_mb(row.get('new_bytes'))} new ({row.get('new_chunks', 0)}/{row.get('chunks', 0)} chunks)  "
                     f"secrets {'yes' if row.get('secrets') else 'no'}" + (f"  {row['label']}" if row.get("label") else ""))
    if len(rows) > limit:
        lines.insert(0, f"(newest {limit} of {len(rows)})")
    return lines


def _list(settings: dict[str, Any], cwd: Path, prompt: Prompt) -> tuple[str, list[dict[str, Any]], list[str]]:
    target = _first_target(settings, prompt)
    code, rows = _cli("list", "--json", "--cwd", str(cwd), "--target", target)
    if code != 0 or not isinstance(rows, list):
        return target, [], [f"Could not list {target}: {str(rows)[:300]}"]
    return target, rows, [f"Snapshots on {target}:", *_snapshot_lines(rows)]


def _probe(name: str, spec: dict[str, Any]) -> str:
    from ciel_runtime_support.session_backup_targets import build_target

    target = build_target(name, spec, os.environ)
    key = f"probe/ciel-backup-probe-{secrets.token_hex(4)}"
    data = secrets.token_bytes(32)
    target.put(key, data)
    flush = getattr(target, "flush", None)
    if flush is not None:
        flush()
    ok = target.get(key) == data
    target.delete(key)
    close = getattr(target, "close", None)
    if close is not None:
        close()
    return "write, read and delete worked" if ok else "read back different bytes"


def _add_target(config_dir: Path, settings: dict[str, Any], prompt: Prompt) -> list[str]:
    from ciel_runtime_support.session_backup_targets import build_target

    kind = prompt("Target type (local / ssh / s3 / rclone)", "local").strip().lower()
    if kind not in _TARGET_FIELDS:
        return [f"Unknown target type {kind!r}."]
    name = prompt("Name for this target", kind).strip()
    if not name or name == "local" or not all(ch.isalnum() or ch in "-_." for ch in name):
        return ["Target names use letters, digits, '-', '_' or '.', and 'local' is the built-in one."]
    spec: dict[str, Any] = {"type": kind}
    for field, label, default in _TARGET_FIELDS[kind]:
        value = prompt(label, default).strip()
        if value:
            spec[field] = value
    try:
        build_target(name, spec, os.environ)
    except ValueError as error:
        return [f"Not added: {error}"]
    messages = []
    if prompt("Test it now (write, read, delete a small file)? yes/no", "yes").strip().lower().startswith("y"):
        try:
            messages.append(f"Test: {_probe(name, spec)}.")
        except Exception as error:  # noqa: BLE001 - shown to the operator
            messages.append(f"Test failed: {type(error).__name__}: {str(error)[:200]} (saved anyway; fix it from the target row).")
    settings["targets"][name] = spec
    if prompt("Use it for backups (add to the selected targets)? yes/no", "yes").strip().lower().startswith("y"):
        selected = settings["default_targets"] or ["local"]
        settings["default_targets"] = [*selected, name] if name not in selected else selected
    save_settings(config_dir, settings)
    return [f"Added target {name} ({_target_detail(spec)}).", *messages]


def _target_action(config_dir: Path, settings: dict[str, Any], name: str, prompt: Prompt) -> list[str]:
    selected = list(settings["default_targets"] or ["local"])
    actions = "use / test" + ("" if name == "local" else " / remove")
    action = prompt(f"{name}: {actions}", "use").strip().lower()
    if action == "use":
        if name in selected:
            if len(selected) == 1:
                return ["At least one target stays selected."]
            selected.remove(name)
            message = f"{name} is no longer used for backups."
        else:
            selected.append(name)
            message = f"{name} is used for backups."
        settings["default_targets"] = selected
        save_settings(config_dir, settings)
        return [message, f"Selected targets: {', '.join(selected)}"]
    if action == "test":
        spec = {"type": "local", "path": str(Path(config_dir) / "backups")} if name == "local" else settings["targets"][name]
        try:
            return [f"{name}: {_probe(name, spec)}."]
        except Exception as error:  # noqa: BLE001 - shown to the operator
            return [f"{name} test failed: {type(error).__name__}: {str(error)[:300]}"]
    if action == "remove" and name != "local":
        if prompt(f"Remove target {name}? Its snapshots stay where they are. yes/no", "no").strip().lower().startswith("y"):
            settings["targets"].pop(name, None)
            settings["default_targets"] = [item for item in selected if item != name]
            save_settings(config_dir, settings)
            return [f"Removed target {name}."]
        return ["Kept."]
    return [f"Unknown action {action!r}."]


def _restore(settings: dict[str, Any], cwd: Path, prompt: Prompt) -> list[str]:
    target, rows, lines = _list(settings, cwd, prompt)
    if not rows:
        return lines
    snapshot = prompt("Snapshot id to restore (blank = newest)", rows[-1]["id"]).strip() or rows[-1]["id"]
    into = prompt("Restore the work folder into (blank = where it was)", "").strip()
    files = prompt("Restore work folder files too? yes/no", "yes").strip().lower().startswith("y")
    confirm = prompt(f"Restore {snapshot} over the current session files? A safety snapshot is taken first. yes/no", "no")
    if not confirm.strip().lower().startswith("y"):
        return ["Restore cancelled."]
    args = ["restore", snapshot, "--json", "--target", target]
    if into:
        args += ["--to-cwd", into]
    if not files:
        args.append("--no-files")
    code, result = _cli(*args)
    if code != 0 or not isinstance(result, dict):
        return [f"Restore failed: {str(result)[:400]}"]
    out = [f"Restored {result.get('written')} files from {snapshot}; credentials "
           f"{'restored' if result.get('secrets_restored') else 'not restored (no backup key)'}.",
           f"Safety snapshot of the previous state: {result.get('safety_snapshot') or '-'}"]
    if result.get("secret_skipped"):
        out.append(f"Left out without a key: {len(result['secret_skipped'])} credential files.")
    out += [f"Continue with: {line}" for line in result.get("next") or []]
    return out


def _number(prompt: Prompt, label: str, current: int) -> int | None:
    text = prompt(label, str(current)).strip()
    try:
        value = int(text)
    except ValueError:
        return None
    return value if value >= 0 else None


def apply(config_dir: Path, cwd: Path, value: str, prompt: Prompt | None = None) -> list[str]:
    prompt = prompt or (lambda _label, default: default)
    settings = load_settings(config_dir)
    schedule = settings["schedule"]
    if value == "now":
        result = run_backup(config_dir, cwd, "menu")
        if result.get("ok"):
            return [f"Saved snapshot {result.get('id')} to {', '.join(result.get('targets') or [])}.",
                    _incremental(result.get("stats")) + ".",
                    f"Credentials: {result.get('secrets')}"]
        return [f"Backup not saved: {result.get('skipped') or str(result.get('output') or '')[:300]}"]
    if value == "list":
        return _list(settings, cwd, prompt)[2]
    if value == "restore":
        return _restore(settings, cwd, prompt)
    if value == "verify":
        target, rows, lines = _list(settings, cwd, prompt)
        if not rows:
            return lines
        snapshot = prompt("Snapshot id to verify (blank = newest)", rows[-1]["id"]).strip() or rows[-1]["id"]
        code, result = _cli("verify", snapshot, "--json", "--target", target)
        if isinstance(result, dict):
            return [f"{snapshot} on {target}: {'complete, every chunk checks out' if result.get('ok') else 'PROBLEMS'}",
                    *[str(item) for item in result.get("problems") or []][:10]]
        return [f"Verify failed: {str(result)[:300]}"]
    if value == "prune":
        target = _first_target(settings, prompt)
        code, result = _cli("prune", "--json", "--cwd", str(cwd), "--target", target)
        if isinstance(result, dict):
            return [f"Pruned {target}: removed {len(result.get('removed') or [])} snapshots, kept {result.get('kept')}, "
                    f"deleted {result.get('chunks_deleted')} unused chunks."]
        return [f"Prune failed: {str(result)[:300]}"]
    if value == "target-add":
        return _add_target(config_dir, settings, prompt)
    if value.startswith("target:"):
        return _target_action(config_dir, settings, value.split(":", 1)[1], prompt)
    if value == "key":
        current = settings.get("key_file") or str(Path(config_dir) / "backup.key")
        path = prompt("Backup key file (blank = none; a new random key is created if the file does not exist)", current).strip()
        if not path:
            settings.pop("key_file", None)
            save_settings(config_dir, settings)
            return ["No backup key file: credentials are left out unless CIEL_RUNTIME_BACKUP_KEY is set."]
        key_path = Path(path).expanduser()
        created = False
        if not key_path.is_file():
            key_path.parent.mkdir(parents=True, exist_ok=True)
            key_path.write_text(secrets.token_hex(32) + "\n", encoding="utf-8")
            created = True
        settings["key_file"] = str(key_path)
        save_settings(config_dir, settings)
        # The status goes first: a long path is cut off at the panel width.
        return ["New random backup key created." if created else "Using the existing backup key file.",
                f"Backup key file: {key_path}",
                "Keep a copy of this file somewhere else: restoring credentials on another machine needs it.",
                "The key file itself is never put into a backup."]
    if value == "interval":
        current = int(schedule["interval_minutes"])
        schedule["interval_minutes"] = next((step for step in INTERVALS if step > current), INTERVALS[0])
        message = f"Scheduled backups run every {schedule['interval_minutes']} minutes when something changed."
    elif value == "min-interval":
        number = _number(prompt, "Minimum minutes between turn-end backups", int(schedule["min_interval_minutes"]))
        if number is None:
            return ["Enter a whole number of minutes (0 or more)."]
        schedule["min_interval_minutes"] = number
        message = f"Turn-end backups at most every {number} minutes."
    elif value == "keep":
        last = _number(prompt, "Keep the newest N snapshots", int(schedule["keep_last"]))
        daily = _number(prompt, "Also keep the newest snapshot of the last N days", int(schedule["keep_daily"]))
        if last is None or daily is None:
            return ["Enter whole numbers (0 or more)."]
        schedule["keep_last"], schedule["keep_daily"] = last, daily
        message = f"Keeping the last {last} snapshots plus one per day for {daily} days."
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
