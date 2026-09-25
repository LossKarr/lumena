"""CONN-5A - porte IDE en mission, cote provider.

Deux parties :

1. CARACTERISATION du hors-mission, ecrite AVANT tout code : aucun test ne couvrait
   les refus du provider IDE (audit du 14/09, section 60 de la spec). Ces tests
   doivent passer sur le code d'avant CONN-5A et apres.
2. PORTE DE MISSION : en mission, seule une lecture de fichier confinee
   (`readonly`, READ_ONLY, confirmation never, capacite `file_read`) part vers
   l'IDE, et seulement si l'IDE est ouverte exactement sur le dossier de la
   mission, pour un appelant identifie, avec un perimetre revalide contre le
   TaskOrchestrator juste avant l'envoi.

Le role se lit dans `parent_id`, jamais dans `allowed_files` (piege H4-b).
"""
from __future__ import annotations

from dataclasses import replace
import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.reasoning.caller_context import CODEAGENT, REACT
from src.runtime.context import RuntimeContext, pop_runtime_context, push_runtime_context
from src.runtime.task_orchestrator import TaskOrchestrator
from tests.reasoning.test_conn3c_live_catalogue import owner as owner, registry as registry
from tests.tools.test_conn3c_ide_capabilities import service as service

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(autouse=True)
def _lanceur_de_mission_indisponible(monkeypatch):
    """Ce contrat d'autorisation ne doit jamais lancer l'application reelle.

    L'ouverture dediee est couverte par les tests L5-3b. Ici, une IDE ouverte
    hors du dossier de mission doit emprunter le chemin d'echec borne et rendre
    le refus historique sans dependre d'une installation locale de Lumena IDE.
    """
    lanceur = SimpleNamespace(ensure_ready=AsyncMock(
        return_value=SimpleNamespace(available=False, state="unavailable")
    ))
    monkeypatch.setattr("src.tools.ide_launcher.get_ide_launcher", lambda: lanceur)
    return lanceur


def _envoi(service, **reply):
    send = AsyncMock(return_value={"success": True, **reply})
    service.bridge.send_command = send
    return send


def _ide_ouverte_sur(service, path):
    session = service.bridge._negotiated
    service.bridge._negotiated = replace(session, workspace_path=None if path is None else str(path))


def _contexte(role="owner", task_id=None):
    return push_runtime_context(RuntimeContext(
        channel="ide", client="test", request_id="request", conversation_id="conversation",
        message_id="message", user_role=role, task_id=task_id,
    ))


def _mission(registry, *, task_id="task_worker", parent="task_lead", allowed=("README.md",),
             kind="mission", projection=None):
    """Mission reelle dans un vrai TaskOrchestrator + projection HandlerContext.

    `allowed=None` : la cle `allowed_files` est ABSENTE, comme pour les 179 workers
    mesures sans liste (workers d'effets H4).
    """
    orch = TaskOrchestrator()
    sub = f"missions/{parent or task_id}"
    meta = {"kind": kind, "mission_workspace": sub, "depth": 2 if parent else 1}
    if parent:
        meta["parent_id"] = parent
    if allowed is not None:
        meta["allowed_files"] = list(allowed)
    orch.start_task(conversation_id="conversation", channel="web", message_preview="mission",
                    metadata=meta, task_id=task_id)
    ctx = registry._v2_context
    projection = projection or {}
    ctx.lumena = SimpleNamespace(task_orchestrator=orch)
    ctx.is_mission_run = True
    ctx.runtime_task_id = task_id
    ctx.mission_workspace = projection.get("mission_workspace", sub)
    ctx.mission_allowed_files = list(projection.get("allowed_files", allowed or []))
    root = ctx.file_guardrails._workspace_root().resolve() / sub
    root.mkdir(parents=True, exist_ok=True)
    return orch, root


# ══════════════════════════════════════════════════════════════════════════
#  1. CARACTERISATION DU HORS-MISSION - inchange par CONN-5A
# ══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_hors_mission_get_status_part_vers_l_ide(registry, service, owner):
    send = _envoi(service, connected=True)
    observation = await registry.execute("ide__get_status", {})
    assert observation.success is True
    send.assert_awaited_once()
    assert send.await_args.args[:2] == ("get_status", {})


