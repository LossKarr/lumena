"""
📂 Registre persistant des projets Lumena.

Permet à Lumena de retrouver automatiquement ses projets par nom,
chemin relatif, ou recherche floue.  Fichier JSON unique dans data/.

Point d'entrée unique : ``resolve_workspace(query)`` — appelé par react.py,
agents.py et sub_agent.py.  Plus AUCUNE logique de résolution dupliquée.

Structure : { "projects": [ { "slug", "path", "description", "created", "last_accessed" } ] }
"""
from __future__ import annotations

import os
import re
import time
import unicodedata
from dataclasses import dataclass
from datetime import date, datetime
from difflib import SequenceMatcher
from pathlib import Path
from typing import Optional

from loguru import logger

from .paths import DATA_DIR, WORKSPACE_DIR, ROOT_DIR
from .persistence import atomic_write_json, safe_read_json

_REGISTRY_PATH = DATA_DIR / "project_registry.json"

# ── Helpers ──────────────────────────────────────────────────────────────────

# Dossiers système/internes à ne JAMAIS considérer comme des projets.
_SYSTEM_DIRS = frozenset({
    "_archives", "_temp", "_backup", "_backups", "_old", "_trash",
    ".git", "__pycache__", "node_modules", ".venv", "venv", ".tox",
})


def _norm(s: str) -> str:
    """Normalise une chaîne : lowercase + suppression accents."""
    s = unicodedata.normalize("NFD", s.lower())
    return "".join(c for c in s if unicodedata.category(c) != "Mn")


def _slug_from_path(p: Path) -> str:
    """Extraire un slug lisible depuis un chemin de projet."""
    return p.name


def _similarity(a: str, b: str) -> float:
    """Score de similarité entre 0.0 et 1.0."""
    return SequenceMatcher(None, _norm(a), _norm(b)).ratio()


# ── API publique ─────────────────────────────────────────────────────────────

def load_registry() -> list[dict]:
    """Charge la liste des projets enregistrés."""
    data = safe_read_json(_REGISTRY_PATH, default={"projects": []})
    return data.get("projects", []) if isinstance(data, dict) else []


def save_registry(projects: list[dict]) -> None:
    """Sauvegarde la liste des projets."""
    atomic_write_json(_REGISTRY_PATH, {"projects": projects})


def register_project(
    path: str | Path,
    description: str = "",
    *,
    slug: str = "",
) -> None:
    """Enregistre ou met à jour un projet dans le registre."""
    path = Path(path).resolve()
    if not path.is_dir():
        return
    path_str = str(path)
    slug = slug or _slug_from_path(path)
    now = datetime.now().isoformat(timespec="seconds")

    projects = load_registry()

    # Mettre à jour si déjà connu (même chemin)
    for p in projects:
        if Path(p.get("path", "")).resolve() == path:
            p["last_accessed"] = now
            if description:
                p["description"] = description
            save_registry(projects)
            logger.debug("[registry] Projet mis à jour: {}", slug)
            return

    # Nouveau projet
    projects.append({
        "slug": slug,
        "path": path_str,
        "description": description[:200],
        "created": now,
        "last_accessed": now,
    })
    # Garder les 100 plus récents
    projects.sort(key=lambda x: x.get("last_accessed", ""), reverse=True)
    projects = projects[:100]
    save_registry(projects)
    logger.info("[registry] Nouveau projet enregistré: {} → {}", slug, path_str)


def _is_fallback_match(query: str, found: Path) -> bool:
    """Détecte si find_project a retourné un fallback sans vrai match.

    Vérifie si au moins un mot significatif du slug du projet apparaît dans
    la query. Si aucun mot ne matche, c'est un fallback (projet le + récent).
    """
    slug = found.name
    slug_words = set(_norm(slug.replace("-", " ")).split())
    slug_words -= {"projet", "new", "app", "web", "site"}
    slug_words.discard("")
    if not slug_words:
        return True
    q_words = set(re.sub(r"[^a-z0-9\s]", " ", _norm(query)).split())
    q_words.discard("")
    return len(slug_words & q_words) == 0


