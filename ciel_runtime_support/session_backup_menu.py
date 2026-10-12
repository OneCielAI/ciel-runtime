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
import shutil
import subprocess
import sys
import textwrap
import time
from pathlib import Path
from typing import Any, Callable, Mapping

from ciel_runtime_support.session_backup_service import (
    SCRIPT,
    load_settings,
    record_state,
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
    "synology": (("address", "Synology QuickConnect ID (or https://host:port)", ""), ("account", "DSM account", ""),
                 ("path", "NAS folder, starting with a shared folder", "/backup/ciel")),
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
    if kind == "synology":
        password = "password stored" if spec.get("password_file") else f"password {spec.get('password') or 'missing'}"
        return f"synology · {spec.get('account')}@{spec.get('address')}:{spec.get('path')} · {password}"
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
        "Full backup now  [reads every file again; other backups skip files unchanged since the last one]",
        "Snapshots of this folder  [list]",
        "Restore a snapshot…",
        "Verify a snapshot…",
        f"Prune old snapshots now  [keep last {schedule['keep_last']} + {schedule['keep_daily']} daily]",
        "── Targets (Enter on a target: use on/off · test · password (Synology) · remove) ──",
        f"  [{'x' if 'local' in selected else ' '}] local  [built-in · {Path(config_dir) / 'backups'}]",
    ]
    values = ["now", "now-full", "list", "restore", "verify", "prune", "__info__", "target:local"]
    for name, spec in sorted(settings["targets"].items()):
        rows.append(f"  [{'x' if name in selected else ' '}] {name}  [{_target_detail(spec)}]")
        values.append(f"target:{name}")
    rows += [
        "  Add a target…  [local folder · SSH · S3 · rclone · Synology QuickConnect]",
        "── Schedule and triggers ──",
        f"Scheduled backups  [{_onoff(schedule['enabled'])}]",
        f"Interval  [{schedule['interval_minutes']} min]",
        f"After each agent turn  [{_onoff(schedule['on_turn_end'])}]",
        f"Minimum minutes between turn backups  [{schedule['min_interval_minutes']}]",
        f"Before a restart  [{_onoff(schedule['before_restart'])}]",
        f"When the session ends  [{_onoff(schedule['on_session_end'])}]",
        f"Include work folder files  [{_onoff(schedule['include_files'])}]",
        f"Exclude from the work folder…  [{', '.join(_excludes(settings, cwd)) or 'defaults only'}]",
        f"Largest single file to back up  [{schedule.get('max_file_mb', 1024):g} MB]",
        f"Parallel transfers  [{schedule.get('parallel_transfers', 8)} at a time]",
        f"Keep snapshots  [last {schedule['keep_last']} + {schedule['keep_daily']} daily]",
        f"Backup key for credentials  [{_key_status(settings)}]",
    ]
    values += ["target-add", "__info__", "toggle-enabled", "interval", "toggle-turn", "min-interval", "toggle-restart",
               "toggle-end", "toggle-files", "excludes", "max-file", "jobs", "keep", "key"]
    folder_rows, folder_values = _folder_section(config_dir, settings, cwd)
    return [*rows, *folder_rows, "Back"], [*values, *folder_values, "back"]


WORKTREE_LABELS = {
    "off": "git worktrees are left out",
    "inside": "worktrees inside this folder are backed up, the ones elsewhere are not",
    "all": "worktrees inside this folder and the ones elsewhere (restored to their own paths) are backed up",
}
_MAX_FOLDER_ROWS = 200


def _folder_choice(settings: dict[str, Any], cwd: Path) -> dict[str, Any]:
    from ciel_runtime_support.workspace_router_selection import workspace_digest

    folders = settings.setdefault("folders", {})
    choice = folders.setdefault(workspace_digest(cwd), {})
    choice.setdefault("skip_top", [])
    if choice.get("worktrees") not in WORKTREE_LABELS:
        choice["worktrees"] = "off"
    return choice


def _worktree_counts(cwd: Path) -> tuple[int, int]:
    from ciel_runtime_support.session_backup_collect import git_worktrees

    base = os.path.normcase(os.path.abspath(cwd)).rstrip("\\/") + os.sep
    paths = [os.path.normcase(os.path.abspath(path)) + os.sep for path in git_worktrees(cwd)]
    inside = sum(1 for path in paths if path.startswith(base))
    return inside, len(paths) - inside


