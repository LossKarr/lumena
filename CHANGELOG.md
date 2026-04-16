# Changelog

Toutes les modifications notables de Lumena sont documentées ici.

Le format suit [Keep a Changelog](https://keepachangelog.com/fr/1.1.0/)
et ce projet adhère au [Semantic Versioning](https://semver.org/lang/fr/).

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
