@echo off
for /f "delims==" %%v in ('set CIEL_RUNTIME_ 2^>nul') do set "%%v="
for /f "delims==" %%v in ('set ANTHROPIC_ 2^>nul') do set "%%v="
for /f "delims==" %%v in ('set CLAUDE_ 2^>nul') do set "%%v="
for /f "delims==" %%v in ('set CODEX_ 2^>nul') do set "%%v="
for /f "delims==" %%v in ('set OPENAI_ 2^>nul') do set "%%v="
set CLAUDECODE=
set "CIEL_RUNTIME_CONFIG_DIR=C:\Users\djlov\ciel-runtime\docs\journal\2026\10\07\features\dangerous-rm-auto-allow\evidence\e2e-claude-1791423707\cfg"
set "CIEL_RUNTIME_ROUTER_PORT=59666"
set "CIEL_RUNTIME_TEST_ISOLATED=1"
cd /d "C:\Users\djlov\ciel-runtime\docs\journal\2026\10\07\features\dangerous-rm-auto-allow\evidence\e2e-claude-1791423707\workspace"
python "C:\Users\djlov\ciel-runtime\ciel_runtime.py" cli --ca-runtime claude --ca-no-update-check --ca-no-self-update-check
echo LAUNCHER-EXIT=%ERRORLEVEL%
