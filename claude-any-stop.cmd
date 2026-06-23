@echo off
setlocal

if defined CLAUDE_ANY_HOME (
  set "CLAUDE_ANY_SCRIPT=%CLAUDE_ANY_HOME%\claude_any.py"
) else (
  set "CLAUDE_ANY_SCRIPT=%USERPROFILE%\.local\share\claude-any\claude_any.py"
)

if defined CLAUDE_ANY_PYTHON (
  "%CLAUDE_ANY_PYTHON%" "%CLAUDE_ANY_SCRIPT%" cli stop
  exit /b %ERRORLEVEL%
)

py -3 "%CLAUDE_ANY_SCRIPT%" cli stop
if not %ERRORLEVEL%==9009 exit /b %ERRORLEVEL%
python "%CLAUDE_ANY_SCRIPT%" cli stop
exit /b %ERRORLEVEL%
