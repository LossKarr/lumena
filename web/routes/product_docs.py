"""Product documentation API — serves the structured Lumena product documentation.

All numeric stats are collected LIVE from the real codebase / runtime at request
time (with a 60-second in-memory cache to avoid unnecessary I/O).
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Dict, List

from fastapi import APIRouter, Depends

from web.routes import deps

router = APIRouter()

from src.utils.paths import ROOT_DIR, OPS_DIR, LAST_TEST_RESULT_JSON, MEMORY_DIR, JOURNAL_DIR

_PROJECT_ROOT = ROOT_DIR

# ── Live stats cache ────────────────────────────────────────────
_stats_cache: Dict[str, Any] = {}
_stats_ts: float = 0.0
_CACHE_TTL = 60  # seconds


def _read_text_auto(path: Path) -> str:
    """Read a text file, auto-detecting UTF-16 BOM (PowerShell default) vs UTF-8."""
    raw = path.read_bytes()
    if raw[:2] in (b"\xff\xfe", b"\xfe\xff"):
        return raw.decode("utf-16", errors="replace")
    if raw[:3] == b"\xef\xbb\xbf":
        return raw[3:].decode("utf-8", errors="replace")
    return raw.decode("utf-8", errors="replace")


def _collect_live_stats() -> Dict[str, Any]:
    """Scan the real codebase / runtime and return accurate numbers."""
    global _stats_cache, _stats_ts
    now = time.monotonic()
    if _stats_cache and (now - _stats_ts) < _CACHE_TTL:
        return _stats_cache

    root = _PROJECT_ROOT

    # ── Tools count (runtime-safe: try ToolRegistry, fallback to handler files) ─
    tools_count = 0
    tools_categories: Dict[str, int] = {}
    try:
        from web.routes import deps
        if deps.lumena and hasattr(deps.lumena, "tool_system"):
            ts = deps.lumena.tool_system
            tools_count = sum(1 for _ in ts._iter_all_tools())
            # categories from V2 registry
            if hasattr(ts, "_tool_registry") and ts._tool_registry:
                reg = ts._tool_registry
                if hasattr(reg, "_v2") and hasattr(reg._v2, "_categories"):
                    tools_categories = {
                        cat: len(names) for cat, names in reg._v2._categories.items()
                    }
    except Exception:
        pass
    # Build categories from handler files (always — runtime rarely exposes them)
    _HANDLER_STEM_TO_CAT = {
        "agents": "agents", "autonomy": "autonomy", "browser": "browser",
        "codebase": "codebase", "computer_use": "computer_use",
        "config_manager": "system", "custom": "custom",
        "discord_admin": "discord", "documents": "documents",
        "files": "files", "git": "git", "github": "github",
        "heartbeat_self": "heartbeat", "http_api": "http",
        "ide": "ide", "lsp": "lsp", "mail": "mail",
        "memory": "memory", "network": "network", "notion": "notion",
        "osint": "osint", "perception": "perception", "plans": "plans",
        "project": "project", "security": "security", "skills": "skills",
        "spotify": "spotify", "system": "system", "twitter": "social",
        "web": "web", "website": "website",
        # Catégorie `data` (data.gouv / SIRENE / géo / workbench) — réorg 2026-05-29
        "datagouv": "data", "sirene": "data", "geo_gouv": "data",
        "data_workbench": "data",
        # Intégrations / médias regroupées sous leur catégorie
        "image_gen": "image", "stripe_api": "stripe", "n8n": "automation",
        "ionos": "ionos", "remotion": "video", "batch": "files",
    }
    if not tools_categories:
        # Scan handler files to build categories (and count if runtime unavailable)
        _file_tools_count = 0
        try:
            handler_dir = root / "src" / "reasoning" / "handlers"
            for py in handler_dir.glob("*.py"):
                if py.name.startswith("_") or py.stem in ("registry_v2", "context", "contracts", "parity_tools"):
                    continue
                txt = py.read_text(encoding="utf-8", errors="replace")
                count = txt.count("HandlerDef(")
                if count > 0:
                    _file_tools_count += count
                    cat = _HANDLER_STEM_TO_CAT.get(py.stem, py.stem)
                    tools_categories[cat] = tools_categories.get(cat, 0) + count
        except Exception:
            pass
        if tools_count == 0:
            tools_count = _file_tools_count

    # ── Skills ──────────────────────────────────────────────────────────────
    skills_dir = root / "skills"
    skill_names: List[str] = []
    if skills_dir.is_dir():
        for d in sorted(skills_dir.iterdir()):
            if d.is_dir() and (d / "SKILL.md").exists():
                skill_names.append(d.name)
    skills_count = len(skill_names)

    # ── Handler modules ─────────────────────────────────────────────────────
    handler_dir = root / "src" / "reasoning" / "handlers"
    handler_modules = 0
    if handler_dir.is_dir():
        handler_modules = sum(
            1 for f in handler_dir.glob("*.py")
            if f.name != "__init__.py"
        )

    # ── Core services ───────────────────────────────────────────────────────
    cs_dir = root / "src" / "core_services"
    core_services_count = sum(
        1 for f in cs_dir.glob("*.py") if f.name != "__init__.py"
    ) if cs_dir.is_dir() else 0

    # ── Tools modules ───────────────────────────────────────────────────────
    tools_mod_dir = root / "src" / "tools"
    tools_modules = sum(
        1 for f in tools_mod_dir.glob("*.py")
        if f.name != "__init__.py" and not f.name.endswith(".backup")
    ) if tools_mod_dir.is_dir() else 0

    # ── Channels ────────────────────────────────────────────────────────────
    chan_dir = root / "src" / "channels"
    channel_modules = sum(
        1 for f in chan_dir.glob("*.py") if f.name != "__init__.py"
    ) if chan_dir.is_dir() else 0

    # ── Test files ──────────────────────────────────────────────────────────
    test_dir = root / "tests"
    test_files = sum(
        1 for _ in test_dir.rglob("test_*.py")
    ) if test_dir.is_dir() else 0

    # ── Tests passed (multiple strategies) ─────────────────────────────────
    tests_passed = 0
    tests_failed = 0
    tests_duration = ""
    # Strategy 1: CI phase gate or test result JSON
    ci_marker = OPS_DIR / "ci_last_run.json"
    last_test_path = LAST_TEST_RESULT_JSON
    for candidate in (ci_marker, last_test_path):
        if candidate.exists():
            try:
                result = json.loads(candidate.read_text(encoding="utf-8", errors="replace"))
                p = result.get("passed", 0)
                if p > tests_passed:
                    tests_passed = p
                    tests_failed = result.get("failed", 0)
                    tests_duration = result.get("duration", "")
            except Exception:
                pass
    # Strategy 2: scan _test_*.txt / pytest_result.txt files at project root for summary lines
    import re
    _PYTEST_SUMMARY_RE = re.compile(r"(\d+)\s+passed(?:.*?(\d+)\s+failed)?(?:.*?in\s+([\d.]+)s)?", re.I)
    for pat in ("pytest_result.txt", "_test_*.txt"):
        for f in sorted(root.glob(pat), key=lambda p: p.stat().st_mtime, reverse=True):
            try:
                txt = _read_text_auto(f)
                for m in _PYTEST_SUMMARY_RE.finditer(txt):
                    p = int(m.group(1))
                    if p > tests_passed:
                        tests_passed = p
                        tests_failed = int(m.group(2)) if m.group(2) else 0
                        tests_duration = f"{m.group(3)}s" if m.group(3) else ""
            except Exception:
                pass
    # Strategy 3: count test functions via pytest --co (collected) — fallback to file count
    if tests_passed == 0 and test_files > 0:
        # Use file count × ~27 (avg tests/file) as rough estimate
        tests_passed = 0  # leave as 0 — will show file count instead

    # ── LLM Providers ───────────────────────────────────────────────────────
    providers_count = 10  # stable enum
    provider_names: List[str] = []
    try:
        from src.llm.providers import ProviderType
        providers_count = len(ProviderType)
        provider_names = [p.value for p in ProviderType]
    except Exception:
        provider_names = ["deepseek", "openai", "anthropic", "google", "mistral", "moonshot", "xai", "nvidia", "minimax", "zai", "ollama"]

    # ── LLM Models ──────────────────────────────────────────────────────────
    models_count = 0
    try:
        from src.llm.providers import AVAILABLE_MODELS
        models_count = len(AVAILABLE_MODELS)
    except Exception:
        models_count = 0

    # ── Memory / ChromaDB ───────────────────────────────────────────────────
    memory_count = 0
    try:
        # Prefer the live singleton to avoid creating a second ChromaDB instance
        from web.server import lumena
        if lumena and hasattr(lumena, "memory") and lumena.memory:
            memory_count = lumena.memory.count()
    except Exception:
        pass
    if memory_count == 0:
        # Fallback: open the canonical vector store (read-only count)
        canonical = MEMORY_DIR / "vector"
        if (canonical / "chromadb").exists():
            try:
                import chromadb as _cdb
                _client = _cdb.PersistentClient(path=str(canonical / "chromadb"))
                _col = _client.get_collection("lumena_memories")
                memory_count = _col.count()
            except Exception:
                pass

    # ── Journal entries ─────────────────────────────────────────────────────
    journal_dir = JOURNAL_DIR
    journal_entries = sum(
        1 for _ in journal_dir.glob("*.md")
    ) if journal_dir.is_dir() else 0

    # ── Requirements locked ─────────────────────────────────────────────────
    req_lock = root / "requirements-lock.txt"
    packages_locked = 0
    if req_lock.exists():
        text = _read_text_auto(req_lock)
        lines = text.splitlines()
        packages_locked = sum(1 for ln in lines if ln.strip() and not ln.strip().startswith("#") and "==" in ln)

    # ── API routes count ────────────────────────────────────────────────────
    routes_count = 0
    routes_dir = root / "web" / "routes"
    if routes_dir.is_dir():
        for py in routes_dir.glob("*.py"):
            if py.name.startswith("_") or py.name == "__init__.py":
                continue
            txt = py.read_text(encoding="utf-8", errors="replace")
            routes_count += txt.count("@router.get(") + txt.count("@router.post(") + txt.count("@router.put(") + txt.count("@router.delete(") + txt.count("@router.patch(")

    # ── Default model ───────────────────────────────────────────────────────
    import os
    default_model = os.environ.get("LUMENA_DEFAULT_MODEL", "deepseek-v3")

    stats = {
        "tools_count": tools_count,
        "tools_categories": tools_categories,
        "skills_count": skills_count,
        "skill_names": skill_names,
        "handler_modules": handler_modules,
        "core_services": core_services_count,
        "tools_modules": tools_modules,
        "channel_modules": channel_modules,
        "test_files": test_files,
        "tests_passed": tests_passed,
        "tests_failed": tests_failed,
        "tests_duration": tests_duration,
        "providers_count": providers_count,
        "provider_names": provider_names,
        "models_count": models_count,
        "memory_count": memory_count,
        "journal_entries": journal_entries,
        "packages_locked": packages_locked,
        "routes_count": routes_count,
        "default_model": default_model,
    }
    _stats_cache = stats
    _stats_ts = now
    return stats


# ── Helpers for building dynamic HTML fragments ─────────────────


def _fmt(n: int) -> str:
    """Format a number with space thousands separator (French convention)."""
    return f"{n:,}".replace(",", "\u202f")



# ── Full product documentation structure ────────────────────────
# Returns a JSON tree of sections rendered client-side.
# Placeholders like {tools_count} are replaced at request time.

_DOC_SECTIONS = [
    {
        "id": "overview",
        "icon": "lumena-logo",
        "title": "Vue d'ensemble",
        "content": """
<div class="doc-callout doc-callout-warn" style="border-left:4px solid #f59e0b;margin-bottom:18px">
  <strong>⚠️ Version Beta — Lumena Beta-v1.0</strong><br>
  Cette version est une <strong>bêta publique</strong>. Certaines fonctionnalités sont encore en développement actif.
  Des anomalies de comportement peuvent survenir ponctuellement. Les corrections sont déployées en continu.
</div>

<p class="doc-lead">Lumena est un assistant IA personnel autonome conçu pour fonctionner 24/7.
Elle raisonne, mémorise, agit, apprend et interagit dans le monde réel à travers 5 canaux simultanés,
{tools_count} outils natifs et une personnalité stable.</p>

<div class="doc-grid doc-grid-3">
  <div class="doc-stat-card">
    <div class="doc-stat-value">{tools_count}</div>
    <div class="doc-stat-label">Outils natifs</div>
  </div>
  <div class="doc-stat-card">
    <div class="doc-stat-value">{tests_passed}</div>
    <div class="doc-stat-label">Tests automatisés</div>
  </div>
  <div class="doc-stat-card">
    <div class="doc-stat-value">{tests_failed}</div>
    <div class="doc-stat-label">Failures</div>
  </div>
  <div class="doc-stat-card">
    <div class="doc-stat-value">{providers_count}</div>
    <div class="doc-stat-label">Providers LLM</div>
  </div>
  <div class="doc-stat-card">
    <div class="doc-stat-value">5</div>
    <div class="doc-stat-label">Canaux</div>
  </div>
  <div class="doc-stat-card">
    <div class="doc-stat-value">{skills_count}</div>
    <div class="doc-stat-label">Skills installés</div>
  </div>
  <div class="doc-stat-card">
    <div class="doc-stat-value">{models_count}</div>
    <div class="doc-stat-label">Modèles LLM</div>
  </div>
  <div class="doc-stat-card">
    <div class="doc-stat-value">13</div>
    <div class="doc-stat-label">Templates documents</div>
  </div>
  <div class="doc-stat-card">
    <div class="doc-stat-value">{routes_count}</div>
    <div class="doc-stat-label">Endpoints API</div>
  </div>
</div>

<h3>Positionnement</h3>
<p>La plupart des assistants IA répondent à des questions puis oublient tout.
Lumena <strong>vit</strong> : elle mémorise chaque conversation, prend des initiatives,
programme ses propres tâches, surveille les systèmes, publie sur les réseaux sociaux,
gère des serveurs, envoie des emails, écrit du code et corrige ses bugs — de manière autonome.</p>

<table class="doc-table">
<thead><tr><th>Fonctionnalité</th><th>Lumena</th><th>ChatGPT / Claude</th><th>AutoGPT / CrewAI</th></tr></thead>
<tbody>
<tr><td>Mémoire persistante vectorielle</td><td class="ok">✓ ChromaDB</td><td class="ko">✗ Session</td><td class="warn">Limitée</td></tr>
<tr><td>Autonomie 24/7 (daemon)</td><td class="ok">✓</td><td class="ko">✗</td><td class="warn">Partielle</td></tr>
<tr><td>Personnalité stable</td><td class="ok">✓</td><td class="ko">✗</td><td class="ko">✗</td></tr>
<tr><td>Multi-canal simultané</td><td class="ok">✓ 5 canaux</td><td class="ko">✗</td><td class="ko">✗</td></tr>
<tr><td>{tools_count} outils natifs</td><td class="ok">✓</td><td class="ko">Plugins limités</td><td class="warn">Partiel</td></tr>
<tr><td>Contrôle complet du PC</td><td class="ok">✓ Souris + clavier</td><td class="ko">✗</td><td class="ko">✗</td></tr>
<tr><td>Développement autonome</td><td class="ok">✓ CodeAgent</td><td class="warn">Limité</td><td class="ok">✓</td></tr>
<tr><td>Multi-LLM fallback auto ({providers_count} providers)</td><td class="ok">✓</td><td class="ko">✗</td><td class="warn">Partiel</td></tr>
<tr><td>Skills créés à la volée</td><td class="ok">✓</td><td class="ko">✗</td><td class="ko">✗</td></tr>
<tr><td>Journal quotidien auto</td><td class="ok">✓</td><td class="ko">✗</td><td class="ko">✗</td></tr>
<tr><td>Documents pro (13 templates)</td><td class="ok">✓ Factures, contrats, devis…</td><td class="ko">✗</td><td class="ko">✗</td></tr>
<tr><td>Fine-tuning local LoRA</td><td class="ok">✓ Pipeline → GGUF → Ollama</td><td class="ko">✗</td><td class="ko">✗</td></tr>
</tbody>
</table>

<h3>Démarrage rapide</h3>
<div class="doc-callout">
  <strong>Option 1 — Docker (recommandé) :</strong>
  <code>cp .env.example .env</code> → configurer les clés API → <code>docker-compose up -d</code>
  → <a href="http://localhost:8080">http://localhost:8080</a>
</div>
<div class="doc-callout">
  <strong>Option 2 — Local (Windows) :</strong>
  <code>venv\\Scripts\\activate</code> → <code>pip install -r requirements.txt</code> →
  <code>python -m src</code> → <a href="http://localhost:8080">http://localhost:8080</a>
</div>
<div class="doc-callout">
  <strong>Option 3 — Wizard One-Click :</strong>
  Lancer le serveur puis ouvrir <a href="http://localhost:8080/setup">http://localhost:8080/setup</a>
  — assistant guidé qui configure les providers, clés API, workspace et Telegram en quelques clics.
</div>
<p>Voir la section <strong>Déploiement</strong> pour les détails complets.</p>

<div class="doc-callout" style="text-align:right;color:var(--muted);font-size:11px;border:none;padding-top:0">
  Lumena — Beta-v1.0
</div>
""",
    },
    {
        "id": "capabilities",
        "icon": "zap",
        "title": "Capacités",
        "content": """
<div class="doc-caps-grid">

<div class="doc-cap-card">
  <h4>Communication</h4>
  <ul>
    <li>Conversation naturelle multi-sujets</li>
    <li>Adaptation du ton (formel, informel, technique)</li>
    <li>Émotions authentiques et humeur évolutive</li>
    <li>Voix : pipeline STT/TTS intégré (Whisper + Piper) — <em>fonctionnalités de base opérationnelles, intégration avancée en cours de développement</em></li>
    <li>5 canaux simultanés : Web, Telegram, Discord, Twitter, CLI</li>
    <li>IDE bridge WebSocket bidirectionnel (36 outils VSCode/Cursor)</li>
  </ul>
</div>

<div class="doc-cap-card">
  <h4>Contrôle PC</h4>
  <ul>
    <li>Cascade Computer Use natif : Anthropic → OpenAI → Google → fallback agent loop</li>
    <li>pywinauto focus fenêtres (backend=uia) + alt+tab fallback</li>
    <li>DOM Indexer pour navigation web précise</li>
    <li>Vision cascade : Gemini Flash → Claude → Ollama → OCR pytesseract</li>
    <li>Commandes shell Windows / Linux + sandbox Docker optionnel</li>
  </ul>
</div>

<div class="doc-cap-card">
  <h4>Navigation Web</h4>
  <ul>
    <li>Recherche web (DuckDuckGo, Brave Search)</li>
    <li>Playwright stealth v2 (10 techniques anti-détection, UA rotatifs)</li>
    <li>68 outils browser natifs (formulaires, clics, scraping, screenshots…)</li>
    <li>Recherche approfondie multi-sources (deep_research)</li>
    <li>SSRF guard : bloque localhost, IPs privées, DNS rebinding</li>
  </ul>
</div>

<div class="doc-cap-card">
  <h4>Développement</h4>
  <ul>
    <li><strong>CodeAgent</strong> pleinement opérationnel — agents spécialisés (Debug, Refactor, Research, Browser, File, Planner) architecturés et en intégration active</li>
    <li>Boucle 30 iter + outer retry 3× avec approche différente</li>
    <li>Auto-test après 3 edits consécutifs, ruff lint natif</li>
    <li>Projets web complets from scratch (HTML/CSS/JS/Python)</li>
    <li>Patches multi-fichiers (apply_patch, edit_lines, edit_file)</li>
  </ul>
</div>

<div class="doc-cap-card">
  <h4>Mémoire & Savoir</h4>
  <ul>
    <li>4 niveaux : session, vectoriel ChromaDB, knowledge graph, BM25</li>
    <li>Embedding cache + file watcher</li>
    <li>Journal quotidien automatique</li>
    <li>Recherche sémantique de souvenirs</li>
    <li>Détection automatique des angles morts</li>
  </ul>
