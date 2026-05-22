"""
Phase 0.5 — Tests du filtre anti-pollution SuccessStore.

Contexte (DIAGNOSTIC_PROD.md §13 — session 15:55 du 16/05) :
La tâche "corriger le site et casser à certain endroit" a fait capturer dans
SuccessStore un pattern `succ_1382c4d7c4c19513 — Introduire des bugs subtils
en modifiant des constantes numériques`. Lumena apprenait donc à casser les
projets utilisateur sur demande ambiguë.

Le filtre Phase 0.5 bloque la capture si le contexte contient un signal
destructeur sans intention explicite.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ── Tests : normalisation case+accent ─────────────────────────────────────────


def test_normalize_lowercases():
    from src.agents.sub_agent import SubAgent

    n = SubAgent._normalize_text_for_filter
    assert n("CASSER") == "casser"
    assert n("Casse") == "casse"
    assert n("MAJUSCULES MIXÉES") == "majuscules mixees"


def test_normalize_strips_accents():
    from src.agents.sub_agent import SubAgent

    n = SubAgent._normalize_text_for_filter
    assert n("CASSÉ") == "casse"
    assert n("cassé") == "casse"
    assert n("abîmer") == "abimer"
    assert n("Détruire") == "detruire"
    assert n("Régression") == "regression"
    assert n("dégrader") == "degrader"


def test_normalize_handles_empty_and_none():
    from src.agents.sub_agent import SubAgent

    n = SubAgent._normalize_text_for_filter
    assert n("") == ""
    assert n(None) == ""


# ── Tests : signaux destructeurs détectés ─────────────────────────────────────


@pytest.mark.parametrize("desc", [
    "corriger le site et casser à certain endroit",
    "casser la page",
    "Casse cette section",
    "CASSÉ par erreur",
    "introduire des bugs subtils",
    "introduire un bug",
    "buguer l'application",
    "détruire le code",
    "Détruire la base",
    "saboter le système",
    "abîmer la page",
    "abimer le rendu",
    "foirer le déploiement",
    "pourrir le projet",
    "endommager la base de données",
    "dégrader les performances",
    "Régression observée sur la page de connexion",
])
def test_destructive_keywords_block_capture(desc):
    """Phase 0.5 — Tous les mots destructeurs listés bloquent la capture."""
    from src.agents.sub_agent import SubAgent

    blocked, reason = SubAgent._should_block_success_capture(
        task_description=desc, outcome_summary="",
    )
    assert blocked is True, f"Non bloqué : {desc!r}"
    assert reason.startswith("destructif:"), (
        f"Raison mal formatée pour {desc!r} → {reason!r}"
    )


# ── Tests : tâches légitimes laissées passer ──────────────────────────────────


@pytest.mark.parametrize("desc", [
    "Ajoute un soleil 3D dans le jeu",
    "Crée une application web complète appelée AtlasForge",
    "Refactor le module auth pour utiliser JWT",
    "Améliore le style de la landing page",
    "Optimise les performances de la requête SQL",
    "Génère un rapport mensuel",
    "Documente l'API REST",
])
def test_legitimate_tasks_pass_through(desc):
    """Phase 0.5 — Les tâches sans mot destructeur passent normalement."""
    from src.agents.sub_agent import SubAgent

    blocked, reason = SubAgent._should_block_success_capture(
        task_description=desc, outcome_summary="",
    )
    assert blocked is False, (
        f"Tâche légitime bloquée : {desc!r} → {reason}"
    )


# ── Tests : intention explicite annule le blocage ─────────────────────────────


@pytest.mark.parametrize("desc", [
    "casser le système volontairement pour tester",
    "introduire des bugs intentionnellement pour QA",
    "test de bug : casser la fonction X",
    "simuler une défaillance réseau",
    "chaos engineering : casser la prod",
    "dégrader les perfs pour tester le fallback",
    "test destructif : abîmer le pipeline",
])
def test_explicit_intention_allows_capture(desc):
    """Phase 0.5 — Intention explicite (volontairement, test de bug, etc.)
    annule le blocage : Lumena peut apprendre des patterns de test destructif
    quand l'utilisateur le demande clairement."""
    from src.agents.sub_agent import SubAgent

    blocked, reason = SubAgent._should_block_success_capture(
        task_description=desc, outcome_summary="",
    )
    assert blocked is False, (
        f"Intention explicite ignorée : {desc!r} → {reason}"
    )


