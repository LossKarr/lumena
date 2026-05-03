"""
Tests — Clôture de plan après delegate_task (Sujet 2).

Valide :
  - La condition de bypass _after_delegate_success est correcte :
    aucun bypass si des verify-tasks restent non résolues
  - is_verify_task détecte correctement les tâches de vérification
    qui doivent bloquer le bypass
  - reconcile_delegate_report ne marque pas les verify-tasks
  - reconcile_delegate_report marque correctement les non-verify tasks
  - L'état du plan reflète la réalité après une délégation partielle
  - [Intégration] ReActLoop._after_delegate_success → FINAL : bypass
    s'active sans verify-task pendante, et est annulé avec verify-task pendante
"""
from __future__ import annotations

import asyncio
import pytest
from unittest.mock import patch

from src.reasoning.plan_evidence import (
    is_verify_task,
    reconcile_delegate_report,
    TaskCompletionStatus,
)
from src.reasoning.react_config import TaskItem


# ─────────────────────────────────────────────────────────────────────────────
# is_verify_task — détection correcte
# ─────────────────────────────────────────────────────────────────────────────

class TestIsVerifyTaskForBypassGuard:
    """Ces tâches doivent être détectées comme verify-tasks et bloquer le bypass."""

    def test_verifier_explicit(self):
        assert is_verify_task("vérifier que le site fonctionne")

    def test_tester_accessible(self):
        assert is_verify_task("tester que l'application est accessible")

    def test_confirmer(self):
        assert is_verify_task("confirmer le déploiement en production")

    def test_fonctionnel(self):
        assert is_verify_task("s'assurer que tout est fonctionnel")

    def test_valider(self):
        assert is_verify_task("valider que les endpoints répondent")

    def test_operationnel(self):
        assert is_verify_task("vérifier que le serveur est opérationnel")

    def test_non_verify_create(self):
        assert not is_verify_task("créer le projet moodforge")

    def test_non_verify_write(self):
        assert not is_verify_task("écrire le fichier css principal")

    def test_non_verify_synthese(self):
        assert not is_verify_task("résumer les changements effectués")

    def test_non_verify_deploy(self):
        assert not is_verify_task("déployer sur le serveur ionos")


# ─────────────────────────────────────────────────────────────────────────────
# Condition de bypass _after_delegate_success
# ─────────────────────────────────────────────────────────────────────────────

class TestAfterDelegateBypassCondition:
    """Vérifie la logique de condition du bypass sans instancier ReActLoop."""

    def _pending_verify(self, task_plan):
        """Reproduit la condition extraite de react.py."""
        return [
            t for t in task_plan
            if not t.completed and is_verify_task(t.description.lower())
        ]

    def test_no_pending_verify_bypass_ok(self):
        """Sans verify-task pendante → bypass autorisé."""
        tasks = [
            TaskItem(description="Créer les fichiers HTML", completed=True),
            TaskItem(description="Déployer sur le serveur", completed=True),
        ]
        assert len(self._pending_verify(tasks)) == 0

    def test_pending_verify_blocks_bypass(self):
        """Avec une verify-task pendante → bypass annulé."""
        tasks = [
            TaskItem(description="Créer les fichiers HTML", completed=True),
            TaskItem(description="Vérifier que le site fonctionne", completed=False),
        ]
        pending = self._pending_verify(tasks)
        assert len(pending) == 1
        assert pending[0].description == "Vérifier que le site fonctionne"

    def test_multiple_pending_verify_blocks_bypass(self):
        """Plusieurs verify-tasks pendantes → bypass annulé."""
        tasks = [
            TaskItem(description="Créer l'application", completed=True),
            TaskItem(description="Vérifier que l'application fonctionne", completed=False),
            TaskItem(description="Confirmer que les tests passent", completed=False),
        ]
        pending = self._pending_verify(tasks)
        assert len(pending) == 2

    def test_completed_verify_task_no_block(self):
        """Verify-task déjà complétée → pas de blocage du bypass."""
        tasks = [
            TaskItem(description="Créer les fichiers", completed=True),
            TaskItem(description="Vérifier que le site fonctionne", completed=True),
        ]
        assert len(self._pending_verify(tasks)) == 0

    def test_empty_plan_no_block(self):
        """Plan vide → bypass autorisé."""
        assert len(self._pending_verify([])) == 0

    def test_all_non_verify_completed(self):
        """Toutes les tâches non-verify sont complétées → bypass OK."""
        tasks = [
            TaskItem(description="Générer le rapport mensuel complet", completed=True),
            TaskItem(description="Envoyer le rapport par mail", completed=True),
        ]
        assert len(self._pending_verify(tasks)) == 0


