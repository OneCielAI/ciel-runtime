"""``ciel-runtime backup`` -- save, list, check and restore agent session snapshots."""

from __future__ import annotations

import argparse
import getpass
import json
import os
import platform
import shutil
import time
import dataclasses
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping

from ciel_runtime_support.session_backup_collect import (
    DEFAULT_CWD_EXCLUDES,
    WORKTREE_MODES,
    CollectOptions,
    default_roots,
    measure_tree,
    outside_worktrees,
)
from ciel_runtime_support.session_backup_ops import (
    create_snapshot,
    prune_snapshots,
    destination_roots,
    live_session_pid,
    restore_snapshot,
    resume_hint,
    verify_snapshot,
)
from ciel_runtime_support.session_backup_secrets import KEY_ENV, backup_key
from ciel_runtime_support.session_backup_service import DEFAULT_SCHEDULE, load_settings, save_settings, workspace_state
from ciel_runtime_support.session_backup_store import (
    DEFAULT_JOBS,
    MAX_JOBS,
    BackupTarget,
    LocalTarget,
    TargetWriteError,
    find_snapshot,
    list_snapshots,
    manifest_key,
)
from ciel_runtime_support.session_backup_targets import TARGET_TYPES, build_target
from ciel_runtime_support.workspace_router_selection import workspace_digest

USAGE = f"""usage: ciel-runtime backup <command> [options]

  create   [--cwd DIR] [--target NAME|DIR ...] [--label TEXT] [--session ID] [--no-files] [--worktrees off|inside|all] [--jobs N]
           [--exclude PATTERN ...] [--key-file FILE] [--trigger NAME] [--full]
  list     [--target NAME|DIR] [--cwd DIR | --all]
  show     ID [--target NAME|DIR]
  verify   ID [--target NAME|DIR]
  restore  ID [--target NAME|DIR] [--to-cwd DIR] [--claude-dir DIR] [--home DIR] [--codex-home DIR]
              [--ciel-dir DIR] [--ciel-ws DIR] [--no-files] [--dry-run] [--force] [--no-safety] [--key-file FILE]
  prune    [--target NAME|DIR] [--cwd DIR] [--keep-last N] [--keep-daily N] [--dry-run]
  schedule [show | set key=value ...]     keys: {", ".join(DEFAULT_SCHEDULE)}
  status   [--cwd DIR]
  target   list | add NAME TYPE [key=value ...] | remove NAME | default NAME ...
           types: {", ".join(TARGET_TYPES)}
  create --prune applies the schedule's keep_last/keep_daily to every target written.
  create skips files unchanged since the newest snapshot on every target (same size, time and file id);
  --full reads every file again.

Credentials are kept only when a backup key is given ({KEY_ENV} or --key-file); they are
encrypted with it and the key is never stored. Without a key, restored sessions need a new sign-in.
"""


@dataclass(frozen=True)
class BackupContext:
    config_dir: Path
    home: Path
    asset_home: Path
    environ: Mapping[str, str]
    versions: Mapping[str, str]
    cwd: Callable[[], Path] = Path.cwd
    output: Callable[[str], None] = print


# Remote targets built for the running command; closed when it ends (a Synology
# target logs out of its DSM session, SSH/rclone remove their staging folder).
_OPENED: list[Any] = []


def _close_opened() -> None:
    while _OPENED:
        close = getattr(_OPENED.pop(), "close", None)
        if callable(close):
            try:
                close()
            except Exception:  # noqa: BLE001 - closing must not hide the command's own result
                pass


