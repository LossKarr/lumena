"""LOT 2.2 — contrat machine + stubs pour missions multi-workers.

Cause (runs PollApp 2026-07-02) : un contrat en PROSE transmis aux workers ne
contraint rien (`reset_options` fantôme). Ici : contrat machine validé → stubs
réels (signatures figées) → objectifs structurés {objective, allowed_files}
prêts pour delegate_and_wait → périmètre appliqué par le garde 2.3.
Doctrine : module PUR src/subagents/mission_contract.py, ZÉRO import CodeAgent.
"""
from __future__ import annotations

import json
import types

import pytest

import src.subagents.mission_contract as mc
from src.reasoning.handlers import missions as M
from src.runtime.task_orchestrator import TaskOrchestrator


CONTRACT = {
    "project": "pollapp",
    "files": [
        {"path": "app.py", "owner": "backend",
         "api": ["def create_app():", "def reset_options():"],
         "desc": "Backend Flask (votes en mémoire)"},
        {"path": "test_api.py", "owner": "tests",
         "api": ["def test_vote_increments():"],
         "desc": "Tests pytest de l'API"},
        {"path": "static/app.js", "owner": "frontend",
         "api": ["function vote(optionId)"],
         "desc": "Fetch + rendu votes"},
    ],
}


# ── module pur : parse / validate ────────────────────────────────────────────────

def test_parse_contract_dict_and_json():
    d, err = mc.parse_contract(CONTRACT)
    assert not err and d["project"] == "pollapp"
    d2, err2 = mc.parse_contract(json.dumps(CONTRACT))
    assert not err2 and d2 == d
    _, err3 = mc.parse_contract("pas du json {{{")
    assert err3


def test_validate_contract_ok():
    assert mc.validate_contract(CONTRACT) == []


def test_validate_contract_errors():
    assert mc.validate_contract({}) != []                      # files manquant
    errs = mc.validate_contract({"files": [
        {"path": "", "owner": "a"},                            # path vide
        {"path": "../evil.py", "owner": "a"},                  # traversal
        {"path": "x.py", "owner": ""},                         # owner vide
        {"path": "x.py", "owner": "b"},                        # doublon (avec le précédent)
        {"path": "y.py", "owner": "c", "api": "pas une liste"},
    ]})
    joined = " ".join(errs)
    assert "path requis" in joined and "invalide" in joined
    assert "owner requis" in joined and "double" in joined and "liste" in joined


# ── module pur : stubs (signatures EXACTES, jamais paraphrasées) ─────────────────

def test_py_stub_freezes_signatures():
    stub = mc.generate_stub(CONTRACT["files"][0])
    assert "def create_app():" in stub and "def reset_options():" in stub
    assert "NE PAS MODIFIER" in stub
    assert "NotImplementedError" in stub
    compile(stub, "app.py", "exec")   # le stub est du Python VALIDE


def test_py_stub_class_and_no_api():
    stub = mc.generate_stub({"path": "m.py", "owner": "a", "api": ["class Store:"]})
    assert "class Store:" in stub
    compile(stub, "m.py", "exec")
    empty = mc.generate_stub({"path": "n.py", "owner": "a"})
    assert "TODO" in empty
    compile(empty, "n.py", "exec")


def test_js_html_css_stubs():
    js = mc.generate_stub(CONTRACT["files"][2])
    assert "function vote(optionId) {" in js and "TODO" in js
    html = mc.generate_stub({"path": "index.html", "owner": "f",
                             "api": ["liste des options", "bouton Voter"]})
    assert "<!DOCTYPE html>" in html and "ANCRE CONTRAT : liste des options" in html
    css = mc.generate_stub({"path": "styles.css", "owner": "f", "api": ["layout"]})
    assert "SECTION CONTRAT : layout" in css


# ── module pur : rendu + objectifs structurés ────────────────────────────────────

