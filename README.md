# Lumena

**Autonomous AI assistant with persistent memory, ReAct reasoning, and multi-channel support.**
Tourne 24/7 sur Windows, Linux ou macOS. Raisonne, agit, apprend, s'améliore seul.

![Python 3.12](https://img.shields.io/badge/python-3.12-blue)
![Tests](https://img.shields.io/badge/tests-7536%20passed-brightgreen)
![License](https://img.shields.io/badge/license-AGPL--3.0-blue)

---

## Qu'est-ce que Lumena

Lumena est une plateforme d'agent IA qui combine un **raisonnement ReAct** (Think → Act → Observe), **511 outils** intégrés répartis en 18 packs contextuels, une **mémoire vectorielle** persistante (ChromaDB), et un **contrôle complet du PC** (clavier, souris, navigateur, apps).

Ce n'est pas un wrapper API. C'est un agent qui planifie, exécute, vérifie, et corrige ses propres erreurs.

### Points forts

| Domaine | Détail |
|---|---|
| **LLM** | 10 providers : Ollama (local), DeepSeek, OpenAI, Anthropic, Google, Moonshot, xAI, NVIDIA, MiniMax, Z.AI — fallback chaîné automatique |
| **Raisonnement** | Boucle ReAct avec 18 packs d'outils contextuels, 8 types de sub-agents (code, research, debug, refactor, browser, planner, file, orchestrator), CodeAgent en arrière-plan avec progression temps réel |
| **Outils** | 511 handlers V2 dans 18 packs : fichiers, web, mail, git, réseau, navigateur (Playwright stealth v2), terminal, vision, images, Stripe, n8n, IDE, computer use |
| **Documents** | 36 handlers, 13 templates Jinja2 (factures, contrats, devis, NDA, bulletins paie…), export PDF via WeasyPrint |
| **Images** | 12 providers (Gemini, OpenAI, Flux, Stability, Imagen, Ideogram, Recraft, Replicate, HuggingFace, xAI, MiniMax, Z.AI), 39 modèles, 13 handlers (generate, edit, compose, thumbnail, upscale, logo, SVG, remove/replace background, sketch-to-image) |
| **Mémoire** | 4 niveaux : session, ChromaDB vectorielle, Knowledge Graph, BM25 — embedding cache, file watcher |
| **Autonomie** | Scheduler CRON, goals auto-évalués, curiosité, self_improve, cycle quotidien de skills |
| **Computer Use** | Cascade native CU (Anthropic→OpenAI→Google→fallback), pywinauto, vision (Gemini→Claude→Ollama→OCR) |
| **Fine-tuning** | Pipeline local LoRA→GGUF→Ollama automatique, 30 modèles, détection GPU nvidia-smi |
| **Voix** | STT (faster-whisper GPU) + TTS (Coqui XTTS / Piper / pyttsx3) |
| **Web** | FastAPI + interface admin complète, chat temps réel (SSE), WebSocket IDE bridge, panel workspaces CodeAgent |
| **Sécurité** | Sandbox Docker (auto/always/never), sanitizer commandes, SSRF guard, rate limiter, path traversal guard |
| **Tests** | 7 536 tests, 0 failed, ~90s sur Windows |

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
├── core.py                 # LumenaCore — cerveau principal (1 088L)
├── reasoning/
│   ├── react.py            # Boucle ReAct V4 (3 511L)
│   ├── react_config.py     # Config, enums, constantes (373L)
│   ├── tool_registry.py    # ToolRegistry — 18 packs contextuels (1 583L)
│   ├── intent_router.py    # Router LLM-first + fallback regex (533L)
│   ├── response_parser.py  # Parsing ReAct pur (292L)
│   ├── prompt_builder.py   # Heuristiques statiques (177L)
│   ├── caller_context.py   # Contexte appelant (injection sous-agents)
│   ├── file_categories.py  # Catégorisation fichiers projet
│   └── handlers/           # 18 packs, 511 outils V2
│       ├── browser.py      # Playwright stealth v2 (54 handlers)
│       ├── documents.py    # 36 handlers PDF/DOCX (factures, contrats…)
│       ├── image_gen.py    # 13 handlers génération d'images (12 providers, 39 modèles)
│       ├── ide.py          # 46 handlers IDE bridge
│       ├── stripe_api.py   # 37 handlers Stripe
│       ├── computer_use.py # 25 handlers CU
│       ├── discord_admin.py # 25 handlers Discord
│       └── ...             # + autres modules
├── llm/
│   └── multi_provider.py   # 10 providers LLM, fallback chaîné, retry intra-provider (3 268L)
├── memory/
│   ├── chromadb_store.py   # Mémoire vectorielle persistante
│   ├── knowledge_graph.py  # Relations entre entités
│   ├── bm25_index.py       # Recherche textuelle classique
│   └── embedding_cache.py  # Cache embeddings
├── autonomy/
│   ├── scheduler.py        # Tâches CRON parallèles
│   ├── daemon.py           # Boucle autonome 24/7
│   ├── goals.py            # Objectifs autonomes
│   ├── curiosity.py        # Exploration thématique
│   ├── self_improve.py     # Auto-amélioration
│   └── ops_handlers.py     # 15+ handlers opérationnels
├── computer_use/
│   ├── cu_router.py        # Router multi-provider
│   ├── native_cu.py        # Cascade native Anthropic→OpenAI→Google
│   ├── controller.py       # Souris, clavier, fenêtres pywinauto
│   ├── vision.py           # Gemini → Claude → Ollama → OCR
│   └── cu_agent_loop.py    # Boucle screenshot → LLM → action
├── agents/
│   └── sub_agent.py        # 8 types d'agents, CodeAgent bg + progression temps réel (5 973L)
├── services/
│   └── image_gen.py        # 12 providers image, 39 modèles, fallback auto
├── training/               # Pipeline fine-tuning LoRA → GGUF → Ollama
├── perception/             # Lecture documents, knowledge extraction
├── voice/                  # STT (faster-whisper GPU) + TTS (Coqui XTTS / Piper)
├── tools/                  # IDE bridge, compaction, code index
├── utils/                  # Docker sandbox, persistence, sanitizer, SSRF guard
└── core_services/          # Services fragmentés du core

web/
├── server.py               # FastAPI + Uvicorn (port 8080)
├── routes/                 # 15 fichiers routes API (workspaces, product_docs…)
├── static/                 # JS + CSS modulaires (main.js, workspaces.js…)
└── index.html              # Interface admin complète (vanilla JS ES modules)

assets/templates/           # 13 templates Jinja2 (documents pro)
models/                     # Modèles TTS Piper + pipeline fine-tuning
tests/                      # 7 536 tests pytest
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
| `ZAI_API_KEY` | Clé API Z.AI | — |
| `TELEGRAM_TOKEN` | Token bot Telegram | — |
| `LUMENA_SANDBOX_MODE` | Mode sandbox : `auto` / `always` / `never` | `auto` |
| `LUMENA_AUTONOMY_EXECUTE_ACTIONS` | Autoriser les actions autonomes | `0` |

Voir `.env.example` pour la liste complète (149 variables documentées, 23 groupes).

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

GNU General Public License v3.0 — Copyright (c) 2025-2026 LossKarr. Voir [LICENSE](LICENSE).