def resolve_targets(context: BackupContext, names: list[str]) -> list[BackupTarget]:
    settings = load_settings(context.config_dir)
    wanted = names or list(settings["default_targets"]) or ["local"]
    targets: list[BackupTarget] = []
    for name in wanted:
        if name == "local" and "local" not in settings["targets"]:
            targets.append(LocalTarget(Path(context.config_dir) / "backups", "local"))
        elif name in settings["targets"]:
            targets.append(build_target(name, settings["targets"][name], context.environ))
            _OPENED.append(targets[-1])
        else:
            targets.append(LocalTarget(Path(name).expanduser()))
    return targets


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="ciel-runtime backup", add_help=False)
    parser.add_argument("command", nargs="?")
    parser.add_argument("rest", nargs="*")
    parser.add_argument("--cwd")
    parser.add_argument("--target", action="append", default=[])
    parser.add_argument("--label", default="")
    parser.add_argument("--session", action="append", default=[])
    parser.add_argument("--no-files", action="store_true")
    parser.add_argument("--exclude", action="append", default=[])
    parser.add_argument("--worktrees", choices=WORKTREE_MODES, default=None)
    parser.add_argument("--jobs", type=int, default=None)
    parser.add_argument("--full", action="store_true")
    parser.add_argument("--key-file")
    parser.add_argument("--trigger", default="manual")
    parser.add_argument("--all", action="store_true")
    parser.add_argument("--to-cwd")
    parser.add_argument("--claude-dir")
    parser.add_argument("--home")
    parser.add_argument("--codex-home")
    parser.add_argument("--ciel-dir")
    parser.add_argument("--ciel-ws")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--no-safety", action="store_true")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--prune", action="store_true")
    parser.add_argument("--keep-last", type=int)
    parser.add_argument("--keep-daily", type=int)
    parser.add_argument("--progress-file")
    parser.add_argument("-h", "--help", action="store_true")
    return parser


