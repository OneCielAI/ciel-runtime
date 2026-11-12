@echo off
chcp 65001 >nul
cd /d "C:\Users\djlov\AppData\Local\Temp\ciel-sse-true-fork-a-sj05_0te\workspace"
py -3 "C:\Users\djlov\ciel-runtime\.local\tmp\work-parallel\.local\sse-true-fork\e2e\snapshot-frozen\ciel_runtime.py" cli -- --session-id a1111111-1111-4111-8111-111111111111 --debug-file "docs\journal\2026\11\11\features\sse-native-fork\e2e-source\run-a-20261111-102246\claude-debug.txt"
echo [probe] ciel-runtime exited with %ERRORLEVEL%
