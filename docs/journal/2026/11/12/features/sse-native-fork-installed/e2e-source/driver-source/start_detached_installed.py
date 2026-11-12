"""Start the installed-build E2E driver with no console (DETACHED_PROCESS, new process group).

Same launch shape as start_detached.py, for walkie_e2e_driver_installed.py.
Usage: start_detached_installed.py <log> <driver args...>
       e.g. ... run-a-installed.log A --output <dir> --installed <npm package root>
"""
import sys
sys.dont_write_bytecode = True
import os, subprocess
from pathlib import Path

PY311 = r'C:\Program Files\Python311\python.exe'
DRIVER = str(Path(__file__).resolve().parent / 'walkie_e2e_driver_installed.py')
log = open(sys.argv[1], 'ab')
env = dict(os.environ, PYTHONDONTWRITEBYTECODE='1', PYTHONIOENCODING='utf-8')
proc = subprocess.Popen([PY311, '-B', DRIVER, *sys.argv[2:]], stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
                        creationflags=0x00000008 | 0x00000200, env=env, close_fds=True)
print(proc.pid)
