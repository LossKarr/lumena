"""
Tests Phase 3 — Vérité des statuts de complétion du plan.

Valide que :
  - TaskCompletionStatus distingue created / verified / sent / deployed
  - task_completion_status() renvoie le bon statut selon l'outil et la tâche
  - completion_status_for_proof() mappe capability → statut correctement
  - reconcile_delegate_report() marque les tâches avec CREATED (pas VERIFIED)
  - TaskItem.completion_status est accessible
  - HandlerResult.status_code est propagé

Ces tests sont des tests purs de fonctions isolées — aucune boucle ReAct lancée.
"""
import pytest

from src.reasoning.plan_evidence import (
    TaskCompletionStatus,
    ProofCapability,
    completion_status_for_proof,
    task_completion_status,
    reconcile_delegate_report,
    is_verify_task,
)
from src.reasoning.react_config import TaskItem
from src.reasoning.handlers.contracts import HandlerResult


# ─────────────────────────────────────────────────────────────────────────────
# TaskCompletionStatus — constantes et sémantique
# ─────────────────────────────────────────────────────────────────────────────

class TestTaskCompletionStatusConstants:
    def test_created_ne_verified(self):
        assert TaskCompletionStatus.CREATED != TaskCompletionStatus.VERIFIED

    def test_sent_distinct(self):
        assert TaskCompletionStatus.SENT not in (
            TaskCompletionStatus.CREATED,
            TaskCompletionStatus.VERIFIED,
            TaskCompletionStatus.DEPLOYED,
        )

    def test_unknown_is_empty_string(self):
        assert TaskCompletionStatus.UNKNOWN == ""


# ─────────────────────────────────────────────────────────────────────────────
# completion_status_for_proof()
# ─────────────────────────────────────────────────────────────────────────────

class TestCompletionStatusForProof:
    def test_none_cap_gives_created(self):
        assert completion_status_for_proof(None, False) == TaskCompletionStatus.CREATED

    def test_message_send_gives_sent(self):
        assert completion_status_for_proof(ProofCapability.MESSAGE_SEND, False) == TaskCompletionStatus.SENT

    def test_message_send_with_verify_still_sent(self):
        assert completion_status_for_proof(ProofCapability.MESSAGE_SEND, True) == TaskCompletionStatus.SENT

    def test_deploy_mutation_gives_deployed(self):
        assert completion_status_for_proof(ProofCapability.DEPLOY_MUTATION, False) == TaskCompletionStatus.DEPLOYED

    def test_http_probe_with_verify_gives_verified(self):
        assert completion_status_for_proof(ProofCapability.HTTP_PROBE, True) == TaskCompletionStatus.VERIFIED

    def test_browser_probe_with_verify_gives_verified(self):
        assert completion_status_for_proof(ProofCapability.BROWSER_PROBE, True) == TaskCompletionStatus.VERIFIED

    def test_test_execution_with_verify_gives_verified(self):
        assert completion_status_for_proof(ProofCapability.TEST_EXECUTION, True) == TaskCompletionStatus.VERIFIED

    def test_process_launch_with_verify_gives_verified(self):
        assert completion_status_for_proof(ProofCapability.PROCESS_LAUNCH, True) == TaskCompletionStatus.VERIFIED

    def test_http_probe_without_verify_gives_created(self):
        # Une tâche de création qui passe via health_check → CREATED, pas VERIFIED
        assert completion_status_for_proof(ProofCapability.HTTP_PROBE, False) == TaskCompletionStatus.CREATED

    def test_file_write_gives_created(self):
        assert completion_status_for_proof(ProofCapability.FILE_WRITE, False) == TaskCompletionStatus.CREATED

    def test_file_write_with_verify_gives_created(self):
        # FILE_WRITE n'est pas dans _VERIFIED_CAPABILITIES → toujours CREATED
        assert completion_status_for_proof(ProofCapability.FILE_WRITE, True) == TaskCompletionStatus.CREATED

    def test_payment_mutation_gives_created(self):
        assert completion_status_for_proof(ProofCapability.PAYMENT_MUTATION, False) == TaskCompletionStatus.CREATED


# ─────────────────────────────────────────────────────────────────────────────
# task_completion_status() — helper react.py
# ─────────────────────────────────────────────────────────────────────────────

