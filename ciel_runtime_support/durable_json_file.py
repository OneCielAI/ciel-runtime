"""Atomic durable JSON replacement shared by queue and mailbox persistence."""
import json
import os
import time
from pathlib import Path
from typing import Any


def save(path: Path, row: dict[str, Any]) -> None:
    raw = json.dumps(row, ensure_ascii=False).encode('utf8')
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f'.{path.stem}.{os.getpid()}.{time.time_ns()}.tmp')
    try:
        with temporary.open('xb') as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        if os.name != 'nt':
            descriptor = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
    finally:
        temporary.unlink(missing_ok=True)