def _folder_section(config_dir: Path, settings: dict[str, Any], cwd: Path) -> tuple[list[str], list[str]]:
    """Work folder rows: the worktree mode and one checkbox per first-level folder (Enter toggles)."""

    import fnmatch

    from ciel_runtime_support.session_backup_collect import DEFAULT_CWD_EXCLUDES, is_linked_worktree

    choice = _folder_choice(json.loads(json.dumps(settings)), cwd)
    skip = set(choice["skip_top"])
    inside, outside = _worktree_counts(cwd)
    state = workspace_state(config_dir, cwd)
    sizes = state.get("folder_sizes") or {}
    measured = state.get("folder_sizes_at")
    rows = [
        "── Work folder: first-level folders (Enter: back up / leave out) ──",
        f"Git worktrees  [{choice['worktrees']} · {inside} in this folder · {outside} elsewhere]",
        f"Measure folder sizes  [{'measured ' + str(measured) if measured else 'not measured yet'}]",
    ]
    values = ["__info__", "worktrees", "measure-folders"]
    try:
        names = sorted(entry.name for entry in os.scandir(cwd) if entry.is_dir(follow_symlinks=False))
    except OSError:
        names = []
    patterns = (*DEFAULT_CWD_EXCLUDES, *_excludes(settings, cwd))
    for name in names[:_MAX_FOLDER_ROWS]:
        size = f" · {_mb(sizes[name])}" if name in sizes else ""
        if any(fnmatch.fnmatch(name, pattern) for pattern in patterns):
            rows.append(f"  [-] {name}  [left out by name (Exclude…){size}]")
            values.append("__info__")
        elif is_linked_worktree(Path(cwd) / name):
            rows.append(f"  [{' ' if choice['worktrees'] == 'off' else 'x'}] {name}  [git worktree · follows Git worktrees{size}]")
            values.append("worktrees")
        else:
            rows.append(f"  [{' ' if name in skip else 'x'}] {name}  [{'left out' if name in skip else 'backed up'}{size}]")
            values.append(f"folder:{name}")
    if len(names) > _MAX_FOLDER_ROWS:
        rows.append(f"  … {len(names) - _MAX_FOLDER_ROWS} more folders (use Exclude… for those)")
        values.append("__info__")
    return rows, values


def _measure_folders(config_dir: Path, settings: dict[str, Any], cwd: Path, output: Callable[[str], None]) -> list[str]:
    from ciel_runtime_support.session_backup_collect import DEFAULT_CWD_EXCLUDES, measure_tree

    choice = _folder_choice(settings, cwd)
    output(f"\nMeasuring {cwd} (up to 20 s)...\n")
    total, files, top, complete = measure_tree(cwd, (*DEFAULT_CWD_EXCLUDES, *_excludes(settings, cwd)),
                                               deadline=time.monotonic() + 20, skip_worktrees=choice["worktrees"] == "off")
    record_state(config_dir, cwd, folder_sizes=top, folder_sizes_at=time.strftime("%m-%d %H:%M"))
    note = "" if complete else " (counting stopped at 20 s; sizes are at least this much)"
    return [f"Measured {len(top)} first-level entries: {_mb(total)} in {files} files{note}."]


def _cli(*args: str, timeout: float = 900) -> tuple[int, Any]:
    result = subprocess.run([sys.executable, str(SCRIPT), "cli", "backup", *args], capture_output=True, text=True,
                            encoding="utf-8", errors="replace",
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
            f"{_mb(stats.get('bytes_uploaded'))} uploaded (snapshot {_mb(stats.get('bytes_total'))})"
            + (f"; {stats['reused_files']} unchanged files not read again" if stats.get("reused_files") else ""))


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

    kind = prompt("Target type (local / ssh / s3 / rclone / synology)", "local").strip().lower()
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
    if kind == "synology":
        problem = _set_nas_password(config_dir, name, spec, prompt)
        if problem:
            return [f"Not added: {problem}"]
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


def _ask_secret(prompt: Prompt, label: str) -> str:
    """A prompt whose typing is not shown, when the menu's prompt supports it."""

    try:
        return prompt(label, "", True)  # type: ignore[call-arg]
    except TypeError:
        return prompt(label, "")


def _set_nas_password(config_dir: Path, name: str, spec: dict[str, Any], prompt: Prompt) -> str:
    """Ask for the NAS password: kept with Windows DPAPI for this user, or an ${ENV} reference. Returns a problem or ""."""

    from ciel_runtime_support.session_backup_synology import SynologyError, store_password

    value = _ask_secret(prompt, "DSM password (not shown; stored encrypted for this Windows user) or ${ENV_NAME}").strip()
    if not value:
        return "no password given"
    if value.startswith("$"):
        spec.pop("password_file", None)
        spec["password"] = value
        return ""
    path = Path(config_dir) / "backup-credentials" / f"{name}.dpapi"
    try:
        store_password(path, value)
    except SynologyError as error:
        return str(error)
    spec.pop("password", None)
    spec["password_file"] = str(path)
    return ""


