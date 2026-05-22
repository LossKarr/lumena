"""
Phase 0.3 — Tests pour `_is_local_bugfix_task` après ajout du bypass investigation.

Contexte (DIAGNOSTIC_PROD.md §12 / session 15:26 du 16/05) :
La fonction classait faussement "corrige le site cassé, à toi de trouver"
comme un bugfix local, ce qui désactivait Architect sur ce qui était en
réalité un chantier multi-fichiers (3 fichiers, 1440 lignes).

Le patch ajoute un bypass `investigation_markers` qui repère les phrases
d'investigation et désactive le mode "local bugfix" pour ces cas.

Les vrais bugfix locaux (1 fichier, 1 ligne) restent classifiés correctement.
"""

from __future__ import annotations

import pytest


# ── Tests : phrases d'investigation doivent activer Architect ────────────────


@pytest.mark.parametrize("desc", [
    "corrige le site cassé, à toi de trouver les bugs",
    "Corriger le site web Lumena Landing à toi de trouver et corriger",
    "analyse et corrige tous les problèmes du site",
    "trouve et corrige tous les bugs",
    "audit complet du site web",
    "fais un audit du code",
    "analyse complète du projet et corrige",
    "analyse complete du projet et corrige",
    "identifie les problèmes du site",
    "cherche les bugs dans le projet",
    "Analyse et corrige TOUS les problèmes du site web",
])
def test_investigation_keywords_disable_local_bugfix(desc):
    """Phase 0.3 — Phrases d'investigation doivent désactiver le mode local
    (= activer Architect pour planifier la correction multi-fichiers).
    """
    from src.agents.sub_agent import _is_local_bugfix_task

    result = _is_local_bugfix_task(
        desc, workspace_path="/tmp/x", resolved_intent="modify"
    )
    assert result is False, (
        f"Investigation classée à tort comme bugfix local : {desc!r}\n"
        f"Architect aurait été désactivé à tort."
    )


# ── Tests : vrais bugfix locaux restent classifiés correctement ──────────────


@pytest.mark.parametrize("desc", [
    "fix le bug ligne 42",
    "corrige l'erreur de syntaxe dans app.py",
    "le bouton ne marche pas",
    "répare l'entrée du formulaire",
    "fix crash au démarrage",
    "le clic ne fait rien",
    "corrige la touche enter qui bloque",
])
def test_simple_bugfix_still_classified_as_local(desc):
    """Phase 0.3 — Les vrais bugfix locaux restent True (Architect skip pour ces cas)."""
    from src.agents.sub_agent import _is_local_bugfix_task

    result = _is_local_bugfix_task(
        desc, workspace_path="/tmp/x", resolved_intent="modify"
    )
    assert result is True, (
        f"Vrai bugfix local mal classifié : {desc!r}\n"
        f"Architect serait activé à tort sur un petit fix ciblé."
    )


# ── Invariants préservés ──────────────────────────────────────────────────────


def test_broad_scope_markers_still_disable_local():
    """Les markers existants (refonte, rewrite, migration) restent prioritaires."""
    from src.agents.sub_agent import _is_local_bugfix_task

    cases_broad = [
        "refonte du site",
        "rewrite from scratch",
        "migre vers React",
        "restructure tout le projet",
        "architecture monorepo",
    ]
    for desc in cases_broad:
        assert _is_local_bugfix_task(
            desc, workspace_path="/tmp/x", resolved_intent="modify"
        ) is False, f"Broad scope marker ignoré : {desc!r}"


def test_no_workspace_no_local_classification():
    """Sans workspace_path NI file_targets, jamais local bugfix."""
    from src.agents.sub_agent import _is_local_bugfix_task

    assert _is_local_bugfix_task(
        "corrige le bug", workspace_path=None, resolved_intent="modify"
    ) is False


def test_wrong_intent_no_local_classification():
    """Intent != modify/read → jamais local bugfix."""
    from src.agents.sub_agent import _is_local_bugfix_task

    for intent in ("create", "unknown", "auto"):
        assert _is_local_bugfix_task(
            "corrige le bug", workspace_path="/tmp/x", resolved_intent=intent
        ) is False, f"Intent {intent!r} mal géré"


