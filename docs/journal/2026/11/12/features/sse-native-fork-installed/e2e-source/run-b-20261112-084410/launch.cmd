@echo off
chcp 65001 >nul
cd /d "C:\Users\djlov\AppData\Local\Temp\ciel-sse-true-fork-b-_ji6g9o2\workspace"
py -3 "C:\Users\djlov\ciel-runtime\.local\tmp\work-parallel\.local\sse-true-fork\e2e\run-copies\run-b-20261112-084410\ciel_runtime.py" cli -- --session-id b2222222-2222-4222-8222-222222222222 --debug-file "C:\Users\djlov\ciel-runtime\.local\tmp\work-parallel\docs\journal\2026\11\12\features\sse-native-fork-installed\e2e-source\run-b-20261112-084410\claude-debug.txt"
echo [probe] ciel-runtime exited with %ERRORLEVEL%
