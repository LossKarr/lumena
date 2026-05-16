# ──────────────────────────────────────────────────────────────────────────────
# © 2025-2026 LossKarr — Lumena Project
# Licensed under AGPL-3.0 (open source) or a Commercial License (proprietary use)
# https://github.com/Losskarr/lumena
# ──────────────────────────────────────────────────────────────────────────────
"""Tests unitaires complets pour project_registry.py

Couvre :
- _detect_intent()
- _is_fallback_match()
- _is_slug_explicitly_named()   [nouvelle fonction]
- resolve_workspace()           [cascade complète, avec registry réel sur tmp_path]

Tous les tests utilisent de vrais dossiers temporaires et un vrai registry JSON
(pas de mocks) pour garantir que le comportement en production est bien testé.
"""
from __future__ import annotations

import json
import time
from datetime import datetime, timedelta
from pathlib import Path

import pytest

import src.utils.project_registry as reg


# ─────────────────────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────────────────────

@pytest.fixture()
def workspace(tmp_path, monkeypatch):
    """Remplace WORKSPACE_DIR, DATA_DIR et _REGISTRY_PATH par des chemins temporaires."""
    ws = tmp_path / "workspace"
    ws.mkdir()
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    registry_path = data_dir / "project_registry.json"

    monkeypatch.setattr(reg, "WORKSPACE_DIR", ws)
    monkeypatch.setattr(reg, "DATA_DIR", data_dir)
    monkeypatch.setattr(reg, "_REGISTRY_PATH", registry_path)
    return ws


def _make_project(workspace: Path, slug: str, *, date_str: str = "2026-05-16",
                  last_accessed: str | None = None, with_file: bool = True) -> Path:
    """Crée un dossier projet réel + entrée registry."""
    project_dir = workspace / date_str / slug
    project_dir.mkdir(parents=True, exist_ok=True)
    if with_file:
        (project_dir / "index.html").write_text("<html/>")

    now = last_accessed or datetime.now().isoformat(timespec="seconds")
    projects = reg.load_registry()
    projects.append({
        "slug": slug,
        "path": str(project_dir),
        "description": f"Projet {slug}",
        "created": now,
        "last_accessed": now,
    })
    reg.save_registry(projects)
    return project_dir


def _make_project_recent(workspace: Path, slug: str, minutes_ago: float = 2.0) -> Path:
    """Crée un projet dont last_accessed est dans les X dernières minutes."""
    ts = (datetime.now() - timedelta(minutes=minutes_ago)).isoformat(timespec="seconds")
    return _make_project(workspace, slug, last_accessed=ts)


def _make_project_old(workspace: Path, slug: str) -> Path:
    """Crée un projet dont last_accessed est il y a 2h (hors fenêtre 10min)."""
    ts = (datetime.now() - timedelta(hours=2)).isoformat(timespec="seconds")
    return _make_project(workspace, slug, last_accessed=ts)


# ─────────────────────────────────────────────────────────────────────────────
# _detect_intent
# ─────────────────────────────────────────────────────────────────────────────

class TestDetectIntent:
    def test_create_site(self):
        assert reg._detect_intent("crée-moi un site vitrine") == "create"

    def test_create_app(self):
        assert reg._detect_intent("génère une application de gestion") == "create"

    def test_create_new_project(self):
        assert reg._detect_intent("nouveau projet dashboard analytics") == "create"

    def test_create_english(self):
        assert reg._detect_intent("create a landing page for my startup") == "create"

    def test_modify_corrige(self):
        assert reg._detect_intent("corrige le bug dans le formulaire") == "modify"

    def test_modify_continue(self):
        assert reg._detect_intent("continue le projet echo-drift") == "modify"

    def test_modify_ameliore(self):
        assert reg._detect_intent("améliore la page d'accueil") == "modify"

    def test_modify_refactor(self):
        assert reg._detect_intent("refactorise le module auth") == "modify"

    def test_modify_typo_transfo(self):
        assert reg._detect_intent("trnasforme la page contact") == "modify"

    def test_modify_typo_modif(self):
        assert reg._detect_intent("modife le header") == "modify"

    def test_modify_anaphoric(self):
        # "la page" = référence anaphorique → modify même sans verbe d'action
        assert reg._detect_intent("la page contact est cassée") == "modify"

    def test_unknown_conversational(self):
        assert reg._detect_intent("bonjour comment ça va") == "unknown"

    def test_unknown_question(self):
        assert reg._detect_intent("qu'est-ce que tu peux faire") == "unknown"

    def test_both_create_and_modify_returns_unknown(self):
        # "crée" + "corrige" dans la même phrase → ambigu, les deux détectés → "unknown"
        # (resolve_workspace traitera ensuite l'unknown selon le contexte)
        assert reg._detect_intent("crée et corrige le projet") == "unknown"


