@echo off

REM === Relance dans une fenetre persistante si double-clic ===
if "%~1"=="" (
    cmd /k ""%~f0" _RUNNING"
    exit /b
)

chcp 65001 >nul 2>&1
setlocal EnableExtensions EnableDelayedExpansion
title LUMENA
cd /d "%~dp0"

echo.
echo  ================================================================
echo      L U M E N A
echo  ================================================================
echo.

REM === Verifier venv ===
if not exist "venv\" (
    echo [ERREUR] venv/ introuvable. Lancez d'abord INSTALL.bat
    pause & exit /b 1
)
call venv\Scripts\activate.bat

REM === Single instance guard ===
set "LUMENA_SINGLE_INSTANCE=1"


REM === Lire le port depuis .env (defaut 8080) ===
set "LUMENA_PORT=8080"
if exist ".env" (
    for /f "usebackq tokens=1,* delims==" %%a in (".env") do (
        if "%%a"=="LUMENA_PORT" set "LUMENA_PORT=%%b"
    )
)

REM === Ollama (optionnel - detection sans blocage) ===
curl.exe -sf --connect-timeout 2 http://localhost:11434/api/tags >nul 2>&1
if errorlevel 1 (
    where ollama >nul 2>&1
    if not errorlevel 1 (
        echo [..] Demarrage d'Ollama...
        start /min "Ollama Server" ollama serve
        set OLL_OK=0
        for /L %%i in (1,1,10) do (
            if "!OLL_OK!"=="0" (
                curl.exe -sf --connect-timeout 2 http://localhost:11434/api/tags >nul 2>&1
                if not errorlevel 1 set OLL_OK=1
                if "!OLL_OK!"=="0" timeout /t 1 /nobreak >nul
            )
        )
        if "!OLL_OK!"=="1" (
            echo [OK] Ollama operationnel
        ) else (
            echo [WARN] Ollama n'a pas demarre - modeles locaux indisponibles
        )
    ) else (
        echo [INFO] Ollama non detecte - optionnel
    )
) else (
    echo [OK] Ollama operationnel
)

REM === Port deja occupe ? ===
curl.exe -sf --connect-timeout 2 http://127.0.0.1:!LUMENA_PORT!/api/health >nul 2>&1
if not errorlevel 1 (
    echo.
    echo [INFO] Lumena tourne deja sur le port !LUMENA_PORT!.
    start http://localhost:!LUMENA_PORT!
    pause & exit /b 0
)

echo.
echo  =========================================================
echo      LANCEMENT
echo  ---------------------------------------------------------
echo.

REM === Serveur (arriere-plan) ===
start "Lumena Backend" venv\Scripts\python.exe web/server.py

REM === Attente serveur pret (max 30s) ===
echo [..] Demarrage du serveur web...
set SRV_OK=0
for /L %%i in (1,1,30) do (
    if "!SRV_OK!"=="0" (
        curl.exe -sf --connect-timeout 1 http://127.0.0.1:!LUMENA_PORT!/api/health >nul 2>&1
        if not errorlevel 1 set SRV_OK=1
        if "!SRV_OK!"=="0" timeout /t 1 /nobreak >nul
    )
)
if "!SRV_OK!"=="1" (
    echo [OK] Lumena operationnel sur http://localhost:!LUMENA_PORT!
) else (
    echo [WARN] Timeout d'attente - ouverture navigateur quand meme
)
echo.
start http://localhost:!LUMENA_PORT!

echo  [OK] Lumena tourne en arriere-plan.
echo  Cette fenetre se fermera automatiquement a l'arret du serveur.
echo.

:_wait_server
timeout /t 3 /nobreak >nul 2>&1
curl.exe -sf --connect-timeout 2 http://127.0.0.1:!LUMENA_PORT!/api/health >nul 2>&1
if not errorlevel 1 goto _wait_server

REM === Serveur arrete ===
echo.
echo  [OK] Lumena arretee.
timeout /t 2 >nul 2>&1
exit /b 0