@pytest.mark.asyncio
async def test_sans_contexte_proprietaire_refus(registry, service):
    send = _envoi(service)
    observation = await registry._ide_tools.execute("ide__get_status", {})
    assert observation.success is False
    assert observation.content == "IDE: ide_owner_context_required"
    send.assert_not_awaited()


@pytest.mark.asyncio
async def test_role_invite_refuse(registry, service):
    send = _envoi(service)
    token = _contexte(role="guest")
    try:
        observation = await registry._ide_tools.execute("ide__get_status", {})
    finally:
        pop_runtime_context(token)
    assert observation.content == "IDE: ide_owner_context_required"
    send.assert_not_awaited()


@pytest.mark.asyncio
async def test_hors_mission_aucun_effet_n_est_ouvert(registry, service, owner):
    """CONN-5A n'ouvre aucun EFFET au chat. Perimetre precise par CONN-6a.

    Ce gel figeait a l'origine que `ide__read_file` etait refuse hors mission, sous
    le titre « CONN-5A n'ouvre RIEN au chat ». C'etait juste POUR CONN-5A, dont ce
    n'etait pas l'objet : le refus des lectures venait d'une liste en dur de deux
    noms heritee de CONN-4, fail-closed **en attendant CONN-6**.

    CONN-6a (23 septembre 2026) a ouvert la LECTURE PURE, et elle seule : 39 des 41
    lectures sans confirmation etaient refusees sans rien proteger, alors que
    `resolveWorkspacePath` les borne deja au workspace actif cote IDE. Ce que CONN-5A
    doit continuer de figer - et qui est le vrai invariant - c'est qu'aucun EFFET ne
    s'ouvre au chat. Les gels jumeaux de CONN-5B-1, 5C-1 et 5C-2 (`write_file`,
    `task_run`, `command_run`) restent intacts.
    """
    send = _envoi(service)
    observation = await registry._ide_tools.execute(
        "ide__sidebar_create_file", {"path": "notes.txt"})
    assert observation.content == "IDE: ide_host_authorization_not_connected"
    send.assert_not_awaited()


@pytest.mark.asyncio
async def test_hors_mission_perimetre_d_outils_du_run(registry, service, owner):
    send = _envoi(service)
    registry._allowed_tools_hard = True
    registry._allowed_tools = {"write_file"}
    observation = await registry._ide_tools.execute("ide__get_status", {})
    assert observation.content == "IDE: ide_tool_outside_run_scope"
    send.assert_not_awaited()


@pytest.mark.asyncio
async def test_appel_positionnel_historique_du_provider_reste_valide(registry, service, owner):
    send = _envoi(service, connected=True)
    observation = await registry._ide_tools.execute("ide__get_status", {})
    assert observation.success is True and send.await_count == 1


# ══════════════════════════════════════════════════════════════════════════
#  2. UN TOUR DE CHAT N'EST PAS UNE MISSION
# ══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_tour_de_chat_avec_tache_d_orchestrateur_n_est_pas_une_mission(registry, service):
    """Defaut latent trouve a l'audit : chat.py pose le task_id de la tache du tour
    dans l'enveloppe AVANT de construire le RuntimeContext (l. 1475-1481 et
    1825-1843). Avec LUMENA_TASK_ORCHESTRATOR_V1 actif, chaque tour de chat etait
    pris pour une mission et meme get_status etait refuse."""
    orch = TaskOrchestrator()
    orch.start_task(conversation_id="conversation", channel="web", message_preview="bonjour",
                    metadata={"source": "api_chat_stream"}, task_id="task_chat")
    registry._v2_context.lumena = SimpleNamespace(task_orchestrator=orch)
    send = _envoi(service, connected=True)
    token = _contexte(task_id="task_chat")
    try:
        observation = await registry.execute("ide__get_status", {})
    finally:
        pop_runtime_context(token)
    assert observation.success is True
    send.assert_awaited_once()


