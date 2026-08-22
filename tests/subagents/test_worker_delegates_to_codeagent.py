"""LOT I MVP (run PostuloTrack 2026-07-05) — le worker de mission délègue le CODE au CodeAgent.

Constat : le worker de mission EST une Lumena complète (registre 578 handlers, delegate_task
présent) mais on l'empêchait de faire ce que la vraie Lumena fait — déléguer le code au
CodeAgent (harnais prouvé 62/62). Triple mur : (1) la porte `category:agents` le refuse
(caller=react sans workspace), (2) le steering LOT G dit « code à la main », (3) pare-feu.

I.1 : le registre de mission est tagué avec son dossier → la catégorie 'agents'
      (delegate_task→CodeAgent, process_status) passe. Le registre du chat n'a pas ce tag.
I.2 : le CodeAgent accepte un périmètre `allowed_files` OPTIONNEL → n'écrit QUE les fichiers
      du worker. Absent (chat / vraie Lumena) → comportement identique (NON-RÉGRESSION).
I.4-min : pour un worker code, l'objectif dit « délègue le code au CodeAgent ».
"""
from __future__ import annotations

from pathlib import Path

from src.agents.sub_agent import _write_within_perimeter, _CODEAGENT_WRITE_ACTIONS


def _mission_handler_context(tmp_path, *, allowed_files=None):
    from src.reasoning.handlers.context import HandlerContext
    from src.tools.file_guardrails import WorkspaceFileGuardrails

    mission = tmp_path / "missions" / "task_scope"
    mission.mkdir(parents=True)
    ctx = HandlerContext(
        lumena_root=tmp_path,
        runtime_root=tmp_path,
        file_guardrails=WorkspaceFileGuardrails(tmp_path),
        runtime_task_id="task_scope",
        is_mission_run=True,
        mission_workspace="missions/task_scope",
        mission_allowed_files=list(allowed_files or []),
    )
    return ctx, mission


def test_top_lead_codeagent_scope_is_mission_root_without_file_perimeter(tmp_path):
    from src.reasoning.handlers.agents import _mission_codeagent_scope

    ctx, mission = _mission_handler_context(tmp_path)
    project_path, allowed = _mission_codeagent_scope(ctx)

    assert Path(project_path) == mission.resolve()
    assert allowed is None


def test_worker_codeagent_scope_keeps_mission_root_and_owned_files(tmp_path):
    from src.reasoning.handlers.agents import _mission_codeagent_scope

    ctx, mission = _mission_handler_context(
        tmp_path, allowed_files=["tests/test_app.py", "app.py"]
    )
    project_path, allowed = _mission_codeagent_scope(ctx)

    assert Path(project_path) == mission.resolve()
    assert allowed == ["app.py", "tests/test_app.py"]


def test_non_mission_codeagent_scope_is_unchanged(tmp_path):
    from src.reasoning.handlers.agents import _mission_codeagent_scope
    from src.reasoning.handlers.context import HandlerContext
    from src.tools.file_guardrails import WorkspaceFileGuardrails

    ctx = HandlerContext(
        lumena_root=tmp_path,
        runtime_root=tmp_path,
        file_guardrails=WorkspaceFileGuardrails(tmp_path),
    )

    assert _mission_codeagent_scope(ctx) == (None, None)


# ── I.1 : la porte category:agents passe pour un registre de mission tagué ─────────

def test_gate_agents_passes_when_mission_registry_tagged(tmp_path):
    from src.reasoning.tool_registry import ToolRegistry
    from src.reasoning.caller_context import REACT

    reg = ToolRegistry()
    # sans tag → la catégorie 'agents' refuse (caller=react, pas de workspace)
    refus = reg._category_contract_check("delegate_task", {"description": "remplis tests/test_app.py selon CONTRAT.md"}, REACT)
    assert refus is not None and refus.success is False
    assert "workspace" in (refus.content or "").lower()

    # avec le tag mission (posé par runner.run_mission) → la porte passe
    reg._mission_workspace_abs = str(tmp_path)
    ok = reg._category_contract_check("delegate_task", {"description": "remplis tests/test_app.py selon CONTRAT.md"}, REACT)
    assert ok is None


