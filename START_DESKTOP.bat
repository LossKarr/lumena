@echo off

REM === Relance dans une fenetre persistante si double-clic ===
if "%~1"=="" (
    cmd /c ""%~f0" _RUNNING"
    exit /b
)

chcp 65001 >nul 2>&1
setlocal EnableExtensions EnableDelayedExpansion
title LUMENA Desktop
cd /d "%~dp0"

echo.
echo  ================================================================
echo      L U M E N A   —   Mode Desktop
echo  ================================================================
echo.

REM === Verifier venv ===
if not exist "venv\" (
    echo [ERREUR] venv/ introuvable. Lancez d'abord INSTALL.bat
    pause & exit /b 1
)
call venv\Scripts\activate.bat

REM === Verifier pywebview ===
python -c "import webview" >nul 2>&1
if errorlevel 1 (
    echo [..] Installation de pywebview...
    pip install pywebview >nul 2>&1
    if errorlevel 1 (
        echo [ERREUR] Impossible d'installer pywebview.
        echo          Lancez START.bat pour le mode navigateur classique.
        pause & exit /b 1
    )
    echo [OK] pywebview installe
)

REM === Verifier WebView2 Runtime (requis pour ES modules) ===
reg query "HKLM\SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}" >nul 2>&1
if errorlevel 1 (
    reg query "HKLM\SOFTWARE\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}" >nul 2>&1
)
if errorlevel 1 (
    echo [WARN] WebView2 Runtime non detecte.
    echo        La fenetre desktop necessite WebView2 pour fonctionner.
    echo.
    echo        Telechargement automatique...
    curl.exe -sL -o "%TEMP%\MicrosoftEdgeWebview2Setup.exe" "https://go.microsoft.com/fwlink/p/?LinkId=2124703"
    if exist "%TEMP%\MicrosoftEdgeWebview2Setup.exe" (
        echo [..] Installation de WebView2 Runtime...
        "%TEMP%\MicrosoftEdgeWebview2Setup.exe" /silent /install
        echo [OK] WebView2 installe
        del "%TEMP%\MicrosoftEdgeWebview2Setup.exe" >nul 2>&1
    ) else (
        echo [ERREUR] Telechargement echoue. Installez WebView2 manuellement:
        echo          https://developer.microsoft.com/en-us/microsoft-edge/webview2/
        echo          Ou lancez START.bat pour le mode navigateur classique.
        pause & exit /b 1
    )
)

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
    echo        Ouverture de la fenetre desktop...
    python run_desktop.py --web
    pause & exit /b 0
)

echo.
echo  =========================================================
echo      LANCEMENT DESKTOP
echo  ---------------------------------------------------------
echo.

REM === Lancement natif (serveur + fenetre) ===
echo [..] Demarrage de Lumena Desktop...
python run_desktop.py

echo.
echo  [OK] Lumena Desktop fermee.
timeout /t 2 >nul 2>&1
exit /b 0
