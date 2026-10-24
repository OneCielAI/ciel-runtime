"""Compare os.fstat and Path.stat st_ctime_ns for one file after an append."""
import os, sys, tempfile, time
from pathlib import Path
d = tempfile.mkdtemp(dir=sys.argv[1] if len(sys.argv) > 1 else None)
p = Path(d) / "probe.jsonl"
p.write_bytes(b"{}\n")
time.sleep(1.1)
with p.open("ab") as w:
    w.write(b"{}\n")
with p.open("rb") as r:
    f = os.fstat(r.fileno())
s = p.stat()
print(sys.version.split()[0], d[:3], "fstat_ctime==stat_ctime", f.st_ctime_ns == s.st_ctime_ns,
      "fstat_ctime==mtime", f.st_ctime_ns == f.st_mtime_ns, "stat_ctime==mtime", s.st_ctime_ns == s.st_mtime_ns,
      "rest_equal", (f.st_dev, f.st_ino, f.st_size, f.st_mtime_ns) == (s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns))
p.unlink(); os.rmdir(d)