# ─────────────────────────────────────────────────────────────────────────────
# reconcile_delegate_report — intégration plan fermé vs ouvert
# ─────────────────────────────────────────────────────────────────────────────

class TestReconcileVsVerifyGate:
    """La réconciliation après delegate_task ne doit pas marquer les verify-tasks."""

    def test_verify_task_stays_uncompleted_after_reconcile(self):
        """Une verify-task doit rester non complétée même si l'obs contient ses mots."""
        plan = [
            TaskItem(description="Créer le projet MoodForge complet"),
            TaskItem(description="Vérifier que l'application fonctionne"),
        ]
        obs = "✅ Projet MoodForge créé, application fonctionnelle et prête"
        marked = reconcile_delegate_report(plan, obs, iteration=1)

        assert plan[0].completed is True      # non-verify → marquée
        assert plan[1].completed is False     # verify → non marquée
        assert marked == 1

    def test_plan_reflects_partial_completion(self):
        """Après delegate_task partiel : non-verify marquées, verify restent pendantes.

        Les mots utilisés sont des stems exacts : l'algorithme fait du substring matching
        donc 'serveur' doit apparaître dans l'obs tel quel (pas de morphologie).
        """
        plan = [
            TaskItem(description="Configurer le serveur Express complet"),
            TaskItem(description="Créer le projet MoodForge avec tous les fichiers"),
            TaskItem(description="Vérifier que le site est accessible en production"),
            TaskItem(description="Confirmer que les tests d'intégration passent"),
        ]
        obs = (
            "✅ Serveur Express configuré sur le port 3000. "
            "Projet MoodForge créé avec tous les fichiers. "
            "Tests non effectués — environnement de test non disponible."
        )
        marked = reconcile_delegate_report(plan, obs, iteration=2)

        # Les 2 tâches de création doivent être marquées (mots présents dans l'obs)
        assert plan[0].completed is True   # "serveur", "express", "complet"... → ≥2 hits
        assert plan[1].completed is True   # "moodforge", "fichiers" → ≥2 hits
        # Les 2 verify-tasks doivent rester non marquées (verify-gate)
        assert plan[2].completed is False
        assert plan[3].completed is False
        assert marked == 2

    def test_reconcile_status_is_created_not_verified(self):
        """Les tâches marquées par réconciliation reçoivent le statut CREATED."""
        plan = [TaskItem(description="Créer le projet MoodForge complet")]
        obs = "✅ Projet MoodForge créé avec succès"
        reconcile_delegate_report(plan, obs, iteration=1)
        assert plan[0].completion_status == TaskCompletionStatus.CREATED

    def test_bypass_condition_consistent_with_reconcile(self):
        """Après réconciliation, la condition de bypass reflète correctement l'état."""
        plan = [
            TaskItem(description="Créer le projet complet"),
            TaskItem(description="Vérifier que tout fonctionne correctement"),
        ]
        obs = "✅ Projet créé avec succès"
        reconcile_delegate_report(plan, obs, iteration=1)

        # Après réconciliation : 1 non-verify complétée, 1 verify encore pendante
        pending_verify = [
            t for t in plan
            if not t.completed and is_verify_task(t.description.lower())
        ]
        assert len(pending_verify) == 1  # le bypass doit être annulé
        assert pending_verify[0].description == "Vérifier que tout fonctionne correctement"


# ─────────────────────────────────────────────────────────────────────────────
# Intégration — ReActLoop._after_delegate_success → FINAL (bout en bout)
# ─────────────────────────────────────────────────────────────────────────────

