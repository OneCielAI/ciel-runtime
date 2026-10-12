"""Does a child writing N bytes to a PIPE ever exit while the parent only polls (as run_backup does)?

usage: python pipe_block.py
Prints, for each size, whether the child exited within 5 s without the parent reading.
"""
import subprocess
import sys
import time

for size in (1024, 4000, 4096, 4200, 4755, 8192, 65536, 70000):
    child = subprocess.Popen([sys.executable, "-c", f"import sys; sys.stdout.write('x' * {size}); sys.stdout.flush()"],
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    deadline = time.time() + 5
    while child.poll() is None and time.time() < deadline:
        time.sleep(0.1)
    exited = child.poll() is not None
    out, _ = child.communicate()
    print(f"{size:>6} bytes: {'exited by itself' if exited else 'BLOCKED until the parent read'} (read back {len(out)})", flush=True)