def test_render_contract_md():
    md = mc.render_contract_md(CONTRACT)
    assert "pollapp" in md and "`app.py`" in md and "**backend**" in md
    assert "`def create_app():`" in md


def test_worker_objectives_group_by_owner_with_perimeters():
    objs = mc.worker_objectives(CONTRACT)
    assert len(objs) == 3   # backend / tests / frontend
    by_owner = {o["allowed_files"][0]: o for o in objs}
    backend = by_owner["app.py"]
    assert backend["allowed_files"] == ["app.py"]
    assert "CONTRAT DE MISSION" in backend["objective"]        # préambule contrat
    assert "test_api.py" in backend["objective"]               # cite les fichiers des AUTRES
    assert "NE touche PAS" in backend["objective"]
    # format directement consommable par delegate_and_wait (2.3)
    st = M._normalize_objectives_struct(objs)
    assert st[0]["allowed_files"] == ["app.py"]


# ── LOT « contrat contraignant » : la shape RÉELLE du run BudgetBuddy ────────────
# Le LLM produit spontanément exports/imports/description + shared_api top-level ;
# avant : seuls api/desc étaient lus → CONTRAT.md sans une signature, stubs .py VIDES.

CONTRACT_BB = {
    "project": "budgetbuddy",
    "files": [
        {"path": "storage.py", "owner": "worker_storage",
         "exports": [
             "def add_expense(label: str, amount: float, category: str) -> dict",
             "def get_expenses() -> list[dict]",
             "def get_total_by_category() -> dict[str, float]",
             "def reset_state() -> None"],
         "description": "Module de stockage pur (liste globale _expenses)."},
        {"path": "app.py", "owner": "worker_app",
         "exports": ["def create_app() -> Flask"],
         "imports": ["from storage import add_expense, get_expenses, "
                     "get_total_by_category, reset_state"],
         "description": "Backend Flask ; s'appuie UNIQUEMENT sur storage.py."},
        {"path": "index.html", "owner": "worker_frontend",
         "description": "Formulaire + liste + résumé."},
        {"path": "tests/test_api.py", "owner": "worker_tests",
         "imports": ["from app import create_app"],
         "description": "Tests pytest de l'API."},
    ],
    "shared_api": {"flask_endpoints": {
        "GET /api/expenses": "liste", "POST /api/expenses": "ajout",
        "GET /api/summary": "totaux par catégorie"}},
}


def test_budgetbuddy_shape_is_valid():
    assert mc.validate_contract(CONTRACT_BB) == []


def test_py_stub_uses_exports_alias():
    stub = mc.generate_stub(CONTRACT_BB["files"][0])
    for sig in ("def add_expense(label: str, amount: float, category: str) -> dict:",
                "def get_expenses() -> list[dict]:", "def reset_state() -> None:"):
        assert sig in stub, sig
    assert "NE PAS MODIFIER" in stub
    compile(stub, "storage.py", "exec")


def test_py_stub_hardcodes_contract_imports():
    # Le verrou anti-dérive max : l'import inter-fichiers est DU CODE dans le stub.
    stub = mc.generate_stub(CONTRACT_BB["files"][1])
    assert ("from storage import add_expense, get_expenses, "
            "get_total_by_category, reset_state") in stub
    assert "def create_app() -> Flask:" in stub
    assert "Imports IMPOSÉS" in stub
    compile(stub, "app.py", "exec")


def test_render_md_budgetbuddy_shape():
    md = mc.render_contract_md(CONTRACT_BB)
    assert "def add_expense(label: str, amount: float, category: str) -> dict" in md
    assert "from storage import add_expense" in md
    assert "API partagée" in md and "flask_endpoints" in md
    assert "s'appuie UNIQUEMENT sur storage.py" in md      # description → desc


