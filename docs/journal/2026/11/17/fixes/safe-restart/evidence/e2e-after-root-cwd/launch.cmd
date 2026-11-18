@echo off
for /f "delims==" %%v in ('set CIEL_RUNTIME_ 2^>nul') do set "%%v="
for /f "delims==" %%v in ('set ANTHROPIC_ 2^>nul') do set "%%v="
for /f "delims==" %%v in ('set CLAUDE_ 2^>nul') do set "%%v="
for /f "delims==" %%v in ('set CODEX_ 2^>nul') do set "%%v="
for /f "delims==" %%v in ('set OPENAI_ 2^>nul') do set "%%v="
set CLAUDECODE=
set "CIEL_RUNTIME_CONFIG_DIR=<temp>\ciel-e2e-safe-restart-9a2z4px6\cfg"
set "CIEL_RUNTIME_ROUTER_PORT=50343"
set "CIEL_RUNTIME_TEST_ISOLATED=1"
set "USERPROFILE=<temp>\ciel-e2e-safe-restart-9a2z4px6\home-repo"
set "HOME=<temp>\ciel-e2e-safe-restart-9a2z4px6\home-repo"
cd /d "<temp>\ciel-e2e-safe-restart-9a2z4px6\home-repo"
python "<home>\ciel-runtime\.local\tmp\work-parallel\ciel_runtime.py" cli --ca-runtime claude --ca-no-update-check --ca-no-self-update-check
echo LAUNCHER-EXIT=%ERRORLEVEL%
