@echo off

REM === Relance dans une fenetre persistante si double-clic ===
if "%~1"=="" (
    cmd /k ""%~f0" _RUNNING"
    exit /b
)

chcp 65001 >nul 2>&1
setlocal EnableExtensions EnableDelayedExpansion
cd /d "%~dp0"
set "APPDIR=%CD%"

REM === Desactive QuickEdit (empeche le freeze console au clic) ===
reg add HKCU\Console /v QuickEdit /t REG_DWORD /d 0 /f >nul 2>&1

echo.
echo  ================================================================
echo      L U M E N A  -  Installation
echo  ================================================================
echo.

REM === 1. Verifie Python ===
set "NEED_PYTHON=0"
set "PY_CMD=python"

REM Chercher python dans PATH
python --version >nul 2>&1
if errorlevel 1 (
    set "NEED_PYTHON=1"
) else (
    for /f "tokens=2 delims= " %%v in ('python --version 2^>^&1') do set PY_VER=%%v
    for /f "tokens=1,2 delims=." %%a in ("!PY_VER!") do (
        set PY_MAJOR=%%a
        set PY_MINOR=%%b
    )
    if !PY_MAJOR! LSS 3 set "NEED_PYTHON=1"
    if !PY_MAJOR! EQU 3 if !PY_MINOR! LSS 10 set "NEED_PYTHON=1"
    if !PY_MAJOR! EQU 3 if !PY_MINOR! GEQ 13 set "NEED_PYTHON=1"
)

if "!NEED_PYTHON!"=="1" (
    echo [..] Python compatible non detecte - installation automatique de Python 3.12...
    echo.

    REM Telecharger l'installeur Python 3.12
    set "PY_URL=https://www.python.org/ftp/python/3.12.10/python-3.12.10-amd64.exe"
    set "PY_INSTALLER=%TEMP%\python-3.12.10-amd64.exe"

    echo [..] Telechargement de Python 3.12.10...
    powershell -Command "[Net.ServicePointManager]::SecurityProtocol=[Net.SecurityProtocolType]::Tls12; Invoke-WebRequest -Uri '!PY_URL!' -OutFile '!PY_INSTALLER!'" 2>nul
    if not exist "!PY_INSTALLER!" (
        echo [ERREUR] Echec du telechargement. Verifiez votre connexion internet.
        echo          Ou installez manuellement : https://www.python.org/downloads/release/python-31210/
        echo          Cochez "Add Python to PATH".
        pause & exit /b 1
    )
    echo [OK] Telechargement termine

    echo [..] Installation de Python 3.12.10 ^(cela peut prendre 1-2 minutes^)...
    "!PY_INSTALLER!" /quiet InstallAllUsers=0 PrependPath=1 Include_test=0 Include_doc=0 Include_launcher=1
    
    REM Rafraichir le PATH pour cette session - chercher Python 3.12 dans les emplacements standards
    set "PY312_USER=%LOCALAPPDATA%\Programs\Python\Python312"
    set "PY312_SYSTEM=C:\Program Files\Python312"
    if exist "!PY312_USER!\python.exe" (
        set "PATH=!PY312_USER!;!PY312_USER!\Scripts;!PATH!"
    ) else if exist "!PY312_SYSTEM!\python.exe" (
        set "PATH=!PY312_SYSTEM!;!PY312_SYSTEM!\Scripts;!PATH!"
    )

    REM Verifier que ca marche
    python --version >nul 2>&1
    if errorlevel 1 (
        REM Peut-etre que l'install a marche mais PATH pas encore a jour - essayer directement
        if exist "!PY312_USER!\python.exe" (
            set "PY_CMD=!PY312_USER!\python.exe"
            "!PY_CMD!" --version >nul 2>&1
        )
        if errorlevel 1 (
            echo [ERREUR] Python installe mais non trouve.
            echo          Fermez TOUTES les fenetres, rouvrez et relancez INSTALL.bat.
            pause & exit /b 1
        )
    ) else (
        set "PY_CMD=python"
    )

    del "!PY_INSTALLER!" >nul 2>&1
    for /f "tokens=2 delims= " %%v in ('"!PY_CMD!" --version 2^>^&1') do set PY_VER=%%v
    echo [OK] Python !PY_VER! installe avec succes
) else (
    set "PY_CMD=python"
    echo [OK] Python !PY_VER!
)

