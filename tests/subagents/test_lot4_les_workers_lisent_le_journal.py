"""LOT 4 — UN WORKER PEUT LIRE CE QUE SES FRÈRES ONT DÉCIDÉ.

═══════════════════════════════════════════════════════════════════════════════
  CE QUI MANQUAIT — ET CE QUI NE MANQUAIT PAS
═══════════════════════════════════════════════════════════════════════════════

⚠️ Une version antérieure du plan disait « les workers sont aveugles ». **C'est
faux**, et l'invariant du `CLAUDE_REPO_GUIDE` le dit :

    un worker lit le contrat ET les fichiers des autres, mais n'écrit que dans
    son `allowed_files`

Ils voient donc le DISQUE. Ce qui manque est le RAISONNEMENT : ce qu'un frère a
choisi, tenté, abandonné, ou constaté impossible. Rien de cela ne vit dans un
fichier.

Mesuré : `get_children()` n'est appelé qu'à DEUX endroits, `runner.py:628` et
`missions.py:953` — les deux fois par le LEAD. Jamais par un worker.

Et le journal (posé le 01/09) contient exactement ce qui manque — `thought`,
`tool_name`, `status`, `error` — pendant que **personne ne le lit**. Un fait
produit, persisté, et jamais consulté : le motif du dépôt, une fois de plus.

═══════════════════════════════════════════════════════════════════════════════
  POURQUOI UN JOURNAL ET PAS UN BUS DE MESSAGES
═══════════════════════════════════════════════════════════════════════════════

Un bus rendrait les runs NON REJOUABLES : deux exécutions du même objectif
divergeraient selon l'ordre d'arrivée des messages, et une mission qui part en
vrille ne laisserait rien à relire. Tout ce dépôt repose sur « ça s'est passé,
voici la trace ». Le journal donne l'essentiel du bénéfice sans ce coût.
"""

from __future__ import annotations

import asyncio
import types

import pytest

from src.reasoning.handlers import missions as M
from src.runtime.task_orchestrator import TaskOrchestrator
from src.subagents import mission_contract as mc
from src.telemetry import mission_journal as mj


@pytest.fixture(autouse=True)
def _journal_isole(tmp_path, monkeypatch):
    """Le journal réel de `data/missions/` ne doit JAMAIS être touché par un test."""
    monkeypatch.setenv("LUMENA_MISSION_JOURNAL", "1")
    monkeypatch.setattr(mj, "_racine", lambda: tmp_path / "missions")
    yield


def _ctx(tmp_path, runtime_task_id=None):
    orch = TaskOrchestrator(persistence_path=str(tmp_path / "s.json"))
    core = types.SimpleNamespace(task_orchestrator=orch)
    return types.SimpleNamespace(lumena=core, runtime_task_id=runtime_task_id), orch


def _equipe(orch):
    """Un lead et deux workers, comme `delegate_and_wait` les crée."""
    lead = orch.start_task(conversation_id="__missions__", channel="mission",
                           message_preview="lead",
                           metadata={"kind": "mission", "depth": 1})
    enfants = []
    for nom in ("w_backend", "w_front"):
        t = orch.start_task(conversation_id="__missions__", channel="mission",
                            message_preview=nom,
                            metadata={"kind": "mission", "depth": 2,
                                      "parent_id": lead.task_id,
                                      "delegation_owner": nom})
        enfants.append(t)
    return lead, enfants


def _lire(ctx, **kw):
    return asyncio.run(M.mission_journal_read_handler(ctx, **kw))


# ══════════════════════════════════════════════════════════════════════════
#  1. LE PÉRIMÈTRE — un worker ne voit QUE sa mission
# ══════════════════════════════════════════════════════════════════════════


def test_un_worker_ne_voit_QUE_sa_propre_famille(tmp_path):
    """SÉCURITÉ. La famille est résolue depuis `runtime_task_id`, jamais depuis un
    argument : un identifiant passé par le modèle serait une porte ouverte sur le
    journal de n'importe quelle mission."""
    ctx, orch = _ctx(tmp_path)
    lead, (wb, wf) = _equipe(orch)

    autre = orch.start_task(conversation_id="__missions__", channel="mission",
                            message_preview="autre lead",
                            metadata={"kind": "mission", "depth": 1})
    mj.grave({"task_id": autre.task_id, "seq": 1, "ts": "2026-09-02T10:00:00",
              "thought": "SECRET-AUTRE-MISSION"})
    mj.grave({"task_id": wb.task_id, "seq": 2, "ts": "2026-09-02T10:00:01",
              "thought": "je prends sqlite"})

    ctx.runtime_task_id = wf.task_id
    out = _lire(ctx)
    assert out.success
    assert "sqlite" in out.output                 # son frère : oui
    assert "SECRET-AUTRE-MISSION" not in out.output   # une autre mission : jamais


