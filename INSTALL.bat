@echo off

REM === Relance dans une fenetre persistante si double-clic ===
if "%~1"=="" (
    cmd /k ""%~f0" _RUNNING"
    exit /b
)

chcp 65001 >nul 2>&1
setlocal EnableExtensions EnableDelayedExpansion
cd /d "%~dp0"

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
if not exist "venv\" (
    echo [..] Creation de l'environnement virtuel...
    !PY_CMD! -m venv venv
    if errorlevel 1 (
        echo [ERREUR] Echec creation venv.
        pause & exit /b 1
    )
)
echo [OK] Environnement virtuel

REM === 3. Active venv ===
call venv\Scripts\activate.bat

REM === 4. Upgrade pip + install deps ===
echo [..] Mise a jour de pip...
python -m pip install --upgrade pip --quiet

echo [..] Installation des dependances (241 packages, 5-10 min)...
REM requirements-lock.txt = versions pinnees (pas de derive sur install existante)
if exist "requirements-lock.txt" (
    pip install -r requirements-lock.txt
) else (
    pip install -r requirements.txt
)
if errorlevel 1 (
    echo [ERREUR] Echec installation des dependances.
    pause & exit /b 1
)
echo [OK] Dependances installees

REM === 4.5. Fine-tuning (GPU NVIDIA auto-detect) ===
set "HAS_GPU=0"
nvidia-smi >nul 2>&1
if not errorlevel 1 set "HAS_GPU=1"

if "!HAS_GPU!"=="1" (
    echo [..] GPU NVIDIA detecte - installation des dependances fine-tuning...
    if exist "requirements-finetuning-lock.txt" (
        pip install -r requirements-finetuning-lock.txt
    ) else (
        pip install -r requirements-finetuning.txt
    )
    if errorlevel 1 (
        echo [WARN] Echec partiel des dependances fine-tuning - le fine-tuning sera limite.
        echo        Vous pourrez reinstaller depuis l'interface Fine-tuning de Lumena.
    ) else (
        echo [OK] Dependances fine-tuning installees
    )
    REM === 4.5b. llama-cpp-python (optionnel, necessite C++ compiler) ===
    pip install "llama-cpp-python>=0.3.0" >nul 2>&1
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
python -m playwright install chromium >nul 2>&1
if errorlevel 1 (
    echo [WARN] Playwright Chromium n'a pas pu etre installe - navigation web limitee
) else (
    echo [OK] Navigateur installe
)

REM === 5b. Docker Sandbox (optionnel) ===
where docker >nul 2>&1
if not errorlevel 1 (
    docker info >nul 2>&1
    if not errorlevel 1 (
        docker image inspect lumena-sandbox >nul 2>&1
        if errorlevel 1 (
            if exist "Dockerfile.sandbox" (
                echo [..] Construction de l'image Docker sandbox (premiere fois, 1-2 min)...
                docker build -f Dockerfile.sandbox -t lumena-sandbox . >nul 2>&1
                if not errorlevel 1 (
                    echo [OK] Image Docker sandbox construite
                ) else (
                    echo [WARN] Build Docker sandbox echoue - execution locale utilisee
                )
            )
        ) else (
            echo [OK] Docker sandbox pret
        )
    ) else (
        echo [INFO] Docker installe mais non demarre - sandbox indisponible
        echo        Lancez Docker Desktop puis relancez INSTALL.bat pour activer le sandbox
    )
) else (
    echo [INFO] Docker non detecte - optionnel, pour sandbox securise
    echo        https://docker.com/products/docker-desktop
)

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
python -c "from src.utils.paths import validate_instance_dirs; validate_instance_dirs(create=True)" 2>nul
echo [OK] Dossiers initialises

REM === 8. Ollama (optionnel) ===
python -c "import httpx; r=httpx.get('http://localhost:11434/api/tags',timeout=2); exit(0 if r.status_code==200 else 1)" >nul 2>&1
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

REM === 9. Lance le serveur, attend qu'il soit pret, puis ouvre le navigateur ===
echo [OK] Installation terminee !
echo.
echo  Le serveur demarre en arriere-plan. Le navigateur s'ouvrira quand il sera pret.
echo.
echo  Cette fenetre affiche les logs du serveur - ne la fermez pas.
echo  Pour relancer plus tard : double-cliquez START.bat
echo.
start /min "Lumena Backend" venv\Scripts\python.exe web/server.py

REM === Attente serveur pret (max 30s) ===
echo [..] Demarrage du serveur web...
set SRV_OK=0
for /L %%i in (1,1,30) do (
    if "!SRV_OK!"=="0" (
        venv\Scripts\python.exe -c "import httpx; httpx.get('http://127.0.0.1:!LUMENA_PORT!/api/health',timeout=1)" >nul 2>&1
        if not errorlevel 1 set SRV_OK=1
        if "!SRV_OK!"=="0" timeout /t 1 /nobreak >nul
    )
)
if "!SRV_OK!"=="1" (
    echo [OK] Lumena operationnel sur http://localhost:!LUMENA_PORT!
) else (
    echo [WARN] Timeout d'attente - ouverture navigateur quand meme
)
start http://localhost:!LUMENA_PORT!
echo.
echo  Appuyez sur une touche pour fermer Lumena...
pause >nul
taskkill /fi "WINDOWTITLE eq Lumena Backend" /f >nul 2>&1

echo.
echo  --- Serveur arrete. ---
pause
