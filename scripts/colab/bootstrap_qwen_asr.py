"""Bootstrap Qwen3-ASR on a Colab T4 and publish it only to the tailnet.

Required Colab Secret: TAILSCALE_AUTHKEY
Optional Colab Secret: CIEL_SPEECH_API_KEY
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
import urllib.request


HOSTNAME = os.environ.get("CIEL_ASR_HOSTNAME", "ciel-asr")
PORT = 8000
SOCKET = "/tmp/ciel-asr-tailscaled.sock"
STATE = "/tmp/ciel-asr-tailscaled.state"
LOG_DIR = Path("/content/ciel-speech-logs")


def secret(name: str, *, required: bool = False) -> str:
    value = str(os.environ.get(name) or "").strip()
    if not value:
        try:
            from google.colab import userdata  # type: ignore

            value = str(userdata.get(name) or "").strip()
        except Exception:
            value = ""
    if required and not value:
        raise RuntimeError(f"Add {name} to Colab Secrets and allow notebook access, then rerun this script.")
    return value


def run(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    visible: list[str] = []
    redact_next = False
    for arg in args:
        if redact_next:
            visible.append("<redacted>")
            redact_next = False
        elif arg.startswith("--auth-key="):
            visible.append("--auth-key=<redacted>")
        else:
            visible.append(arg)
            redact_next = arg == "--api-key"
    print("+", " ".join(visible))
    return subprocess.run(args, check=check, text=True, capture_output=False)


def install_tailscale() -> None:
    if shutil.which("tailscale"):
        return
    run("bash", "-lc", "curl -fsSL https://tailscale.com/install.sh | sh")


def start_tailscale(auth_key: str) -> tuple[str, str]:
    install_tailscale()
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    tail_log = (LOG_DIR / "tailscale-asr.log").open("ab")
    subprocess.Popen(
        ["tailscaled", f"--socket={SOCKET}", f"--state={STATE}", "--tun=userspace-networking"],
        stdout=tail_log,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )
    for _ in range(60):
        if Path(SOCKET).exists():
            break
        time.sleep(1)
    login = run("tailscale", f"--socket={SOCKET}", "up", f"--auth-key={auth_key}", f"--hostname={HOSTNAME}", "--accept-dns=true", "--reset", check=False)
    if login.returncode:
        raise RuntimeError("Tailscale authentication failed; use a valid reusable key or a fresh key for this worker")
    status = subprocess.check_output(["tailscale", f"--socket={SOCKET}", "status", "--json"], text=True)
    dns_name = str(json.loads(status).get("Self", {}).get("DNSName") or HOSTNAME).rstrip(".")
    run("tailscale", f"--socket={SOCKET}", "serve", "--bg", "--http=80", f"http://127.0.0.1:{PORT}")
    return dns_name, f"http://{dns_name}"


def wait_for_server(api_key: str) -> None:
    headers = {"authorization": f"Bearer {api_key}"} if api_key else {}
    for _ in range(180):
        try:
            with urllib.request.urlopen(urllib.request.Request(f"http://127.0.0.1:{PORT}/health", headers=headers), timeout=3) as response:
                if response.status < 500:
                    return
        except Exception:
            time.sleep(2)
    raise RuntimeError("Qwen ASR did not become healthy; inspect /content/ciel-speech-logs/qwen-asr.log")


def main() -> None:
    auth_key = secret("TAILSCALE_AUTHKEY", required=True)
    api_key = secret("CIEL_SPEECH_API_KEY")
    run(sys.executable, "-m", "pip", "install", "-U", "qwen-asr[vllm]", "vllm[audio]")
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    command = [
        "qwen-asr-serve", "Qwen/Qwen3-ASR-0.6B", "--host", "127.0.0.1", "--port", str(PORT),
        "--gpu-memory-utilization", "0.78", "--max-model-len", "8192",
    ]
    if api_key:
        command.extend(["--api-key", api_key])
    server_log = (LOG_DIR / "qwen-asr.log").open("ab")
    subprocess.Popen(command, stdout=server_log, stderr=subprocess.STDOUT, start_new_session=True)
    wait_for_server(api_key)
    dns_name, base_url = start_tailscale(auth_key)
    print(json.dumps({"ok": True, "role": "asr", "hostname": dns_name, "base_url": base_url, "model": "Qwen/Qwen3-ASR-0.6B", "api_key_set": bool(api_key)}, indent=2))


if __name__ == "__main__":
    main()