def test_worker_objectives_carry_api_and_imports():
    objs = mc.worker_objectives(CONTRACT_BB)
    app_obj = next(o for o in objs if o["allowed_files"] == ["app.py"])
    assert "def create_app() -> Flask" in app_obj["objective"]
    assert "from storage import add_expense" in app_obj["objective"]


def test_validation_py_without_signatures_fails_with_explicit_gates():
    # Un .py NON-test sans api/exports = ce qui a vidé les stubs → erreur actionnable.
    errs = mc.validate_contract({"files": [{"path": "core.py", "owner": "a"}]})
    assert errs and "signatures" in errs[0] and "no_public_api" in errs[0]
    # Portes explicites : tests / __init__.py / no_public_api / internal → exemptés
    # de SIGNATURES. (LOT 2.8 : une desc reste requise — le sens — sauf __init__.)
    for entry in (
        {"path": "tests/test_core.py", "owner": "t", "desc": "tests du core"},
        {"path": "pkg/__init__.py", "owner": "a"},
        {"path": "helpers.py", "owner": "a", "no_public_api": True,
         "desc": "helpers internes"},
        {"path": "util.py", "owner": "a", "internal": True, "desc": "utilitaires"},
    ):
        assert mc.validate_contract({"files": [entry]}) == [], entry


# ── handler write_mission_contract ───────────────────────────────────────────────

def _mission_ctx(tmp_path, orch, lead_id):
    from src.reasoning.handlers.context import HandlerContext
    from src.tools.file_guardrails import WorkspaceFileGuardrails
    core = types.SimpleNamespace(task_orchestrator=orch)
    return HandlerContext(lumena=core, lumena_root=tmp_path, runtime_root=tmp_path,
                          file_guardrails=WorkspaceFileGuardrails(tmp_path),
                          runtime_task_id=lead_id, is_mission_run=True)


def _lead(orch, objective="PollApp site de sondage"):
    return orch.start_task(conversation_id="__missions__", channel="mission",
                           message_preview="lead",
                           metadata={"kind": "mission", "depth": 1, "objective": objective})


@pytest.mark.asyncio
async def test_handler_refused_at_chat(tmp_path):
    from src.reasoning.handlers.context import HandlerContext
    orch = TaskOrchestrator(persistence_path=str(tmp_path / "s.json"))
    ctx = HandlerContext(lumena=types.SimpleNamespace(task_orchestrator=orch),
                         lumena_root=tmp_path, runtime_root=tmp_path)
    res = await M.write_mission_contract_handler(ctx, contract=CONTRACT)
    assert not res.success and "DANS une mission" in res.error


@pytest.mark.asyncio
async def test_handler_invalid_contract_clear_errors(tmp_path):
    orch = TaskOrchestrator(persistence_path=str(tmp_path / "s.json"))
    lead = _lead(orch)
    ctx = _mission_ctx(tmp_path, orch, lead.task_id)
    res = await M.write_mission_contract_handler(ctx, contract={"files": []})
    # H4 — le contrat accepte désormais `files` ET/OU `effects` : un contrat vide
    # reste refusé, et le message enseigne les DEUX formes.
    assert not res.success
    assert "contract.files" in res.error and "contract.effects" in res.error


@pytest.mark.asyncio
async def test_handler_markdown_contract_gets_guiding_error(tmp_path):
    # LOT 2.10 (run StockPilot) : contrat MARKDOWN → avant, « illisible » sec →
    # le lead bypassait l'outil. Maintenant : erreur GUIDANTE (exemple JSON exact
    # + interdiction du fallback manuel) pour provoquer un RETRY, pas un bypass.
    orch = TaskOrchestrator(persistence_path=str(tmp_path / "s.json"))
    lead = _lead(orch)
    ctx = _mission_ctx(tmp_path, orch, lead.task_id)
    res = await M.write_mission_contract_handler(
        ctx, contract="## Contrat StockPilot\n\n| Fichier | Owner |\n|---|---|")
    assert not res.success
    assert "RAPPELLE write_mission_contract" in res.error
    assert '"exports"' in res.error                      # exemple JSON concret
    assert "à la main" in res.error                      # interdiction du bypass
    # même guide sur un contrat structurellement invalide
    res2 = await M.write_mission_contract_handler(ctx, contract={"files": []})
    assert "RAPPELLE write_mission_contract" in res2.error