def _size(value: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            return f"{value:.0f} {unit}" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1024
    return str(value)


def _progress_writer(path: str | None):
    if not path:
        return None
    target = Path(path)

    def write(state: dict[str, Any]) -> None:
        try:
            temporary = target.with_name(target.name + ".tmp")
            temporary.write_text(json.dumps({**state, "at": time.time()}), encoding="utf-8")
            os.replace(temporary, target)
        except OSError:
            pass

    return write


def _gb(value: float) -> str:
    return f"{value / (1024 ** 3):.1f} GB" if value >= 1024 ** 3 else f"{value / (1024 ** 2):.0f} MB"


def _free_and_first(target: LocalTarget, workspace: str) -> tuple[int, bool]:
    """(free bytes where the target lives, no snapshot of this workspace there yet)."""

    probe = target.root
    while not probe.exists() and probe.parent != probe:
        probe = probe.parent
    return shutil.disk_usage(probe).free, not list_snapshots(target, workspace)


def preflight(cwd: Path, targets: list[BackupTarget], excludes: tuple[str, ...], skip: tuple[Path, ...],
              workspace: str, progress: Any = None, max_file_bytes: int = 1024 ** 3, *,
              skip_top: tuple[str, ...] = (), worktrees: str = "off", extra_roots: tuple[Path, ...] = ()) -> str:
    """'' when the backup can go ahead, else why not (a local target without room for a first backup)."""

    local = [target for target in targets if isinstance(target, LocalTarget)]
    if not local:
        return ""
    room = {target.name: _free_and_first(target, workspace) for target in local}
    if all(not first and free >= 1024 ** 3 for free, first in room.values()):
        return ""  # a later backup needs at most 1 GB: walking the whole folder (minutes) would not change the answer
    total, _files, top, _complete = measure_tree(cwd, excludes, skip, progress=progress, max_file_bytes=max_file_bytes,
                                                 skip_top=skip_top, skip_worktrees=worktrees == "off")
    for extra in extra_roots:
        size = measure_tree(extra, excludes, skip, progress=progress, max_file_bytes=max_file_bytes)[0]
        total += size
        top[f"worktree {Path(extra).name}"] = size
    for target in local:
        root = target.root
        free, first = room[target.name]
        needed = total if first else min(total, 1024 ** 3)
        if free < 256 * 1024 ** 2 or needed > free:
            largest = ", ".join(f"{name} {_gb(size)}" for name, size in sorted(top.items(), key=lambda item: -item[1])[:5])
            return (f"not enough space on target {target.name} ({root}): {_gb(free)} free, the work folder is {_gb(total)}"
                    f"{' for a first backup' if first else ''} (largest: {largest}). "
                    "Exclude big folders (menu: Exclude from the work folder) or choose a target with more room.")
    return ""


def workspace_excludes(context: BackupContext, cwd: Path) -> tuple[str, ...]:
    return tuple((load_settings(context.config_dir).get("excludes") or {}).get(workspace_digest(cwd)) or ())


# Skip reasons that come from the user's own choices; they are not "missing" from the backup.
CHOSEN_SKIPS = ("excluded", "not selected", "worktree")


def workspace_options(settings: Mapping[str, Any], cwd: Path) -> dict[str, Any]:
    """Per work folder: first-level folders left out and the worktree mode (default off)."""

    options = (settings.get("folders") or {}).get(workspace_digest(cwd)) or {}
    mode = str(options.get("worktrees") or "off")
    return {"skip_top": tuple(str(name) for name in options.get("skip_top") or ()),
            "worktrees": mode if mode in WORKTREE_MODES else "off"}


def create(context: BackupContext, args: argparse.Namespace) -> dict[str, Any]:
    cwd = Path(args.cwd or context.cwd()).resolve()
    roots = default_roots(cwd, environ=context.environ, home=context.home, asset_home=context.asset_home, config_dir=context.config_dir)
    progress = _progress_writer(getattr(args, "progress_file", None))
    extra_excludes = (*args.exclude, *workspace_excludes(context, cwd))
    max_file_bytes = int(float(load_settings(context.config_dir)["schedule"].get("max_file_mb") or 1024) * 1048576)
    chosen = workspace_options(load_settings(context.config_dir), cwd)
    worktrees = args.worktrees or chosen["worktrees"]
    if worktrees == "all" and not args.no_files:
        roots = dataclasses.replace(roots, worktrees=tuple(outside_worktrees(cwd)))
    options = CollectOptions(
        max_file_bytes=max_file_bytes,
        include_files=not args.no_files,
        session_ids=tuple(args.session),
        excludes=DEFAULT_CWD_EXCLUDES,
        extra_excludes=tuple(extra_excludes),
        label=args.label,
        trigger=args.trigger,
        skip_paths=tuple(path for path in (args.key_file, load_settings(context.config_dir).get("key_file")) if path),
        progress=progress,
        skip_top=chosen["skip_top"],
        worktrees=worktrees,
    )
    targets = resolve_targets(context, args.target)
    if options.include_files:
        from ciel_runtime_support.session_backup_collect import workspace_label

        problem = preflight(cwd, targets, (*DEFAULT_CWD_EXCLUDES, *extra_excludes),
                            (roots.claude, roots.codex, roots.ciel, roots.ciel_ws),
                            workspace_label(cwd, platform.node(), getpass.getuser()), progress, max_file_bytes,
                            skip_top=options.skip_top, worktrees=worktrees, extra_roots=roots.worktrees)
        if problem:
            raise ValueError(problem)
    key = backup_key(context.environ, args.key_file or load_settings(context.config_dir).get("key_file") or None)
    jobs = args.jobs or int(load_settings(context.config_dir)["schedule"].get("parallel_transfers") or DEFAULT_JOBS)
    result = create_snapshot(
        roots, targets, options, key=key, versions=context.versions,
        host=platform.node(), user=getpass.getuser(), jobs=max(1, min(MAX_JOBS, jobs)), full=args.full,
    )
    manifest = result.manifest
    pruned = {}
    if args.prune:
        schedule = load_settings(context.config_dir)["schedule"]
        for target in resolve_targets(context, args.target):
            pruned[target.name] = prune_snapshots(
                target, manifest["workspace"], keep_last=int(schedule["keep_last"]), keep_daily=int(schedule["keep_daily"])
            )
    return {
        "id": manifest["id"],
        "workspace": manifest["workspace"],
        "targets": result.targets,
        "files": len(manifest["entries"]),
        "skipped": len(manifest["skipped"]),
        # Files the user would expect in the backup but that are not there (excluded folders are not listed).
        "not_backed_up": [f"{item['root']}/{item['path']}: {item['reason']}" for item in manifest["skipped"]
                          if item.get("reason") not in CHOSEN_SKIPS][:5],
        "not_backed_up_count": sum(1 for item in manifest["skipped"] if item.get("reason") not in CHOSEN_SKIPS),
        # What the menu choices left out: first-level folders and git worktrees.
        "left_out": {"folders": sorted(item["path"] for item in manifest["skipped"] if item.get("reason") == "not selected"),
                     "worktrees": sorted(item["path"] for item in manifest["skipped"] if item.get("reason") == "worktree"),
                     "worktree_mode": worktrees, "outside_worktrees": [str(path) for path in roots.worktrees]},
        "secrets": "encrypted" if manifest["secrets"].get("included") else f"left out ({len(manifest['secrets'].get('names') or [])} items, no backup key)",
        "sessions": manifest["sessions"],
        "stats": result.stats,
        **({"pruned": {name: len(value.get("removed") or []) for name, value in pruned.items()}} if pruned else {}),
    }


def _target(context: BackupContext, args: argparse.Namespace) -> BackupTarget:
    return resolve_targets(context, args.target[:1])[0]


def cmd_list(context: BackupContext, args: argparse.Namespace) -> list[dict[str, Any]]:
    target = _target(context, args)
    rows = []
    digest = "" if args.all else workspace_digest(Path(args.cwd or context.cwd()).resolve())
    for workspace, snapshot_id in list_snapshots(target):
        if digest and not workspace.endswith("-" + digest):
            continue
        try:
            from ciel_runtime_support.session_backup_store import decode_manifest

            manifest = decode_manifest(target.get(manifest_key(workspace, snapshot_id)))
        except Exception as error:  # noqa: BLE001 - list what can be read
            rows.append({"id": snapshot_id, "workspace": workspace, "error": type(error).__name__})
            continue
        rows.append({
            "id": snapshot_id,
            "workspace": workspace,
            "created": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(manifest.get("created") or 0)),
            "trigger": manifest.get("trigger"),
            "label": manifest.get("label"),
            "files": len(manifest.get("entries") or []),
            "bytes": sum(int(entry.get("size") or 0) for entry in manifest.get("entries") or []),
            "secrets": bool((manifest.get("secrets") or {}).get("included")),
            # Incremental: what this snapshot added to the target (chunks already there are reused).
            "new_chunks": int((manifest.get("stats") or {}).get("uploaded_chunks") or 0),
            "chunks": int((manifest.get("stats") or {}).get("chunks") or 0),
            "new_bytes": int((manifest.get("stats") or {}).get("bytes_uploaded") or 0),
        })
    return rows