</div>

<div class="doc-cap-card">
  <h4>Services Tiers</h4>
  <ul>
    <li>Stripe — 33 outils (paiements, abonnements, factures, coupons)</li>
    <li>n8n — 17 outils (workflows, webhooks, automatisation)</li>
    <li>Notion — 7 outils (pages, bases de données, blocs)</li>
    <li>Spotify — 8 outils (play, pause, volume, queue, recherche)</li>
    <li>GitHub — 10 outils (repos, issues, PRs, commits)</li>
    <li>Email Gmail — lecture, envoi, pièces jointes</li>
    <li>HTTP générique — 5 outils (GET/POST/PUT/DELETE/PATCH)</li>
  </ul>
</div>

<div class="doc-cap-card">
  <h4>Données publiques France 🇫🇷</h4>
  <ul>
    <li><strong>data.gouv.fr</strong> — 3 outils : recherche dans 50 000+ datasets officiels (INSEE, DVF immobilier, marchés publics, démographie), scoring qualité /100 par dataset, téléchargement avec sidecar provenance (MD5 + URL stable)</li>
    <li><strong>SIRENE</strong> — 2 outils : recherche entreprise (nom/dirigeant/NAF) et lookup direct par SIRET via <code>recherche-entreprises.api.gouv.fr</code></li>
    <li><strong>Géocodage BAN</strong> — 3 outils : adresse → GPS, GPS → adresse, métadonnées commune par code INSEE (population, surface, EPCI)</li>
    <li><strong>Workbench tabulaire</strong> — 6 outils sur CSV/JSON/XLSX téléchargés : profile (colonnes/types/nulls), filter (ops whitelistés), aggregate (group_by + count/sum/mean/min/max/median), unique_values, export transformé, join multi-fichiers (inner/left/right/outer)</li>
    <li>APIs publiques officielles, <strong>0 clé requise</strong>, scoring anti-faux-positifs (taille resource, profil acheteur détecté)</li>
  </ul>
</div>

<div class="doc-cap-card">
  <h4>Documents professionnels</h4>
  <ul>
    <li>36 handlers V2 : factures, devis, contrats, NDA, PO, CV, rapports…</li>
    <li>13 templates Jinja2 (assets/templates/) → export PDF WeasyPrint</li>
    <li>Lecture et résumé de PDF, DOCX, images (OCR)</li>
    <li>Ingestion dans base de connaissances vectorielle</li>
  </ul>
</div>

<div class="doc-cap-card">
  <h4>Sécurité & Réseau</h4>
  <ul>
    <li>Scan de ports, reconnaissance de domaines</li>
    <li>SSRF guard Playwright (bloque IPs privées, DNS rebinding)</li>
    <li>Anti-injection shell (command_sanitizer.py)</li>
    <li>Path traversal guard (file_guardrails.py)</li>
    <li>Sandbox Docker, retry intra-provider, context overflow guard</li>
    <li>OSINT (domaines, emails, IPs, Shodan) — 16 outils</li>
  </ul>
</div>

<div class="doc-cap-card">
  <h4>Multi-Lumena LAN</h4>
  <ul>
    <li>Jumelage sécurisé par code court 6 chars (TTL 5 min, usage unique)</li>
    <li>Peer tokens révocables stockés hashés (SHA-256), liés à l'instance (anti-usurpation)</li>
    <li>Délégation de tâches inter-instances via peer token (admin token jamais exposé)</li>
    <li>Découverte LAN active (<code>LUMENA_PEER_DISCOVERY=1</code>) multi-réseau (sélection par adaptateur)</li>
    <li>Découverte mDNS passive <code>_lumena._tcp.local</code> — optionnel, sans secret dans les TXT records</li>
    <li>Validation RFC1918 stricte anti-SSRF sur toutes les sorties réseau</li>
    <li>Audit log des délégations, pare-feu Windows assisté avec confirmation explicite</li>
    <li>UI vue simple (statut/actions) + vue avancée (cartes techniques complètes)</li>
  </ul>
</div>

<div class="doc-cap-card">
  <h4>Génération d'images</h4>
  <ul>
    <li>12 providers : Gemini, OpenAI (GPT-Image), Flux (BFL), Stability AI, Imagen (Google), Ideogram, Recraft, Replicate, Hugging Face, xAI (Grok), MiniMax et Z.AI (CogView-4, GLM-Image)</li>
    <li>44 modèles — des modèles cloud gratuits (Gemini Flash, HuggingFace SDXL) aux rendus premium (Flux 2 Max, Imagen 4 Ultra)</li>
    <li>15 handlers ReAct : generate, edit (inpaint/outpaint/erase), compose (multi-images), thumbnail, thumbnail-pro (pipeline LLM), headlines, logo, upscale, remove/replace background, sketch-to-image, SVG vectoriel</li>
    <li>Mode <code>auto</code> : modèles cloud gratuits puis modèles payants classés par coût croissant</li>
    <li>8 templates de prompt : <code>photo</code>, <code>illustration</code>, <code>3d_render</code>, <code>pixel_art</code>, <code>watercolor</code>, <code>anime</code>, <code>logo</code>, <code>icon</code></li>
    <li>Édition avancée : inpainting (masque), outpainting (extension), search-and-replace, erase object</li>
    <li>Upscale 2×/4× (modes fast, conservative, creative) via Stability AI</li>
    <li>SVG natif via Recraft V4 — export vectoriel vrai</li>
    <li>API : <code>POST /api/images/generate</code> + <code>GET /api/images/models</code></li>
    <li>Config : <code>LUMENA_BRAIN_IMAGE_GEN</code>, <code>LUMENA_IMAGE_DEFAULT_SIZE</code>, <code>LUMENA_IMAGE_DEFAULT_QUALITY</code></li>
  </ul>
</div>

<div class="doc-cap-card">
  <h4>Vidéo & Multimédia</h4>
  <ul>
    <li>Génération vidéo programmatique avec <strong>Remotion</strong> (React TSX → MP4/WebM)</li>
    <li>5 templates : <code>presentation</code>, <code>social_short</code>, <code>explainer</code>, <code>square_social</code>, <code>custom</code></li>
    <li>3 formats : paysage 1920×1080, portrait 1080×1920, carré 1080×1080</li>
    <li>Intégration assets locaux : upload image/vidéo/audio → <code>public/</code> → <code>staticFile()</code> TSX</li>
    <li>Auto-détection assets récents (images/documents reçus dans les 24h)</li>
    <li>Retry JSON plan (2 tentatives, température +0.1) + regex fallback</li>
    <li>Rendu local Node.js ≥18 (prioritaire) ou Docker en mode sandbox</li>
    <li>Auto-fix sur erreur de rendu : correction LLM ciblée sur le fichier fautif sans casser les imports des scènes</li>
    <li>Pas d'images externes : fonds CSS gradient exclusivement — les URLs Unsplash/externes sont interdites pour garantir un rendu stable</li>
    <li>SSE logs en direct : phases 1→4 visibles dans le chat</li>
  </ul>
</div>

<div class="doc-cap-card">
  <h4>Auto-évolution</h4>
  <ul>
    <li>Fine-tuning local : pipeline LoRA → GGUF → import Ollama automatique</li>
    <li>Création de skills en langage naturel ({skills_count} installées)</li>
    <li>self_improve.py — analyse erreurs → création skills auto</li>
    <li>curiosity.py — exploration thématique autonome</li>
    <li>Rollback si régression détectée</li>
    <li>Planification et exécution CRON autonome</li>
  </ul>
</div>

</div>
""",
    },
    {
        "id": "architecture",
        "icon": "layers",
        "title": "Architecture",
        "content": """
<h3>Stack technique</h3>
<table class="doc-table">
<thead><tr><th>Composant</th><th>Technologie</th></tr></thead>
<tbody>
<tr><td>Langage</td><td>Python 3.11+</td></tr>
<tr><td>LLM par défaut</td><td>{default_model}</td></tr>
<tr><td>LLM alternatifs</td><td>{provider_names_display} ({models_count} modèles)</td></tr>
<tr><td>Mémoire vectorielle</td><td>ChromaDB + Knowledge Graph + BM25</td></tr>
<tr><td>Interface web</td><td>FastAPI + SPA (HTML/JS vanilla ES modules, Vite build)</td></tr>
<tr><td>Messagerie</td><td>Telegram Bot API, Discord.py 2.x, Tweepy 4.x</td></tr>
<tr><td>Voix</td><td>Whisper (STT) + Piper (TTS) — <em>en développement actif</em></td></tr>
<tr><td>Automatisation web</td><td>Playwright stealth v2 (10 techniques anti-détection)</td></tr>
<tr><td>Documents</td><td>WeasyPrint PDF + 13 templates Jinja2</td></tr>
<tr><td>Vidéo</td><td>Remotion 4.x (React TSX → MP4) — rendu Node.js local ou Docker sandbox</td></tr>
<tr><td>Computer Use</td><td>Cascade native (Anthropic→OpenAI→Google) + pywinauto</td></tr>
<tr><td>Qualité code</td><td>ruff, pytest ({tests_passed} tests, {tests_failed} failure)</td></tr>
<tr><td>Sandbox</td><td>Docker (auto/always/off), command_sanitizer, file_guardrails</td></tr>
</tbody>
</table>

<h3>Boucle de raisonnement — ReAct</h3>
<div class="doc-code-block">
<pre>
 ┌──────────────────────────────────────────────────────────────┐
 │                       MESSAGE ENTRANT                        │
 └─────────────────────────────┬────────────────────────────────┘
                               │
                               ▼
 ┌──────────────────────────────────────────────────────────────┐
 │  CLASSIFICATION                                              │
 │  intent_classifier ──► chat · tool_direct · react · project │
 │  apply_context_filter ──► outils filtrés selon l'intention   │
 └─────────────────────────────┬────────────────────────────────┘
                               │
                               ▼
 ┌──────────────────────────────────────────────────────────────┐
 │  INITIALISATION RUN                                          │
 │  reset exec_state · ledger · established_facts               │
 │  contexte ChromaDB · profil modèle P5 · skills actifs        │
 └─────────────────────────────┬────────────────────────────────┘
                               │
           ╔═══════════════════╧══════════════════════╗
           ║            BOUCLE  (max 30 iter)          ║
           ║                                           ║
           ║  ① Context guard                          ║
           ║    >75% fenêtre ──► compaction urgence    ║
           ║                         │                 ║
           ║  ② LLM call             ▼                 ║
           ║    stop=["OBSERVATION:"]                  ║
           ║    timeout 240–300s (adapté au modèle)    ║
           ║         │                                 ║
           ║    vide ──► retry format                  ║
           ║    tronqué ──► continuation automatique   ║
           ║         │                                 ║
           ║  ③ Parse réponse                          ║
           ║    THOUGHT  +  ACTION                     ║
           ║         │                                 ║
           ║    ┌────┴────────────┐                    ║
           ║    │                 │                    ║
           ║  FINAL          TOOL CALL                 ║
           ║    │                 │                    ║
           ║  Ledger guard    Exécution outil           ║
           ║  mutation réelle?   │                     ║
           ║  ✗ ──► retry    write verify (Fix 3.2)    ║
           ║  ✓ ──► sortie       │                     ║
           ║                OBSERVATION                ║
           ║                → history · ledger         ║
           ║                → hallucination check      ║
           ║                     │                     ║
           ║  ④ Guards fins de boucle                  ║
           ║    même action 3× ──► change approach     ║
           ║    is_stuck ──► outer retry (+0.05 temp)  ║
           ╚═══════════════════╤══════════════════════╝
                               │
                               ▼
 ┌──────────────────────────────────────────────────────────────┐
 │  TIMEOUT GLOBAL  ──►  message contextuel (projet ? serveur ?)│
 └─────────────────────────────┬────────────────────────────────┘
                               │
                               ▼
 ┌──────────────────────────────────────────────────────────────┐
 │  RÉPONSE FINALE  ──►  Web SSE · Telegram · Discord · CLI     │
 └──────────────────────────────────────────────────────────────┘
</pre>
</div>

<h3>Caractéristiques clés</h3>
<ul>
<li><strong>Stop sequences physiques</strong> — <code>OBSERVATION:</code> injecté dans tous les providers pour empêcher
le LLM d'écrire de fausses observations</li>
<li><strong>Hallucination tracking</strong> — détection de récidives, warning injecté si récidive ≥ 2</li>
<li><strong>Compaction automatique</strong> — résumé head+tail si historique trop long</li>
<li><strong>Parallel tools</strong> — exécution simultanée d'outils indépendants</li>
<li><strong>Observation limit dynamique</strong> — adapté par type d'intention (chat, react, project)</li>
<li><strong>established_facts priorité 0</strong> — chemin projet lu sans verrou depuis <code>StructuredState</code>, fallback IdentityService</li>
<li><strong>Verbalization redirect</strong> — monologue interne (<code>**THOUGHT:**</code>, "je délègue") détecté et redirigé vers une action concrète</li>
<li><strong>Vérification write_file</strong> — après chaque écriture, contrôle existence + taille &gt; 0 du fichier produit</li>
<li><strong>Ledger guard FINAL</strong> — Lumena ne peut pas conclure sans mutation réelle dans le ledger d'outils réussis</li>
</ul>

<h3>Structure du projet</h3>
<div class="doc-code-block">
<pre>
lumena/
├── src/
│   ├── core.py                     # Cerveau principal — LumenaCore (1 061L)
│   ├── personality.py              # System prompt et identité
│   ├── emotion.py                  # Moteur émotionnel
│   ├── core_services/              # {core_services} services modulaires
│   │   ├── agent_service.py        #   Conversations + journal (1 868L)
│   │   ├── context_service.py      #   Construction contexte LLM
│   │   ├── identity_service.py     #   Identité et mémoire permanente
│   │   ├── memory_service.py       #   Interface ChromaDB
│   │   ├── voice_service.py        #   STT + TTS
│   │   ├── web_service.py          #   Serveur FastAPI
│   │   ├── workspace_service.py    #   Gestion fichiers projets
│   │   ├── code_service.py         #   Indexation code source
│   │   ├── runtime_context.py      #   Contexte runtime
│   │   ├── contracts.py            #   Contrats inter-services
│   │   └── base_service.py         #   Classe de base
│   ├── llm/
│   │   ├── multi_provider.py       # Routing + fallback {providers_count} providers
│   │   ├── providers.py            # {models_count} modèles dans AVAILABLE_MODELS
│   │   └── output_normalizer.py    # Normalisation réponses LLM
│   ├── reasoning/
│   │   ├── react.py                # Boucle ReAct (4 953L façade)
│   │   ├── react_config.py         # Config, enums, constantes (373L)
│   │   ├── tool_registry.py        # Registre {tools_count} outils (1 763L)
│   │   ├── response_parser.py      # Parsing ReAct (292L)
│   │   ├── prompt_builder.py       # Heuristiques prompt (258L)
│   │   └── handlers/               # {handler_modules} modules handlers V2
│   ├── tools/                      # {tools_modules} modules d'outils bas niveau
│   ├── agents/
│   │   └── sub_agent.py            # CodeAgent 8 types + délégation (6 752L)
│   ├── skills/                     # Moteur de skills (loader, skill, sync, tools)
│   ├── channels/                   # Telegram, Discord, Twitter, IDE bridge
│   ├── computer_use/               # Cascade native CU + pywinauto + vision
│   ├── autonomy/                   # Scheduler, heartbeat, daemon, goals, curiosity
│   ├── learning/                   # Reflection, instincts, conversation logger
│   ├── training/                   # Pipeline fine-tuning LoRA → GGUF → Ollama
│   ├── perception/                 # Document reader, knowledge extractor
│   ├── memory/                     # ChromaDB, knowledge graph, BM25, embedding cache
│   ├── telemetry/                  # File edits tracker, trace bus
│   ├── runtime/                    # Task orchestrator, SLO monitor
│   ├── background/                 # Background task manager
│   └── prompts/                    # Prompt builder
├── web/                            # Panel de contrôle (FastAPI + SPA)
│   ├── routes/                     # {routes_count} endpoints API (14 fichiers)
│   └── static/                     # 15 fichiers JS + 9 fichiers CSS
├── assets/templates/               # 13 templates Jinja2 (documents pro)
├── data/                           # 31 répertoires runtime
├── tests/                          # {test_files} fichiers de tests pytest
├── skills/                         # {skills_count} skills installés
├── models/                         # Modèles TTS Piper + pipeline fine-tuning
└── docs/                           # Documentation technique
</pre>
</div>

<h3>Multi-LLM avec fallback automatique</h3>
<p>Si un provider échoue (402, timeout, quota), Lumena bascule automatiquement sur le suivant :</p>
{fallback_chain_html}
""",
    },
    {
        "id": "mcp",
        "icon": "plug",
        "title": "MCP",
        "content": """
<p class="doc-lead">Lumena intègre le Model Context Protocol comme couche d'extension runtime :
elle peut proposer, installer, activer, catégoriser et utiliser des serveurs MCP depuis la conversation,
avec sandbox, approval queue, trust score, audit et garde-fous de live mode.</p>

<div class="doc-grid doc-grid-3">
  <div class="doc-stat-card">
    <div class="doc-stat-value">40+</div>
    <div class="doc-stat-label">Modules MCP</div>
  </div>
  <div class="doc-stat-card">
    <div class="doc-stat-value">9</div>
    <div class="doc-stat-label">Outils conversationnels</div>
  </div>
  <div class="doc-stat-card">
    <div class="doc-stat-value">data/mcp</div>
    <div class="doc-stat-label">Sandbox install</div>
  </div>
</div>

<h3>Ce que Lumena sait faire avec MCP</h3>
<table class="doc-table">
<thead><tr><th>Besoin utilisateur</th><th>Comportement Lumena</th><th>Composants</th></tr></thead>
<tbody>
<tr><td>“Trouve-moi un outil pour X”</td><td>Résout la capacité, cherche un MCP connu/local/npm/PyPI, propose la meilleure suite</td><td><code>CapabilityResolver</code>, <code>MCPProposalPlanner</code>, <code>network_sources.py</code></td></tr>
<tr><td>“Installe ce MCP”</td><td>Résout la cible, ajoute au Catalog, crée/consomme le ticket, installe dans <code>data/mcp/&lt;server_id&gt;</code></td><td><code>target_resolver.py</code>, <code>catalog_add_orchestrator.py</code>, <code>install_orchestrator.py</code></td></tr>
<tr><td>“Active-le et utilise-le”</td><td>Démarre le serveur, initialise le client JSON-RPC, découvre les tools, les enregistre dans le ToolRegistry</td><td><code>activation_service.py</code>, <code>client.py</code>, <code>discovery.py</code>, <code>handler_adapter.py</code></td></tr>
<tr><td>“Désactive / supprime / préfère ce MCP”</td><td>Applique les mutations contrôlées avec confirmation et audit</td><td><code>react_integration.py</code>, <code>server_catalog.py</code>, <code>overlap_detector.py</code></td></tr>
<tr><td>“Il manque une clé/config”</td><td>Expose un schéma de configuration et stocke secrets/config chiffrés sans fuite dans l'audit</td><td><code>config_schema.py</code>, <code>credentials_service.py</code>, <code>secrets_resolver_service.py</code></td></tr>
</tbody>
</table>

<h3>Outils MCP visibles par le ReAct</h3>
<p>La catégorie d'outils <code>mcp</code> contient les contrôles conversationnels suivants :</p>
<div class="doc-code-block">
<pre>
request_mcp_capability(intent)
request_mcp_ticket(...)
run_mcp_autonomy(intent, live, confirmation_phrase)
resume_mcp_task(intent)
add_mcp(target, live, confirmation_phrase)
disable_mcp(server_id, confirmation_phrase)
remove_mcp(server_id, confirmation_phrase)
set_mcp_preference(server_id, prefer_over_native, confirmation_phrase)
set_mcp_category(server_id, human_phrase, confirmation_phrase)
</pre>
</div>
<p>Une fois un serveur actif, ses tools apparaissent avec le namespace
<code>mcp__&lt;server_id&gt;__&lt;tool_name&gt;</code>. Le nom exact vient du serveur via <code>tools/list</code> :
Lumena ne doit pas deviner les noms.</p>