# ── Tests : inspection multi-source ───────────────────────────────────────────


def test_filter_inspects_outcome_summary():
    """Phase 0.5 — Si la description est neutre mais que l'outcome révèle
    la pollution, on bloque quand même."""
    from src.agents.sub_agent import SubAgent

    blocked, reason = SubAgent._should_block_success_capture(
        task_description="Corriger les fautes d'orthographe",
        outcome_summary="J'ai introduit des bugs subtils dans script.js",
    )
    assert blocked is True, "Outcome destructif non détecté"
    assert "destructif:" in reason


def test_filter_inspects_current_task_description():
    """Phase 0.5 — Si current_task.description contient un mot destructeur,
    on bloque même si task_description (paramètre) est neutre."""
    from src.agents.sub_agent import SubAgent

    task = MagicMock()
    task.description = "casser le site à certains endroits"
    task.context = None

    blocked, reason = SubAgent._should_block_success_capture(
        task_description="résumé court",
        outcome_summary="",
        current_task=task,
    )
    assert blocked is True


def test_filter_inspects_current_task_context():
    """Phase 0.5 — Inspection du context dict (peut contenir le prompt
    utilisateur original sous une clé arbitraire)."""
    from src.agents.sub_agent import SubAgent

    task = MagicMock()
    task.description = "Tâche neutre"
    task.context = {"original_user_prompt": "casser le site à certains endroits"}

    blocked, reason = SubAgent._should_block_success_capture(
        task_description="résumé reformulé",
        outcome_summary="",
        current_task=task,
    )
    assert blocked is True


def test_filter_intention_in_any_source_lifts_block():
    """Phase 0.5 — L'intention explicite peut venir de n'importe quelle source."""
    from src.agents.sub_agent import SubAgent

    # Mot destructif dans task_description, intention dans outcome
    blocked, reason = SubAgent._should_block_success_capture(
        task_description="casser le site",
        outcome_summary="C'était un test destructif, tout fonctionne comme prévu",
    )
    assert blocked is False, (
        "Intention 'test destructif' dans outcome doit lever le blocage"
    )


# ── Tests : robustesse word boundary (pas de faux positifs) ───────────────────


@pytest.mark.parametrize("desc", [
    "Range les casseroles",                    # contient "casse" mais pas word-boundary
    "Debugger la fonction startup",            # contient "bug" mais sous-chaîne
    "Lancer le bugzilla integration",          # idem
    "Code pour saboteur game ennemi",          # "sabote" sous-chaîne dans "saboteur" → word-boundary OK
])
def test_no_false_positive_on_substrings(desc):
    """Phase 0.5 — Les sous-chaînes (casserole, debugger, bugzilla) ne doivent
    PAS déclencher le blocage. Word boundary requis dans les patterns."""
    from src.agents.sub_agent import SubAgent

    blocked, reason = SubAgent._should_block_success_capture(
        task_description=desc, outcome_summary="",
    )
    # Note : "saboteur" contient "sabote" mais avec word boundary \bsaboter\b,
    # le mot complet "saboter" n'est pas matché dans "saboteur".
    assert blocked is False, (
        f"Faux positif sur sous-chaîne : {desc!r} → {reason}"
    )


# ── Tests : intégration bout-en-bout avec _maybe_generate_success_pattern ────


