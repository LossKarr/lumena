# Changelog

Toutes les modifications notables de Lumena sont documentées ici.

Le format suit [Keep a Changelog](https://keepachangelog.com/fr/1.1.0/)
et ce projet adhère au [Semantic Versioning](https://semver.org/lang/fr/).

---

## [1.0.9] — 2026-04-22

### Ajouts
- **Z.AI** — 10ème provider LLM intégré (GLM-4 series), position 2 dans la chaîne de fallback
- **Router v2 — 18 packs contextuels** — `_CONTEXT_RULES` réécrit : BROWSER et SEARCH séparés,
  pack CODE avec `delegate_task`/`delegate_task_bg`, pack VIDEO isolé
- **`delegate_task_bg`** — CodeAgent en arrière-plan avec `task_id` immédiat + progression temps réel
- **`run_task_bg`** dans l'Orchestrator + `progress_callback` pour push vers le chat
- **Panel Workspaces** — interface admin complète : groupement par date, badges tech stack,
  arbre de fichiers lazy, recherche, tri
- **`intent_router.py`** — classifieur LLM-first + fallback regex + cache TTL + audit JSONL
- **`reliability_metrics.py`** — métriques de routage temps réel

### Corrections
- `nvidia-cublas-cu12` / `nvidia-cudnn-cu12` ajoutés aux requirements — résout `cublas64_12.dll not found` pour faster-whisper GPU
- `test_get_next_healthy_provider` mis à jour pour refléter Z.AI en position 2 du fallback
- Tests `test_image_gen_compose` : patchaient `GEMINI_API_KEY` au lieu de `GOOGLE_API_KEY`
- Model picker : `flex-wrap: wrap` remplace `overflow-x: auto` (tous les providers visibles)

### Changements
- Licence confirmée **AGPL-3.0** sur ~200 fichiers source (classifiers pyproject mis à jour)
- `.env.example` : 149 variables documentées (23 groupes), ajout variables Z.AI
- `pytest.ini` : marker `timeout` enregistré, suppression faux `PytestUnknownMarkWarning`

---

## [1.0.0] — 2026-04-16

Première release publique. Assistant IA autonome complet avec raisonnement
ReAct, mémoire persistante et support multi-canal.

### Fonctionnalités principales

#### Raisonnement & Agents
- **ReAct loop** — boucle de raisonnement itérative (max 35 itérations, timeout 900s)
  avec détection de boucle, hallucination guards et compaction automatique
- **466 outils natifs** répartis sur 35 modules de handlers V2
  (browser, documents, IDE, mail, GitHub, Stripe, Discord, sécurité, etc.)
- **CodeAgent** — agent de code itératif (max 30 itérations) avec auto-lint ruff,
  auto-syntax check, détection de boucle, retry externe et compaction mid-loop
- **Forking Agent** — 4 forks parallèles avec synthèse Socratique
- **Pipeline Router** — routage intelligent (chat / code / skill / react)

#### LLM & Providers
- **9 providers LLM** : DeepSeek, OpenAI, Anthropic, Google, Moonshot, xAI,
  NVIDIA NIM, MiniMax, Ollama (local)
- **46 modèles pré-configurés** avec rotation, failover automatique et retry
  intra-provider (backoff 1s/3s pour 429/500/502/503)
- **Context window overflow guard** — compaction automatique au-delà de 85%

#### Mémoire
- **ChromaDB** — recherche vectorielle sémantique persistante
- **BM25** — recherche par mots-clés avec Reciprocal Rank Fusion (hybride)
- **Knowledge Graph** — GraphRAG léger avec triples sujet-relation-objet (JSON)
- **Cache d'embeddings SQLite** — évite le recalcul
- **Session memory** — contexte de conversation avec decay temporel
- **File watcher** — re-indexation automatique quand les fichiers mémoire changent

#### Canaux de communication
- **7 canaux** : CLI, Web UI, Discord, Telegram, Twitter/X, WhatsApp, API REST
- Architecture multi-canal avec enveloppes unifiées (`ChannelEnvelope`)

#### Autonomie
- **Scheduler CRON** — planification de tâches récurrentes avec idempotence
- **Heartbeat** — surveillance périodique des tâches (HEARTBEAT.md)
- **Daemon mode** — exécution 24/7 avec 8 guards de sécurité (opt-in)
- **Goals** — système de buts autonomes
- **Curiosité** — apprentissage proactif

#### Computer Use
- **UIAutomation** — contrôle desktop complet via pywinauto
- **Vision multi-provider** — cascade Gemini → Claude → Ollama → OCR (pytesseract)
- **CU Router** — routing cloud / hybrid / local
- **DOM Indexer** + State Policy (DOM/UIA/OCR)

#### Voice
- **TTS** — edge-tts (principal) → Piper → XTTS → pyttsx3 (fallback)
- **STT** — faster-whisper (transcription locale)
- **Boucle vocale interactive** — assistant_loop.py

#### Web UI
- **FastAPI** avec 20 modules de routes
- **Vite** build frontend
- **Setup wizard** interactif pour la configuration initiale
- **Dashboard** — outils, mémoire, émotions, instincts, configuration

#### Skills
- **29 skills** programmables (PDF, DOCX, PPTX, XLSX, website-generator,
  canvas-design, mcp-builder, self-awareness, etc.)
- Scripts : `init_skill.py`, `validate_skill.py`, `package_skill.py`

#### Documents
- **Génération** : PDF, DOCX, PPTX, XLSX, factures, devis, contrats
- **13 templates Jinja2** (facture, devis, contrat, etc.)

#### Sécurité
- **SSRF guard** — `assert_url_safe()` dans Playwright
- **Rate limiting** — middleware ASGI token-bucket/IP, 3 catégories, 429+Retry-After
- **Anti-injection de prompt** — 20 patterns de détection + normalisation homographes Unicode
- **Workspace file guardrails** — résolution de chemins sécurisée
- **Command sanitizer** — nettoyage des commandes shell
- **Autorisation renforcée** — 7 outils offensifs protégés par validation structurée

#### Infrastructure
- **Docker** — multi-stage (Node.js build + Python 3.12-slim), utilisateur non-root
- **Docker sandbox** — image isolée pour exécution de code
- **Persistence atomique** — `atomic_write_json` / `safe_read_json` avec quarantaine
- **Paths centralisés** — 25 répertoires + 12 fichiers constants
- **Fine-tuning local** — pipeline Unsloth + TRL, GGUF export, import Ollama
- **Déploiement IONOS** — SFTP automatisé pour sites web

#### Tests & CI
- **213 fichiers de tests**, 6400+ tests (pytest + pytest-asyncio)
- **CI GitHub Actions** — lint ruff, tests Python 3.12, build Docker
- **Phase gate** — ci_phase_gate.py avec runs multiples de stabilité

### Dépendances clés
- Python ≥3.10, <3.13 (cible 3.12)
- FastAPI, Uvicorn, ChromaDB, Playwright, edge-tts, faster-whisper
- httpx, loguru, rich, rank-bm25, tiktoken, ruff

---

_Pour les versions futures, chaque release aura sa propre section ci-dessus._