def find_project(query: str) -> Optional[Path]:
    """
    Trouve le meilleur projet correspondant à la requête.

    Cascade de résolution (du plus précis au plus flou) :
    1. Chemin relatif exact extrait de la query (workspace/date/slug)
    2. Match exact de slug dans le registre
    3. Match flou de nom dans le registre (> 0.5 similarité)
    4. Match flou sur les dossiers réels du filesystem workspace/
    5. Projet le plus récemment modifié dans workspace/

    Retourne le Path absolu du projet, ou None.
    """
    q = query.lower().replace("\\", "/")

    # ── 1. Chemin relatif dans la query ──
    # Pattern A: "workspace/2026-04-10/projet-lumena-website"
    _rel_m = re.search(
        r"workspace[/\\](?:\d{4}-\d{2}-\d{2}[/\\])?[\w][\w\-]*",
        query, re.IGNORECASE,
    )
    if _rel_m:
        _rel_path = Path(_rel_m.group(0).replace("\\", "/"))
        _abs = ROOT_DIR / _rel_path
        if _abs.is_dir():
            logger.info("[registry] Chemin relatif trouvé: {}", _abs)
            return _abs
        _abs2 = WORKSPACE_DIR.parent / _rel_path
        if _abs2.is_dir():
            logger.info("[registry] Chemin relatif (alt) trouvé: {}", _abs2)
            return _abs2

    # Pattern B: "2026-04-10/projet-lumena-website" (sans préfixe workspace/)
    _bare_m = re.search(
        r"(\d{4}-\d{2}-\d{2})[/\\]([\w][\w\-]*)",
        query, re.IGNORECASE,
    )
    if _bare_m:
        _abs = WORKSPACE_DIR / _bare_m.group(1) / _bare_m.group(2)
        if _abs.is_dir():
            logger.info("[registry] Chemin date/slug trouvé: {}", _abs)
            return _abs

    # ── Extraire les mots significatifs de la query ──
    # Noise grammatical seulement — PAS les mots de contenu utiles pour le matching.
    _NOISE = {
        "tu", "va", "vas", "aller", "le", "la", "les", "un", "une",
        "des", "de", "du", "mon", "ma", "mes", "ton", "ta", "tes", "son", "sa",
        "corrige", "corriger", "fix", "repare", "continue", "continuer",
        "modifie", "modifier", "ameliore", "ameliorer", "termine", "finir",
        "car", "parce", "que", "qui", "est", "et", "en", "dans", "pour", "avec",
        "casser", "casse", "casse", "broken", "il", "elle", "on", "ou",
        "workspace", "dossier", "fichier", "faudrais", "faut", "pas", "plus",
        "okay", "ok", "oui", "non", "bah", "tien", "tiens", "moi",
    }
    _q_words = set(re.sub(r"[^a-z0-9\s]", " ", _norm(query)).split()) - _NOISE
    _q_words.discard("")

    # ── 2 & 3. Registre : match exact puis flou ──
    projects = load_registry()
    if projects:
        # 2. Match exact slug (le slug apparaît littéralement dans la query)
        for p in projects:
            _slug = _norm(p.get("slug", ""))
            # Ignorer les slugs système
            if not _slug or _slug.lstrip("_") in _SYSTEM_DIRS or _slug in _SYSTEM_DIRS:
                continue
            if _slug in _norm(query):
                _pp = Path(p["path"])
                if _pp.is_dir():
                    logger.info("[registry] Match exact registre: {}", _pp)
                    return _pp

        # 3. Match flou : intersection de MOTS entre slug et query (pas de char-level)
        _best_score = 0.0
        _best_project: Optional[dict] = None
        # Tous les mots de la query (normalisés, sans filtre noise)
        _q_all = set(re.sub(r"[^a-z0-9\s]", " ", _norm(query)).split())
        _q_all -= {""}
        for p in projects:
            _slug = p.get("slug", "")
            if _slug.lstrip("_").lower() in _SYSTEM_DIRS or _slug.lower() in _SYSTEM_DIRS:
                continue
            # Mots du slug (ex: "projet-lumena-website" → {"projet","lumena","website"})
            _slug_words = set(_norm(_slug.replace("-", " ")).split())
            _slug_words -= {"projet", "new", "app"}  # Trop génériques
            _slug_words.discard("")
            if not _slug_words:
                continue
            _hits = len(_slug_words & _q_all)
            _score = _hits / len(_slug_words)
            if _score > _best_score:
                _best_score = _score
                _best_project = p
        if _best_score >= 0.5 and _best_project:
            _pp = Path(_best_project["path"])
            if _pp.is_dir():
                logger.info("[registry] Match flou registre (score={:.2f}): {}", _best_score, _pp)
                return _pp

    # ── 4. Scan filesystem : dossiers dans workspace/ ──
    if WORKSPACE_DIR.exists():
        _best_fs_score = 0.0
        _best_fs_dir: Optional[Path] = None
        _q_normalized = _norm(query.replace("\\", "/"))

        # Mots complets de la query normalisée (sans noise) pour comparaison
        _q_all_words = set(re.sub(r"[^a-z0-9\s]", " ", _q_normalized).split())
        _q_all_words -= {""}

        def _score_dir(proj_dir: Path) -> float:
            _slug = proj_dir.name
            # Blacklist : dossiers système → score 0
            if _slug.startswith("_") or _slug.startswith(".") or _slug.lower() in _SYSTEM_DIRS:
                return 0.0
            _slug_norm = _norm(_slug)
            # Bonus A: le slug apparaît littéralement dans la query → match direct
            if _slug_norm in _q_normalized:
                _base = 0.95
            else:
                # Intersection de mots entre slug et query (pas de char-level similarity)
                _slug_words = set(_norm(_slug.replace("-", " ")).split())
                _slug_words -= {"projet", "new", "app"}  # Trop génériques
                _slug_words.discard("")
                if not _slug_words:
                    return 0.0
                _hits = len(_slug_words & _q_all_words)
                _base = _hits / len(_slug_words)
            # Bonus B: dossiers non-vides valent +0.1 (préférer le vrai projet)
            try:
                _has_files = any(f.is_file() for f in proj_dir.iterdir())
                if _has_files:
                    _base += 0.1
            except OSError:
                pass
            return _base

        try:
            for _date_dir in sorted(WORKSPACE_DIR.iterdir(), reverse=True):
                if not _date_dir.is_dir():
                    continue
                if re.match(r"\d{4}-\d{2}-\d{2}$", _date_dir.name):
                    for _proj_dir in _date_dir.iterdir():
                        if not _proj_dir.is_dir():
                            continue
                        _score = _score_dir(_proj_dir)
                        if _score > _best_fs_score:
                            _best_fs_score = _score
                            _best_fs_dir = _proj_dir
                else:
                    _score = _score_dir(_date_dir)
                    if _score > _best_fs_score:
                        _best_fs_score = _score
                        _best_fs_dir = _date_dir

            if _best_fs_score >= 0.50 and _best_fs_dir:
                logger.info("[registry] Match filesystem (score={:.2f}): {}", _best_fs_score, _best_fs_dir)
                return _best_fs_dir
        except OSError:
            pass

    # ── 5. Fallback : projet le plus récemment modifié ──
    if WORKSPACE_DIR.exists():
        try:
            _latest_dir: Optional[Path] = None
            _latest_mtime = 0.0
            for _date_dir in WORKSPACE_DIR.iterdir():
                if not _date_dir.is_dir():
                    continue
                if re.match(r"\d{4}-\d{2}-\d{2}$", _date_dir.name):
                    for _proj_dir in _date_dir.iterdir():
                        if _proj_dir.is_dir():
                            # Chercher le fichier le + récent dans le projet
                            try:
                                _files = list(_proj_dir.iterdir())
                                if _files:
                                    _mt = max((f.stat().st_mtime for f in _files if f.is_file()), default=0.0)
                                    if _mt > _latest_mtime:
                                        _latest_mtime = _mt
                                        _latest_dir = _proj_dir
                            except OSError:
                                continue
                else:
                    try:
                        _files = list(_date_dir.iterdir())
                        if _files:
                            _mt = max((f.stat().st_mtime for f in _files if f.is_file()), default=0.0)
                            if _mt > _latest_mtime:
                                _latest_mtime = _mt
                                _latest_dir = _date_dir
                    except OSError:
                        continue
            if _latest_dir:
                logger.info("[registry] Fallback projet le plus récent: {}", _latest_dir)
                return _latest_dir
        except OSError:
            pass

    return None