# ─────────────────────────────────────────────────────────────────────────────
# _is_fallback_match
# ─────────────────────────────────────────────────────────────────────────────

class TestIsFallbackMatch:
    def test_real_match_slug_in_query(self, tmp_path):
        proj = tmp_path / "echo-drift"
        proj.mkdir()
        assert reg._is_fallback_match("corrige echo-drift", proj) is False

    def test_real_match_word_overlap(self, tmp_path):
        proj = tmp_path / "projet-dashboard"
        proj.mkdir()
        assert reg._is_fallback_match("améliore le dashboard analytics", proj) is False

    def test_fallback_no_overlap(self, tmp_path):
        proj = tmp_path / "projet-portfolio"
        proj.mkdir()
        assert reg._is_fallback_match("crée un site SaaS", proj) is True

    def test_fallback_generic_words_only(self, tmp_path):
        # Mots génériques filtrés : "web", "site", "app" → fallback
        proj = tmp_path / "projet-web-app"
        proj.mkdir()
        assert reg._is_fallback_match("fais quelque chose de sympa", proj) is True

    def test_empty_slug_words_is_fallback(self, tmp_path):
        # Slug entièrement composé de mots filtrés → fallback
        proj = tmp_path / "projet"
        proj.mkdir()
        assert reg._is_fallback_match("crée un truc", proj) is True


# ─────────────────────────────────────────────────────────────────────────────
# _is_slug_explicitly_named  (nouvelle fonction)
# ─────────────────────────────────────────────────────────────────────────────

class TestIsSlugExplicitlyNamed:
    def test_full_slug_in_query(self, tmp_path):
        proj = tmp_path / "echo-drift"
        proj.mkdir()
        assert reg._is_slug_explicitly_named("continue echo-drift", proj) is True

    def test_slug_with_spaces_in_query(self, tmp_path):
        proj = tmp_path / "echo-drift"
        proj.mkdir()
        # "echo drift" avec espace = équivalent normalisé
        assert reg._is_slug_explicitly_named("continue echo drift svp", proj) is True

    def test_partial_word_not_enough(self, tmp_path):
        # "images" seul ≠ "projet-images" complet
        proj = tmp_path / "projet-images"
        proj.mkdir()
        assert reg._is_slug_explicitly_named("crée un site images", proj) is False

    def test_single_shared_word_not_enough(self, tmp_path):
        proj = tmp_path / "projet-saas-dashboard"
        proj.mkdir()
        assert reg._is_slug_explicitly_named("crée un site SaaS", proj) is False

    def test_no_overlap_at_all(self, tmp_path):
        proj = tmp_path / "portfolio-design"
        proj.mkdir()
        assert reg._is_slug_explicitly_named("crée une app mobile", proj) is False

    def test_normalized_accents(self, tmp_path):
        proj = tmp_path / "site-ecommerce"
        proj.mkdir()
        # "écommerce" normalisé = "ecommerce"
        assert reg._is_slug_explicitly_named("reprends site-écommerce", proj) is True

    def test_exact_match_one_word_slug(self, tmp_path):
        proj = tmp_path / "lumena"
        proj.mkdir()
        assert reg._is_slug_explicitly_named("teste lumena", proj) is True


# ─────────────────────────────────────────────────────────────────────────────
# resolve_workspace — cascade complète
# ─────────────────────────────────────────────────────────────────────────────