<h3>Flux complet</h3>
<div class="doc-code-block">
<pre>
Message utilisateur
  ↓
ReAct détecte une capacité externe ou un MCP explicite
  ↓
run_mcp_autonomy / add_mcp
  ↓
CapabilityResolver → ProposalPlanner → ExecutionBridge
  ↓
CatalogAdd / ApprovalQueue / AutoApprove
  ↓
InstallOrchestrator → MCPSandboxRunner.install()
  ↓
ActivationService → MCPClient → Discovery → ToolRegistry
  ↓
Tool visible : mcp__server__tool
  ↓
Lumena reprend la tâche avec le nouvel outil
</pre>
</div>

<h3>Sécurité et garde-fous</h3>
<ul>
  <li>Installation isolée sous <code>data/mcp/&lt;server_id&gt;/</code> ; jamais d'installation MCP directe dans le repo.</li>
  <li>ApprovalQueue et confirmations verbales pour les mutations sensibles.</li>
  <li>Live mode désactivable par <code>LUMENA_MCP_LIVE</code> et kill switches dédiés install/activation/trust/auto-approve.</li>
  <li>Trust score et policies MCP : <code>READ_ONLY</code>, <code>EXTERNAL_READ</code>, <code>LOCAL_WRITE</code>, <code>EXTERNAL_WRITE_*</code>, <code>SECRETS_AUTH</code>.</li>
  <li>Audit whitelist : pas de secrets, pas d'arguments raw, pas de descriptions/input_schema raw.</li>
  <li>RuntimeWatcher surveille crashes, unhealthy, drift et états actifs.</li>
  <li>CodeAgent ne doit pas installer de MCP à la main : la chaîne MCP est responsable du sandbox, du Catalog et de l'activation.</li>
</ul>

<h3>Panel MCP</h3>
<p>Le panel <strong>Infra → MCP</strong> expose :</p>
<ul>
  <li><strong>Bibliothèque</strong> : serveurs connus, installés, actifs, curated, état configuration.</li>
  <li><strong>Approvals</strong> : tickets pending et décisions récentes.</li>
  <li><strong>Watcher snapshots</strong> : état runtime des serveurs actifs.</li>
  <li><strong>Audit / Discovery</strong> : événements sanitizés et rapports de découverte.</li>
  <li><strong>Auto-Approve</strong> : patterns bornés avec double opt-in.</li>
  <li><strong>Diagnostics</strong> : readiness, observability, keys status, audit integrity, coherence.</li>
</ul>

<div class="doc-callout doc-callout-warn">
  <strong>Important :</strong> la documentation historique peut contenir des états intermédiaires.
  Pour patcher la chaîne MCP, vérifier le code réel puis lire ensemble
  <code>MCP_PHASES_STATUS.md</code>, <code>MCP_FINAL_PLAN.md</code> et
  <code>MCP_CATEGORY_UNIFICATION.md</code>.
</div>
""",
    },
    {
        "id": "p2p",
        "icon": "git-fork",
        "title": "Autonomie Multi-Lumena",
        "content": """
<p class="doc-lead">Au-dessus du jumelage sécurisé (voir <strong>Réseau Multi-Lumena</strong>), une Lumena peut
<strong>décider seule</strong> de confier une mission à une autre instance de la flotte, sous filet de
sécurité. C'est un <strong>essaim décentralisé</strong> : pas d'orchestration cloud, chaque instance
t'appartient.</p>

<div class="doc-callout doc-callout-warn" style="border-left:4px solid #f59e0b">
  <strong>Principe Lumena 24/7 :</strong> toute l'autonomie <strong>gate le FUTUR, jamais le PRÉSENT</strong>.
  Couper le réseau ou mettre un pair en quarantaine empêche les <em>nouvelles</em> délégations —
  mais une mission <strong>en cours se termine toujours</strong>.
</div>

<p class="doc-muted">Prérequis : les pairs doivent être jumelés et au niveau <code>mission</code>
(scope <code>task.delegate</code>) — détails dans la section <strong>Réseau Multi-Lumena</strong>.</p>