@pytest.mark.asyncio
async def test_maybe_generate_success_pattern_blocks_polluted_task(monkeypatch):
    """Phase 0.5 — Sur une tâche avec mots destructifs, le LLM ne doit jamais
    être appelé et le compteur ne doit pas être incrémenté."""
    from src.agents.sub_agent import CodeAgent

    agent = CodeAgent()
    agent._success_generated_count = 0
    agent.current_task = None

    mock_llm = MagicMock()
    mock_llm.chat = AsyncMock(return_value="never_called")

    with patch.object(agent, "_get_llm", return_value=mock_llm):
        await agent._maybe_generate_success_pattern(
            task_description="corriger le site et casser à certain endroit à toi de trouver",
            tools_used=["apply_patches"],
            iterations=6,
            outcome_summary="",
        )

    # Le LLM ne doit JAMAIS être appelé pour une tâche bloquée
    mock_llm.chat.assert_not_called()
    assert agent._success_generated_count == 0


@pytest.mark.asyncio
async def test_maybe_generate_success_pattern_allows_legitimate_task(monkeypatch, tmp_path):
    """Phase 0.5 — Une tâche légitime (sans mot destructif) doit toujours
    déclencher l'appel LLM et l'enregistrement éventuel."""
    from src.agents.sub_agent import CodeAgent
    from src.learning import success_store as ss_mod

    agent = CodeAgent()
    agent._success_generated_count = 0
    agent.current_task = None

    # Réponse LLM valide
    fake_response = (
        '{"task_type":"bugfix","summary":"Fix authentication bug",'
        '"approach":"read then edit","apply_when":"auth issues",'
        '"tags":["auth"],"confidence":0.8}'
    )
    mock_llm = MagicMock()
    mock_llm.chat = AsyncMock(return_value=fake_response)

    # Isolation du store
    isolated_store = ss_mod.SuccessStore(path=tmp_path / "s.jsonl")
    monkeypatch.setattr(ss_mod, "get_success_store", lambda: isolated_store)

    with patch.object(agent, "_get_llm", return_value=mock_llm):
        await agent._maybe_generate_success_pattern(
            # Note Phase 0.5 : le mot "bug" déclenche le filtre (strict volontaire).
            # On utilise "problème" ici pour valider qu'une tâche légitime SANS
            # mot destructif passe normalement.
            task_description="Corriger un problème d'authentification dans le module login",
            tools_used=["read_file", "edit_lines"],
            iterations=4,
            outcome_summary="Tests passent, authentification OK",
        )

    # Le LLM DOIT avoir été appelé
    mock_llm.chat.assert_called_once()
    assert agent._success_generated_count == 1
    assert len(isolated_store) == 1


@pytest.mark.asyncio
async def test_maybe_generate_success_pattern_allows_intentional_test(monkeypatch, tmp_path):
    """Phase 0.5 — Une tâche destructive AVEC intention explicite
    (volontairement, test de bug) doit passer le filtre."""
    from src.agents.sub_agent import CodeAgent
    from src.learning import success_store as ss_mod

    agent = CodeAgent()
    agent._success_generated_count = 0
    agent.current_task = None

    fake_response = (
        '{"task_type":"other","summary":"Test chaos engineering",'
        '"approach":"simuler panne","apply_when":"qa","tags":["chaos"],"confidence":0.7}'
    )
    mock_llm = MagicMock()
    mock_llm.chat = AsyncMock(return_value=fake_response)

    isolated_store = ss_mod.SuccessStore(path=tmp_path / "s.jsonl")
    monkeypatch.setattr(ss_mod, "get_success_store", lambda: isolated_store)

    with patch.object(agent, "_get_llm", return_value=mock_llm):
        await agent._maybe_generate_success_pattern(
            task_description="Chaos engineering : simuler une panne réseau volontairement",
            tools_used=["run_command"],
            iterations=3,
            outcome_summary="Système a fallback correctement",
        )

    # Intention explicite ("chaos engineering" + "volontairement") → autorisé
    mock_llm.chat.assert_called_once()
    assert agent._success_generated_count == 1
