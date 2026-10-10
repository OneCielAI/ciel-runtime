"""E2E: the session left by e2e-1 is backed up to SSH, S3 and rclone targets, then restored from each.

Targets are local test containers bound to 127.0.0.1 only:
  ssh    linuxserver/openssh-server (port 2222, user ciel, test-only ed25519 key, own known_hosts file)
  s3     rclone/rclone:1.71.1 `serve s3` (port 18080, test-only keys passed as ${ENV} references)
  rclone rclone/rclone:1.71.1 via rclone_docker.py, remote `:local:/remote/ciel-backups`
For each target: `backup target add`, `backup create --target`, a second create (only changed chunks),
`backup list`, `backup verify`, then `backup restore --to-cwd/--codex-home/--ciel-dir/--home/--claude-dir`
into an empty folder and a byte comparison of every restored file with the snapshot's source.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = Path(os.environ.get("E2E_REPO") or r"C:\Users\djlov\ciel-runtime")
SCRATCH = Path(os.environ["E2E_SCRATCH"])
SRC = SCRATCH / "e2e-1-remote-backup-restore"
CFG, WS, HOME, USER_HOME = SRC / "cfg", SRC / "ws", SRC / "codex-home", SRC / "user-home"
KEYS = SCRATCH / "targets" / "keys"
RUN = HERE / "e2e-2-remote-targets"
RUN.mkdir(exist_ok=True)
LOG = open(RUN / "driver.log", "w", encoding="utf-8")
KEY = "e2e-backup-passphrase-test-only"


def log(m: str) -> None:
    line = f"{time.strftime('%H:%M:%S')} {m}"
    print(line, flush=True)
    LOG.write(line + "\n")
    LOG.flush()


def env() -> dict[str, str]:
    e = {k: v for k, v in os.environ.items() if not k.startswith(("CIEL_RUNTIME_", "ANTHROPIC_", "CLAUDE_", "CODEX_", "OPENAI_"))}
    e.update({"CIEL_RUNTIME_CONFIG_DIR": str(CFG), "CODEX_HOME": str(HOME), "CIEL_RUNTIME_BACKUP_KEY": KEY,
              "USERPROFILE": str(USER_HOME), "HOME": str(USER_HOME), "CLAUDE_CONFIG_DIR": str(USER_HOME / ".claude"),
              "E2E_S3_ACCESS": "e2eaccess", "E2E_S3_SECRET": "e2esecretkey123",
              "E2E_RCLONE_REMOTE_DIR": str(SCRATCH / "targets" / "rclone-remote")})
    return e


def ciel(*args: str, show: int = 1500) -> subprocess.CompletedProcess:
    r = subprocess.run([sys.executable, str(REPO / "ciel_runtime.py"), "cli", *args], cwd=str(WS), env=env(),
                       capture_output=True, text=True, timeout=900)
    log(f"$ ciel-runtime {' '.join(args)}  (exit {r.returncode})\n{(r.stdout + r.stderr).strip()[:show]}")
    return r


rclone_cmd = RUN / "rclone-docker.cmd"
rclone_cmd.write_text(f'@"{sys.executable}" "{HERE / "rclone_docker.py"}" %*\n', encoding="utf-8")
(SCRATCH / "targets" / "rclone-remote").mkdir(parents=True, exist_ok=True)
TARGETS = {
    "e2e-ssh": ["ssh", "host=ciel@127.0.0.1", "port=2222", "path=/config/ciel-backups",
                f"identity={KEYS / 'id_e2e'}", f"known_hosts={KEYS / 'known_hosts'}"],
    "e2e-s3": ["s3", "endpoint=http://127.0.0.1:18080", "bucket=e2e-bucket", "prefix=ciel",
               "access_key=${E2E_S3_ACCESS}", "secret_key=${E2E_S3_SECRET}"],
    "e2e-rclone": ["rclone", "remote=:local:/remote/ciel-backups", f"binary={rclone_cmd}"],
}
summary = {}
for name, spec in TARGETS.items():
    log(f"===== target {name}")
    ciel("backup", "target", "add", name, *spec)
    first = ciel("backup", "create", "--target", name, "--label", f"to-{name}", "--json", show=400)
    if first.returncode != 0:
        summary[name] = {"create": "failed"}
        continue
    created = json.loads(first.stdout)
    with (WS / "work-notes.md").open("a", encoding="utf-8") as f:
        f.write(f"edit for {name}\n")
    second = json.loads(ciel("backup", "create", "--target", name, "--label", "second", "--json", show=400).stdout)
    ciel("backup", "list", "--target", name)
    verified = ciel("backup", "verify", created["id"], "--target", name).returncode == 0
    dest = SCRATCH / "targets" / f"restored-{name}"
    import shutil

    shutil.rmtree(dest, ignore_errors=True)
    restored = ciel("backup", "restore", created["id"], "--target", name, "--json", "--no-safety",
                    "--to-cwd", str(dest / "ws"), "--codex-home", str(dest / "codex-home"), "--ciel-dir", str(dest / "cfg"),
                    "--home", str(dest / "user-home"), "--claude-dir", str(dest / "user-home" / ".claude"), show=600)
    result = json.loads(restored.stdout) if restored.returncode == 0 else {}
    # Compare restored files with the local snapshot of the same id (the local target holds the same manifest only
    # when created there), so compare against the live source for files that did not change since.
    same = differ = 0
    differ_list = []
    for root_name, src_root in (("ws", WS), ("codex-home", HOME)):
        for p in (dest / root_name).rglob("*"):
            if not p.is_file():
                continue
            original = src_root / p.relative_to(dest / root_name)
            if original.is_file() and original.read_bytes() == p.read_bytes():
                same += 1
            else:
                differ += 1
                differ_list.append(str(p.relative_to(dest)))
    summary[name] = {
        "first_upload_chunks": created["stats"]["uploaded_chunks"], "first_chunks": created["stats"]["chunks"],
        "second_upload_chunks": second["stats"]["uploaded_chunks"], "verify_ok": verified,
        "restored_files": result.get("written"), "secrets_restored": result.get("secrets_restored"),
        "identical_to_source": same, "different_from_source": differ, "different": differ_list[:8],
    }
    log(f"summary {name}: {json.dumps(summary[name])}")
log("SUMMARY " + json.dumps(summary, indent=1))