@pytest.mark.asyncio
async def test_manual_contract_write_blocked_in_mission(tmp_path):
    # LOT 2.10 : le fallback manuel est fermé — write_file de contract.json /
    # CONTRAT.md DANS une mission → refus dirigé vers l'outil.
    from src.reasoning.handlers.context import HandlerContext
    from src.reasoning.handlers.files import write_file_handler
    from src.tools.file_guardrails import WorkspaceFileGuardrails
    ctx = HandlerContext(lumena_root=tmp_path, runtime_root=tmp_path,
                         file_guardrails=WorkspaceFileGuardrails(tmp_path),
                         is_mission_run=True, runtime_task_id="lead",
                         mission_workspace="missions/task_x")
    for name in ("contract.json", "CONTRAT.md", "workspace/stockpilot/contract.json"):
        r = await write_file_handler(ctx, path=name, content="{}")
        assert not r.success and "write_mission_contract" in r.error, name
    # hors mission (chat/CodeAgent) : aucune restriction
    chat = HandlerContext(lumena_root=tmp_path, runtime_root=tmp_path,
                          file_guardrails=WorkspaceFileGuardrails(tmp_path))
    ok = await write_file_handler(chat, path="contract.json", content="{}")
    assert "✅" in ok.output


@pytest.mark.asyncio
async def test_handler_writes_contract_md_json_and_stubs(tmp_path):
    orch = TaskOrchestrator(persistence_path=str(tmp_path / "s.json"))
    lead = _lead(orch)
    ctx = _mission_ctx(tmp_path, orch, lead.task_id)
    res = await M.write_mission_contract_handler(ctx, contract=json.dumps(CONTRACT))
    assert res.success, res.error
    ws = orch.get_task(lead.task_id)["metadata"].get("mission_workspace")
    assert ws and ws.startswith("missions/")          # 2.1 posé/réutilisé par le contrat
    root = tmp_path / ws
    assert (root / "contract.json").is_file()
    assert (root / "CONTRAT.md").is_file()
    assert "def create_app():" in (root / "app.py").read_text(encoding="utf-8")
    assert (root / "static" / "app.js").is_file()     # sous-dossier créé
    # le retour contient les objectifs prêts pour delegate_and_wait
    assert "delegate_and_wait" in res.output and "allowed_files" in res.output


@pytest.mark.asyncio
async def test_handler_never_overwrites_existing(tmp_path):
    orch = TaskOrchestrator(persistence_path=str(tmp_path / "s.json"))
    lead = _lead(orch)
    ctx = _mission_ctx(tmp_path, orch, lead.task_id)
    # pré-existant : un app.py déjà rempli dans le dossier mission
    ws = M._ensure_mission_workspace(orch, lead.task_id,
                                     orch.get_task(lead.task_id)["metadata"])
    target = tmp_path / ws / "app.py"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("DEJA REMPLI", encoding="utf-8")
    res = await M.write_mission_contract_handler(ctx, contract=CONTRACT)
    assert res.success
    assert target.read_text(encoding="utf-8") == "DEJA REMPLI"   # jamais écrasé
    assert "conservé" in res.output


@pytest.mark.asyncio
async def test_handler_recall_same_contract_idempotent(tmp_path):
    # Rappel avec le MÊME contrat → idempotent (stubs conservés, objectifs rendus).
    orch = TaskOrchestrator(persistence_path=str(tmp_path / "s.json"))
    lead = _lead(orch)
    ctx = _mission_ctx(tmp_path, orch, lead.task_id)
    r1 = await M.write_mission_contract_handler(ctx, contract=CONTRACT)
    r2 = await M.write_mission_contract_handler(ctx, contract=CONTRACT)
    assert r1.success and r2.success
    assert "delegate_and_wait" in r2.output


