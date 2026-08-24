@echo off
REM ═══════════════════════════════════════════════════════════
REM Lumena v1.0.0 — Script d'installation Windows
REM RTX 3060 12Go VRAM | CUDA 12.1+
REM ═══════════════════════════════════════════════════════════

echo.
echo  ██╗     ██╗   ██╗███╗   ███╗███████╗███╗   ██╗ █████╗
echo  ██║     ██║   ██║████╗ ████║██╔════╝████╗  ██║██╔══██╗
echo  ██║     ██║   ██║██╔████╔██║█████╗  ██╔██╗ ██║███████║
echo  ██║     ██║   ██║██║╚██╔╝██║██╔══╝  ██║╚██╗██║██╔══██║
echo  ███████╗╚██████╔╝██║ ╚═╝ ██║███████╗██║ ╚████║██║  ██║
echo  ╚══════╝ ╚═════╝ ╚═╝     ╚═╝╚══════╝╚═╝  ╚═══╝╚═╝  ╚═╝
echo.
echo  v1.0.0 — Installation Fine-Tuning
echo  ═══════════════════════════════════
echo.

REM Vérifier Python
python --version >nul 2>&1
if errorlevel 1 (
    echo ERREUR : Python non trouvé. Installez Python 3.11+
    pause
    exit /b 1
)

echo [1/5] Vérification Python...
python --version

REM Vérifier CUDA
echo.
echo [2/5] Vérification CUDA...
nvidia-smi >nul 2>&1
if errorlevel 1 (
    echo AVERTISSEMENT : nvidia-smi non trouvé
    echo Assurez-vous que les drivers NVIDIA sont installés
) else (
    nvidia-smi --query-gpu=name,memory.total --format=csv,noheader
)

REM Installer PyTorch CUDA 12.1
echo.
echo [3/5] Installation PyTorch (CUDA 12.1)...
pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu121

REM Installer Unsloth
echo.
echo [4/5] Installation Unsloth...
pip install "unsloth[colab-new] @ git+https://github.com/unslothai/unsloth.git"

REM Installer les autres dépendances
echo.
echo [5/5] Installation des dépendances...
pip install transformers>=4.51.0 datasets peft trl accelerate bitsandbytes
pip install sentencepiece tokenizers huggingface-hub
pip install pandas numpy tqdm pyyaml
pip install rouge-score sacrebleu requests

echo.
echo ═══════════════════════════════════════════════════════════
echo  Installation terminée !
echo.
echo  Pipeline :
echo    1. python 1_prepare_data.py    (générer les données)
echo    2. python 2_train.py           (fine-tuner Qwen3-8B)
echo    3. python 3_export_gguf.py     (exporter vers Ollama)
echo    4. python 4_evaluate.py        (tester le modèle)
echo.
echo  Test rapide (avec Qwen3-8B de base, sans fine-tuning) :
echo    ollama pull qwen3:8b
echo    ollama create lumena-v1 -f Modelfile
echo    ollama run lumena-v1
echo ═══════════════════════════════════════════════════════════
echo.
pause
