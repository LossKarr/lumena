"""
Diagnostic : le CodeAgent est réservé au développement, pas à la création de
documents (PDF/DOCX/rapport texte).

Contexte : un log prod (2026-05-27) a montré ReAct déléguant « Crée un PDF
premium » au CodeAgent, qui a interprété la tâche comme une recherche de code
(grep dans src/) et a échoué en silence — puis le modèle a halluciné la
réussite. La frontière correcte existait dans file_categories mais n'était pas
branchée sur delegate_task.

Ces tests vérifient :
  1. looks_like_document_creation() distingue doc vs code (pas de faux positifs
     sur « script qui génère un PDF »).
  2. delegate_task_handler refuse une tâche document pour code/debug/refactor
     et redirige vers les outils directs.
"""
from __future__ import annotations

import pytest
from unittest.mock import MagicMock

from src.reasoning.file_categories import looks_like_document_creation
from src.reasoning.handlers.agents import delegate_task_handler


# ─────────────────────────────────────────────────────────────────────────────
# 1. Détection pure
# ─────────────────────────────────────────────────────────────────────────────

class TestLooksLikeDocumentCreation:
    @pytest.mark.parametrize("desc", [
        "Crée un PDF premium du rapport mensuel de mai 2026",
        "fais-moi le rapport en pdf",
        "génère une facture pour le client",
        "rédige une lettre de motivation en docx",
        "prépare une présentation powerpoint",
        "mets ça dans un document Word",
    ])
    def test_detects_document_tasks(self, desc):
        assert looks_like_document_creation(desc) is True

    @pytest.mark.parametrize("desc", [
        "crée un script Python qui génère un PDF avec reportlab",
        "corrige le bug dans la fonction export_pdf",
        "refactor le module qui produit les rapports",
        "ajoute un endpoint API pour télécharger le document",
        "écris des tests pour la génération de facture",
        "implémente une classe DocumentBuilder en TypeScript",
    ])
    def test_ignores_dev_tasks_mentioning_documents(self, desc):
        # Signal code présent → ne doit PAS être traité comme tâche document.
        assert looks_like_document_creation(desc) is False

    def test_empty_description(self):
        assert looks_like_document_creation("") is False
        assert looks_like_document_creation(None) is False  # type: ignore[arg-type]


# ─────────────────────────────────────────────────────────────────────────────
# 2. Garde-fou dans delegate_task_handler
# ─────────────────────────────────────────────────────────────────────────────

class TestDelegateTaskDocumentGuard:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("agent_type", ["code", "debug", "refactor", "CODE", " Code "])
    async def test_blocks_document_task_for_dev_agents(self, agent_type):
        ctx = MagicMock()
        result = await delegate_task_handler(
            ctx,
            description="Crée un PDF premium du rapport mensuel",
            agent_type=agent_type,
        )
        assert result.success is False
        assert "create_pdf" in result.error or "développement" in result.error

    @pytest.mark.asyncio
    async def test_dev_task_not_blocked(self):
        # Tâche de dev mentionnant un document → ne doit PAS être bloquée par
        # le garde-fou (elle peut échouer plus loin faute de contexte, mais
        # surtout pas avec le message de redirection document).
        ctx = MagicMock()
        ctx.original_user_query = ""
        ctx.runtime_root = None
        result = await delegate_task_handler(
            ctx,
            description="corrige le bug dans la fonction generate_pdf du module export",
            agent_type="code",
        )
        if result.success is False:
            assert "réservé au développement" not in (result.error or "")
