"""Lot 1.1 — `think_and_act_silent` : params optionnels (additifs).

Contrat : sans les nouveaux params → comportement INCHANGÉ (registre partagé du
cœur, orchestrateur None). Avec `tool_registry` → registre isolé utilisé, et le
registre du cœur **non muté**. `task_orchestrator`/`task_id` transmis à la boucle.
"""
from __future__ import annotations

import types

import pytest

from src.core_services import agent_service as svc_mod


class _FakeLLM:
    def __init__(self):
        self.chat_calls = 0

    async def chat(self, messages, stop=None):
        self.chat_calls += 1
        return "ok"

    def get_last_response_meta(self):
        return {}


def _fake_registry(tag):
    # Imite les attributs mutés par think_and_act_silent.
    return types.SimpleNamespace(
        tag=tag,
        _allowed_tools=None,
        _tools_desc_cache=None,
        _observation_cache={},
        _caller_set_allowed=False,
        _allowed_tools_hard=False,
        _outside_access_grant=None,
        _v2_context=None,
    )


def _make_service():
    core_reg = _fake_registry("core")
    core = types.SimpleNamespace(
        llm=_FakeLLM(),
        _tool_registry=core_reg,
        mcp_react_integration=None,
    )
    return svc_mod.AgentService(core), core, core_reg


class _TaskStore:
    def __init__(self, metadata, preview=""):
        self._record = {"metadata": metadata, "message_preview": preview}

    def get_task(self, _task_id):
        return self._record


@pytest.fixture
def _record_react(monkeypatch):
    """Capture les arguments passés à ReActLoop, sans exécuter la vraie boucle."""
    rec = {}

    class _FakeReact:
        def __init__(self, llm_chat, tools, **kwargs):
            rec["tools"] = tools
            rec["kwargs"] = kwargs

        async def run(self, task):
            return "done"

    monkeypatch.setattr(svc_mod, "ReActLoop", _FakeReact)
    monkeypatch.setattr(svc_mod, "REASONING_AVAILABLE", True, raising=False)
    return rec


@pytest.mark.asyncio
async def test_default_uses_core_registry_and_no_orchestrator(_record_react):
    svc, core, core_reg = _make_service()
    await svc.think_and_act_silent("tâche", allow_when_busy=True)
    # registre = celui du cœur (comportement actuel)
    assert _record_react["tools"] is core_reg
    # orchestrateur/ task_id non fournis → None
    assert _record_react["kwargs"].get("task_orchestrator") is None
    assert _record_react["kwargs"].get("task_id") is None
    # le registre du cœur reste celui d'origine
    assert core._tool_registry is core_reg


@pytest.mark.asyncio
async def test_isolated_registry_is_used_and_core_not_mutated(_record_react):
    svc, core, core_reg = _make_service()
    iso = _fake_registry("isolated")
    await svc.think_and_act_silent("tâche", allow_when_busy=True, tool_registry=iso)
    # la boucle reçoit le registre ISOLÉ
    assert _record_react["tools"] is iso
    # le registre du cœur n'est PAS remplacé (chat intact)
    assert core._tool_registry is core_reg


@pytest.mark.asyncio
async def test_orchestrator_and_task_id_forwarded(_record_react):
    svc, core, core_reg = _make_service()
    orch = _TaskStore({"objective": "mission de test"})
    core._build_active_skills_context_for_query = lambda *args, **kwargs: ""
    await svc.think_and_act_silent(
        "tâche", allow_when_busy=True, task_orchestrator=orch, task_id="mission-1",
    )
    assert _record_react["kwargs"].get("task_orchestrator") is orch
    assert _record_react["kwargs"].get("task_id") == "mission-1"


