@echo off
for /f "delims==" %%v in ('set CIEL_RUNTIME_ 2^>nul') do set "%%v="
for /f "delims==" %%v in ('set ANTHROPIC_ 2^>nul') do set "%%v="
for /f "delims==" %%v in ('set CLAUDE_ 2^>nul') do set "%%v="
for /f "delims==" %%v in ('set CODEX_ 2^>nul') do set "%%v="
for /f "delims==" %%v in ('set OPENAI_ 2^>nul') do set "%%v="
set CLAUDECODE=
set "CIEL_RUNTIME_CONFIG_DIR=G:\ciel-e2e\direct-plain\codex-router-head\cfg"
set "CIEL_RUNTIME_ROUTER_PORT=65238"
set "CIEL_RUNTIME_TEST_ISOLATED=1"
set "CODEX_HOME=G:\ciel-e2e\direct-plain\codex-router-head\codex-home"
set "CIEL_RUNTIME_CODEX_APP_SERVER_LISTEN=ws://127.0.0.1:65239"
set "CIEL_RUNTIME_STARTUP_AUTO_ANSWER=1"
cd /d "G:\ciel-e2e\direct-plain\codex-router-head\ws"
python "G:\ciel-e2e\head-tree\ciel_runtime.py" cli --ca-runtime codex --ca-no-update-check --ca-no-self-update-check
echo LAUNCHER-EXIT=%ERRORLEVEL%