def restore(context: BackupContext, args: argparse.Namespace) -> dict[str, Any]:
    if not args.rest:
        raise SystemExit("restore needs a snapshot id")
    target = _target(context, args)
    _, manifest = find_snapshot(target, args.rest[0])
    overrides = {
        "cwd": args.to_cwd, "claude": args.claude_dir, "home": args.home,
        "codex": args.codex_home, "ciel": args.ciel_dir, "ciel_ws": args.ciel_ws,
    }
    overrides = {name: value for name, value in overrides.items() if value}
    if "ciel_ws" not in overrides and ("cwd" in overrides or "ciel" in overrides):
        base = Path(overrides.get("ciel") or manifest["roots"]["ciel"])
        cwd = Path(overrides.get("cwd") or manifest["roots"]["cwd"])
        overrides["ciel_ws"] = str(base / "workspaces" / workspace_digest(cwd))
    dest = destination_roots(manifest, overrides)
    live = live_session_pid(dest["ciel"], dest["cwd"])
    if live and not args.force and not args.dry_run:
        raise SystemExit(
            f"A Ciel session (pid {live}) is still running for {dest['cwd']}. Stop it first, or pass --force."
        )
    key = backup_key(context.environ, args.key_file or load_settings(context.config_dir).get("key_file") or None)
    safety = None
    if not args.dry_run and not args.no_safety:
        safety_args = argparse.Namespace(**{**vars(args), "cwd": str(dest["cwd"]), "label": f"before restoring {manifest['id']}",
                                            "trigger": "pre-restore", "session": [], "exclude": []})
        if Path(dest["cwd"]).is_dir():
            safety = create(context, safety_args)["id"]
    result = restore_snapshot(target, manifest, dest, key=key, include_files=not args.no_files, dry_run=args.dry_run)
    result.pop("paths") if not args.dry_run else None
    result.update({"id": manifest["id"], "dry_run": args.dry_run, "safety_snapshot": safety,
                   "roots": {name: str(path) for name, path in dest.items()}, "next": resume_hint(manifest, dest)})
    return result