@pytest.mark.asyncio
async def test_mission_injects_skills_from_worker_routing_objective(
    _record_react, monkeypatch,
):
    svc, core, _ = _make_service()
    calls = {}
    route = object()
    orch = _TaskStore({
        "objective": "CONTRAT DE MISSION : protocole technique",
        "routing_objective": "Construire un dashboard React avec tests Playwright",
    })

    def _resolve(query, *, mission_run=False):
        calls["route"] = (query, mission_run)
        return route

    def _skills(query, **kwargs):
        calls["skills"] = (query, kwargs)
        return "## Skills actifs\n- webapp-testing"

    monkeypatch.setattr(svc_mod, "_resolve_agent_document_route", _resolve)
    core._build_active_skills_context_for_query = _skills

    await svc.think_and_act_silent(
        "[protocole injecte] fais le travail",
        allow_when_busy=True,
        task_orchestrator=orch,
        task_id="worker-1",
    )

    semantic = "Construire un dashboard React avec tests Playwright"
    assert calls["route"] == (semantic, True)
    assert calls["skills"] == (semantic, {"document_route": route})
    assert _record_react["kwargs"]["active_skills_context"].startswith("## Skills")
    assert _record_react["kwargs"]["document_route"] is route


@pytest.mark.asyncio
async def test_mission_lead_uses_stored_objective_for_skills(
    _record_react, monkeypatch,
):
    svc, core, _ = _make_service()
    seen = []
    orch = _TaskStore({"objective": "Creer une presentation PowerPoint commerciale"})
    monkeypatch.setattr(
        svc_mod,
        "_resolve_agent_document_route",
        lambda query, *, mission_run=False: types.SimpleNamespace(query=query),
    )
    core._build_active_skills_context_for_query = (
        lambda query, **kwargs: seen.append(query) or "presentation-skill"
    )

    await svc.think_and_act_silent(
        "[Mode mission] long preambule technique",
        allow_when_busy=True,
        task_orchestrator=orch,
        task_id="lead-1",
    )

    assert seen == ["Creer une presentation PowerPoint commerciale"]
    assert _record_react["kwargs"]["active_skills_context"] == "presentation-skill"


@pytest.mark.asyncio
async def test_non_mission_silent_keeps_skills_and_route_empty(_record_react):
    svc, core, _ = _make_service()

    def _unexpected(*args, **kwargs):
        raise AssertionError("non-mission silent jobs must not activate skills")

    core._build_active_skills_context_for_query = _unexpected
    await svc.think_and_act_silent("background task", allow_when_busy=True)

    assert _record_react["kwargs"]["active_skills_context"] == ""
    assert _record_react["kwargs"]["document_route"] is None


@pytest.mark.asyncio
async def test_mission_react_error_never_falls_back_to_raw_llm(monkeypatch):
    svc, core, _ = _make_service()

    class _FailingReact:
        def __init__(self, *args, **kwargs):
            pass

        async def run(self, task):
            raise ValueError("react broke")

    monkeypatch.setattr(svc_mod, "ReActLoop", _FailingReact)
    monkeypatch.setattr(svc_mod, "REASONING_AVAILABLE", True, raising=False)

    with pytest.raises(RuntimeError, match="mission_react_error:react broke"):
        await svc.think_and_act_silent("mission", task_id="mission-1")
    assert core.llm.chat_calls == 0


@pytest.mark.asyncio
async def test_non_mission_react_error_keeps_historical_raw_fallback(monkeypatch):
    svc, core, _ = _make_service()

    class _FailingReact:
        def __init__(self, *args, **kwargs):
            pass

        async def run(self, task):
            raise ValueError("react broke")

    monkeypatch.setattr(svc_mod, "ReActLoop", _FailingReact)
    monkeypatch.setattr(svc_mod, "REASONING_AVAILABLE", True, raising=False)

    assert await svc.think_and_act_silent("background task") == "ok"
    assert core.llm.chat_calls == 1


@pytest.mark.asyncio
async def test_mission_without_react_fails_closed(monkeypatch):
    svc, core, _ = _make_service()
    monkeypatch.setattr(svc_mod, "REASONING_AVAILABLE", False, raising=False)

    with pytest.raises(RuntimeError, match="mission_react_unavailable"):
        await svc.think_and_act_silent("mission", task_id="mission-1")
    assert core.llm.chat_calls == 0
