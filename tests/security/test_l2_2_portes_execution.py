"""Lot L2-2 - les autres portes d'execution sont jugees comme `run_command`.

Faits mesures le 16 septembre 2026 (lecture seule, avant toute ligne de code) :

- `bg_start` ne passe PAS par `ProcessManager` (mon inventaire le disait a tort) : il
  appelle `background/manager.py::start_command` -> `create_subprocess_shell(command)`
  **sans `cwd`** et **sans juge de commandes**. C'est la porte la plus ouverte du depot.
- `process_run` utilise `ProcessManager.work_dir = work_dir or Path.cwd()`, et les 3
  appels a `get_process_manager()` sont sans argument. Mesure : `START.bat` fait
  `cd /d "%~dp0"` et `Path.cwd()` rend la RACINE DU DEPOT.
- `run_tests` (`cwd=ctx.lumena_root`) et `execute_skill` (`cwd=skill.path`, donc
  `skills/`) travaillent LEGITIMEMENT dans le depot -> exemptions NOMMEES, pas un trou.
- `execute_multilang` s'execute dans un dossier temporaire jetable : la garde de dossier
  **ne mord pas** sur lui. On l'ecrit noir sur blanc plutot que de pretendre l'avoir
  ferme.

Aucun test ne lance de vrai processus : les lanceurs sont remplaces par des doublures
qui echouent si elles sont appelees apres un refus.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from src.reasoning.handlers.context import HandlerContext
from src.reasoning.handlers.files import (
    assert_execution_cwd_allowed,
    execution_work_dir,
)
from src.tools.file_guardrails import (
    OutsideAccessGrant,
    PathSecurityError,
    WorkspaceFileGuardrails,
)


@pytest.fixture
def monde(tmp_path, monkeypatch):
    root = tmp_path / "lumena"
    ws = root / "workspace"
    (ws / "projet").mkdir(parents=True)
    (root / "src").mkdir()
    (root / "skills" / "mon-skill").mkdir(parents=True)
    mission = ws / "missions" / "m1"
    mission.mkdir(parents=True)
    monkeypatch.setattr(WorkspaceFileGuardrails, "_workspace_root", lambda self: ws)
    return {"root": root, "ws": ws, "mission": mission, "tmp": tmp_path}


def _chat(m, runtime=None) -> HandlerContext:
    ctx = HandlerContext.for_testing(lumena_root=m["root"], runtime_root=runtime or m["ws"])
    ctx.outside_access_grant = OutsideAccessGrant.none()
    return ctx


def _mission(m) -> HandlerContext:
    ctx = _chat(m)
    ctx.is_mission_run = True
    ctx.runtime_task_id = "task-l2-2"
    ctx.mission_workspace = "missions/m1"
    return ctx


# ── 1. Le dossier du tour ───────────────────────────────────────────────────

def test_dossier_du_tour_en_mission_est_le_dossier_de_mission(monde):
    m = monde
    assert execution_work_dir(_mission(m)) == m["mission"]


def test_dossier_du_tour_en_chat_est_la_racine_d_execution(monde):
    m = monde
    assert execution_work_dir(_chat(m, m["ws"] / "projet")) == m["ws"] / "projet"


def test_dossier_du_tour_contexte_leger_ne_plante_pas(monde):
    from types import SimpleNamespace
    assert execution_work_dir(SimpleNamespace()) is None


# ── 2. Exemptions nommees (outils legitimement dans le depot) ───────────────

def test_run_tests_exempte_peut_travailler_a_la_racine(monde):
    """`run_tests` lance pytest SUR Lumena : son dossier est la racine, par conception."""
    m = monde
    assert_execution_cwd_allowed(m["root"], _chat(m), outil="run_tests")


def test_execute_skill_exempte_peut_travailler_dans_skills(monde):
    m = monde
    assert_execution_cwd_allowed(m["root"] / "skills" / "mon-skill", _chat(m),
                                 outil="execute_skill")


def test_sans_exemption_la_racine_reste_refusee(monde):
    """L'exemption est NOMMEE : elle ne s'etend a aucun autre outil."""
    m = monde
    with pytest.raises(PathSecurityError):
        assert_execution_cwd_allowed(m["root"], _chat(m))
    with pytest.raises(PathSecurityError):
        assert_execution_cwd_allowed(m["root"], _chat(m), outil="bg_start")


def test_une_exemption_ne_couvre_pas_une_autre_zone(monde):
    """`run_tests` est exempte pour la racine, pas pour ecrire n'importe ou hors depot."""
    m = monde
    with pytest.raises(PathSecurityError):
        assert_execution_cwd_allowed(m["tmp"] / "ailleurs", _chat(m), outil="run_tests")


