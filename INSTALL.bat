@echo off
chcp 65001 >nul 2>&1
setlocal EnableExtensions
cd /d "%~dp0"

REM Double-clic : garder la console ouverte pour montrer le resultat.
if "%~1"=="" (
    cmd /k ""%~f0" _RUNNING"
    exit /b
)

title LUMENA - Installation
echo.
echo  ================================================================
echo      L U M E N A  -  Installation
echo  ================================================================
echo.

set "RUNTIME_INSTALLER=%CD%\scripts\install_runtime.ps1"
set "PYTHON_INSTALLER=%CD%\installer\deps\python-3.12.10-amd64.exe"
set "WEBVIEW_INSTALLER=%CD%\installer\deps\MicrosoftEdgeWebView2RuntimeInstallerX64.exe"
set "INSTALL_LOG=%CD%\data\logs\installer.log"

if not exist "%RUNTIME_INSTALLER%" (
    echo [ERREUR] scripts\install_runtime.ps1 est introuvable.
    exit /b 1
)

powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%RUNTIME_INSTALLER%" -AppDir "%CD%" -BundledPythonInstaller "%PYTHON_INSTALLER%" -BundledWebViewInstaller "%WEBVIEW_INSTALLER%" -LogFile "%INSTALL_LOG%"
if errorlevel 1 (
    echo.
    echo [ERREUR] Installation interrompue.
    echo          Journal : %INSTALL_LOG%
    exit /b 1
)

echo.
echo [OK] Lumena est installe.
echo      Lancement de Lumena Desktop...
start "" "%CD%\START_DESKTOP.bat"
exit /b 0
