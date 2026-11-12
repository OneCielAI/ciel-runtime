"""Bounded, read-only workspace evidence for the side actor."""
from __future__ import annotations

import fnmatch
import os
from pathlib import Path
import re

from .side_redaction import redact_text
from .work_plan_tree import WorkPlanError

MAX_FILE_BYTES = 2 * 1024 * 1024
MAX_OUTPUT_CHARS = 48 * 1024
MAX_LINES = 400
MAX_MATCHES = 80
MAX_SEARCH_FILES = 5000
MAX_SEARCH_BYTES = 64 * 1024 * 1024
_SECRET_NAMES = re.compile(r'(?i)(^|[\\/])(?:\.ssh|\.aws|\.gnupg|credentials?)(?:[\\/]|$)|(?:^|[\\/])(?:\.env(?:\..*)?|\.npmrc|\.pypirc|[^\\/]+\.(?:key|pem|p12|pfx))$')
_SKIP_DIRS = {'.git', '.hg', '.svn', 'node_modules', '__pycache__', '.venv', 'venv'}


def workspace_root():
    raw = os.environ.get('CIEL_RUNTIME_LAUNCH_CWD')
    if not raw:
        raise WorkPlanError('side code workspace is unavailable')
    root = Path(raw).resolve()
    if not root.is_dir() or root.is_symlink():
        raise WorkPlanError('side code workspace is unavailable')
    return root


def _path(root, value):
    if not isinstance(value, str) or not value or '\x00' in value or value.startswith(('\\\\', '//')):
        raise WorkPlanError('invalid side code path')
    raw = Path(value)
    if raw.is_absolute() or any(part == '..' for part in raw.parts):
        raise WorkPlanError('side code path must be workspace-relative')
    target = (root / raw).resolve()
    try:
        target.relative_to(root)
    except ValueError:
        raise WorkPlanError('side code path escapes the workspace') from None
    if _SECRET_NAMES.search(str(target.relative_to(root))):
        raise WorkPlanError('side code secret paths are not readable')
    return target


def read_code(path, *, offset=1, limit=200):
    root = workspace_root()
    target = _path(root, path)
    if target.is_symlink() or not target.is_file() or target.stat().st_size > MAX_FILE_BYTES:
        raise WorkPlanError('side code path is not a bounded regular file')
    if (isinstance(offset, bool) or not isinstance(offset, int) or offset < 1
            or isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= MAX_LINES):
        raise WorkPlanError('side code offset/limit is invalid')
    try:
        text = target.read_text(encoding='utf8')
    except (OSError, UnicodeError) as error:
        raise WorkPlanError('side code file is not readable UTF-8 text') from error
    lines = text.splitlines()
    selected = lines[offset - 1:offset - 1 + limit]
    body = '\n'.join(f'{offset + index}\t{line}' for index, line in enumerate(selected))
    return {'path': target.relative_to(root).as_posix(), 'offset': offset, 'lines': len(selected),
            'text': redact_text(body)[:MAX_OUTPUT_CHARS], 'truncated': len(body) > MAX_OUTPUT_CHARS}


def search_code(pattern, *, glob='*'):
    root = workspace_root()
    if not isinstance(pattern, str) or not pattern or len(pattern) > 300 or '\x00' in pattern:
        raise WorkPlanError('side code search pattern is invalid')
    if not isinstance(glob, str) or not glob or len(glob) > 200 or Path(glob).is_absolute() or '..' in Path(glob).parts:
        raise WorkPlanError('side code search glob is invalid')
    try:
        expression = re.compile(pattern)
    except re.error as error:
        raise WorkPlanError('side code search regex is invalid') from error
    matches = []
    scanned_files = 0
    scanned_bytes = 0
    for directory, names, files in os.walk(root, followlinks=False):
        names[:] = [name for name in names if name not in _SKIP_DIRS and not (Path(directory) / name).is_symlink()]
        for name in files:
            path = Path(directory) / name
            relative = path.relative_to(root).as_posix()
            if not fnmatch.fnmatch(relative, glob) and not fnmatch.fnmatch(name, glob):
                continue
            try:
                checked = _path(root, relative)
                size = checked.stat().st_size
                if checked.is_symlink() or size > MAX_FILE_BYTES:
                    continue
                scanned_files += 1
                scanned_bytes += size
                if scanned_files > MAX_SEARCH_FILES or scanned_bytes > MAX_SEARCH_BYTES:
                    return {'matches': matches, 'truncated': True}
                text = checked.read_text(encoding='utf8')
            except (OSError, UnicodeError, WorkPlanError):
                continue
            for number, line in enumerate(text.splitlines(), 1):
                if expression.search(line):
                    matches.append({'path': relative, 'line': number, 'text': redact_text(line)[:500]})
                    if len(matches) == MAX_MATCHES:
                        return {'matches': matches, 'truncated': True}
    return {'matches': matches, 'truncated': False}
