@echo off
call "C:/Users/djlov/.local/bin/ciel-runtime.cmd" --ca-runtime codex-remote --ca-no-update-check --ca-no-self-update-check
echo OWNED-LAUNCHER-EXIT=%ERRORLEVEL%