def prune(context: BackupContext, args: argparse.Namespace) -> dict[str, Any]:
    from ciel_runtime_support.session_backup_collect import workspace_label

    schedule = load_settings(context.config_dir)["schedule"]
    cwd = Path(args.cwd or context.cwd()).resolve()
    target = _target(context, args)
    workspace = workspace_label(cwd, platform.node(), getpass.getuser())
    keep_last = args.keep_last if args.keep_last is not None else int(schedule["keep_last"])
    keep_daily = args.keep_daily if args.keep_daily is not None else int(schedule["keep_daily"])
    return {"target": target.name, "workspace": workspace,
            **prune_snapshots(target, workspace, keep_last=keep_last, keep_daily=keep_daily, dry_run=args.dry_run)}


def _setting_value(key: str, text: str) -> Any:
    default = DEFAULT_SCHEDULE[key]
    if isinstance(default, bool):
        if text.lower() not in ("1", "0", "true", "false", "on", "off", "yes", "no"):
            raise ValueError(f"{key} takes on/off")
        return text.lower() in ("1", "true", "on", "yes")
    if isinstance(default, int):
        value = int(text)
        if value < 0:
            raise ValueError(f"{key} must be 0 or more")
        return value
    return text


def schedule_command(context: BackupContext, rest: list[str]) -> dict[str, Any]:
    settings = load_settings(context.config_dir)
    if rest[:1] == ["set"]:
        for item in rest[1:]:
            key, _, text = item.partition("=")
            if key not in DEFAULT_SCHEDULE:
                raise ValueError(f"unknown schedule key {key!r}; keys: {', '.join(DEFAULT_SCHEDULE)}")
            settings["schedule"][key] = _setting_value(key, text)
        save_settings(context.config_dir, settings)
    elif rest and rest[0] != "show":
        raise SystemExit(USAGE)
    return {"schedule": settings["schedule"], "default_targets": settings["default_targets"] or ["local"]}


def target_command(context: BackupContext, rest: list[str]) -> Any:
    settings = load_settings(context.config_dir)
    action = rest[0] if rest else "list"
    if action == "list":
        return {"targets": settings["targets"], "default_targets": settings["default_targets"] or ["local"],
                "builtin_local": str(Path(context.config_dir) / "backups")}
    if action == "add" and len(rest) >= 3:
        name, kind = rest[1], rest[2]
        options = dict(item.split("=", 1) for item in rest[3:] if "=" in item)
        spec = {"type": kind, **options}
        build_target(name, spec, context.environ)
        settings["targets"][name] = spec
        save_settings(context.config_dir, settings)
        return {"added": name, "spec": spec}
    if action == "remove" and len(rest) >= 2:
        settings["targets"].pop(rest[1], None)
        settings["default_targets"] = [name for name in settings["default_targets"] if name != rest[1]]
        save_settings(context.config_dir, settings)
        return {"removed": rest[1]}
    if action == "default" and len(rest) >= 2:
        unknown = [name for name in rest[1:] if name != "local" and name not in settings["targets"]]
        if unknown:
            raise SystemExit(f"unknown target(s): {', '.join(unknown)}")
        settings["default_targets"] = rest[1:]
        save_settings(context.config_dir, settings)
        return {"default_targets": settings["default_targets"]}
    raise SystemExit(USAGE)