@pytest.mark.asyncio
async def test_handler_recall_different_contract_fails(tmp_path):
    # Hardening note de revue : contrat DIFFÉRENT alors que contract.json existe →
    # FAIL propre (sinon : anciens stubs sur disque + objectifs du nouveau contrat
    # = divergence silencieuse). L'ancien contrat reste intact.
    import copy
    orch = TaskOrchestrator(persistence_path=str(tmp_path / "s.json"))
    lead = _lead(orch)
    ctx = _mission_ctx(tmp_path, orch, lead.task_id)
    r1 = await M.write_mission_contract_handler(ctx, contract=CONTRACT)
    assert r1.success
    other = copy.deepcopy(CONTRACT)
    other["files"][0]["api"] = ["def create_app(config):"]   # signature changée
    r2 = await M.write_mission_contract_handler(ctx, contract=other)
    assert not r2.success and "DIFFÉRENT" in r2.error
    ws = orch.get_task(lead.task_id)["metadata"]["mission_workspace"]
    saved = json.loads((tmp_path / ws / "contract.json").read_text(encoding="utf-8"))
    # ANCIEN contrat intact (les 2 signatures d'origine, pas celle du nouveau)
    assert saved["files"][0]["api"] == ["def create_app():", "def reset_options():"]


# ── enregistrement outil + schéma structuré ──────────────────────────────────────

def test_registered_and_schema_structured():
    from src.reasoning.tool_registry import ToolRegistry
    reg = ToolRegistry(lumena=None)
    assert "write_mission_contract" in reg.tools
    assert reg._tool_modules["write_mission_contract"] == "missions"
    # Note de revue 2.2 : le schéma delegate_and_wait doit DÉCLARER la forme
    # structurée, sinon les modèles ne produisent jamais allowed_files.
    # le registre aplatit parameters = les properties directement
    params = reg.tools["delegate_and_wait"]["parameters"]
    items = params["objectives"]["items"]
    assert "object" in items["type"]
    assert "allowed_files" in items["properties"]


# ── enrichissement delegate_and_wait quand contract.json présent ─────────────────

@pytest.fixture(autouse=True)
def _reset_worker(monkeypatch):
    from src.subagents import manager as manager_mod
    from src.subagents import queue as qmod
    from src.subagents import worker as worker_mod
    qmod.reset_for_tests()
    worker_mod.reset_worker_for_tests()
    manager_mod._manager = None
    yield
    qmod.reset_for_tests()
    worker_mod.reset_worker_for_tests()
    manager_mod._manager = None


@pytest.mark.asyncio
async def test_delegate_injects_contract_preamble(tmp_path, monkeypatch):
    from src.subagents import worker as worker_mod
    from src.tools.file_guardrails import WorkspaceFileGuardrails
    monkeypatch.setenv("LUMENA_MISSION_MAX_DEPTH", "2")
    orch = TaskOrchestrator(persistence_path=str(tmp_path / "s.json"))
    lead = _lead(orch)
    orch.set_task_metadata(lead.task_id, mission_workspace="missions/x_m1")
    (tmp_path / "missions" / "x_m1").mkdir(parents=True)
    (tmp_path / "missions" / "x_m1" / "contract.json").write_text("{}", encoding="utf-8")
    core = types.SimpleNamespace(task_orchestrator=orch)
    ctx = types.SimpleNamespace(lumena=core, runtime_task_id=lead.task_id,
                                file_guardrails=WorkspaceFileGuardrails(tmp_path))

    async def fake_run(core_arg, *, mission_id, objective, **k):
        core_arg.task_orchestrator.mark_running(mission_id)
        core_arg.task_orchestrator.mark_done(mission_id, result_summary="ok")
        return {"status": "done"}

    monkeypatch.setattr(worker_mod, "run_mission", fake_run)
    worker_mod.start_mission_worker(core)
    await M.delegate_and_wait_handler(ctx, ["Objectif brut sans contrat dedans"], timeout=5.0)
    child = orch.get_children(lead.task_id)[0]
    obj = child["metadata"]["objective"]
    assert "CONTRAT DE MISSION" in obj                 # préambule injecté
    assert "Objectif brut sans contrat dedans" in obj


