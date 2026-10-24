# Update Log

## 2026-10-24
- **Update**: Published `0.2.51-nightly.20261024-115611.5d4dc0e` (gitHead verified) and installed it locally: per-file atomic replacement, identical hashes, 99 installed-path tests OK; live routers unchanged. Celly's own prefix still holds `a7e28f9`. See [release](/verification.md#release).
- **Creation**: Fixed native checkpoint scans that returned nothing on Windows Python 3.12+. The cause was a `Path.stat` versus `os.fstat` `st_ctime_ns` mismatch introduced in `2e55ca4`. Verified with isolated tests and a replay of celly's blocked admission state. 
