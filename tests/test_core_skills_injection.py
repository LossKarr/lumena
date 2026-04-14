from pathlib import Path
import sys
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).parent.parent))

import src.core as core_module
import src.skills as skills_module
from src.core_services.context_service import ContextService
from src.core_services.contracts import ServiceContext


def _build_core_stub():
    core = core_module.LumenaCore.__new__(core_module.LumenaCore)
    core.skills_auto_activation = True
    core._last_active_skills = []
    ctx = ServiceContext.__new__(ServiceContext)
    core._context_svc = ContextService(ctx)
    return core


def test_build_active_skills_context_updates_last_active(monkeypatch):
    core = _build_core_stub()

    monkeypatch.setattr(
        skills_module,
        "match_skills",
        lambda query, max_results=3: [SimpleNamespace(name="pdf"), SimpleNamespace(name="docx")],
    )
    monkeypatch.setattr(
        skills_module,
        "build_active_skills_context",
        lambda query, max_results=3, max_chars=5000: "## Skills actifs\n- pdf\n- docx",
    )

    context = core_module.LumenaCore._build_active_skills_context_for_query(core, "fais un pdf")
    assert "Skills actifs" in context
    assert core_module.LumenaCore.get_last_active_skills(core) == ["pdf", "docx"]


def test_build_active_skills_context_disabled_clears_last_active(monkeypatch):
    core = _build_core_stub()
    core.skills_auto_activation = False
    core._last_active_skills = ["legacy"]

    context = core_module.LumenaCore._build_active_skills_context_for_query(core, "fais un pdf")
    assert context == ""
    assert core_module.LumenaCore.get_last_active_skills(core) == []