def _target_action(config_dir: Path, settings: dict[str, Any], name: str, prompt: Prompt) -> list[str]:
    selected = list(settings["default_targets"] or ["local"])
    kind = "local" if name == "local" else settings["targets"][name].get("type")
    actions = "use / test" + (" / password" if kind == "synology" else "") + ("" if name == "local" else " / remove")
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
    if action == "password" and kind == "synology":
        spec = settings["targets"][name]
        problem = _set_nas_password(config_dir, name, spec, prompt)
        if problem:
            return [f"Password not changed: {problem}"]
        save_settings(config_dir, settings)
        return [f"{name}: password {'stored for this Windows user' if spec.get('password_file') else 'taken from ' + spec['password']}."]
    if action == "remove" and name != "local":
        if prompt(f"Remove target {name}? Its snapshots stay where they are. yes/no", "no").strip().lower().startswith("y"):
            removed = settings["targets"].pop(name, None) or {}
            if removed.get("password_file"):
                Path(removed["password_file"]).unlink(missing_ok=True)
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


def _excludes(settings: Mapping[str, Any], cwd: Path) -> list[str]:
    from ciel_runtime_support.workspace_router_selection import workspace_digest

    return list((settings.get("excludes") or {}).get(workspace_digest(cwd)) or [])


def _cancel_requested() -> bool:
    """Esc (or q) pressed while a backup runs."""

    try:
        if os.name == "nt":
            import msvcrt

            while msvcrt.kbhit():
                if msvcrt.getwch() in ("\x1b", "q", "Q"):
                    return True
            return False
        import select

        ready, _, _ = select.select([sys.stdin], [], [], 0)
        return bool(ready) and sys.stdin.readline().strip().lower() in ("", "q", "esc")
    except Exception:  # noqa: BLE001 - no console: no cancel key
        return False


def _progress_line(state: Mapping[str, Any], elapsed: float) -> str:
    if not state:
        return f"Starting the backup ({elapsed:.0f} s)  Esc cancels"
    if state.get("phase") == "measuring":
        return (f"Measuring the work folder: {state.get('files', 0)} files, {_mb(state.get('measured'))} so far "
                f"({elapsed:.0f} s)  Esc cancels")
    if state.get("phase") == "uploading":
        return (f"Uploading: {state.get('in_flight', 0)} chunk(s) still sending, {_mb(state.get('uploaded'))} stored"
                + (f", {_mb(state.get('sent'))} sent" if state.get("sent") else "")
                + f" ({elapsed:.0f} s)  Esc cancels")
    return (f"Backing up: {state.get('files', 0)} files"
            + (f" ({state['unchanged']} unchanged)" if state.get("unchanged") else "")
            + f", {_mb(state.get('read'))} read, {_mb(state.get('uploaded'))} stored"
            + (f", {_mb(state.get('sent'))} sent" if state.get("sent") else "")
            + f" ({elapsed:.0f} s)  Esc cancels")


def _backup_now(config_dir: Path, cwd: Path, output: Callable[[str], None], *, full: bool = False) -> list[str]:
    # One line, rewritten in place: never wider than the console, or it wraps into new lines.
    width = max(40, shutil.get_terminal_size((100, 30)).columns - 2)

    def show(state: dict[str, Any], elapsed: float) -> bool:
        output("\r" + _progress_line(state, elapsed)[:width].ljust(width))
        return not _cancel_requested()

    output("\n")
    result = run_backup(config_dir, cwd, "menu", on_progress=show, full=full)
    output("\n")
    if result.get("ok"):
        lines = [f"Saved snapshot {result.get('id')} to {', '.join(result.get('targets') or [])}.",
                 _incremental(result.get("stats")) + ".",
                 f"Credentials: {result.get('secrets')}"]
        left = result.get("left_out") or {}
        if left.get("folders"):
            lines.append(f"Left out by your choice: {len(left['folders'])} folder(s): {', '.join(left['folders'])[:100]}")
        mode = left.get("worktree_mode") or "off"
        if left.get("worktrees") or mode != "off":
            lines.append(f"Git worktrees: {mode}"
                         + (f"; {len(left['worktrees'])} left out" if left.get("worktrees") else "")
                         + (f"; {len(left['outside_worktrees'])} outside this folder included" if left.get("outside_worktrees") else ""))
        missing = int(result.get("not_backed_up_count") or 0)
        if missing:
            lines.append(f"Not in this backup: {missing} file(s) (file limit or unreadable), e.g.:")
            lines += [f"  {item[:110]}" for item in (result.get("not_backed_up") or [])[:3]]
        return lines
    reason = result.get("skipped") or str(result.get("output") or "").strip() or "no reason was reported"
    # The panel cuts long lines: the reason is wrapped so the numbers stay visible.
    return textwrap.wrap(f"Backup not saved: {reason[:600]}", width=100) or ["Backup not saved."]


