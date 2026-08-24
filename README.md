# Lumena

**Assistant IA personnel autonome, local-first, doté d'une mémoire persistante et capable d'agir réellement.**

[![Python 3.12](https://img.shields.io/badge/Python-3.12-3776AB)](https://www.python.org/)
[![Version](https://img.shields.io/badge/version-v1.0.50-F28C28)](#etat-du-projet)
[![Tests](https://img.shields.io/badge/tests-18K%2B_passed-22C55E)](#tests)
[![License](https://img.shields.io/badge/license-AGPL--3.0_%2F_Commercial-2563EB)](#licence)
[![Status](https://img.shields.io/badge/status-Beta-F59E0B)](#etat-du-projet)

![Lumena Control Panel](assets/pic1readme.png)

Lumena réunit dans une seule application le dialogue, l'exécution d'outils, le
développement logiciel, les missions longues, la création documentaire, la
navigation web, la mémoire, les intégrations professionnelles et l'automatisation
locale 24/7.

Elle ne se contente pas de proposer une procédure : en **mode Agent**, elle peut
planifier une demande, appeler ses outils, produire des fichiers, contrôler les
résultats obtenus et rendre compte des preuves réellement observées.

> **Version bêta v1.0.50**
>
> Lumena est utilisable au quotidien, mais reste un projet solo en évolution.
> Certaines fonctions dépendent d'API, de logiciels locaux, d'identifiants ou
> d'une validation humaine. Une capacité disponible n'est jamais une garantie
> universelle de réussite sur tous les environnements.

---

## Ce que Lumena sait faire

Lumena possède **37 catégories d'outils** et plus de **590 définitions natives**.
Le registre runtime peut dépasser **700 outils** lorsqu'il agrège les outils
dynamiques, les compatibilités historiques, les extensions et les serveurs MCP.
Ces nombres décrivent deux niveaux différents et ne doivent pas être confondus.

### Dialoguer et agir

- deux usages distincts : **Chat** pour dialoguer et **Agent** pour exécuter ;
- routage entre conversation, outil direct, projet et raisonnement multi-étapes ;
- boucle de raisonnement ReAct avec plan, actions, observations et preuves ;
- plus de **590 outils natifs**, répartis dans plus de **35 catégories** ;
- réponses en streaming, interruption, reprise et suivi des tâches ;
- identité, personnalité, humeur et contexte cohérents entre les interfaces ;
- utilisation depuis le web, le terminal, Discord, Telegram, WhatsApp et
  X/Twitter selon la configuration installée.

### Réaliser des missions longues

- missions asynchrones suivies depuis le panneau dédié ;
- délégation à plusieurs workers avec espaces de travail isolés ;
- contrats de fichiers et signatures pour coordonner les projets logiciels ;
- CodeAgent spécialisé pour écrire, corriger et tester du code ;
- vérification des tests, du navigateur, des artefacts et de la publication ;
- clôture honnête : une action non prouvée n'est pas présentée comme réussie ;
- coopération P2P entre plusieurs instances Lumena lorsque cette fonction est
  activée et autorisée.

### Créer et manipuler des documents

- **Document Studio** avec 30 modèles professionnels intégrés ;
- PDF, DOCX, XLSX, PPTX, HTML, CSV et formats texte ;
- factures, devis, contrats, rapports, ressources RH et documents opérationnels ;
- import de modèles utilisateur, personnalisation, logos et aperçu de rendu ;
- lecture, extraction, conversion, génération et modification documentaire ;
- composition documentaire utilisable dans les missions autonomes ;
- ingestion avec découpage sémantique, OCR PDF, extraction d'entités, citations
  et alimentation de la mémoire et du Knowledge Graph.

### Développer et vérifier des projets

- création et modification de projets frontend et backend ;
- opérations Git et GitHub, terminal, fichiers et commandes en sandbox ;
- Repo Map, recherche vectorielle du code, AST, WorldModel et règles projet ;
- diagnostics LSP, navigation vers les définitions et recherche de références ;
- pont IDE, sessions CodeAgent et historique des modifications ;
- tests unitaires et contrôles d'intégration ;
- prévisualisation locale et vérification par navigateur ;
- contrôle du DOM, captures d'écran et validation d'interactions ;
- génération vidéo avec Remotion et traitement d'images multi-provider.

### Utiliser le web et l'ordinateur

- recherche web, lecture de pages et téléchargement de ressources ;
- navigateur Playwright avec navigation et interactions ;
- campagnes de crawl, recherche approfondie et changement de source en cas de
  blocage ;
- Computer Use avec souris, clavier, fenêtres et vision ;
- garde SSRF, validation des URL et contrôle des commandes ;
- déploiement de sites et intégration IONOS selon les autorisations configurées ;
- diagnostics réseau, WHOIS, DNS, TLS, sous-domaines et outils OSINT encadrés ;
- opérations réseau locales comme SSH, Wake-on-LAN ou transfert distant sous
  politiques de sécurité.

### Mémoriser et apprendre

- mémoire de session et mémoire vectorielle persistante ChromaDB ;
- Knowledge Graph, recherche BM25, cache d'embeddings et contexte de projet ;
- ReflexionStore, SuccessStore, règles apprises et instincts ;
- journal des conversations, faits d'identité et continuité entre sessions ;
- scheduler, objectifs autonomes, curation et cycles d'apprentissage ;
- heartbeat, suivi de santé, rapports quotidiens et sauvegardes contrôlées ;
- micro-évaluations, curation et préparation de jeux de données ;
- fine-tuning local LoRA vers GGUF et Ollama lorsque l'environnement le permet.

### Produire des images et des vidéos

- génération d'images via plusieurs fournisseurs locaux ou distants ;
- édition, composition, upscale, remplacement ou suppression d'arrière-plan ;
- création de logos, miniatures et ressources SVG ;
- génération de vidéos Remotion en MP4/WebM à partir de composants React ;
- modèles vidéo, prévisualisation, validation et réparation du rendu ;
- analyse d'images par les modèles vision déclarés compatibles.

### Travailler avec des données publiques

- recherche et récupération de jeux de données sur data.gouv.fr ;
- interrogation SIRENE pour les entreprises françaises ;
- géocodage et données géographiques françaises ;
- Data Workbench pour charger, explorer, filtrer et exporter des données ;
- lecture de tableaux CSV/XLSX et génération de feuilles de calcul ;
- conservation des sources et des preuves utilisées dans les rapports.

### Piloter des services professionnels

- **GitHub** : dépôts, fichiers, issues et publication de dossiers ;
- **Notion** : recherche, lecture, création, mise à jour et bases de données ;
- **n8n** : workflows, exécutions et modèles d'automatisation ;
- **Stripe** : clients, produits, prix, paiements, abonnements, factures,
  remboursements et liens de paiement ;
- **IONOS** : déploiement SFTP et gestion de base de données par propositions
  soumises à validation ;
- **Mail et messagerie** : emails, pièces jointes, documents Telegram/WhatsApp,
  SMS et appels critiques selon les fournisseurs configurés ;
- **Discord** : serveurs, salons, rôles, permissions et messages ;
- **X/Twitter** : publication, recherche, timeline et mentions ;
- **Spotify** : lecture, pause, file d'attente et morceau courant.

Toutes les intégrations sont optionnelles. Leur présence dans Lumena ne contourne
jamais les quotas, abonnements, permissions ou politiques de leur fournisseur.

### Observer et administrer le runtime

- Overview alimenté par des données réelles en lecture seule ;
- tâches, missions, workers, sessions et conversations persistées ;
- Live Trace SSE, logs, alertes et console ;
- santé des fournisseurs, mémoire, disque et processus ;
- historique d'autonomie et preuves d'exécution ;
- panneau de configuration, catalogue de modèles et assistant d'installation ;
- annulation, reprise, archivage et restauration lorsque le composant le permet.

### Étendre ses capacités avec MCP

Lumena intègre le **Model Context Protocol** dans sa boucle conversationnelle :

- découverte et catalogue de serveurs MCP ;
- installation isolée et activation à chaud ;
- classification des outils et intégration au registre natif ;
- politiques de confiance, permissions et file d'approbation ;
- utilisation des outils `mcp__<serveur>__<outil>` depuis le mode Agent ;
- diagnostic, désactivation et suppression depuis le panneau MCP.

Les mutations sensibles restent soumises aux politiques et confirmations de
Lumena. Un MCP externe reste dépendant de son service, de ses droits et de ses
identifiants.

### Charger des skills et des règles

Lumena embarque un système de skills indépendant du fournisseur LLM. Les skills
actuels couvrent notamment la création de sites, les tests web, les documents,
les feuilles de calcul, les présentations, les images, Remotion, Stripe, IONOS,
data.gouv.fr, l'automatisation et la création de nouveaux skills.

Les fichiers `.lumena_rules` et `.lumena/rules.yaml` permettent également
d'adapter les conventions à chaque projet sans modifier le cœur de Lumena.

<details>
<summary><strong>Voir les 37 catégories d'outils enregistrées</strong></summary>

`agents`, `automation`, `autonomy`, `browser`, `codebase`, `communication`,
`computer_use`, `custom`, `data`, `discord`, `documents`, `files`, `git`,
`github`, `ide`, `image`, `ionos`, `lsp`, `mail`, `mcp`, `media`, `memory`,
`missions`, `network`, `notion`, `peers`, `platform`, `project`, `security`,
`skills`, `social`, `spotify`, `stripe`, `system`, `video`, `web`, `website`.

</details>

---

## Le Control Panel

L'interface n'est pas uniquement une fenêtre de chat. Elle expose les composants
réels de Lumena dans des panneaux spécialisés :

| Espace | Panneaux principaux |
|---|---|
| Travail | Chat, Projets, fichiers et workspaces |
| Contrôle | Overview, Repo Map, Code Search, Mémoire, Journal, Identité |
| Agent | Outils, Règles, Instincts, Tâches, Missions, Documents, Sessions |
| Apprentissage | Datasets, rapports d'apprentissage et Fine-tuning |
| Système | Émotions, Voix, Hooks, Live Trace, Console, Logs, Alertes |
| Infrastructure | Telegram, WhatsApp, Autonomie, Réseau/P2P, MCP, Providers, IONOS |
| Commerce | Vue Stripe, Paiements, Abonnements et Produits |
| Administration | Configuration, assistant de démarrage et documentation intégrée |

Les panneaux affichent un état réel ou une action câblée. Les composants encore
partiels sont identifiés comme tels dans la section suivante.

---

## Une seule interface, plusieurs moteurs

Lumena peut utiliser des modèles locaux ou distants sans modifier son interface
de travail :

- Ollama ;
- DeepSeek ;
- OpenAI ;
- Anthropic ;
- Google ;
- Mistral ;
- Moonshot/Kimi ;
- xAI ;
- NVIDIA NIM ;
- MiniMax ;
- Z.AI.

Une session **ChatGPT/Codex** peut également être configurée comme source de
modèle, séparément des fournisseurs utilisant une clé API. La disponibilité des
modèles dépend toujours du compte, de l'abonnement et des droits du fournisseur.

Les modèles d'image utilisent leur propre catalogue et leur propre chaîne de
fallback. Lumena ne présente pas un modèle texte comme capable de générer une
image si cette capacité n'est pas déclarée.

---

## Sécurité et contrôle

Lumena agit sur une machine réelle. Ses garde-fous font donc partie du produit :

- sandbox Docker configurable (`auto`, `always`, `never`) ;
- bornes de chemins et protection contre le path traversal ;
- sanitizer de commandes et restrictions sur les actions destructrices ;
- protection SSRF et registre contrôlé des prévisualisations locales ;
- confirmations pour les opérations sensibles ;
- secrets et identifiants séparés du code ;
- preuves d'exécution conservées dans un ledger ;
- annulation coopérative des tâches et sous-agents.

Lumena doit être utilisée avec un périmètre de fichiers adapté et des droits
minimaux. Les actions externes importantes doivent rester supervisées.

---

## État des composants

| Composant | État | Remarque |
|---|---|---|
| Chat et mode Agent | Opérationnel | Deux comportements distincts, même interface |
| Routage et registre d'outils | Opérationnel | Outil direct, projet, ReAct et politiques par catégorie |
| Missions locales et sous-agents | V1 certifiée | Délégation, preuves, artefacts et clôture |
| Document Studio | V1 close | 30 modèles, import et composition en mission |
| Overview | V1 production | Données réelles en lecture seule et widgets configurables |
| MCP | Opérationnel | Les services externes restent conditionnels |
| CodeAgent | Opérationnel | Développement logiciel uniquement |
| Mémoire et continuité | Opérationnel | ChromaDB, BM25, Knowledge Graph et sessions |
| Navigation et vérification web | Opérationnel | La réussite dépend du site et des protections externes |
| Images et Remotion | Opérationnel selon configuration | Fournisseurs et dépendances optionnels |
| Canaux de communication | Opérationnel selon configuration | Tokens, webhooks et droits nécessaires |
| Intégrations professionnelles | Opérationnel selon configuration | GitHub, Notion, n8n, Stripe, IONOS et autres |
| Autonomie 24/7 | Opérationnel avec garde-fous | Actions sensibles conditionnées par les flags et permissions |
| Données publiques et perception | Opérationnel | Services externes et OCR parfois nécessaires |
| Voix V2 | Certifiée techniquement | Timbre final et validation humaine encore ouverts |
| P2P multi-Lumena | Bêta avancée | Certification complète multi-instance encore ouverte |
| Fine-tuning local | Expérimental | Dépend fortement du matériel et des modèles |
| Hooks | Infrastructure disponible | Branchement produit encore partiel |

---

## Installation

### Prérequis

- Python 3.10 à 3.12 ;
- Windows, Linux ou macOS ;
- au moins un fournisseur LLM configuré, ou un modèle Ollama local ;
- Docker Desktop en option pour l'isolation ;
- Node.js pour certaines fonctions web et vidéo.

### Installation rapide

```bash
git clone https://github.com/Losskarr/lumena.git
cd lumena
```

Sous Windows :

```cmd
INSTALL.bat
START.bat
```

Sous Linux ou macOS :

```bash
chmod +x install.sh start.sh
./install.sh
./start.sh
```

Installation manuelle :

```bash
python -m venv .venv

# Linux/macOS
source .venv/bin/activate

# Windows PowerShell
.venv\Scripts\Activate.ps1

pip install -r requirements.txt
cp .env.example .env
```

L'interface est ensuite disponible sur `http://localhost:8080`.

### Configuration minimale

Une seule source LLM suffit pour démarrer. Par exemple :

```env
DEEPSEEK_API_KEY=...
LUMENA_DEFAULT_MODEL=deepseek-v3
```

Les clés API, les modèles locaux, l'abonnement Codex, la voix, les canaux et les
services externes peuvent ensuite être configurés depuis l'interface.

Ne commitez jamais votre fichier `.env`.

---

## Exemples

```text
Crée une application web de suivi de stock avec backend, tests et vérification
réelle dans le navigateur.

Analyse les PDF de ce dossier et génère un rapport DOCX avec un tableau de
synthèse et les sources utilisées.

Prépare une facture à partir de ces données en utilisant mon modèle Document
Studio, vérifie son rendu puis ouvre le résultat.

Surveille ce projet chaque matin, exécute les tests et préviens-moi uniquement
si une régression est prouvée.

Cherche un serveur MCP adapté à ce besoin, présente les permissions demandées et
attends mon approbation avant de l'installer.
```

---

## Architecture

```text
Utilisateur et canaux
        |
        v
Interface web / CLI / Telegram / Discord / WhatsApp / X
        |
        v
LumenaCore et services de contexte
        |
        +-- Routage d'intention
        |     +-- Chat direct
        |     +-- Outil direct
        |     +-- Pipeline projet
        |     +-- ReAct multi-étapes
        |
        +-- ReAct Agent
        |     +-- ToolRegistry (natif + dynamique + MCP)
        |     +-- contrats de catégories et confirmations
        |     +-- skills, règles, instincts et mémoire
        |     +-- ledger de preuves et garde-fous finaux
        |
        +-- Missions
        |     +-- lead
        |     +-- workers isolés
        |     +-- contrat, stubs et fichiers autorisés
        |     +-- CodeAgent et intégration
        |     +-- tests, navigateur, documents et publication
        |
        +-- Capacités
        |     +-- fichiers / terminal / Git / LSP / IDE
        |     +-- navigateur / web / Computer Use
        |     +-- documents / données / images / vidéo
        |     +-- communication / commerce / hébergement
        |
        +-- Mémoire et apprentissage
        |     +-- ChromaDB / BM25 / Knowledge Graph
        |     +-- journal / réflexions / succès / identité
        |     +-- datasets / évaluation / fine-tuning
        |
        +-- Runtime étendu
              +-- autonomie / scheduler / heartbeat
              +-- MCP
              +-- Voice V2
              +-- P2P multi-instance
              +-- télémétrie / alertes / Live Trace
```

Répertoires principaux :

```text
src/                 coeur, raisonnement, outils, mémoire et autonomie
src/reasoning/       boucle ReAct, registre et politiques d'exécution
src/agents/          CodeAgent, Architect et agents spécialisés
src/autonomy/        daemon, scheduler, heartbeat, objectifs et opérations
src/channels/        Discord, Telegram, WhatsApp et X/Twitter
src/computer_use/    vision et contrôle natif de l'ordinateur
src/core_services/   routage, contexte, identité, mémoire et services métier
src/documents/       moteur Document Studio
src/learning/        réflexions, succès, instincts et journaux d'apprentissage
src/llm/             catalogues, profils et routage multi-provider
src/mcp/             intégration Model Context Protocol
src/memory/          ChromaDB, BM25, Knowledge Graph et contexte de code
src/perception/      lecture documentaire et extraction de connaissances
src/runtime/         orchestration, preuves et coopération
src/services/        fournisseurs et intégrations externes
src/skills/          chargement et sélection des skills
src/telemetry/       événements, traces et suivi des modifications
src/training/        préparation, entraînement, export GGUF et Ollama
src/voice/           STT, TTS et Voice V2
web/                 API FastAPI et interface utilisateur
assets/templates/    modèles documentaires intégrés
tests/               tests unitaires, intégration et non-régression
plans/               historique technique local et certifications
```

Les compteurs détaillés évoluent rapidement. Le code et les tests constituent la
source de vérité ; le README décrit volontairement l'architecture stable.

---

## Tests

```bash
# Suite complète
python -m pytest tests/ --timeout=15 -q

# Exemple ciblé
python -m pytest tests/reasoning/test_react_plan.py -v
```

La dernière certification documentaire connue dépasse **18 500 tests réussis**.
Ce nombre n'est pas une promesse permanente : chaque modification doit être
validée contre la suite correspondant à son périmètre.

---

## Limites connues

- le projet reste en bêta et est maintenu par une seule personne ;
- les fournisseurs externes peuvent imposer quotas, pannes et restrictions ;
- le Computer Use est plus complet sous Windows ;
- Voice V2 attend encore sa certification humaine finale ;
- la certification P2P multi-instance n'est pas entièrement terminée ;
- les opérations réelles peuvent nécessiter une confirmation ou des identifiants ;
- aucun agent ne peut garantir la réussite de toutes les demandes imaginables.

Ces limites sont affichées pour distinguer les capacités du produit des garanties
qui nécessitent encore une preuve dans l'environnement de l'utilisateur.

---

## Contribuer

Les rapports de bugs reproductibles sont les contributions les plus utiles.
Incluez si possible :

1. la demande envoyée ;
2. le modèle et le fournisseur utilisés ;
3. les lignes de logs pertinentes ;
4. le résultat attendu et le résultat obtenu ;
5. le système d'exploitation et les dépendances externes concernées.

Voir [CONTRIBUTING.md](CONTRIBUTING.md) avant de proposer une modification.

## Licence

| Usage | Licence |
|---|---|
| Personnel, académique, associatif ou open source compatible | [AGPL-3.0](LICENSE) |
| SaaS, intégration propriétaire ou exploitation commerciale | [Licence commerciale](LICENSE_COMMERCIAL.md) |

---

**Lumena v1.0.50 — projet solo, architecture ouverte, actions contrôlées.**
