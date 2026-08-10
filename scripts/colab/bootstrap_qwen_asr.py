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
SUPPORTED_MODELS = {"Qwen/Qwen3-ASR-0.6B", "Qwen/Qwen3-ASR-1.7B"}
MODEL = os.environ.get("CIEL_ASR_MODEL", "Qwen/Qwen3-ASR-0.6B").strip()
PORT = 8000
SOCKET = "/tmp/ciel-asr-tailscaled.sock"
STATE = "/tmp/ciel-asr-tailscaled.state"
LOG_DIR = Path("/content/ciel-speech-logs")
MODEL_MARKER = Path("/content/ciel-speech-asr-model")


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
    if not Path(SOCKET).exists():
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


def server_is_healthy(api_key: str) -> bool:
    headers = {"authorization": f"Bearer {api_key}"} if api_key else {}
    try:
        with urllib.request.urlopen(urllib.request.Request(f"http://127.0.0.1:{PORT}/health", headers=headers), timeout=3) as response:
            return response.status < 500
    except Exception:
        return False


def wait_for_server(api_key: str, process: subprocess.Popen[bytes]) -> None:
    for _ in range(180):
        if process.poll() is not None:
            log_path = LOG_DIR / "qwen-asr.log"
            log_tail = log_path.read_text(encoding="utf-8", errors="replace")[-12000:] if log_path.exists() else "log unavailable"
            raise RuntimeError(f"Qwen ASR exited with status {process.returncode}:\n{log_tail}")
        if server_is_healthy(api_key):
            return
        time.sleep(2)
    raise RuntimeError("Qwen ASR did not become healthy; inspect /content/ciel-speech-logs/qwen-asr.log")


def prepare_model() -> None:
    current = MODEL_MARKER.read_text(encoding="utf-8", errors="replace").strip() if MODEL_MARKER.exists() else ""
    if current != MODEL:
        run("bash", "-lc", f"fuser -k {PORT}/tcp >/dev/null 2>&1 || true", check=False)
        time.sleep(2)


def main() -> None:
    if MODEL not in SUPPORTED_MODELS:
        raise RuntimeError(f"Unsupported CIEL_ASR_MODEL: {MODEL}")
    auth_key = secret("TAILSCALE_AUTHKEY", required=True)
    api_key = secret("CIEL_SPEECH_API_KEY")
    run(sys.executable, "-m", "pip", "install", "-U", "qwen-asr[vllm]", "vllm[audio]")
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    prepare_model()
    if not server_is_healthy(api_key):
        command = [
            "qwen-asr-serve", MODEL, "--host", "127.0.0.1", "--port", str(PORT),
            "--gpu-memory-utilization", "0.78", "--max-model-len", "8192",
        ]
        if api_key:
            command.extend(["--api-key", api_key])
        server_log = (LOG_DIR / "qwen-asr.log").open("ab")
        process = subprocess.Popen(command, stdout=server_log, stderr=subprocess.STDOUT, start_new_session=True)
        wait_for_server(api_key, process)
    MODEL_MARKER.write_text(MODEL, encoding="utf-8")
    dns_name, base_url = start_tailscale(auth_key)
    print(json.dumps({"ok": True, "role": "asr", "hostname": dns_name, "base_url": base_url, "model": MODEL, "api_key_set": bool(api_key)}, indent=2))


if __name__ == "__main__":
    main()
