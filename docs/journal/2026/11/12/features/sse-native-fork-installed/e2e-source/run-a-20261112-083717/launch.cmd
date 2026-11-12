@echo off
chcp 65001 >nul
cd /d "C:\Users\djlov\AppData\Local\Temp\ciel-sse-true-fork-a-lzcair34\workspace"
py -3 "C:\Users\djlov\ciel-runtime\.local\tmp\work-parallel\.local\sse-true-fork\e2e\run-copies\run-a-20261112-083717\ciel_runtime.py" cli -- --session-id a1111111-1111-4111-8111-111111111111 --debug-file "C:\Users\djlov\ciel-runtime\.local\tmp\work-parallel\docs\journal\2026\11\12\features\sse-native-fork-installed\e2e-source\run-a-20261112-083717\claude-debug.txt"
echo [probe] ciel-runtime exited with %ERRORLEVEL%
