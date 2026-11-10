@echo off
chcp 65001 >nul
cd /d C:\Users\djlov\AppData\Local\Temp\ciel-owned-side-release-skq2ww78\workspace
py -3 "C:\Users\djlov\.local\share\ciel-runtime-5c91ea8838c83b6f39214ba63b850d853e989508\ciel_runtime.py" version
py -3 "C:\Users\djlov\.local\share\ciel-runtime-5c91ea8838c83b6f39214ba63b850d853e989508\ciel_runtime.py" cli -- --session-id 55555555-5555-4555-8555-555555555555 --debug-file "C:\Users\djlov\ciel-runtime\.local\tmp\work-parallel\.local\owned-side-release-20261110-115209\claude-debug.txt"
echo [probe] ciel-runtime exited with %ERRORLEVEL%