# ── 3. process_run : dossier du tour, plus jamais `Path.cwd()` ──────────────

@pytest.mark.asyncio
async def test_process_run_refuse_quand_le_dossier_du_tour_est_le_depot(monde, monkeypatch):
    from src.reasoning.handlers.agents import process_run_handler
    import src.tools.process_manager as pm

    m = monde
    monkeypatch.setattr(pm, "get_process_manager",
                        lambda *a, **k: pytest.fail("processus lance malgre le refus"))
    r = await process_run_handler(_chat(m, m["root"]), command="echo test")
    assert "refus" in str(r.output).lower()


@pytest.mark.asyncio
async def test_process_run_donne_le_dossier_du_tour_au_gestionnaire(monde, monkeypatch):
    from src.reasoning.handlers.agents import process_run_handler
    import src.tools.process_manager as pm

    m = monde
    recu = {}

    class _FauxGestionnaire:
        async def run_background(self, command, wait_ms_before_async=5000, timeout_s=60):
            return ("sortie", "proc-1")

    def _faux_get(work_dir=None):
        recu["work_dir"] = work_dir
        return _FauxGestionnaire()

    monkeypatch.setattr(pm, "get_process_manager", _faux_get)
    cible = m["ws"] / "projet"
    r = await process_run_handler(_chat(m, cible), command="echo test")
    assert r.success, r.output
    assert recu.get("work_dir") is not None
    assert Path(recu["work_dir"]) == cible


# ── 4. bg_start : la porte la plus ouverte ──────────────────────────────────

@pytest.mark.asyncio
async def test_bg_start_refuse_quand_le_dossier_du_tour_est_le_depot(monde, monkeypatch):
    from src.reasoning.handlers.agents import bg_start_handler
    import src.background.manager as bm

    m = monde
    monkeypatch.setattr(bm, "get_task_manager",
                        lambda *a, **k: pytest.fail("tache lancee malgre le refus"))
    r = await bg_start_handler(_chat(m, m["root"]), name="tache", command="echo test")
    assert "refus" in str(r.output).lower()


@pytest.mark.asyncio
async def test_bg_start_applique_le_juge_de_commandes(monde, monkeypatch):
    """Avant L2-2 : aucune liste blanche sur cette porte."""
    from src.reasoning.handlers.agents import bg_start_handler
    import src.background.manager as bm

    m = monde
    monkeypatch.setattr(bm, "get_task_manager",
                        lambda *a, **k: pytest.fail("commande dangereuse lancee"))
    r = await bg_start_handler(_chat(m, m["ws"] / "projet"), name="danger",
                               command="rm -rf /")
    assert not r.success or "⛔" in str(r.output)


# ── 5. dev_run_fix ──────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_dev_run_fix_refuse_le_depot_sans_lancer(monde, monkeypatch):
    from src.reasoning.handlers import project as project_mod

    m = monde

    async def _interdit(*a, **k):
        pytest.fail("commande lancee malgre le refus")

    monkeypatch.setattr(project_mod, "_run_project_cmd", _interdit)
    # Le handler sort sur « LLM non disponible » AVANT la garde si le contexte n'en a
    # pas : mon premier test passait donc a cote. On fournit un LLM factice.
    from types import SimpleNamespace
    ctx = _chat(m)
    ctx.lumena = SimpleNamespace(llm=SimpleNamespace())
    r = await project_mod.dev_run_fix_handler(ctx, command="pytest",
                                              project_dir=str(m["root"] / "src"))
    assert "refus" in str(r.output).lower()


# ── 6. Limite assumee : execute_multilang ───────────────────────────────────

def test_execute_multilang_est_une_exemption_assumee(monde, tmp_path):
    """Exemption NOMMEE, justifiee : son dossier est un temporaire jetable, donc la
    garde de dossier n'a rien a y juger. **Limite assumee** : le CODE execute peut
    viser des chemins absolus ; cette porte n'est donc PAS fermee par L2-2, et on
    l'ecrit au lieu de laisser croire le contraire."""
    jetable = tmp_path / "tmp_exec"
    jetable.mkdir()
    assert_execution_cwd_allowed(jetable, _chat(monde), outil="execute_multilang")
