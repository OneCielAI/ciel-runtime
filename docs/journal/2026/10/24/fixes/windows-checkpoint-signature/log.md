# Update Log

## 2026-10-24
- **Creation**: Fixed native checkpoint scans that returned nothing on Windows Python 3.12+. The cause was a `Path.stat` versus `os.fstat` `st_ctime_ns` mismatch introduced in `2e55ca4`. Verified with isolated tests and a replay of celly's blocked admission state. Not yet released.
