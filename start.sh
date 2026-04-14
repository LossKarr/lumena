#!/usr/bin/env bash
# ================================================================
#     L U M E N A  —  Lancement (Linux / macOS)
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

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

echo ""
echo "  ================================================================"
echo "      L U M E N A"
echo "  ================================================================"
echo ""

# === Vérifier venv ===
if [ ! -d "venv" ]; then
    fail "venv/ introuvable. Lancez d'abord : ./install.sh"
fi
source venv/bin/activate

# === Charger .env ===
if [ -f ".env" ]; then
    set -a
    source .env
    set +a
fi

# === Port (défaut 8080) ===
LUMENA_PORT="${LUMENA_PORT:-8080}"

# === Ollama (optionnel) ===
if ! curl -sf --connect-timeout 2 http://localhost:11434/api/tags &>/dev/null; then
    if command -v ollama &>/dev/null; then
        echo "[..] Démarrage d'Ollama..."
        ollama serve &>/dev/null &
        sleep 3
        if curl -sf --connect-timeout 2 http://localhost:11434/api/tags &>/dev/null; then
            ok "Ollama opérationnel"
        else
            warn "Ollama n'a pas démarré — modèles locaux indisponibles"
        fi
    else
        info "Ollama non détecté — optionnel"
    fi
else
    ok "Ollama opérationnel"
fi

# === Port déjà occupé ? ===
if curl -sf --connect-timeout 2 "http://127.0.0.1:${LUMENA_PORT}/api/health" &>/dev/null; then
    echo ""
    ok "Lumena tourne déjà sur le port $LUMENA_PORT"
    info "Ouverture : http://localhost:$LUMENA_PORT"
    if command -v xdg-open &>/dev/null; then
        xdg-open "http://localhost:$LUMENA_PORT" 2>/dev/null &
    elif command -v open &>/dev/null; then
        open "http://localhost:$LUMENA_PORT" 2>/dev/null &
    fi
    exit 0
fi

# === Mode d'exécution ===
MODE="${1:-web}"

case "$MODE" in
    --daemon|-d)
        info "Mode daemon autonome"
        python run_daemon.py
        ;;
    --telegram|-t)
        info "Mode Telegram"
        python run_telegram.py
        ;;
    --twitter|-x)
        info "Mode Twitter/X"
        python run_twitter.py
        ;;
    --full|-f)
        export LUMENA_AUTONOMY=always
        export LUMENA_LEARNING_ENABLED=true
        export LUMENA_PROACTIVE=true
        info "Mode complet (autonomie maximale)"
        python -m uvicorn web.server:app --host 0.0.0.0 --port "$LUMENA_PORT" &
        SERVER_PID=$!
        sleep 3
        if curl -sf --connect-timeout 5 "http://127.0.0.1:${LUMENA_PORT}/api/health" &>/dev/null; then
            ok "Lumena opérationnel — http://localhost:$LUMENA_PORT"
            if command -v xdg-open &>/dev/null; then
                xdg-open "http://localhost:$LUMENA_PORT" 2>/dev/null &
            elif command -v open &>/dev/null; then
                open "http://localhost:$LUMENA_PORT" 2>/dev/null &
            fi
            wait $SERVER_PID
        else
            warn "Le serveur n'a pas répondu dans les temps"
            wait $SERVER_PID
        fi
        ;;
    --safe|-s)
        export LUMENA_AUTONOMY=ask
        export LUMENA_LEARNING_ENABLED=false
        export LUMENA_PROACTIVE=false
        info "Mode safe (autonomie limitée)"
        python -m uvicorn web.server:app --host 0.0.0.0 --port "$LUMENA_PORT" &
        SERVER_PID=$!
        sleep 3
        if curl -sf --connect-timeout 5 "http://127.0.0.1:${LUMENA_PORT}/api/health" &>/dev/null; then
            ok "Lumena opérationnel — http://localhost:$LUMENA_PORT"
            if command -v xdg-open &>/dev/null; then
                xdg-open "http://localhost:$LUMENA_PORT" 2>/dev/null &
            elif command -v open &>/dev/null; then
                open "http://localhost:$LUMENA_PORT" 2>/dev/null &
            fi
            wait $SERVER_PID
        else
            warn "Le serveur n'a pas répondu dans les temps"
            wait $SERVER_PID
        fi
        ;;
    *)
        # Mode web par défaut
        python -m uvicorn web.server:app --host 0.0.0.0 --port "$LUMENA_PORT" &
        SERVER_PID=$!
        sleep 3
        if curl -sf --connect-timeout 5 "http://127.0.0.1:${LUMENA_PORT}/api/health" &>/dev/null; then
            ok "Lumena opérationnel — http://localhost:$LUMENA_PORT"
            if command -v xdg-open &>/dev/null; then
                xdg-open "http://localhost:$LUMENA_PORT" 2>/dev/null &
            elif command -v open &>/dev/null; then
                open "http://localhost:$LUMENA_PORT" 2>/dev/null &
            fi
            wait $SERVER_PID
        else
            warn "Le serveur n'a pas répondu dans les temps"
            wait $SERVER_PID
        fi
        ;;
esac
