# Contribuer à Lumena

Merci de vouloir contribuer à Lumena ! Ce guide explique comment démarrer,
les conventions à respecter, et le processus de soumission.

---

## Table des matières

- [Prérequis](#prérequis)
- [Installation développeur](#installation-développeur)
- [Lancer les tests](#lancer-les-tests)
- [Lint & formatage](#lint--formatage)
- [Structure du projet](#structure-du-projet)
- [Créer un skill](#créer-un-skill)
- [Ajouter un outil (handler V2)](#ajouter-un-outil-handler-v2)
- [Conventions de code](#conventions-de-code)
- [CI / Pipeline](#ci--pipeline)
- [Soumettre une Pull Request](#soumettre-une-pull-request)

---

## Prérequis

- **Python 3.10, 3.11 ou 3.12** (3.12 recommandé)
- **Git**
- (Optionnel) **Docker** pour les builds conteneurisés
- (Optionnel) **nmap**, **Playwright** pour certains outils spécifiques

## Installation développeur

```bash
# Cloner le repo
git clone https://github.com/Losskarr/lumena.git
cd lumena

# Créer un environnement virtuel
python -m venv venv

# Activer (Windows)
venv\Scripts\activate

# Activer (Linux / macOS)
source venv/bin/activate

# Installer les dépendances
pip install -r requirements.txt

# Vérifier que tout fonctionne
python -m pytest tests/ --timeout=15 -q
```

### Variables d'environnement

Copier `.env.example` en `.env` et renseigner au minimum :

```bash
LUMENA_DEEPSEEK_API_KEY=...    # Clé DeepSeek (provider par défaut)
LUMENA_ADMIN_TOKEN=...          # Token admin pour l'API web
```

Le fichier `.env.example` documente les 149 variables disponibles (23 groupes).

## Lancer les tests

```bash
# Suite complète (~7536 tests)
python -m pytest tests/ -q

# Subset critique uniquement (CI gate, ~16 tests)
python scripts/ci_phase_gate.py

# Run de stabilité ×3 (preuve de non-flakiness)
python scripts/ci_phase_gate.py --full --runs=3

# Un fichier spécifique
python -m pytest tests/test_core.py -v

# Tests marqués slow exclus
python -m pytest tests/ -m "not slow"
```

**Configuration pytest** (`pytest.ini`) :
- `asyncio_mode = strict` — `@pytest.mark.asyncio` requis explicitement
- Répertoires exclus : `venv`, `Backup`, `workspace`, `web`, `__pycache__`

## Lint & formatage

```bash
# Erreurs bloquantes (identique à la CI)
ruff check src/ web/ tests/ --select F821,F811,E722 --ignore E501,E402

# Toutes les règles E/F/W
ruff check src/ web/ tests/ --select E,F,W --ignore E501,E402

# Vérifier le formatage
ruff format --check src/ web/ tests/ --diff

# Appliquer le formatage automatiquement
ruff format src/ web/ tests/
```

**Configuration ruff** (`pyproject.toml`) :
- `target-version = "py312"`
- `line-length = 120`
- Règles actives : `E`, `F`, `W`

## Structure du projet

```
lumena/
├── src/                    # Code source principal
│   ├── core.py             # Noyau (LumenaCore)
│   ├── cli.py              # Interface ligne de commande
│   ├── emotion.py          # Système émotionnel PAD
│   ├── personality.py      # Personnalité et traits
│   ├── agents/             # Sub-agents (CodeAgent, forking, session)
│   ├── autonomy/           # Daemon, scheduler CRON, heartbeat, goals
│   ├── channels/           # CLI, Discord, Telegram, Twitter, WhatsApp
│   ├── computer_use/       # Automatisation desktop (vision, controller)
│   ├── context/            # AST parser, code index, repo map
│   ├── core_services/      # 12 services (agent, identity, memory, voice…)
│   ├── hooks/              # Système de hooks (WebSocket brain 3D)
│   ├── learning/           # Reflection, instincts
│   ├── llm/                # Multi-provider LLM (10 providers, fallback chaîné)
│   ├── memory/             # ChromaDB + BM25 + Knowledge Graph
│   ├── perception/         # Lecture de documents, extraction de savoir
│   ├── prompts/            # Construction de prompts
│   ├── reasoning/          # ReAct loop + 511 handlers V2 — 18 packs contextuels
│   │   └── handlers/       # modules de handlers (1 fichier = 1 domaine)
│   ├── runtime/            # Enveloppes canal, orchestrateur de tâches
│   ├── services/           # IONOS, n8n, Stripe
│   ├── skills/             # Chargement et gestion des skills
│   ├── telemetry/          # Trace bus, suivi des éditions
│   ├── tools/              # Browser, mail hub, patching, compaction…
│   ├── training/           # Fine-tuning local (Unsloth + TRL)
│   ├── utils/              # Persistence atomique, paths, sécurité
│   └── voice/              # TTS (edge-tts/Piper/XTTS) + STT (whisper)
├── web/                    # Web UI FastAPI + frontend Vite
├── skills/                 # 29 skills installés
├── tests/                  # 213 fichiers de tests
├── scripts/                # Outils de développement
├── assets/templates/       # Templates Jinja2 (factures, devis…)
├── models/                 # Modèles locaux (LoRA, Piper, XTTS)
└── data/                   # Données persistantes (mémoire, état)
```

## Créer un skill

Un **skill** est un module de connaissances que Lumena charge dynamiquement.

```bash
# Créer le squelette
python scripts/init_skill.py mon-nouveau-skill

# Avec des dossiers de ressources
python scripts/init_skill.py mon-skill --resources scripts,references,assets
```

Cela crée `skills/mon-nouveau-skill/SKILL.md` avec le frontmatter YAML requis.

### Structure d'un skill

```
skills/mon-skill/
├── SKILL.md          # Obligatoire — frontmatter YAML + instructions markdown
├── scripts/          # Optionnel — scripts exécutables
├── references/       # Optionnel — documents de référence
└── assets/           # Optionnel — images, fichiers statiques
```

### Frontmatter SKILL.md

```yaml
---
name: mon-skill
description: Description concise du skill
---

Instructions markdown que Lumena suivra quand ce skill est activé.
```

**Règles** :
- Le nom doit être en lowercase avec tirets (`^[a-z0-9-]+$`)
- Le nom du dossier doit correspondre au champ `name` du frontmatter
- Le fichier `SKILL.md` est obligatoire

```bash
# Valider un skill
python scripts/validate_skill.py skills/mon-skill
```

## Ajouter un outil (handler V2)

Tous les outils de Lumena sont des handlers V2 dans `src/reasoning/handlers/`.

### Anatomie d'un handler

```python
# src/reasoning/handlers/mon_domaine.py

from __future__ import annotations
from .context import HandlerContext
from .contracts import HandlerResult
from .registry_v2 import HandlerDef


async def mon_outil_handler(ctx: HandlerContext, param1: str, param2: int = 10) -> HandlerResult:
    """Description de ce que fait l'outil."""
    try:
        # Logique métier
        result = f"Traité {param1} avec valeur {param2}"
        return HandlerResult.ok(result, handler_name="mon_outil")
    except Exception as e:
        return HandlerResult.fail(f"❌ Erreur: {e}", handler_name="mon_outil")


def get_mon_domaine_handler_defs() -> list[HandlerDef]:
    return [
        HandlerDef(
            name="mon_outil",
            handler=mon_outil_handler,
            category="mon_domaine",
            description="Description affichée au LLM",
            parameters={
                "param1": {"type": "string", "description": "Premier paramètre"},
                "param2": {"type": "integer", "description": "Deuxième paramètre (défaut: 10)"},
            },
            required=["param1"],
        ),
    ]
```

### Enregistrement

Ajouter le module dans `src/reasoning/tool_registry.py`, tableau `_HANDLER_MODULES` :

```python
(".handlers.mon_domaine", "get_mon_domaine_handler_defs", "mon_domaine"),
```

### Bonnes pratiques pour les handlers
- Toujours retourner `HandlerResult.ok()` ou `HandlerResult.fail()`
- Utiliser `ctx.lumena_root` pour les chemins relatifs au projet
- Utiliser `ctx.runtime_root` pour le workspace courant
- Logger avec `loguru` (`from loguru import logger`)
- Tester dans `tests/test_handlers_mon_domaine.py`

## Conventions de code

### Langue
- **Commentaires et docstrings** : français
- **Noms de variables, fonctions, classes** : anglais
- **Messages d'erreur retournés à l'utilisateur** : français

### Style
- Ligne max : **120 caractères**
- Logging : **loguru** (pas `logging` stdlib)
- Type hints recommandés
- Persistence : `atomic_write_json()` / `safe_read_json()` (jamais de `json.dump` direct)
- Async : utiliser `async/await` partout, éviter `asyncio.get_event_loop().run_until_complete()`

### Tests
- Fichier : `tests/test_<module>.py`
- Classes : `Test<Feature>`
- Fonctions : `test_<comportement>`
- Pas besoin de `@pytest.mark.asyncio` (mode auto)
- Mocker les appels réseau (httpx, API keys)

## CI / Pipeline

Deux workflows GitHub Actions se déclenchent sur push/PR vers `main` :

### ci.yml
| Job | OS | Description |
|-----|----|-------------|
| `test` | Windows | `pytest tests/ --timeout=15` |
| `lint` | Ubuntu | ruff F821/F811/E722 (bloquant) + E/F/W (advisory) |
| `docker` | Ubuntu | `docker build --target runtime` |

### phase-gate.yml
| Job | OS | Description |
|-----|----|-------------|
| `lint` | Ubuntu | ruff bloquant + format check advisory |
| `gate-critical` | Ubuntu | `ci_phase_gate.py --timeout=15` (~16 tests critiques) |
| `full-windows` | Windows | `ci_phase_gate.py --full --runs=3` (suite ×3) |

**Tous les checks doivent passer avant merge.**

## Soumettre une Pull Request

1. **Fork** le repo et créer une branche descriptive :
   ```bash
   git checkout -b feat/mon-amelioration
   ```

2. **Implémenter** les changements en suivant les conventions ci-dessus

3. **Tester** :
   ```bash
   # Lint (doit passer sans erreur)
   ruff check src/ web/ tests/ --select F821,F811,E722 --ignore E501,E402

   # Tests
   python -m pytest tests/ -q
   ```

4. **Committer** avec un message clair :
   ```
   feat: ajouter handler X pour domaine Y
   fix: corriger le parsing de Z dans module W
   docs: mettre à jour le guide de déploiement
   test: ajouter tests pour le handler X
   ```

5. **Push** et ouvrir une Pull Request vers `main`

6. **Attendre** que la CI passe (lint + tests + Docker build)

### Checklist PR
- [ ] Les tests passent localement
- [ ] Le lint ruff passe sans erreur
- [ ] Les nouveaux fichiers ont des docstrings
- [ ] Les handlers V2 ont des tests correspondants
- [ ] Le CHANGELOG.md est mis à jour si c'est une feature visible

---

## Questions ?

Ouvrir une [issue](https://github.com/Losskarr/lumena/issues) sur GitHub.

---

_Licence : [GPL-3.0](LICENSE)_