class TestAfterDelegateSuccessIntegration:
    """Vérifie le comportement de bout en bout du bypass _after_delegate_success.

    Stratégie :
      - ReActLoop est créé avec un mock LLM qui :
          1. Positionne _after_delegate_success = True (simule le retour d'un
             delegate_task ✅ qui a déjà eu lieu)
          2. Retourne immédiatement ACTION: FINAL
      - Le plan est pré-chargé (tâches complètes ou verify pendante)
      - On patche _mark_task_done pour capturer quel chemin a été emprunté
    """

    def _make_loop_with_plan(self, tasks):
        """Crée un ReActLoop avec plan pré-chargé et mock LLM.

        Le mock LLM positionne _after_delegate_success = True avant de
        retourner FINAL, simulant un run post-delegate_task.
        """
        from src.reasoning.react import ReActLoop

        loop_holder = {}

        async def mock_llm(messages, **kwargs):
            # Simulate state right after a delegate_task ✅ observation:
            # exec_state.reset() s'est déjà exécuté (début de _run_internal),
            # on peut donc positionner le flag ici et il sera stable jusqu'au
            # traitement FINAL dans cette même itération.
            loop_holder["loop"]._after_delegate_success = True
            return (
                "THOUGHT: Le CodeAgent a terminé sa mission.\n"
                "ACTION: FINAL\n"
                "ACTION_INPUT: Projet créé avec succès par le CodeAgent."
            )

        loop = ReActLoop(llm_chat_func=mock_llm)
        loop_holder["loop"] = loop
        loop._task_plan = list(tasks)
        loop._plan_emitted = True
        return loop

    def test_bypass_fires_when_no_pending_verify(self):
        """Sans verify-task pendante → le bypass direct est autorisé.

        _mark_task_done doit être appelé avec le sentinel 'delegate_task_final_direct'.
        """
        from src.reasoning.react import ReActLoop

        tasks = [
            TaskItem(description="Créer le projet MoodForge", completed=True),
            TaskItem(description="Écrire les fichiers CSS et HTML", completed=True),
        ]
        loop = self._make_loop_with_plan(tasks)

        with patch.object(loop, "_mark_task_done") as mock_done:
            result = asyncio.get_event_loop().run_until_complete(
                loop.run("Crée le projet MoodForge complet")
            )

        # Flag consommé dans tous les cas
        assert not loop._after_delegate_success

        # Bypass direct déclenché : sentinel transmis à _mark_task_done
        call_args = [str(c.args) for c in mock_done.call_args_list]
        assert any("delegate_task_final_direct" in a for a in call_args), (
            f"Bypass non déclenché — appels: {call_args}"
        )

        # La réponse est bien celle du mock (pas le message de fallback vide)
        assert result.strip()

    def test_bypass_blocked_when_verify_task_pending(self):
        """Avec une verify-task pendante → bypass annulé, chemin FINAL normal.

        _mark_task_done ne doit PAS être appelé avec 'delegate_task_final_direct'.
        La verify-task doit rester non complétée après le run.

        Note: la description utilise "accessible" (dans _VERIFY_TASK_KEYWORDS mais PAS
        dans _SYNTH_KW) pour éviter l'auto-complétion FINAL qui précède le bypass guard.
        """
        tasks = [
            TaskItem(description="Créer le projet MoodForge", completed=True),
            TaskItem(description="S'assurer que le site est accessible en production", completed=False),
        ]
        loop = self._make_loop_with_plan(tasks)

        with patch.object(loop, "_mark_task_done") as mock_done:
            result = asyncio.get_event_loop().run_until_complete(
                loop.run("Crée le projet et vérifie qu'il fonctionne")
            )

        # Flag consommé (False) même quand bypass annulé
        assert not loop._after_delegate_success

        # Bypass NON déclenché : le sentinel ne doit pas apparaître
        call_args = [str(c.args) for c in mock_done.call_args_list]
        assert not any("delegate_task_final_direct" in a for a in call_args), (
            f"Bypass déclenché à tort — appels: {call_args}"
        )

        # La verify-task doit rester pendante (non marquée par le chemin normal)
        verify_task = loop._task_plan[1]
        assert not verify_task.completed, (
            "La verify-task a été incorrectement marquée comme complétée"
        )

        # La réponse est bien celle du mock
        assert result.strip()

    def test_flag_always_consumed_regardless_of_verify_state(self):
        """_after_delegate_success est toujours False après le run, quelle que soit
        la présence de verify-tasks (pas de fuite d'état entre runs).

        Note: description "accessible" pour éviter l'auto-complétion _SYNTH_KW.
        """
        for has_verify in (True, False):
            tasks = [
                TaskItem(description="Créer les fichiers du projet", completed=True),
            ]
            if has_verify:
                tasks.append(
                    TaskItem(description="S'assurer que l'application est accessible", completed=False)
                )

            loop = self._make_loop_with_plan(tasks)
            asyncio.get_event_loop().run_until_complete(
                loop.run("Test run")
            )
            assert not loop._after_delegate_success, (
                f"Flag non consommé (has_verify={has_verify})"
            )
