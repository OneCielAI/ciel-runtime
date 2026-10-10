"""``ciel-runtime backup`` -- save, list, check and restore agent session snapshots."""

from __future__ import annotations

import argparse
import getpass
import json
import platform
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping

from ciel_runtime_support.session_backup_collect import DEFAULT_CWD_EXCLUDES, CollectOptions, default_roots
from ciel_runtime_support.session_backup_ops import (
    ROOT_NAMES,
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
from ciel_runtime_support.session_backup_store import BackupTarget, LocalTarget, find_snapshot, list_snapshots, manifest_key
from ciel_runtime_support.session_backup_targets import TARGET_TYPES, build_target
from ciel_runtime_support.workspace_router_selection import workspace_digest

USAGE = f"""usage: ciel-runtime backup <command> [options]

  create   [--cwd DIR] [--target NAME|DIR ...] [--label TEXT] [--session ID] [--no-files]
           [--exclude PATTERN ...] [--key-file FILE] [--trigger NAME]
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


def resolve_targets(context: BackupContext, names: list[str]) -> list[BackupTarget]:
    settings = load_settings(context.config_dir)
    wanted = names or list(settings["default_targets"]) or ["local"]
    targets: list[BackupTarget] = []
    for name in wanted:
        if name == "local" and "local" not in settings["targets"]:
            targets.append(LocalTarget(Path(context.config_dir) / "backups", "local"))
        elif name in settings["targets"]:
            targets.append(build_target(name, settings["targets"][name], context.environ))
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
    parser.add_argument("-h", "--help", action="store_true")
    return parser


def _size(value: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            return f"{value:.0f} {unit}" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1024
    return str(value)


def create(context: BackupContext, args: argparse.Namespace) -> dict[str, Any]:
    cwd = Path(args.cwd or context.cwd()).resolve()
    roots = default_roots(cwd, environ=context.environ, home=context.home, asset_home=context.asset_home, config_dir=context.config_dir)
    options = CollectOptions(
        include_files=not args.no_files,
        session_ids=tuple(args.session),
        excludes=DEFAULT_CWD_EXCLUDES,
        extra_excludes=tuple(args.exclude),
        label=args.label,
        trigger=args.trigger,
        skip_paths=tuple(path for path in (args.key_file, load_settings(context.config_dir).get("key_file")) if path),
    )
    key = backup_key(context.environ, args.key_file or load_settings(context.config_dir).get("key_file") or None)
    result = create_snapshot(
        roots, resolve_targets(context, args.target), options, key=key, versions=context.versions,
        host=platform.node(), user=getpass.getuser(),
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
                   "roots": {name: str(dest[name]) for name in ROOT_NAMES}, "next": resume_hint(manifest, dest)})
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
                    f"({_size(item['bytes_uploaded'])} uploaded compressed, {_size(item['bytes_total'])} in the snapshot)")
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
    except (LookupError, ValueError, OSError) as error:
        context.output(f"backup {command} failed: {type(error).__name__}: {error}")
        return 1
    return 0


__all__ = ["BackupContext", "USAGE", "resolve_targets", "run_backup_command"]
