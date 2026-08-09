"""Bootstrap MOSS-TTS-Nano with vLLM-Omni on Colab and Tailscale Serve.

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


HOSTNAME = os.environ.get("CIEL_TTS_HOSTNAME", "ciel-tts")
PORT = 8091
SOCKET = "/tmp/ciel-tts-tailscaled.sock"
STATE = "/tmp/ciel-tts-tailscaled.state"
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
    print("+", " ".join(args))
    return subprocess.run(args, check=check, text=True, capture_output=False)


def install_tailscale() -> None:
    if shutil.which("tailscale"):
        return
    run("bash", "-lc", "curl -fsSL https://tailscale.com/install.sh | sh")


def start_tailscale(auth_key: str) -> tuple[str, str]:
    install_tailscale()
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    tail_log = (LOG_DIR / "tailscale-tts.log").open("ab")
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
    run("tailscale", f"--socket={SOCKET}", "up", f"--auth-key={auth_key}", f"--hostname={HOSTNAME}", "--accept-dns=true", "--reset")
    status = subprocess.check_output(["tailscale", f"--socket={SOCKET}", "status", "--json"], text=True)
    dns_name = str(json.loads(status).get("Self", {}).get("DNSName") or HOSTNAME).rstrip(".")
    serve = run("tailscale", f"--socket={SOCKET}", "serve", "--bg", "--https=443", f"http://127.0.0.1:{PORT}", check=False)
    if serve.returncode == 0:
        return dns_name, f"https://{dns_name}"
    run("tailscale", f"--socket={SOCKET}", "serve", "--bg", "--http=80", f"http://127.0.0.1:{PORT}")
    return dns_name, f"http://{dns_name}"


def wait_for_server(api_key: str) -> None:
    headers = {"authorization": f"Bearer {api_key}"} if api_key else {}
    for _ in range(240):
        try:
            with urllib.request.urlopen(urllib.request.Request(f"http://127.0.0.1:{PORT}/health", headers=headers), timeout=3) as response:
                if response.status < 500:
                    return
        except Exception:
            time.sleep(2)
    raise RuntimeError("MOSS TTS did not become healthy; inspect /content/ciel-speech-logs/moss-tts.log")


def main() -> None:
    auth_key = secret("TAILSCALE_AUTHKEY", required=True)
    api_key = secret("CIEL_SPEECH_API_KEY")
    run(sys.executable, "-m", "pip", "install", "-U", "vllm-omni==0.24.0")
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    command = [
        "vllm", "serve", "OpenMOSS-Team/MOSS-TTS-Nano", "--omni", "--host", "127.0.0.1", "--port", str(PORT),
        "--gpu-memory-utilization", "0.72", "--trust-remote-code", "--enforce-eager",
    ]
    if api_key:
        command.extend(["--api-key", api_key])
    server_log = (LOG_DIR / "moss-tts.log").open("ab")
    subprocess.Popen(command, stdout=server_log, stderr=subprocess.STDOUT, start_new_session=True)
    wait_for_server(api_key)
    dns_name, base_url = start_tailscale(auth_key)
    print(json.dumps({"ok": True, "role": "tts", "hostname": dns_name, "base_url": base_url, "model": "OpenMOSS-Team/MOSS-TTS-Nano", "api_key_set": bool(api_key)}, indent=2))


if __name__ == "__main__":
    main()
