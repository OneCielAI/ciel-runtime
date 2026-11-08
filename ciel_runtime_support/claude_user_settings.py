"""Allow-listed edits to Claude Code's two agent-view preferences.

Claude Code keeps ``leftArrowOpensAgents`` in its global config
(``~/.claude.json``) and ``disableAgentView`` in user settings
(``~/.claude/settings.json``); both take effect in Claude sessions started
after the change.  Both files belong to Claude Code, so unknown
keys are kept verbatim, nothing is written when a file cannot be parsed, and the
global config is replaced only while holding Claude Code's own proper-lockfile
lock: the ``<config>.lock`` directory its saves create with ``mkdir``.  That
lock is never taken over; a busy lock leaves the file unchanged.
"""
from __future__ import annotations

import json
import os
import secrets
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Mapping

from .work_plan_tree import WorkPlanError

LEFT_ARROW = 'claude_left_arrow_opens_agents'
BACKGROUND = 'claude_background_agents_enabled'
KEYS = (LEFT_ARROW, BACKGROUND)
LOCK_WAIT_SECONDS = 5.0


def _config_home(env: Mapping[str, str], home: Path) -> Path:
    return Path(env.get('CLAUDE_CONFIG_DIR') or home / '.claude')


def global_config_path(env: Mapping[str, str] | None = None, home: Path | None = None) -> Path:
    """The file Claude Code reads as its global config, by its own rule."""
    env = os.environ if env is None else env
    home = Path.home() if home is None else Path(home)
    suffix = '-custom-oauth' if env.get('CLAUDE_CODE_CUSTOM_OAUTH_URL') else ''
    main = Path(env.get('CLAUDE_CONFIG_DIR') or home) / f'.claude{suffix}.json'
    legacy = _config_home(env, home) / '.config.json'
    try:
        if legacy.is_file() and (legacy.stat().st_size > 0 or not main.exists()):
            return legacy
    except OSError:
        pass
    return main


def user_settings_path(env: Mapping[str, str] | None = None, home: Path | None = None) -> Path:
    env = os.environ if env is None else env
    return _config_home(env, Path.home() if home is None else Path(home)) / 'settings.json'


def _read(target: Path) -> dict[str, Any]:
    try:
        value = json.loads(target.read_text(encoding='utf8'))
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as error:
        raise WorkPlanError(f'{target.name} is unreadable; nothing changed') from error
    if not isinstance(value, dict):
        raise WorkPlanError(f'{target.name} is not a JSON object; nothing changed')
    return value


def _write(target: Path, value: dict[str, Any]) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f'{target.name}.tmp.{os.getpid()}.{secrets.token_hex(6)}')
    try:
        mode = target.stat().st_mode & 0o777
    except FileNotFoundError:
        mode = 0o600
    try:
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, 'O_BINARY', 0), mode)
        with os.fdopen(descriptor, 'wb') as stream:
            stream.write(json.dumps(value, indent=2, ensure_ascii=False).encode('utf8'))
            stream.flush()
            os.fsync(stream.fileno())
        for attempt in range(20):
            try:
                os.replace(temporary, target)
                break
            except PermissionError as error:
                # A Windows reader of the same file can briefly deny the replace.
                if os.name != 'nt' or getattr(error, 'winerror', None) not in (5, 32) or attempt == 19:
                    raise
                time.sleep(0.01)
    finally:
        temporary.unlink(missing_ok=True)


@contextmanager
def _claude_lock(target: Path):
    lock = target.with_name(target.name + '.lock')
    deadline = time.monotonic() + LOCK_WAIT_SECONDS
    while True:
        try:
            os.mkdir(lock)
            break
        except FileExistsError:
            if time.monotonic() >= deadline:
                raise WorkPlanError('Claude Code is saving its config; nothing changed, try again') from None
            time.sleep(0.05)
    try:
        yield
    finally:
        try:
            os.rmdir(lock)
        except FileNotFoundError:
            pass


def values(env: Mapping[str, str] | None = None, home: Path | None = None) -> dict[str, bool | None]:
    """Current public values; None marks a file that cannot be read."""
    result: dict[str, bool | None] = {}
    try:
        result[LEFT_ARROW] = _read(global_config_path(env, home)).get('leftArrowOpensAgents', True) is not False
    except WorkPlanError:
        result[LEFT_ARROW] = None
    try:
        result[BACKGROUND] = _read(user_settings_path(env, home)).get('disableAgentView') is not True
    except WorkPlanError:
        result[BACKGROUND] = None
    return result


def set_value(key: str, value: str, *, env: Mapping[str, str] | None = None,
              home: Path | None = None) -> dict[str, bool | None]:
    if key not in KEYS:
        raise WorkPlanError('unknown Claude agent-view setting')
    token = str(value).strip().lower()
    if token not in ('on', 'off', 'true', 'false'):
        raise WorkPlanError('use on/off or true/false')
    enabled = token in ('on', 'true')
    if key == LEFT_ARROW:
        target = global_config_path(env, home)
        with _claude_lock(target):
            current = _read(target)
            if current.get('leftArrowOpensAgents') is not enabled:
                current['leftArrowOpensAgents'] = enabled
                _write(target, current)
    else:
        target = user_settings_path(env, home)
        current = _read(target)
        if (current.get('disableAgentView') is True) == enabled:
            if enabled:
                current.pop('disableAgentView', None)
            else:
                current['disableAgentView'] = True
            _write(target, current)
    return values(env, home)
