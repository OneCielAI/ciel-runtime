@echo off
for /f "delims==" %%v in ('set CIEL_RUNTIME_ 2^>nul') do set "%%v="
for /f "delims==" %%v in ('set CLAUDE_ 2^>nul') do set "%%v="
for /f "delims==" %%v in ('set CODEX_ 2^>nul') do set "%%v="
set CLAUDECODE=
set "CIEL_RUNTIME_CONFIG_DIR=G:\ciel-e2e\company-prompt\menu\cfg"
set "CIEL_RUNTIME_TEST_ISOLATED=1"
cd /d "G:\ciel-e2e\company-prompt\menu\ws"
python "C:\Users\djlov\ciel-runtime\ciel_runtime.py" cli --ca-menu --ca-no-update-check --ca-no-self-update-check
echo MENU-EXIT=%ERRORLEVEL%