REM === 2. Cree venv ===
set "VENV_PY=%APPDIR%\venv\Scripts\python.exe"
set "VENV_PIP=%APPDIR%\venv\Scripts\pip.exe"

if not exist "%APPDIR%\venv\Scripts\python.exe" (
    if exist "%APPDIR%\venv\" (
        echo [..] venv incomplet detecte - suppression et recreation...
        rmdir /s /q "%APPDIR%\venv"
    ) else (
        echo [..] Creation de l'environnement virtuel...
    )
    !PY_CMD! -m venv "%APPDIR%\venv"
    if errorlevel 1 (
        echo [ERREUR] Echec creation venv.
        pause & exit /b 1
    )
    if not exist "%APPDIR%\venv\Scripts\python.exe" (
        echo [ERREUR] venv cree mais python.exe introuvable.
        pause & exit /b 1
    )
)
echo [OK] Environnement virtuel

REM === 4. Upgrade pip + install deps ===
echo [..] Mise a jour de pip...
"!VENV_PY!" -m pip install --upgrade pip --quiet

echo [..] Installation des dependances...
if exist "%APPDIR%\wheelhouse\" (
    echo      Mode offline - installation depuis le cache local...
    "!VENV_PIP!" install --no-index --find-links "%APPDIR%\wheelhouse" wheel setuptools pip
    "!VENV_PIP!" install --no-index --find-links "%APPDIR%\wheelhouse" -r "%APPDIR%\requirements-lock.txt"
) else if exist "%APPDIR%\requirements-lock.txt" (
    echo      Mode online - telechargement depuis internet...
    "!VENV_PIP!" install -r "%APPDIR%\requirements-lock.txt"
) else (
    "!VENV_PIP!" install -r "%APPDIR%\requirements.txt"
)
if errorlevel 1 (
    echo [ERREUR] Echec installation des dependances.
    pause & exit /b 1
)
echo [OK] Dependances installees

REM === 4.5. Fine-tuning (GPU NVIDIA auto-detect) ===
set "HAS_GPU=0"
wmic path win32_VideoController get name 2>nul | findstr /i "NVIDIA" >nul 2>&1
if not errorlevel 1 set "HAS_GPU=1"

if "!HAS_GPU!"=="1" (
    echo [..] GPU NVIDIA detecte - installation des dependances fine-tuning...
    if exist "%APPDIR%\requirements-finetuning-lock.txt" (
        "!VENV_PIP!" install -r "%APPDIR%\requirements-finetuning-lock.txt"
    ) else (
        "!VENV_PIP!" install -r "%APPDIR%\requirements-finetuning.txt"
    )
    if errorlevel 1 (
        echo [WARN] Echec partiel des dependances fine-tuning - le fine-tuning sera limite.
        echo        Vous pourrez reinstaller depuis l'interface Fine-tuning de Lumena.
    ) else (
        echo [OK] Dependances fine-tuning installees
    )
    REM === 4.5b. llama-cpp-python (optionnel, necessite C++ compiler) ===
    "!VENV_PIP!" install "llama-cpp-python>=0.3.0" >nul 2>&1
    if errorlevel 1 (
        echo [INFO] llama-cpp-python non installe (necessite Visual Studio Build Tools^) - quantization GGUF via CLI uniquement.
    ) else (
        echo [OK] llama-cpp-python installe
    )
) else (
    echo [INFO] Pas de GPU NVIDIA detecte - fine-tuning local non disponible.
    echo        Si vous ajoutez un GPU plus tard, relancez INSTALL.bat ou installez depuis l'interface.
)

REM === 5. Playwright ===
echo [..] Installation du navigateur automatise (Chromium)...
"!VENV_PY!" -m playwright install chromium >nul 2>&1
if errorlevel 1 (
    echo [WARN] Playwright Chromium n'a pas pu etre installe - navigation web limitee
) else (
    echo [OK] Navigateur installe
)