# ── Point d'entrée unique : resolve_workspace ────────────────────────────────

@dataclass
class WorkspaceResolution:
    """Résultat structuré de la résolution du workspace."""
    path: Optional[Path]
    intent: str           # "modify" | "create" | "unknown"
    source: str           # "context" | "explicit" | "registry" | "filesystem" | "created" | "fallback"
    confidence: float     # 0.0 – 1.0


# Mots-clés de création vs modification
_CREATE_KW = re.compile(
    r'(?:cr[eé]|g[eé]n[eè]re|build|make|develop|create|nouveau|nouvelle|new|construi)'
    r'.{0,40}'
    r'(?:site|web|app|page|projet|project|portfolio|landing|dashboard|'
    r'boutique|shop|store|application|jeu|game)',
    re.IGNORECASE,
)

_MODIFY_KW = re.compile(
    r'(?:corrige|corriger|fix|r[eé]pare|reparer|continue|continuer|reprend|reprendre'
    r'|modifie|modifier|am[eé]liore|ameliorer|termine|terminer|finis|finir'
    r'|ach[eè]ve|achever|debug|update|upgrade|improve|restructur'
    r'|compl[eè]te|compl[eé]ter|complete|casser|cass[eé]|broken|bug'
    r'|ajout|ajouter|add|change|enl[eè]ve|enlever|remove|supprime|supprimer)',
    re.IGNORECASE,
)