@pytest.mark.asyncio
async def test_vraie_mission_sans_projection_reste_refusee(registry, service):
    """Le task_id designe une mission mais le HandlerContext n'est pas projete :
    jamais traitee comme du chat."""
    orch = TaskOrchestrator()
    orch.start_task(conversation_id="conversation", channel="web", message_preview="mission",
                    metadata={"kind": "mission", "mission_workspace": "missions/task_m"}, task_id="task_m")
    registry._v2_context.lumena = SimpleNamespace(task_orchestrator=orch)
    send = _envoi(service)
    token = _contexte(task_id="task_m")
    try:
        observation = await registry._ide_tools.execute("ide__get_status", {}, caller=REACT)
    finally:
        pop_runtime_context(token)
    assert observation.content == "IDE: ide_mission_scope_incomplete"
    send.assert_not_awaited()


@pytest.mark.asyncio
async def test_tache_inverifiable_sans_orchestrateur_reste_refusee(registry, service):
    registry._v2_context.lumena = None
    send = _envoi(service)
    token = _contexte(task_id="task_inconnue")
    try:
        observation = await registry._ide_tools.execute("ide__get_status", {}, caller=REACT)
    finally:
        pop_runtime_context(token)
    assert observation.content == "IDE: ide_mission_scope_incomplete"
    send.assert_not_awaited()


# ══════════════════════════════════════════════════════════════════════════
#  3. LECTURE DE FICHIER BORNEE AU DOSSIER DE MISSION
# ══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_lecture_de_fichier_admise_sur_le_dossier_de_mission(registry, service, owner):
    _, root = _mission(registry)
    _ide_ouverte_sur(service, root)
    send = _envoi(service, content="# Herbier")
    observation = await registry.execute("ide__read_file", {"path": "README.md"}, caller=REACT)
    assert observation.success is True
    send.assert_awaited_once()
    assert send.await_args.args[:2] == ("read_file", {"path": "README.md"})


@pytest.mark.asyncio
@pytest.mark.parametrize("parent", [None, "task_lead"], ids=["lead", "worker"])
async def test_lead_et_worker_lisent_tous_deux(registry, service, owner, parent):
    """Un worker lit le contrat et les fichiers des autres (invariant du guide)."""
    task_id = "task_lead" if parent is None else "task_worker"
    _, root = _mission(registry, task_id=task_id, parent=parent, allowed=None if parent is None else ("app.py",))
    _ide_ouverte_sur(service, root)
    send = _envoi(service, content="x")
    observation = await registry.execute("ide__read_file", {"path": "CONTRAT.md"}, caller=REACT)
    assert observation.success is True
    send.assert_awaited_once()


@pytest.mark.asyncio
async def test_worker_d_effets_sans_allowed_files_lit_aussi(registry, service, owner):
    """H4-b : 179 workers n'ont pas la cle allowed_files. Ils restent des workers."""
    _, root = _mission(registry, allowed=None)
    _ide_ouverte_sur(service, root)
    send = _envoi(service, content="x")
    observation = await registry.execute("ide__read_file", {"path": "CONTRAT.md"}, caller=REACT)
    assert observation.success is True
    send.assert_awaited_once()


@pytest.mark.asyncio
async def test_codeagent_identifie_est_admis(registry, service, owner):
    _, root = _mission(registry)
    _ide_ouverte_sur(service, root)
    send = _envoi(service, content="x")
    observation = await registry.execute("ide__read_file", {"path": "README.md"}, caller=CODEAGENT)
    assert observation.success is True
    send.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("variante", ["separateur_final", "barres_obliques", "casse"])
async def test_chemin_ide_equivalent_au_dossier_de_mission_admis(registry, service, owner, variante):
    _, root = _mission(registry)
    texte = str(root)
    if variante == "separateur_final":
        chemin = texte + os.sep
    elif variante == "barres_obliques":
        chemin = texte.replace("\\", "/")
    else:
        if os.name != "nt":
            pytest.skip("la casse n'est insensible que sous Windows")
        chemin = texte.upper()
    _ide_ouverte_sur(service, chemin)
    send = _envoi(service, content="x")
    observation = await registry.execute("ide__read_file", {"path": "README.md"}, caller=REACT)
    assert observation.success is True
    send.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("ouverture", ["dossier_parent", "autre_mission", "aucune", "relative"])