REM === 5b. Tesseract OCR (moteur OCR pour vision + lecture documents) ===
set "TESS_OK=0"
where tesseract >nul 2>&1
if not errorlevel 1 set "TESS_OK=1"
if "!TESS_OK!"=="0" if exist "C:\Program Files\Tesseract-OCR\tesseract.exe" set "TESS_OK=1"
if "!TESS_OK!"=="0" if exist "C:\Program Files (x86)\Tesseract-OCR\tesseract.exe" set "TESS_OK=1"
if "!TESS_OK!"=="1" (
    echo [OK] Tesseract OCR detecte
) else (
    echo [WARN] Tesseract OCR non installe - OCR desactive ^(vision et lecture de documents limitees^)
)

REM === 5c. Stripe CLI ===
where stripe >nul 2>&1
if errorlevel 1 (
    echo [..] Installation de Stripe CLI...
    powershell -Command "winget install --id Stripe.StripeCLI --accept-source-agreements --accept-package-agreements --silent" >nul 2>&1
    where stripe >nul 2>&1
    if errorlevel 1 (
        echo [WARN] Stripe CLI non installee - webhooks locaux desactives
        echo        Installez manuellement : winget install Stripe.StripeCLI
    ) else (
        echo [OK] Stripe CLI installee
    )
) else (
    echo [OK] Stripe CLI detectee
)

REM === 5d. Docker Sandbox (optionnel) ===
where docker >nul 2>&1
if errorlevel 1 goto :docker_absent
docker info >nul 2>&1
if errorlevel 1 goto :docker_stopped
docker image inspect lumena-sandbox >nul 2>&1
if not errorlevel 1 (
    echo [OK] Docker sandbox pret
    goto :docker_done
)
if not exist "Dockerfile.sandbox" goto :docker_done
echo [..] Construction de l'image Docker sandbox (premiere fois, 1-2 min)...
docker build -f Dockerfile.sandbox -t lumena-sandbox . >nul 2>&1
if not errorlevel 1 (
    echo [OK] Image Docker sandbox construite
) else (
    echo [WARN] Build Docker sandbox echoue - execution locale utilisee
)
goto :docker_done
:docker_absent
echo [INFO] Docker non detecte - optionnel, pour sandbox securise
echo        https://docker.com/products/docker-desktop
goto :docker_done
:docker_stopped
echo [INFO] Docker installe mais non demarre - sandbox indisponible
echo        Lancez Docker Desktop puis relancez INSTALL.bat pour activer le sandbox
:docker_done

REM === 6. .env ===
if not exist ".env" (
    if exist ".env.example" (
        copy .env.example .env >nul
        echo [OK] Fichier .env cree depuis .env.example
    ) else (
        echo [INFO] Pas de .env.example - le wizard creera la configuration.
    )
)

REM === 7. Dossiers data/ ===
"!VENV_PY!" -c "from src.utils.paths import validate_instance_dirs; validate_instance_dirs(create=True)" 2>nul
echo [OK] Dossiers initialises

REM === 8. Ollama (optionnel) ===
"!VENV_PY!" -c "import httpx; r=httpx.get('http://localhost:11434/api/tags',timeout=2); exit(0 if r.status_code==200 else 1)" >nul 2>&1
if errorlevel 1 (
    where ollama >nul 2>&1
    if not errorlevel 1 (
        echo [..] Demarrage d'Ollama...
        start "" /B ollama serve >nul 2>&1
        timeout /t 5 /nobreak >nul
    ) else (
        echo [INFO] Ollama non detecte - optionnel, pour modeles IA locaux.
        echo        https://ollama.com/download
    )
) else (
    echo [OK] Ollama detecte
)

echo.
echo  ================================================================
echo      Lancement de Lumena...
echo  ================================================================
echo.

REM === Lire le port depuis .env (defaut 8080) ===
set "LUMENA_PORT=8080"
if exist ".env" (
    for /f "usebackq tokens=1,* delims==" %%a in (".env") do (
        if "%%a"=="LUMENA_PORT" set "LUMENA_PORT=%%b"
    )
)

REM === 9. Lance START_DESKTOP ===
echo [OK] Installation terminee !
echo.
echo  Lancement de Lumena Desktop...
echo  Pour relancer plus tard : double-cliquez START_DESKTOP.bat
echo.
start "" "%APPDIR%\START_DESKTOP.bat"
