#!/usr/bin/env bash
# ================================================================
#     L U M E N A  —  Installation (Linux / macOS)
# ================================================================
set -euo pipefail

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

ok()   { echo -e "${GREEN}[OK]${NC} $1"; }
info() { echo -e "${BLUE}[INFO]${NC} $1"; }
warn() { echo -e "${YELLOW}[WARN]${NC} $1"; }
fail() { echo -e "${RED}[ERREUR]${NC} $1"; exit 1; }

echo ""
echo "  ================================================================"
echo "      L U M E N A  —  Installation"
echo "  ================================================================"
echo ""

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# === 1. Vérifier Python ===
PY_CMD=""
for cmd in python3.12 python3.11 python3.10 python3 python; do
    if command -v "$cmd" &>/dev/null; then
        PY_VER=$("$cmd" --version 2>&1 | awk '{print $2}')
        PY_MAJOR=$(echo "$PY_VER" | cut -d. -f1)
        PY_MINOR=$(echo "$PY_VER" | cut -d. -f2)
        if [ "$PY_MAJOR" -eq 3 ] && [ "$PY_MINOR" -ge 10 ] && [ "$PY_MINOR" -le 12 ]; then
            PY_CMD="$cmd"
            break
        fi
    fi
done

if [ -z "$PY_CMD" ]; then
    echo ""
    fail "Python 3.10-3.12 non trouvé. Installez-le :
         Ubuntu/Debian : sudo apt install python3.12 python3.12-venv
         Fedora        : sudo dnf install python3.12
         macOS         : brew install python@3.12
         Ou : https://www.python.org/downloads/"
fi

PY_VER=$("$PY_CMD" --version 2>&1 | awk '{print $2}')
ok "Python $PY_VER ($PY_CMD)"

# === 2. Créer venv ===
if [ ! -d "venv" ]; then
    echo "[..] Création de l'environnement virtuel..."
    "$PY_CMD" -m venv venv || fail "Échec création venv. Installez python3-venv."
fi
ok "Environnement virtuel"

# === 3. Activer venv ===
source venv/bin/activate

# === 4. Upgrade pip + install deps ===
echo "[..] Mise à jour de pip..."
python -m pip install --upgrade pip --quiet

echo "[..] Installation des dépendances (241 packages, 5-10 min)..."
if [ -f "requirements-lock.txt" ]; then
    pip install -r requirements-lock.txt
else
    pip install -r requirements.txt
fi
ok "Dépendances installées"

# === 4.5. Fine-tuning (GPU NVIDIA auto-detect) ===
if command -v nvidia-smi &>/dev/null; then
    echo "[..] GPU NVIDIA détecté — installation des dépendances fine-tuning..."
    if [ -f "requirements-finetuning-lock.txt" ]; then
        pip install -r requirements-finetuning-lock.txt || warn "Échec partiel fine-tuning"
    else
        pip install -r requirements-finetuning.txt || warn "Échec partiel fine-tuning"
    fi
    ok "Dépendances fine-tuning installées"
else
    info "Pas de GPU NVIDIA détecté — fine-tuning local non disponible."
fi

# === 5. Playwright ===
echo "[..] Installation du navigateur automatisé (Chromium)..."
python -m playwright install chromium 2>/dev/null && ok "Navigateur installé" || warn "Playwright non installé — navigation web limitée"

# === 5b. Stripe CLI ===
if command -v stripe &>/dev/null; then
    ok "Stripe CLI détectée"
else
    info "Installation de Stripe CLI..."
    if command -v brew &>/dev/null; then
        brew install stripe/stripe-cli/stripe 2>/dev/null && ok "Stripe CLI installée (brew)" || warn "Stripe CLI non installée — webhooks locaux désactivés"
    elif command -v apt-get &>/dev/null; then
        curl -s https://packages.stripe.dev/api/security/keypair/stripe-cli-gpg/public | gpg --dearmor 2>/dev/null | sudo tee /usr/share/keyrings/stripe.gpg >/dev/null 2>&1
        echo "deb [signed-by=/usr/share/keyrings/stripe.gpg] https://packages.stripe.dev/stripe-cli-debian-local stable main" | sudo tee /etc/apt/sources.list.d/stripe.list >/dev/null 2>&1
        sudo apt-get update -qq 2>/dev/null && sudo apt-get install -y stripe 2>/dev/null && ok "Stripe CLI installée (apt)" || warn "Stripe CLI non installée — webhooks locaux désactivés"
    else
        warn "Stripe CLI non installée — installez manuellement: https://docs.stripe.com/stripe-cli"
    fi
fi

# === 6. .env ===
if [ ! -f ".env" ]; then
    if [ -f ".env.example" ]; then
        cp .env.example .env
        ok "Fichier .env créé depuis .env.example"
    else
        info "Pas de .env.example — le wizard créera la configuration."
    fi
fi

# === 7. Dossiers data/ ===
python -c "from src.utils.paths import validate_instance_dirs; validate_instance_dirs(create=True)" 2>/dev/null || true
ok "Dossiers initialisés"

# === 8. Ollama (optionnel) ===
if curl -sf --connect-timeout 2 http://localhost:11434/api/tags &>/dev/null; then
    ok "Ollama détecté"
elif command -v ollama &>/dev/null; then
    echo "[..] Démarrage d'Ollama..."
    ollama serve &>/dev/null &
    sleep 3
    if curl -sf --connect-timeout 2 http://localhost:11434/api/tags &>/dev/null; then
        ok "Ollama démarré"
    else
        warn "Ollama n'a pas démarré"
    fi
else
    info "Ollama non détecté — optionnel, pour modèles IA locaux."
    info "https://ollama.com/download"
fi

echo ""
echo "  ================================================================"
echo "      Installation terminée !"
echo "  ================================================================"
echo ""
echo "  Lancer Lumena :"
echo "    ./start.sh              # Serveur web (port 8080)"
echo "    ./start.sh --daemon     # Mode daemon autonome"
echo "    ./start.sh --telegram   # Mode Telegram"
echo ""
