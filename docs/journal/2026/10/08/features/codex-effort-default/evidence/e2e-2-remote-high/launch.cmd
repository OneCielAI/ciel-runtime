@echo off
for /f "delims==" %%v in ('set CIEL_RUNTIME_ 2^>nul') do set "%%v="
for /f "delims==" %%v in ('set ANTHROPIC_ 2^>nul') do set "%%v="
for /f "delims==" %%v in ('set CLAUDE_ 2^>nul') do set "%%v="
for /f "delims==" %%v in ('set CODEX_ 2^>nul') do set "%%v="
for /f "delims==" %%v in ('set OPENAI_ 2^>nul') do set "%%v="
set CLAUDECODE=
set "CIEL_RUNTIME_CONFIG_DIR=C:\Users\djlov\AppData\Local\Temp\claude\C--Users-djlov-ciel-runtime\839a1c11-9672-467c-bcda-8f929bddb4f3\scratchpad\effort-e2e\e2e-2-remote-high\cfg"
set "CIEL_RUNTIME_ROUTER_PORT=54648"
set "CODEX_HOME=C:\Users\djlov\AppData\Local\Temp\claude\C--Users-djlov-ciel-runtime\839a1c11-9672-467c-bcda-8f929bddb4f3\scratchpad\effort-e2e\e2e-2-remote-high\codex-home"
set "CIEL_RUNTIME_CODEX_APP_SERVER_LISTEN=ws://127.0.0.1:54649"
set "CIEL_RUNTIME_CODEX_ROUTED_UPSTREAM=http://127.0.0.1:54647/backend-api/codex"
cd /d "C:\Users\djlov\AppData\Local\Temp\claude\C--Users-djlov-ciel-runtime\839a1c11-9672-467c-bcda-8f929bddb4f3\scratchpad\effort-e2e\e2e-2-remote-high\ws"
python "C:\Users\djlov\ciel-runtime\ciel_runtime.py" cli --ca-runtime codex-remote --ca-no-update-check --ca-no-self-update-check
echo LAUNCHER-EXIT=%ERRORLEVEL%