async def test_ide_hors_du_dossier_de_mission_refuse(registry, service, owner, ouverture):
    """L'IDE est normalement ouverte sur le projet de l'utilisateur : une mission
    n'y lit rien."""
    _, root = _mission(registry)
    autre = root.parent / "task_autre"
    autre.mkdir(exist_ok=True)
    chemin = {"dossier_parent": root.parent, "autre_mission": autre, "aucune": None,
              "relative": "missions/task_lead"}[ouverture]
    _ide_ouverte_sur(service, chemin)
    send = _envoi(service)
    observation = await registry.execute("ide__read_file", {"path": "README.md"}, caller=REACT)
    assert observation.content == "IDE: ide_mission_workspace_mismatch"
    send.assert_not_awaited()


def _lien(cible: Path, lien: Path) -> None:
    try:
        os.symlink(cible, lien, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("creation de lien symbolique non autorisee sur cette machine")


@pytest.mark.asyncio
async def test_lien_symbolique_vers_un_autre_dossier_refuse(registry, service, owner):
    _, root = _mission(registry)
    ailleurs = root.parent / "ailleurs"
    ailleurs.mkdir()
    lien = root.parent / "lien_trompeur"
    _lien(ailleurs, lien)
    _ide_ouverte_sur(service, lien)
    send = _envoi(service)
    observation = await registry.execute("ide__read_file", {"path": "README.md"}, caller=REACT)
    assert observation.content == "IDE: ide_mission_workspace_mismatch"
    send.assert_not_awaited()


@pytest.mark.asyncio
async def test_lien_symbolique_vers_le_dossier_de_mission_admis(registry, service, owner):
    _, root = _mission(registry)
    lien = root.parent / "lien_mission"
    _lien(root, lien)
    _ide_ouverte_sur(service, lien)
    send = _envoi(service, content="x")
    observation = await registry.execute("ide__read_file", {"path": "README.md"}, caller=REACT)
    assert observation.success is True
    send.assert_awaited_once()


# ══════════════════════════════════════════════════════════════════════════
#  4. TOUT LE RESTE RESTE FERME EN MISSION
# ══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_mutation_sans_contenu_refusee_meme_sur_le_bon_dossier(registry, service, owner):
    """CONN-5A figeait le refus de write_file en mission ; CONN-5B-1 ouvre
    volontairement l'ecriture de contenu. Une creation sans contenu reste fermee."""
    _, root = _mission(registry)
    _ide_ouverte_sur(service, root)
    send = _envoi(service)
    observation = await registry.execute(
        "ide__sidebar_create_file", {"path": "README.md"}, caller=REACT,
    )
    assert observation.content == "IDE: ide_mission_mutation_not_connected"
    send.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("nom", ["ide__list_files", "ide__workspace_recent", "ide__get_status"])
async def test_lecture_non_confinee_refusee(registry, service, owner, nom):
    """31 lectures `readonly` portent `generic_readonly` : `workspace_recent` donne
    les chemins d'autres projets. La capacite ne prouve pas le confinement."""
    _, root = _mission(registry)
    _ide_ouverte_sur(service, root)
    send = _envoi(service)
    observation = await registry.execute(nom, {}, caller=REACT)
    assert observation.content == "IDE: ide_mission_read_unconfined"
    send.assert_not_awaited()


@pytest.mark.asyncio
async def test_commande_interdite_en_mission(registry, service, owner):
    _, root = _mission(registry)
    _ide_ouverte_sur(service, root)
    send = _envoi(service)
    observation = await registry.execute("ide__command_palette_show", {}, caller=REACT)
    assert observation.content == "IDE: ide_mission_policy_forbidden"
    send.assert_not_awaited()


# ══════════════════════════════════════════════════════════════════════════
#  5. LE PERIMETRE DOIT ETRE COMPLET, COHERENT ET ENCORE VALIDE A L'ENVOI
# ══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_appelant_inconnu_refuse_en_mission(registry, service, owner):
    _, root = _mission(registry)
    _ide_ouverte_sur(service, root)
    send = _envoi(service)
    observation = await registry.execute("ide__read_file", {"path": "README.md"})
    assert observation.content == "IDE: ide_mission_caller_unknown"
    send.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("defaut", ["sans_id_de_tache", "sans_orchestrateur", "tache_inconnue",
                                    "pas_une_mission", "dossier_invalide"])
async def test_perimetre_incomplet_refuse(registry, service, owner, defaut):
    kind = "voice_turn" if defaut == "pas_une_mission" else "mission"
    _, root = _mission(registry, kind=kind)
    ctx = registry._v2_context
    if defaut == "sans_id_de_tache":
        ctx.runtime_task_id = None
    elif defaut == "sans_orchestrateur":
        ctx.lumena = None
    elif defaut == "tache_inconnue":
        ctx.runtime_task_id = "task_fantome"
    elif defaut == "dossier_invalide":
        ctx.mission_workspace = "../evasion"
    _ide_ouverte_sur(service, root)
    send = _envoi(service)
    observation = await registry.execute("ide__read_file", {"path": "README.md"}, caller=REACT)
    assert observation.content == "IDE: ide_mission_scope_incomplete"
    send.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("projection", [
    {"mission_workspace": "missions/autre_mission"},
    {"allowed_files": ["autre.py"]},
], ids=["dossier", "fichiers"])
async def test_projection_divergente_du_task_orchestrator_refusee(registry, service, owner, projection):
    _, root = _mission(registry, projection=projection)
    _ide_ouverte_sur(service, root)
    send = _envoi(service)
    observation = await registry.execute("ide__read_file", {"path": "README.md"}, caller=REACT)
    assert observation.content == "IDE: ide_mission_scope_divergent"
    send.assert_not_awaited()


@pytest.mark.asyncio
async def test_mission_annulee_refusee(registry, service, owner):
    orch, root = _mission(registry)
    orch.cancel_task("task_worker")
    assert orch.is_cancel_requested("task_worker")
    _ide_ouverte_sur(service, root)
    send = _envoi(service)
    observation = await registry.execute("ide__read_file", {"path": "README.md"}, caller=REACT)
    assert observation.content == "IDE: ide_mission_cancelled"
    send.assert_not_awaited()


@pytest.mark.asyncio
async def test_revalidation_juste_avant_l_envoi(registry, service, owner):
    """Le perimetre change entre la premiere lecture et l'envoi : rien ne part."""
    orch, root = _mission(registry)
    _ide_ouverte_sur(service, root)
    send = _envoi(service)
    vrai = orch.get_task
    appels = []

    def get_task(task_id):
        appels.append(task_id)
        data = vrai(task_id)
        if len(appels) > 1 and data:
            data = {**data, "metadata": {**data["metadata"], "mission_workspace": "missions/deplacee"}}
        return data

    orch.get_task = get_task
    observation = await registry.execute("ide__read_file", {"path": "README.md"}, caller=REACT)
    assert observation.content == "IDE: ide_mission_scope_divergent"
    assert len(appels) >= 2
    send.assert_not_awaited()


@pytest.mark.asyncio
async def test_perimetre_d_outils_de_la_mission_s_applique_aussi(registry, service, owner):
    _, root = _mission(registry)
    _ide_ouverte_sur(service, root)
    registry._allowed_tools_hard = True
    registry._allowed_tools = {"read_file"}
    send = _envoi(service)
    observation = await registry._ide_tools.execute("ide__read_file", {"path": "README.md"}, caller=REACT)
    assert observation.content == "IDE: ide_tool_outside_run_scope"
    send.assert_not_awaited()


# ══════════════════════════════════════════════════════════════════════════
#  6. STRUCTURE
# ══════════════════════════════════════════════════════════════════════════


def test_la_porte_de_mission_reste_hors_de_react_py():
    source = (ROOT / "src/reasoning/react.py").read_text(encoding="utf-8")
    assert len(source.splitlines()) <= 9718
    for interdit in ("ide_mission", "MissionScope", "authorize_mission_call"):
        assert interdit not in source
