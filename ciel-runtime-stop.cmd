@echo off
setlocal

if defined CIEL_RUNTIME_HOME (
  set "CIEL_RUNTIME_SCRIPT=%CIEL_RUNTIME_HOME%\ciel_runtime.py"
) else (
  set "CIEL_RUNTIME_SCRIPT=%USERPROFILE%\.local\share\ciel-runtime\ciel_runtime.py"
)

if defined CIEL_RUNTIME_PYTHON (
  "%CIEL_RUNTIME_PYTHON%" "%CIEL_RUNTIME_SCRIPT%" cli stop
  exit /b %ERRORLEVEL%
)

py -3 "%CIEL_RUNTIME_SCRIPT%" cli stop
if not %ERRORLEVEL%==9009 exit /b %ERRORLEVEL%
python "%CIEL_RUNTIME_SCRIPT%" cli stop
exit /b %ERRORLEVEL%
