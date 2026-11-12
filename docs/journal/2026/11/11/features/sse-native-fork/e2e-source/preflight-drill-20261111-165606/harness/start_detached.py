"""Start the owned E2E driver with no console (DETACHED_PROCESS, new process group).

The driver attaches to the owned Claude console itself. Started this way it shares
no console with the calling shell, and runs python.exe directly (no py.exe launcher
job object), so a console event in the calling shell cannot end it mid-run.
Usage: start_detached.py <log> <driver args...>
"""
import sys
sys.dont_write_bytecode = True
import os, subprocess
from pathlib import Path

PY311 = r'C:\Program Files\Python311\python.exe'
DRIVER = str(Path(__file__).resolve().parent / 'walkie_e2e_driver_v4.py')
log = open(sys.argv[1], 'ab')
env = dict(os.environ, PYTHONDONTWRITEBYTECODE='1', PYTHONIOENCODING='utf-8')
proc = subprocess.Popen([PY311, '-B', DRIVER, *sys.argv[2:]], stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
                        creationflags=0x00000008 | 0x00000200, env=env, close_fds=True)
print(proc.pid)