def test_le_handler_n_accepte_AUCUN_identifiant_de_mission():
    """Garde structurelle : si un `mission_id` apparaissait dans les paramètres,
    n'importe quelle mission deviendrait lisible."""
    spec = next(d for d in M.get_missions_handler_defs()
                if d.name == "mission_journal_read")
    props = set(spec.parameters.get("properties") or {})
    assert props == {"depuis", "worker", "limit"}
    assert not (spec.parameters.get("required") or [])


def test_hors_mission_le_message_est_UTILE(tmp_path):
    """Au chat, l'outil n'a pas de sens — mais l'erreur doit dire où regarder."""
    ctx, _ = _ctx(tmp_path, runtime_task_id=None)
    out = _lire(ctx)
    assert not out.success
    assert "DANS une mission" in out.output
    assert "panneau" in out.output.lower()


# ══════════════════════════════════════════════════════════════════════════
#  2. CE QUE LE WORKER LIT VRAIMENT
# ══════════════════════════════════════════════════════════════════════════


def test_le_worker_voit_le_RAISONNEMENT_de_ses_freres(tmp_path):
    """L'objet du lot : la pensée, pas le fichier (il l'a déjà)."""
    ctx, orch = _ctx(tmp_path)
    lead, (wb, wf) = _equipe(orch)
    mj.grave({"task_id": wb.task_id, "seq": 1, "ts": "2026-09-02T10:00:00",
              "thought": "sqlite plutôt qu'un dict : le contrat parle de persistance"})
    mj.grave({"task_id": wb.task_id, "seq": 2, "ts": "2026-09-02T10:00:01",
              "tool_name": "run_command", "error": "ModuleNotFoundError: flask"})

    ctx.runtime_task_id = wf.task_id
    out = _lire(ctx)
    assert "w_backend" in out.output
    assert "sqlite" in out.output                       # la décision
    assert "ModuleNotFoundError" in out.output          # et l'échec rencontré


def test_le_worker_ne_se_relit_PAS_lui_meme(tmp_path):
    """Il sait ce qu'il vient de faire ; ces lignes prendraient la place des autres."""
    ctx, orch = _ctx(tmp_path)
    lead, (wb, wf) = _equipe(orch)
    mj.grave({"task_id": wf.task_id, "seq": 1, "ts": "2026-09-02T10:00:00",
              "thought": "MA-PROPRE-PENSEE"})
    mj.grave({"task_id": wb.task_id, "seq": 2, "ts": "2026-09-02T10:00:01",
              "thought": "pensee-du-frere"})

    ctx.runtime_task_id = wf.task_id
    out = _lire(ctx)
    assert "MA-PROPRE-PENSEE" not in out.output
    assert "pensee-du-frere" in out.output


def test_le_lead_est_visible_par_ses_workers(tmp_path):
    """Le lead décide du découpage : ses arbitrages intéressent tout le monde."""
    ctx, orch = _ctx(tmp_path)
    lead, (wb, wf) = _equipe(orch)
    mj.grave({"task_id": lead.task_id, "seq": 1, "ts": "2026-09-02T10:00:00",
              "thought": "je découpe en backend et frontend"})
    ctx.runtime_task_id = wb.task_id
    out = _lire(ctx)
    assert "[lead]" in out.output and "découpe" in out.output


def test_journal_vide_n_est_PAS_une_erreur(tmp_path):
    """Au début d'une mission, personne n'a rien publié. Le worker doit continuer,
    pas croire qu'il a échoué."""
    ctx, orch = _ctx(tmp_path)
    lead, (wb, wf) = _equipe(orch)
    ctx.runtime_task_id = wb.task_id
    out = _lire(ctx)
    assert out.success
    assert "pas une erreur" in out.output.lower()


# ══════════════════════════════════════════════════════════════════════════
#  3. LES FILTRES — ne pas noyer un prompt déjà à ~16 k tokens
# ══════════════════════════════════════════════════════════════════════════


def test_depuis_ne_rend_que_la_SUITE(tmp_path):
    ctx, orch = _ctx(tmp_path)
    lead, (wb, wf) = _equipe(orch)
    for i in (1, 2, 3):
        mj.grave({"task_id": wb.task_id, "seq": i,
                  "ts": f"2026-09-02T10:00:0{i}", "thought": f"etape-{i}"})
    ctx.runtime_task_id = wf.task_id
    out = _lire(ctx, depuis=2)
    assert "etape-3" in out.output
    assert "etape-1" not in out.output and "etape-2" not in out.output


def test_le_pied_donne_le_curseur_pour_la_suite(tmp_path):
    """Sans curseur rendu, le worker relit tout à chaque appel."""
    ctx, orch = _ctx(tmp_path)
    lead, (wb, wf) = _equipe(orch)
    mj.grave({"task_id": wb.task_id, "seq": 7, "ts": "2026-09-02T10:00:00",
              "thought": "x"})
    ctx.runtime_task_id = wf.task_id
    assert "depuis=7" in _lire(ctx).output