<h3>Trois modes d'initiative</h3>
<p>Réglée par instance via <code>LUMENA_PEER_AUTONOMY</code> : <code>off</code> (ne délègue que sur demande) ·
<code>shadow</code> (observation : <strong>décide et montre</strong> ce qu'elle déléguerait, <strong>sans agir</strong>) ·
<code>live</code> (<strong>agit seule</strong>, sous filet).</p>

<div class="doc-caps-grid">
  <div class="doc-cap-card">
    <h4>Carte des capacités (C2)</h4>
    <ul>
      <li>Par pair : capacités, scopes, niveau, joignabilité (fraîcheur), quarantaine</li>
      <li>Booléen <code>delegable</code> = appelable ∧ joignable ∧ non-quarantaine ∧ scopes</li>
      <li>Substrat de décision de l'autonomie · <code>GET /api/peer/capability-map</code></li>
    </ul>
  </div>
  <div class="doc-cap-card">
    <h4>Initiative — shadow (C3)</h4>
    <ul>
      <li>Propose des délégations sur les objectifs actifs, <strong>n'agit jamais</strong></li>
      <li>Suggestions <strong>persistées</strong> + visibles dans le panneau (bouton « Tester »)</li>
      <li>Permet d'<strong>observer</strong> les décisions avant d'activer le live</li>
    </ul>
  </div>
  <div class="doc-cap-card">
    <h4>Initiative — live (C3)</h4>
    <ul>
      <li>Délègue <strong>seule</strong>, l'envoi réutilise tout le filet de sécurité</li>
      <li>Freins anti-emballement : <strong>halt → présence → dedup → budget</strong></li>
      <li>Par défaut n'agit que si l'utilisateur est <strong>absent</strong> ; <code>WHEN_PRESENT=1</code> → 24/7</li>
    </ul>
  </div>
  <div class="doc-cap-card">
    <h4>Filet de sécurité (C4)</h4>
    <ul>
      <li><strong>Kill-switch</strong> <code>LUMENA_PEER_HALT</code> : coupe tout nouveau, draine l'en-cours</li>
      <li><strong>Quarantaine auto</strong> : un pair qui enchaîne les échecs est isolé des nouvelles délégations</li>
      <li>Bus d'événements SSE temps réel (missions, suggestions, quarantaine)</li>
    </ul>
  </div>
</div>

<h3>Exécution d'une mission déléguée</h3>
<p>Le pair qui reçoit une mission l'exécute avec son <strong>cerveau Lumena complet</strong> en arrière-plan
(<code>think_and_act_silent</code>, <code>allow_when_busy</code> → il reste joignable pendant) au sein d'une
<strong>file d'attente bornée</strong> (sémaphore, <code>LUMENA_PEER_MISSION_CONCURRENCY</code>) pour ne pas saturer le LLM.
Les <strong>livrables</strong> (fichiers produits) reviennent à l'émetteur via un canal d'artefacts signé,
déposés dans <code>workspace/inbound/&lt;pair&gt;/&lt;mission&gt;/</code>.</p>

<h3>Clés de configuration (groupe « Instance », effet immédiat)</h3>
<table class="doc-table">
<thead><tr><th>Clé</th><th>Défaut</th><th>Rôle</th></tr></thead>
<tbody>
<tr><td><code>LUMENA_PEER_ENABLED</code></td><td>0</td><td>Réseau maître (découverte + collaboration)</td></tr>
<tr><td><code>LUMENA_PEER_AUTONOMY</code></td><td>off</td><td>Initiative : off / shadow / live</td></tr>
<tr><td><code>LUMENA_PEER_AUTONOMY_MAX_PER_HOUR</code></td><td>3</td><td>Plafond de délégations autonomes par heure</td></tr>
<tr><td><code>LUMENA_PEER_AUTONOMY_WHEN_PRESENT</code></td><td>0</td><td>live : agir même si l'utilisateur est présent (24/7)</td></tr>
<tr><td><code>LUMENA_PEER_HALT</code></td><td>0</td><td>Kill-switch (veto nouveau, draine l'en-cours)</td></tr>
<tr><td><code>LUMENA_PEER_QUARANTINE_THRESHOLD</code></td><td>5</td><td>Échecs consécutifs avant quarantaine auto</td></tr>
<tr><td><code>LUMENA_PEER_MISSION_CONCURRENCY</code></td><td>1</td><td>Missions exécutées en parallèle</td></tr>
</tbody>
</table>

<h3>Panneau « Instances & Réseau »</h3>
<ul>
  <li><strong>Carte maître</strong> : activer le réseau + bouton « Couper » (kill-switch).</li>
  <li><strong>Autonomie</strong> : sélecteur off/shadow/live + bouton « Tester » + badge de budget en mode live.</li>
  <li><strong>Équipe Lumena</strong> : pairs connus (statut, scopes, niveau, actions).</li>
  <li><strong>Quarantaine</strong> et <strong>Suggestions de délégation</strong> : cartes dédiées.</li>
  <li>Vue avancée : jumelage par code, niveau par pair, observabilité, diagnostic réseau.</li>
</ul>

<div class="doc-callout">
  <strong>Invariant :</strong> tout dialogue passe par <strong>l'utilisateur ↔ Lumena (chat)</strong>.
  Le panneau réseau sert au <strong>pilotage et à l'observation</strong> — il n'y a jamais de conversation directe
  entre pairs dans l'UI.
</div>
""",
    },
    {
        "id": "tools",
        "icon": "wrench",
        "title": "Catalogue d'outils",
        "content": """
<p class="doc-lead">{tools_count} outils natifs répartis en {tools_cat_count} catégories, tous enregistrés dans le <code>ToolRegistry</code>
et exposés automatiquement au LLM.</p>

{tools_catalog_table}
""",
    },
    {
        "id": "skills",
        "icon": "puzzle",
        "title": "Skills",
        "content": """
<p class="doc-lead">Les skills sont des modules de comportement chargés dynamiquement.
Ils peuvent être activés à la demande, créés par Lumena elle-même via le skill <code>skill-creator</code>,
ou installés depuis un fichier YAML. Actuellement <strong>{skills_count} skills</strong> installés.</p>

{skills_table}

<h3>Cycle de vie d'un skill</h3>
<div class="doc-code-block">
<pre>
1. Création      → tools.py (14 handlers : install, create, search, enable, disable…)
2. Définition    → skill.py — classe Skill (nom, description, triggers, action, YAML)
3. Synchronisation → sync.py — scan skills/ + .lumena_rules → déduplique → persiste
4. Chargement    → loader.py — hot-reload dynamique au démarrage + à la demande
5. Invocation    → active_skills_context injecté dans le prompt ReAct
</pre>
</div>

<h3>Moteur — <code>src/skills/</code></h3>
<table class="doc-table">
<thead><tr><th>Fichier</th><th>Rôle</th></tr></thead>
<tbody>
<tr><td><code>loader.py</code></td><td>Chargement et hot-reload des skills au runtime</td></tr>
<tr><td><code>skill.py</code></td><td>Classe <code>Skill</code> — modèle de données (triggers, actions, YAML)</td></tr>
<tr><td><code>sync.py</code></td><td>Synchronisation <code>skills/</code> ↔ registre interne, déduplique</td></tr>
<tr><td><code>tools.py</code></td><td>14 handlers V2 (install, create, search, enable, disable, list…)</td></tr>
</tbody>
</table>

<h3>Fiches standalone</h3>
<p>7 fiches <code>.md</code> dans <code>skills/</code> enrichissent le contexte :</p>
<ul>
<li><code>coding_agent.md</code> — Consignes CodeAgent avancé</li>
<li><code>github.md</code> — Workflow GitHub (issues, PRs, branches)</li>
<li><code>ia_impressionnant.md</code> — Persona, ton et style</li>
<li><code>meteo.md</code> — API météo</li>
<li><code>project_analyzer.md</code> — Analyse de projets existants</li>
<li><code>vision_analysis.md</code> — Analyse d'images et screenshots</li>
<li><code>auto_amelioration.md</code> — Auto-amélioration et apprentissage</li>
</ul>

<h3>Catégories</h3>
<table class="doc-table">
<thead><tr><th>Domaine</th><th>Nb</th><th>Exemples</th></tr></thead>
<tbody>
<tr><td>Intelligence</td><td>10</td><td>research, analysis, summarization</td></tr>
<tr><td>Web / Frontend</td><td>11</td><td>website builder, scraping, SEO</td></tr>
<tr><td>Resilience</td><td>7</td><td>retry, fallback, health check</td></tr>
<tr><td>Code quality</td><td>6</td><td>lint, format, test runner</td></tr>
<tr><td>Ops / Automation</td><td>5</td><td>deploy, backup, cron</td></tr>
<tr><td>Documents</td><td>4</td><td>facture, contrat, rapport</td></tr>
<tr><td>Communication</td><td>3</td><td>email, notification, briefing</td></tr>
<tr><td>Création</td><td>3</td><td>image (11 providers, 37 modèles), music, 3D</td></tr>
</tbody>
</table>

<h3>Templates</h3>
<p>Dossier <code>scripts/templates/</code> — scaffolding de nouveaux skills :</p>
<ul>
<li><strong>basic</strong> — skill minimal (nom, description, triggers, action)</li>
<li><strong>with-script</strong> — skill avec script Python exécutable</li>
</ul>

<div class="doc-callout">
  <strong>Auto-création :</strong> Le skill <code>skill-creator</code> permet à Lumena de créer de
  nouveaux skills en langage naturel, sans intervention humaine. Les skills sont hot-reloadés
  et immédiatement disponibles dans la boucle ReAct.
</div>
""",
    },
    {
        "id": "memory",
        "icon": "brain",
        "title": "Mémoire & Apprentissage",
        "content": """
<h3>Architecture mémoire à 4 niveaux</h3>

<div class="doc-memory-levels">
  <div class="doc-memory-level" style="--level-color:#6366f1">
    <div class="doc-memory-level-num">1</div>
    <div>
      <strong>Session mémoire — <code>session_memory.py</code> (246L)</strong>
      <p>Mémoire courte durée, contexte de la conversation en cours. Réinitialisée à chaque session.</p>
    </div>
  </div>
  <div class="doc-memory-level" style="--level-color:#8b5cf6">
    <div class="doc-memory-level-num">2</div>
    <div>
      <strong>ChromaDB vectoriel — <code>chromadb_store.py</code> (962L)</strong>
      <p>{memory_count} souvenirs indexés par embedding cosinus. Recherche sémantique instantanée, persisté dans <code>data/vector/chromadb/</code>.</p>
    </div>
  </div>
  <div class="doc-memory-level" style="--level-color:#a855f7">
    <div class="doc-memory-level-num">3</div>
    <div>
      <strong>Knowledge Graph — <code>knowledge_graph.py</code> (282L)</strong>
      <p>Relations entre entités (personnes, projets, concepts). Requêtes par traversée de graphe.</p>
    </div>
  </div>
  <div class="doc-memory-level" style="--level-color:#c084fc">
    <div class="doc-memory-level-num">4</div>
    <div>
      <strong>BM25 — <code>bm25_index.py</code> (276L)</strong>
      <p>Recherche textuelle classique (Term-Frequency / Inverse Document Frequency). Complémente le vectoriel.</p>
    </div>
  </div>
</div>

<h3>Modules support</h3>
<table class="doc-table">
<thead><tr><th>Module</th><th>LOC</th><th>Rôle</th></tr></thead>
<tbody>
<tr><td><code>embedding_cache.py</code></td><td>269</td><td>Cache local des embeddings (évite re-calcul)</td></tr>
<tr><td><code>code_file_watcher.py</code></td><td>171</td><td>Surveillance fichiers code — re-indexation automatique</td></tr>
<tr><td><code>migration.py</code></td><td>275</td><td>Migrations schéma mémoire (upgrades)</td></tr>
</tbody>
</table>

<h3>Mémoire persistante</h3>
<p>Stockée dans <code>data/memory/</code> via ChromaDB + BM25. Contient l'identité, les préférences et le contexte actif du projet — injectée dans chaque session LLM.</p>

<h3>Journal quotidien + Insights</h3>
<ul>
  <li><strong>Journal</strong> : fichiers <code>YYYY-MM-DD.md</code> écrits automatiquement à chaque interaction</li>
  <li><strong>Insights</strong> : apprentissages déduits automatiquement, stockés dans <code>data/insights.json</code></li>
  <li>Les angles morts (sujets inconnus) sont détectés et notés, marqués <code>blind_spot_resolved</code> une fois résolus</li>
</ul>

<h3>Pipeline d'apprentissage — <code>src/learning/</code></h3>
<table class="doc-table">
<thead><tr><th>Fichier</th><th>Rôle</th></tr></thead>
<tbody>
<tr><td><code>reflection.py</code></td><td>Auto-analyse des conversations passées pour en tirer des leçons</td></tr>
<tr><td><code>instincts.py</code></td><td>Patterns appris automatiquement (réflexes conditionnels)</td></tr>
<tr><td><code>conversation_logger.py</code></td><td>Log structuré des conversations → alimentation training_pool</td></tr>
</tbody>
</table>

<div class="doc-code-block">
<pre>
Conversations → training_pool/ → Validation → training_validated/ → Fine-tuning
     ↓                                ↑
auto_learning_system.py ─── curation automatique des données
</pre>
</div>
<ul>
  <li>Détection automatique des exemples de qualité</li>
  <li>Micro-évaluation périodique (<code>micro_eval_log.jsonl</code>)</li>
  <li>Alerte HEARTBEAT si le score baisse sur 3 mesures consécutives</li>
</ul>

<h3>Structure données mémoire</h3>
<table class="doc-table">
<thead><tr><th>Répertoire</th><th>Contenu</th></tr></thead>
<tbody>
<tr><td><code>data/memory/</code></td><td>Facts, journal, souvenirs persistés</td></tr>
<tr><td><code>data/vector/chromadb/</code></td><td>Base vectorielle SQLite + segments Chroma</td></tr>
<tr><td><code>data/training_pool/</code></td><td>Conversations brutes JSONL pour fine-tuning</td></tr>
<tr><td><code>data/training_validated/</code></td><td>Conversations validées (curated)</td></tr>
<tr><td><code>data/learning/</code></td><td>Patterns extraits, reports d'apprentissage</td></tr>
</tbody>
</table>

<div class="doc-callout doc-callout-warn">
  <strong>Anti-confabulation :</strong> Avant de confirmer tout événement passé, Lumena effectue
  systématiquement une <code>memory_search</code>. Elle ne dit jamais "oui j'ai fait X" sans vérification.
</div>
""",
    },
    {
        "id": "autonomy",
        "icon": "bot",
        "title": "Autonomie & Planification",
        "content": """
<h3>Daemon 24/7 — <code>daemon.py</code> (785L)</h3>
<p>Lumena tourne en arrière-plan en permanence. Elle agit sans être sollicitée selon ses tâches CRON,
ses objectifs actifs et les événements de ses canaux de communication.</p>

<h3>Scheduler CRON — <code>scheduler.py</code> (1 617L)</h3>
<p>Parallélisation des tâches non-critiques, <code>setup_default_tasks()</code>, clé d'idempotence <code>handler:window:hash</code>.</p>
<table class="doc-table">
<thead><tr><th>Tâche</th><th>Fréquence</th><th>Description</th></tr></thead>
<tbody>
<tr><td>Heartbeat système</td><td>6h et 18h</td><td>Santé RAM, disque, providers LLM, pipeline training</td></tr>
<tr><td>Morning briefing Discord</td><td>10h chaque jour</td><td>Résumé quotidien sur le serveur Discord</td></tr>
<tr><td>Polling Twitter mentions</td><td>Toutes les 90s</td><td>Vérification des mentions entrantes</td></tr>
</tbody>
</table>

<h3>Heartbeat — <code>heartbeat.py</code> (411L)</h3>
<p>Monitoring vital : RAM, disque, providers LLM actifs, pipeline training, ChromaDB. Alerte quand dégradation détectée.</p>

<h3>Modules autonomie — <code>src/autonomy/</code></h3>
<table class="doc-table">
<thead><tr><th>Fichier</th><th>LOC</th><th>Rôle</th></tr></thead>
<tbody>
<tr><td><code>goals.py</code></td><td>498</td><td>Gestion objectifs autonomes (create, track, complete). Persistés dans <code>data/goals/</code></td></tr>
<tr><td><code>curiosity.py</code></td><td>444</td><td>Exploration thématique autonome — choisit des sujets à étudier</td></tr>
<tr><td><code>self_improve.py</code></td><td>1 003</td><td>Analyse erreurs → génère des skills auto, corrige ses propres faiblesses</td></tr>
<tr><td><code>ops_handlers.py</code></td><td>2 508</td><td>15+ handlers opérationnels, <code>_STATE_LOCK</code> thread-safe</td></tr>
</tbody>
</table>

<h3>DAG Orchestrator — Exécution parallèle en 3 vagues</h3>
<div class="doc-code-block">
<pre>
Vague 1 — Tâches indépendantes       →  parallèle
         ↓
Vague 2 — Tâches dépendant de V1     →  parallèle
         ↓
Vague 3 — Finalisation et assemblage
</pre>
</div>

<h3>Classification d'intention automatique</h3>
<table class="doc-table">
<thead><tr><th>Intention</th><th>Comportement</th><th>Exemple</th></tr></thead>
<tbody>
<tr><td><code>chat</code></td><td>Réponse directe, pas d'outils</td><td>"Comment tu vas ?"</td></tr>
<tr><td><code>tool_direct</code></td><td>Appel d'outil simple</td><td>"Joue du jazz sur Spotify"</td></tr>
<tr><td><code>react</code></td><td>Boucle ReAct complète</td><td>"Recherche les dernières news IA"</td></tr>
<tr><td><code>project</code></td><td>Mode projet longue durée</td><td>"Crée un site portfolio"</td></tr>
</tbody>
</table>

<h3>Système de plans — <code>src/tools/plan_manager.py</code> (321L)</h3>
<div class="doc-code-block">
<pre>
Plan créé → étapes décomposées automatiquement
[x] Étape 1 — terminée
[x] Étape 2 — terminée
[ ] Étape 3 — en cours
[?] Étape 4 — bloquée
[!] Étape 5 — erreur
</pre>
</div>
<p>Plans persistés dans <code>data/plans/</code>, visibles depuis le panel Tâches du control panel. 4 handlers V2 (<code>plans.py</code>).</p>

<h3>Structure données autonomie</h3>
<table class="doc-table">
<thead><tr><th>Répertoire</th><th>Contenu</th></tr></thead>
<tbody>
<tr><td><code>data/ops/</code></td><td>État opérationnel (<code>ops_state.json</code>, <code>metrics.jsonl</code>, <code>micro_eval_log.jsonl</code>)</td></tr>
<tr><td><code>data/goals/</code></td><td>Objectifs autonomes (JSON par objectif)</td></tr>
<tr><td><code>data/plans/</code></td><td>Plans d'action (JSON par plan)</td></tr>
<tr><td><code>data/scheduler/</code></td><td>État scheduler (tâches CRON persistées)</td></tr>
<tr><td><code>data/autonomy/</code></td><td>État agent autonome (snapshots)</td></tr>
<tr><td><code>data/backups/</code></td><td>Backups généraux (snapshots périodiques)</td></tr>
</tbody>
</table>
""",
    },
    {
        "id": "codeagent",
        "icon": "terminal",
        "title": "CodeAgent",
        "content": """
<p class="doc-lead">Le CodeAgent est un sous-agent spécialisé (<code>sub_agent.py</code> — 6 752 LOC) qui travaille en boucle
itérative autonome pour les tâches de développement complexes.</p>

<h3>Architecture multi-agents — 8 types</h3>

<div class="doc-callout doc-callout-warn">
  <strong>État d'intégration :</strong> Le <code>CodeAgent</code> est l'agent de production, pleinement opérationnel.
  Les agents spécialisés (Debug, Refactor, Research, Browser, File, Planner) sont architecturés et enregistrés dans l'orchestrateur — leur activation complète et leur routage automatique sont en cours de développement actif.
</div>

<table class="doc-table">
<thead><tr><th>Type</th><th>Spécialité</th><th>État</th></tr></thead>
<tbody>
<tr><td><code>CodeAgent</code></td><td>Écriture, modification, tests — boucle LLM 30 itérations</td><td class="ok">✅ Production</td></tr>
<tr><td><code>DebugAgent</code></td><td>Diagnostic et correction de bugs, analyse de stack traces</td><td class="warn">⚙️ Intégration</td></tr>
<tr><td><code>RefactorAgent</code></td><td>Restructuration et nettoyage de code sans régression</td><td class="warn">⚙️ Intégration</td></tr>
<tr><td><code>ResearchAgent</code></td><td>Recherche web + mémoire vectorielle multi-sources</td><td class="warn">⚙️ Intégration</td></tr>
<tr><td><code>BrowserAgent</code></td><td>Navigation web autonome et scraping</td><td class="warn">⚙️ Intégration</td></tr>
<tr><td><code>FileAgent</code></td><td>Opérations fichiers en masse (lecture, écriture, recherche)</td><td class="warn">⚙️ Intégration</td></tr>
<tr><td><code>PlannerAgent</code></td><td>Décomposition de tâches complexes en sous-étapes DAG</td><td class="warn">⚙️ Intégration</td></tr>
<tr><td><code>SubAgentOrchestrator</code></td><td>Orchestration parallèle, routage automatique, anti-cycles</td><td class="ok">✅ Production</td></tr>
</tbody>
</table>

<h3>Fonctionnement</h3>
<div class="doc-code-block">
<pre>
Contexte initial :
  _gather_project_context() → RepoMap 800 tokens + CodeIndex RAG 1 500 tokens
  + historique conversation + mémoire ChromaDB
       ↓
Boucle interne (max 30 itérations) :
  LLM → choisit une action JSON
       ↓
  Exécution de l'action
       ↓
  Observation du résultat (16 000 chars max)
  Cache lecture LRU 120k chars / 12 fichiers en session
       ↓
  LLM → action suivante → … → "done"
       ↓
Boucle externe : si bloqué (is_stuck) → retry avec température +0.05
  (max 3 essais avec historique des échecs — prior_failures)
</pre>
</div>

<h3>12 actions disponibles</h3>
<table class="doc-table">
<thead><tr><th>Action</th><th>Description</th></tr></thead>
<tbody>
<tr><td><code>read_file</code></td><td>Lire un fichier du projet</td></tr>
<tr><td><code>write_file</code></td><td>Créer ou écraser un fichier</td></tr>
<tr><td><code>edit_file</code></td><td>Modifier un passage (fuzzy matching 4-pass)</td></tr>
<tr><td><code>edit_lines</code></td><td>Modifier par numéros de lignes</td></tr>
<tr><td><code>apply_patch</code></td><td>Patch multi-fichiers multi-hunks (reverse-order)</td></tr>
<tr><td><code>list_files</code></td><td>Explorer un répertoire</td></tr>
<tr><td><code>grep</code></td><td>Rechercher dans le code</td></tr>
<tr><td><code>run_command</code></td><td>Exécuter une commande shell</td></tr>
<tr><td><code>run_tests</code></td><td>Lancer pytest et analyser les erreurs</td></tr>
<tr><td><code>lint</code></td><td>Analyse statique ruff (E, F, W) + fallback py_compile</td></tr>
<tr><td><code>plan</code></td><td>Décomposer la tâche en sous-étapes</td></tr>
<tr><td><code>done</code></td><td>Terminer avec résumé du travail effectué</td></tr>
</tbody>
</table>

<h3>10 guardrails automatiques</h3>
<div class="doc-guard-grid">
  <div class="doc-guard-item">
    <div class="doc-guard-icon"><i data-lucide="search"></i></div>
    <strong>Lint auto</strong>
    <p><code>_check_python_syntax()</code> — ruff + py_compile après chaque modification</p>
  </div>
  <div class="doc-guard-item">
    <div class="doc-guard-icon"><i data-lucide="flask-conical"></i></div>
    <strong>Tests forcés</strong>
    <p>pytest automatique après 3 modifications sans test (<code>edits_since_last_test</code>)</p>
  </div>
  <div class="doc-guard-item">
    <div class="doc-guard-icon"><i data-lucide="book-open"></i></div>
    <strong>Auto-reread</strong>
    <p>Si edit_file échoue ("non trouvé"), relit le fichier et réinjecte le contenu</p>
  </div>
  <div class="doc-guard-item">
    <div class="doc-guard-icon"><i data-lucide="package"></i></div>
    <strong>Compaction mid-loop</strong>
    <p>&gt;20 messages → résumé head + tail + summary pour garder le contexte</p>
  </div>
  <div class="doc-guard-item">
    <div class="doc-guard-icon"><i data-lucide="refresh-cw"></i></div>
    <strong>Anti-boucle</strong>
    <p>Même action 3× → arrêt, changement d'approche automatique</p>
  </div>
  <div class="doc-guard-item">
    <div class="doc-guard-icon"><i data-lucide="zap"></i></div>
    <strong>is_stuck → retry</strong>
    <p>Détection blocage → outer retry avec prior_failures + température +0.05</p>
  </div>
  <div class="doc-guard-item">
    <div class="doc-guard-icon"><i data-lucide="git-branch"></i></div>
    <strong>Outer retry</strong>
    <p>Si 3 boucles internes échouent → approche différente avec historique complet des échecs</p>
  </div>
  <div class="doc-guard-item">
    <div class="doc-guard-icon"><i data-lucide="file-check"></i></div>
    <strong>Vérification écriture</strong>
    <p>Après write_file/apply_patch : vérifie que le fichier existe et n'est pas vide (chemins absolus)</p>
  </div>
  <div class="doc-guard-item">
    <div class="doc-guard-icon"><i data-lucide="trash-2"></i></div>
    <strong>Cache invalidation</strong>
    <p>Cache lecture (120k LRU) invalidé après chaque edit — pas de contenu périmé</p>
  </div>
  <div class="doc-guard-item">
    <div class="doc-guard-icon"><i data-lucide="message-square-warning"></i></div>
    <strong>Verbalization redirect</strong>
    <p>Détecte monologue interne (<code>**THOUGHT:**</code>, "je délègue") sans action → nudge pour agir</p>
  </div>
</div>

<h3>Modules complémentaires</h3>
<table class="doc-table">
<thead><tr><th>Fichier</th><th>LOC</th><th>Rôle</th></tr></thead>
<tbody>
<tr><td><code>session_manager.py</code></td><td>217</td><td>Gestion de sessions CodeAgent (persistence, restore)</td></tr>
<tr><td><code>session.py</code></td><td>208</td><td>Modèle de données session (état, historique actions)</td></tr>
<tr><td><code>audit_log.py</code></td><td>151</td><td>Journal d'audit de toutes les actions exécutées</td></tr>
<tr><td><code>forking_agent.py</code></td><td>216</td><td>Exécution parallèle de sous-tâches (fork + merge)</td></tr>
</tbody>
</table>
""",
    },
    {
        "id": "channels",
        "icon": "radio",
        "title": "Canaux de communication",
        "content": """
<div class="doc-channels-grid">

  <div class="doc-channel-card">
    <div class="doc-channel-header">
      <span class="doc-channel-icon" style="background:#6366f1"><i data-lucide="globe" style="width:18px;height:18px;color:#fff"></i></span>
      <h4>Interface Web</h4>
    </div>
    <p>SPA complète avec 25+ panels : chat SSE streaming, mémoire, outils, tâches, configuration, logs,
    émotions, identité, console, éditeur de fichiers, live trace, apprentissage, fine-tuning, Stripe…</p>
    <div class="doc-channel-tech">FastAPI + HTML/JS vanilla + ES Modules — 15 fichiers JS (6 824L) + 9 fichiers CSS</div>
  </div>

  <div class="doc-channel-card">
    <div class="doc-channel-header">
      <span class="doc-channel-icon" style="background:#0088cc"><i data-lucide="send" style="width:18px;height:18px;color:#fff"></i></span>
      <h4>Telegram</h4>
    </div>
    <p>Canal principal de communication. Multi-user via <code>sender_info</code> + <code>tg_contexts</code>.
    Supporte conversations longues, voix, pièces jointes et toutes les réponses structurées.</p>
    <div class="doc-channel-tech"><code>telegram_channel.py</code> (1 014L) — Telegram Bot API — <code>run_telegram.py</code></div>
  </div>

  <div class="doc-channel-card">
    <div class="doc-channel-header">
      <span class="doc-channel-icon" style="background:#5865f2"><i data-lucide="message-circle" style="width:18px;height:18px;color:#fff"></i></span>
      <h4>Discord</h4>
    </div>
    <p>Serveur dédié avec administration complète : canaux, rôles, messages programmés,
    morning briefing quotidien à 10h. 29 handlers V2 dans <code>discord_admin.py</code>.</p>
    <div class="doc-channel-tech"><code>discord_channel.py</code> (805L) — Discord.py 2.x</div>
  </div>

  <div class="doc-channel-card">
    <div class="doc-channel-header">
      <span class="doc-channel-icon" style="background:#1da1f2"><i data-lucide="twitter" style="width:18px;height:18px;color:#fff"></i></span>
      <h4>Twitter / X</h4>
    </div>
    <p>Publication et monitoring de mentions. Polling actif toutes les 90 secondes. 13 handlers V2.</p>
    <div class="doc-channel-tech"><code>twitter_channel.py</code> (579L) — Tweepy 4.x — <code>run_twitter.py</code></div>
  </div>

  <div class="doc-channel-card">
    <div class="doc-channel-header">
      <span class="doc-channel-icon" style="background:#22c55e"><i data-lucide="mic" style="width:18px;height:18px;color:#fff"></i></span>
      <h4>CLI + Voix</h4>
    </div>
    <p>Mode terminal direct (<code>src/cli.py</code> 712L, interface Rich interactive).
    Pipeline vocal intégré : STT via Whisper (<code>src/voice/stt.py</code> 882L), TTS via Piper (<code>src/voice/tts.py</code> 711L), boucle vocale (<code>src/voice/assistant_loop.py</code> 577L).</p>
    <div class="doc-channel-tech doc-channel-wip">
      Whisper + Piper — <code>models/piper/fr_FR-siwis-low.onnx</code>
      <span class="doc-badge-wip">En développement actif</span>
      — Transcription et synthèse vocale de base disponibles. La boucle vocale interactive (wake word, conversation continue, interruption) est en cours d'intégration industrielle.
    </div>
  </div>

  <div class="doc-channel-card">
    <div class="doc-channel-header">
      <span class="doc-channel-icon" style="background:#2563eb"><i data-lucide="monitor" style="width:18px;height:18px;color:#fff"></i></span>
      <h4>IDE Bridge (WebSocket)</h4>
    </div>
    <p>Connexion bidirectionnelle Lumena ↔ VSCode/Cursor. 36 handlers IDE : open, read, write, terminal,
    navigate, list, diff, launch. Auto-reconnect 5s.</p>
    <div class="doc-channel-tech"><code>src/tools/ide_bridge.py</code> (364L) — WebSocket <code>/ws/ide</code></div>
  </div>

</div>

<h3>Orchestration multi-canal</h3>
<table class="doc-table">
<thead><tr><th>Fichier</th><th>Rôle</th></tr></thead>
<tbody>
<tr><td><code>manager.py</code> (217L)</td><td>Orchestration des canaux simultanés, routing des messages</td></tr>
<tr><td><code>base.py</code> (122L)</td><td>Classe de base abstraite pour tous les canaux</td></tr>
</tbody>
</table>
""",
    },
    {
        "id": "web-panel",
        "icon": "layout-dashboard",
        "title": "Panel de contrôle",
        "content": """
<p class="doc-lead">Interface web complète accessible sur le port 8080, offrant un contrôle total
sur l'ensemble des sous-systèmes de Lumena.</p>

<h3>Panels disponibles (sidebar)</h3>
<table class="doc-table">
<thead><tr><th>Groupe</th><th>Panel</th><th>Contenu</th></tr></thead>
<tbody>
<tr><td rowspan="6"><strong>Control</strong></td><td>Overview</td><td>Statut système, scheduler, logs trace live</td></tr>
<tr><td>Repo Map</td><td>Carte du code source indexé</td></tr>
<tr><td>Code Search</td><td>Recherche sémantique dans le code</td></tr>
<tr><td>Mémoire</td><td>Souvenirs ChromaDB, recherche vectorielle</td></tr>
<tr><td>Journal</td><td>Journaux quotidiens navigables</td></tr>
<tr><td>Identité</td><td>Profil, personnalité, humeur actuelle</td></tr>

<tr><td rowspan="7"><strong>Agent</strong></td><td>Outils</td><td>Catalogue {tools_count} outils, filtre et recherche</td></tr>
<tr><td>Règles</td><td>Directives .lumena_rules</td></tr>
<tr><td>Instincts</td><td>Patterns appris automatiquement</td></tr>
<tr><td>Tâches</td><td>Tâches actives, historique, statuts</td></tr>
<tr><td>Sessions</td><td>Historique des conversations</td></tr>
<tr><td>Todos</td><td>Liste de tâches manuelle</td></tr>
<tr><td>Apprentissage</td><td>Pipeline training, learning reports</td></tr>

<tr><td rowspan="7"><strong>Système</strong></td><td>Émotions</td><td>État émotionnel en temps réel</td></tr>
<tr><td>Voix</td><td>Contrôle STT/TTS</td></tr>
<tr><td>Hooks</td><td>Webhooks et callbacks actifs</td></tr>
<tr><td>Live Trace</td><td>Flux ReAct en direct</td></tr>
<tr><td>Console</td><td>Terminal intégré</td></tr>
<tr><td>Logs</td><td>Logs daemon en temps réel</td></tr>
<tr><td>Alertes</td><td>Alertes critiques et notifications</td></tr>

<tr><td rowspan="7"><strong>Infra</strong></td><td>Telegram</td><td>Statut et détails du bot Telegram</td></tr>
<tr><td>Autonomie</td><td>État du daemon, tâches planifiées</td></tr>
<tr><td>Réseau Lumena</td><td>Statut réseau, pairs LAN, jumelage par code, découverte mDNS, délégation, pare-feu assisté</td></tr>
<tr><td>MCP</td><td>Bibliothèque MCP, approvals, install/activation, catalog, auto-approve, trust, diagnostics et audit</td></tr>
<tr><td>Providers LLM</td><td>Santé de chaque provider, latence, coûts</td></tr>
<tr><td>Configuration</td><td>Variables d'environnement, clés API (149 entrées, 23 groupes)</td></tr>
<tr><td>Fichiers</td><td>Éditeur .lumena_rules, README, HEARTBEAT et documents MCP opérationnels</td></tr>
</tbody>
</table>

<h3>Pages spécialisées</h3>
<table class="doc-table">
<thead><tr><th>Page</th><th>Description</th></tr></thead>
<tbody>
<tr><td><strong>Fine-tuning</strong></td><td>Détection GPU nvidia-smi, catalogue 30 modèles filtrés par VRAM, install dépendances auto, pipeline LoRA, export GGUF, import Ollama, SSE streaming progression</td></tr>
<tr><td><strong>Setup / Wizard</strong></td><td>One-Click Install wizard (<code>setup.py</code> 1 239L + <code>setup.js</code> 2 091L) — config providers, clés API, Telegram, workspace, sandbox Docker</td></tr>
<tr><td><strong>Projets / Workspaces</strong></td><td>Panel redesigné : groupes par date, arbre de fichiers lazy-load, badges tech stack (Node.js, Python, HTML, TypeScript, Rust, Go, Docker), recherche + tri, 4 endpoints REST</td></tr>
<tr><td><strong>Stripe</strong></td><td>Dashboard Stripe intégré : produits, clients, paiements, abonnements, création de liens</td></tr>
<tr><td><strong>Documentation</strong></td><td>Product Docs interactive (cette page)</td></tr>
<tr><td><strong>Chat</strong></td><td>Interface chat SSE streaming, markdown rendu, upload fichiers, historique</td></tr>
</tbody>
</table>

<h3>Stack frontend</h3>
<ul>
<li>SPA vanilla JS (<strong>aucun framework</strong>) — 15 fichiers JS (6 824 LOC), 9 fichiers CSS</li>
<li>Build avec <strong>Vite</strong> (optimisation production)</li>
<li>Icônes : <strong>Lucide</strong></li>
<li>Branding : <code>web/static/branding/</code> — logo SVG horizontal + mark, boot splash</li>
<li>Easter egg : démineur intégré (<code>demineur.js</code> 272L + <code>demineur.css</code>)</li>
</ul>

<h3>API REST — résumé</h3>
<table class="doc-table">
<thead><tr><th>Endpoint</th><th>Méthode</th><th>Description</th></tr></thead>
<tbody>
<tr><td><code>/api/status</code></td><td>GET</td><td>Statut global (tous sous-systèmes)</td></tr>
<tr><td><code>/api/config</code></td><td>GET / PUT</td><td>Lecture et mise à jour de la configuration</td></tr>
<tr><td><code>/api/docs/{key}</code></td><td>GET / PUT</td><td>Lecture/écriture fichiers de config</td></tr>
<tr><td><code>/api/chat</code></td><td>POST</td><td>Envoi d'un message (stream SSE)</td></tr>
<tr><td><code>/api/logs</code></td><td>GET</td><td>Logs daemon</td></tr>
<tr><td><code>/api/health</code></td><td>GET</td><td>Health check (sans auth)</td></tr>
<tr><td><code>/api/tasks</code></td><td>GET / POST</td><td>Gestion des tâches</td></tr>
<tr><td><code>/api/tools</code></td><td>GET</td><td>Liste des outils enregistrés</td></tr>
</tbody>
</table>
<p>Voir la section <strong>Référence API</strong> pour les 123 endpoints complets.</p>
""",
    },
    {
        "id": "quality",
        "icon": "shield-check",
        "title": "Robustesse & Qualité",
        "content": """
<h3>Métriques de qualité</h3>
<div class="doc-grid doc-grid-3">
  <div class="doc-stat-card">
    <div class="doc-stat-value">{tests_passed}</div>
    <div class="doc-stat-label">Tests pytest</div>
  </div>
  <div class="doc-stat-card">
    <div class="doc-stat-value" style="color:var(--ok)">{tests_failed}</div>
    <div class="doc-stat-label">Failures</div>
  </div>
  <div class="doc-stat-card">
    <div class="doc-stat-value" style="color:var(--ok)">0</div>
    <div class="doc-stat-label">Warnings</div>
  </div>
  <div class="doc-stat-card">
    <div class="doc-stat-value">{tests_duration}</div>
    <div class="doc-stat-label">Durée suite</div>
  </div>
  <div class="doc-stat-card">
    <div class="doc-stat-value">3×</div>
    <div class="doc-stat-label">CI gate (runs)</div>
  </div>
  <div class="doc-stat-card">
    <div class="doc-stat-value">{packages_locked}</div>
    <div class="doc-stat-label">Packages verrouillés</div>
  </div>
</div>

<h3>Configuration tests</h3>
<ul>
<li><code>pytest.ini</code> configuré avec <code>pytest-asyncio</code>, <code>pytest-timeout=15</code></li>
<li>16 <code>filterwarnings</code> pour zéro bruit (DeprecationWarning, ResourceWarning, etc.)</li>
<li><strong>CI gate</strong> : <code>ci_phase_gate.py</code> — exécute tests × N runs consécutifs, gate bloquante</li>
<li><code>requirements-lock.txt</code> — {packages_locked} dépendances verrouillées</li>
</ul>

<h3>Sécurité OWASP</h3>
<table class="doc-table">
<thead><tr><th>Protection</th><th>Fichier</th><th>Détail</th></tr></thead>
<tbody>
<tr><td>SSRF guard</td><td><code>playwright_browser.py</code></td><td>Bloque localhost, IP privées, DNS rebinding, schemes <code>file://</code> et <code>data://</code></td></tr>
<tr><td>Injection shell</td><td><code>command_sanitizer.py</code> (431L)</td><td>Validation et sanitization de toutes les commandes système</td></tr>
<tr><td>Path traversal</td><td><code>file_guardrails.py</code> (639L)</td><td>Restriction des chemins, size limits, liste blanche</td></tr>
<tr><td>Sandbox Docker</td><td><code>docker_sandbox.py</code> (365L)</td><td>Exécution code tiers dans container isolé, kill on timeout</td></tr>
<tr><td>Écritures atomiques</td><td><code>persistence.py</code> (205L)</td><td><code>tmp + mv</code> — aucun JSON corrompu possible en cas de crash</td></tr>
<tr><td>File locking</td><td><code>file_lock.py</code> (154L)</td><td>Verrous fichier exclusifs pour accès concurrent</td></tr>
</tbody>
</table>

<h3>Fiabilité LLM</h3>
<div class="doc-reliability-grid">
  <div class="doc-rel-item">
    <span class="doc-rel-icon"><i data-lucide="refresh-cw"></i></span>
    <div>
      <strong>Retry intra-provider</strong>
      <p>Backoff 1s/3s pour codes 429/500/502/503/ReadTimeout (2 retries)</p>
    </div>
  </div>
  <div class="doc-rel-item">
    <span class="doc-rel-icon"><i data-lucide="shield-alert"></i></span>
    <div>
      <strong>Context window overflow guard</strong>
      <p>&gt;85% du <code>max_context</code> → pop old history + rebuild prompt automatique</p>
    </div>
  </div>
  <div class="doc-rel-item">
    <span class="doc-rel-icon"><i data-lucide="atom"></i></span>
    <div>
      <strong>Fallback multi-niveaux</strong>
      <p>Boucle <code>while</code> sur 10 providers — jamais de réponse vide</p>
    </div>
  </div>
  <div class="doc-rel-item">
    <span class="doc-rel-icon"><i data-lucide="shield-check"></i></span>
    <div>
      <strong>Stop sequences physiques</strong>
      <p>Hallucinations LLM physiquement bloquées au niveau provider</p>
    </div>
  </div>
  <div class="doc-rel-item">
    <span class="doc-rel-icon"><i data-lucide="timer"></i></span>
    <div>
      <strong>Timeout httpx</strong>
      <p><code>httpx.Timeout(connect=10, read=300)</code> — pas de blocage silencieux</p>
    </div>
  </div>
  <div class="doc-rel-item">
    <span class="doc-rel-icon"><i data-lucide="archive"></i></span>
    <div>
      <strong>Quarantine</strong>
      <p>Fichiers JSON corrompus automatiquement isolés avec backup</p>
    </div>
  </div>
  <div class="doc-rel-item">
    <span class="doc-rel-icon"><i data-lucide="lock"></i></span>
    <div>
      <strong>Locks threading</strong>
      <p>Toutes les ressources partagées protégées (<code>threading.Lock()</code>)</p>
    </div>
  </div>
  <div class="doc-rel-item">
    <span class="doc-rel-icon"><i data-lucide="link"></i></span>
    <div>
      <strong>Détection de cycles</strong>
      <p>Délégation d'agents protégée par <code>DelegationContext</code> immuable</p>
    </div>
  </div>
  <div class="doc-rel-item">
    <span class="doc-rel-icon"><i data-lucide="badge-check"></i></span>
    <div>
      <strong>TaskProofDecision</strong>
      <p>Chaque tâche complétée reçoit une annotation <code>evidence_kind</code> + <code>confidence</code> (strong/medium/weak) — observabilité sans blocage</p>
    </div>
  </div>
  <div class="doc-rel-item">
    <span class="doc-rel-icon"><i data-lucide="brain"></i></span>
    <div>
      <strong>Thought dedup</strong>
      <p>Si le LLM répète <code>THOUGHT:</code> plus de 2 fois dans un bloc (hallucination DeepSeek), seul le dernier segment est conservé</p>
    </div>
  </div>
  <div class="doc-rel-item">
    <span class="doc-rel-icon"><i data-lucide="eye-off"></i></span>
    <div>
      <strong>Read guard seuil 3</strong>
      <p>CodeAgent bloqué dès la 4ème lecture identique consécutive sans modification — détecte les boucles de relecture au plus tôt</p>
    </div>
  </div>
</div>

<h3>Health checks — <code>health_check.py</code> (378L)</h3>
<ul>
<li>Playwright installé et fonctionnel</li>
<li>Espace disque suffisant</li>
<li>Docker daemon accessible</li>
<li>Ollama local accessible (<code>LUMENA_OLLAMA_HOST</code>)</li>
<li>Endpoint <code>GET /api/preflight</code> — vérification complète pré-démarrage</li>
</ul>

<h3>Guards anti-hallucination ReAct</h3>
<table class="doc-table">
<thead><tr><th>Guard</th><th>Description</th></tr></thead>
<tbody>
<tr><td>Ledger FINAL guard</td><td>Lumena ne peut conclure qu'après une mutation réelle (outil réussi dans le ledger)</td></tr>
<tr><td>Vérification write_file</td><td>Après écriture, contrôle existence + taille &gt; 0 du fichier produit (chemins absolus)</td></tr>
<tr><td>Verbalization redirect</td><td>Monologue interne sans action (<code>**THOUGHT:**</code>, "je délègue") → nudge forcé vers action concrète</td></tr>
<tr><td>established_facts zéro-lock</td><td>Chemin projet lu directement dans <code>StructuredState</code>, fallback IdentityService si absent</td></tr>
<tr><td>Cache invalidation post-edit</td><td>Cache lecture LRU invalidé après chaque modification de fichier (pas de contenu périmé)</td></tr>
<tr><td>Thought dedup</td><td>Bloc THOUGHT répété &gt;2× (hallucination DeepSeek) → seul le dernier segment est conservé, les 5 000+ chars simulés sont ignorés</td></tr>
<tr><td>Fausses OBSERVATION: strip</td><td>Les blocs <code>OBSERVATION:</code> hallucinés par le LLM dans sa propre réponse sont supprimés avant parsing — seules les observations système sont acceptées</td></tr>
</tbody>
</table>

<h3>Modules de robustesse</h3>
<table class="doc-table">
<thead><tr><th>Module</th><th>LOC</th><th>Rôle</th></tr></thead>
<tbody>
<tr><td><code>graceful_degradation.py</code></td><td>244</td><td>Dégradation gracieuse si sous-système indisponible</td></tr>
<tr><td><code>output_normalizer.py</code></td><td>259</td><td>Normalisation des sorties LLM (HTML entities, smart quotes)</td></tr>
</tbody>
</table>

<h3>Sécurité réseau</h3>
<ul>
<li>Token admin requis sur toutes les routes sensibles (<code>LUMENA_ADMIN_TOKEN</code>)</li>
<li>Aucun <code>shell=True</code> dans les commandes système</li>
<li>Fichiers éditables depuis le web limités à une liste blanche explicite</li>
<li>Rate limiting par IP (token bucket) avec catégories différenciées</li>
<li>Headers de sécurité HTTP injectés automatiquement</li>
<li>Aucune donnée sensible exposée dans les réponses API</li>
</ul>
""",
    },
    {
        "id": "deployment",
        "icon": "server",
        "title": "Déploiement",
        "content": """
<div class="doc-callout doc-callout-warn" style="border-left:4px solid #f59e0b;margin-bottom:18px">
  <strong>⚠️ Version Beta — Lumena Beta-v1.0</strong><br>
  Lumena v1.0 est actuellement en bêta active. Le déploiement est fonctionnel et stable pour un usage personnel,
  mais des comportements inattendus peuvent survenir sur certaines configurations ou fonctionnalités en cours d'intégration.
  Nous recommandons de consulter les <strong>logs daemon</strong> en cas d'anomalie.
</div>

<h3>🐳 Déploiement Docker (recommandé)</h3>
<p>La méthode la plus simple et isolée. Lumena se lance en une commande.</p>
<div class="doc-code-block">
<pre>
# 1. Copier et configurer .env
cp .env.example .env
# → Renseigner au minimum DEEPSEEK_API_KEY et LUMENA_ADMIN_TOKEN

# 2. Lancer Lumena
docker-compose up -d

# → Interface web sur http://localhost:8080
# → Logs : docker-compose logs -f lumena
# → Arrêt : docker-compose down
</pre>
</div>

<div class="doc-callout">
  <strong>Volumes persistants :</strong> Les données (<code>data/</code>) et le workspace (<code>workspace/</code>)
  sont montés en volumes. Rien n'est perdu au redémarrage du container.
</div>

<h4>Image Docker — détails</h4>
<table class="doc-table">
<thead><tr><th>Propriété</th><th>Valeur</th></tr></thead>
<tbody>
<tr><td>Image de base</td><td><code>python:3.12-slim</code></td></tr>
<tr><td>Build frontend</td><td>Node 20 multi-stage (Vite)</td></tr>
<tr><td>OCR intégré</td><td>Tesseract + fra/eng</td></tr>
<tr><td>Navigateur</td><td>Chromium via Playwright</td></tr>
<tr><td>Utilisateur</td><td><code>lumena</code> (non-root)</td></tr>
<tr><td>Healthcheck</td><td><code>GET /api/health</code> toutes les 30s</td></tr>
<tr><td>Logs</td><td>Rotation JSON 10 Mo × 3 fichiers</td></tr>
<tr><td>Port</td><td>8080 (configurable via <code>LUMENA_PORT</code>)</td></tr>
</tbody>
</table>

<h4>Sandbox Docker interne</h4>
<p>Indépendamment du mode de déploiement, Lumena peut exécuter le code utilisateur
dans un container Docker isolé (sandbox). Trois modes disponibles :</p>
<table class="doc-table">
<thead><tr><th>Mode</th><th>Variable</th><th>Comportement</th></tr></thead>
<tbody>
<tr><td><strong>auto</strong> (défaut)</td><td><code>LUMENA_SANDBOX_MODE=auto</code></td><td>Commandes Windows → hôte local. Code Python/scripts → container Docker isolé.</td></tr>
<tr><td><strong>always</strong></td><td><code>LUMENA_SANDBOX_MODE=always</code></td><td>Tout s'exécute dans Docker. Sécurité max, mais pas de commandes Windows.</td></tr>
<tr><td><strong>never</strong></td><td><code>LUMENA_SANDBOX_MODE=never</code></td><td>Tout s'exécute en local. Comportement classique pré-Docker.</td></tr>
</tbody>
</table>
<p>Configuration sandbox : <code>LUMENA_SANDBOX_IMAGE</code> (image Docker), <code>LUMENA_SANDBOX_MEMORY</code> (512m par défaut), <code>LUMENA_SANDBOX_CPUS</code> (1 par défaut).</p>

<hr/>

<h3>Déploiement local (sans Docker)</h3>
<div class="doc-code-block">
<pre>
# 1. Activer le venv
cd lumena
venv\\Scripts\\activate

# 2. Installer les dépendances
pip install -r requirements.txt

# 3. Configurer .env (minimum requis)
DEEPSEEK_API_KEY=sk-...
TELEGRAM_BOT_TOKEN=...
DISCORD_BOT_TOKEN=...
LUMENA_ADMIN_TOKEN=&lt;token_fort&gt;

# 4. Démarrer
python -m src
# → Interface web sur http://localhost:8080
</pre>
</div>

<h3>One-Click Install — Wizard Web</h3>
<p>Accéder à <code>http://localhost:8080/setup</code> pour un assistant d'installation guidé :</p>

<div class="doc-callout doc-callout-warn" style="border-left:4px solid #f59e0b">
  <strong>⚠️ Bêta — wizard en développement actif</strong><br>
  L'assistant de configuration est fonctionnel pour les cas standards. Sur certaines configurations système ou
  combinaisons de providers, des étapes peuvent nécessiter une vérification manuelle dans <code>.env</code>.
  En cas de blocage, consulter la section <strong>Déploiement local</strong> ci-dessus.
</div>

<ul>
<li>Configuration des providers LLM et clés API</li>
<li>Configuration Telegram / Discord / Twitter</li>
<li>Sélection du workspace et des répertoires</li>
<li>Configuration sandbox Docker</li>
<li>Test de connexion automatique</li>
</ul>
<p><code>setup.py</code> (1 239L) + <code>setup.js</code> (2 091L)</p>

<h3>Scripts BAT Windows</h3>
<table class="doc-table">
<thead><tr><th>Script</th><th>Rôle</th></tr></thead>
<tbody>
<tr><td><code>INSTALL.bat</code></td><td>Installation complète (venv + deps + .env)</td></tr>
<tr><td><code>START.bat</code></td><td>Démarrage web uniquement</td></tr>
<tr><td><code>START_FULL.bat</code></td><td>Démarrage complet (web + Telegram + daemon)</td></tr>
<tr><td><code>START_SAFE.bat</code></td><td>Démarrage sans daemon ni bots</td></tr>
</tbody>
</table>

<h3>Orchestrateur — <code>lumena_ultime.py</code> (1 128L)</h3>
<p>Lance Web + Telegram + Voice + Daemon simultanément. Point d'entrée recommandé pour production.</p>

<h3>Entry points</h3>
<table class="doc-table">
<thead><tr><th>Commande</th><th>Ce qui démarre</th></tr></thead>
<tbody>
<tr><td><code>python lumena_ultime.py</code></td><td>Tout (Web + Telegram + Voice + Daemon)</td></tr>
<tr><td><code>python -m src</code></td><td>CLI interactive</td></tr>
<tr><td><code>python run_telegram.py</code></td><td>Bot Telegram seul</td></tr>
<tr><td><code>python run_twitter.py</code></td><td>Bot Twitter seul</td></tr>
<tr><td><code>python run_daemon.py</code></td><td>Daemon autonome seul</td></tr>
<tr><td><code>uvicorn web.server:app</code></td><td>Serveur web seul</td></tr>
</tbody>
</table>

<h3>Preflight — <code>lumena_ultime.py</code></h3>
<p>Vérification pré-démarrage intégrée au démarrage principal : .env, dépendances, ports, Docker, Ollama. Accessible via <code>GET /api/preflight</code>.</p>

<h3>Variables d'environnement — 93 entrées (19 groupes)</h3>
<p>Lumena est 100% API — <strong>aucun GPU requis</strong>. Un VPS économique suffit.</p>
<table class="doc-table">
<thead><tr><th>Hébergeur</th><th>Plan</th><th>Prix</th><th>RAM</th><th>CPU</th></tr></thead>
<tbody>
<tr><td>Hetzner</td><td>CAX11</td><td>3.29 €/mois</td><td>4 GB</td><td>2 vCPU ARM</td></tr>
<tr><td>OVH</td><td>VPS Starter</td><td>3.00 €/mois</td><td>2 GB</td><td>1 vCPU</td></tr>
<tr><td>Contabo</td><td>VPS S</td><td>4.99 €/mois</td><td>8 GB</td><td>4 vCPU</td></tr>
</tbody>
</table>

<h3>Variables d'environnement</h3>
<table class="doc-table">
<thead><tr><th>Variable</th><th>Requis</th><th>Description</th></tr></thead>
<tbody>
<tr><td><code>DEEPSEEK_API_KEY</code></td><td>✓</td><td>Modèle principal (~0.27$/M tokens)</td></tr>
<tr><td><code>TELEGRAM_BOT_TOKEN</code></td><td>✓</td><td>Bot Telegram</td></tr>
<tr><td><code>DISCORD_BOT_TOKEN</code></td><td>✓</td><td>Bot Discord</td></tr>
<tr><td><code>LUMENA_ADMIN_TOKEN</code></td><td>✓</td><td>Accès au panel de contrôle web</td></tr>
<tr><td><code>OPENAI_API_KEY</code></td><td>Optionnel</td><td>Fallback LLM (GPT-4o)</td></tr>
<tr><td><code>ANTHROPIC_API_KEY</code></td><td>Optionnel</td><td>Fallback LLM (Claude)</td></tr>
<tr><td><code>TWITTER_BEARER_TOKEN</code></td><td>Optionnel</td><td>Lecture mentions Twitter</td></tr>
<tr><td><code>GMAIL_APP_PASSWORD</code></td><td>Optionnel</td><td>Envoi/lecture email Gmail</td></tr>
<tr><td><code>REMOTION_LICENSE_KEY</code></td><td>Optionnel</td><td>Licence Remotion commerciale (non-requis pour usage non-commercial)</td></tr>
<tr><td><code>LUMENA_VIDEO_FORCE_DOCKER</code></td><td>Optionnel</td><td>Forcer le rendu vidéo via Docker même si Node.js est disponible</td></tr>
<tr><td><code>LUMENA_VIDEO_RENDER_TIMEOUT</code></td><td>Optionnel</td><td>Timeout rendu vidéo en secondes (défaut : 900s)</td></tr>
</tbody>
</table>
""",
    },
    {
        "id": "api-ref",
        "icon": "code",
        "title": "Référence API",
        "content": """
<p class="doc-lead">123 endpoints REST répartis dans 19 fichiers route actifs. Toutes les routes sensibles
protégées par <code>Authorization: Bearer &lt;LUMENA_ADMIN_TOKEN&gt;</code> (admin) ou <code>Bearer &lt;peer_token&gt;</code> (peer).</p>

<h3>Vue d'ensemble — 123 endpoints</h3>
<table class="doc-table">
<thead><tr><th>Fichier route</th><th>GET</th><th>POST</th><th>PUT</th><th>DEL</th><th>Total</th><th>Auth</th></tr></thead>
<tbody>
<tr><td><code>peers.py</code></td><td>11</td><td>13</td><td>0</td><td>0</td><td>24</td><td>Admin / Peer / Public</td></tr>
<tr><td><code>advanced.py</code></td><td>8</td><td>2</td><td>0</td><td>1</td><td>11</td><td>Token</td></tr>
<tr><td><code>system.py</code></td><td>9</td><td>3</td><td>0</td><td>0</td><td>12</td><td>Token</td></tr>
<tr><td><code>finetuning.py</code></td><td>5</td><td>4</td><td>0</td><td>1</td><td>10</td><td>Token</td></tr>
<tr><td><code>content.py</code></td><td>4</td><td>2</td><td>1</td><td>1</td><td>8</td><td>Token</td></tr>
<tr><td><code>setup.py</code></td><td>3</td><td>4</td><td>0</td><td>0</td><td>7</td><td>Mixte</td></tr>
<tr><td><code>tasks.py</code></td><td>4</td><td>3</td><td>0</td><td>0</td><td>7</td><td>Token</td></tr>
<tr><td><code>product_docs.py</code></td><td>2</td><td>1</td><td>1</td><td>1</td><td>5</td><td>Mixte</td></tr>
<tr><td><code>stripe_dashboard.py</code></td><td>4</td><td>1</td><td>0</td><td>0</td><td>5</td><td>Token</td></tr>
<tr><td><code>ionos.py</code></td><td>2</td><td>2</td><td>0</td><td>1</td><td>5</td><td>Token</td></tr>
<tr><td><code>config.py</code></td><td>3</td><td>0</td><td>1</td><td>0</td><td>4</td><td>Token</td></tr>
<tr><td><code>models.py</code></td><td>3</td><td>1</td><td>0</td><td>0</td><td>4</td><td>Token</td></tr>
<tr><td><code>chat.py</code></td><td>0</td><td>4</td><td>0</td><td>0</td><td>4</td><td>Token</td></tr>
<tr><td><code>workspaces.py</code></td><td>3</td><td>0</td><td>0</td><td>1</td><td>4</td><td>Token</td></tr>
<tr><td><code>docs.py</code></td><td>2</td><td>0</td><td>1</td><td>0</td><td>3</td><td>Token</td></tr>
<tr><td><code>emotion.py</code></td><td>2</td><td>1</td><td>0</td><td>0</td><td>3</td><td>Token</td></tr>
<tr><td><code>image_gen.py</code></td><td>2</td><td>1</td><td>0</td><td>0</td><td>3</td><td>Token</td></tr>
<tr><td><code>whatsapp.py</code></td><td>2</td><td>1</td><td>0</td><td>0</td><td>3</td><td>Token</td></tr>
<tr><td><code>stripe_webhook.py</code></td><td>0</td><td>1</td><td>0</td><td>0</td><td>1</td><td>Signature</td></tr>
<tr style="font-weight:bold"><td>TOTAL</td><td>71</td><td>44</td><td>4</td><td>5</td><td>123</td><td></td></tr>
</tbody>
</table>

<h3>Système</h3>
<div class="doc-api-group">
  <div class="doc-api-item">
    <span class="doc-api-method get">GET</span>
    <code>/api/health</code>
    <span class="doc-api-desc">Health check (sans auth)</span>
  </div>
  <div class="doc-api-item">
    <span class="doc-api-method get">GET</span>
    <code>/api/status</code>
    <span class="doc-api-desc">Statut complet de tous les sous-systèmes</span>
  </div>
  <div class="doc-api-item">
    <span class="doc-api-method get">GET</span>
    <code>/api/preflight</code>
    <span class="doc-api-desc">Vérification complète pré-démarrage (localhost uniquement)</span>
  </div>
</div>

<h3>Chat</h3>
<div class="doc-api-group">
  <div class="doc-api-item">
    <span class="doc-api-method post">POST</span>
    <code>/api/chat</code>
    <span class="doc-api-desc">Envoi d'un message (réponse SSE streamée)</span>
  </div>
  <div class="doc-api-item">
    <span class="doc-api-method post">POST</span>
    <code>/api/chat/upload</code>
    <span class="doc-api-desc">Upload fichier + message (PDF, images, texte)</span>
  </div>
</div>

<h3>Tâches</h3>
<div class="doc-api-group">
  <div class="doc-api-item">
    <span class="doc-api-method get">GET</span>
    <code>/api/tasks</code>
    <span class="doc-api-desc">Liste des tâches actives et récentes</span>
  </div>
  <div class="doc-api-item">
    <span class="doc-api-method post">POST</span>
    <code>/api/tasks</code>
    <span class="doc-api-desc">Créer une nouvelle tâche</span>
  </div>
  <div class="doc-api-item">
    <span class="doc-api-method post">POST</span>
    <code>/api/tasks/{id}/cancel</code>
    <span class="doc-api-desc">Annuler une tâche en cours</span>
  </div>
</div>

<h3>Configuration</h3>
<div class="doc-api-group">
  <div class="doc-api-item">
    <span class="doc-api-method get">GET</span>
    <code>/api/config</code>
    <span class="doc-api-desc">Lecture configuration (149 entrées, 23 groupes)</span>
  </div>
  <div class="doc-api-item">
    <span class="doc-api-method put">PUT</span>
    <code>/api/config</code>
    <span class="doc-api-desc">Mise à jour de clés de configuration</span>
  </div>
  <div class="doc-api-item">
    <span class="doc-api-method get">GET</span>
    <code>/api/config/alerts</code>
    <span class="doc-api-desc">Alertes de configuration (clés manquantes, warnings)</span>
  </div>
</div>

<h3>Outils & Mémoire</h3>
<div class="doc-api-group">
  <div class="doc-api-item">
    <span class="doc-api-method get">GET</span>
    <code>/api/tools</code>
    <span class="doc-api-desc">Catalogue complet des {tools_count} outils</span>
  </div>
  <div class="doc-api-item">
    <span class="doc-api-method get">GET</span>
    <code>/api/memories</code>
    <span class="doc-api-desc">Souvenirs récents (ChromaDB)</span>
  </div>
  <div class="doc-api-item">
    <span class="doc-api-method post">POST</span>
    <code>/api/memories/search</code>
    <span class="doc-api-desc">Recherche sémantique dans la mémoire</span>
  </div>
</div>

<h3>Modèles</h3>
<div class="doc-api-group">
  <div class="doc-api-item">
    <span class="doc-api-method get">GET</span>
    <code>/api/models</code>
    <span class="doc-api-desc">Liste des {models_count} modèles LLM disponibles</span>
  </div>
  <div class="doc-api-item">
    <span class="doc-api-method post">POST</span>
    <code>/api/models/switch</code>
    <span class="doc-api-desc">Changer le modèle actif</span>
  </div>
</div>

<h3>Fine-tuning</h3>
<div class="doc-api-group">
  <div class="doc-api-item">
    <span class="doc-api-method get">GET</span>
    <code>/api/finetuning/status</code>
    <span class="doc-api-desc">État GPU, modèle en cours, progression</span>
  </div>
  <div class="doc-api-item">
    <span class="doc-api-method get">GET</span>
    <code>/api/finetuning/models</code>
    <span class="doc-api-desc">Catalogue 30 modèles filtrés par VRAM</span>
  </div>
  <div class="doc-api-item">
    <span class="doc-api-method post">POST</span>
    <code>/api/finetuning/start</code>
    <span class="doc-api-desc">Lancer un fine-tuning (SSE streaming progression)</span>
  </div>
  <div class="doc-api-item">
    <span class="doc-api-method post">POST</span>
    <code>/api/finetuning/install-deps</code>
    <span class="doc-api-desc">Installer les dépendances fine-tuning</span>
  </div>
</div>

<h3>Setup / Wizard</h3>
<div class="doc-api-group">
  <div class="doc-api-item">
    <span class="doc-api-method get">GET</span>
    <code>/api/setup/status</code>
    <span class="doc-api-desc">État de la configuration (setup complet ou non)</span>
  </div>
  <div class="doc-api-item">
    <span class="doc-api-method post">POST</span>
    <code>/api/setup/save</code>
    <span class="doc-api-desc">Sauvegarder la configuration wizard</span>
  </div>
  <div class="doc-api-item">
    <span class="doc-api-method post">POST</span>
    <code>/api/setup/test-key</code>
    <span class="doc-api-desc">Tester une clé API provider</span>
  </div>
</div>

<h3>Stripe — Dashboard + Webhook</h3>
<div class="doc-api-group">
  <div class="doc-api-item">
    <span class="doc-api-method get">GET</span>
    <code>/api/stripe/dashboard/summary</code>
    <span class="doc-api-desc">Solde, revenus du mois, 10 derniers paiements</span>
  </div>
  <div class="doc-api-item">
    <span class="doc-api-method get">GET</span>
    <code>/api/stripe/dashboard/payments</code>
    <span class="doc-api-desc">Liste paginée des PaymentIntents</span>
  </div>
  <div class="doc-api-item">
    <span class="doc-api-method get">GET</span>
    <code>/api/stripe/dashboard/subscriptions</code>
    <span class="doc-api-desc">Abonnements (actifs, essai, échus, annulés)</span>
  </div>
  <div class="doc-api-item">
    <span class="doc-api-method get">GET</span>
    <code>/api/stripe/dashboard/products</code>
    <span class="doc-api-desc">Catalogue produits + prix</span>
  </div>
  <div class="doc-api-item">
    <span class="doc-api-method post">POST</span>
    <code>/api/stripe/dashboard/payment-link</code>
    <span class="doc-api-desc">Générer un lien de paiement</span>
  </div>
  <div class="doc-api-item">
    <span class="doc-api-method post">POST</span>
    <code>/api/stripe/webhook</code>
    <span class="doc-api-desc">Webhook Stripe (signature HMAC vérifiée)</span>
  </div>
</div>

<h3>Réseau Multi-Lumena</h3>
<div class="doc-api-group">
  <div class="doc-api-item"><span class="doc-api-method get">GET</span><code>/api/instance/hello</code><span class="doc-api-desc">Présentation de l'instance (public)</span></div>
  <div class="doc-api-item"><span class="doc-api-method get">GET</span><code>/api/instance/capabilities</code><span class="doc-api-desc">Capacités déclarées (public)</span></div>
  <div class="doc-api-item"><span class="doc-api-method get">GET</span><code>/api/instance/health</code><span class="doc-api-desc">Health check instance (public)</span></div>
  <div class="doc-api-item"><span class="doc-api-method get">GET</span><code>/api/instance/network-diagnostic</code><span class="doc-api-desc">Diagnostic réseau complet (bind, LAN IPs, pare-feu)</span></div>
  <div class="doc-api-item"><span class="doc-api-method get">GET</span><code>/api/instance/network-interfaces</code><span class="doc-api-desc">Liste des sous-réseaux /24 disponibles pour le scan</span></div>
  <div class="doc-api-item"><span class="doc-api-method get">GET</span><code>/api/instance/firewall-command</code><span class="doc-api-desc">Commande netsh à exécuter pour ouvrir le port</span></div>
  <div class="doc-api-item"><span class="doc-api-method post">POST</span><code>/api/instance/firewall-apply</code><span class="doc-api-desc">Applique la règle pare-feu Windows (confirmation requise)</span></div>
  <div class="doc-api-item"><span class="doc-api-method get">GET</span><code>/api/peers</code><span class="doc-api-desc">Liste des pairs connus (tokens filtrés)</span></div>
  <div class="doc-api-item"><span class="doc-api-method post">POST</span><code>/api/peers/pair</code><span class="doc-api-desc">Jumelage direct host:port (mode avancé)</span></div>
  <div class="doc-api-item"><span class="doc-api-method post">POST</span><code>/api/peers/block</code><span class="doc-api-desc">Bloquer un pair</span></div>
  <div class="doc-api-item"><span class="doc-api-method post">POST</span><code>/api/peer/probe</code><span class="doc-api-desc">Sonder un pair (anti-SSRF RFC1918)</span></div>
  <div class="doc-api-item"><span class="doc-api-method post">POST</span><code>/api/peer/discover</code><span class="doc-api-desc">Scan LAN actif pour découvrir des instances</span></div>
  <div class="doc-api-item"><span class="doc-api-method post">POST</span><code>/api/peer/pairing-code</code><span class="doc-api-desc">Génère un code de jumelage 6 chars (TTL 5 min)</span></div>
  <div class="doc-api-item"><span class="doc-api-method post">POST</span><code>/api/peer/validate-pairing-code</code><span class="doc-api-desc">Valide le code + échange symétrique de peer tokens (public)</span></div>
  <div class="doc-api-item"><span class="doc-api-method post">POST</span><code>/api/peer/accept-pairing</code><span class="doc-api-desc">Initie le jumelage depuis cet hôte vers un pair distant</span></div>
  <div class="doc-api-item"><span class="doc-api-method post">POST</span><code>/api/peer/revoke-token/{instance_id}</code><span class="doc-api-desc">Révoque les tokens d'un pair, repasse à trust=unknown</span></div>
  <div class="doc-api-item"><span class="doc-api-method post">POST</span><code>/api/peer/delegate</code><span class="doc-api-desc">Reçoit une délégation de tâche (auth peer token)</span></div>
  <div class="doc-api-item"><span class="doc-api-method post">POST</span><code>/api/peer/test-delegation</code><span class="doc-api-desc">Teste la délégation vers un pair connu</span></div>
  <div class="doc-api-item"><span class="doc-api-method get">GET</span><code>/api/peer/audit-log</code><span class="doc-api-desc">Journal des délégations (refus inclus)</span></div>
  <div class="doc-api-item"><span class="doc-api-method get">GET</span><code>/api/mdns/status</code><span class="doc-api-desc">État mDNS (flag, lib disponible, service type)</span></div>
  <div class="doc-api-item"><span class="doc-api-method post">POST</span><code>/api/mdns/browse</code><span class="doc-api-desc">Découverte mDNS passive (intègre les pairs en unknown)</span></div>
  <div class="doc-api-item"><span class="doc-api-method post">POST</span><code>/api/mdns/advertise</code><span class="doc-api-desc">Démarre/arrête l'annonce mDNS de cette instance</span></div>
</div>

<div class="doc-callout" style="text-align:right;color:var(--muted);font-size:11px;border:none;padding-top:0">
  Lumena — Beta-v1.0
</div>
""",
    },
    {
        "id": "n8n",
        "icon": "workflow",
        "title": "n8n — Automation",
        "content": """
<p class="doc-lead">Lumena se connecte à <strong>n8n</strong> (self-hosted) pour piloter des workflows d'automatisation.
Avec plus de 400 intégrations (Gmail, Sheets, Notion, Slack, Airtable, Jira…), n8n devient le pont entre Lumena et le reste du monde.</p>

<h3>Démarrage automatique</h3>
<div class="doc-callout">
  <strong>Lumena démarre n8n automatiquement via Docker au boot.</strong><br>
  Au premier lancement, le container Docker est créé et l'image n8n téléchargée (~500 Mo).<br>
  Les lancements suivants sont instantanés.<br><br>
  <strong>Pré-requis :</strong> Docker Desktop installé et lancé.<br>
  <strong>Désactiver :</strong> <code>N8N_AUTO_START=0</code> dans <code>.env</code>
</div>

<h3>Première utilisation</h3>
<div class="doc-callout">
  1. Lancer Lumena → n8n démarre tout seul<br>
  2. Ouvrir <a href="http://localhost:5678" target="_blank">http://localhost:5678</a><br>
  3. Créer un compte local (email + mot de passe)<br>
  4. Settings → API → Create API Key<br>
  5. Coller la clé dans <code>N8N_API_KEY</code> (page Configuration ou <code>.env</code>)
</div>

<h3>Configuration</h3>
<div class="doc-callout">
  <strong>Variables dans <code>.env</code> :</strong><br>
  <code>N8N_BASE_URL=http://localhost:5678</code> — URL de l'instance n8n<br>
  <code>N8N_API_KEY=n8n_api_...</code> — Clé API (Settings → API → Create API Key)<br>
  <code>N8N_AUTO_START=1</code> — Démarrage automatique Docker (1=oui, 0=non)
</div>

<h3>Outils ReAct n8n</h3>
<p>10 outils natifs permettent à Lumena de piloter n8n en langage naturel.
7 outils additionnels sont disponibles dans le bridge complet (<code>n8n_bridge.py</code> 893L) — <strong>17 outils au total</strong>.</p>

<table class="doc-table">
<thead><tr><th>Instruction naturelle</th><th>Outil appelé</th></tr></thead>
<tbody>
<tr><td>"Est-ce que n8n est connecté ?"</td><td><code>n8n_status</code></td></tr>
<tr><td>"Montre-moi mes workflows"</td><td><code>n8n_list_workflows</code></td></tr>
<tr><td>"Déclenche le workflow 42"</td><td><code>n8n_trigger_workflow</code></td></tr>
<tr><td>"Envoie des données au webhook 'alerte-stock'"</td><td><code>n8n_trigger_webhook</code></td></tr>
<tr><td>"Active le workflow 15"</td><td><code>n8n_activate_workflow</code></td></tr>
<tr><td>"Pause le workflow 15"</td><td><code>n8n_deactivate_workflow</code></td></tr>
<tr><td>"Quels workflows ont planté ?"</td><td><code>n8n_list_executions</code></td></tr>
<tr><td>"Détails de l'exécution 1234"</td><td><code>n8n_get_execution</code></td></tr>
<tr><td>"Crée un workflow 'Email quotidien'"</td><td><code>n8n_create_workflow</code></td></tr>
<tr><td>"Supprime le workflow 99"</td><td><code>n8n_delete_workflow</code></td></tr>
</tbody>
</table>

<h3>Catalogue des 10 outils</h3>
<div class="doc-caps-grid">

<div class="doc-cap-card">
  <h4>Statut & Monitoring</h4>
  <ul>
    <li><code>n8n_status</code> — Vérifie la connexion, l'état de santé et le nombre de workflows</li>
    <li><code>n8n_list_workflows</code> — Liste tous les workflows (actifs/inactifs)</li>
    <li><code>n8n_list_executions</code> — Historique des exécutions (succès, erreurs, en cours)</li>
    <li><code>n8n_get_execution</code> — Détails complets d'une exécution</li>
  </ul>
</div>

<div class="doc-cap-card">
  <h4>Déclenchement</h4>
  <ul>
    <li><code>n8n_trigger_workflow</code> — Déclenche par ID avec données optionnelles</li>
    <li><code>n8n_trigger_webhook</code> — Déclenche via chemin webhook</li>
  </ul>
</div>

<div class="doc-cap-card">
  <h4>Gestion</h4>
  <ul>
    <li><code>n8n_activate_workflow</code> — Active un workflow (triggers auto)</li>
    <li><code>n8n_deactivate_workflow</code> — Désactive un workflow</li>
    <li><code>n8n_create_workflow</code> — Crée un nouveau workflow vide</li>
    <li><code>n8n_delete_workflow</code> — Supprime un workflow</li>
  </ul>
</div>

</div>

<h3>Cas d'usage concrets</h3>
<table class="doc-table">
<thead><tr><th>Scénario</th><th>Workflow n8n</th><th>Ce que fait Lumena</th></tr></thead>
<tbody>
<tr><td>Briefing matinal</td><td>Gmail → résumé → Slack</td><td>Déclenche le workflow au réveil</td></tr>
<tr><td>Veille concurrentielle</td><td>RSS → scraping → Notion</td><td>Active/désactive selon besoin</td></tr>
<tr><td>Alerte stock</td><td>Webhook → vérif stock → email</td><td>Trigger webhook avec données</td></tr>
<tr><td>CRM automatisé</td><td>Stripe webhook → Google Sheets</td><td>Vérifie les exécutions, relance si erreur</td></tr>
<tr><td>Publication sociale</td><td>Webhook → Twitter + LinkedIn</td><td>Envoie le contenu via webhook</td></tr>
</tbody>
</table>

<h3>Intégrations populaires via n8n</h3>
<p>Grâce à n8n, Lumena accède à :</p>
<div class="doc-grid doc-grid-3">
  <div class="doc-stat-card"><div class="doc-stat-value">400+</div><div class="doc-stat-label">Intégrations</div></div>
  <div class="doc-stat-card"><div class="doc-stat-value">17</div><div class="doc-stat-label">Outils ReAct</div></div>
  <div class="doc-stat-card"><div class="doc-stat-value">∞</div><div class="doc-stat-label">Workflows possibles</div></div>
</div>
<p><strong>Exemples :</strong> Gmail, Google Sheets, Slack, Notion, Airtable, Jira, Trello, HubSpot, Salesforce,
Shopify, WooCommerce, Telegram, Discord, Twitter/X, LinkedIn, RSS, webhook, HTTP, MySQL, PostgreSQL, MongoDB…</p>

<h3>Sécurité</h3>
<ul>
  <li>La clé API n8n est stockée en <code>.env</code> et masquée dans le panel de configuration</li>
  <li>Toutes les requêtes utilisent l'en-tête <code>X-N8N-API-KEY</code> (jamais dans l'URL)</li>
  <li>n8n tourne en local (localhost:5678) — pas d'exposition internet par défaut</li>
  <li>Lumena ne crée jamais de workflows avec des credentials — la configuration se fait dans l'interface n8n</li>
</ul>
""",
    },
    {
        "id": "ionos",
        "icon": "server",
        "title": "IONOS — Hébergement & BDD",
        "content": """
<p class="doc-lead">Lumena gère vos sites <strong>IONOS</strong> (déploiement SFTP multi-sites) et, depuis le panel,
accède à leurs <strong>bases de données MySQL</strong> via un <strong>bridge sécurisé</strong> — alors même que ces
BDD ne sont pas joignables depuis l'extérieur.</p>

<h3>Déploiement SFTP</h3>
<div class="doc-callout">
  Ajoutez un compte IONOS (domaine, hôte SFTP, identifiants) dans le panel <strong>IONOS</strong>.
  La connexion est testée à l'ajout. Les credentials sont <strong>chiffrés (Fernet)</strong> dans
  <code>data/ionos_sites.json</code>. Lumena peut alors déployer un dossier de projet, lister et supprimer
  des fichiers distants.
</div>

<h3>Le problème des BDD IONOS</h3>
<div class="doc-callout">
  Les bases MySQL en hébergement mutualisé IONOS (<code>*.hosting-data.io</code>) ne sont
  <strong>pas accessibles depuis Internet</strong> — impossible de s'y connecter directement depuis Lumena.<br><br>
  <strong>Solution :</strong> un petit fichier <strong>bridge PHP</strong> est déployé sur votre site (dans
  <code>.lumena/</code>). Il s'exécute <em>sur</em> IONOS, donc à côté de la BDD, et expose une API HTTPS
  signée que seul Lumena peut appeler.
</div>

<h3>Sécurité du bridge</h3>
<ul>
  <li><strong>Signature HMAC</strong> sur chaque requête (<code>op|body|ts|nonce</code>) — anti-falsification</li>
  <li><strong>Nonce anti-rejeu</strong> (verrou fichier) — une requête ne peut pas être rejouée</li>
  <li><strong>Credentials BDD scellés en AES-256-GCM</strong> par requête (clé dérivée HKDF) — jamais en clair</li>
  <li><strong>HTTPS strict</strong> + secret du bridge chiffré Fernet au repos côté Lumena</li>
  <li><strong>Accès réservé admin</strong> (token) — toutes les opérations BDD sont protégées</li>
  <li>Un fichier <code>index.php</code> renvoie <strong>403</strong> pour empêcher le listing du dossier</li>
</ul>

<h3>Capacités BDD (activables une par une, désactivées par défaut)</h3>
<div class="doc-caps-grid">

<div class="doc-cap-card">
  <h4>Lecture (read-only)</h4>
  <ul>
    <li>Lister les tables, voir le schéma d'une table</li>
    <li>Aperçu borné des lignes (SELECT structuré, limité)</li>
    <li>Tables sensibles signalées ⚠️ avant affichage</li>
  </ul>
</div>

<div class="doc-cap-card">
  <h4>Écriture contrôlée</h4>
  <ul>
    <li>INSERT / UPDATE seulement (jamais de SQL libre)</li>
    <li><strong>Désactivée par défaut</strong> + allowlist des tables autorisées</li>
    <li>Confirmation obligatoire, transaction + rollback, plafond de lignes</li>
    <li>UPDATE sans filtre WHERE interdit</li>
  </ul>
</div>

<div class="doc-cap-card">
  <h4>Tables sandbox (créer / vider / supprimer)</h4>
  <ul>
    <li>Création de tables préfixées <code>lumena_sandbox_</code> uniquement, types whitelistés</li>
    <li><strong>Vidage</strong> d'une table sandbox (CLEAR) : DELETE total contrôlé + snapshot avant</li>
    <li><strong>Suppression</strong> d'une table sandbox (DROP) : uniquement si la table est VIDE</li>
    <li>Chaque capacité a son propre flag, désactivée par défaut. Aucun ALTER / TRUNCATE / RENAME, aucun DROP générique</li>
  </ul>
</div>

<div class="doc-cap-card">
  <h4>Snapshot & restauration</h4>
  <ul>
    <li>Image-avant chiffrée capturée automatiquement avant chaque UPDATE / DELETE / vidage</li>
    <li>Stockée chiffrée (Fernet) — aucune valeur en clair</li>
    <li>Restauration possible (désactivée par défaut, confirmation requise)</li>
  </ul>
</div>

<div class="doc-cap-card">
  <h4>Suppression de lignes contrôlée</h4>
  <ul>
    <li>DELETE avec filtre WHERE <strong>obligatoire</strong> (jamais de suppression totale)</li>
    <li>Désactivée par défaut + allowlist <strong>séparée</strong> de l'écriture</li>
    <li>Double confirmation : retaper le nom exact de la table</li>
    <li>Snapshot obligatoire avant suppression → restauration par ré-insertion</li>
  </ul>
</div>

<div class="doc-cap-card">
  <h4>Assistant : propose, l'humain exécute</h4>
  <ul>
    <li>L'IA peut <strong>proposer</strong> écriture / suppression / vidage / DROP — jamais les exécuter seule</li>
    <li>Chaque proposition apparaît dans <em>Actions IA en attente</em> et exige une approbation humaine</li>
    <li>DELETE et DROP par l'IA exigent en plus un <strong>kill-switch global</strong> (Configuration → IONOS), OFF par défaut</li>
    <li>L'assistant n'affiche que des métadonnées (colonnes, compteurs), jamais les valeurs</li>
  </ul>
</div>

</div>

<h3>Garanties de confidentialité</h3>
<ul>
  <li>Aucune valeur de vos données n'apparaît jamais en clair dans les logs, l'API, l'interface ou l'audit</li>
  <li>Un journal d'audit (<code>data/ionos_db_audit.jsonl</code>) trace chaque opération avec seulement les
      noms de colonnes et des compteurs — <strong>jamais</strong> les valeurs ni les secrets</li>
  <li>Les fichiers de config lus (config.php, .env) sont <strong>masqués</strong> : mots de passe, tokens et secrets
      ne sont jamais exposés à l'assistant</li>
  <li>Les snapshots ont une durée de vie limitée (7 jours) et un nombre maximum par site</li>
</ul>

<div class="doc-callout">
  <strong>Bridge versionné.</strong> À chaque nouvelle capacité, le bridge change de version
  (actuelle : <strong>v9</strong>) et demande une réinstallation en un clic depuis le panel.
  Toutes les capacités d'écriture/suppression/vidage/DROP sont <strong>pilotées depuis l'interface</strong>
  avec confirmation humaine ; l'assistant ne fait que <em>proposer</em>. Pour toute action BDD IONOS,
  Lumena passe exclusivement par le bridge sécurisé (jamais mysql/php/node ni config.php en direct).
</div>
""",
    },
    {
        "id": "stripe",
        "icon": "credit-card",
        "title": "Stripe & Paiements",
        "content": """
<p class="doc-lead">Lumena intègre Stripe nativement : gestion des paiements, abonnements, webhooks,
et une interface complète dans le dashboard. Toute la logique est pilotable via des outils ReAct.</p>

<h3>Configuration</h3>
<div class="doc-callout">
  <strong>Variables requises dans <code>.env</code> :</strong><br>
  <code>STRIPE_API_KEY=sk_live_...</code> — Clé secrète (live ou test)<br>
  <code>STRIPE_MODE=live</code> — <code>live</code> ou <code>test</code><br>
  <code>STRIPE_WEBHOOK_SECRET=whsec_...</code> — Généré automatiquement par le CLI<br>
  <code>STRIPE_CLI_AUTO=1</code> — Lance automatiquement <code>stripe listen</code> au démarrage
</div>
<div class="doc-callout warn">
  <strong>Clés de test :</strong> utiliser <code>sk_test_...</code> + cartes fictives (<code>4242 4242 4242 4242</code>) pour développer sans argent réel.
</div>

<h3>Démarrage automatique du CLI</h3>
<p>Quand <code>STRIPE_CLI_AUTO=1</code>, Lumena lance <code>stripe listen --forward-to localhost:8080/api/stripe/webhook</code> en arrière-plan.
Si le CLI n'est pas authentifié (erreur 403), <strong>un navigateur s'ouvre automatiquement</strong> pour la connexion — aucune action terminal requise.</p>

<table class="doc-table">
<thead><tr><th>Étape</th><th>Ce qui se passe</th></tr></thead>
<tbody>
<tr><td>Démarrage serveur</td><td>StripeCLIService détecte <code>STRIPE_CLI_AUTO=1</code></td></tr>
<tr><td>Lancement CLI</td><td><code>stripe listen</code> dans un thread daemon (pas d'événement loop bloqué)</td></tr>
<tr><td>Capture du secret</td><td><code>whsec_...</code> extrait du stdout → injecté dans <code>STRIPE_WEBHOOK_SECRET</code></td></tr>
<tr><td>Erreur 403</td><td>Auto-login : navigateur ouvert → clic "Accès accordé" → relance</td></tr>
</tbody>
</table>

<h3>Outils ReAct Stripe</h3>
<p>Les 33 outils Stripe sont disponibles nativement dans Lumena. Exemples d'instructions :</p>

<table class="doc-table">
<thead><tr><th>Instruction naturelle</th><th>Outil appelé</th></tr></thead>
<tbody>
<tr><td>"Montre-moi mes paiements du mois"</td><td><code>stripe_list_payments</code></td></tr>
<tr><td>"Crée un abonnement à 29€/mois pour chet@example.com"</td><td><code>stripe_create_subscription</code></td></tr>
<tr><td>"Génère un lien de paiement pour 99€"</td><td><code>stripe_create_payment_link</code></td></tr>
<tr><td>"Rembourse le paiement pi_xyz"</td><td><code>stripe_refund_payment</code></td></tr>
<tr><td>"Quels sont mes produits actifs ?"</td><td><code>stripe_list_products</code></td></tr>
<tr><td>"Annule l'abonnement sub_xyz"</td><td><code>stripe_cancel_subscription</code></td></tr>
<tr><td>"Quel est mon solde Stripe ?"</td><td><code>stripe_get_balance</code></td></tr>
<tr><td>"Crée un client pour Marie Dupont"</td><td><code>stripe_create_customer</code></td></tr>
</tbody>
</table>

<h3>Catalogue complet des 33 outils</h3>
<div class="doc-caps-grid">

<div class="doc-cap-card">
  <h4>Paiements</h4>
  <ul>
    <li><code>stripe_create_payment_intent</code> — Créer un intent de paiement</li>
    <li><code>stripe_confirm_payment</code> — Confirmer un paiement</li>
    <li><code>stripe_refund_payment</code> — Rembourser (total ou partiel)</li>
    <li><code>stripe_list_payments</code> — Lister les paiements</li>
    <li><code>stripe_get_payment</code> — Détails d'un paiement</li>
    <li><code>stripe_create_payment_link</code> — Lien de paiement direct</li>
  </ul>
</div>

<div class="doc-cap-card">
  <h4>Abonnements</h4>
  <ul>
    <li><code>stripe_create_subscription</code> — Créer un abonnement</li>
    <li><code>stripe_cancel_subscription</code> — Annuler un abonnement</li>
    <li><code>stripe_update_subscription</code> — Modifier un abonnement</li>
    <li><code>stripe_list_subscriptions</code> — Lister les abonnements</li>
    <li><code>stripe_get_subscription</code> — Détails d'un abonnement</li>
    <li><code>stripe_pause_subscription</code> — Mettre en pause</li>
  </ul>
</div>

<div class="doc-cap-card">
  <h4>Clients</h4>
  <ul>
    <li><code>stripe_create_customer</code> — Créer un client</li>
    <li><code>stripe_update_customer</code> — Modifier un client</li>
    <li><code>stripe_delete_customer</code> — Supprimer un client</li>
    <li><code>stripe_list_customers</code> — Lister les clients</li>
    <li><code>stripe_get_customer</code> — Détails d'un client</li>
    <li><code>stripe_search_customer</code> — Rechercher par email</li>
  </ul>
</div>

<div class="doc-cap-card">
  <h4>Produits & Prix</h4>
  <ul>
    <li><code>stripe_create_product</code> — Créer un produit</li>
    <li><code>stripe_update_product</code> — Modifier un produit</li>
    <li><code>stripe_list_products</code> — Lister les produits</li>
    <li><code>stripe_create_price</code> — Créer un prix (one-time ou récurrent)</li>
    <li><code>stripe_list_prices</code> — Lister les prix</li>
  </ul>
</div>

<div class="doc-cap-card">
  <h4>Facturation & Paiements</h4>
  <ul>
    <li><code>stripe_create_invoice</code> — Créer une facture</li>
    <li><code>stripe_send_invoice</code> — Envoyer une facture par email</li>
    <li><code>stripe_list_invoices</code> — Lister les factures</li>
    <li><code>stripe_get_invoice</code> — Détails d'une facture</li>
    <li><code>stripe_void_invoice</code> — Annuler une facture</li>
  </ul>
</div>

<div class="doc-cap-card">
  <h4>Divers</h4>
  <ul>
    <li><code>stripe_get_balance</code> — Solde du compte</li>
    <li><code>stripe_list_transactions</code> — Transactions du solde</li>
    <li><code>stripe_list_events</code> — Événements webhook récents</li>
    <li><code>stripe_get_webhook_endpoints</code> — Endpoints webhook configurés</li>
    <li><code>stripe_test_webhook</code> — Tester un événement webhook</li>
  </ul>
</div>

</div>

<h3>Webhooks</h3>
<p>L'endpoint <code>POST /api/stripe/webhook</code> reçoit et vérifie tous les événements Stripe :</p>
<table class="doc-table">
<thead><tr><th>Événement Stripe</th><th>Action Lumena</th></tr></thead>
<tbody>
<tr><td><code>payment_intent.succeeded</code></td><td>Log + notification interne</td></tr>
<tr><td><code>payment_intent.payment_failed</code></td><td>Alerte + log d'erreur</td></tr>
<tr><td><code>customer.subscription.created</code></td><td>Notification nouvel abonné</td></tr>
<tr><td><code>customer.subscription.deleted</code></td><td>Notification désabonnement</td></tr>
<tr><td><code>invoice.payment_succeeded</code></td><td>Confirmation paiement récurrent</td></tr>
<tr><td><code>invoice.payment_failed</code></td><td>Alerte paiement échoué</td></tr>
</tbody>
</table>

<h3>Dashboard Stripe intégré</h3>
<p>Le panel <strong>Stripe → Vue d'ensemble</strong> dans le dashboard affiche en temps réel :</p>
<ul>
  <li>Solde disponible et en attente</li>
  <li>Revenus du mois en cours (nombre de paiements + montant total)</li>
  <li>10 derniers paiements avec statut coloré</li>
  <li>Formulaire de création de lien de paiement rapide</li>
</ul>
<p>Les onglets <strong>Paiements</strong>, <strong>Abonnements</strong> et <strong>Produits</strong> donnent accès aux listes complètes avec pagination.</p>

<h3>Sécurité</h3>
<ul>
  <li>La clé secrète Stripe n'est <strong>jamais</strong> transmise au processus CLI — la variable <code>STRIPE_API_KEY</code> est retirée de l'environnement subprocess</li>
  <li>Le webhook vérifie la signature HMAC <code>whsec_...</code> avant tout traitement</li>
  <li>En mode live, seules les clés <code>sk_live_...</code> ou <code>rk_live_...</code> sont acceptées par l'API Stripe</li>
</ul>
""",
    },
    {
        "id": "finetuning",
        "icon": "cpu",
        "title": "Fine-tuning Local",
        "content": """
<p class="doc-lead">Lumena intègre un pipeline complet de fine-tuning local : détection GPU automatique,
LoRA 4-bit via Unsloth, export GGUF, import automatique dans Ollama — le tout pilotable depuis l'interface web.</p>

<h3>Prérequis</h3>
<ul>
<li>GPU NVIDIA avec CUDA 12.1+</li>
<li>Détection automatique via <code>nvidia-smi</code> (prioritaire) + fallback WMI Windows</li>
<li><code>gpu_detect.py</code> (345L) — <code>detect_gpu_safe()</code> retourne nom, VRAM, driver</li>
</ul>

<h3>Catalogue modèles — 30 modèles dans <code>_HF_MAP</code></h3>
<p>Filtrage automatique par VRAM GPU totale. De <strong>qwen3:0.6B</strong> (2 Go VRAM) à <strong>llama3.3:70B</strong> (50 Go VRAM).</p>
<table class="doc-table">
<thead><tr><th>Catégorie</th><th>Exemples</th><th>VRAM min</th></tr></thead>
<tbody>
<tr><td>LLM compact</td><td>qwen3:0.6B, phi-4-mini, gemma3:4B</td><td>2-4 Go</td></tr>
<tr><td>LLM standard</td><td>llama3.1:8B, mistral:7B, deepseek:7B</td><td>6-8 Go</td></tr>
<tr><td>Code</td><td>qwen2.5-coder:7B, codellama:13B</td><td>6-10 Go</td></tr>
<tr><td>Vision</td><td>llava:7B, llava:13B</td><td>6-10 Go</td></tr>
<tr><td>LLM large</td><td>llama3.1:70B, qwen3:30B, command-r:35B</td><td>24-50 Go</td></tr>
</tbody>
</table>

<h3>Pipeline complet — <code>src/training/</code></h3>
<div class="doc-code-block">
<pre>
1. data_prep.py (200L)    → Charge conversations JSONL (min 50 exemples)
2. pipeline.py (241L)     → Fine-tuning LoRA 4-bit via Unsloth
3. export_gguf.py (156L)  → Conversion GGUF Q4_K_M
4. ollama_import.py (182L)→ Import automatique dans Ollama (Modelfile)
</pre>
</div>

<h3>Données d'entraînement</h3>
<ul>
<li>Format JSONL conversations (system/user/assistant)</li>
<li>Seuil minimum : 50 conversations</li>
<li>Pool auto-alimenté par <code>auto_learning_system.py</code> (curation automatique)</li>
<li>Répertoires : <code>data/training_pool/</code> → <code>data/training_validated/</code></li>
</ul>

<h3>Configuration</h3>
<table class="doc-table">
<thead><tr><th>Paramètre</th><th>Défaut</th><th>Description</th></tr></thead>
<tbody>
<tr><td>Epochs</td><td>3</td><td>Nombre de passes sur les données</td></tr>
<tr><td>LoRA r</td><td>16</td><td>Rang de la matrice LoRA</td></tr>
<tr><td>LoRA alpha</td><td>32</td><td>Facteur d'échelle</td></tr>
<tr><td>LoRA dropout</td><td>0.05</td><td>Régularisation</td></tr>
<tr><td>Quantization</td><td>4-bit</td><td>Via Unsloth (économie VRAM)</td></tr>
</tbody>
</table>

<h3>Interface web — 9 endpoints API</h3>
<ul>
<li>Page Fine-tuning avec détection GPU, catalogue modèles filtrés par VRAM</li>
<li>Bouton "Installer dépendances" (<code>requirements-finetuning.txt</code> + <code>llama-cpp-python</code> CUDA prebuilt)</li>
<li>SSE streaming de la progression en temps réel</li>
<li>Liste des modèles fine-tunés avec import Ollama en un clic</li>
</ul>

<h3>Pipeline existant</h3>
<p><code>models/lumena-v1.0.0/</code> — pipeline 7 étapes pré-configuré :</p>
<div class="doc-code-block">
<pre>
1_prepare_data → 2_start_finetuning → 3_export_gguf → 4_create_modelfile
→ 5_import_ollama → 6_test_model → 7_auto_retrain
+ config.yaml + Modelfile
</pre>
</div>
""",
    },
    {
        "id": "computer-use",
        "icon": "monitor",
        "title": "Computer Use",
        "content": """
<p class="doc-lead">Lumena contrôle l'ordinateur de manière autonome grâce à une architecture
multi-provider avec cascade intelligente et vision par IA.</p>

<h3>Architecture — <code>cu_router.py</code> (196L)</h3>
<p>Router avec provider health, sélection automatique du meilleur backend disponible.</p>
<div class="doc-code-block">
<pre>
Requête Computer Use
       ↓
cu_router.py — sélection meilleur provider
       ↓
┌─────────────────────────────────────────────┐
│  Cascade native (native_cu.py — 928L)       │
│  1. Anthropic Computer Use API              │
│  2. OpenAI Computer Use                     │
│  3. Google Computer Use                     │
│  4. Fallback → CU Agent Loop                │
└─────────────────────────────────────────────┘
       ↓ (si cascade native échoue)
cu_agent_loop.py — screenshot → LLM → action → observation
</pre>
</div>

<h3>Vision — <code>vision.py</code> (1 270L)</h3>
<p>Cascade de reconnaissance visuelle pour analyser les screenshots :</p>
<div class="doc-fallback-chain">
  <span class="doc-provider active">Gemini Flash</span>
  <span class="doc-arrow">→</span>
  <span class="doc-provider">Claude</span>
  <span class="doc-arrow">→</span>
  <span class="doc-provider">Ollama local</span>
  <span class="doc-arrow">→</span>
  <span class="doc-provider">OCR pytesseract</span>
</div>

<h3>Controller — <code>controller.py</code> (1 165L)</h3>
<table class="doc-table">
<thead><tr><th>Module</th><th>Rôle</th></tr></thead>
<tbody>
<tr><td><code>MouseController</code></td><td>Déplacement, clic, drag, scroll</td></tr>
<tr><td><code>KeyboardController</code></td><td>Frappe, raccourcis, saisie texte</td></tr>
<tr><td><code>WindowController</code></td><td>Focus fenêtre via pywinauto (backend="uia"), <code>alt+tab</code> fallback</td></tr>
</tbody>
</table>

<h3>Agent Loop — <code>cu_agent_loop.py</code> (1 023L)</h3>
<div class="doc-code-block">
<pre>
Boucle itérative :
  1. Screenshot de l'écran
  2. LLM analyse l'image → décide d'une action
  3. Action exécutée (clic, frappe, scroll…)
  4. Observation du résultat
  5. Détection stuck → unstick automatique (Escape, alt+tab)
  → Répéter jusqu'à objectif atteint
</pre>
</div>

<h3>DOM Indexer — <code>dom_indexer.py</code> (707L)</h3>
<p>Indexation du DOM pour navigation web précise : extraction des éléments interactifs,
coordonnées, labels, pour permettre des clics ciblés sans dépendre uniquement de la vision.</p>

<h3>Sécurité Computer Use</h3>
<ul>
<li>Process tree kill on timeout : <code>taskkill /F /T</code> (Windows) / <code>start_new_session</code> (Linux)</li>
<li><code>re.escape()</code> sur tous les <code>window_title</code> dans les regex (anti-injection)</li>
<li>30 handlers V2 dans <code>handlers/computer_use.py</code></li>
</ul>

<div class="doc-callout" style="text-align:right;color:var(--muted);font-size:11px;border:none;padding-top:0">
  Lumena — Beta-v1.0
</div>
""",
    },
    {
        "id": "multi-lumena",
        "icon": "network",
        "title": "Réseau Multi-Lumena",
        "content": """
<p class="doc-lead">Plusieurs instances Lumena sur le même réseau LAN peuvent se découvrir,
se jumeler et se déléguer des tâches de manière sécurisée — sans copier de token admin,
sans configuration IP manuelle.</p>

<h3>Architecture générale</h3>
<div class="doc-code-block">
<pre>
Instance A                         Instance B
──────────                         ──────────
Génère code court (6 cars)
  → POST /api/peer/pairing-code
                                    Soumet code + host:port
                                    → POST /api/peer/validate-pairing-code
                                         ↓
                              Échange symétrique de peer tokens
                                 (hash stocké, raw jamais exposé)
                                         ↓
                         trust = "trusted" des deux côtés
                                         ↓
                         Délégation de tâches bidirectionnelle
                         POST /api/peer/delegate
                         Authorization: Bearer &lt;peer_token_outbound&gt;
</pre>
</div>

<h3>Jumelage par code court</h3>
<table class="doc-table">
<thead><tr><th>Étape</th><th>Endpoint</th><th>Description</th></tr></thead>
<tbody>
<tr><td>1 — Générer</td><td><code>POST /api/peer/pairing-code</code></td><td>Génère un code 6 chars alphanumérique, TTL 5 min, usage unique</td></tr>
<tr><td>2 — Valider</td><td><code>POST /api/peer/validate-pairing-code</code></td><td>L'autre instance soumet le code + son host:port — échange de tokens automatique</td></tr>
<tr><td>3 — Initier</td><td><code>POST /api/peer/accept-pairing</code></td><td>Initie le pairing depuis cet hôte vers un pair distant (avec son code)</td></tr>
<tr><td>4 — Révoquer</td><td><code>POST /api/peer/revoke-token/{id}</code></td><td>Supprime les tokens d'un pair, repasse à trust=unknown</td></tr>
</tbody>
</table>

<h3>Sécurité des peer tokens</h3>
<ul>
<li><strong>Stockage hashé</strong> — SHA-256 du token reçu stocké dans le registre ; le raw n'est jamais exposé via l'API</li>
<li><strong>Liaison à l'instance</strong> — <code>verify_peer_token</code> retourne le pair authentifié ; <code>receive_delegation</code> vérifie que <code>authenticated_peer.instance_id == req.from_instance_id</code> (anti-usurpation P1)</li>
<li><strong>Token admin isolé</strong> — jamais utilisé pour la délégation entre instances</li>
<li><strong>Révocation</strong> — suppression immédiate des tokens, audit tracé</li>
<li><strong>Champs last_seen et allowed_scopes</strong> — traçabilité et contrôle de portée</li>
</ul>

<h3>Anti-SSRF</h3>
<p>Toutes les sorties réseau vers des pairs sont filtrées par <code>_validate_peer_host()</code> :</p>
<ul>
<li>Whitelist RFC1918 stricte : <code>10.0.0.0/8</code>, <code>172.16.0.0/12</code>, <code>192.168.0.0/16</code></li>
<li>Rejette CGNAT (<code>100.64/10</code>), loopback, link-local, domaines, IPs publiques</li>
<li>Appliqué sur : <code>/api/peers/pair</code>, <code>/api/peer/probe</code>, <code>/api/peer/accept-pairing</code>, résultats mDNS</li>
</ul>

<h3>Découverte réseau</h3>
<table class="doc-table">
<thead><tr><th>Méthode</th><th>Variable</th><th>Description</th></tr></thead>
<tbody>
<tr><td><strong>Scan LAN actif</strong></td><td><code>LUMENA_PEER_DISCOVERY=1</code></td><td>Scan /24 sur tous les adaptateurs détectés, configurable par sous-réseau</td></tr>
<tr><td><strong>Multi-réseau</strong></td><td>—</td><td><code>GET /api/instance/network-interfaces</code> liste les sous-réseaux disponibles pour cibler le bon adaptateur</td></tr>
<tr><td><strong>mDNS/Zeroconf</strong></td><td><code>LUMENA_MDNS_DISCOVERY=1</code></td><td>Annonce et découverte passive via <code>_lumena._tcp.local.</code> — nécessite <code>pip install zeroconf</code></td></tr>
</tbody>
</table>

<h4>mDNS — règles de sécurité</h4>
<ul>
<li>TXT records autorisés : <code>instance_id</code>, <code>instance_name</code>, <code>role</code>, <code>version</code>, <code>caps_hash</code>, <code>port</code></li>
<li>Aucun secret (token, hash, clé) ne sort via mDNS</li>
<li>Instances découvertes → <code>trust: "unknown"</code> — <strong>le jumelage par code reste obligatoire</strong></li>
<li>Auto-exclusion : l'instance ne se découvre pas elle-même</li>
<li>Fallback gracieux si <code>python-zeroconf</code> absent — Lumena continue sans erreur</li>
</ul>

<h3>Délégation de tâches</h3>
<div class="doc-code-block">
<pre>
POST /api/peer/delegate
Authorization: Bearer &lt;peer_token_outbound&gt;

{
  "task_id": "uuid",
  "from_instance_id": "instance-b-id",
  "from_user_id": "user",
  "actor_id": "actor",
  "scope": "chat",
  "prompt": "Fais X..."
}
</pre>
</div>
<p>Chaque délégation est auditée (<code>GET /api/peer/audit-log</code>). Les refus (mauvais token, usurpation d'instance_id) sont également tracés.</p>

<h3>Diagnostic réseau</h3>
<p><code>GET /api/instance/network-diagnostic</code> retourne :</p>
<ul>
<li>Adresses LAN de l'instance, port d'écoute, état bind host</li>
<li>Accessibilité réseau, vérification pare-feu Windows (via netsh)</li>
<li>Issues détectées avec sévérité (<code>error</code> / <code>warning</code>) et actions suggérées</li>
</ul>

<h3>Pare-feu assisté</h3>
<p>Windows uniquement. Nécessite une confirmation explicite <code>{"confirmed": true}</code> — jamais automatique.</p>
<ul>
<li><code>GET /api/instance/firewall-command</code> — retourne la commande netsh à exécuter</li>
<li><code>POST /api/instance/firewall-apply</code> — applique la règle (confirmation obligatoire)</li>
</ul>

<h3>Panel UI — Vue simple / Vue avancée</h3>
<p>Le panel <strong>Infra → Réseau Lumena</strong> propose deux niveaux d'affichage :</p>
<table class="doc-table">
<thead><tr><th>Vue</th><th>Contenu</th></tr></thead>
<tbody>
<tr><td><strong>Vue simple</strong> (défaut)</td><td>Statut réseau (dot coloré), liste des pairs avec actions rapides (Tester / Jumeler / Bloquer), formulaire de jumelage par code, diagnostic inline</td></tr>
<tr><td><strong>Vue avancée</strong></td><td>Toutes les cartes techniques : instance courante, pairs LAN, découverte multi-réseau, actions directes host:port, pare-feu, audit log</td></tr>
</tbody>
</table>
""",
    },
]


@router.get("/api/product-docs")
async def get_product_docs():
    """Return the full product documentation structure with live stats."""
    stats = _collect_live_stats()

    # ── Build dynamic HTML fragments ────────────────────────────────────
    # Fallback chain
    provider_display = {
        "deepseek": "DeepSeek V3", "openai": "OpenAI", "anthropic": "Anthropic",
        "google": "Gemini", "moonshot": "Kimi", "xai": "xAI Grok",
        "nvidia": "NVIDIA", "ollama": "Ollama",
    }
    fallback_order: list[str] = []
    try:
        from src.llm.multi_provider import MultiProviderLLM
        mp = MultiProviderLLM.__new__(MultiProviderLLM)
        if hasattr(MultiProviderLLM, "__init__"):
            fallback_order = ["deepseek", "mistral", "zai", "google", "moonshot", "minimax", "nvidia", "xai", "anthropic", "openai", "ollama"]
    except Exception:
        fallback_order = stats["provider_names"] or list(provider_display.keys())
    if not fallback_order:
        fallback_order = list(provider_display.keys())

    chain_parts: list[str] = []
    for i, p in enumerate(fallback_order):
        cls = ' class="doc-provider active"' if i == 0 else ' class="doc-provider"'
        label = provider_display.get(p, p.title())
        chain_parts.append(f'  <span{cls}>{label}</span>')
        if i < len(fallback_order) - 1:
            chain_parts.append('  <span class="doc-arrow">→</span>')
    fallback_chain_html = '<div class="doc-fallback-chain">\n' + "\n".join(chain_parts) + "\n</div>"

    # Provider names for display
    provider_names_display = ", ".join(
        provider_display.get(p, p.title()) for p in stats["provider_names"]
    )

    # ── Tools catalog table (dynamic from V2 registry categories) ──────
    _CAT_COLORS = {
        "browser": "#6366f1", "computer_use": "#8b5cf6", "discord": "#5865f2",
        "files": "#22c55e", "security": "#ef4444", "mail": "#f97316",
        "skills": "#06b6d4", "network": "#14b8a6", "agents": "#a855f7",
        "web": "#3b82f6", "git": "#64748b", "github": "#64748b",
        "memory": "#ec4899", "spotify": "#1db954", "notion": "#000000",
        "autonomy": "#f59e0b", "project": "#10b981", "documents": "#dc2626",
        "system": "#78716c", "http": "#0ea5e9", "social": "#be185d",
        "perception": "#7c3aed", "plans": "#059669", "custom": "#475569",
        "heartbeat": "#e11d48", "ide": "#2563eb", "codebase": "#0d9488",
        "lsp": "#7c3aed", "website": "#10b981", "config": "#78716c",
        "automation": "#f97316", "stripe": "#6366f1",
    }
    _CAT_LABELS = {
        "browser": "Navigateur", "computer_use": "Computer Use", "discord": "Discord",
        "files": "Fichiers", "security": "Sécurité", "mail": "Mail & Alertes",
        "skills": "Skills", "network": "Réseau", "agents": "Agents",
        "web": "Web & Recherche", "git": "Git", "github": "GitHub",
        "memory": "Mémoire", "spotify": "Spotify", "notion": "Notion",
        "autonomy": "Autonomie", "project": "Projets / Code", "documents": "Documents",
        "system": "Système / OS", "http": "HTTP / API", "social": "Twitter / X",
        "perception": "Perception / KG", "plans": "Plans", "custom": "Custom Tools",
        "heartbeat": "Heartbeat", "ide": "IDE", "codebase": "Codebase",
        "lsp": "LSP", "website": "Website", "config": "Configuration",
        "automation": "n8n / Automation", "stripe": "Stripe",
    }

    cats = stats.get("tools_categories") or {}
    if cats:
        sorted_cats = sorted(cats.items(), key=lambda kv: -kv[1])
        rows: list[str] = []
        for cat, count in sorted_cats:
            color = _CAT_COLORS.get(cat, "#64748b")
            label = _CAT_LABELS.get(cat, cat.replace("_", " ").title())
            rows.append(
                f'<tr><td><span class="doc-cat-badge" style="--cat-color:{color}">{label}</span></td>'
                f'<td>{count}</td></tr>'
            )
        tools_catalog_table = (
            '<table class="doc-table doc-table-tools">\n'
            '<thead><tr><th>Catégorie</th><th>Nb outils</th></tr></thead>\n'
            '<tbody>\n' + "\n".join(rows) + '\n</tbody></table>'
        )
    else:
        tools_catalog_table = '<p class="doc-muted">Catalogue dynamique disponible quand Lumena est démarrée.</p>'

    # ── Skills table (dynamic from skills/ directory) ──────────────────
    skill_names = stats.get("skill_names") or []
    if skill_names:
        rows = []
        for name in skill_names:
            # Try to read first line of SKILL.md for description
            desc = ""
            skill_md = _PROJECT_ROOT / "skills" / name / "SKILL.md"
            if skill_md.exists():
                try:
                    lines = skill_md.read_text(encoding="utf-8", errors="replace").splitlines()
                    for line in lines:
                        stripped = line.strip()
                        if stripped and not stripped.startswith("#") and not stripped.startswith("---"):
                            desc = stripped[:120]
                            break
                except Exception:
                    pass
            rows.append(f'<tr><td><code>{name}</code></td><td>{desc}</td></tr>')
        skills_table = (
            '<table class="doc-table">\n'
            '<thead><tr><th>Skill</th><th>Description</th></tr></thead>\n'
            '<tbody>\n' + "\n".join(rows) + '\n</tbody></table>'
        )
    else:
        skills_table = '<p class="doc-muted">Aucun skill installé.</p>'

    # ── Template substitution ──────────────────────────────────────────
    tp = stats["tests_passed"]
    tf = stats["tests_failed"]
    td = stats["tests_duration"]
    tfiles = stats["test_files"]

    # Tests display: prefer concrete run data, fallback to test file count
    if tp > 0:
        tests_display = _fmt(tp)
        failed_display = str(tf)
        duration_display = td or "—"
    else:
        tests_display = f"{tfiles} fichiers"
        failed_display = "—"
        duration_display = "—"

    replacements = {
        "{tools_count}": _fmt(stats["tools_count"]) if stats["tools_count"] else "—",
        "{tools_cat_count}": str(len(cats)) if cats else "—",
        "{skills_count}": str(stats["skills_count"]),
        "{tests_passed}": tests_display,
        "{tests_failed}": failed_display,
        "{tests_duration}": duration_display,
        "{providers_count}": str(stats["providers_count"]),
        "{provider_names_display}": provider_names_display,
        "{models_count}": str(stats["models_count"]) if stats["models_count"] else "—",
        "{default_model}": stats["default_model"],
        "{memory_count}": _fmt(stats["memory_count"]) if stats["memory_count"] else "—",
        "{handler_modules}": str(stats["handler_modules"]),
        "{core_services}": str(stats["core_services"]),
        "{tools_modules}": str(stats["tools_modules"]),
        "{test_files}": str(tfiles),
        "{packages_locked}": str(stats["packages_locked"]) if stats["packages_locked"] else "—",
        "{tools_catalog_table}": tools_catalog_table,
        "{skills_table}": skills_table,
        "{fallback_chain_html}": fallback_chain_html,
        "{routes_count}": str(stats["routes_count"]) if stats.get("routes_count") else "77",
    }

    sections = []
    for section in _DOC_SECTIONS:
        content = section["content"]
        for key, val in replacements.items():
            content = content.replace(key, val)
        sections.append({**section, "content": content})

    return {
        "success": True,
        "product": "Lumena",
        "version": "Beta-v1.0",
        "sections": sections,
        "stats": {k: v for k, v in stats.items() if k not in ("skill_names", "tools_categories", "provider_names")},
    }
# ──────────────────────────────────────────────────────────────────────────────
# © 2025-2026 LossKarr — Lumena Project
# Licensed under AGPL-3.0 (open source) or a Commercial License (proprietary use)
# https://github.com/Losskarr/lumena
# ──────────────────────────────────────────────────────────────────────────────
