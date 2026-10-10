"""Test-only stand-in for an installed rclone: runs rclone/rclone:1.71.1 in Docker.

Host paths under %TEMP% (the backup staging/get folders) are mounted at /hosttemp, and
E2E_RCLONE_REMOTE_DIR at /remote, so ``:local:/remote/...`` acts as the rclone remote.
"""
import os
import subprocess
import sys
import tempfile

temp = os.path.normcase(os.path.abspath(tempfile.gettempdir()))
remote = os.environ["E2E_RCLONE_REMOTE_DIR"]


def translate(arg: str) -> str:
    if not (len(arg) > 2 and arg[1] == ":"):
        return arg
    full = os.path.abspath(arg)
    if os.path.normcase(full).startswith(temp):
        return "/hosttemp" + full[len(temp):].replace("\\", "/")
    return arg


args = [translate(a) for a in sys.argv[1:]]
command = ["docker", "run", "--rm", "-v", f"{tempfile.gettempdir()}:/hosttemp", "-v", f"{remote}:/remote", "rclone/rclone:1.71.1", *args]
sys.exit(subprocess.run(command).returncode)
