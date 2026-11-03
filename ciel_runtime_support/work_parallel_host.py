"""Local launch bridge secret; never included in model context or status."""
import os
import secrets
from pathlib import Path
from .channel_message_repository import exclusive_file_lock


def host_token():
    from .runtime_paths import CONFIG_DIR
    path = Path(CONFIG_DIR) / "work-parallel-host.token"
    with exclusive_file_lock(path):
        if path.exists():
            token = path.read_text(encoding="ascii").strip()
            if len(token) != 64 or any(c not in "0123456789abcdef" for c in token):
                raise ValueError("invalid parallel host credential")
            return token
        token = secrets.token_hex(32)
        with path.open("x", encoding="ascii") as f:
            f.write(token)
        if os.name != "nt":
            path.chmod(0o600)
        return token