def _generate_slug(query: str) -> str:
    """Génère un slug court depuis une requête utilisateur."""
    _STOPWORDS = {
        "creer", "cree", "create", "genere", "generer", "fais", "faire", "make",
        "build", "construis", "construire", "developpe", "ecris", "ecrire", "write",
        "donne", "moi", "tu", "il", "elle", "nous", "vous", "ils", "elles", "on",
        "un", "une", "des", "le", "la", "les", "de", "du", "en", "pour", "avec",
        "complet", "complete", "simple", "parfait", "parfaite", "nouveau", "nouvelle",
        "jeu", "jeux", "application", "app", "site", "page", "bah", "veux", "fait",
        "please", "just", "me", "a", "an", "the", "of", "with", "and", "qui", "que",
        "truc", "chose", "petit", "petite", "grand", "grande", "super", "top",
        "sympa", "cool", "vite", "rapide", "entier", "entiere",
    }
    raw = re.sub(r'[^a-zA-Z0-9\s]', ' ', _norm(query)).lower().split()
    kept = [w for w in raw if w not in _STOPWORDS and len(w) > 1][:6]
    slug = '-'.join(kept) if kept else "projet"
    return f"projet-{slug[:45]}"


def _detect_intent(query: str) -> str:
    """Détecte l'intention : 'modify', 'create', ou 'unknown'.

    Regarde les ~500 premiers caractères (instruction + début de contexte).
    Les très longs prompts contiennent souvent du texte descriptif, mais 200
    était trop court : pour une requête de 400 chars dont le verbe d'action
    est en 2e moitié, la troncature à 200 → "unknown" → faux positif routage.
    """
    check_text = query[:500] if len(query) > 500 else query
    has_modify = bool(_MODIFY_KW.search(check_text))
    has_create = bool(_CREATE_KW.search(check_text))
    if has_modify and not has_create:
        return "modify"
    if has_create and not has_modify:
        return "create"
    # Les deux ou aucun → heuristique : si un projet existe, c'est modification
    return "unknown"


