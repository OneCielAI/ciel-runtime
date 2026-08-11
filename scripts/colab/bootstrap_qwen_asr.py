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
    status = subprocess.check_output(["tailscale", f"--socket={SOCKET}", "status", "--json"], text=True)
    status_data = json.loads(status)
    if auth_key:
        login = run("tailscale", f"--socket={SOCKET}", "up", f"--auth-key={auth_key}", f"--hostname={HOSTNAME}", "--accept-dns=true", "--reset", check=False)
        if login.returncode:
            raise RuntimeError("Tailscale authentication failed; use a valid reusable key or a fresh key for this worker")
        status_data = json.loads(subprocess.check_output(["tailscale", f"--socket={SOCKET}", "status", "--json"], text=True))
    elif status_data.get("BackendState") != "Running":
        raise RuntimeError("TAILSCALE_AUTHKEY is required because this Colab worker has no reusable Tailscale login state")
    dns_name = str(status_data.get("Self", {}).get("DNSName") or HOSTNAME).rstrip(".")
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
    for _ in range(600):
        if process.poll() is not None:
            log_path = LOG_DIR / "qwen-asr.log"
            log_tail = log_path.read_text(encoding="utf-8", errors="replace")[-12000:] if log_path.exists() else "log unavailable"
            raise RuntimeError(f"Qwen ASR exited with status {process.returncode}:\n{log_tail}")
        if server_is_healthy(api_key):
            return
        time.sleep(2)
    raise RuntimeError("Qwen ASR did not become healthy; inspect /content/ciel-speech-logs/qwen-asr.log")


def stop_existing_server() -> None:
    """Release a previous vLLM worker, including orphaned GPU children.

    A Colab session is dedicated to ASR. Killing every compute process is safe here
    and is necessary because an EngineCore child can survive after its API process
    exits, leaving almost all VRAM allocated while the health endpoint is down.
    """
    cleanup = f"""
fuser -k {PORT}/tcp >/dev/null 2>&1 || true
pkill -TERM -f '[q]wen-asr-serve|[v]llm|VLLM::' >/dev/null 2>&1 || true
sleep 3
for pid in $(nvidia-smi --query-compute-apps=pid --format=csv,noheader,nounits 2>/dev/null | tr -d ' '); do
  case "$pid" in (*[!0-9]*|'') continue;; esac
  kill -TERM "$pid" >/dev/null 2>&1 || true
done
sleep 3
for pid in $(nvidia-smi --query-compute-apps=pid --format=csv,noheader,nounits 2>/dev/null | tr -d ' '); do
  case "$pid" in (*[!0-9]*|'') continue;; esac
  kill -KILL "$pid" >/dev/null 2>&1 || true
done
"""
    run("bash", "-lc", cleanup, check=False)
    time.sleep(2)


def main() -> None:
    if MODEL not in SUPPORTED_MODELS:
        raise RuntimeError(f"Unsupported CIEL_ASR_MODEL: {MODEL}")
    auth_key = secret("TAILSCALE_AUTHKEY")
    api_key = secret("CIEL_SPEECH_API_KEY")
    run(sys.executable, "-m", "pip", "install", "-U", "qwen-asr[vllm]", "vllm[audio]")
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    if not server_is_healthy(api_key):
        stop_existing_server()
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
