@echo off
for /f "delims==" %%v in ('set CIEL_RUNTIME_ 2^>nul') do set "%%v="
for /f "delims==" %%v in ('set ANTHROPIC_ 2^>nul') do set "%%v="
for /f "delims==" %%v in ('set CLAUDE_ 2^>nul') do set "%%v="
for /f "delims==" %%v in ('set CODEX_ 2^>nul') do set "%%v="
for /f "delims==" %%v in ('set OPENAI_ 2^>nul') do set "%%v="
set CLAUDECODE=
set "CIEL_RUNTIME_CONFIG_DIR=C:\Users\djlov\AppData\Local\Temp\claude\C--Users-djlov-ciel-runtime\839a1c11-9672-467c-bcda-8f929bddb4f3\scratchpad\e2e-backup\e2e-3-migrate-triggers\cfg2"
set "CODEX_HOME=C:\Users\djlov\AppData\Local\Temp\claude\C--Users-djlov-ciel-runtime\839a1c11-9672-467c-bcda-8f929bddb4f3\scratchpad\e2e-backup\e2e-3-migrate-triggers\codex2"
set "USERPROFILE=C:\Users\djlov\AppData\Local\Temp\claude\C--Users-djlov-ciel-runtime\839a1c11-9672-467c-bcda-8f929bddb4f3\scratchpad\e2e-backup\e2e-3-migrate-triggers\home2"
set "HOME=C:\Users\djlov\AppData\Local\Temp\claude\C--Users-djlov-ciel-runtime\839a1c11-9672-467c-bcda-8f929bddb4f3\scratchpad\e2e-backup\e2e-3-migrate-triggers\home2"
set "CLAUDE_CONFIG_DIR=C:\Users\djlov\AppData\Local\Temp\claude\C--Users-djlov-ciel-runtime\839a1c11-9672-467c-bcda-8f929bddb4f3\scratchpad\e2e-backup\e2e-3-migrate-triggers\home2\.claude"
set "CIEL_RUNTIME_CODEX_ROUTED_UPSTREAM=http://127.0.0.1:9/backend-api/codex"
cd /d "C:\Users\djlov\AppData\Local\Temp\claude\C--Users-djlov-ciel-runtime\839a1c11-9672-467c-bcda-8f929bddb4f3\scratchpad\e2e-backup\e2e-3-migrate-triggers\ws2"
python "C:\Users\djlov\ciel-runtime\ciel_runtime.py" cli --ca-menu --ca-no-update-check --ca-no-self-update-check
echo LAUNCHER-EXIT=%ERRORLEVEL%