class TestResolveWorkspace:

    # ── Étape 1 : contexte pré-résolu ────────────────────────────────────────

    def test_context_preresolved_bypasses_all(self, workspace):
        proj = _make_project(workspace, "mon-projet")
        result = reg.resolve_workspace(
            "n'importe quoi",
            context={"project_dir": str(proj)},
        )
        assert result.path == proj
        assert result.source == "context"
        assert result.confidence == 1.0

    # ── Étape 2 : chemin absolu explicite dans la query ───────────────────────

    def test_explicit_absolute_path_in_query(self, workspace):
        proj = _make_project(workspace, "mon-projet")
        result = reg.resolve_workspace(
            f'ouvre le projet dans "{proj}"',
        )
        assert result.path == proj
        assert result.source == "explicit"

    # ── Create + fallback → création (cas existant) ───────────────────────────

    def test_create_fallback_no_recent_creates_new(self, workspace):
        """BUG EXISTANT COUVERT : intent=create + fallback + pas récent → nouveau projet."""
        _make_project_old(workspace, "projet-images")
        result = reg.resolve_workspace("crée-moi un site SaaS")
        assert result.intent == "create"
        assert result.source == "created"
        assert result.path is not None
        assert result.path.name != "projet-images"

    # ── THE FIX : create + match flou non-explicite → création ───────────────

    def test_create_ambiguous_word_match_creates_new(self, workspace):
        """LE BUG CORRIGÉ : 'crée un site images' ne doit PAS router via registry vers projet-images."""
        _make_project_old(workspace, "projet-images")
        result = reg.resolve_workspace("crée un site avec des images")
        # Le routing doit passer par "created" (step 4), jamais par "registry"
        assert result.intent == "create"
        assert result.source == "created"
        assert result.path is not None

    def test_create_saas_word_match_creates_new(self, workspace):
        """'crée-moi un site SaaS' ne doit pas router vers projet-saas existant."""
        _make_project_old(workspace, "projet-saas")
        result = reg.resolve_workspace("crée-moi un site SaaS")
        assert result.intent == "create"
        assert result.source == "created"

    def test_create_dashboard_word_match_creates_new(self, workspace):
        _make_project_old(workspace, "site-dashboard")
        result = reg.resolve_workspace("génère un dashboard pour mes finances")
        assert result.intent == "create"
        assert result.source == "created"

    # ── Create + slug explicite → modify ─────────────────────────────────────

    def test_create_explicit_slug_routes_to_modify(self, workspace):
        """'crée une page pour echo-drift' → modify echo-drift (slug cité explicitement)."""
        proj = _make_project_old(workspace, "echo-drift")
        result = reg.resolve_workspace("crée une nouvelle page pour echo-drift")
        assert result.intent == "modify"
        assert result.path == proj

    def test_create_explicit_slug_spaces_routes_to_modify(self, workspace):
        """'crée une page pour site ecommerce' → modify (slug normalisé avec espaces)."""
        proj = _make_project_old(workspace, "site-ecommerce")
        result = reg.resolve_workspace("crée une page produit pour site ecommerce")
        assert result.intent == "modify"
        assert result.path == proj

    # ── Create + projet récemment actif → modify (continuation) ──────────────

    def test_create_recently_active_routes_to_modify(self, workspace):
        """intent=create + projet actif depuis 2 min → modify (continuation conversation)."""
        proj = _make_project_recent(workspace, "projet-images", minutes_ago=2)
        result = reg.resolve_workspace("crée un bouton pour ce site")
        assert result.intent == "modify"
        assert result.path == proj

    def test_create_recently_active_even_with_fuzzy_match(self, workspace):
        """Projet actif récemment, même avec un match flou → toujours modify."""
        proj = _make_project_recent(workspace, "projet-images", minutes_ago=5)
        result = reg.resolve_workspace("crée un site images")
        assert result.intent == "modify"
        assert result.path == proj

    def test_create_old_project_not_recently_active(self, workspace):
        """Projet actif il y a 2h → hors fenêtre → pas de biais récent."""
        _make_project_old(workspace, "projet-landing")
        result = reg.resolve_workspace("crée un site landing page")
        # "landing" seul dans le slug "projet-landing" → "projet landing" pas dans la query
        assert result.source == "created"

    # ── Modify → toujours router vers projet existant ─────────────────────────

    def test_modify_routes_to_existing_project(self, workspace):
        proj = _make_project_old(workspace, "echo-drift")
        result = reg.resolve_workspace("corrige le bug dans echo-drift")
        assert result.intent == "modify"
        assert result.path == proj

    def test_modify_fuzzy_match_routes_correctly(self, workspace):
        """Pour modify, le match flou est légitime — on doit trouver le bon projet."""
        proj = _make_project_old(workspace, "projet-portfolio")
        result = reg.resolve_workspace("améliore le portfolio")
        assert result.intent == "modify"
        assert result.path == proj

    # ── Unknown + récent → modify ─────────────────────────────────────────────

    def test_unknown_recently_active_is_modify(self, workspace):
        proj = _make_project_recent(workspace, "mon-projet", minutes_ago=3)
        result = reg.resolve_workspace("c'est bon pour ça")
        assert result.intent == "modify"
        assert result.path == proj

    def test_unknown_not_recent_returns_low_confidence_registry(self, workspace):
        """intent=unknown + projet existant (fallback non-récent) → registry conf=0.4.

        Le système route vers le projet existant mais avec une confiance faible (0.4)
        pour éviter le fast-route CodeAgent sur des messages sans rapport.
        """
        _make_project_old(workspace, "mon-projet")
        result = reg.resolve_workspace("quelque chose de nouveau")
        assert result.source == "registry"
        assert result.confidence == pytest.approx(0.4)

    # ── Aucun projet existant → création ─────────────────────────────────────

    def test_no_project_creates_new(self, workspace):
        result = reg.resolve_workspace("crée un site vitrine pour un restaurant")
        assert result.intent == "create"
        assert result.source == "created"
        assert result.path is not None
        assert result.path.is_dir()

    def test_no_project_slug_generated_from_query(self, workspace):
        result = reg.resolve_workspace("crée un site vitrine pour un restaurant")
        # Le slug doit contenir des mots significatifs de la query
        assert result.path is not None
        slug = result.path.name
        assert len(slug) > 0
        assert slug != "projet"

    # ── allow_create=False → None si pas de match ─────────────────────────────

    def test_allow_create_false_returns_none_when_no_match(self, workspace):
        result = reg.resolve_workspace(
            "crée un truc complètement nouveau",
            allow_create=False,
        )
        assert result.path is None
        assert result.source == "fallback"
        assert result.confidence == 0.0

    # ── Confiance (confidence) ────────────────────────────────────────────────

    def test_context_confidence_is_1(self, workspace):
        proj = _make_project(workspace, "mon-projet")
        result = reg.resolve_workspace("n/a", context={"project_dir": str(proj)})
        assert result.confidence == 1.0

    def test_modify_real_match_confidence_08(self, workspace):
        _make_project_old(workspace, "echo-drift")
        result = reg.resolve_workspace("corrige echo-drift")
        assert result.confidence == pytest.approx(0.8)

    def test_unknown_match_confidence_05(self, workspace):
        _make_project_old(workspace, "echo-drift")
        result = reg.resolve_workspace("echo-drift")  # pas de verbe d'action
        assert result.confidence == pytest.approx(0.5)

    def test_recently_active_fallback_confidence_075(self, workspace):
        _make_project_recent(workspace, "mon-projet", minutes_ago=3)
        result = reg.resolve_workspace("et pour ça ?")
        # fallback + recently active → conf 0.75
        assert result.confidence == pytest.approx(0.75)

    # ── Source correcte selon le chemin de résolution ─────────────────────────

    def test_source_registry_on_slug_match(self, workspace):
        _make_project_old(workspace, "echo-drift")
        result = reg.resolve_workspace("améliore echo-drift")
        assert result.source == "registry"

    def test_source_created_on_new_project(self, workspace):
        result = reg.resolve_workspace("crée une app de todo")
        assert result.source == "created"

    def test_source_explicit_on_absolute_path(self, workspace):
        proj = _make_project(workspace, "mon-projet")
        result = reg.resolve_workspace(f'dans "{proj}"')
        assert result.source == "explicit"
