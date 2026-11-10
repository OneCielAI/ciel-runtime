@echo off
chcp 65001 >nul
cd /d C:\Users\djlov\AppData\Local\Temp\ciel-owned-side-release-l01q5zeq\workspace
py -3 "C:\Users\djlov\ciel-runtime\.local\tmp\work-parallel\ciel_runtime.py" version
py -3 "C:\Users\djlov\ciel-runtime\.local\tmp\work-parallel\ciel_runtime.py" cli -- --session-id 55555555-5555-4555-8555-555555555555 --debug-file "C:\Users\djlov\ciel-runtime\.local\tmp\work-parallel\.local\owned-side-release-20261110-105830\claude-debug.txt"
echo [probe] ciel-runtime exited with %ERRORLEVEL%
