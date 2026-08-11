"""Encrypt a downloaded Colab tailscaled state file in Ciel's portable vault."""

from __future__ import annotations

import argparse
import base64
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ciel_runtime_support.colab_speech_jobs import PortableEncryptedSecretStore
from ciel_runtime_support.runtime_paths import CONFIG_DIR


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--profile", required=True)
    parser.add_argument("--role", choices=("asr", "tts"), required=True)
    parser.add_argument("--input", type=Path, required=True)
    args = parser.parse_args()
    raw = args.input.read_bytes()
    if not raw:
        raise RuntimeError("Downloaded tailscaled state is empty")
    store = PortableEncryptedSecretStore(CONFIG_DIR / "colab-worker-credentials.vault.json")
    store.save(args.profile, {f"tailscale_{args.role}_state": base64.b64encode(raw).decode("ascii")})
    print(f"Saved encrypted {args.role.upper()} Tailscale device state for profile '{args.profile}'.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
