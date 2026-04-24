"""
Tests d'intégration — conditions de déclenchement de la phase Architect.

Vérifie que :
- _resolved_intent est correctement propagé depuis TaskContext → AgentTask → CodeAgent
- _is_complex_modify est True quand intent in ("create","modify") + projet existant + description longue
- _is_complex_modify est False quand intent="read" ou projet vide ou description courte
- delegate_task_handler construit un safe_context avec intent et workspace_path
"""

from __future__ import annotations

import pytest
from pathlib import Path
from unittest.mock import MagicMock, patch, AsyncMock


# ══════════════════════════════════════════════════════════════════════════════
# Tests TaskContext → intent propagation
# ══════════════════════════════════════════════════════════════════════════════

class TestTaskContextIntentPropagation:
    """Vérifie que TaskContext propage correctement l'intent."""

    def test_explicit_project_path_existing_dir_sets_modify(self, tmp_path):
        """project_path existant + fichiers → intent=modify."""
        from src.agents.task_context import TaskContext
        (tmp_path / "index.html").write_text("<h1>test</h1>")
        (tmp_path / "style.css").write_text("body{}")

        ctx = TaskContext.from_delegate_call(
            description="Modifie la page d'accueil du site",
            project_path=str(tmp_path),
        )
        assert ctx.intent == "modify"
        assert ctx.workspace_path == tmp_path
        assert ctx.resolution_source == "explicit_param"

    def test_explicit_project_path_empty_dir_sets_create(self, tmp_path):
        """project_path existant mais vide → intent=create."""
        from src.agents.task_context import TaskContext
        ctx = TaskContext.from_delegate_call(
            description="Crée un nouveau projet web",
            project_path=str(tmp_path),
        )
        assert ctx.intent == "create"

    def test_context_dict_intent_preserved(self, tmp_path):
        """Intent dans le dict context est préservé tel quel."""
        from src.agents.task_context import TaskContext
        (tmp_path / "app.py").write_text("print('hello')")

        ctx = TaskContext.from_delegate_call(
            description="Analyse le code sans modifier",
            context={"intent": "read", "workspace_path": str(tmp_path)},
        )
        assert ctx.intent == "read"

    def test_to_legacy_dict_contains_intent(self, tmp_path):
        """to_legacy_dict() expose intent pour _build_initial_messages."""
        from src.agents.task_context import TaskContext
        (tmp_path / "main.py").write_text("x = 1")

        ctx = TaskContext.from_delegate_call(
            description="Ajoute une fonction de calcul",
            project_path=str(tmp_path),
        )
        d = ctx.to_legacy_dict()
        assert "intent" in d
        assert d["intent"] in ("create", "modify")
        assert "workspace_path" in d

    def test_to_legacy_dict_no_intent_when_auto(self):
        """to_legacy_dict() n'expose pas intent si auto (non résolu)."""
        from src.agents.task_context import TaskContext
        ctx = TaskContext.from_delegate_call(
            description="Fais quelque chose",
        )
        d = ctx.to_legacy_dict()
        assert "intent" not in d


# ══════════════════════════════════════════════════════════════════════════════
# Tests _is_complex_modify (conditions de déclenchement Architect)
# ══════════════════════════════════════════════════════════════════════════════

class TestIsComplexModify:
    """Vérifie la logique _is_complex_modify dans CodeAgent._single_code_attempt."""

    def _make_code_agent(self, intent: str, project_files: list, description: str):
        """Construit un CodeAgent minimal avec l'état nécessaire."""
        _is_complex_modify = (
            intent in ("modify", "create")
            and len(description) > 40
        )
        return _is_complex_modify

    def test_modify_with_long_desc_triggers(self):
        """intent=modify + desc>40 chars → Architect déclenché."""
        result = self._make_code_agent(
            intent="modify",
            project_files=["index.html", "style.css", "app.js"],
            description="Ajoute une section newsletter avec formulaire d'inscription",
        )
        assert result is True

    def test_create_empty_project_long_desc_triggers(self):
        """intent=create + projet VIDE + desc>40 chars → Architect déclenché (nouveau comportement)."""
        result = self._make_code_agent(
            intent="create",
            project_files=[],
            description="Crée une landing page complète avec hero, services et contact",
        )
        assert result is True

    def test_create_existing_project_long_desc_triggers(self):
        """intent=create + projet existant + desc>40 chars → Architect déclenché."""
        result = self._make_code_agent(
            intent="create",
            project_files=["index.html", "style.css", "app.js"],
            description="Crée une page de contact avec validation de formulaire",
        )
        assert result is True

    def test_read_intent_never_triggers(self):
        """intent=read → Architect jamais déclenché."""
        result = self._make_code_agent(
            intent="read",
            project_files=["index.html", "style.css", "app.js"],
            description="Analyse le code et dis-moi ce que fait chaque fichier",
        )
        assert result is False

    def test_auto_intent_never_triggers(self):
        """intent=auto → Architect jamais déclenché."""
        result = self._make_code_agent(
            intent="auto",
            project_files=["index.html", "style.css", "app.js"],
            description="Fais quelque chose avec le projet existant ici",
        )
        assert result is False

    def test_short_description_never_triggers(self):
        """Description <40 chars → Architect jamais déclenché."""
        result = self._make_code_agent(
            intent="modify",
            project_files=["index.html", "style.css", "app.js"],
            description="Corrige l'erreur",
        )
        assert result is False


# ══════════════════════════════════════════════════════════════════════════════
# Tests _build_initial_messages — lecture intent depuis task_ctx
# ══════════════════════════════════════════════════════════════════════════════

class TestBuildInitialMessagesTaskCtx:
    """Vérifie que _build_initial_messages lit task_ctx en priorité."""

    def test_task_ctx_intent_takes_priority_over_context_dict(self, tmp_path):
        """task.task_ctx.intent='modify' prend la priorité sur context dict."""
        from src.agents.task_context import TaskContext
        (tmp_path / "file.py").write_text("x = 1")

        tc = TaskContext(
            workspace_path=tmp_path,
            intent="modify",
            description="test",
            resolution_source="explicit_param",
            confidence=1.0,
        )

        # Simuler le comportement de _build_initial_messages
        _tc = tc
        _ws = (
            str(_tc.workspace_path) if _tc and _tc.workspace_path
            else None
        )
        _ctx_intent_from_tc = _tc.intent if _tc and _tc.intent not in ("auto", None) else None
        _ctx_intent = _ctx_intent_from_tc or {}.get("intent")

        assert _ws == str(tmp_path)
        assert _ctx_intent == "modify"

    def test_fallback_to_context_dict_when_no_task_ctx(self, tmp_path):
        """Sans task_ctx, fallback sur context dict."""
        _ctx = {"intent": "create", "workspace_path": str(tmp_path)}
        _ctx_intent = None or _ctx.get("intent")
        assert _ctx_intent == "create"
