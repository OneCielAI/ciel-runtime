@echo off
for /f "delims==" %%v in ('set CIEL_RUNTIME_ 2^>nul') do set "%%v="
for /f "delims==" %%v in ('set ANTHROPIC_ 2^>nul') do set "%%v="
for /f "delims==" %%v in ('set CLAUDE_ 2^>nul') do set "%%v="
for /f "delims==" %%v in ('set CODEX_ 2^>nul') do set "%%v="
for /f "delims==" %%v in ('set OPENAI_ 2^>nul') do set "%%v="
set CLAUDECODE=
set "CIEL_RUNTIME_CONFIG_DIR=D:\Temp\ciel-e2e-safe-restart-pyuyzurg\cfg"
set "CIEL_RUNTIME_ROUTER_PORT=59125"
set "CIEL_RUNTIME_TEST_ISOLATED=1"
set "USERPROFILE=D:\Temp\ciel-e2e-safe-restart-pyuyzurg\home-repo"
set "HOME=D:\Temp\ciel-e2e-safe-restart-pyuyzurg\home-repo"
set "TEMP=D:\Temp"
set "TMP=D:\Temp"
cd /d "D:\Temp\ciel-e2e-safe-restart-pyuyzurg\home-repo"
python "<home>\ciel-runtime\.local\tmp\work-parallel\ciel_runtime.py" cli --ca-runtime claude --ca-no-update-check --ca-no-self-update-check
echo LAUNCHER-EXIT=%ERRORLEVEL%