def _edit_excludes(config_dir: Path, cwd: Path, prompt: Prompt, output: Callable[[str], None]) -> list[str]:
    from ciel_runtime_support.session_backup_collect import DEFAULT_CWD_EXCLUDES, measure_tree
    from ciel_runtime_support.workspace_router_selection import workspace_digest

    settings = load_settings(config_dir)
    current = _excludes(settings, cwd)
    output(f"\nMeasuring {cwd} (up to 20 s)...\n")
    total, files, top, complete = measure_tree(cwd, (*DEFAULT_CWD_EXCLUDES, *current), deadline=time.monotonic() + 20)
    note = "" if complete else " (counting stopped at 20 s; at least this much)"
    output(f"Work folder as a backup reads it: {_mb(total)} in {files} files{note}\n")
    for name, size in sorted(top.items(), key=lambda item: -item[1])[:12]:
        output(f"  {_mb(size):>12}  {name}\n")
    output(f"Always left out: {', '.join(DEFAULT_CWD_EXCLUDES)}\n")
    answer = prompt("Folder or file names to leave out, comma-separated ('-name' puts one back; blank keeps the list)",
                    ", ".join(current)).strip()
    patterns = list(current)
    for item in (part.strip() for part in answer.split(",")):
        if not item:
            continue
        if item.startswith("-"):
            patterns = [entry for entry in patterns if entry != item[1:]]
        elif item not in patterns:
            patterns.append(item)
    settings.setdefault("excludes", {})[workspace_digest(cwd)] = patterns
    save_settings(config_dir, settings)
    return [f"Left out of this work folder's backups: {', '.join(patterns) or 'nothing beyond the defaults'}."]


def apply(config_dir: Path, cwd: Path, value: str, prompt: Prompt | None = None,
          output: Callable[[str], None] | None = None) -> list[str]:
    """Run one panel action; a failure becomes a message instead of closing the menu."""

    try:
        return _apply(config_dir, cwd, value, prompt or (lambda _label, default: default), output or _write)
    except Exception as error:  # noqa: BLE001 - the menu stays open with the reason
        return [f"Session backup {value} failed: {type(error).__name__}: {str(error)[:300]}"]


def _write(text: str) -> None:
    import sys

    sys.stdout.write(text)
    sys.stdout.flush()


def _apply(config_dir: Path, cwd: Path, value: str, prompt: Prompt, output: Callable[[str], None]) -> list[str]:
    settings = load_settings(config_dir)
    schedule = settings["schedule"]
    if value == "now":
        return _backup_now(config_dir, cwd, output)
    if value == "now-full":
        return _backup_now(config_dir, cwd, output, full=True)
    if value == "excludes":
        return _edit_excludes(config_dir, cwd, prompt, output)
    if value == "measure-folders":
        return _measure_folders(config_dir, settings, cwd, output)
    if value == "worktrees":
        choice = _folder_choice(settings, cwd)
        order = list(WORKTREE_LABELS)
        choice["worktrees"] = order[(order.index(choice["worktrees"]) + 1) % len(order)]
        save_settings(config_dir, settings)
        inside, outside = _worktree_counts(cwd)
        return [f"Git worktrees: {choice['worktrees']} - {WORKTREE_LABELS[choice['worktrees']]}.",
                f"This folder's repository has {inside} worktree(s) inside it and {outside} elsewhere."]
    if value.startswith("folder:"):
        name = value.split(":", 1)[1]
        choice = _folder_choice(settings, cwd)
        skip = list(choice["skip_top"])
        if name in skip:
            skip.remove(name)
            message = f"{name} is backed up again."
        else:
            skip.append(name)
            message = f"{name} is left out of this folder's backups (this first-level folder only, not same-named ones deeper)."
        choice["skip_top"] = sorted(skip)
        save_settings(config_dir, settings)
        return [message]
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
    elif value == "jobs":
        number = _number(prompt, "Chunks compressed and sent at the same time (1-16)", int(schedule.get("parallel_transfers", 8)))
        if not number or not 1 <= number <= 16:
            return ["Enter a whole number from 1 to 16."]
        schedule["parallel_transfers"] = number
        message = f"Backups compress and send {number} chunk(s) at the same time."
    elif value == "max-file":
        number = _number(prompt, "Largest single work-folder file to back up, in MB", int(schedule.get("max_file_mb", 1024)))
        if not number:
            return ["Enter a whole number of MB greater than 0."]
        schedule["max_file_mb"] = number
        message = f"Work-folder files over {number} MB are left out (and listed after each backup)."
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
