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
import sys
from unittest.mock import AsyncMock, MagicMock, patch

from src.agents.sub_agent import AgentResult, StatusCode
from src.reasoning.file_categories import looks_like_document_creation
from src.reasoning.handlers.context import HandlerContext
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
    async def test_dev_task_not_blocked(self, tmp_path):
        # Tâche de dev mentionnant un document → ne doit PAS être bloquée par
        # le garde-fou (elle peut échouer plus loin faute de contexte, mais
        # surtout pas avec le message de redirection document).
        ctx = HandlerContext.for_testing(
            lumena_root=tmp_path,
            runtime_root=tmp_path / "workspace",
        )
        ctx.runtime_root.mkdir(exist_ok=True)
        ctx.original_user_query = ""
        delegated = AgentResult(
            task_id="dev-task",
            success=True,
            output="Mutation du module export terminee",
            status_code=StatusCode.SUCCESS,
            duration_ms=1200,
            meta={"iterations": 2},
        )
        with patch(
            "src.agents.sub_agent.delegate_to_agent_full",
            new=AsyncMock(return_value=delegated),
        ):
            result = await delegate_task_handler(
                ctx,
                description="corrige le bug dans la fonction generate_pdf du module export",
                agent_type="code",
                project_path=str(ctx.runtime_root),
            )
        assert result.success is True
        if result.success is False:
            assert "réservé au développement" not in (result.error or "")


class TestDelegateTaskMCPGuard:
    @pytest.mark.asyncio
    async def test_blocks_mcp_resume_from_codeagent(self, tmp_path):
        ctx = HandlerContext.for_testing(
            lumena_root=tmp_path,
            runtime_root=tmp_path / "workspace",
        )
        ctx.original_user_query = "c'est bon, reprends"

        result = await delegate_task_handler(
            ctx,
            description="Crée un serveur MCP local dédié à la surveillance des prix e-commerce",
            agent_type="code",
            context={
                "ticket": "Ticket MCP #abc approuvé",
                "next": "Utilise resume_mcp_task pour reprendre",
            },
        )

        assert result.success is False
        text = result.error or result.output
        assert "CodeAgent" in text
        assert "resume_mcp_task" in text

    @pytest.mark.asyncio
    async def test_blocks_llm_invented_external_workspace_path(self, tmp_path):
        runtime_root = tmp_path / "workspace"
        runtime_root.mkdir()
        outside_parent = tmp_path / "outside"
        outside_parent.mkdir()
        outside_project = outside_parent / "mcp-price-tracker"

        ctx = HandlerContext.for_testing(lumena_root=tmp_path, runtime_root=runtime_root)
        ctx.original_user_query = "reprends la tâche de code"

        mock_mod = MagicMock()
        mock_mod.delegate_to_agent_full = AsyncMock(
            return_value=AgentResult(
                task_id="t1",
                success=True,
                output="should not run",
                status_code=StatusCode.SUCCESS,
            )
        )

        with patch.dict(sys.modules, {"src.agents.sub_agent": mock_mod}):
            result = await delegate_task_handler(
                ctx,
                description=f'Crée le projet dans "{outside_project}"',
                agent_type="code",
            )

        assert result.success is False
        text = result.error or result.output
        assert "hors scope" in text
        assert str(outside_project) in text
        mock_mod.delegate_to_agent_full.assert_not_called()

    @pytest.mark.asyncio
    async def test_allows_external_workspace_path_when_user_mentioned_it(self, tmp_path):
        runtime_root = tmp_path / "workspace"
        runtime_root.mkdir()
        outside_parent = tmp_path / "outside"
        outside_parent.mkdir()
        outside_project = outside_parent / "explicit-project"

        ctx = HandlerContext.for_testing(lumena_root=tmp_path, runtime_root=runtime_root)
        ctx.original_user_query = f'Travaille dans "{outside_project}"'

        mock_mod = MagicMock()
        mock_mod.delegate_to_agent_full = AsyncMock(
            return_value=AgentResult(
                task_id="t1",
                success=True,
                output="Done: created",
                status_code=StatusCode.SUCCESS,
                duration_ms=1200,
                meta={"iterations": 2},
            )
        )

        with patch.dict(sys.modules, {"src.agents.sub_agent": mock_mod}):
            result = await delegate_task_handler(
                ctx,
                description=f'Crée le projet dans "{outside_project}"',
                agent_type="code",
            )

        assert result.success is True
        mock_mod.delegate_to_agent_full.assert_called_once()
