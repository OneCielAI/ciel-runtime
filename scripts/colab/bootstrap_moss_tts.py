"""Bootstrap MOSS-TTS-Nano with vLLM-Omni on Colab and Tailscale Serve.

Required Colab Secret: TAILSCALE_AUTHKEY
Optional Colab Secret: CIEL_SPEECH_API_KEY
"""

from __future__ import annotations

import base64
import json
import os
from pathlib import Path
import site
import shutil
import subprocess
import sys
import time
import urllib.request


HOSTNAME = os.environ.get("CIEL_TTS_HOSTNAME", "ciel-tts")
PORT = 8091
SOCKET = Path("/tmp/ciel-tts-tailscaled.sock")
STATE = Path("/tmp/ciel-tts-tailscaled.state")
LOG_DIR = Path("/content/ciel-speech-logs")
BACKEND_MARKER = Path("/content/ciel-speech-tts-backend")


def restore_tailscale_state() -> None:
    encoded = str(os.environ.get("CIEL_TTS_TAILSCALE_STATE") or "").strip()
    if not encoded:
        return
    try:
        STATE.write_bytes(base64.b64decode(encoded, validate=True))
        STATE.chmod(0o600)
    except (ValueError, OSError) as exc:
        raise RuntimeError("Saved TTS Tailscale device state could not be restored") from exc


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
    tail_log = (LOG_DIR / "tailscale-tts.log").open("ab")
    if not SOCKET.exists():
        restore_tailscale_state()
        subprocess.Popen(
            ["tailscaled", f"--socket={SOCKET}", f"--state={STATE}", "--tun=userspace-networking"],
            stdout=tail_log,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    for _ in range(60):
        if SOCKET.exists():
            break
        time.sleep(1)
    status = subprocess.check_output(["tailscale", f"--socket={SOCKET}", "status", "--json"], text=True)
    status_data = json.loads(status)
    if status_data.get("BackendState") == "Running":
        pass
    elif auth_key:
        login = run("tailscale", f"--socket={SOCKET}", "up", f"--auth-key={auth_key}", f"--hostname={HOSTNAME}", "--accept-dns=true", "--reset", check=False)
        if login.returncode:
            raise RuntimeError("Tailscale authentication failed; replace the saved recovery key")
        status_data = json.loads(subprocess.check_output(["tailscale", f"--socket={SOCKET}", "status", "--json"], text=True))
    else:
        raise RuntimeError("TAILSCALE_AUTHKEY is required because this Colab worker has no reusable Tailscale login state")
    dns_name = str(status_data.get("Self", {}).get("DNSName") or HOSTNAME).rstrip(".")
    run("tailscale", f"--socket={SOCKET}", "serve", "--bg", "--http=80", f"http://127.0.0.1:{PORT}")
    return dns_name, f"http://{dns_name}"


def wait_for_server(api_key: str, process: subprocess.Popen[bytes]) -> None:
    headers = {"authorization": f"Bearer {api_key}"} if api_key else {}
    for _ in range(240):
        if process.poll() is not None:
            log_path = LOG_DIR / "moss-tts.log"
            log_tail = log_path.read_text(encoding="utf-8", errors="replace")[-12000:] if log_path.exists() else "log unavailable"
            raise RuntimeError(f"MOSS TTS exited with status {process.returncode}:\n{log_tail}")
        try:
            with urllib.request.urlopen(urllib.request.Request(f"http://127.0.0.1:{PORT}/health", headers=headers), timeout=3) as response:
                if response.status < 500:
                    return
        except Exception:
            time.sleep(2)
    raise RuntimeError("MOSS TTS did not become healthy; inspect /content/ciel-speech-logs/moss-tts.log")


def server_is_healthy(api_key: str) -> bool:
    headers = {"authorization": f"Bearer {api_key}"} if api_key else {}
    try:
        with urllib.request.urlopen(urllib.request.Request(f"http://127.0.0.1:{PORT}/health", headers=headers), timeout=3) as response:
            return response.status < 500
    except Exception:
        return False


def prepare_backend() -> None:
    current = BACKEND_MARKER.read_text(encoding="utf-8", errors="replace").strip() if BACKEND_MARKER.exists() else ""
    if current and current != "moss":
        run("bash", "-lc", f"fuser -k {PORT}/tcp >/dev/null 2>&1 || true", check=False)
        time.sleep(2)


def main() -> None:
    auth_key = secret("TAILSCALE_AUTHKEY")
    api_key = secret("CIEL_SPEECH_API_KEY")
    run(
        sys.executable,
        "-m",
        "pip",
        "install",
        "-U",
        "nvidia-cuda-runtime==13.0.96",
        "vllm==0.24.0",
        "vllm-omni==0.24.0",
    )
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    prepare_backend()
    if not server_is_healthy(api_key):
        command = [
            "vllm-omni", "serve", "OpenMOSS-Team/MOSS-TTS-Nano", "--omni", "--host", "127.0.0.1", "--port", str(PORT),
            "--gpu-memory-utilization", "0.72",
        ]
        if api_key:
            command.extend(["--api-key", api_key])
        server_env = os.environ.copy()
        cuda_runtime_libraries = [
            library
            for package_dir in site.getsitepackages()
            for library in Path(package_dir).glob("**/libcudart.so.13")
        ]
        if cuda_runtime_libraries:
            existing_library_path = server_env.get("LD_LIBRARY_PATH", "")
            server_env["LD_LIBRARY_PATH"] = str(cuda_runtime_libraries[0].parent) + (f":{existing_library_path}" if existing_library_path else "")
        server_log = (LOG_DIR / "moss-tts.log").open("ab")
        process = subprocess.Popen(command, stdout=server_log, stderr=subprocess.STDOUT, start_new_session=True, env=server_env)
        wait_for_server(api_key, process)
    BACKEND_MARKER.write_text("moss", encoding="utf-8")
    dns_name, base_url = start_tailscale(auth_key)
    print(json.dumps({"ok": True, "role": "tts", "hostname": dns_name, "base_url": base_url, "model": "OpenMOSS-Team/MOSS-TTS-Nano", "api_key_set": bool(api_key)}, indent=2))


if __name__ == "__main__":
    main()
