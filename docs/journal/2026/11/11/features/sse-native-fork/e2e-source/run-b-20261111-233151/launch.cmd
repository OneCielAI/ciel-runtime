@echo off
chcp 65001 >nul
cd /d "C:\Users\djlov\AppData\Local\Temp\ciel-sse-true-fork-b-3wc04zuy\workspace"
py -3 "C:\Users\djlov\ciel-runtime\.local\tmp\work-parallel\.local\sse-true-fork\e2e\run-copies\run-b-20261111-233151\ciel_runtime.py" cli -- --session-id b2222222-2222-4222-8222-222222222222 --debug-file "C:\Users\djlov\ciel-runtime\.local\tmp\work-parallel\docs\journal\2026\11\11\features\sse-native-fork\e2e-source\run-b-20261111-233151\claude-debug.txt"
echo [probe] ciel-runtime exited with %ERRORLEVEL%