def test_empty_description_returns_false():
    """Description vide → False."""
    from src.agents.sub_agent import _is_local_bugfix_task

    assert _is_local_bugfix_task(
        "", workspace_path="/tmp/x", resolved_intent="modify"
    ) is False
    assert _is_local_bugfix_task(
        None, workspace_path="/tmp/x", resolved_intent="modify"
    ) is False


def test_priority_investigation_over_local_markers():
    """Si une phrase contient À LA FOIS investigation ET local markers,
    investigation l'emporte (= Architect activé)."""
    from src.agents.sub_agent import _is_local_bugfix_task

    # Contient "fix" (local) ET "tous les bugs" (investigation)
    desc = "fix tous les bugs du site"
    assert _is_local_bugfix_task(
        desc, workspace_path="/tmp/x", resolved_intent="modify"
    ) is False, (
        "Phrase d'investigation avec mots locaux doit privilégier Architect"
    )


# ── Phase 0.4 — Cible globale sans fichier précis ────────────────────────────


@pytest.mark.parametrize("desc", [
    # Cas réel observé (session 15:44 du 16/05) — ReAct reformule
    "Corriger les problèmes du site Lumena landing page",
    "Corrige le site",
    "Fix le projet",
    "Corrige les bugs du site",
    "Corriger la page d'accueil",
    "Fix le projet web",
    "Répare l'application",
    "Corrige l app",
    "Corrige toutes les pages",
    "Corrige tout le site",
    "Corrige ce site",
    "Corriger les bugs du projet",
])
def test_global_target_disables_local_bugfix_phase04(desc):
    """Phase 0.4 — Cible globale (site/projet/page/app) sans fichier précis
    désactive le mode local bugfix → Architect activé.

    Couvre le cas où ReAct a reformulé une demande d'investigation utilisateur
    en une description plus courte mais ambiguë.
    """
    from src.agents.sub_agent import _is_local_bugfix_task

    result = _is_local_bugfix_task(
        desc, workspace_path="/tmp/x", resolved_intent="modify"
    )
    assert result is False, (
        f"Cible globale classée à tort comme bugfix local : {desc!r}\n"
        f"Architect aurait été désactivé pour un chantier multi-fichiers."
    )


@pytest.mark.parametrize("desc", [
    # Vrais bugfix locaux ciblant UN fichier explicite — ne doivent PAS être bypassés
    "fix le bug ligne 42 dans app.py",
    "corrige main.py qui plante",
    "corrige styles.css",
])
def test_specific_file_still_classified_as_local_phase04(desc):
    """Phase 0.4 — Si un fichier précis est mentionné, on reste en mode local."""
    from src.agents.sub_agent import _is_local_bugfix_task

    result = _is_local_bugfix_task(
        desc, workspace_path="/tmp/x", resolved_intent="modify"
    )
    assert result is True, (
        f"Bugfix avec fichier précis mal classifié : {desc!r}"
    )


def test_global_target_without_workspace_no_effect():
    """Phase 0.4 — Sans workspace_path, l'heuristique de cible globale ne
    s'applique pas (sinon on bypasserait toute description vague)."""
    from src.agents.sub_agent import _is_local_bugfix_task

    # Sans workspace : pas de local bugfix de toute façon (cas géré avant)
    assert _is_local_bugfix_task(
        "corrige le site", workspace_path=None, resolved_intent="modify"
    ) is False  # déjà False parce que pas de workspace + pas de file_targets


def test_global_target_does_not_break_specific_targets():
    """Si la description mentionne ET site ET un fichier précis, le fichier
    précis l'emporte (le bypass ne s'active que si pas de file_targets)."""
    from src.agents.sub_agent import _is_local_bugfix_task

    # "le site" + "main.py" → file_target présent → mode local conservé
    desc = "corrige le bug dans main.py qui casse le site"
    result = _is_local_bugfix_task(
        desc, workspace_path="/tmp/x", resolved_intent="modify"
    )
    # Note : main.py est un file_target explicite → bypass Phase 0.4 ne s'applique pas
    assert result is True, (
        "Fichier précis mentionné + cible globale → reste local bugfix"
    )