def test_gate_untagged_chat_registry_unchanged(tmp_path):
    """NON-RÉGRESSION : un registre SANS tag (chat) garde son comportement (refus react)."""
    from src.reasoning.tool_registry import ToolRegistry
    from src.reasoning.caller_context import REACT

    reg = ToolRegistry()
    assert not hasattr(reg, "_mission_workspace_abs") or reg._mission_workspace_abs is None
    refus = reg._category_contract_check("delegate_task", {"description": "remplis tests/test_app.py selon CONTRAT.md"}, REACT)
    assert refus is not None and refus.success is False


# ── I.2 : périmètre d'écriture du CodeAgent (helper pur) ───────────────────────────

def test_perimeter_allows_owned_file():
    allowed = ["tests/test_app.py"]
    assert _write_within_perimeter("tests/test_app.py", allowed)
    assert _write_within_perimeter("./tests/test_app.py", allowed)
    assert _write_within_perimeter("test_app.py", allowed)  # match par basename


def test_perimeter_refuses_foreign_file():
    allowed = ["tests/test_app.py"]
    assert not _write_within_perimeter("backend/app.py", allowed)
    assert not _write_within_perimeter("frontend/index.html", allowed)


def test_perimeter_absolute_path_normalized(tmp_path):
    (tmp_path / "tests").mkdir()
    allowed = ["tests/test_app.py"]
    abs_owned = str(tmp_path / "tests" / "test_app.py")
    abs_foreign = str(tmp_path / "backend" / "app.py")
    assert _write_within_perimeter(abs_owned, allowed, workspace_root=tmp_path)
    assert not _write_within_perimeter(abs_foreign, allowed, workspace_root=tmp_path)


def test_perimeter_none_means_no_effect():
    """NON-RÉGRESSION cœur : hors mission (allowed None/[]), TOUT passe → CodeAgent inchangé."""
    assert _write_within_perimeter("anything/at/all.py", None)
    assert _write_within_perimeter("backend/app.py", [])
    assert _write_within_perimeter("/abs/whatever.py", None)


def test_write_actions_set_is_write_only():
    # garde-fou : les lectures ne sont pas dans l'ensemble filtré
    assert "write_file" in _CODEAGENT_WRITE_ACTIONS
    assert "apply_patch" in _CODEAGENT_WRITE_ACTIONS
    assert "read_file" not in _CODEAGENT_WRITE_ACTIONS
    assert "run_command" not in _CODEAGENT_WRITE_ACTIONS
    assert "done" not in _CODEAGENT_WRITE_ACTIONS


# ── I.2 : le SubAgent lit bien le périmètre depuis le context ──────────────────────

def test_subagent_reads_allowed_files_from_context():
    from src.agents.sub_agent import SubAgent
    s = SubAgent.__new__(SubAgent)
    s._allowed_files = None
    # simulate ce que fait _build_initial_messages
    ctx = {"allowed_files": ["tests/test_app.py", "backend/app.py"]}
    perim = ctx.get("allowed_files")
    s._allowed_files = frozenset(str(p).replace("\\", "/").strip().strip("/") for p in perim)
    assert "tests/test_app.py" in s._allowed_files
    assert not _write_within_perimeter("frontend/x.js", s._allowed_files)
    assert _write_within_perimeter("backend/app.py", s._allowed_files)


# ── I.4-min : le worker code reçoit le steer de délégation ─────────────────────────

def test_code_worker_objective_has_delegate_steer():
    from src.subagents.mission_contract import worker_objectives
    data = {
        "project": "PostuloTrack",
        "files": [
            {"path": "tests/test_app.py", "owner": "w_tests",
             "exports": ["def test_x()"]},
        ],
    }
    objs = worker_objectives(data)
    assert len(objs) == 1
    obj_text = objs[0]["objective"]
    assert "délègue" in obj_text.lower() and "codeagent" in obj_text.lower()
    assert objs[0]["allowed_files"] == ["tests/test_app.py"]


def test_non_code_worker_objective_has_no_delegate_steer():
    """NON-RÉGRESSION : un worker qui n'a QUE des livrables non-code n'a pas le steer."""
    from src.subagents.mission_contract import worker_objectives
    data = {
        "project": "Doc",
        "files": [
            {"path": "README.md", "owner": "w_doc", "desc": "doc"},
        ],
    }
    objs = worker_objectives(data)
    assert objs and "delegate_task" not in objs[0]["objective"]
