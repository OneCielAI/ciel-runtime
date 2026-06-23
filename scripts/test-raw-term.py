#!/usr/bin/env python3
"""Minimal terminal raw-mode test for SSH debugging."""
import os
import sys
import time

fd = sys.stdin.fileno()
is_tty = os.isatty(fd)
print(f"fd={fd} isatty={is_tty}", flush=True)

if os.name != "nt" and is_tty:
    import termios
    old = termios.tcgetattr(fd)
    new = termios.tcgetattr(fd)
    new[3] = new[3] & ~(termios.ECHO | termios.ICANON)
    new[6][termios.VMIN] = 1
    new[6][termios.VTIME] = 0
    termios.tcsetattr(fd, termios.TCSANOW, new)
    print("Raw mode ON. Press arrow keys (Enter to quit).", flush=True)
else:
    print("Raw mode NOT available. Press Enter to quit.", flush=True)

while True:
    ch = os.read(fd, 1)
    now = time.time()
    print(f"[{now:.3f}] read={ch!r}", flush=True)
    if ch == b"\r" or ch == b"\n":
        break
    if ch == b"\x1b":
        seq = ch.decode("latin-1")
        while True:
            b = os.read(fd, 1)
            if not b:
                break
            seq += b.decode("latin-1")
            if 0x40 <= b[0] <= 0x7E:
                break
        print(f"  sequence={seq!r}", flush=True)

if os.name != "nt" and is_tty:
    termios.tcsetattr(fd, termios.TCSANOW, old)
    print("Restored terminal.", flush=True)