class TestTaskCompletionStatusHelper:
    def test_run_command_verify_task_gives_verified(self):
        desc = "vérifier que le serveur est fonctionnel"
        status = task_completion_status("run_command", desc, "system", "system")
        assert status == TaskCompletionStatus.VERIFIED

    def test_run_command_create_task_gives_created(self):
        desc = "créer le répertoire de logs"
        status = task_completion_status("run_command", desc, "system", "system")
        assert status == TaskCompletionStatus.CREATED

    def test_health_check_verify_gives_verified(self):
        desc = "vérifier que l'API est accessible"
        status = task_completion_status("health_check", desc, "system", "system")
        assert status == TaskCompletionStatus.VERIFIED

    def test_write_file_create_task_gives_created(self):
        desc = "créer le fichier de configuration"
        status = task_completion_status("write_file", desc, "files", "files")
        assert status == TaskCompletionStatus.CREATED

    def test_send_email_gives_sent(self):
        # send_email est dans la catégorie communication → MESSAGE_SEND
        desc = "envoyer le rapport par email"
        status = task_completion_status("send_email", desc, "communication", "mail")
        assert status == TaskCompletionStatus.SENT

    def test_github_push_gives_deployed(self):
        # github_push → DEPLOY_MUTATION (override)
        desc = "déployer le site en production"
        status = task_completion_status("github_push", desc, "github", "github")
        assert status == TaskCompletionStatus.DEPLOYED

    def test_unknown_tool_create_gives_created(self):
        desc = "créer la base de données"
        status = task_completion_status("some_new_unknown_tool", desc, "", "")
        assert status == TaskCompletionStatus.CREATED

    def test_discord_message_gives_sent(self):
        # discord_admin module → communication → MESSAGE_SEND
        desc = "envoyer le message de confirmation"
        status = task_completion_status("discord_send_message", desc, "communication", "discord")
        assert status == TaskCompletionStatus.SENT

    def test_browser_probe_verify_gives_verified(self):
        desc = "vérifier que le tableau de bord est accessible"
        status = task_completion_status("browser_navigate", desc, "browser", "browser")
        assert status == TaskCompletionStatus.VERIFIED


# ─────────────────────────────────────────────────────────────────────────────
# TaskItem.completion_status — champ présent et initialisé à ""
# ─────────────────────────────────────────────────────────────────────────────

class TestTaskItemCompletionStatus:
    def test_default_completion_status_is_empty(self):
        task = TaskItem(description="Créer le projet")
        assert task.completion_status == ""

    def test_completion_status_settable(self):
        task = TaskItem(description="Vérifier que l'app tourne")
        task.completion_status = TaskCompletionStatus.VERIFIED
        assert task.completion_status == TaskCompletionStatus.VERIFIED

    def test_completion_status_created(self):
        task = TaskItem(description="Créer le fichier")
        task.completed = True
        task.completed_by_tool = "write_file"
        task.completion_status = TaskCompletionStatus.CREATED
        assert task.completion_status == "created"


# ─────────────────────────────────────────────────────────────────────────────
# reconcile_delegate_report — marque CREATED, pas VERIFIED
# ─────────────────────────────────────────────────────────────────────────────

class TestReconcileDelegateReportStatus:
    def test_delegate_marks_created_not_verified(self):
        plan = [
            TaskItem(description="Créer le projet MoodForge complet"),
            TaskItem(description="Vérifier que l'application fonctionne"),
        ]
        obs = "Le projet MoodForge a été créé avec succès, tous les fichiers sont en place."
        marked = reconcile_delegate_report(plan, obs, iteration=3)

        assert marked == 1
        assert plan[0].completed is True
        assert plan[0].completion_status == TaskCompletionStatus.CREATED

    def test_verify_task_not_reconciled(self):
        plan = [
            TaskItem(description="Vérifier que l'application fonctionne correctement"),
        ]
        obs = "L'application fonctionne correctement, tout est opérationnel."
        marked = reconcile_delegate_report(plan, obs, iteration=1)

        # Les tâches de vérification sont exclues de la réconciliation delegate
        assert marked == 0
        assert plan[0].completed is False
        assert plan[0].completion_status == ""

    def test_delegate_preserves_created_status(self):
        plan = [
            TaskItem(description="Générer le rapport mensuel complet"),
        ]
        obs = "Le rapport mensuel complet a été généré et exporté."
        reconcile_delegate_report(plan, obs, iteration=2)

        assert plan[0].completion_status == TaskCompletionStatus.CREATED


# ─────────────────────────────────────────────────────────────────────────────
# HandlerResult.status_code — propagation
# ─────────────────────────────────────────────────────────────────────────────

class TestHandlerResultStatusCode:
    def test_default_status_code_is_empty(self):
        result = HandlerResult.ok("OK")
        assert result.status_code == ""

    def test_ok_with_status_code(self):
        result = HandlerResult.ok("Succès", status_code="success")
        assert result.status_code == "success"
        assert result.success is True

    def test_fail_with_status_code(self):
        result = HandlerResult.fail("Erreur", status_code="error")
        assert result.status_code == "error"
        assert result.success is False

    def test_fail_with_partial_status(self):
        result = HandlerResult.fail("Partiel", status_code="partial")
        assert result.status_code == "partial"

    def test_status_code_propagated_in_output(self):
        result = HandlerResult.ok("Projet créé", handler_name="delegate_task", status_code="success")
        assert result.handler_name == "delegate_task"
        assert result.status_code == "success"
        assert result.output == "Projet créé"