def test_worker_filtre_sur_un_frere(tmp_path):
    ctx, orch = _ctx(tmp_path)
    lead, (wb, wf) = _equipe(orch)
    mj.grave({"task_id": wb.task_id, "seq": 1, "ts": "2026-09-02T10:00:00",
              "thought": "cote-backend"})
    mj.grave({"task_id": lead.task_id, "seq": 2, "ts": "2026-09-02T10:00:01",
              "thought": "cote-lead"})
    ctx.runtime_task_id = wf.task_id
    out = _lire(ctx, worker="backend")
    assert "cote-backend" in out.output and "cote-lead" not in out.output


def test_la_limite_est_bornee(tmp_path):
    """Un worker qui demande 10 000 lignes noierait son propre prompt."""
    ctx, orch = _ctx(tmp_path)
    lead, (wb, wf) = _equipe(orch)
    for i in range(1, 40):
        mj.grave({"task_id": wb.task_id, "seq": i,
                  "ts": f"2026-09-02T10:{i:02d}:00", "thought": f"t{i}"})
    ctx.runtime_task_id = wf.task_id
    assert _lire(ctx, limit=5).output.count("[w_backend]") == 5
    assert _lire(ctx, limit=99999).output.count("[w_backend]") <= 100
    assert _lire(ctx, limit=0).output.count("[w_backend]") >= 1   # jamais zéro


def test_les_evenements_sans_contenu_sont_IGNORES(tmp_path):
    """Zéro bruit : un événement sans pensée, sans outil et sans erreur n'apporte
    rien et prendrait la place d'un utile."""
    assert M._ligne_journal("w", {"stage": "tick", "seq": 1}) == ""
    assert M._ligne_journal("w", {}) == ""


def test_le_rendu_est_COMPACT(tmp_path):
    """Les plafonds du journal sont de 400 (thought) et 300 (error) caractères ;
    servis bruts, quelques lignes suffiraient à noyer le prompt."""
    ligne = M._ligne_journal("w", {"thought": "x" * 400})
    assert len(ligne) < 260
    ligne_err = M._ligne_journal("w", {"tool_name": "run", "error": "y" * 300})
    assert len(ligne_err) < 200


# ══════════════════════════════════════════════════════════════════════════
#  4. LE WORKER DOIT SAVOIR QUE ÇA EXISTE
# ══════════════════════════════════════════════════════════════════════════


def test_le_steer_de_coordination_arrive_A_PLUSIEURS():
    """GARDE ANTI-CAPACITÉ-MORTE.

    `contract.effects` existe depuis H4 et n'est employé que par 3 contrats sur 176 :
    un worker voit 732 outils et n'en emploie qu'une poignée. Déclarer
    `mission_journal_read` ne suffit donc pas — il faut le lui dire."""
    duo = {"project": "P", "files": [
        {"path": "a.py", "owner": "w1", "desc": "x", "exports": ["def f():"]},
        {"path": "b.py", "owner": "w2", "desc": "y", "exports": ["def g():"]}]}
    for o in mc.worker_objectives(duo):
        assert "mission_journal_read" in o["objective"]


def test_le_steer_est_MUET_pour_un_worker_SEUL():
    """AUD-017 — désaturation des gardes. Seul, il n'a personne à lire ; une consigne
    de plus diluerait les autres."""
    solo = {"project": "P", "files": [
        {"path": "a.py", "owner": "w1", "desc": "x", "exports": ["def f():"]}]}
    assert "mission_journal_read" not in mc.worker_objectives(solo)[0]["objective"]
    assert mc.coordination_steer(1) == ""
    assert mc.coordination_steer(0) == ""
    assert mc.coordination_steer("x") == ""


def test_le_steer_rappelle_que_le_CONTRAT_reste_la_verite():
    """Le journal est un CONSTAT. Sans ce garde-fou, un worker pourrait s'autoriser
    à sortir de son périmètre parce qu'« un frère l'a fait »."""
    steer = mc.coordination_steer(3)
    assert "CONTRAT" in steer and "périmètre" in steer
    assert "3 workers" in steer


def test_le_steer_arrive_EN_DERNIER():
    """C'est un moyen, pas la mission : il ne doit pas passer devant le contrat,
    la discipline ou la liste des fichiers."""
    duo = {"project": "P", "files": [
        {"path": "a.py", "owner": "w1", "desc": "x", "exports": ["def f():"]},
        {"path": "b.py", "owner": "w2", "desc": "y", "exports": ["def g():"]}]}
    txt = mc.worker_objectives(duo)[0]["objective"]
    assert txt.index("mission_journal_read") > txt.index("DISCIPLINE")
    assert txt.index("mission_journal_read") > txt.index("Tes fichiers")


def test_l_outil_est_DECLARE_au_registre():
    spec = next((d for d in M.get_missions_handler_defs()
                 if d.name == "mission_journal_read"), None)
    assert spec is not None
    assert spec.handler is M.mission_journal_read_handler
    # La description doit dire QUAND s'en servir, pas seulement ce que c'est.
    for mot in ("bloqué", "dépend", "conclure"):
        assert mot in spec.description
