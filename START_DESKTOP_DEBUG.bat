@echo off
REM Demarrage Desktop avec console visible, meme si LUMENA_DESKTOP_SHOW_CONSOLE=0.
set "LUMENA_DESKTOP_SHOW_CONSOLE=1"
call "%~dp0START_DESKTOP.bat" _RUNNING_VISIBLE