def resolve_workspace(
    query: str,
    *,
    context: Optional[dict] = None,
    allow_create: bool = True,
) -> WorkspaceResolution:
    """
    Point d'entrée UNIQUE pour la résolution du workspace projet.

    Appelé par react.py, agents.py et sub_agent.py.
    Plus aucune logique de résolution dupliquée ailleurs.

    Cascade :
    1. context["project_dir"] déjà résolu → retourner directement
    2. Chemin absolu explicite dans la query
    3. find_project(query) — registre + filesystem
    4. Si allow_create ET intention de création → créer un nouveau workspace
    5. None
    """
    ctx = context or {}
    intent = _detect_intent(query)

    # ── 1. Contexte pré-résolu ──
    _pre = ctx.get("project_dir") or ctx.get("workspace_path")
    if _pre:
        p = Path(str(_pre))
        if p.is_dir():
            return WorkspaceResolution(path=p, intent=intent or "modify", source="context", confidence=1.0)

    # ── 2. Chemin absolu explicite dans la query ──
    _EXPLICIT_RE = re.compile(
        r'(?:situ[eé]e?\s+(?:dans|[àa]|en)|(?:dans|from|in|at)\s+(?:le\s+(?:dossier|r[eé]pertoire|chemin)\s+)?)'
        r'\s*["\']?([A-Za-z]:[/\\][^"\'>\n,]+|/[^"\'>\n,]+)["\']?',
        re.IGNORECASE,
    )
    _explicit = _EXPLICIT_RE.search(query)
    if _explicit:
        p = Path(_explicit.group(1).strip().rstrip('/\\'))
        if p.is_dir():
            logger.info("[resolve_workspace] Chemin explicite: {}", p)
            return WorkspaceResolution(path=p, intent=intent, source="explicit", confidence=0.95)

    # ── 3. Registre + recherche floue (find_project) ──
    found = find_project(query)
    if found and found.is_dir():
        effective_intent = "modify" if intent == "unknown" else intent
        # Si l'intent est clairement "create", ne PAS réutiliser un match faible.
        # L'utilisateur veut un NOUVEAU projet, pas un vieux dossier.
        if intent == "create":
            logger.info("[resolve_workspace] Intent=create, match existant ignoré: {}", found)
            # Fall through to creation (step 4)
        else:
            # Quand l'intent est "unknown" (aucun verbe d'action dans la query),
            # on abaisse la confiance à 0.5 pour éviter le fast-route CodeAgent
            # sur des messages purement conversationnels ("ca va ?", "bah alors").
            # Le seuil du fast-route est 0.7 — intent explicite ("modify") garde 0.8.
            #
            # Fallback (projet le plus récent, aucun match réel) → conf 0.4 max
            # pour ne JAMAIS déclencher le fast-route sur une requête sans rapport.
            _is_fallback = _is_fallback_match(query, found)
            if _is_fallback:
                _conf = 0.4
            elif intent == "unknown":
                _conf = 0.5
            else:
                _conf = 0.8
            logger.info("[resolve_workspace] Projet trouvé via registre: {} (intent={}, conf={:.1f}, fallback={})", found, effective_intent, _conf, _is_fallback)
            return WorkspaceResolution(path=found, intent=effective_intent, source="registry", confidence=_conf)

    # ── 4. Création si intention détectée et aucun projet existant ──
    if allow_create and intent in ("create", "unknown"):
        slug = _generate_slug(query)
        project_dir = WORKSPACE_DIR / str(date.today()) / slug
        project_dir.mkdir(parents=True, exist_ok=True)
        register_project(project_dir, description=query[:200], slug=slug)
        logger.info("[resolve_workspace] Projet créé: {} (slug={})", project_dir, slug)
        return WorkspaceResolution(path=project_dir, intent="create", source="created", confidence=0.7)

    # ── 5. Aucun match, pas de création ──
    return WorkspaceResolution(path=None, intent=intent, source="fallback", confidence=0.0)
# ──────────────────────────────────────────────────────────────────────────────
# © 2025-2026 LossKarr — Lumena Project
# Licensed under the Apache License, Version 2.0
# https://github.com/Losskarr/lumena
# ──────────────────────────────────────────────────────────────────────────────
