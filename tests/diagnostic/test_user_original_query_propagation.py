"""
Phase 0.6 — Tests de propagation de la demande utilisateur originale.

Contexte (DIAGNOSTIC_PROD.md §14 — session 15:55 du 16/05) :
ReAct a reformulé "corriger le site et CASSER à toi de trouver" en
"Corriger les problèmes du site Lumena landing page" avant de déléguer
au CodeAgent → l'intent "casser" était perdu, Architect ne pouvait pas
demander clarification.

Patch minimal Phase 0.6 :
1. HandlerContext.original_user_query (champ ajouté)
2. ReAct set ce champ avant tools.execute (même pattern que runtime_task_id)
3. delegate_task_handler injecte dans context["user_original_request"]
4. CodeAgent lit task.context et l'injecte dans le prompt Architect
5. Règle dans le prompt : la demande originale PRIME sur la reformulation

Ce fichier teste les invariants structurels + 1 test d'intégration light.
"""

from __future__ import annotations

from pathlib import Path

import pytest


# ── Étape 1 : HandlerContext expose le champ ──────────────────────────────────


def test_handler_context_has_original_user_query_field():
    """Phase 0.6 étape 1 — HandlerContext.original_user_query existe avec défaut "" ."""
    from src.reasoning.handlers.context import HandlerContext

    ctx = HandlerContext()
    assert hasattr(ctx, "original_user_query")
    assert ctx.original_user_query == ""


def test_handler_context_original_user_query_settable():
    """Phase 0.6 étape 1 — Le champ est mutable (pour set par ReAct)."""
    from src.reasoning.handlers.context import HandlerContext

    ctx = HandlerContext()
    ctx.original_user_query = "lumena, corrige le site et casser à toi de trouver"
    assert ctx.original_user_query.startswith("lumena, corrige")


# ── Étape 2 : ReAct set le champ — invariant source ──────────────────────────


def test_react_sets_original_user_query_pattern_present():
    """Phase 0.6 étape 2 — le pattern d'écriture existe dans react.py.

    Vérifie que ReAct propage bien `_original_query` vers `tools._v2_context`
    via le même mécanisme que `runtime_task_id`.
    """
    src = Path("src/reasoning/react.py").read_text(encoding="utf-8")
    # Pattern d'assignation (peut varier sur le nom de variable, mais doit être présent)
    assert "_v2_context.original_user_query" in src, (
        "Phase 0.6 : ReAct doit propager _original_query vers HandlerContext"
    )


# ── Étape 3 : delegate_task_handler injecte dans context ─────────────────────


def test_delegate_task_handler_pattern_present():
    """Phase 0.6 étape 3 — le handler doit lire ctx.original_user_query
    et l'injecter dans le context dict passé au sub_agent."""
    src = Path("src/reasoning/handlers/agents.py").read_text(encoding="utf-8")
    assert "original_user_query" in src, (
        "Phase 0.6 : delegate_task_handler doit lire ctx.original_user_query"
    )
    assert "user_original_request" in src, (
        "Phase 0.6 : delegate_task_handler doit injecter user_original_request "
        "dans le context dict"
    )


# ── Étape 4 : CodeAgent lit dans le prompt Architect ─────────────────────────


def test_codeagent_reads_user_original_request_in_architect_prompt():
    """Phase 0.6 étape 4 — Le prompt Architect contient la lecture
    de task.context['user_original_request']."""
    src = Path("src/agents/sub_agent.py").read_text(encoding="utf-8")
    assert "user_original_request" in src, (
        "Phase 0.6 : CodeAgent doit lire user_original_request dans le prompt Architect"
    )


def test_architect_prompt_contains_primacy_rule():
    """Phase 0.6 étape 4 — Le prompt Architect doit contenir la règle
    'demande originale PRIME sur reformulation'."""
    src = Path("src/agents/sub_agent.py").read_text(encoding="utf-8")
    # Cherche le motif clé
    assert "PRIME sur la reformulation" in src or "BASE TON PLAN SUR LA DEMANDE ORIGINALE" in src, (
        "Phase 0.6 : la règle de primauté doit apparaître dans le prompt Architect"
    )


# ── Étape 5 : Test bout-en-bout léger (mock le handler) ──────────────────────