@pytest.mark.asyncio
async def test_delegate_no_preamble_without_contract(tmp_path, monkeypatch):
    from src.subagents import worker as worker_mod
    from src.tools.file_guardrails import WorkspaceFileGuardrails
    monkeypatch.setenv("LUMENA_MISSION_MAX_DEPTH", "2")
    orch = TaskOrchestrator(persistence_path=str(tmp_path / "s.json"))
    lead = _lead(orch)
    core = types.SimpleNamespace(task_orchestrator=orch)
    ctx = types.SimpleNamespace(lumena=core, runtime_task_id=lead.task_id,
                                file_guardrails=WorkspaceFileGuardrails(tmp_path))

    async def fake_run(core_arg, *, mission_id, objective, **k):
        core_arg.task_orchestrator.mark_running(mission_id)
        core_arg.task_orchestrator.mark_done(mission_id, result_summary="ok")
        return {"status": "done"}

    monkeypatch.setattr(worker_mod, "run_mission", fake_run)
    worker_mod.start_mission_worker(core)
    await M.delegate_and_wait_handler(ctx, ["Mission sans contrat"], timeout=5.0)
    child = orch.get_children(lead.task_id)[0]
    assert "CONTRAT DE MISSION" not in child["metadata"]["objective"]


# ── INTÉGRATION 2.2 → 2.3 : le périmètre issu du contrat BLOQUE réellement ──────

@pytest.mark.asyncio
async def test_contract_perimeters_enforced_end_to_end(tmp_path):
    from src.reasoning.handlers.context import HandlerContext
    from src.reasoning.handlers.files import edit_file_handler, write_file_handler
    from src.tools.file_guardrails import WorkspaceFileGuardrails
    orch = TaskOrchestrator(persistence_path=str(tmp_path / "s.json"))
    lead = _lead(orch)
    lead_ctx = _mission_ctx(tmp_path, orch, lead.task_id)
    res = await M.write_mission_contract_handler(lead_ctx, contract=CONTRACT)
    assert res.success
    ws = orch.get_task(lead.task_id)["metadata"]["mission_workspace"]
    objs = mc.worker_objectives(CONTRACT)
    backend = next(o for o in objs if o["allowed_files"] == ["app.py"])

    # worker backend : ctx = workspace 2.1 hérité + périmètre 2.3 issu du contrat
    worker = HandlerContext(lumena_root=tmp_path, runtime_root=tmp_path,
                            file_guardrails=WorkspaceFileGuardrails(tmp_path),
                            is_mission_run=True, runtime_task_id="w_backend",
                            mission_workspace=ws,
                            mission_allowed_files=backend["allowed_files"])
    # il REMPLIT son stub (edit_file, comme le contrat l'exige) → OK
    ok = await edit_file_handler(
        worker, file_path="app.py",
        old_content="raise NotImplementedError('TODO worker — cf. CONTRAT.md')",
        new_content="return {'ok': True}")
    assert "✅" in ok.output
    # il tente d'écrire le fichier d'un AUTRE worker → BLOQUÉ (la dérive PollApp)
    blocked = await write_file_handler(worker, path="test_api.py", content="import app")
    assert "périmètre" in blocked.output or "⛔" in blocked.output
    # le stub test_api.py du contrat est INTACT
    assert "def test_vote_increments():" in (
        tmp_path / ws / "test_api.py").read_text(encoding="utf-8")
