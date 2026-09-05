#!/usr/bin/env python3
"""Probe installed Claude/Codex TUI redraws through native Windows ConPTY."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
import shutil
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ciel_runtime_support.windows_conpty import WindowsConPtySession


def probe(name: str) -> dict[str, object]:
    executable = shutil.which(name)
    if not executable:
        return {"runtime": name, "available": False}
    logs: list[tuple[str, str]] = []
    session = WindowsConPtySession(
        [executable],
        dict(os.environ),
        log=lambda level, message: logs.append((level, message)),
        mirror_output=False,
        forward_stdin=False,
    )
    try:
        deadline = time.monotonic() + 15
        last_length = -1
        stable_since = time.monotonic()
        while time.monotonic() < deadline and session.poll() is None:
            length = len(session.output_tail())
            if length != last_length:
                last_length = length
                stable_since = time.monotonic()
            if length >= 100 and time.monotonic() - stable_since >= 0.5:
                break
            time.sleep(0.05)
        before = session.prompt_readiness_checkpoint()
        old_cols, old_rows = session._last_size
        new_size = (old_cols + 3, old_rows + 2)
        session._terminal_size = lambda: new_size
        resized = session.resize_if_needed()
        deadline = time.monotonic() + 2
        while session.prompt_readiness_checkpoint() <= before and time.monotonic() < deadline:
            time.sleep(0.02)
        redraw, _cursor = session._output_since(before)
        return {
            "runtime": name,
            "available": True,
            "resized": resized,
            "from": [old_cols, old_rows],
            "to": list(new_size),
            "redraw_bytes": len(redraw),
            "redraw_sha256": hashlib.sha256(redraw).hexdigest(),
            "resize_log": next(
                (message for _level, message in reversed(logs) if "conpty_resize" in message),
                "",
            ),
        }
    finally:
        session.close()


def main() -> int:
    import json

    results = [probe(name) for name in ("claude", "codex")]
    print(json.dumps(results, ensure_ascii=False, separators=(",", ":")))
    return 0 if all(item.get("resized") for item in results if item.get("available")) else 1


if __name__ == "__main__":
    raise SystemExit(main())
