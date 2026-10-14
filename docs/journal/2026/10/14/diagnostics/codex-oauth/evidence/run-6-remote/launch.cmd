@echo off
for /f "delims==" %%v in ('set CIEL_RUNTIME_ 2^>nul') do set "%%v="
for /f "delims==" %%v in ('set CLAUDE_ 2^>nul') do set "%%v="
for /f "delims==" %%v in ('set CODEX_ 2^>nul') do set "%%v="
set CLAUDECODE=
set "CIEL_RUNTIME_CONFIG_DIR=G:\ciel-e2e\astra\cfg"
set "CIEL_RUNTIME_ROUTER_PORT=60056"
set "CIEL_RUNTIME_TEST_ISOLATED=1"
set "CODEX_HOME=G:\ciel-e2e\astra\codex-home"
set "CIEL_RUNTIME_CODEX_ROUTED_UPSTREAM=http://127.0.0.1:60055"
set "CIEL_RUNTIME_STARTUP_AUTO_ANSWER=1"
set "PATH=G:\ciel-e2e\astra\codex162\node_modules\.bin;%PATH%"
set "CIEL_RUNTIME_CODEX_APP_SERVER_LISTEN=ws://127.0.0.1:60057"
cd /d "G:\ciel-e2e\astra\ws"
call codex --version
python "C:\Users\djlov\ciel-runtime\ciel_runtime.py" cli --ca-runtime codex-remote --ca-no-update-check --ca-no-self-update-check
echo LAUNCHER-EXIT=%ERRORLEVEL%
