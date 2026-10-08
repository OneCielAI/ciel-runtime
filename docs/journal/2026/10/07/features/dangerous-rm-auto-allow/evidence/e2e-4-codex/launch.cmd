@echo off
for /f "delims==" %%v in ('set CIEL_RUNTIME_ 2^>nul') do set "%%v="
for /f "delims==" %%v in ('set ANTHROPIC_ 2^>nul') do set "%%v="
for /f "delims==" %%v in ('set CLAUDE_ 2^>nul') do set "%%v="
for /f "delims==" %%v in ('set CODEX_ 2^>nul') do set "%%v="
for /f "delims==" %%v in ('set OPENAI_ 2^>nul') do set "%%v="
set CLAUDECODE=
set "CIEL_RUNTIME_CONFIG_DIR=C:\Users\djlov\ciel-runtime\docs\journal\2026\10\07\features\dangerous-rm-auto-allow\evidence\e2e-codex-1791425486\cfg"
set "CIEL_RUNTIME_ROUTER_PORT=53346"
set "CODEX_HOME=C:\Users\djlov\.ciel-e2e\codex-home"
set "CIEL_RUNTIME_CODEX_APP_SERVER_LISTEN=ws://127.0.0.1:53347"
set "CIEL_DIAG_RECEIPT_LOG=C:\Users\djlov\ciel-runtime\docs\journal\2026\10\07\features\dangerous-rm-auto-allow\evidence\e2e-codex-1791425486\receipt-diag.log"
cd /d "C:\Users\djlov\.ciel-e2e\ws-rm"
python "C:\Users\djlov\ciel-runtime\ciel_runtime.py" cli --ca-runtime codex --ca-no-update-check --ca-no-self-update-check
echo LAUNCHER-EXIT=%ERRORLEVEL%
