# Lumena

**Autonomous AI assistant with persistent memory, ReAct reasoning, and multi-channel support.**
Tourne 24/7 sur Windows, Linux ou macOS. Raisonne, agit, apprend, s'améliore seul.

![Python 3.12](https://img.shields.io/badge/python-3.12-blue)
![Tests](https://img.shields.io/badge/tests-6572%20passed-brightgreen)
![License](https://img.shields.io/badge/license-AGPL--3.0-blue)

---

## Qu'est-ce que Lumena

Lumena est une plateforme d'agent IA qui combine un **raisonnement ReAct** (Think → Act → Observe), **451 outils** intégrés, une **mémoire vectorielle** persistante (ChromaDB), et un **contrôle complet du PC** (clavier, souris, navigateur, apps).

Ce n'est pas un wrapper API. C'est un agent qui planifie, exécute, vérifie, et corrige ses propres erreurs.

### Points forts

| Domaine | Détail |
|---|---|
| **LLM** | 9 providers : Ollama (local), DeepSeek, OpenAI, Anthropic, Google, Moonshot, xAI, NVIDIA, MINIMAX — 46 modèles |
| **Raisonnement** | Boucle ReAct avec plan auto, 8 types de sub-agents (code, research, debug, refactor, browser, planner, file, orchestrator), anti-hallucination |
| **Outils** | 451 handlers V2 dans 33 modules : fichiers, web, mail, git, réseau, navigateur (Playwright stealth v2), terminal, vision, Stripe, n8n, IDE |
| **Documents** | 36 handlers, 13 templates Jinja2 (factures, contrats, devis, NDA, bulletins paie…), export PDF via WeasyPrint |
| **Mémoire** | 4 niveaux : session, ChromaDB vectorielle, Knowledge Graph, BM25 — embedding cache, file watcher |
| **Autonomie** | Scheduler CRON, goals auto-évalués, curiosité, self_improve, cycle quotidien de skills |
| **Computer Use** | Cascade native CU (Anthropic→OpenAI→Google→fallback), pywinauto, vision (Gemini→Claude→Ollama→OCR) |
| **Fine-tuning** | Pipeline local LoRA→GGUF→Ollama automatique, 30 modèles, détection GPU nvidia-smi |
| **Voix** | STT (Whisper) + TTS (Coqui XTTS / Piper / pyttsx3) |
| **Web** | FastAPI + interface Vite, chat temps réel (SSE), WebSocket IDE bridge, 77 endpoints API |
| **Sécurité** | Sandbox Docker (auto/always/never), sanitizer commandes, SSRF guard, rate limiter, path traversal guard |
| **Tests** | 6 572 tests, 0 failed, ~90s sur Windows |

---

## Démarrage rapide

### Prérequis

- **Windows 10/11**, **Linux** ou **macOS**
- **Python 3.10 – 3.12**
- **Docker Desktop** (optionnel — pour le sandbox d'exécution)
- **Ollama** (optionnel — pour les modèles locaux)

### Installation

```bash
git clone https://github.com/Losskarr/lumena.git
cd lumena
```

**Windows :**
```cmd
INSTALL.bat
```

**Linux / macOS :**
```bash
chmod +x install.sh start.sh
./install.sh
```

**Manuelle :**
```bash
python3 -m venv venv
source venv/bin/activate        # Linux/Mac
# venv\Scripts\activate         # Windows

pip install -r requirements.txt
cp .env.example .env
# → Configurer les clés API dans .env
```

### Lancement

**Windows :**
```cmd
START.bat
```

**Linux / macOS :**
```bash
./start.sh                  # Serveur web (port 8080)
./start.sh --daemon         # Mode daemon autonome 24/7
./start.sh --telegram       # Mode Telegram
./start.sh --whatsapp       # Mode WhatsApp
./start.sh --full           # Mode complet (autonomie maximale)
./start.sh --safe           # Mode safe (autonomie limitée)
```

**Commandes directes :**
```bash
# Interface web seule (port 8080)
uvicorn web.server:app

# Tout-en-un (web + bots + daemon)
python lumena_ultime.py

# Mode CLI interactif
python -m src

# Daemon autonome 24/7
python run_daemon.py

# Bot Telegram
python run_telegram.py

# Bot Twitter
python run_twitter.py
```

### One-Click Install (Wizard Web)

Accéder à `http://localhost:8080/setup` pour un assistant guidé qui configure
providers LLM, clés API, Telegram, WhatsApp, Discord, workspace et sandbox Docker.

### Docker

```bash
docker-compose up -d
# → http://localhost:8080
```

---

## Architecture

```
src/
├── core.py                 # LumenaCore — cerveau principal (1 083L)
├── reasoning/
│   ├── react.py            # Boucle ReAct façade (3 321L) + 4 modules extraits
│   ├── react_config.py     # Config, enums, constantes (373L)
│   ├── tool_registry.py    # ToolRegistry complet (1 243L)
│   ├── response_parser.py  # Parsing ReAct pur (292L)
│   ├── prompt_builder.py   # Heuristiques statiques (177L)
│   └── handlers/           # 33 modules, 451 outils V2
│       ├── browser.py      # Playwright stealth v2 (52 handlers)
│       ├── documents.py    # 36 handlers PDF/DOCX (factures, contrats…)
│       ├── ide.py          # 33 handlers IDE bridge
│       ├── stripe_api.py   # 33 handlers Stripe
│       ├── computer_use.py # 28 handlers CU
│       ├── discord_admin.py # 25 handlers Discord
│       └── ...             # + 27 autres modules
├── llm/
│   └── multi_provider.py   # 9 providers LLM, fallback chaîné, retry intra-provider
├── memory/
│   ├── chromadb_store.py   # Mémoire vectorielle persistante (962L)
│   ├── knowledge_graph.py  # Relations entre entités (282L)
│   ├── bm25_index.py       # Recherche textuelle classique (276L)
│   └── embedding_cache.py  # Cache embeddings (269L)
├── autonomy/
│   ├── scheduler.py        # Tâches CRON parallèles (1 430L)
│   ├── daemon.py           # Boucle autonome 24/7 (713L)
│   ├── goals.py            # Objectifs autonomes (391L)
│   ├── curiosity.py        # Exploration thématique (440L)
│   ├── self_improve.py     # Auto-amélioration (923L)
│   └── ops_handlers.py     # 15+ handlers opérationnels (2 444L)
├── computer_use/
│   ├── cu_router.py        # Router multi-provider (196L)
│   ├── native_cu.py        # Cascade native Anthropic→OpenAI→Google (928L)
│   ├── controller.py       # Souris, clavier, fenêtres pywinauto (1 165L)
│   ├── vision.py           # Gemini → Claude → Ollama → OCR (1 270L)
│   └── cu_agent_loop.py    # Boucle screenshot → LLM → action (1 023L)
├── agents/
│   └── sub_agent.py        # 8 types d'agents, 12 actions (3 856L)
├── training/               # Pipeline fine-tuning LoRA → GGUF → Ollama
├── perception/             # Lecture documents, knowledge extraction
├── voice/                  # STT (Whisper) + TTS (Coqui XTTS / Piper)
├── tools/                  # IDE bridge, compaction, code index
├── utils/                  # Docker sandbox, persistence, sanitizer, SSRF guard
└── core_services/          # 12 services fragmentés du core

web/
├── server.py               # FastAPI + Uvicorn (port 8080)
├── routes/                 # 14 fichiers, 77 endpoints API
├── static/                 # 14 fichiers JS + 8 fichiers CSS
└── index.html              # Interface Vite (vanilla JS)

assets/templates/           # 13 templates Jinja2 (documents pro)
models/                     # Modèles TTS Piper + pipeline fine-tuning
tests/                      # 6 200+ tests pytest
```

---

## Configuration

Copier `.env.example` vers `.env`. Variables principales :

| Variable | Description | Défaut |
|---|---|---|
| `LUMENA_DEFAULT_MODEL` | Modèle LLM par défaut | `deepseek-v3` |
| `OLLAMA_HOST` | URL du serveur Ollama | `http://localhost:11434` |
| `OPENAI_API_KEY` | Clé API OpenAI | — |
| `ANTHROPIC_API_KEY` | Clé API Anthropic | — |
| `GOOGLE_API_KEY` | Clé API Google (Gemini) | — |
| `DEEPSEEK_API_KEY` | Clé API DeepSeek | — |
| `TELEGRAM_TOKEN` | Token bot Telegram | — |
| `LUMENA_SANDBOX_MODE` | Mode sandbox : `auto` / `always` / `never` | `auto` |
| `LUMENA_AUTONOMY_EXECUTE_ACTIONS` | Autoriser les actions autonomes | `0` |

Voir `.env.example` pour la liste complète (93 variables documentées, 19 groupes).

---

## Tests

```bash
# Suite complète
python -m pytest tests/ --timeout=15 -q

# Un fichier spécifique
python -m pytest tests/test_react_plan.py -v

# Avec coverage (optionnel)
python -m pytest tests/ --cov=src --cov-report=html
```

Gate CI locale :

```bash
python scripts/ci_phase_gate.py --full --runs=3
```

---

## Docker Sandbox

Lumena exécute le code utilisateur dans un container Docker isolé (`python:3.12-slim`).

| Mode | Comportement |
|---|---|
| `auto` | Commandes Windows → local, code/scripts → Docker |
| `always` | Tout dans Docker (pas de commandes Windows) |
| `never` | Tout en local (comportement classique) |

Configurer via `.env` :

```env
LUMENA_SANDBOX_MODE=auto
LUMENA_SANDBOX_IMAGE=python:3.12-slim
LUMENA_SANDBOX_MEMORY=512m
LUMENA_SANDBOX_CPUS=1
```

---

## Canaux d'interaction

| Canal | Commande | Port | Statut par défaut |
|---|---|---|---|
| **Web** | `python web/server.py` | 8080 | Activé |
| **CLI** | `python -m src` | — | Activé |
| **Telegram** | Configurer `TELEGRAM_TOKEN` dans `.env` | — | Activé si token présent |
| **Discord** | Configurer `DISCORD_TOKEN` dans `.env` | — | Activé si token présent |
| **Twitter/X** | Configurer `TWITTER_*` dans `.env` | — | Activé si tokens présents |
| **WhatsApp** | Credentials Meta Cloud API requis | — | Désactivé (`LUMENA_DISABLE_WHATSAPP=1`) |
| **IDE** | WebSocket bridge (`/ws/ide`) | 8080 | Activé |

---

## Fine-tuning Local

Lumena peut fine-tuner des modèles LLM locaux sur vos propres conversations via Unsloth + TRL.

### Prérequis

- **GPU NVIDIA** avec ≥ 6 Go VRAM (RTX 3060+ recommandé)
- **CUDA 12.1+** et **cuDNN**
- **Ollama** installé et fonctionnel

### Installation des dépendances

```bash
pip install -r requirements-finetuning.txt
```

Ou depuis l'interface web : panneau **Fine-tuning** → **Installer les dépendances**.

### Utilisation

1. Accumuler des conversations dans le pool (automatique via `conversation_logger`)
2. Ouvrir l'interface web → panneau **Fine-tuning**
3. Sélectionner un modèle de base (ex: `qwen3:8b`, `mistral:7b`)
4. Configurer les paramètres (ou garder les défauts)
5. Lancer le fine-tuning → suivre la progression en temps réel
6. Le modèle est automatiquement importé dans Ollama et utilisable

### Modèles supportés

| VRAM | Modèles recommandés |
|------|---------------------|
| 6 Go | Qwen3 4B, Gemma3 4B |
| 8 Go | Mistral 7B, DeepSeek-R1 7B |
| 10 Go | Qwen3 8B, LLaMA 3.3 8B |
| 24 Go | Gemma3 27B |

---

## Licence

GNU Affero General Public License v3.0 — Copyright (c) 2025-2026 LossKarr. Voir [LICENSE](LICENSE).
