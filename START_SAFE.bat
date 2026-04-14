@echo off
chcp 65001 >nul 2>&1
setlocal EnableExtensions EnableDelayedExpansion
title LUMENA — Mode Safe

REM === Autonomie limitee ===
set "LUMENA_AUTONOMY_EXECUTE_ACTIONS=1"
set "LUMENA_AUTONOMY_MAX_ACTIONS_PER_HOUR=3"
set "LUMENA_AUTONOMY_ACTION_TIMEOUT_SEC=120"

REM === Delegation au launcher principal ===
call "%~dp0START.bat"