@pytest.mark.asyncio
async def test_delegate_task_injects_user_original_when_set(monkeypatch):
    """Phase 0.6 étape 3+5 — bout-en-bout (mocked) :
    si ctx.original_user_query est set, le sub_agent reçoit context['user_original_request']."""
    from src.reasoning.handlers.context import HandlerContext
    from src.reasoning.handlers import agents as agents_mod
    from unittest.mock import AsyncMock, MagicMock

    # Mock delegate_to_agent_full pour capturer le context reçu
    captured_context = {}

    async def fake_delegate(description, agent_type, context):
        captured_context.update(context or {})
        # Retourne un AgentResult-like minimal
        result = MagicMock()
        result.success = True
        result.output = "fake success"
        result.status_code = "success"
        result.meta = {}
        result.artifacts = []
        result.missing_fields = []
        result.next_action = None
        result.duration_ms = 100
        result.task_id = "test-task"
        return result

    # Patcher delegate_to_agent_full
    import src.agents.sub_agent as sub_agent_mod
    monkeypatch.setattr(sub_agent_mod, "delegate_to_agent_full", fake_delegate)

    # Mock resolve_workspace pour éviter dépendance disque
    def fake_resolve(*args, **kwargs):
        res = MagicMock()
        res.path = Path.cwd()
        res.intent = "modify"
        res.source = "fake"
        res.confidence = 0.95
        return res

    monkeypatch.setattr(
        "src.utils.project_registry.resolve_workspace", fake_resolve
    )

    # Mock TaskContext.from_delegate_call pour éviter setup workspace
    from src.agents.task_context import TaskContext
    real_from_delegate = TaskContext.from_delegate_call

    def fake_from_delegate(*args, **kwargs):
        # On garde le context tel qu'il a été enrichi avant
        tc = MagicMock()
        tc.workspace_path = Path.cwd()
        tc.intent = "modify"
        tc.resolution_source = "fake"
        tc.confidence = 0.95
        tc.summary = lambda: "fake_summary"
        # to_legacy_dict retourne le context dict enrichi
        ctx_dict = kwargs.get("context", {}) or {}
        tc.to_legacy_dict = lambda: ctx_dict
        return tc

    monkeypatch.setattr(TaskContext, "from_delegate_call", staticmethod(fake_from_delegate))

    # HandlerContext avec original_user_query set
    handler_ctx = HandlerContext()
    handler_ctx.original_user_query = "corriger le site et casser à toi de trouver"
    handler_ctx.lumena = None
    handler_ctx.runtime_task_id = None  # pas de cancel coopératif

    # Appel du handler
    await agents_mod.delegate_task_handler(
        ctx=handler_ctx,
        description="Corriger les problèmes du site (reformulation ReAct)",
        agent_type="code",
        context={},
        project_path="",
    )

    # Vérification : le context final passé au sub_agent contient bien la query originale
    assert "user_original_request" in captured_context, (
        f"Phase 0.6 : user_original_request manquant dans le context reçu par sub_agent. "
        f"Context reçu : {list(captured_context.keys())}"
    )
    assert "casser" in captured_context["user_original_request"].lower(), (
        f"Phase 0.6 : la phrase verbatim devrait contenir 'casser'. "
        f"Reçu : {captured_context['user_original_request']!r}"
    )


@pytest.mark.asyncio
async def test_delegate_task_does_not_overwrite_existing_user_original(monkeypatch):
    """Phase 0.6 étape 3 — Si le context déjà passé contient déjà
    user_original_request (cas tests/programmatique), on ne l'écrase pas."""
    from src.reasoning.handlers.context import HandlerContext
    from src.reasoning.handlers import agents as agents_mod
    from unittest.mock import AsyncMock, MagicMock

    captured_context = {}

    async def fake_delegate(description, agent_type, context):
        captured_context.update(context or {})
        result = MagicMock()
        result.success = True
        result.output = "ok"
        result.status_code = "success"
        result.meta = {}
        result.artifacts = []
        result.missing_fields = []
        result.next_action = None
        result.duration_ms = 100
        result.task_id = "test"
        return result

    import src.agents.sub_agent as sub_agent_mod
    monkeypatch.setattr(sub_agent_mod, "delegate_to_agent_full", fake_delegate)

    def fake_resolve(*args, **kwargs):
        res = MagicMock()
        res.path = Path.cwd()
        res.intent = "modify"
        res.source = "fake"
        res.confidence = 0.95
        return res

    monkeypatch.setattr(
        "src.utils.project_registry.resolve_workspace", fake_resolve
    )

    from src.agents.task_context import TaskContext

    def fake_from_delegate(*args, **kwargs):
        tc = MagicMock()
        tc.workspace_path = Path.cwd()
        tc.intent = "modify"
        tc.resolution_source = "fake"
        tc.confidence = 0.95
        tc.summary = lambda: "fake"
        ctx_dict = kwargs.get("context", {}) or {}
        tc.to_legacy_dict = lambda: ctx_dict
        return tc

    monkeypatch.setattr(TaskContext, "from_delegate_call", staticmethod(fake_from_delegate))

    handler_ctx = HandlerContext()
    handler_ctx.original_user_query = "Phrase ReAct courante"
    handler_ctx.lumena = None
    handler_ctx.runtime_task_id = None

    # Context déjà pré-rempli
    pre_filled = {"user_original_request": "Phrase pré-remplie qui doit gagner"}

    await agents_mod.delegate_task_handler(
        ctx=handler_ctx,
        description="reformulation",
        agent_type="code",
        context=pre_filled,
        project_path="",
    )

    assert captured_context.get("user_original_request") == "Phrase pré-remplie qui doit gagner", (
        f"Phase 0.6 : un user_original_request pré-rempli ne doit pas être écrasé. "
        f"Reçu : {captured_context.get('user_original_request')!r}"
    )


# ── Étape 5 : Architect prompt contient bien le verbatim ─────────────────────


def test_architect_prompt_skips_block_when_description_matches():
    """Phase 0.6 — Si la reformulation == originale, le bloc verbatim
    n'est pas injecté (évite duplication inutile)."""
    src = Path("src/agents/sub_agent.py").read_text(encoding="utf-8")
    # Cherche le pattern conditionnel
    assert "_user_orig.lower() != (task.description or \"\").lower().strip()" in src, (
        "Phase 0.6 : skip le bloc si originale == reformulation"
    )