def _print(context: BackupContext, value: Any, as_json: bool) -> None:
    if as_json:
        context.output(json.dumps(value, indent=2, ensure_ascii=False, default=str))
        return
    if isinstance(value, list):
        if not value:
            context.output("No snapshots.")
        for row in value:
            context.output(
                f"{row['id']}  {row.get('created', '')}  {row.get('trigger') or '-':<11} files={row.get('files')} "
                f"size={_size(int(row.get('bytes') or 0))} new={_size(int(row.get('new_bytes') or 0))} "
                f"({row.get('new_chunks', 0)}/{row.get('chunks', 0)} chunks) secrets={'yes' if row.get('secrets') else 'no'}"
                + (f"  {row['label']}" if row.get("label") else "")
                + (f"  [{row['workspace']}]" if row.get("workspace") else "")
            )
        return
    for key, item in value.items():
        if key == "stats" and isinstance(item, dict):
            item = (f"incremental: {item['uploaded_chunks']} of {item['chunks']} chunks new "
                    f"({_size(item['bytes_uploaded'])} uploaded compressed, {_size(item['bytes_total'])} in the snapshot)"
                    + (f"; {item['reused_files']} unchanged files not read again" if item.get("reused_files") else ""))
        elif isinstance(item, (dict, list)):
            item = json.dumps(item, ensure_ascii=False, default=str)
        context.output(f"{key}: {item}")


def run_backup_command(argv: list[str], context: BackupContext) -> int:
    args, unknown = _parser().parse_known_args(argv)
    if args.help or not args.command or unknown:
        context.output(USAGE if not unknown else f"Unknown option(s): {' '.join(unknown)}\n\n{USAGE}")
        return 0 if args.help or not args.command else 2
    command = args.command
    try:
        if command == "create":
            _print(context, create(context, args), args.json)
        elif command == "list":
            _print(context, cmd_list(context, args), args.json)
        elif command == "show" and args.rest:
            _, manifest = find_snapshot(_target(context, args), args.rest[0])
            summary = {key: manifest.get(key) for key in ("id", "workspace", "label", "trigger", "host", "user", "roots", "sessions", "versions", "stats")}
            summary["created"] = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(manifest.get("created") or 0))
            summary["files"] = len(manifest.get("entries") or [])
            summary["skipped"] = manifest.get("skipped") or []
            summary["secrets"] = {key: value for key, value in (manifest.get("secrets") or {}).items() if key != "chunks"}
            _print(context, summary, args.json)
        elif command == "verify" and args.rest:
            target = _target(context, args)
            _, manifest = find_snapshot(target, args.rest[0])
            problems = verify_snapshot(target, manifest)
            _print(context, {"id": manifest["id"], "ok": not problems, "problems": problems}, args.json)
            return 0 if not problems else 1
        elif command == "restore":
            _print(context, restore(context, args), args.json)
        elif command == "target":
            _print(context, target_command(context, args.rest), True)
        elif command == "prune":
            _print(context, prune(context, args), args.json)
        elif command == "schedule":
            _print(context, schedule_command(context, args.rest), True)
        elif command == "status":
            cwd = Path(args.cwd or context.cwd()).resolve()
            settings = load_settings(context.config_dir)
            _print(context, {"cwd": str(cwd), "schedule": settings["schedule"],
                             "default_targets": settings["default_targets"] or ["local"],
                             "last": workspace_state(context.config_dir, cwd)}, True)
        else:
            context.output(USAGE)
            return 2
    except TargetWriteError as error:
        context.output(json.dumps({"error": str(error)}) if args.json else f"backup {command} failed: {error}")
        return 1
    except (LookupError, ValueError, OSError) as error:
        message = f"{type(error).__name__}: {error}" if not isinstance(error, ValueError) else str(error)
        context.output(json.dumps({"error": message}) if args.json else f"backup {command} failed: {message}")
        return 1
    finally:
        _close_opened()
    return 0


__all__ = ["BackupContext", "USAGE", "resolve_targets", "run_backup_command"]
